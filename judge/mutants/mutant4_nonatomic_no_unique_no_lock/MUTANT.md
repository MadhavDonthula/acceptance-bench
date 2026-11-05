# mutant4_nonatomic_no_unique_no_lock

Intended failing check: **4**

Non-atomic SELECT-then-INSERT: no per-key lock and no unique constraint on the key.
