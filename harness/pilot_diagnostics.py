"""Pilot diagnostics (no new pilot runs; one controlled long-generation probe).

1. decode-speed validity: recompute decode tok/s over calls with >=200 VISIBLE
   output tokens, both counting and excluding reasoning; report chunk stats.
2. controlled long-generation probe (same prompt, ~600 output tokens,
   reasoning_effort none, 5 reps/provider) vs the pilot's decode numbers.
3. separate speed from rounds: per-round agent time, per-call model time p50/p95,
   OpenCode startup time per round, per provider.
4. cost decomposition: price each provider's tokens at the other's rates; split
   the cost gap into price vs token/cache behavior.
5. cache misses: classify uncached calls as session-start (first call) vs
   mid-session; report hit rate excluding first calls.

Writes results/preflight/pilot_diagnostics.json.
"""

from __future__ import annotations

import json
import os
import statistics as st
import time
from datetime import datetime

import httpx

from harness.common import PREFLIGHT_DIR, RAW_DIR, load_providers, proxy_base_url
from harness.proxy_manager import ProxyManager
from proxy.config import load_dotenv

load_dotenv()
CALLS = os.path.join(RAW_DIR, "calls.jsonl")


def _pilot_calls():
    rows = [json.loads(l) for l in open(CALLS)]
    return [r for r in rows if str(r.get("run_id", "")).startswith("run") and r.get("http_status") == 200]


def _pct(xs, p):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    k = (len(xs) - 1) * p / 100
    lo = int(k); hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


# ---- step 1 -----------------------------------------------------------------
def step1_decode_validity(calls):
    out = {}
    for prov in ("coral", "baseten"):
        cs = [c for c in calls if c["provider"] == prov]
        # visible output = total completion minus reasoning tokens
        def vis(c):
            return (c.get("output_tokens") or 0) - (c.get("reasoning_tokens") or 0)
        big = [c for c in cs if vis(c) >= 200 and c.get("decode_s")]
        incl = [(c.get("output_tokens") or 0) / c["decode_s"] for c in big]        # counts reasoning
        excl = [vis(c) / c["decode_s"] for c in big]                                # visible only
        # whether pilot recorded reasoning-delta streaming (older calls: field absent)
        has_split = [c for c in cs if "n_reasoning_deltas" in c]
        streamed_reasoning = sum(c.get("n_reasoning_deltas") or 0 for c in has_split)
        out[prov] = {
            "n_calls": len(cs),
            "n_calls_decode_ge200vis": len(big),
            "decode_tok_s_incl_reasoning_p50": _pct(incl, 50),
            "decode_tok_s_visible_only_p50": _pct(excl, 50),
            "decode_tok_s_incl_reasoning_p95": _pct(incl, 95),
            "decode_tok_s_visible_only_p95": _pct(excl, 95),
            "pilot_calls_with_split_fields": len(has_split),
            "pilot_streamed_reasoning_deltas_total": streamed_reasoning,
            "note": "pilot calls predate split instrumentation" if not has_split else "",
        }
    return out


# ---- step 2 -----------------------------------------------------------------
LONGGEN_PROMPT = ("Write approximately 600 words of continuous prose about the history of "
                  "mechanical timekeeping, from sundials to the pendulum clock. Write flowing "
                  "paragraphs only; do not use lists or headings.")


