"""Visible test suite for the payments service.

These tests describe the *current* expected behavior. They must keep passing
after the idempotency change (the ticket says: keep existing behavior for
requests without an Idempotency-Key).
"""


def _charge(client, amount=500, currency="usd"):
    return client.post(
        "/charges",
        json={"customer_id": "cus_test", "amount_cents": amount, "currency": currency},
    )


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["ok"] is True  # MUTANT 7: original test function rewritten


def test_create_charge(client):
    r = _charge(client, amount=1234, currency="usd")
    assert r.status_code == 201
    body = r.json()
    assert body["id"].startswith("ch_")
    assert body["customer_id"] == "cus_test"
    assert body["amount_cents"] == 1234
    assert body["currency"] == "usd"
    assert body["status"] == "succeeded"


def test_get_charge(client):
    charge = _charge(client).json()
    r = client.get(f"/charges/{charge['id']}")
    assert r.status_code == 200
    assert r.json()["id"] == charge["id"]


def test_get_unknown_charge_404(client):
    r = client.get("/charges/ch_does_not_exist")
    assert r.status_code == 404


def test_charge_unknown_customer_404(client):
    r = client.post(
        "/charges",
        json={"customer_id": "cus_nope", "amount_cents": 100, "currency": "usd"},
    )
    assert r.status_code == 404


def test_charge_rejects_nonpositive_amount(client):
    r = _charge(client, amount=0)
    assert r.status_code == 422


def test_full_refund(client):
    charge = _charge(client, amount=800).json()
    r = client.post("/refunds", json={"charge_id": charge["id"], "amount_cents": 800})
    assert r.status_code == 201
    assert r.json()["charge_status"] == "refunded"
    got = client.get(f"/charges/{charge['id']}").json()
    assert got["status"] == "refunded"


def test_partial_refund(client):
    charge = _charge(client, amount=800).json()
    r = client.post("/refunds", json={"charge_id": charge["id"], "amount_cents": 300})
    assert r.status_code == 201
    assert r.json()["charge_status"] == "partially_refunded"


def test_refund_cannot_exceed_charge(client):
    charge = _charge(client, amount=800).json()
    client.post("/refunds", json={"charge_id": charge["id"], "amount_cents": 500})
    r = client.post("/refunds", json={"charge_id": charge["id"], "amount_cents": 500})
    assert r.status_code == 422
