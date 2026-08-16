"""
Database connection helpers.

Provides a single SQLAlchemy 2.x engine factory configured from environment
variables (or a .env file). All other modules import get_engine() from here.
"""
from __future__ import annotations

import os
from urllib.parse import quote_plus

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

load_dotenv()


def _require_env(key: str) -> str:
    value = os.getenv(key)
    if value is None:
        raise RuntimeError(f"Required environment variable {key} is not set")
    return value


def get_database_url() -> str:
    """Build a SQLAlchemy PostgreSQL URL from environment variables."""
    user = _require_env("POSTGRES_USER")
    password = _require_env("POSTGRES_PASSWORD")
    host = os.getenv("DB_HOST", "localhost")
    port = int(os.getenv("DB_PORT", "5432"))
    db = _require_env("POSTGRES_DB")

    return (
        f"postgresql+psycopg2://{quote_plus(user)}:{quote_plus(password)}"
        f"@{host}:{port}/{quote_plus(db)}"
    )


def get_engine(echo: bool = False) -> Engine:
    """Return a configured SQLAlchemy engine."""
    return create_engine(get_database_url(), echo=echo, future=True)


def execute_sql(engine: Engine, sql: str, params: dict | None = None) -> None:
    """Execute a raw SQL statement inside a transaction."""
    with engine.begin() as conn:
        conn.execute(text(sql), params or {})


def fetch_one(engine: Engine, sql: str, params: dict | None = None):
    """Fetch a single row from a SELECT."""
    with engine.connect() as conn:
        result = conn.execute(text(sql), params or {})
        return result.fetchone()


def fetch_all(engine: Engine, sql: str, params: dict | None = None):
    """Fetch all rows from a SELECT."""
    with engine.connect() as conn:
        result = conn.execute(text(sql), params or {})
        return result.fetchall()
