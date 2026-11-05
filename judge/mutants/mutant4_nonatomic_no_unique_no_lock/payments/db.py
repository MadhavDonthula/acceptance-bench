"""SQLite storage for the payments service.

A new connection is opened per call (cheap for SQLite) so the service works
correctly when FastAPI runs sync endpoints in its threadpool. The database path
comes from the PAYMENTS_DB env var so tests can point at a throwaway file.
"""

from __future__ import annotations

import os
import sqlite3

DEFAULT_DB = os.path.join(os.path.dirname(__file__), "payments.db")


def db_path() -> str:
    return os.environ.get("PAYMENTS_DB", DEFAULT_DB)


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path(), check_same_thread=False, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS charges (
    id           TEXT PRIMARY KEY,
    customer_id  TEXT NOT NULL REFERENCES customers(id),
    amount_cents INTEGER NOT NULL,
    currency     TEXT NOT NULL,
    status       TEXT NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS refunds (
    id           TEXT PRIMARY KEY,
    charge_id    TEXT NOT NULL REFERENCES charges(id),
    amount_cents INTEGER NOT NULL,
    created_at   TEXT NOT NULL
);

-- Idempotency keys for charge creation. request_hash pins the key to the exact
-- request body so a reused key with a different payload can be rejected (409).
CREATE TABLE IF NOT EXISTS idempotency_keys (
    key          TEXT,  -- MUTANT 4: unique constraint removed
    request_hash TEXT NOT NULL,
    charge_id    TEXT NOT NULL REFERENCES charges(id),
    created_at   TEXT NOT NULL
);
"""


def init_db() -> None:
    conn = get_conn()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def reset_db() -> None:
    """Drop and recreate everything. Used by tests."""
    conn = get_conn()
    try:
        for table in ("idempotency_keys", "refunds", "charges", "customers"):
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        conn.commit()
    finally:
        conn.close()
    init_db()


def seed_customer(customer_id: str, name: str) -> None:
    conn = get_conn()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO customers (id, name, created_at) VALUES (?, ?, ?)",
            (customer_id, name, _now()),
        )
        conn.commit()
    finally:
        conn.close()


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
