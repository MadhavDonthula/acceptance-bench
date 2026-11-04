"""Deterministic acceptance judge (no LLM).

Protocol:
  * Copy the agent's workspace into a throwaway temp directory.
  * Copy the hidden tests in and run the 7 prioritized checks against the copy.
  * Delete the copy.
  * ACCEPTED only if all 7 checks pass. Otherwise return the pre-written,
    developer-style feedback for the FIRST failing check in priority order
    (one problem per round).

Priority order of checks:
  1 visible suite still passes
  2 same key twice -> exactly one charge, same response
  3 same key, different body -> 409
  4 concurrent same key -> exactly one charge
  5 requests without a key behave as before
  6 GET /customers/{id}/charges newest first, paginated
  7 hygiene: visible tests unmodified + no new third-party dependencies
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

TASK_DIR = os.path.join(os.path.dirname(__file__), "..", "tasks", "payments_idempotency")
TASK_DIR = os.path.abspath(TASK_DIR)
HIDDEN_DIR = os.path.join(TASK_DIR, "hidden_tests")

CHECK_ORDER = [1, 2, 3, 4, 5, 6, 7]


def _load_feedback() -> dict:
    with open(os.path.join(HIDDEN_DIR, "feedback.json")) as f:
        return {int(k): v for k, v in json.load(f).items()}


def _load_baseline() -> dict:
    with open(os.path.join(HIDDEN_DIR, "baseline_hashes.json")) as f:
        return json.load(f)


@dataclass
class JudgeResult:
    accepted: bool
    first_failed: int | None
    feedback: str | None
    checks: dict = field(default_factory=dict)  # check_num -> bool
    runtime_s: float = 0.0
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "accepted": self.accepted,
            "first_failed": self.first_failed,
            "feedback": self.feedback,
            "checks_passed": sorted(k for k, v in self.checks.items() if v),
            "checks_failed": sorted(k for k, v in self.checks.items() if not v),
            "runtime_s": round(self.runtime_s, 3),
            "detail": self.detail,
        }


def _python() -> str:
    # Prefer the venv python running the judge so pytest + deps are available.
    return sys.executable


def _run_visible_suite(copy_dir: str) -> tuple[bool, str]:
    proc = subprocess.run(
        [_python(), "-m", "pytest", "tests/", "-q", "-p", "no:cacheprovider"],
        cwd=copy_dir,
        capture_output=True,
        text=True,
        timeout=120,
    )
    ok = proc.returncode == 0
    return ok, (proc.stdout + proc.stderr)[-4000:]


def _run_hidden(copy_dir: str) -> tuple[dict, str]:
    """Run hidden checks 2-6 and return {check_num: passed}."""
    src = os.path.join(HIDDEN_DIR, "test_hidden.py")
    dst = os.path.join(copy_dir, "zz_hidden_test.py")
    shutil.copyfile(src, dst)
    junit = os.path.join(copy_dir, "zz_hidden_report.xml")
    proc = subprocess.run(
        [
            _python(), "-m", "pytest", "zz_hidden_test.py",
            "-q", "-p", "no:cacheprovider", f"--junitxml={junit}",
        ],
        cwd=copy_dir,
        capture_output=True,
        text=True,
        timeout=120,
    )
    results = {n: False for n in (2, 3, 4, 5, 6)}
    if os.path.exists(junit):
        tree = ET.parse(junit)
        for case in tree.iter("testcase"):
            name = case.get("name", "")
            failed = any(child.tag in ("failure", "error") for child in case)
            for n in results:
                if f"check{n}" in name:
                    results[n] = not failed
    log = (proc.stdout + proc.stderr)[-4000:]
    return results, log


def _check_hygiene(copy_dir: str) -> tuple[bool, dict]:
    import hashlib

    baseline = _load_baseline()
    detail = {"modified_files": [], "new_requirements": []}
    ok = True

    for rel, expected in baseline["protected_files"].items():
        path = os.path.join(copy_dir, rel)
        if not os.path.exists(path):
            detail["modified_files"].append(rel + " (missing)")
            ok = False
            continue
        with open(path, "rb") as f:
            actual = hashlib.sha256(f.read()).hexdigest()
        if actual != expected:
            detail["modified_files"].append(rel)
            ok = False

    allowed = set(baseline["allowed_requirements"])
    req_path = os.path.join(copy_dir, "requirements.txt")
    if os.path.exists(req_path):
        with open(req_path) as f:
            for ln in f:
                ln = ln.strip()
                if not ln or ln.startswith("#"):
                    continue
                name = ln.split("==")[0].split(">=")[0].split("<")[0].split("[")[0].strip().lower()
                if name and name not in allowed:
                    detail["new_requirements"].append(name)
                    ok = False
    return ok, detail


def judge_workspace(workspace_dir: str) -> JudgeResult:
    start = time.monotonic()
    tmp = tempfile.mkdtemp(prefix="judge_copy_")
    copy_dir = os.path.join(tmp, "workspace")
    try:
        shutil.copytree(
            workspace_dir, copy_dir,
            ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc", "payments.db", ".pytest_cache"),
        )

        checks: dict = {}
        detail: dict = {}

        visible_ok, visible_log = _run_visible_suite(copy_dir)
        checks[1] = visible_ok
        detail["visible_log_tail"] = visible_log[-800:]

        hidden, hidden_log = _run_hidden(copy_dir)
        checks.update(hidden)
        detail["hidden_log_tail"] = hidden_log[-800:]

        hygiene_ok, hygiene_detail = _check_hygiene(copy_dir)
        checks[7] = hygiene_ok
        detail["hygiene"] = hygiene_detail

        feedback_map = _load_feedback()
        first_failed = next((n for n in CHECK_ORDER if not checks.get(n, False)), None)
        accepted = first_failed is None

        return JudgeResult(
            accepted=accepted,
            first_failed=first_failed,
            feedback=None if accepted else feedback_map[first_failed],
            checks=checks,
            runtime_s=time.monotonic() - start,
            detail=detail,
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python -m judge.judge <workspace_dir>", file=sys.stderr)
        sys.exit(2)
    result = judge_workspace(sys.argv[1])
    print(json.dumps(result.to_dict(), indent=2))
    sys.exit(0 if result.accepted else 1)