def step2_longgen_probe(reps=5):
    providers = load_providers()
    os.environ["PROXY_INJECT_PARAMS"] = "0"  # we set params explicitly below
    out = {}
    with ProxyManager():
        for prov in ("coral", "baseten"):
            model = providers[prov].model
            reps_data = []
            for i in range(reps):
                url = proxy_base_url(prov, "diag-longgen", i) + "/chat/completions"
                body = {"model": model, "messages": [{"role": "user", "content": LONGGEN_PROMPT}],
                        "stream": True, "stream_options": {"include_usage": True},
                        "max_tokens": 900, "reasoning_effort": "none", "temperature": 0.0}
                t0 = time.perf_counter()
                first = None
                usage = None
                n_chunks = 0
                with httpx.stream("POST", url, json=body, timeout=180) as r:
                    for line in r.iter_lines():
                        if not line.startswith("data:"):
                            continue
                        d = line[5:].strip()
                        if d == "[DONE]":
                            continue
                        try:
                            o = json.loads(d)
                        except Exception:
                            continue
                        ch = o.get("choices") or []
                        if ch and (ch[0].get("delta") or {}).get("content"):
                            n_chunks += 1
                            if first is None:
                                first = time.perf_counter()
                        last = time.perf_counter()
                        if isinstance(o.get("usage"), dict):
                            usage = o["usage"]
                reps_data.append({"usage": usage, "n_content_chunks": n_chunks,
                                  "ttft_s": round(first - t0, 3) if first else None,
                                  "content_decode_s": round(last - first, 3) if first else None})
            # read the enhanced proxy records for these calls (authoritative split)
            recs = [json.loads(l) for l in open(CALLS)
                    if '"diag-longgen"' in l]
            recs = [r for r in recs if r["provider"] == prov]
            summ = _summarize_longgen(prov, reps_data, recs)
            out[prov] = summ
    return out


def _summarize_longgen(prov, reps_data, recs):
    outs, decs, tps, reasoning = [], [], [], []
    for r in recs:
        o = r.get("output_tokens") or 0
        reasoning.append(r.get("reasoning_tokens") or 0)
        cd = r.get("content_decode_s")
        nc = r.get("n_content_deltas") or 0
        vis = o - (r.get("reasoning_tokens") or 0)
        if cd and cd > 0 and vis > 0:
            decs.append(vis / cd)
        if nc > 0:
            tps.append(vis / nc)  # visible tokens per content chunk
        outs.append(o)
    return {
        "reps": len(recs),
        "visible_decode_tok_s_p50": _pct(decs, 50),
        "output_tokens_median": st.median(outs) if outs else None,
        "reasoning_tokens_seen": sorted(set(reasoning)),
        "streamed_reasoning_deltas_total": sum(r.get("n_reasoning_deltas") or 0 for r in recs),
        "tokens_per_content_chunk_p50": _pct(tps, 50),
        "n_content_deltas_median": st.median([r.get("n_content_deltas") or 0 for r in recs]) if recs else None,
    }


# ---- step 3 -----------------------------------------------------------------
def step3_speed_vs_rounds(calls):
    rounds = [json.loads(l) for l in open(os.path.join(RAW_DIR, "rounds.jsonl"))]
    out = {}
    for prov in ("coral", "baseten"):
        rr = [r for r in rounds if r["provider"] == prov]
        cs = [c for c in calls if c["provider"] == prov]
        agent_times = [r.get("agent_duration_s") for r in rr if r.get("agent_duration_s")]
        # per-call model latency (proxy monotonic clock; valid per call)
        lat = [(c["mono_request_end"] - c["mono_request_start"])
               for c in cs if c.get("mono_request_end") and c.get("mono_request_start")]
        # startup per round = first call's wall start - round agent_phase_start
        startups = []
        for r in rr:
            try:
                ap = datetime.fromisoformat(r["agent_phase_start"])
            except Exception:
                continue
            rc = [c for c in cs if c["run_id"] == r["run_id"] and str(c["round"]) == str(r["round"])
                  and c.get("wall_request_start")]
            if not rc:
                continue
            firstcall = min(datetime.fromisoformat(c["wall_request_start"]) for c in rc)
            startups.append((firstcall - ap).total_seconds())
        out[prov] = {
            "rounds": len(rr),
            "agent_time_per_round_p50": _pct(agent_times, 50),
            "agent_time_per_round_p95": _pct(agent_times, 95),
            "model_latency_per_call_p50": _pct(lat, 50),
            "model_latency_per_call_p95": _pct(lat, 95),
            "opencode_startup_per_round_p50": _pct(startups, 50),
            "opencode_startup_per_round_p95": _pct(startups, 95),
        }
    return out


# ---- step 4 -----------------------------------------------------------------
def _cost(U, C, O, pr):
    return (U * pr["input"] + C * pr["cached"] + O * pr["output"]) / 1e6


