"""LAYER A — offline cache analysis of ALREADY-RECORDED data + a Coral-only
network baseline.

HARD RULE: the ONLY new API calls this module makes are Coral `/v1/models`
pings (step 2). ZERO Baseten calls. Everything else is offline analysis of
results/raw/calls.jsonl (main experiment, windowed exactly like analyze.py) and
results/raw/trace_replay.jsonl.

Outputs:
  results/CACHE_ANALYSIS.md
  results/cache_charts/ttft_vs_uncached.png
  results/cache_charts/cache_hit_vs_index_context.png
  results/raw/cache_analysis.json   (gitignored raw dump)

Run:  python -m harness.cache_analysis
"""

from __future__ import annotations

import json
import os
import statistics
import time

import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from harness.common import RAW_DIR, RESULTS_DIR
from proxy.config import load_dotenv, load_providers, price_date

load_dotenv()  # Coral key read ONLY from acceptance-bench/.env

CALLS_PATH = os.path.join(RAW_DIR, "calls.jsonl")
RUNS_JSONL = os.path.join(RAW_DIR, "runs.jsonl")
REPLAY_PATH = os.path.join(RAW_DIR, "trace_replay.jsonl")
CHARTS_DIR = os.path.join(RESULTS_DIR, "cache_charts")  # committable (not gitignored)
OUT_MD = os.path.join(RESULTS_DIR, "CACHE_ANALYSIS.md")
OUT_JSON = os.path.join(RAW_DIR, "cache_analysis.json")

ARMS = ["coral", "baseten", "baseten_sept"]
ARM_LABEL = {
    "coral": "Coral (deepseek-v4.1-flash-fast)",
    "baseten": "Baseten 1017-Fast",
    "baseten_sept": "Baseten Sept-Fast",
}
COLOR = {"coral": "#3b7dd8", "baseten": "#d8923b", "baseten_sept": "#5bbf5b"}


# --------------------------------------------------------------------------- io
def _load_jsonl(path):
    out = []
    if not os.path.exists(path):
        return out
    for ln in open(path):
        ln = ln.strip()
        if ln:
            out.append(json.loads(ln))
    return out


def load_main_calls():
    """Main-experiment chat calls, windowed to each run's [wall_start, wall_end]
    exactly like analyze.py (guards against stale calls reusing a run_id)."""
    runs = _load_jsonl(RUNS_JSONL)
    win = {r["run_id"]: (r.get("wall_start"), r.get("wall_end"), r.get("provider"))
           for r in runs}
    out = []
    for c in _load_jsonl(CALLS_PATH):
        rid = c.get("run_id")
        if rid not in win:
            continue
        ws, we, prov = win[rid]
        t = c.get("wall_request_start")
        if not (ws and we and t and ws <= t <= we):
            continue
        if not (c.get("path") or "").endswith("chat/completions"):
            continue
        if c.get("http_status") != 200:
            continue
        c["_provider"] = prov or c.get("provider")
        out.append(c)
    return out


# ------------------------------------------------------------------ regression
def _ols(X, y):
    """Ordinary least squares via lstsq. X already has an intercept column."""
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    return beta


def _boot_ci(X, y, n_boot=5000, seed=7):
    """Bootstrap the OLS coefficients by resampling rows (calls)."""
    rng = np.random.default_rng(seed)
    n = len(y)
    coefs = []
    for _ in range(n_boot):
        idx = rng.integers(0, n, n)
        try:
            coefs.append(_ols(X[idx], y[idx]))
        except np.linalg.LinAlgError:
            continue
    coefs = np.array(coefs)
    lo = np.percentile(coefs, 2.5, axis=0)
    hi = np.percentile(coefs, 97.5, axis=0)
    return lo, hi


