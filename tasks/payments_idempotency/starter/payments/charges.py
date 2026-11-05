"""Charge and refund business logic."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from . import db


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


def create_charge(customer_id: str, amount_cents: int, currency: str) -> dict:
    if amount_cents <= 0:
        raise ChargeError(422, "amount_cents must be positive")
    if len(currency) != 3:
        raise ChargeError(422, "currency must be a 3-letter code")

    conn = db.get_conn()
    try:
        cust = conn.execute(
            "SELECT id FROM customers WHERE id = ?", (customer_id,)
        ).fetchone()
        if cust is None:
            raise ChargeError(404, f"unknown customer {customer_id}")

        charge_id = "ch_" + uuid.uuid4().hex[:24]
        created_at = _now()
        conn.execute(
            "INSERT INTO charges (id, customer_id, amount_cents, currency, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (charge_id, customer_id, amount_cents, currency.lower(), "succeeded", created_at),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM charges WHERE id = ?", (charge_id,)
        ).fetchone()
        return _charge_row_to_dict(row)
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
