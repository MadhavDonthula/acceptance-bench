"""Outer review-driven loop: the pilot.

3 runs per provider, interleaved in randomized pairs, STRICTLY sequential (never
concurrent). Per run: fresh starter copy -> git init -> OpenCode on the ticket ->
commit -> judge -> if not accepted, send the first-failing-check feedback into
the SAME session (--continue) for the next round.

Caps: 6 rounds, 10 min/round, 25 min/run. Spend guard: stop the whole pilot if
cumulative cost at public prices would exceed $5. Every run is recorded with
exactly one outcome; failed runs are never dropped.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from datetime import datetime, timezone

from harness.common import (
    CALLS_PATH, MAX_ROUND_SECONDS, MAX_ROUNDS, MAX_RUN_SECONDS, RAW_DIR,
    ROUNDS_JSONL, RUNS_DIR, RUNS_JSONL, SPEND_CAP_USD, STARTER_DIR,
    build_run_plan, git_commit_all, git_init_commit, load_providers, load_settings,
    read_ticket, write_opencode_config,
)
from harness.launcher import run_round
from harness.proxy_manager import ProxyManager
from judge.judge import judge_workspace
from proxy.config import load_dotenv

load_dotenv()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append(path: str, rec: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(rec, default=str) + "\n")


def _calls_for(run_id: str, rnd: int | None = None) -> list[dict]:
    if not os.path.exists(CALLS_PATH):
        return []
    out = []
    for ln in open(CALLS_PATH):
        try:
            r = json.loads(ln)
        except Exception:
            continue
        if r.get("run_id") != run_id:
            continue
        if rnd is not None and str(r.get("round")) != str(rnd):
            continue
        out.append(r)
    return out


def _totals(calls: list[dict]) -> dict:
    def s(key):
        return sum(int(c.get(key) or 0) for c in calls)
    statuses = [c.get("http_status") for c in calls]
    return {
        "calls": len(calls),
        "uncached_tokens": s("uncached_tokens"),
        "cached_tokens": s("cached_tokens"),
        "output_tokens": s("output_tokens"),
        "reasoning_tokens": s("reasoning_tokens"),
        "cost_usd_public": round(sum(float(c.get("cost_usd_public") or 0) for c in calls), 6),
        "n_200": sum(1 for s_ in statuses if s_ == 200),
        "n_429": sum(1 for s_ in statuses if s_ == 429),
        "n_529": sum(1 for s_ in statuses if s_ == 529),
        "n_5xx": sum(1 for s_ in statuses if isinstance(s_, int) and 500 <= s_ < 600),
    }


def _total_spend() -> float:
    if not os.path.exists(CALLS_PATH):
        return 0.0
    total = 0.0
    for ln in open(CALLS_PATH):
        try:
            total += float(json.loads(ln).get("cost_usd_public") or 0)
        except Exception:
            pass
    return total


def _provider_error_round(calls: list[dict]) -> bool:
    """A round where the model API never once succeeded but did return errors."""
    if not calls:
        return False
    statuses = [c.get("http_status") for c in calls]
    had_ok = any(s == 200 for s in statuses)
    had_err = any(s is None or (isinstance(s, int) and (s == 429 or s >= 500)) for s in statuses)
    return (not had_ok) and had_err


def _require_keys() -> None:
    providers = load_providers()
    missing = [p.api_key_env for p in providers.values() if not p.api_key()]
    if missing:
        raise SystemExit(
            "Refusing to run the pilot: missing API key(s) in .env: "
            + ", ".join(sorted(set(missing)))
            + "\nBoth arms must have keys. (Preflight would have caught this.)"
        )


def run_one(provider: str, order_index: int, run_id: str) -> dict:
    providers = load_providers()
    model = providers[provider].model
    run_dir = os.path.join(RUNS_DIR, run_id)
    workspace = os.path.join(run_dir, "workspace")
    cfg_path = os.path.join(run_dir, "opencode.json")
    # clear the whole run dir (workspace + any stale captured bodies from a prior run)
    if os.path.exists(run_dir):
        shutil.rmtree(run_dir)
    os.makedirs(run_dir, exist_ok=True)
    shutil.copytree(STARTER_DIR, workspace,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "payments.db", ".pytest_cache"))
    git_init_commit(workspace, "starter")

    ticket = read_ticket()
    run_wall_start = _now()
    run_mono_start = time.monotonic()
    outcome = None
    rounds_done = 0
    abort_pilot = False

    for rnd in range(MAX_ROUNDS):
        rounds_done = rnd + 1
        write_opencode_config(cfg_path, provider, run_id, rnd)
        prompt = ticket if rnd == 0 else feedback  # noqa: F821 (feedback set below)

        elapsed = time.monotonic() - run_mono_start
        remaining = MAX_RUN_SECONDS - elapsed
        if remaining <= 0:
            outcome = "timeout"
            break
        round_timeout = min(MAX_ROUND_SECONDS, remaining)

        agent_start = _now()
        ex = run_round(workspace, cfg_path, prompt, continue_session=(rnd > 0),
                       timeout_s=round_timeout)
        agent_end = _now()

        git_commit_all(workspace, f"round {rnd} ({provider})")

        round_calls = _calls_for(run_id, rnd)
        provider_err = _provider_error_round(round_calls)

        judge_start = _now()
        jr = judge_workspace(workspace)
        judge_end = _now()

        feedback = jr.feedback  # for the next round, if any

        _append(ROUNDS_JSONL, {
            "run_id": run_id, "provider": provider, "round": rnd,
            "agent_phase_start": agent_start, "agent_phase_end": agent_end,
            "agent_duration_s": round(ex.duration_s, 3),
            "agent_rc": ex.rc, "agent_timed_out": ex.timed_out,
            "agent_launch_error": ex.launch_error,
            "judge_start": judge_start, "judge_end": judge_end,
            "judge_runtime_s": jr.runtime_s,
            "checks_passed": sorted(k for k, v in jr.checks.items() if v),
            "checks_failed": sorted(k for k, v in jr.checks.items() if not v),
            "accepted": jr.accepted,
            "first_failed_check": jr.first_failed,
            "feedback_sent": None if jr.accepted else jr.feedback,
            "n_calls_round": len(round_calls),
            "provider_error_round": provider_err,
            "stdout_tail": ex.stdout_tail[-1500:],
        })

        if jr.accepted:
            outcome = "accepted"
            break
        if ex.launch_error is not None:
            outcome = "agent_error"
            break
        if provider_err:
            outcome = "provider_error"
            break
        if ex.timed_out:
            outcome = "timeout"
            break
        if ex.rc not in (0, None):
            outcome = "agent_error"
            break
        if (time.monotonic() - run_mono_start) >= MAX_RUN_SECONDS:
            outcome = "timeout"
            break
        if _total_spend() > SPEND_CAP_USD:
            outcome = outcome or "round_cap"
            abort_pilot = True
            break
    else:
        outcome = "round_cap"

    if outcome is None:
        outcome = "round_cap"

    run_calls = _calls_for(run_id)
    rec = {
        "run_id": run_id, "provider": provider, "model": model,
        "order_index": order_index,
        "wall_start": run_wall_start, "wall_end": _now(),
        "wall_duration_s": round(time.monotonic() - run_mono_start, 3),
        "outcome": outcome, "rounds": rounds_done,
        "totals": _totals(run_calls),
        "_abort_pilot": abort_pilot,
    }
    _append(RUNS_JSONL, {k: v for k, v in rec.items() if k != "_abort_pilot"})
    return rec


def main() -> int:
    load_dotenv()
    _require_keys()

    seed = int(os.environ.get("PILOT_SEED", "20261103"))
    runs_per_arm = int(os.environ.get("RUNS_PER_ARM", "3"))
    arms = tuple(a.strip() for a in os.environ.get("ARMS", "coral,baseten").split(",") if a.strip())
    plan = build_run_plan(seed, runs_per_arm, arms)

    os.makedirs(RAW_DIR, exist_ok=True)
    # Fresh experiment: start clean. Archive the previous calls.jsonl (run_ids are
    # reused across experiments, so appending would collide — e.g. pilot + main
    # both have run00_baseten). This keeps the new experiment's data uncontaminated.
    archive_dir = os.path.join(RAW_DIR, "archive")
    os.makedirs(archive_dir, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for p in (CALLS_PATH, RUNS_JSONL, ROUNDS_JSONL):
        if os.path.exists(p):
            os.rename(p, os.path.join(archive_dir, f"{os.path.basename(p)}.{ts}"))

    # TRACE REPLAY capture: opt-in full-body capture for the first 3 coral and
    # first 3 baseten-1017 runs (pre-registered). run_ids are derived from the plan.
    run_ids = [f"run{i:02d}_{p}" for i, p in enumerate(plan)]
    capture_ids = []
    for want in ("coral", "baseten"):
        capture_ids += [rid for rid in run_ids if rid.endswith("_" + want)][:3]
    os.environ["PROXY_CAPTURE_RUN_IDS"] = ",".join(capture_ids)

    plan_meta = {
        "seed": seed,
        "runs_per_arm": runs_per_arm,
        "arms": list(arms),
        "order": plan,
        "run_ids": run_ids,
        "capture_run_ids": capture_ids,
        "started": _now(),
        "caps": {"max_rounds": MAX_ROUNDS, "max_round_s": MAX_ROUND_SECONDS,
                 "max_run_s": MAX_RUN_SECONDS, "spend_cap_usd": SPEND_CAP_USD},
        "settings": load_settings().__dict__,
    }
    with open(os.path.join(RAW_DIR, "pilot_plan.json"), "w") as f:
        json.dump(plan_meta, f, indent=2)

    print(f"Pilot plan (seed={seed}, arms={arms}): {plan}")
    print(f"Trace-capture run_ids: {capture_ids}")

    with ProxyManager():
        for i, provider in enumerate(plan):
            run_id = f"run{i:02d}_{provider}"
            print(f"\n=== RUN {i} :: {provider} :: {run_id} ===")
            rec = run_one(provider, i, run_id)
            print(f"  outcome={rec['outcome']} rounds={rec['rounds']} "
                  f"wall={rec['wall_duration_s']}s cost=${rec['totals']['cost_usd_public']:.4f} "
                  f"(cumulative ${_total_spend():.4f})")
            if rec["_abort_pilot"]:
                print(f"\n!! SPEND GUARD: cumulative spend exceeded ${SPEND_CAP_USD}. "
                      f"Stopping the pilot. Remaining runs not executed.")
                break

    print(f"\nPilot done. Total recorded spend (public prices): ${_total_spend():.4f}")
    print("Run `make analyze` to build results/SUMMARY.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
