"""PostgreSQL access: one database, two tables.

    candidates   one row per PERSON
    interviews   one row per INTERVIEW, with candidate_id referencing
                 candidates(id) ON DELETE CASCADE, so removing a person
                 takes their history with them

Both tables and their indexes are created on first use, and so is the database
itself, so a fresh install needs nothing set up in pgAdmin beyond a server and
a role allowed to create databases.

Columns are snake_case because that is what SQL and pgAdmin are pleasant to
read, while the documents the rest of the app passes around stay camelCase.
The two ``_MAP`` tables below are the only place that translation lives. A key
with no column is dropped on write — the shape of a candidate and of an
interview is fixed here, not by whatever the caller happens to hand over.

The pool is built lazily rather than at import time: the app must still boot
(and say so on screen) when Postgres is unreachable, instead of dying before
it can serve the page that would explain why.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)


class StoreError(RuntimeError):
    """A write failed. Reads degrade to empty instead of raising."""


class NotConfigured(StoreError):
    """Postgres settings are missing — the app boots, but cannot store anything."""


# ── Document ⇄ column mapping ────────────────────────────────────────
CANDIDATE_MAP: dict[str, str] = {
    "id": "id",
    "displayName": "display_name",
    "nameKey": "name_key",
    "emails": "emails",
    "phones": "phones",
    "phoneKeys": "phone_keys",
    "linkedins": "linkedins",
    "githubs": "githubs",
    "searchBlob": "search_blob",
    "interviewCount": "interview_count",
    "createdAt": "created_at",
    "updatedAt": "updated_at",
    "firstInterviewAt": "first_interview_at",
    "lastInterviewAt": "last_interview_at",
    "lastStatus": "last_status",
    "lastPosition": "last_position",
    "demoSeed": "demo_seed",
}

INTERVIEW_MAP: dict[str, str] = {
    "id": "id",
    "candidateId": "candidate_id",
    "createdAt": "created_at",
    "updatedAt": "updated_at",
    "linkReason": "link_reason",
    "cv": "cv",
    "demoSeed": "demo_seed",
    "team": "team",
    "name": "name",
    "interviewer": "interviewer",
    "interviewDateTime": "interview_datetime",
    "skillSet": "skill_set",
    "education": "education",
    "experience": "experience",
    "salaryExpectation": "salary_expectation",
    "reasonForLeaving": "reason_for_leaving",
    "noticePeriod": "notice_period",
    "position": "position",
    "note": "note",
    "feedback1": "feedback1",
    "feedback2": "feedback2",
    "feedback3": "feedback3",
    "status": "status",
    "email": "email",
    "phone": "phone",
    "linkedin": "linkedin",
    "github": "github",
}

ARRAY_COLUMNS = frozenset({"emails", "phones", "phone_keys", "linkedins", "githubs"})
JSON_COLUMNS = frozenset({"cv"})
INT_COLUMNS = frozenset({"interview_count"})
BOOL_COLUMNS = frozenset({"demo_seed"})


# ── Schema ───────────────────────────────────────────────────────────
# Dates are text holding ISO-8601 strings rather than timestamptz. The form
# sends a value with no timezone, the server stamps one in UTC, and the app
# compares and sorts them as strings throughout; text keeps one representation
# end to end instead of converting at every edge.
SCHEMA = """
CREATE TABLE IF NOT EXISTS candidates (
    id                 text    PRIMARY KEY,
    display_name       text    NOT NULL DEFAULT '',
    name_key           text    NOT NULL DEFAULT '',
    emails             text[]  NOT NULL DEFAULT '{}',
    phones             text[]  NOT NULL DEFAULT '{}',
    phone_keys         text[]  NOT NULL DEFAULT '{}',
    linkedins          text[]  NOT NULL DEFAULT '{}',
    githubs            text[]  NOT NULL DEFAULT '{}',
    search_blob        text    NOT NULL DEFAULT '',
    interview_count    integer NOT NULL DEFAULT 0,
    created_at         text    NOT NULL DEFAULT '',
    updated_at         text    NOT NULL DEFAULT '',
    first_interview_at text    NOT NULL DEFAULT '',
    last_interview_at  text    NOT NULL DEFAULT '',
    last_status        text    NOT NULL DEFAULT '',
    last_position      text    NOT NULL DEFAULT '',
    demo_seed          boolean NOT NULL DEFAULT false
);