def regress_provider(calls, ttft_field="ttft_s"):
    """Per provider: TTFT(ms) regressed on uncached_tokens and cached_tokens,
    separately and jointly. Slopes reported per 1k tokens, with intercept and
    bootstrap 95% CIs. Returns a dict of fits."""
    y = np.array([c[ttft_field] * 1000.0 for c in calls], dtype=float)  # ms
    unc = np.array([c.get("uncached_tokens") or 0 for c in calls], dtype=float) / 1000.0
    cac = np.array([c.get("cached_tokens") or 0 for c in calls], dtype=float) / 1000.0
    n = len(y)
    ones = np.ones(n)

    fits = {"n": n}

    # uncached-only
    X = np.column_stack([ones, unc])
    b = _ols(X, y); lo, hi = _boot_ci(X, y)
    fits["uncached_only"] = {
        "intercept_ms": b[0], "intercept_ci": [lo[0], hi[0]],
        "slope_ms_per_1k_uncached": b[1], "slope_ci": [lo[1], hi[1]],
    }
    # cached-only
    X = np.column_stack([ones, cac])
    b = _ols(X, y); lo, hi = _boot_ci(X, y)
    fits["cached_only"] = {
        "intercept_ms": b[0], "intercept_ci": [lo[0], hi[0]],
        "slope_ms_per_1k_cached": b[1], "slope_ci": [lo[1], hi[1]],
    }
    # joint
    X = np.column_stack([ones, unc, cac])
    b = _ols(X, y); lo, hi = _boot_ci(X, y)
    fits["joint"] = {
        "intercept_ms": b[0], "intercept_ci": [lo[0], hi[0]],
        "slope_ms_per_1k_uncached": b[1], "unc_ci": [lo[1], hi[1]],
        "slope_ms_per_1k_cached": b[2], "cac_ci": [lo[2], hi[2]],
    }
    fits["ttft_p50_ms"] = float(np.percentile(y, 50))
    fits["ttft_p95_ms"] = float(np.percentile(y, 95))
    return fits


# ---------------------------------------------------- Coral-only network ping
def coral_network_baseline(n=20):
    """CORAL ONLY: time GET /v1/models n times; return median RTT (s).

    This is the only place Layer A touches the network, and it touches Coral
    ONLY. Baseten's network baseline is UNMEASURED by choice (hard rule)."""
    import httpx

    providers = load_providers()
    coral = providers["coral"]
    key = coral.api_key()
    if not key:
        return {"error": "no CORALBRICKS_API_KEY in .env", "rtts_s": []}
    url = f"{coral.origin}/v1/models"
    headers = {"Authorization": f"Bearer {key}"}
    rtts = []
    statuses = []
    with httpx.Client(timeout=30.0) as client:
        # one warmup (TLS/connection) that we do NOT time
        try:
            client.get(url, headers=headers)
        except Exception:
            pass
        for _ in range(n):
            t0 = time.perf_counter()
            try:
                r = client.get(url, headers=headers)
                dt = time.perf_counter() - t0
                statuses.append(r.status_code)
                if r.status_code == 200:
                    rtts.append(dt)
            except Exception as e:
                statuses.append(f"err:{type(e).__name__}")
            time.sleep(0.2)
    return {
        "n_requested": n,
        "n_ok": len(rtts),
        "statuses": statuses,
        "rtts_s": rtts,
        "median_rtt_s": statistics.median(rtts) if rtts else None,
        "p05_rtt_s": float(np.percentile(rtts, 5)) if rtts else None,
        "p95_rtt_s": float(np.percentile(rtts, 95)) if rtts else None,
    }


# ------------------------------------------------- cache hit rate vs index/ctx
def _run_sequence(calls):
    """Assign each call its 0-based position within its own run (ordered by
    wall_request_start), so 'call index' means position in the session."""
    by_run = {}
    for c in calls:
        by_run.setdefault(c["run_id"], []).append(c)
    for rid, cs in by_run.items():
        cs.sort(key=lambda c: c.get("wall_request_start") or "")
        for i, c in enumerate(cs):
            c["_seq"] = i


