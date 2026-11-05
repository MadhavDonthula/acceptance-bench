"""Measure check-4 (concurrency) sensitivity.

Runs ONLY hidden check 4 many times against:
  * the non-atomic mutant (mutant4) -> how often does check 4 catch the race?
  * the reference solution        -> is check 4 flake-free (should pass every time)?

Target: catch rate >= ~95% on the mutant AND 50/50 pass on the reference. If the
mutant catch rate is too low, strengthen check 4 in hidden_tests/test_hidden.py
and re-run this.

Run:  python -m judge.check4_sensitivity [N]
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

from judge.judge import HIDDEN_DIR, TASK_DIR
from judge.mutants.build_mutants import HERE as MUTANT_DIR

CHECK4_NODE = "zz_hidden_test.py::test_check4_concurrent_same_key"


def run_check4_once(workspace: str) -> bool:
    """Return True if check 4 PASSES against this workspace."""
    tmp = tempfile.mkdtemp(prefix="c4_")
    copy = os.path.join(tmp, "ws")
    try:
        shutil.copytree(workspace, copy, ignore=shutil.ignore_patterns(
            ".git", "__pycache__", "*.pyc", "payments.db", ".pytest_cache"))
        shutil.copyfile(os.path.join(HIDDEN_DIR, "test_hidden.py"),
                        os.path.join(copy, "zz_hidden_test.py"))
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", CHECK4_NODE, "-q", "-p", "no:cacheprovider"],
            cwd=copy, capture_output=True, text=True, timeout=120,
        )
        return proc.returncode == 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def measure(label: str, workspace: str, n: int) -> tuple[int, int]:
    passes = 0
    for i in range(n):
        if run_check4_once(workspace):
            passes += 1
        print(f"\r  {label}: {i+1}/{n} (passes={passes})", end="", flush=True)
    print()
    return passes, n


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    mutant = os.path.join(MUTANT_DIR, "mutant4_nonatomic_no_unique_no_lock")
    reference = os.path.join(TASK_DIR, "reference_solution")

    print(f"Check-4 sensitivity over N={n} runs each\n")
    m_pass, _ = measure("mutant4 (non-atomic)", mutant, n)
    r_pass, _ = measure("reference", reference, n)

    m_caught = n - m_pass          # check 4 FAILS on the mutant == caught
    catch_rate = 100.0 * m_caught / n
    print("\n=== results ===")
    print(f"mutant4: check 4 CAUGHT the race {m_caught}/{n} = {catch_rate:.1f}%  "
          f"(mutant passed check 4 {m_pass}/{n} times = missed)")
    print(f"reference: check 4 PASSED {r_pass}/{n}  "
          f"({'no flakiness' if r_pass == n else 'FLAKY!'})")
    print(f"\nPASS CRITERIA: catch_rate >= 95% AND reference {n}/{n}.")
    good = catch_rate >= 95.0 and r_pass == n
    print("RESULT:", "OK" if good else "NEEDS STRENGTHENING")
    return 0 if good else 1


if __name__ == "__main__":
    raise SystemExit(main())