-- The matcher looks a candidate up by one value out of an array, which is
-- exactly what GIN plus the @> operator is for.
CREATE INDEX IF NOT EXISTS candidates_emails_idx     ON candidates USING gin (emails);
CREATE INDEX IF NOT EXISTS candidates_phone_keys_idx ON candidates USING gin (phone_keys);
CREATE INDEX IF NOT EXISTS candidates_linkedins_idx  ON candidates USING gin (linkedins);
CREATE INDEX IF NOT EXISTS candidates_githubs_idx    ON candidates USING gin (githubs);
CREATE INDEX IF NOT EXISTS candidates_name_key_idx   ON candidates (name_key);
CREATE INDEX IF NOT EXISTS candidates_recent_idx     ON candidates (last_interview_at DESC);
-- The candidate list's status filter.
CREATE INDEX IF NOT EXISTS candidates_status_idx     ON candidates (last_status);

CREATE TABLE IF NOT EXISTS interviews (
    id                 text    PRIMARY KEY,
    candidate_id       text    NOT NULL REFERENCES candidates (id) ON DELETE CASCADE,
    created_at         text    NOT NULL DEFAULT '',
    updated_at         text    NOT NULL DEFAULT '',
    link_reason        text    NOT NULL DEFAULT '',
    cv                 jsonb,
    demo_seed          boolean NOT NULL DEFAULT false,
    team               text    NOT NULL DEFAULT '',
    name               text    NOT NULL DEFAULT '',
    interviewer        text    NOT NULL DEFAULT '',
    interview_datetime text    NOT NULL DEFAULT '',
    skill_set          text    NOT NULL DEFAULT '',
    education          text    NOT NULL DEFAULT '',
    experience         text    NOT NULL DEFAULT '',
    salary_expectation text    NOT NULL DEFAULT '',
    reason_for_leaving text    NOT NULL DEFAULT '',
    notice_period      text    NOT NULL DEFAULT '',
    -- Quoted because POSITION is a SQL keyword. Postgres does allow it
    -- unquoted as a column name; the quotes remove the question.
    "position"         text    NOT NULL DEFAULT '',
    note               text    NOT NULL DEFAULT '',
    feedback1          text    NOT NULL DEFAULT '',
    feedback2          text    NOT NULL DEFAULT '',
    feedback3          text    NOT NULL DEFAULT '',
    status             text    NOT NULL DEFAULT '',
    email              text    NOT NULL DEFAULT '',
    phone              text    NOT NULL DEFAULT '',
    linkedin           text    NOT NULL DEFAULT '',
    github             text    NOT NULL DEFAULT ''
);

-- One person's history, newest first: the read behind every profile page.
CREATE INDEX IF NOT EXISTS interviews_candidate_idx
    ON interviews (candidate_id, interview_datetime DESC);
