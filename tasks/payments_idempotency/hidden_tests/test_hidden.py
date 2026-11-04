"""Hidden acceptance checks 2-6 (driven at the HTTP boundary).

NEVER copied into an agent workspace except inside the judge's throwaway copy.
Each test is self-contained: it points PAYMENTS_DB at its own temp file, resets
the schema, seeds a customer, and drives the app via Starlette's TestClient.

The app contract exercised here (payments.app:create_app, payments.db.reset_db,
payments.db.seed_customer) is the same one pinned by the visible suite, so a
solution that keeps the visible suite green keeps these importable too.
"""

import threading

import pytest

CUST = "cus_hidden"


def _make_client(tmp_path):
    import os

    os.environ["PAYMENTS_DB"] = str(tmp_path / "hidden.db")
    from fastapi.testclient import TestClient

    from payments import db
    from payments.app import create_app

    db.reset_db()
    db.seed_customer(CUST, "Hidden Customer")
    return TestClient(create_app())


@pytest.fixture()
def client(tmp_path):
    with _make_client(tmp_path) as c:
        yield c


def _charge_body(amount=500, currency="usd"):
    return {"customer_id": CUST, "amount_cents": amount, "currency": currency}


# ---- check 2: same key twice -> exactly one charge, same response ----------
def test_check2_same_key_twice(client):
    headers = {"Idempotency-Key": "key-check2"}
    r1 = client.post("/charges", json=_charge_body(500), headers=headers)
    assert r1.status_code == 201, r1.text
    r2 = client.post("/charges", json=_charge_body(500), headers=headers)
    assert r2.status_code in (200, 201), r2.text
    b1, b2 = r1.json(), r2.json()
    # same charge returned -> client was not double charged
    assert b1["id"] == b2["id"], (b1, b2)
    assert b2["amount_cents"] == 500
    assert b2["customer_id"] == CUST


# ---- check 3: same key, different body -> 409 ------------------------------
def test_check3_same_key_different_body_409(client):
    headers = {"Idempotency-Key": "key-check3"}
    r1 = client.post("/charges", json=_charge_body(500), headers=headers)
    assert r1.status_code == 201, r1.text
    r2 = client.post("/charges", json=_charge_body(999), headers=headers)
    assert r2.status_code == 409, r2.text


# ---- check 4: concurrent same key -> exactly one charge --------------------
def test_check4_concurrent_same_key(client):
    headers = {"Idempotency-Key": "key-check4"}
    n = 10
    barrier = threading.Barrier(n)
    results = [None] * n

    def worker(i):
        barrier.wait()  # release all threads together
        r = client.post("/charges", json=_charge_body(700), headers=headers)
        results[i] = r

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert all(r is not None for r in results), "a concurrent request never returned"
    for r in results:
        assert r.status_code in (200, 201), r.text
    ids = {r.json()["id"] for r in results}
    assert len(ids) == 1, f"concurrent retries created {len(ids)} charges: {ids}"


# ---- check 5: no key -> behaves as before (distinct charges) ---------------
def test_check5_no_key_distinct(client):
    r1 = client.post("/charges", json=_charge_body(500))
    r2 = client.post("/charges", json=_charge_body(500))
    assert r1.status_code == 201 and r2.status_code == 201
    assert r1.json()["id"] != r2.json()["id"]


# ---- check 6: GET /customers/{id}/charges newest first, paginated ----------
def test_check6_list_newest_first_paginated(client):
    created = []
    for amount in (100, 200, 300, 400, 500):
        r = client.post("/charges", json=_charge_body(amount))
        assert r.status_code == 201, r.text
        created.append(r.json()["id"])
    newest_first = list(reversed(created))  # last created should come first

    r = client.get(f"/customers/{CUST}/charges")
    assert r.status_code == 200, r.text
    payload = r.json()
    items = payload["data"] if isinstance(payload, dict) and "data" in payload else payload
    got_ids = [c["id"] for c in items]
    assert got_ids == newest_first, (got_ids, newest_first)

    r = client.get(f"/customers/{CUST}/charges?limit=2")
    items = r.json()
    items = items["data"] if isinstance(items, dict) and "data" in items else items
    assert [c["id"] for c in items] == newest_first[:2]

    r = client.get(f"/customers/{CUST}/charges?limit=2&offset=1")
    items = r.json()
    items = items["data"] if isinstance(items, dict) and "data" in items else items
    assert [c["id"] for c in items] == newest_first[1:3]