def hit_rate_curves(calls):
    """Per provider: cache hit rate vs call index (session position) and vs
    context size (prompt tokens), from recorded data."""
    _run_sequence(calls)
    out = {}
    for arm in ARMS:
        cs = [c for c in calls if c["_provider"] == arm]
        if not cs:
            continue
        # vs call index
        by_idx = {}
        for c in cs:
            inp = (c.get("uncached_tokens") or 0) + (c.get("cached_tokens") or 0)
            if inp <= 0:
                continue
            hr = (c.get("cached_tokens") or 0) / inp
            by_idx.setdefault(c["_seq"], []).append(hr)
        idx_curve = {i: statistics.mean(v) for i, v in sorted(by_idx.items())}
        # vs context size (binned)
        bins = [(0, 2_000), (2_000, 8_000), (8_000, 20_000), (20_000, 40_000),
                (40_000, 80_000), (80_000, 10**9)]
        ctx_curve = {}
        for lo, hi in bins:
            hrs = []
            for c in cs:
                inp = (c.get("uncached_tokens") or 0) + (c.get("cached_tokens") or 0)
                if lo <= inp < hi and inp > 0:
                    hrs.append((c.get("cached_tokens") or 0) / inp)
            if hrs:
                label = f"{lo//1000}-{hi//1000}k" if hi < 10**9 else f"{lo//1000}k+"
                ctx_curve[label] = {"mean_hit": statistics.mean(hrs), "n": len(hrs)}
        out[arm] = {"idx_curve": idx_curve, "ctx_curve": ctx_curve, "n": len(cs)}
    return out


# ----------------------------------------------------------------- miss anatomy
def miss_anatomy(calls, miss_threshold=0.9, near_total=0.1):
    """Per provider, classify calls that cached < miss_threshold of prompt:
      - near-total miss (cached/prompt < near_total): likely cold-replica ROUTING
      - partial tail miss (near_total <= ratio < miss_threshold): likely EVICTION
    Only calls with a non-trivial prompt (>256 tokens, i.e. a cacheable prefix)."""
    out = {}
    for arm in ARMS:
        cs = [c for c in calls if c["_provider"] == arm]
        total = 0
        near = 0
        partial = 0
        near_ratios = []
        partial_ratios = []
        for c in cs:
            inp = (c.get("uncached_tokens") or 0) + (c.get("cached_tokens") or 0)
            if inp <= 256:  # too small to carry a cacheable prefix
                continue
            total += 1
            ratio = (c.get("cached_tokens") or 0) / inp
            if ratio >= miss_threshold:
                continue  # a hit, not a miss
            if ratio < near_total:
                near += 1
                near_ratios.append(ratio)
            else:
                partial += 1
                partial_ratios.append(ratio)
        out[arm] = {
            "n_cacheable_calls": total,
            "n_miss": near + partial,
            "near_total_miss": near,
            "partial_tail_miss": partial,
            "near_total_mean_ratio": statistics.mean(near_ratios) if near_ratios else None,
            "partial_mean_ratio": statistics.mean(partial_ratios) if partial_ratios else None,
            "miss_rate": (near + partial) / total if total else None,
        }
    return out


