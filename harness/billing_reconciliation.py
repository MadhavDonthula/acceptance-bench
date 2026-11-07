"""LAYER C — billing reconciliation. NO API CALLS (pure offline).

Sums proxy-recorded cost + tokens per provider/model from results/raw/calls.jsonl
(and its archived snapshots), for runs BEFORE this caching work, broken into
pilot / main / replay / probes / smoke-preflight / other, and compares to the
provider dashboard figures. Reports any unexplained remainder.

Dedup: the same physical call can appear in several archived snapshots (e.g. the
pre-clean `.dirty` copy vs the cleaned live file). Calls are deduplicated by
(provider, wall_request_start, run_id, round, call_index) so each real call is
counted once. Replays bypassed the proxy, so they are summed from
results/raw/trace_replay.jsonl separately.

Run:  python -m harness.billing_reconciliation
"""

from __future__ import annotations

import json
import os
import re
from collections import defaultdict

from harness.common import RAW_DIR, RESULTS_DIR
from proxy.config import price_date

OUT_MD = os.path.join(RESULTS_DIR, "BILLING_RECONCILIATION.md")
RUNS_JSONL = os.path.join(RAW_DIR, "runs.jsonl")
REPLAY_JSONL = os.path.join(RAW_DIR, "trace_replay.jsonl")

# every proxy-recorded snapshot: the live file + archived copies
CALL_FILES = [
    os.path.join(RAW_DIR, "calls.jsonl"),
    os.path.join(RAW_DIR, "archive", "calls.jsonl.20261105T052940Z"),
    os.path.join(RAW_DIR, "archive", "calls.jsonl.20261105T053438Z"),
    os.path.join(RAW_DIR, "archive", "calls.jsonl.dirty"),
]

RUN_RE = re.compile(r"^run\d+_")

# Provider dashboard figures (given by the operator; tokens in absolute counts).
DASH = {
    ("baseten", "deepseek-ai/DeepSeek-V4.1-Flash-1017-Fast"): {
        "label": "Baseten 1017-Fast", "cost": 4.95,
        "uncached": 6_730_000, "cached": 16_000_000, "out": 287_600},
    ("baseten_sept", "deepseek-ai/DeepSeek-V4.1-Flash-Fast"): {
        "label": "Baseten Sept-Fast", "cost": 3.48,
        "uncached": 4_562_000, "cached": 12_236_000, "out": 236_700},
    ("coral", "deepseek-v4.1-flash-fast"): {
        "label": "Coral", "cost": 1.25,
        # dashboard gives total input + cached; uncached = input - cached
        "input": 24_300_000, "cached": 23_500_000, "uncached": 24_300_000 - 23_500_000,
        "out": 370_500},
}

PROBE_IDS = {"preflight", "ppv-coral", "ppv-baseten", "affprobe", "v2smoke",
             "ctl", "hdrcap", "diag-reason", "diag-longgen", "cache_investigation",
             "baseten_cache_deepdive"}


def _load(path):
    if not os.path.exists(path):
        return
    for ln in open(path):
        ln = ln.strip()
        if not ln:
            continue
        try:
            yield json.loads(ln)
        except Exception:
            continue  # tolerate a partially-written trailing line


def main_window():
    runs = list(_load(RUNS_JSONL))
    first = min(r["wall_start"] for r in runs)
    # per-run windows for the canonical (windowed) main calls
    win = {r["run_id"]: (r["wall_start"], r["wall_end"]) for r in runs}
    return first, win


def classify(c, main_first, win):
    """Bucket a deduped proxy call. cacheexp-* (this caching work) is dropped."""
    rid = c.get("run_id", "")
    if rid.startswith("cacheexp"):
        return None  # this caching work — excluded (it is AFTER, not before)
    t = c.get("wall_request_start") or ""
    if not RUN_RE.match(rid):
        # non-run id: preflight / probes / smoke
        if rid in ("preflight",):
            return "smoke_preflight"
        return "probes"
    # run##_ call
    ws, we = win.get(rid, (None, None))
    if ws and we and ws <= t <= we:
        return "main"
    if t < main_first:
        return "pilot_premain"   # pilot + earlier/aborted attempts before main
    return "stale"               # run-id'd but outside any main window (stale bursts)


def dedup_and_bucket():
    main_first, win = main_window()
    seen = set()
    buckets = defaultdict(lambda: defaultdict(lambda: {
        "calls": 0, "uncached": 0, "cached": 0, "out": 0, "cost": 0.0}))
    for path in CALL_FILES:
        for c in _load(path):
            key = (c.get("provider"), c.get("wall_request_start"),
                   c.get("run_id"), c.get("round"), c.get("call_index"))
            if key in seen:
                continue
            seen.add(key)
            bucket = classify(c, main_first, win)
            if bucket is None:
                continue
            pm = (c.get("provider"), c.get("model"))
            b = buckets[pm][bucket]
            b["calls"] += 1
            b["uncached"] += c.get("uncached_tokens") or 0
            b["cached"] += c.get("cached_tokens") or 0
            b["out"] += c.get("output_tokens") or 0
            b["cost"] += c.get("cost_usd_public") or 0.0
    return buckets


def replay_totals():
    """Replays bypassed the proxy; sum them from trace_replay.jsonl per arm."""
    out = defaultdict(lambda: {"calls": 0, "uncached": 0, "cached": 0, "out": 0,
                               "cost": 0.0, "sessions": 0})
    for r in _load(REPLAY_JSONL):
        pm = (r.get("arm"), r.get("model"))
        o = out[pm]
        o["sessions"] += 1
        o["calls"] += r.get("n_ok") or 0
        o["uncached"] += r.get("uncached_tokens") or 0
        o["cached"] += r.get("cached_tokens") or 0
        o["out"] += r.get("output_tokens") or 0
        o["cost"] += r.get("cost_usd_public") or 0.0
    return out