CREATE INDEX IF NOT EXISTS interviews_created_idx ON interviews (created_at DESC);
"""


# ── Connection ───────────────────────────────────────────────────────
_lock = threading.Lock()
_pool = None


def _ensure_database() -> None:
    """Create the database if this is the first run against a fresh server.

    A failed connection arrives as a bare OperationalError — psycopg fills in
    no sqlstate for it, and the message text is translated on a localized
    server, so neither is worth reading. Instead, fall back to the
    always-present ``postgres`` database and ask pg_database directly.

    If that fallback cannot connect either, the FIRST error is the one worth
    showing: a wrong password fails both ways, and "password authentication
    failed" tells you far more than anything this function could add.
    """
    import psycopg

    first_error: Exception | None = None
    try:
        with psycopg.connect(settings.dsn, connect_timeout=10):
            return
    except psycopg.OperationalError as exc:
        first_error = exc

    from psycopg import sql

    name = settings.db_name
    try:
        # CREATE DATABASE cannot run inside a transaction block, hence autocommit.
        with psycopg.connect(
            settings.maintenance_dsn, connect_timeout=10, autocommit=True
        ) as conn:
            exists = conn.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (name,)
            ).fetchone()
            if exists:
                # The database is there, so the first failure was something
                # else entirely — too many connections, pg_hba, a dropped link.
                raise first_error
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
            logger.info("Created database %s.", name)
    except psycopg.errors.DuplicateDatabase:
        pass  # another worker got there first
    except psycopg.OperationalError:
        raise first_error from None


def _get_pool():
    global _pool
    if _pool is not None:
        return _pool
    with _lock:
        if _pool is not None:  # another thread won the race
            return _pool
        if not settings.db_configured:
            raise NotConfigured(
                "PostgreSQL is not configured. Set PG_HOST, PG_DATABASE, PG_USER and "
                "PG_PASSWORD (or a single DATABASE_URL) in your .env file."
            )
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

        _ensure_database()
        pool = ConnectionPool(
            settings.dsn,
            min_size=1,
            max_size=settings.pg_pool_max,
            # Autocommit: every statement here is self-contained, and an idle
            # open transaction holding row locks is the last thing a shared
            # office database needs.
            kwargs={"row_factory": dict_row, "autocommit": True, "connect_timeout": 10},
            open=True,
            timeout=10,
        )
        try:
            pool.wait(timeout=15)
            with pool.connection() as conn:
                conn.execute(SCHEMA)
        except Exception:
            # Never cache a pool that could not reach the server: the next
            # request should try again rather than inherit a dead handle.
            pool.close()
            raise
        logger.info("PostgreSQL ready: %s", settings.db_target)
        _pool = pool
        return _pool


def close() -> None:
    """Shut the pool down, so Ctrl+C does not wait on its worker threads."""
    global _pool
    with _lock:
        if _pool is not None:
            _pool.close()
            _pool = None


def health() -> dict[str, Any]:
    """Is the database reachable? Used by the UI's status pill."""
    if not settings.db_configured:
        return {"ok": False, "detail": "PostgreSQL connection details are not set in .env."}
    try:
        with _get_pool().connection() as conn:
            conn.execute("SELECT 1")
        return {
            "ok": True,
            "database": settings.db_name,
            "endpoint": settings.db_target,
        }
    except Exception as exc:
        logger.exception("PostgreSQL health check failed.")
        return {"ok": False, "detail": str(exc)}


# ── Document ⇄ row conversion ────────────────────────────────────────
def _value_for(column: str, value: Any) -> Any:
    """One document field as the column wants it, with a sane empty default.

    A missing key is not an error: an upsert writes the whole row, so every
    column needs a value whether the caller supplied one or not.
    """
    if column in ARRAY_COLUMNS:
        return [str(v) for v in value if v] if isinstance(value, (list, tuple)) else []
    if column in INT_COLUMNS:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0
    if column in BOOL_COLUMNS:
        return bool(value)
    if column in JSON_COLUMNS:
        from psycopg.types.json import Jsonb

        return Jsonb(value) if value else None
    return "" if value is None else str(value)


def _row_values(doc: dict, mapping: dict[str, str]) -> list[Any]:
    return [_value_for(column, doc.get(key)) for key, column in mapping.items()]


def _to_doc(row: dict, mapping: dict[str, str]) -> dict[str, Any]:
    return {key: row[column] for key, column in mapping.items() if column in row}