# ----------------------------------------------------------------------- charts
def chart_ttft_vs_uncached(calls, path):
    os.makedirs(CHARTS_DIR, exist_ok=True)
    fig, ax = plt.subplots(figsize=(7, 4.5))
    calls = [c for c in calls if c.get("ttft_s") is not None]
    for arm in ARMS:
        cs = [c for c in calls if c["_provider"] == arm]
        if not cs:
            continue
        x = np.array([(c.get("uncached_tokens") or 0) / 1000.0 for c in cs])
        y = np.array([c["ttft_s"] * 1000.0 for c in cs])
        ax.scatter(x, y, s=8, alpha=0.25, color=COLOR[arm], label=None)
        # fit line
        if len(x) > 2:
            b = _ols(np.column_stack([np.ones(len(x)), x]), y)
            xs = np.linspace(x.min(), x.max(), 50)
            ax.plot(xs, b[0] + b[1] * xs, color=COLOR[arm], lw=2,
                    label=f"{ARM_LABEL[arm]}: {b[1]:.1f} ms/1k")
    ax.set_xlabel("uncached prompt tokens (thousands)")
    ax.set_ylabel("TTFT (ms)")
    ax.set_title("TTFT vs uncached tokens per provider (recorded main-run calls)")
    ax.set_ylim(0, np.percentile([c["ttft_s"] * 1000 for c in calls], 98))
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def chart_hit_curves(curves, path):
    os.makedirs(CHARTS_DIR, exist_ok=True)
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.5))
    for arm, d in curves.items():
        ic = d["idx_curve"]
        if ic:
            xs = sorted(ic)
            ax1.plot(xs, [ic[i] * 100 for i in xs], color=COLOR[arm],
                     label=ARM_LABEL[arm], lw=1.5)
    ax1.set_xlabel("call index within run (session position)")
    ax1.set_ylabel("cache hit rate (%)")
    ax1.set_title("Hit rate vs call index")
    ax1.set_ylim(0, 100)
    ax1.legend(fontsize=8)

    bin_order = ["0-2k", "2-8k", "8-20k", "20-40k", "40-80k", "80k+"]
    width = 0.26
    for j, arm in enumerate([a for a in ARMS if a in curves]):
        cc = curves[arm]["ctx_curve"]
        xs = [i for i, b in enumerate(bin_order) if b in cc]
        ys = [cc[bin_order[i]]["mean_hit"] * 100 for i in xs]
        ax2.bar([x + j * width for x in xs], ys, width,
                color=COLOR[arm], label=ARM_LABEL[arm])
    ax2.set_xticks([i + width for i in range(len(bin_order))])
    ax2.set_xticklabels(bin_order, fontsize=8)
    ax2.set_xlabel("context size (prompt tokens)")
    ax2.set_ylabel("cache hit rate (%)")
    ax2.set_title("Hit rate vs context size")
    ax2.set_ylim(0, 100)
    ax2.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


# ------------------------------------------------------------------------- main
def _fmt_ci(lo, hi, nd=1):
    return f"[{lo:.{nd}f}, {hi:.{nd}f}]"


def main() -> int:
    calls = load_main_calls()
    if not calls:
        print("No windowed main-run calls found.")
        return 1
    print(f"Loaded {len(calls)} windowed main-run chat calls.")

    # 1. regressions
    regs = {}
    excluded = {}
    for arm in ARMS:
        all_cs = [c for c in calls if c["_provider"] == arm]
        cs = [c for c in all_cs if c.get("ttft_s") is not None]
        excluded[arm] = {"n_200": len(all_cs), "n_timed": len(cs),
                         "n_untimed": len(all_cs) - len(cs)}
        if cs:
            regs[arm] = regress_provider(cs)

    # 2. Coral-only network baseline
    print("Coral-only network baseline (20x GET /v1/models)...")
    net = coral_network_baseline(20)
    coral_ttft_p50_ms = regs.get("coral", {}).get("ttft_p50_ms")
    net_adj = None
    if net.get("median_rtt_s") is not None and coral_ttft_p50_ms is not None:
        net_adj = coral_ttft_p50_ms - net["median_rtt_s"] * 1000.0

    # 3. hit-rate curves
    curves = hit_rate_curves(calls)

    # 4. miss anatomy
    miss = miss_anatomy(calls)

    # charts
    chart_ttft_vs_uncached(calls, os.path.join(CHARTS_DIR, "ttft_vs_uncached.png"))
    chart_hit_curves(curves, os.path.join(CHARTS_DIR, "cache_hit_vs_index_context.png"))

    dump = {"regressions": regs, "network_coral": net, "net_adjusted_coral_ttft_ms": net_adj,
            "hit_curves": curves, "miss_anatomy": miss, "n_calls": len(calls)}
    json.dump(dump, open(OUT_JSON, "w"), indent=2, default=str)

    dump["timed_exclusions"] = excluded
    json.dump(dump, open(OUT_JSON, "w"), indent=2, default=str)
    write_markdown(regs, net, net_adj, coral_ttft_p50_ms, curves, miss, len(calls), excluded)
    print(f"Wrote {OUT_MD}")
    return 0


