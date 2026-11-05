"""FastAPI payments service.

Endpoints:
  POST /charges            create a charge
  GET  /charges/{id}       fetch a charge
  POST /refunds            refund (full or partial) a charge
  GET  /healthz            liveness
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import charges, db


class ChargeIn(BaseModel):
    customer_id: str
    amount_cents: int = Field(..., description="positive integer amount in cents")
    currency: str = Field("usd", description="3-letter currency code")


class RefundIn(BaseModel):
    charge_id: str
    amount_cents: int


def create_app() -> FastAPI:
    app = FastAPI(title="payments")

    @app.on_event("startup")
    def _startup() -> None:
        db.init_db()
        # a default customer so the service is usable out of the box
        db.seed_customer("cus_demo", "Demo Customer")

    @app.exception_handler(charges.ChargeError)
    async def _charge_error_handler(_: Request, exc: charges.ChargeError):
        return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True}

    @app.post("/charges", status_code=201)
    def post_charge(body: ChargeIn) -> dict:
        return charges.create_charge(body.customer_id, body.amount_cents, body.currency)

    @app.get("/charges/{charge_id}")
    def get_charge(charge_id: str) -> dict:
        return charges.get_charge(charge_id)

    @app.post("/refunds", status_code=201)
    def post_refund(body: RefundIn) -> dict:
        return charges.refund_charge(body.charge_id, body.amount_cents)

    return app


app = create_app()