def _m(n):
    return f"{n/1e6:.3f}M"


def main():
    buckets = dedup_and_bucket()
    replay = replay_totals()

    order = ["pilot_premain", "main", "probes", "smoke_preflight", "stale"]
    label = {"pilot_premain": "pilot + earlier/aborted (pre-main)", "main": "main",
             "probes": "probes", "smoke_preflight": "smoke/preflight",
             "stale": "stale (run-id'd, outside window)"}

    L = ["# BILLING_RECONCILIATION (Layer C — NO API calls)\n"]
    L.append("> Pure offline reconciliation. Sums **proxy-recorded** cost + tokens "
             "per provider/model from `results/raw/calls.jsonl` and its archived "
             "snapshots, for runs **before** this caching work (the `cacheexp-*` "
             "Layer-B calls are excluded). Calls are **deduplicated** across "
             "snapshots by (provider, timestamp, run_id, round, call_index). "
             "**Replays bypassed the proxy**, so they are summed separately from "
             f"`trace_replay.jsonl`. Public prices as of {price_date()}.\n")

    for pm, dash in DASH.items():
        prov, model = pm
        L.append(f"\n## {dash['label']}  (`{prov}` / `{model}`)\n")
        L.append("\n| bucket | calls | uncached | cached | output | cost (public) |\n")
        L.append("|---|---|---|---|---|---|\n")
        tot = {"calls": 0, "uncached": 0, "cached": 0, "out": 0, "cost": 0.0}
        for b in order:
            d = buckets.get(pm, {}).get(b)
            if not d or d["calls"] == 0:
                continue
            L.append(f"| {label[b]} | {d['calls']} | {_m(d['uncached'])} | "
                     f"{_m(d['cached'])} | {d['out']/1e3:.1f}K | ${d['cost']:.4f} |\n")
            for k in tot:
                tot[k] += d[k]
        # replay row (from trace_replay.jsonl)
        rp = replay.get(pm)
        if rp and rp["calls"]:
            L.append(f"| replay (direct, not via proxy) | {rp['calls']} "
                     f"({rp['sessions']} sess) | {_m(rp['uncached'])} | "
                     f"{_m(rp['cached'])} | {rp['out']/1e3:.1f}K | ${rp['cost']:.4f} |\n")
            for k in tot:
                tot[k] += rp[k]
        L.append(f"| **our recorded total** | **{tot['calls']}** | "
                 f"**{_m(tot['uncached'])}** | **{_m(tot['cached'])}** | "
                 f"**{tot['out']/1e3:.1f}K** | **${tot['cost']:.4f}** |\n")
        # dashboard + remainder
        d_unc = dash.get("uncached", 0)
        d_cac = dash.get("cached", 0)
        d_out = dash.get("out", 0)
        d_cost = dash.get("cost", 0)
        L.append(f"| **provider dashboard** | — | **{_m(d_unc)}** | **{_m(d_cac)}** | "
                 f"**{d_out/1e3:.1f}K** | **${d_cost:.2f}** |\n")
        rem_unc = d_unc - tot["uncached"]
        rem_cac = d_cac - tot["cached"]
        rem_out = d_out - tot["out"]
        rem_cost = d_cost - tot["cost"]
        L.append(f"| **unexplained remainder** | — | {_m(rem_unc)} | {_m(rem_cac)} | "
                 f"{rem_out/1e3:.1f}K | ${rem_cost:.2f} |\n")
        pct = (tot["cost"] / d_cost * 100) if d_cost else 0
        L.append(f"\n_Our recorded spend is **{pct:.0f}%** of the dashboard "
                 f"${d_cost:.2f}; remainder **${rem_cost:.2f}**._\n")

    L.append("\n## Where the remainder comes from (attribution)\n")
    L.append(
        "The dashboard bills **every** call the account ever made; our proxy "
        "records only calls routed through it. Known un-recorded (or "
        "differently-counted) sources of the remainder, in rough order:\n\n"
        "1. **Direct probes that bypassed the proxy.** Several investigation "
        "scripts called the providers directly (the Baseten cache deep-dive, "
        "affinity/multi-slug probes, billing checks) and the trace-replay (folded "
        "in above from `trace_replay.jsonl`). The non-replay direct probes are "
        "**not** in `calls.jsonl` at all.\n"
        "2. **Prior use of the same account.** The Coral account was also used by "
        "the earlier `coral-probe` project; those tokens are on the Coral "
        "dashboard but were never seen by this harness — the single largest "
        "driver of Coral's remainder.\n"
        "3. **Dashboard token semantics.** Providers may count cache-**write** "
        "tokens and failed/aborted requests toward dashboard totals that our "
        "per-call `usage` parsing attributes differently (we bill cached reads at "
        "the discounted rate; the dashboard may show them under a different "
        "column).\n"
        "4. **Aggregation timing.** Dashboard subtotals settle with lag (observed "
        "in the main experiment's billing check), so a snapshot can lead or trail "
        "our recorded total.\n")
    L.append("\n_No fabricated numbers: every 'our recorded' value is summed from "
             "recorded call rows; the remainder is reported as-is, not forced to "
             "zero._\n")

    with open(OUT_MD, "w") as f:
        f.write("".join(L))
    print("".join(L))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