def write_markdown(regs, net, net_adj, coral_ttft_p50_ms, curves, miss, n_calls, excluded):
    L = []
    L.append("# CACHE_ANALYSIS (Layer A — offline analysis of recorded data)\n")
    L.append("> **Scope:** bounded to effects on the acceptance loop. Every number "
             "here is computed offline from already-recorded data in "
             "`results/raw/calls.jsonl` (the pre-registered MAIN EXPERIMENT, "
             "windowed to each run's `[wall_start, wall_end]` exactly like "
             "`analyze.py`), EXCEPT the Coral network baseline in §2, which is the "
             "only new network activity and is **CoralBricks-only by choice** "
             "(hard rule: zero Baseten calls).\n")
    L.append(f"_Prices as of {price_date()}. Calls analyzed: {n_calls} (200-only, "
             "chat/completions, windowed)._\n")
    L.append("\n**Note on replay calls:** `trace_replay.jsonl` persists only "
             "per-session aggregates (the proxy's full per-call rows are not "
             "written for replays, which bypass the proxy), so the per-call TTFT "
             "regression below uses the proxy-recorded main-run calls. Replay "
             "session aggregates are used for cross-checking in the final report.\n")

    # §1 regressions
    L.append("\n## 1. TTFT vs tokens — per-call regressions per provider\n")
    L.append("TTFT here is `ttft_s` (request start → first streamed token = network "
             "RTT + queue + prefill). Slopes are **ms per 1,000 tokens**; intercept "
             "is ms; 95% CIs are bootstrap (5,000 resamples of calls).\n")
    L.append("\n| provider | n | model | slope ms/1k **uncached** (CI) | slope ms/1k "
             "**cached** (CI) | intercept ms (CI) |\n")
    L.append("|---|---|---|---|---|---|\n")
    for arm in ARMS:
        if arm not in regs:
            continue
        r = regs[arm]
        j = r["joint"]
        L.append(
            f"| {ARM_LABEL[arm]} | {r['n']} | joint | "
            f"{j['slope_ms_per_1k_uncached']:.1f} {_fmt_ci(*j['unc_ci'])} | "
            f"{j['slope_ms_per_1k_cached']:.2f} {_fmt_ci(*j['cac_ci'], nd=2)} | "
            f"{j['intercept_ms']:.0f} {_fmt_ci(*j['intercept_ci'], nd=0)} |\n")
    L.append("\n**Separate univariate fits** (each token type alone):\n\n")
    L.append("| provider | uncached-only slope (CI) | cached-only slope (CI) | "
             "TTFT p50 / p95 (ms) |\n|---|---|---|---|\n")
    for arm in ARMS:
        if arm not in regs:
            continue
        r = regs[arm]
        uo = r["uncached_only"]; co = r["cached_only"]
        L.append(
            f"| {ARM_LABEL[arm]} | {uo['slope_ms_per_1k_uncached']:.1f} "
            f"{_fmt_ci(*uo['slope_ci'])} | {co['slope_ms_per_1k_cached']:.2f} "
            f"{_fmt_ci(*co['slope_ci'], nd=2)} | "
            f"{r['ttft_p50_ms']:.0f} / {r['ttft_p95_ms']:.0f} |\n")
    L.append("\n**Reading it:** the uncached slope is the *miss penalty* — extra "
             "TTFT per 1k freshly-prefilled tokens. A cached slope near zero (and "
             "much smaller than the uncached slope) means cache hits are nearly free "
             "in latency, so caching helps the loop, not just the bill.\n")
    # selection caveat: tool-call-only completions are not timed by the proxy
    cor = excluded.get("coral", {})
    L.append(
        f"\n**Selection caveat (recorded-data artifact, not a new measurement):** "
        f"the proxy timestamps the first *content*/*reasoning* delta only, so "
        f"completions that stream **tool-call deltas only** (output tokens but no "
        f"content/reasoning) have no `ttft_s` and are excluded from these fits. This "
        f"hits Coral specifically: {cor.get('n_untimed', 0)} of {cor.get('n_200', 0)} "
        f"Coral 200-calls are tool-call-only (Baseten: "
        f"{excluded.get('baseten', {}).get('n_untimed', 0)} / "
        f"{excluded.get('baseten', {}).get('n_200', 0)}; Sept: "
        f"{excluded.get('baseten_sept', {}).get('n_untimed', 0)} / "
        f"{excluded.get('baseten_sept', {}).get('n_200', 0)}). So Coral's TTFT fit is "
        f"over its content-bearing calls; its tool-call turns (which still prefill "
        f"the prompt) are not represented here. §3/§4 below use ALL 200-calls.\n")
    L.append("\n![TTFT vs uncached tokens](cache_charts/ttft_vs_uncached.png)\n")

    # §2 network baseline
    L.append("\n## 2. Network baseline — CORAL ONLY\n")
    if net.get("median_rtt_s") is not None:
        L.append(f"20× `GET /v1/models` to CoralBricks (1 untimed warmup first): "
                 f"**{net['n_ok']}/{net['n_requested']}** OK. "
                 f"Median RTT **{net['median_rtt_s']*1000:.0f} ms** "
                 f"(p05 {net['p05_rtt_s']*1000:.0f} / p95 {net['p95_rtt_s']*1000:.0f} ms).\n")
        if net_adj is not None:
            L.append(f"\nCoral recorded TTFT p50 = **{coral_ttft_p50_ms:.0f} ms**; "
                     f"network-adjusted (TTFT p50 − median RTT) = "
                     f"**{net_adj:.0f} ms** of server-side queue+prefill.\n")
    else:
        L.append(f"Coral `/v1/models` baseline unavailable: {net.get('error') or net.get('statuses')}\n")
    L.append("\n**Baseten network-adjusted comparison: UNMEASURED (by choice).** "
             "The hard rule forbids any new Baseten call, including `/v1/models`, so "
             "we deliberately do not measure Baseten's RTT here. Baseten's recorded "
             "TTFT (from the main experiment) is NOT network-adjusted.\n")

    # §3 hit curves
    L.append("\n## 3. Cache hit rate vs call index and vs context size\n")
    L.append("![Cache hit rate vs index and context](cache_charts/cache_hit_vs_index_context.png)\n")
    L.append("\n| provider | hit@index0 | hit@index5 | hit@index15 | by-context (small→large) |\n")
    L.append("|---|---|---|---|---|\n")
    for arm in ARMS:
        if arm not in curves:
            continue
        ic = curves[arm]["idx_curve"]
        def at(i):
            return f"{ic[i]*100:.0f}%" if i in ic else "—"
        cc = curves[arm]["ctx_curve"]
        ctx_str = ", ".join(f"{k}:{v['mean_hit']*100:.0f}%" for k, v in cc.items())
        L.append(f"| {ARM_LABEL[arm]} | {at(0)} | {at(5)} | {at(15)} | {ctx_str} |\n")
    L.append("\nIndex 0 is the first call of a run (cold); hit rate climbs as the "
             "session prefix warms. Context-size bins show whether large prompts "
             "hit the cache as well as small ones.\n")

    # §4 miss anatomy
    L.append("\n## 4. Miss anatomy per provider\n")
    L.append("Among calls carrying a cacheable prefix (>256 prompt tokens), a *miss* "
             "is cached < 90% of the prompt. **Near-total miss** (cached < 10%) ≈ a "
             "cold replica / routing miss; **partial tail miss** (10–90% cached) ≈ "
             "eviction of the growing tail while the prefix stays warm.\n")
    L.append("\n| provider | cacheable calls | misses | near-total (routing) | "
             "partial-tail (eviction) | miss rate |\n|---|---|---|---|---|---|\n")
    for arm in ARMS:
        if arm not in miss:
            continue
        m = miss[arm]
        mr = f"{m['miss_rate']*100:.1f}%" if m["miss_rate"] is not None else "—"
        L.append(f"| {ARM_LABEL[arm]} | {m['n_cacheable_calls']} | {m['n_miss']} | "
                 f"{m['near_total_miss']} | {m['partial_tail_miss']} | {mr} |\n")
    L.append("\n**Reading it:** mostly near-total misses point to replica routing "
             "(a request landing on a cold replica); mostly partial-tail misses "
             "point to the cache holding the prefix but evicting the newest tokens. "
             "These imply different mitigations (affinity/routing vs prefix size).\n")

    with open(OUT_MD, "w") as f:
        f.write("".join(L))


if __name__ == "__main__":
    raise SystemExit(main())
