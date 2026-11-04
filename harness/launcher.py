"""Launch OpenCode headlessly for one review round.

Validated OpenCode behavior (see DECISION_LOG step 4):
  * `opencode run "<msg>"` runs headlessly and exits — BUT it blocks forever if
    stdin stays open, so stdin MUST be /dev/null.
  * `--continue` continues the last session in the project (same-session
    follow-up), so feedback rounds keep the agent's full context.
  * Custom provider + model come from a per-run OPENCODE_CONFIG file.
  * permission={edit,bash,webfetch: allow} auto-approves with no prompts.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from harness.common import OPENCODE_BIN, ROOT


@dataclass
class RoundExec:
    rc: int | None
    timed_out: bool
    launch_error: str | None
    stdout_tail: str
    wall_start: str
    wall_end: str
    mono_start: float
    mono_end: float

    @property
    def duration_s(self) -> float:
        return self.mono_end - self.mono_start


def run_round(
    workspace: str,
    config_path: str,
    prompt: str,
    continue_session: bool,
    timeout_s: float,
) -> RoundExec:
    env = {
        **os.environ,
        "OPENCODE_CONFIG": config_path,
        "OPENCODE_DISABLE_AUTOUPDATE": "1",
        "PATH": os.path.join(ROOT, "node_modules", ".bin") + os.pathsep + os.environ.get("PATH", ""),
    }
    cmd = [OPENCODE_BIN, "run"]
    if continue_session:
        cmd.append("--continue")
    cmd.append(prompt)

    wall_start = datetime.now(timezone.utc).isoformat()
    mono_start = time.monotonic()

    try:
        proc = subprocess.Popen(
            cmd, cwd=workspace, env=env,
            stdin=subprocess.DEVNULL,              # critical: or opencode blocks
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, start_new_session=True,     # own process group for clean kill
        )
    except Exception as e:
        now = time.monotonic()
        return RoundExec(
            rc=None, timed_out=False, launch_error=f"{type(e).__name__}: {e}",
            stdout_tail="", wall_start=wall_start,
            wall_end=datetime.now(timezone.utc).isoformat(),
            mono_start=mono_start, mono_end=now,
        )

    timed_out = False
    try:
        out, _ = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except Exception:
            proc.terminate()
        try:
            out, _ = proc.communicate(timeout=10)
        except Exception:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception:
                proc.kill()
            out, _ = proc.communicate()

    mono_end = time.monotonic()
    wall_end = datetime.now(timezone.utc).isoformat()
    out = out or ""
    return RoundExec(
        rc=proc.returncode, timed_out=timed_out, launch_error=None,
        stdout_tail=out[-4000:], wall_start=wall_start, wall_end=wall_end,
        mono_start=mono_start, mono_end=mono_end,
    )
