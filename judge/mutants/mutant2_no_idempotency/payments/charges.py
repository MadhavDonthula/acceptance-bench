"""Charge and refund business logic.

Charge creation is idempotent when the caller supplies an Idempotency-Key:
the key is stored alongside a hash of the request body, so a retry with the same
key returns the original charge, a reused key with a different body is a 409, and
concurrent retries collapse to exactly one charge. Requests without a key behave
as they always did (every call creates a new charge).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone

from . import db

# Per-key in-process locks serialize concurrent requests that share an
# Idempotency-Key, so the "check key then insert charge" sequence is atomic.
# FastAPI runs the sync endpoints in a threadpool within one process, so an
# in-process lock is sufficient and keeps the concurrency test fast and
# deterministic (no SQLite write contention between the racing retries).
_key_locks_guard = threading.Lock()
_key_locks: dict[str, threading.Lock] = {}


def _lock_for(key: str) -> threading.Lock:
    with _key_locks_guard:
        lock = _key_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _key_locks[key] = lock
        return lock


def _request_hash(customer_id: str, amount_cents: int, currency: str) -> str:
    payload = json.dumps(
        {"customer_id": customer_id, "amount_cents": amount_cents, "currency": currency.lower()},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


class ChargeError(Exception):
    """Raised for invalid charge/refund operations. Carries an HTTP status."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _charge_row_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "customer_id": row["customer_id"],
        "amount_cents": row["amount_cents"],
        "currency": row["currency"],
        "status": row["status"],
        "created_at": row["created_at"],
    }


def _insert_charge(conn, customer_id: str, amount_cents: int, currency: str) -> dict:
    charge_id = "ch_" + uuid.uuid4().hex[:24]
    created_at = _now()
    conn.execute(
        "INSERT INTO charges (id, customer_id, amount_cents, currency, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (charge_id, customer_id, amount_cents, currency.lower(), "succeeded", created_at),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM charges WHERE id = ?", (charge_id,)).fetchone()
    return _charge_row_to_dict(row)


def create_charge(
    customer_id: str,
    amount_cents: int,
    currency: str,
    idempotency_key: str | None = None,
) -> dict:
    if amount_cents <= 0:
        raise ChargeError(422, "amount_cents must be positive")
    if len(currency) != 3:
        raise ChargeError(422, "currency must be a 3-letter code")

    # MUTANT 2: idempotency key ignored -> every call creates a new charge.
    if True:
        conn = db.get_conn()
        try:
            if conn.execute(
                "SELECT id FROM customers WHERE id = ?", (customer_id,)
            ).fetchone() is None:
                raise ChargeError(404, f"unknown customer {customer_id}")
            return _insert_charge(conn, customer_id, amount_cents, currency)
        finally:
            conn.close()

    req_hash = _request_hash(customer_id, amount_cents, currency)
    with _lock_for(idempotency_key):
        conn = db.get_conn()
        try:
            existing = conn.execute(
                "SELECT request_hash, charge_id FROM idempotency_keys WHERE key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                if existing["request_hash"] != req_hash:
                    raise ChargeError(
                        409, "Idempotency-Key reused with a different request body"
                    )
                charge = conn.execute(
                    "SELECT * FROM charges WHERE id = ?", (existing["charge_id"],)
                ).fetchone()
                return _charge_row_to_dict(charge)

            if conn.execute(
                "SELECT id FROM customers WHERE id = ?", (customer_id,)
            ).fetchone() is None:
                raise ChargeError(404, f"unknown customer {customer_id}")

            charge = _insert_charge(conn, customer_id, amount_cents, currency)
            try:
                conn.execute(
                    "INSERT INTO idempotency_keys (key, request_hash, charge_id, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (idempotency_key, req_hash, charge["id"], _now()),
                )
                conn.commit()
            except sqlite3.IntegrityError:
                # Lost a cross-process race (defensive; the in-process lock makes
                # this unreachable for same-process retries). Fall back to the
                # stored charge.
                conn.rollback()
                row = conn.execute(
                    "SELECT charge_id FROM idempotency_keys WHERE key = ?",
                    (idempotency_key,),
                ).fetchone()
                charge = _charge_row_to_dict(
                    conn.execute(
                        "SELECT * FROM charges WHERE id = ?", (row["charge_id"],)
                    ).fetchone()
                )
            return charge
        finally:
            conn.close()


def list_customer_charges(customer_id: str, limit: int = 20, offset: int = 0) -> list:
    """Return a customer's charges, newest first (deterministic), paginated."""
    limit = max(0, min(int(limit), 100))
    offset = max(0, int(offset))
    conn = db.get_conn()
    try:
        if conn.execute(
            "SELECT id FROM customers WHERE id = ?", (customer_id,)
        ).fetchone() is None:
            raise ChargeError(404, f"unknown customer {customer_id}")
        # rowid DESC is a deterministic "newest first" even when created_at ties.
        rows = conn.execute(
            "SELECT * FROM charges WHERE customer_id = ? "
            "ORDER BY rowid DESC LIMIT ? OFFSET ?",
            (customer_id, limit, offset),
        ).fetchall()
        return [_charge_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def get_charge(charge_id: str) -> dict:
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM charges WHERE id = ?", (charge_id,)
        ).fetchone()
        if row is None:
            raise ChargeError(404, f"unknown charge {charge_id}")
        return _charge_row_to_dict(row)
    finally:
        conn.close()


def refund_charge(charge_id: str, amount_cents: int) -> dict:
    if amount_cents <= 0:
        raise ChargeError(422, "amount_cents must be positive")

    conn = db.get_conn()
    try:
        charge = conn.execute(
            "SELECT * FROM charges WHERE id = ?", (charge_id,)
        ).fetchone()
        if charge is None:
            raise ChargeError(404, f"unknown charge {charge_id}")

        already = conn.execute(
            "SELECT COALESCE(SUM(amount_cents), 0) AS total FROM refunds WHERE charge_id = ?",
            (charge_id,),
        ).fetchone()["total"]
        if already + amount_cents > charge["amount_cents"]:
            raise ChargeError(422, "refund exceeds charge amount")

        refund_id = "re_" + uuid.uuid4().hex[:24]
        conn.execute(
            "INSERT INTO refunds (id, charge_id, amount_cents, created_at) VALUES (?, ?, ?, ?)",
            (refund_id, charge_id, amount_cents, _now()),
        )
        new_total = already + amount_cents
        new_status = "refunded" if new_total == charge["amount_cents"] else "partially_refunded"
        conn.execute(
            "UPDATE charges SET status = ? WHERE id = ?", (new_status, charge_id)
        )
        conn.commit()
        return {
            "id": refund_id,
            "charge_id": charge_id,
            "amount_cents": amount_cents,
            "created_at": _now(),
            "charge_status": new_status,
        }
    finally:
        conn.close()
