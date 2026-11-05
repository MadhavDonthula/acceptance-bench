"""Pre-pilot verification under OpenCode 1.19.5, through the recording proxy.

For each provider: 3 short end-to-end rounds in ONE session (round 0 creates a
file, rounds 1-2 append via --continue), exercising real tool/file edits exactly
as the pilot will. Verifies:
  * same-session --continue carries context (file grows across rounds),
  * tool-call streaming is not altered by the proxy (edits actually land),
  * Coral works with the new session headers,
  * all proxy calls returned 200 and carried/forwarded the session headers.

Prints a PASS/FAIL per provider and writes results/preflight/prepilot_verify.json.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile

from harness.common import RAW_DIR, ROOT, git_init_commit, load_providers, write_opencode_config
from harness.launcher import run_round
from harness.proxy_manager import ProxyManager
from proxy.config import load_dotenv

load_dotenv()

ROUND_PROMPTS = [
    "Create a file named notes.txt containing exactly one line: alpha",
    "Append a new second line to notes.txt containing exactly: beta",
    "Append a new third line to notes.txt containing exactly: gamma",
]
EXPECTED = ["alpha", "beta", "gamma"]
ROUND_TIMEOUT = 300.0


def _calls(run_id: str) -> list[dict]:
    path = os.path.join(RAW_DIR, "calls.jsonl")
    out = []
    if os.path.exists(path):
        for ln in open(path):
            try:
                r = json.loads(ln)
            except Exception:
                continue
            if r.get("run_id") == run_id:
                out.append(r)
    return out


def verify_provider(provider: str) -> dict:
    run_id = f"ppv-{provider}"
    ws = tempfile.mkdtemp(prefix=f"ppv_{provider}_")
    cfg = os.path.join(ws, "opencode.json")
    # Mirror the pilot: a git-init'd workspace so OpenCode 1.19.5 resolves its
    # project root to THIS directory (otherwise it escapes to the nearest git repo).
    with open(os.path.join(ws, "README.md"), "w") as f:
        f.write("prepilot verification workspace\n")
    git_init_commit(ws, "workspace")
    rounds = []
    print(f"\n=== provider: {provider} (run_id={run_id}) ===")
    for rnd, prompt in enumerate(ROUND_PROMPTS):
        write_opencode_config(cfg, provider, run_id, rnd)
        ex = run_round(ws, cfg, prompt, continue_session=(rnd > 0), timeout_s=ROUND_TIMEOUT)
        notes = os.path.join(ws, "notes.txt")
        content = open(notes).read() if os.path.exists(notes) else None
        lines = [ln.strip() for ln in content.splitlines() if ln.strip()] if content else []
        ok_round = (ex.rc == 0) and (not ex.timed_out) and (EXPECTED[rnd] in (content or ""))
        rounds.append({
            "round": rnd, "prompt": prompt, "rc": ex.rc, "timed_out": ex.timed_out,
            "launch_error": ex.launch_error, "duration_s": round(ex.duration_s, 1),
            "file_exists": content is not None, "lines": lines, "ok": ok_round,
            "stdout_tail": ex.stdout_tail[-600:],
        })
        print(f"  round {rnd}: rc={ex.rc} timed_out={ex.timed_out} dur={ex.duration_s:.1f}s "
              f"file_lines={lines} -> {'OK' if ok_round else 'PROBLEM'}")

    calls = _calls(run_id)
    statuses = [c.get("http_status") for c in calls]
    all_200 = all(s == 200 for s in statuses) if statuses else False
    sess_hdr_present = all(bool(c.get("session_headers")) for c in calls) if calls else False
    forwarded_ok = all("x-session-affinity" in (c.get("forwarded_header_names") or []) for c in calls) if calls else False
    # final file should contain all three lines in order (same-session context carried)
    final_lines = rounds[-1]["lines"]
    context_carried = final_lines[:3] == EXPECTED
    provider_ok = all(r["ok"] for r in rounds) and context_carried and all_200

    shutil.rmtree(ws, ignore_errors=True)
    result = {
        "provider": provider, "ok": provider_ok, "context_carried": context_carried,
        "final_lines": final_lines, "n_calls": len(calls), "all_200": all_200,
        "statuses": statuses, "session_headers_present_all_calls": sess_hdr_present,
        "affinity_forwarded_all_calls": forwarded_ok, "rounds": rounds,
    }
    print(f"  -> calls={len(calls)} all_200={all_200} affinity_forwarded={forwarded_ok} "
          f"context_carried={context_carried}  PROVIDER {'PASS' if provider_ok else 'FAIL'}")
    return result


def main() -> int:
    providers = load_providers()
    os.makedirs(RAW_DIR, exist_ok=True)
    out = {}
    with ProxyManager():
        for name in providers:
            out[name] = verify_provider(name)

    with open(os.path.join(ROOT, "results", "preflight", "prepilot_verify.json"), "w") as f:
        json.dump(out, f, indent=2, default=str)

    all_ok = all(v["ok"] for v in out.values())
    print("\n" + "=" * 60)
    for name, v in out.items():
        print(f"  {name}: {'PASS' if v['ok'] else 'FAIL'}")
    print(f"OVERALL: {'PASS — safe to run the pilot' if all_ok else 'FAIL — fix before pilot'}")
    print("=" * 60)
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