def _upsert_sql(table: str, mapping: dict[str, str]) -> str:
    """INSERT ... ON CONFLICT — the SQL spelling of the old upsert_item.

    Every column is written every time, so a save replaces the whole row the
    way the document store replaced the whole document: no field quietly
    survives from a previous version of the record.

    Built from ``mapping``, a literal in this module, so no caller input
    reaches the identifiers — only the %s placeholders.
    """
    columns = list(mapping.values())
    quoted = [f'"{c}"' for c in columns]
    placeholders = ", ".join(["%s"] * len(columns))
    updates = ", ".join(f"{q} = EXCLUDED.{q}" for q, c in zip(quoted, columns) if c != "id")
    return (
        f"INSERT INTO {table} ({', '.join(quoted)}) VALUES ({placeholders}) "
        f"ON CONFLICT (id) DO UPDATE SET {updates} RETURNING *"
    )


_UPSERT_CANDIDATE = _upsert_sql("candidates", CANDIDATE_MAP)
_UPSERT_INTERVIEW = _upsert_sql("interviews", INTERVIEW_MAP)


# ── Generic helpers ──────────────────────────────────────────────────
def _query(sql: str, params: tuple | list = (), *, mapping: dict[str, str]) -> list[dict]:
    """Run a query. A failed read logs and returns [].

    A listing that degrades to empty beats a 500 on a page render — but note
    this is *reads only*. Writes raise, because a save that silently did
    nothing is how a recruiter loses an interview they just recorded.
    """
    try:
        with _get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return [_to_doc(row, mapping) for row in cur.fetchall()]
    except Exception:
        logger.exception("Query failed: %s", sql)
        return []


def _count(sql: str, params: tuple | list = ()) -> int | None:
    """A COUNT, or None when the query itself did not run."""
    try:
        with _get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
        return int(row["n"]) if row else 0
    except Exception:
        logger.exception("Count failed: %s", sql)
        return None


def _upsert(sql: str, doc: dict, mapping: dict[str, str], label: str) -> dict:
    try:
        with _get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(sql, _row_values(doc, mapping))
            row = cur.fetchone()
        return _to_doc(row, mapping) if row else doc
    except Exception as exc:
        logger.exception("Failed to save %s.", label)
        raise StoreError(f"Could not save the {label}. {exc}") from exc


def _like(term: str) -> str:
    r"""A substring pattern with the user's own %, _ and \ taken literally."""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


# ── Candidates ───────────────────────────────────────────────────────
def save_candidate(doc: dict) -> dict:
    return _upsert(_UPSERT_CANDIDATE, doc, CANDIDATE_MAP, "candidate")


def get_candidate(candidate_id: str) -> dict | None:
    rows = _query(
        "SELECT * FROM candidates WHERE id = %s",
        (candidate_id,),
        mapping=CANDIDATE_MAP,
    )
    return rows[0] if rows else None


def _find_by_array(column: str, value: str) -> list[dict]:
    """Candidates holding ``value`` in one of their identity arrays."""
    if not value:
        return []
    return _query(
        f"SELECT * FROM candidates WHERE {column} @> ARRAY[%s]::text[]",
        (value,),
        mapping=CANDIDATE_MAP,
    )


def find_by_email(email: str) -> list[dict]:
    return _find_by_array("emails", email)


def find_by_phone_key(phone_key: str) -> list[dict]:
    return _find_by_array("phone_keys", phone_key)


def find_by_linkedin(slug: str) -> list[dict]:
    return _find_by_array("linkedins", slug)


def find_by_github(handle: str) -> list[dict]:
    return _find_by_array("githubs", handle)


def find_by_name_key(name_key: str) -> list[dict]:
    if not name_key:
        return []
    return _query(
        "SELECT * FROM candidates WHERE name_key = %s",
        (name_key,),
        mapping=CANDIDATE_MAP,
    )


def _candidate_filters(search: str = "", status: str = "") -> tuple[str, list[Any]]:
    """The WHERE clause for the candidate list's search box and status filter.

    The search is a substring over the display name and the raw contact fields
    — whoever is looking for "ayesha" should not have to know how the matcher
    normalizes it.

    The status is the candidate's LATEST one: the badge the list shows. Someone
    rejected in 2024 and selected this year is "Selected", and filtering on
    "Rejected" should not bring them back.
    """
    clauses: list[str] = []
    params: list[Any] = []
    if search:
        pattern = _like(search.lower())
        clauses.append(
            "(lower(display_name) LIKE %s ESCAPE '\\' "
            "OR lower(search_blob) LIKE %s ESCAPE '\\')"
        )
        params += [pattern, pattern]
    if status:
        clauses.append("last_status = %s")
        params.append(status)
    where = f"WHERE {' AND '.join(clauses)} " if clauses else ""
    return where, params


