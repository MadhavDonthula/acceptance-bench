"""Build results/SUMMARY.md purely from recorded raw files. No fabricated numbers.

Reads results/raw/{calls,runs,rounds}.jsonl + pilot_plan.json + proxy_overhead.json
and results/preflight/report.json. Every metric traces to recorded data; anything
unmeasured is stated as such.
"""

from __future__ import annotations

import json
import os
import statistics as stats

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = os.path.abspath(os.path.dirname(__file__))
RAW = os.path.join(ROOT, "results", "raw")
PREFLIGHT = os.path.join(ROOT, "results", "preflight")
CHARTS = os.path.join(ROOT, "results", "charts")
SUMMARY = os.path.join(ROOT, "results", "SUMMARY.md")


def _load_jsonl(name):
    path = os.path.join(RAW, name)
    if not os.path.exists(path):
        return []
    out = []
    for ln in open(path):
        try:
            out.append(json.loads(ln))
        except Exception:
            pass
    return out


def _load_json(path):
    if not os.path.exists(path):
        return None
    try:
        return json.load(open(path))
    except Exception:
        return None


def _union_len(intervals):
    """Total length covered by a set of [start,end] intervals (one clock)."""
    iv = sorted((a, b) for a, b in intervals if a is not None and b is not None and b >= a)
    if not iv:
        return 0.0
    total = 0.0
    cur_s, cur_e = iv[0]
    for s, e in iv[1:]:
        if s > cur_e:
            total += cur_e - cur_s
            cur_s, cur_e = s, e
        else:
            cur_e = max(cur_e, e)
    total += cur_e - cur_s
    return total


