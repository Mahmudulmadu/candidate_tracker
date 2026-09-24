"""Test setup: point the app at a scratch database, wipe it between tests.

The database tests run against a REAL PostgreSQL server — the same one the app
uses, but a database named `<PG_DATABASE>_test` that is created on first use
and truncated before every test. Nothing here can touch real data, and there
is no mock to drift out of step with how psycopg actually behaves.

Set PG_* in the environment (or .env) as usual; the "_test" suffix is added
here. Override it with TEST_PG_DATABASE if you want the scratch database
somewhere else entirely.
"""

from __future__ import annotations

import os

import pytest

# Must happen BEFORE app.config is imported, since settings are read once at
# import time. load_dotenv() does not override variables already set, so this
# wins over .env.
_base = os.environ.get("PG_DATABASE", "")
if not _base:
    # .env has not been read yet, so read just the one key out of it.
    from pathlib import Path

    env_file = Path(__file__).resolve().parent.parent / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("PG_DATABASE="):
                _base = line.split("=", 1)[1].strip()
                break
os.environ["PG_DATABASE"] = os.environ.get(
    "TEST_PG_DATABASE", f"{_base or 'interview_tracker'}_test"
)

from app import store  # noqa: E402  (after the env is set)


@pytest.fixture(autouse=True)
def clean_database():
    """Every test starts from an empty database.

    TRUNCATE rather than DELETE: it resets both tables in one statement, and
    CASCADE follows the foreign key so interviews go with their candidates.
    """
    try:
        pool = store._get_pool()
    except Exception as exc:  # pragma: no cover - only when Postgres is down
        pytest.skip(f"PostgreSQL is not reachable: {exc}")
    with pool.connection() as conn:
        conn.execute("TRUNCATE candidates, interviews CASCADE")
    yield


@pytest.fixture(scope="session", autouse=True)
def close_pool():
    yield
    store.close()
