"""Verify the judge end-to-end:
  * the starter must FAIL (first failing check = 2),
  * the reference must PASS all 7 checks,
  * each mutant in judge/mutants/ must fail EXACTLY its intended check
    (the judge's first-failing check equals the mutant's target).

Run with:  uv run python -m judge.verify_task
Exit 0 only if all of the above hold.
"""

from __future__ import annotations

import json
import os
import sys
import time

from judge.judge import TASK_DIR, judge_workspace
from judge.mutants.build_mutants import HERE as MUTANT_DIR
from judge.mutants.build_mutants import MUTANTS, build

STARTER = os.path.join(TASK_DIR, "starter")
REFERENCE = os.path.join(TASK_DIR, "reference_solution")


def main() -> int:
    print("== Verifying judge ==")
    t0 = time.monotonic()
    ok = True

    starter = judge_workspace(STARTER)
    print("\n-- starter --")
    print(json.dumps(starter.to_dict(), indent=2))
    if starter.accepted or starter.first_failed != 2:
        print("FAIL: starter should be REJECTED at check 2.")
        ok = False

    reference = judge_workspace(REFERENCE)
    print("\n-- reference_solution --")
    print(json.dumps(reference.to_dict(), indent=2))
    if not reference.accepted:
        print("FAIL: reference should be ACCEPTED (all 7 checks).")
        ok = False

    print("\n-- mutation testing (rebuild mutants from reference, then judge) --")
    build()  # regenerate so mutants can never drift from the reference
    for name, intended, desc, _ in MUTANTS:
        r = judge_workspace(os.path.join(MUTANT_DIR, name))
        good = (not r.accepted) and r.first_failed == intended
        ok = ok and good
        passed = sorted(k for k, v in r.checks.items() if v)
        print(f"  {'OK  ' if good else 'FAIL'} {name:40s} "
              f"first_failed={r.first_failed} (intended {intended}) passed={passed}")
        if not good:
            print(f"        -> {desc}")

    total = time.monotonic() - t0
    print(f"\nTotal verify runtime: {total:.2f}s")
    if not ok:
        print("\nFAIL: judge verification did not meet all expectations.")
        return 1

    print(f"\nOK: starter REJECTED@{starter.first_failed}, reference ACCEPTED, "
          f"and all {len(MUTANTS)} mutants fail exactly their intended check.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
