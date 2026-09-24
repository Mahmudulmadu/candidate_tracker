"""Settings, read once from the environment / .env file.

Deliberately tiny: this app has no auth, no multi-tenancy and no AI, so the
only things worth configuring are where PostgreSQL lives, where uploaded CVs
land, and how eagerly two records are considered the same person.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

from dotenv import load_dotenv

# Project root = the directory holding this package's parent.
ROOT_DIR = Path(__file__).resolve().parent.parent

load_dotenv(ROOT_DIR / ".env")


def _env(key: str, default: str = "") -> str:
    return (os.getenv(key) or default).strip()


def _env_float(key: str, default: float) -> float:
    try:
        return float(_env(key) or default)
    except ValueError:
        return default


def _env_int(key: str, default: int) -> int:
    try:
        return int(_env(key) or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # ── PostgreSQL ───────────────────────────────────────────────────
    # DATABASE_URL wins when it is set, because that is the single string a
    # hosted Postgres hands you. Otherwise the DSN is assembled from the
    # discrete PG_* parts, which are friendlier to fill in by hand next to a
    # local server you administer through pgAdmin.
    database_url: str = field(default_factory=lambda: _env("DATABASE_URL"))
    pg_host: str = field(default_factory=lambda: _env("PG_HOST", "localhost"))
    pg_port: int = field(default_factory=lambda: _env_int("PG_PORT", 5432))
    pg_database: str = field(default_factory=lambda: _env("PG_DATABASE", "interview_tracker"))
    pg_user: str = field(default_factory=lambda: _env("PG_USER", "postgres"))
    pg_password: str = field(default_factory=lambda: _env("PG_PASSWORD"))
    # "prefer" talks TLS when the server offers it and plain text when it does
    # not, which is what a local server on the office machine wants. A managed
    # Postgres over the internet wants "require".
    pg_sslmode: str = field(default_factory=lambda: _env("PG_SSLMODE", "prefer"))
    # FastAPI runs these sync endpoints in a thread pool, so connections are
    # pooled rather than opened per request.
    pg_pool_max: int = field(default_factory=lambda: _env_int("PG_POOL_MAX", 10))

    # ── CV storage ───────────────────────────────────────────────────
    # Local disk, not blob storage: this deploys to one machine in the
    # office, and a folder you can back up with a file copy is easier to
    # reason about than a storage account nobody on the team administers.
    cv_dir: Path = field(default_factory=lambda: ROOT_DIR / _env("CV_STORAGE_DIR", "data/cvs"))
    max_cv_mb: float = field(default_factory=lambda: _env_float("MAX_CV_MB", 15.0))

    # ── Identity resolution ──────────────────────────────────────────
    # Minimum confidence for two records to be reported as the SAME person
    # rather than as a "possible match" needing a human glance.
    #   1.00  email          0.95  LinkedIn / GitHub
    #   0.85  phone          0.40  name only (never auto-links)
    auto_link_min_confidence: float = field(
        default_factory=lambda: _env_float("IDENTITY_AUTO_LINK_MIN_CONFIDENCE", 0.85)
    )
    # Region assumed for a phone written with no country code. "01711223344"
    # is read as +8801711223344 under BD.
    default_phone_region: str = field(
        default_factory=lambda: _env("IDENTITY_DEFAULT_PHONE_REGION", "BD").upper()
    )

    # ── Server ───────────────────────────────────────────────────────
    host: str = field(default_factory=lambda: _env("HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: _env_int("PORT", 8000))

    @property
    def dsn(self) -> str:
        """Connection string for the application database."""
        if self.database_url:
            return self.database_url
        # quote(): a password with an @ or a / in it would otherwise split the
        # URL in the wrong place and point us at a host that does not exist.
        return (
            f"postgresql://{quote(self.pg_user, safe='')}:{quote(self.pg_password, safe='')}"
            f"@{self.pg_host}:{self.pg_port}/{quote(self.pg_database, safe='')}"
            f"?sslmode={self.pg_sslmode}"
        )

    @property
    def maintenance_dsn(self) -> str:
        """Same server, but the always-present ``postgres`` database.

        Used once, to CREATE DATABASE when the configured one does not exist
        yet: you cannot create a database from inside itself.
        """
        parts = urlsplit(self.dsn)
        return urlunsplit(parts._replace(path="/postgres"))

    @property
    def db_name(self) -> str:
        """What the database is called, however the DSN was supplied."""
        if self.database_url:
            return urlsplit(self.database_url).path.lstrip("/") or "postgres"
        return self.pg_database

    @property
    def db_target(self) -> str:
        """One line naming the server, for logs and the startup banner."""
        if self.database_url:
            parts = urlsplit(self.database_url)
            return f"{self.db_name} on {parts.hostname or '?'}:{parts.port or 5432}"
        return f"{self.pg_database} on {self.pg_host}:{self.pg_port}"

    @property
    def db_configured(self) -> bool:
        # No password check: a local server set to "trust" authentication is a
        # perfectly ordinary way to run this, and refusing to start would be
        # wrong. A bad password surfaces as a connection error instead.
        return bool(self.database_url or (self.pg_host and self.pg_database and self.pg_user))


settings = Settings()
settings.cv_dir.mkdir(parents=True, exist_ok=True)
