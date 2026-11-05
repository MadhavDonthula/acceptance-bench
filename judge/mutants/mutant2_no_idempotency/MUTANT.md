# mutant2_no_idempotency

Intended failing check: **2**

create_charge ignores the Idempotency-Key entirely (always creates a new charge).
