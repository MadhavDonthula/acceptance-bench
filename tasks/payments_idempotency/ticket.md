Customers are sometimes charged twice when the client retries. Make charge
creation idempotent using an Idempotency-Key header. Keep existing behavior for
requests without a key. Also add GET /customers/{id}/charges.
