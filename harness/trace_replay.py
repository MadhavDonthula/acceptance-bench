"""TRACE REPLAY (pre-registered secondary experiment).

Replays each CAPTURED session's exact request sequence (same bodies, same
inter-call gaps) against ALL arms, with a fresh session/affinity id per replay, to
isolate inference speed + caching from agent behavior.

Reads: results/raw/pilot_plan.json (capture_run_ids) + runs/<id>/bodies/*.json
       (gitignored captured bodies) + results/raw/calls.jsonl (original gaps).
Writes: results/raw/trace_replay.jsonl (one row per session×arm; no message bodies).

Run:  python -m harness.trace_replay
"""

from __future__ import annotations

import glob
import json
import os
import time
import uuid
from datetime import datetime

import httpx

from harness.common import RAW_DIR, RUNS_DIR
from proxy.config import load_dotenv, load_providers

load_dotenv()
REPLAY_ARMS = ["coral", "baseten", "baseten_sept"]
GAP_CAP_S = 20.0  # reproduce original inter-call gaps, capped for safety


def _plan():
    return json.load(open(os.path.join(RAW_DIR, "pilot_plan.json")))


def _ordered_bodies(run_id: str):
    bdir = os.path.join(RUNS_DIR, run_id, "bodies")
    files = sorted(glob.glob(os.path.join(bdir, "*.json")),
                   key=lambda p: [int(x) for x in os.path.basename(p)[:-5].split("_")])
    return files


def _original_gaps(run_id: str, n: int):
    """Inter-call wall gaps (s) for the captured run, ordered by (round, call_index)."""
    calls = []
    for ln in open(os.path.join(RAW_DIR, "calls.jsonl")):
        r = json.loads(ln)
        if r.get("run_id") == run_id and (r.get("path") or "").endswith("chat/completions"):
            calls.append(r)
    calls.sort(key=lambda r: (int(r["round"]), int(r["call_index"])))
    ts = [datetime.fromisoformat(r["wall_request_start"]) for r in calls if r.get("wall_request_start")]
    gaps = [0.0]
    for i in range(1, len(ts)):
        gaps.append(min(max((ts[i] - ts[i - 1]).total_seconds(), 0.0), GAP_CAP_S))
    while len(gaps) < n:
        gaps.append(0.0)
    return gaps[:n]


def _post_with_retry(url, b, headers, attempts=5):
    """Retry on 429/5xx with backoff (Baseten TPM limits; baseten + baseten_sept
    share one account quota, so replays can transiently throttle)."""
    for i in range(attempts):
        r = httpx.stream("POST", url, json=b, headers=headers, timeout=180)
        resp = r.__enter__()
        if resp.status_code not in (429, 500, 502, 503, 529):
            return r, resp
        retry_after = resp.headers.get("retry-after")
        r.__exit__(None, None, None)
        wait = float(retry_after) if (retry_after and retry_after.replace(".", "").isdigit()) else min(2 ** i, 30)
        time.sleep(wait)
    r = httpx.stream("POST", url, json=b, headers=headers, timeout=180)
    return r, r.__enter__()


def _replay_call(origin, key, model, body, affinity):
    b = dict(body)
    b["model"] = model
    b["stream"] = True
    b["stream_options"] = {"include_usage": True}
    headers = {"Authorization": f"Bearer {key}", "x-session-affinity": affinity}
    t0 = time.perf_counter()
    first = last = None
    usage = None
    cm, r = _post_with_retry(f"{origin}/v1/chat/completions", b, headers)  # cm already entered
    try:
        status = r.status_code
        if status != 200:
            return {"status": status, "error": r.read().decode("utf-8", "ignore")[:150]}
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
                now = time.perf_counter()
                if first is None:
                    first = now
                last = now
            if isinstance(o.get("usage"), dict):
                usage = o["usage"]
    finally:
        cm.__exit__(None, None, None)
    latency = time.perf_counter() - t0
    det = (usage or {}).get("prompt_tokens_details") or {}
    cached = det.get("cached_tokens") or 0
    prompt = (usage or {}).get("prompt_tokens") or 0
    out = (usage or {}).get("completion_tokens") or 0
    return {
        "status": status, "latency_s": latency,
        "ttft_content_s": (first - t0) if first else None,
        "content_decode_s": (last - first) if (first and last) else None,
        "prompt_tokens": prompt, "cached_tokens": cached,
        "uncached_tokens": max(prompt - cached, 0), "output_tokens": out,
    }


def replay_session(run_id: str, arm: str, providers) -> dict:
    cfg = providers[arm]
    files = _ordered_bodies(run_id)
    gaps = _original_gaps(run_id, len(files))
    affinity = str(uuid.uuid4())  # fresh per replay -> cold, attributable to provider
    calls = []
    for f, gap in zip(files, gaps):
        if gap > 0:
            time.sleep(gap)
        body = json.load(open(f))
        calls.append(_replay_call(cfg.origin, cfg.api_key(), cfg.model, body, affinity))
    ok = [c for c in calls if c.get("status") == 200]
    cached = sum(c["cached_tokens"] for c in ok)
    uncached = sum(c["uncached_tokens"] for c in ok)
    output = sum(c["output_tokens"] for c in ok)
    model_time = sum(c["latency_s"] for c in ok)
    cost = cfg.cost(uncached, cached, output)
    return {
        "source_run_id": run_id, "arm": arm, "model": cfg.model,
        "n_calls": len(files), "n_ok": len(ok),
        "model_time_s": round(model_time, 3),
        "cache_hit_rate": (cached / (cached + uncached)) if (cached + uncached) else None,
        "cost_usd_public": round(cost, 6),
        "ttft_content_p50": _p50([c.get("ttft_content_s") for c in ok]),
        "content_decode_total_s": round(sum(c.get("content_decode_s") or 0 for c in ok), 3),
        "cached_tokens": cached, "uncached_tokens": uncached, "output_tokens": output,
        "per_call": calls,
    }


def _p50(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    return xs[len(xs) // 2]


def main() -> int:
    providers = load_providers()
    plan = _plan()
    capture_ids = plan.get("capture_run_ids", [])
    capture_ids = [rid for rid in capture_ids if _ordered_bodies(rid)]
    if not capture_ids:
        print("No captured session bodies found (run the main experiment with capture first).")
        return 1
    out_path = os.path.join(RAW_DIR, "trace_replay.jsonl")
    open(out_path, "w").close()
    print(f"Replaying {len(capture_ids)} captured sessions × {len(REPLAY_ARMS)} arms "
          f"= {len(capture_ids)*len(REPLAY_ARMS)} replays\n")
    for rid in capture_ids:
        for arm in REPLAY_ARMS:
            time.sleep(5)  # pace replays; baseten + baseten_sept share one account TPM
            res = replay_session(rid, arm, providers)
            with open(out_path, "a") as f:
                f.write(json.dumps({k: v for k, v in res.items() if k != "per_call"}) + "\n")
            print(f"  {rid:16s} -> {arm:14s} model_time={res['model_time_s']}s "
                  f"cache={_pf(res['cache_hit_rate'])} cost=${res['cost_usd_public']:.4f} "
                  f"ttft_p50={res['ttft_content_p50']}")
    print(f"\nWrote {out_path}")
    return 0


def _pf(x):
    return "—" if x is None else f"{x*100:.1f}%"


if __name__ == "__main__":
    raise SystemExit(main())
