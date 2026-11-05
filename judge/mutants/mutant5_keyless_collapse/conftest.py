"""Pytest fixtures: a throwaway SQLite db and a TestClient per test."""

import os
import tempfile

import pytest


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_file = tmp_path / "payments_test.db"
    monkeypatch.setenv("PAYMENTS_DB", str(db_file))

    # Import after the env var is set so the app uses the test database.
    from fastapi.testclient import TestClient

    from payments import db
    from payments.app import create_app

    db.reset_db()
    db.seed_customer("cus_test", "Test Customer")

    app = create_app()
    with TestClient(app) as c:
        yield c