def step4_cost_decomposition(calls):
    providers = load_providers()
    pr = {p: {"input": providers[p].price_input, "cached": providers[p].price_cached_read,
              "output": providers[p].price_output} for p in ("coral", "baseten")}
    tok = {}
    for prov in ("coral", "baseten"):
        cs = [c for c in calls if c["provider"] == prov]
        tok[prov] = {
            "U": sum(c.get("uncached_tokens") or 0 for c in cs),
            "C": sum(c.get("cached_tokens") or 0 for c in cs),
            "O": sum(c.get("output_tokens") or 0 for c in cs),
        }
    cor, bas = tok["coral"], tok["baseten"]
    coral_own = _cost(cor["U"], cor["C"], cor["O"], pr["coral"])
    baseten_own = _cost(bas["U"], bas["C"], bas["O"], pr["baseten"])
    baseten_at_coral = _cost(bas["U"], bas["C"], bas["O"], pr["coral"])
    coral_at_baseten = _cost(cor["U"], cor["C"], cor["O"], pr["baseten"])
    gap = baseten_own - coral_own
    price_effect = baseten_own - baseten_at_coral      # baseten tokens: own vs coral prices
    token_cache_effect = baseten_at_coral - coral_own  # same (coral) prices: token/cache diff
    return {
        "tokens": tok, "prices": pr,
        "coral_own_cost": coral_own, "baseten_own_cost": baseten_own,
        "baseten_tokens_at_coral_prices": baseten_at_coral,
        "coral_tokens_at_baseten_prices": coral_at_baseten,
        "gap_baseten_minus_coral": gap,
        "price_effect": price_effect,
        "token_cache_effect": token_cache_effect,
        "price_effect_pct_of_gap": (price_effect / gap * 100) if gap else None,
        "token_cache_effect_pct_of_gap": (token_cache_effect / gap * 100) if gap else None,
    }


# ---- step 5 -----------------------------------------------------------------
def step5_cache_misses(calls):
    out = {}
    for prov in ("coral", "baseten"):
        cs = [c for c in calls if c["provider"] == prov]
        by_run = {}
        for c in cs:
            by_run.setdefault(c["run_id"], []).append(c)
        first_ids = set()
        for rid, rc in by_run.items():
            rc_sorted = sorted(rc, key=lambda c: (int(c["round"]), int(c["call_index"])))
            first_ids.add(id(rc_sorted[0]))
        def hit(subset):
            cac = sum(c.get("cached_tokens") or 0 for c in subset)
            inp = cac + sum(c.get("uncached_tokens") or 0 for c in subset)
            return (cac / inp) if inp else None
        firsts = [c for c in cs if id(c) in first_ids]
        mids = [c for c in cs if id(c) not in first_ids]
        out[prov] = {
            "n_runs": len(by_run),
            "hit_rate_all_calls": hit(cs),
            "hit_rate_excluding_first_call": hit(mids),
            "first_calls_cached_tokens": sum(c.get("cached_tokens") or 0 for c in firsts),
            "first_calls_uncached_tokens": sum(c.get("uncached_tokens") or 0 for c in firsts),
            "first_calls_share_of_uncached": (
                sum(c.get("uncached_tokens") or 0 for c in firsts)
                / max(sum(c.get("uncached_tokens") or 0 for c in cs), 1)
            ),
        }
    return out


def main():
    calls = _pilot_calls()
    result = {
        "step1_decode_validity": step1_decode_validity(calls),
        "step3_speed_vs_rounds": step3_speed_vs_rounds(calls),
        "step4_cost_decomposition": step4_cost_decomposition(calls),
        "step5_cache_misses": step5_cache_misses(calls),
    }
    print("running step 2 long-generation probe (reasoning_effort=none, 5 reps/provider)...")
    result["step2_longgen_probe"] = step2_longgen_probe()

    os.makedirs(PREFLIGHT_DIR, exist_ok=True)
    with open(os.path.join(PREFLIGHT_DIR, "pilot_diagnostics.json"), "w") as f:
        json.dump(result, f, indent=2, default=str)
    print(json.dumps(result, indent=2, default=str))
    print("\nWrote results/preflight/pilot_diagnostics.json")


if __name__ == "__main__":
    main()
