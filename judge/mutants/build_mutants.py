"""Materialize one deliberately-flawed solution per check into judge/mutants/.

Each mutant is a full copy of reference_solution with exactly ONE targeted flaw,
designed so the judge's FIRST failing check is the mutant's intended check (all
higher-priority checks still pass). This lets verify_task mutation-test the judge:
a judge that silently accepted a broken solution would be caught here.

Run:  python -m judge.mutants.build_mutants
"""

from __future__ import annotations

import os
import shutil

HERE = os.path.dirname(__file__)
REF = os.path.abspath(os.path.join(HERE, "..", "..", "tasks", "payments_idempotency", "reference_solution"))

# (dirname, intended_check, description, [(relpath, old, new), ...])
MUTANTS = [
    (
        "mutant2_no_idempotency", 2,
        "create_charge ignores the Idempotency-Key entirely (always creates a new charge).",
        [("payments/charges.py",
          "    # No key -> original behavior: every call creates a new charge.\n    if not idempotency_key:",
          "    # MUTANT 2: idempotency key ignored -> every call creates a new charge.\n    if True:")],
    ),
    (
        "mutant3_no_409_on_body_mismatch", 3,
        "A reused key with a different body returns the stored charge instead of 409.",
        [("payments/charges.py",
          '            if existing is not None:\n                if existing["request_hash"] != req_hash:',
          '            if existing is not None:\n                if False:  # MUTANT 3: dropped 409 on body mismatch')],
    ),
    (
        "mutant4_nonatomic_no_unique_no_lock", 4,
        "Non-atomic SELECT-then-INSERT: no per-key lock and no unique constraint on the key.",
        [
            ("payments/charges.py",
             "    req_hash = _request_hash(customer_id, amount_cents, currency)\n    with _lock_for(idempotency_key):",
             "    req_hash = _request_hash(customer_id, amount_cents, currency)\n    if True:  # MUTANT 4: no per-key lock (non-atomic)"),
            ("payments/db.py",
             "    key          TEXT PRIMARY KEY,",
             "    key          TEXT,  -- MUTANT 4: unique constraint removed"),
        ],
    ),
    (
        "mutant5_keyless_collapse", 5,
        "Keyless requests are treated as one shared key, so they wrongly collapse.",
        [("payments/charges.py",
          "    # No key -> original behavior: every call creates a new charge.\n    if not idempotency_key:\n        conn = db.get_conn()\n        try:\n            if conn.execute(\n                \"SELECT id FROM customers WHERE id = ?\", (customer_id,)\n            ).fetchone() is None:\n                raise ChargeError(404, f\"unknown customer {customer_id}\")\n            return _insert_charge(conn, customer_id, amount_cents, currency)\n        finally:\n            conn.close()",
          "    # MUTANT 5: keyless requests wrongly share one key -> they collapse.\n    if not idempotency_key:\n        idempotency_key = \"__shared_keyless__\"")],
    ),
    (
        "mutant6_bad_list_order", 6,
        "List endpoint returns charges oldest-first instead of newest-first.",
        [("payments/charges.py",
          '        # rowid DESC is a deterministic "newest first" even when created_at ties.\n        rows = conn.execute(\n            "SELECT * FROM charges WHERE customer_id = ? "\n            "ORDER BY rowid DESC LIMIT ? OFFSET ?",',
          '        # MUTANT 6: wrong order (oldest first instead of newest first).\n        rows = conn.execute(\n            "SELECT * FROM charges WHERE customer_id = ? "\n            "ORDER BY rowid ASC LIMIT ? OFFSET ?",')],
    ),
    (
        "mutant7_edits_original_test", 7,
        "Correct solution, but it rewrites an original visible test function (keeps it passing).",
        [("tests/test_payments.py",
          '    assert r.json() == {"ok": True}',
          '    assert r.json()["ok"] is True  # MUTANT 7: original test function rewritten')],
    ),
]


def _apply(src: str, old: str, new: str, where: str) -> str:
    if src.count(old) != 1:
        raise RuntimeError(f"patch anchor not found exactly once in {where}: {old[:60]!r}")
    return src.replace(old, new)


def build() -> list[tuple[str, int, str]]:
    meta = []
    for name, check, desc, patches in MUTANTS:
        dst = os.path.join(HERE, name)
        if os.path.exists(dst):
            shutil.rmtree(dst)
        shutil.copytree(REF, dst, ignore=shutil.ignore_patterns(
            "__pycache__", "*.pyc", "payments.db", ".pytest_cache", ".git"))
        for rel, old, new in patches:
            path = os.path.join(dst, rel)
            with open(path) as f:
                src = f.read()
            src = _apply(src, old, new, rel)
            with open(path, "w") as f:
                f.write(src)
        # a note file documenting the intended flaw
        with open(os.path.join(dst, "MUTANT.md"), "w") as f:
            f.write(f"# {name}\n\nIntended failing check: **{check}**\n\n{desc}\n")
        meta.append((name, check, desc))
        print(f"built {name}  (intended check {check})")
    return meta


if __name__ == "__main__":
    build()
