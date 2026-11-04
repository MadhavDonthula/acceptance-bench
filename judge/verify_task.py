"""Verify the judge: the starter must FAIL and the reference must PASS.

Run with:  uv run python -m judge.verify_task
Exit 0 only if starter is rejected AND reference is accepted.
"""

from __future__ import annotations

import json
import os
import sys
import time

from judge.judge import TASK_DIR, judge_workspace

STARTER = os.path.join(TASK_DIR, "starter")
REFERENCE = os.path.join(TASK_DIR, "reference_solution")


def main() -> int:
    print("== Verifying judge ==")
    t0 = time.monotonic()

    starter = judge_workspace(STARTER)
    print("\n-- starter --")
    print(json.dumps(starter.to_dict(), indent=2))

    reference = judge_workspace(REFERENCE)
    print("\n-- reference_solution --")
    print(json.dumps(reference.to_dict(), indent=2))

    total = time.monotonic() - t0
    print(f"\nTotal verify runtime: {total:.2f}s "
          f"(starter {starter.runtime_s:.2f}s, reference {reference.runtime_s:.2f}s)")

    ok = (not starter.accepted) and reference.accepted
    if not ok:
        print("\nFAIL: expected starter REJECTED and reference ACCEPTED.")
        print(f"  starter.accepted={starter.accepted} (first_failed={starter.first_failed})")
        print(f"  reference.accepted={reference.accepted} (first_failed={reference.first_failed})")
        return 1

    print(f"\nOK: starter REJECTED (first failing check = {starter.first_failed}), "
          f"reference ACCEPTED (all 7 checks pass).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