def _pct(xs, p):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    k = (len(xs) - 1) * (p / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def _fmt(x, nd=2):
    return "—" if x is None else f"{x:.{nd}f}"


def build_run_metrics(runs, rounds, calls):
    by_run = {}
    for r in runs:
        rid = r["run_id"]
        rc = [c for c in calls if c.get("run_id") == rid]
        rr = [x for x in rounds if x.get("run_id") == rid]

        model_time = _union_len([
            (c.get("mono_request_start"), c.get("mono_request_end")) for c in rc
        ])
        agent_time = sum(float(x.get("agent_duration_s") or 0) for x in rr)
        judge_time = sum(float(x.get("judge_runtime_s") or 0) for x in rr)
        wall = float(r.get("wall_duration_s") or 0)
        non_model_agent = max(agent_time - model_time, 0.0)
        other = max(wall - agent_time - judge_time, 0.0)

        t = r.get("totals", {})
        cached = t.get("cached_tokens", 0)
        uncached = t.get("uncached_tokens", 0)
        inp = cached + uncached
        by_run[rid] = {
            "run": r, "provider": r["provider"], "outcome": r["outcome"],
            "rounds": r.get("rounds"), "wall": wall,
            "model_time": model_time, "non_model_agent": non_model_agent,
            "judge_time": judge_time, "other": other,
            "f_model_wall": (model_time / wall) if wall else None,
            "calls": t.get("calls", 0),
            "uncached": uncached, "cached": cached, "output": t.get("output_tokens", 0),
            "cache_hit_rate": (cached / inp) if inp else None,
            "cost": t.get("cost_usd_public", 0.0),
            "n_429": t.get("n_429", 0), "n_529": t.get("n_529", 0), "n_5xx": t.get("n_5xx", 0),
        }
    return by_run


def per_call_stats(calls, provider):
    rc = [c for c in calls if c.get("provider") == provider and c.get("http_status") == 200]
    ttfts = [c.get("ttft_s") for c in rc if c.get("ttft_s")]
    decode = []
    for c in rc:
        d = c.get("decode_s")
        o = c.get("output_tokens") or 0
        if d and d > 0 and o > 0:
            decode.append(o / d)
    # Baseten speculative-decoding acceptance
    acc = rej = 0
    for c in rc:
        pt = c.get("prediction_tokens") or {}
        for k, v in pt.items():
            if "accept" in k.lower():
                acc += v
            elif "reject" in k.lower():
                rej += v
    spec = (acc / (acc + rej)) if (acc + rej) else None
    return {
        "ttft_p50": _pct(ttfts, 50), "ttft_p95": _pct(ttfts, 95),
        "decode_p50": _pct(decode, 50), "n_calls": len(rc),
        "spec_accept": spec,
    }


def provider_summary(by_run, calls, provider):
    runs = [m for m in by_run.values() if m["provider"] == provider]
    n = len(runs)
    accepted = [m for m in runs if m["outcome"] == "accepted"]
    walls = [m["wall"] for m in runs]
    total_wall = sum(walls)
    total_cost = sum(m["cost"] for m in runs)
    cached = sum(m["cached"] for m in runs)
    inp = cached + sum(m["uncached"] for m in runs)
    pcs = per_call_stats(calls, provider)
    return {
        "n": n, "accepted": len(accepted),
        "wall_median": _pct(walls, 50) if walls else None,
        "wall_min": min(walls) if walls else None, "wall_max": max(walls) if walls else None,
        "time_per_accepted": (total_wall / len(accepted)) if accepted else None,
        "cost_per_accepted": (total_cost / len(accepted)) if accepted else None,
        "rounds_median": _pct([m["rounds"] for m in runs], 50) if runs else None,
        "cache_hit_rate": (cached / inp) if inp else None,
        "total_cost": total_cost, **pcs,
    }


def make_chart(by_run):
    provs = [p for p in ("coral", "baseten") if any(m["provider"] == p for m in by_run.values())]
    if not provs:
        return None
    labels = ["model", "non-model agent", "judge"]
    data = {p: [] for p in provs}
    for p in provs:
        runs = [m for m in by_run.values() if m["provider"] == p]
        data[p] = [
            _pct([m["model_time"] for m in runs], 50) or 0,
            _pct([m["non_model_agent"] for m in runs], 50) or 0,
            _pct([m["judge_time"] for m in runs], 50) or 0,
        ]
    os.makedirs(CHARTS, exist_ok=True)
    fig, ax = plt.subplots(figsize=(6, 4))
    bottoms = [0] * len(provs)
    colors = ["#3b7dd8", "#d8923b", "#5bbf5b"]
    for i, lab in enumerate(labels):
        vals = [data[p][i] for p in provs]
        ax.bar(provs, vals, bottom=bottoms, label=lab, color=colors[i])
        bottoms = [b + v for b, v in zip(bottoms, vals)]
    ax.set_ylabel("seconds (median of runs)")
    ax.set_title("Median wall-time split per provider (PILOT, n≤3)")
    ax.legend()
    path = os.path.join(CHARTS, "time_split.png")
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def amdahl_section(coral, baseten, by_run):
    if not (coral["decode_p50"] and baseten["decode_p50"]):
        return ("Amdahl check: not computable — no successful per-call decode data recorded "
                "on one or both arms.\n")
    s = coral["decode_p50"] / baseten["decode_p50"]  # model-throughput speedup, coral vs baseten
    # f = model share of wall on the BASELINE (baseten) arm
    bt_runs = [m for m in by_run.values() if m["provider"] == "baseten" and m["f_model_wall"]]
    f = _pct([m["f_model_wall"] for m in bt_runs], 50) if bt_runs else None
    if f is None:
        return "Amdahl check: not computable — no model-share (f) recorded on the baseline arm.\n"
    predicted = 1.0 / ((1 - f) + f / s) if s else None
    observed = None
    if coral["wall_median"] and baseten["wall_median"]:
        observed = baseten["wall_median"] / coral["wall_median"]
    return (
        f"- Per-call model-throughput speedup (coral decode p50 / baseten decode p50): **{s:.2f}×**\n"
        f"- Model share of wall on baseline (baseten), f (median): **{f:.2f}**\n"
        f"- Amdahl-predicted end-to-end speedup `1/((1-f)+f/s)`: **{_fmt(predicted)}×**\n"
        f"- Observed end-to-end speedup (baseten median wall / coral median wall): "
        f"**{_fmt(observed)}×**\n\n"
        "  (Interpretation: if observed ≪ predicted, faster decode did not translate into faster "
        "ACCEPTED work because non-model agent + judge time dominate.)\n"
    )


def main():
    runs = _load_jsonl("runs.jsonl")
    rounds = _load_jsonl("rounds.jsonl")
    calls = _load_jsonl("calls.jsonl")
    plan = _load_json(os.path.join(RAW, "pilot_plan.json"))
    overhead = _load_json(os.path.join(RAW, "proxy_overhead.json"))
    preflight = _load_json(os.path.join(PREFLIGHT, "report.json"))

    pilot_calls = [c for c in calls if str(c.get("run_id", "")).startswith("run")]
    by_run = build_run_metrics(runs, rounds, pilot_calls)
    chart = make_chart(by_run)

    L = []
    L.append("# Pilot results — CoralBricks vs Baseten (ACCEPTED-work benchmark)\n")
    L.append("> ## ⚠️ PILOT, n=3 per arm. NO STATISTICAL CLAIMS.\n"
             "> These are real, recorded numbers from a handful of runs. They are **not** powered "
             "to prove a difference between providers. Read them as a smoke test of the method, "
             "not as a verdict.\n")

    if not runs:
        L.append("\n## No pilot data recorded yet\n")
        L.append("`results/raw/runs.jsonl` is empty — the pilot (`make pilot`) has not run, or was "
                 "blocked at preflight. Everything below the method/limitations is therefore "
                 "unmeasured and deliberately left blank rather than fabricated.\n")

    if plan:
        L.append("\n## Run order (recorded)\n")
        L.append(f"- seed: `{plan.get('seed')}`\n- order: `{plan.get('order')}`\n"
                 f"- caps: `{json.dumps(plan.get('caps'))}`\n"
                 f"- identical settings both arms: `{json.dumps(plan.get('settings'))}`\n")

    # Per-run table
    L.append("\n## Per-run table\n")
    if by_run:
        L.append("| run | provider | outcome | rounds | wall s | model s | non-model agent s | "
                 "judge s | f=model/wall | calls | uncached | cached | output | cache hit | "
                 "cost $ | 429 | 529 | 5xx |\n")
        L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|\n")
        for rid in sorted(by_run):
            m = by_run[rid]
            L.append(
                f"| {rid} | {m['provider']} | {m['outcome']} | {m['rounds']} | {_fmt(m['wall'])} | "
                f"{_fmt(m['model_time'])} | {_fmt(m['non_model_agent'])} | {_fmt(m['judge_time'])} | "
                f"{_fmt(m['f_model_wall'])} | {m['calls']} | {m['uncached']} | {m['cached']} | "
                f"{m['output']} | {_fmt((m['cache_hit_rate'] or 0)*100,1)}% | {_fmt(m['cost'],4)} | "
                f"{m['n_429']} | {m['n_529']} | {m['n_5xx']} |\n")
        L.append("\n*model s = union of API call intervals (proxy clock). non-model agent s = agent "
                 "phase time − model s. 'other' overhead (git/copy) = wall − agent − judge.*\n")
    else:
        L.append("_No runs recorded._\n")

    # Per-provider summary
    L.append("\n## Per-provider pilot summary\n")
    if by_run:
        coral = provider_summary(by_run, pilot_calls, "coral")
        baseten = provider_summary(by_run, pilot_calls, "baseten")
        L.append("| metric | coral | baseten |\n|---|---|---|\n")
        def row(label, key, nd=2, pct=False, mul=1):
            cv, bv = coral.get(key), baseten.get(key)
            def f(v):
                if v is None:
                    return "—"
                return f"{v*mul:.{nd}f}{'%' if pct else ''}"
            L.append(f"| {label} | {f(cv)} | {f(bv)} |\n")
        L.append(f"| runs | {coral['n']} | {baseten['n']} |\n")
        L.append(f"| accepted | {coral['accepted']} | {baseten['accepted']} |\n")
        row("wall median (s)", "wall_median")
        L.append(f"| wall range (s) | {_fmt(coral['wall_min'])}–{_fmt(coral['wall_max'])} | "
                 f"{_fmt(baseten['wall_min'])}–{_fmt(baseten['wall_max'])} |\n")
        row("time per ACCEPTED result (s)", "time_per_accepted")
        row("cost per ACCEPTED result ($)", "cost_per_accepted", nd=4)
        row("median rounds", "rounds_median", nd=1)
        row("TTFT p50 (s)", "ttft_p50", nd=3)
        row("TTFT p95 (s)", "ttft_p95", nd=3)
        row("decode tok/s p50", "decode_p50", nd=1)
        row("cache hit rate", "cache_hit_rate", nd=1, pct=True, mul=100)
        row("total cost ($)", "total_cost", nd=4)
        row("speculative acceptance", "spec_accept", nd=3)
        L.append("\n*time/cost per ACCEPTED = (sum of ALL that arm's runs' wall/cost) ÷ "
                 "(# accepted). '—' where nothing was accepted (division undefined — not zero).*\n")

        L.append("\n## Amdahl check\n")
        L.append(amdahl_section(coral, baseten, by_run))
    else:
        L.append("_No runs recorded._\n")

    # Chart
    L.append("\n## Time decomposition chart\n")
    if chart:
        L.append(f"![time split](charts/{os.path.basename(chart)})\n")
    else:
        L.append("_No chart — no run data._\n")

    # Proxy overhead
    L.append("\n## Proxy overhead (measured)\n")
    if overhead:
        L.append(f"- {overhead['n']} identical tiny requests, direct vs through the proxy (offline "
                 f"mock upstream).\n- direct median **{overhead['direct_ms']['median']} ms**, "
                 f"proxy median **{overhead['proxy_ms']['median']} ms** → "
                 f"**{overhead['overhead_ms_median']} ms** median added latency per call.\n")
    else:
        L.append("_Not measured (run `make overhead`)._\n")

    # Preflight cache fields
    L.append("\n## Preflight notes\n")
    if preflight:
        L.append(f"- preflight result: `{preflight.get('result')}`\n")
        cache = preflight.get("cache") or {}
        for prov, d in cache.items():
            if isinstance(d, dict) and "cached_second" in d:
                L.append(f"- {prov}: cached tokens 1st={d.get('cached_first')} "
                         f"2nd={d.get('cached_second')} (cache grew: {d.get('cache_grew')})\n")
    else:
        L.append("_Preflight not run._\n")

    # Limitations
    L.append("\n## Limitations (read before citing anything above)\n")
    bt_model = None
    if runs:
        bt = [r for r in runs if r["provider"] == "baseten"]
        bt_model = bt[0]["model"] if bt else None
    L.append(
        "- **n=3 per arm.** Pilot scale. No confidence intervals, no significance — do not treat any "
        "difference as real.\n"
        f"- **Snapshot match UNRESOLVED.** Coral serves `deepseek-v4.1-flash-fast` (opaque snapshot); "
        f"Baseten was run on `{bt_model or '(see .env BASETEN_MODEL)'}`. We could not confirm the two "
        "are the identical weights snapshot. This is a real confound: a decode-speed or quality gap "
        "could be a snapshot gap, not a serving-stack gap.\n"
        "- **Precision differs / undisclosed.** Coral documents FP4 serving; Baseten's precision is "
        "undisclosed. Token-for-token quality may differ for reasons unrelated to speed.\n"
        "- **One task, one location.** Single task (payments idempotency), single machine/region. "
        "TTFT and rate limits are location-sensitive.\n"
        "- **Public pricing, not account pricing.** Cost uses published list prices for BOTH arms "
        "(see config/providers.toml + date). Coral may grandfather cheaper Fast pricing; that is "
        "deliberately NOT used.\n"
        "- **Proxy overhead** (~see above) is added to every call on both arms equally.\n"
        "- **Identical settings enforced by the proxy** (temp 0 / max_tokens 8192 / reasoning_effort "
        "high) injected identically on both arms; recorded per call in calls.jsonl.\n"
        "- **Cross-process clocks:** model-time union uses the proxy's monotonic clock (consistent "
        "within a run); non-model agent time is a derived difference and inherits that assumption.\n"
    )

    os.makedirs(os.path.dirname(SUMMARY), exist_ok=True)
    with open(SUMMARY, "w") as f:
        f.write("".join(L))
    print(f"Wrote {SUMMARY}")
    if chart:
        print(f"Wrote {chart}")


if __name__ == "__main__":
    main()