def list_candidates(search: str = "", limit: int = 200, status: str = "") -> list[dict]:
    """Every candidate, newest activity first, optionally filtered."""
    where, params = _candidate_filters(search, status)
    params.append(int(limit))
    return _query(
        f"SELECT * FROM candidates {where}ORDER BY last_interview_at DESC LIMIT %s",
        params,
        mapping=CANDIDATE_MAP,
    )


def count_candidates_by_status(search: str = "") -> dict[str, int]:
    """How many candidates are at each status, within the current search.

    These are the numbers on the filter chips, so the status filter itself is
    deliberately NOT applied: counting only the chosen status would make every
    other chip read 0 the moment one is picked. Candidates with no status come
    back under "" and still count towards the total.
    """
    where, params = _candidate_filters(search)
    try:
        with _get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT last_status AS status, count(*) AS n FROM candidates {where}"
                "GROUP BY last_status",
                params,
            )
            return {row["status"]: int(row["n"]) for row in cur.fetchall()}
    except Exception:
        logger.exception("Status count failed.")
        return {}


def list_demo_candidates() -> list[dict]:
    """The records seed_demo.py planted, so it can take them away again."""
    return _query("SELECT * FROM candidates WHERE demo_seed", mapping=CANDIDATE_MAP)


def count_candidates() -> int:
    return _count("SELECT count(*) AS n FROM candidates") or 0


# ── Interviews ───────────────────────────────────────────────────────
def save_interview(doc: dict) -> dict:
    return _upsert(_UPSERT_INTERVIEW, doc, INTERVIEW_MAP, "interview")


def get_interview(interview_id: str, candidate_id: str) -> dict | None:
    rows = _query(
        "SELECT * FROM interviews WHERE id = %s AND candidate_id = %s",
        (interview_id, candidate_id),
        mapping=INTERVIEW_MAP,
    )
    return rows[0] if rows else None


def list_interviews_for(candidate_id: str) -> list[dict]:
    """One person's full history, newest first."""
    return _query(
        "SELECT * FROM interviews WHERE candidate_id = %s ORDER BY interview_datetime DESC",
        (candidate_id,),
        mapping=INTERVIEW_MAP,
    )


def list_recent_interviews(limit: int = 50) -> list[dict]:
    return _query(
        "SELECT * FROM interviews ORDER BY created_at DESC LIMIT %s",
        (int(limit),),
        mapping=INTERVIEW_MAP,
    )


def count_interviews() -> int:
    return _count("SELECT count(*) AS n FROM interviews") or 0


def count_interviews_for(candidate_id: str) -> int | None:
    """How many interviews one candidate has, or None when the read FAILED.

    The distinction matters to the caller that decides whether a candidate
    still has any history left. A plain count would report 0 for "the database
    blinked" — and deleting somebody's record on that basis would be
    unrecoverable.
    """
    return _count(
        "SELECT count(*) AS n FROM interviews WHERE candidate_id = %s",
        (candidate_id,),
    )


def delete_candidate(candidate_id: str) -> bool:
    """Remove a person. Their interviews go too, by ON DELETE CASCADE."""
    try:
        with _get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM candidates WHERE id = %s", (candidate_id,))
            return cur.rowcount > 0
    except Exception:
        logger.exception("Failed to delete candidate %s.", candidate_id)
        return False


def delete_interview(interview_id: str, candidate_id: str) -> bool:
    try:
        with _get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM interviews WHERE id = %s AND candidate_id = %s",
                (interview_id, candidate_id),
            )
            return cur.rowcount > 0
    except Exception:
        logger.exception("Failed to delete interview %s.", interview_id)
        return False
