"""Deep-dive on Baseten prompt caching (steps 2-5 of the cache investigation).

All requests go DIRECT to Baseten (no proxy) so the proxy is ruled out.

  step 2: 3 identical ~20k-token requests (same x-session-affinity uuid) per slug
          [1017-Fast, Fast, standard, GLM-5.3]; record cached_tokens + TTFT.
  step 3: repeat the 1017-Fast probe with the official OpenAI Python SDK.
  step 4: query Baseten usage API for the probe window (cached vs uncached).
  step 5: save 3 Baseten request IDs (from response headers) for a support ticket.

Writes results/preflight/baseten_cache_deepdive.json.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone

import httpx

from harness.common import PREFLIGHT_DIR
from proxy.config import load_dotenv, load_providers

load_dotenv()
P = load_providers()["baseten"]
ORIGIN = P.origin
KEY = P.api_key()

SLUGS = [
    "deepseek-ai/DeepSeek-V4.1-Flash-1017-Fast",
    "deepseek-ai/DeepSeek-V4.1-Flash-Fast",
    "deepseek-ai/DeepSeek-V4.1-Flash",
    "zai-org/GLM-5.3",
]

REQUEST_ID_HEADERS = ["x-baseten-request-id", "baseten-request-id", "x-request-id",
                      "x-amzn-requestid", "cf-ray"]


def _big_prompt(approx_tokens: int) -> str:
    unit = "The quick brown fox jumps over the lazy dog. "
    return "CACHE PROBE CONTEXT.\n" + unit * (approx_tokens * 4 // len(unit))


def stream_once(slug: str, prompt: str, affinity: str) -> dict:
    url = f"{ORIGIN}/v1/chat/completions"
    headers = {"Authorization": f"Bearer {KEY}", "x-session-affinity": affinity}
    body = {"model": slug, "messages": [{"role": "user", "content": prompt}],
            "stream": True, "stream_options": {"include_usage": True}, "max_tokens": 16}
    t0 = time.perf_counter()
    ttft = None
    usage = None
    rid = {}
    status = None
    try:
        with httpx.stream("POST", url, json=body, headers=headers, timeout=120) as r:
            status = r.status_code
            rid = {h: r.headers.get(h) for h in REQUEST_ID_HEADERS if r.headers.get(h)}
            if status != 200:
                return {"status": status, "error": r.read().decode("utf-8", "ignore")[:200],
                        "request_id_headers": rid}
            for line in r.iter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    continue
                try:
                    obj = json.loads(data)
                except Exception:
                    continue
                ch = obj.get("choices") or []
                if ch and (ch[0].get("delta") or {}).get("content") and ttft is None:
                    ttft = time.perf_counter() - t0
                if isinstance(obj.get("usage"), dict):
                    usage = obj["usage"]
    except Exception as e:
        return {"status": status, "error": f"{type(e).__name__}: {e}", "request_id_headers": rid}
    det = (usage or {}).get("prompt_tokens_details") or {}
    return {
        "status": status,
        "prompt_tokens": (usage or {}).get("prompt_tokens"),
        "cached_tokens": det.get("cached_tokens"),
        "ttft_s": round(ttft, 3) if ttft else None,
        "request_id_headers": rid,
    }


def step2_multi_slug() -> dict:
    print("=== step 2: multi-slug affinity probe (3x ~20k, same uuid) DIRECT to Baseten ===")
    prompt = _big_prompt(20000) + "\nReply DONE."
    out = {}
    for slug in SLUGS:
        aff = str(uuid.uuid4())
        rows = []
        print(f"\n  {slug}  (affinity={aff})")
        for i in range(3):
            res = stream_once(slug, prompt, aff)
            rows.append(res)
            print(f"    #{i+1}: status={res.get('status')} prompt_tokens={res.get('prompt_tokens')} "
                  f"cached_tokens={res.get('cached_tokens')} ttft={res.get('ttft_s')}s "
                  f"err={res.get('error')}")
        out[slug] = {"affinity_uuid": aff, "runs": rows}
    return out


def step3_openai_sdk() -> dict:
    print("\n=== step 3: 1017-Fast probe via official OpenAI Python SDK (no proxy) ===")
    try:
        from openai import OpenAI
    except Exception as e:
        print(f"  openai SDK unavailable: {e}")
        return {"error": str(e)}
    client = OpenAI(base_url=f"{ORIGIN}/v1", api_key=KEY)
    slug = SLUGS[0]
    aff = str(uuid.uuid4())
    prompt = _big_prompt(20000) + "\nReply DONE."
    rows = []
    print(f"  {slug} (affinity={aff})")
    for i in range(3):
        t0 = time.perf_counter()
        ttft = None
        usage = None
        rid = None
        stream = client.chat.completions.create(
            model=slug, messages=[{"role": "user", "content": prompt}],
            stream=True, stream_options={"include_usage": True}, max_tokens=16,
            extra_headers={"x-session-affinity": aff},
        )
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content and ttft is None:
                ttft = time.perf_counter() - t0
            if getattr(chunk, "usage", None):
                usage = chunk.usage
        det = getattr(usage, "prompt_tokens_details", None)
        cached = getattr(det, "cached_tokens", None) if det else None
        row = {"prompt_tokens": getattr(usage, "prompt_tokens", None),
               "cached_tokens": cached, "ttft_s": round(ttft, 3) if ttft else None}
        rows.append(row)
        print(f"    #{i+1}: prompt_tokens={row['prompt_tokens']} cached_tokens={row['cached_tokens']} "
              f"ttft={row['ttft_s']}s")
    return {"slug": slug, "affinity_uuid": aff, "runs": rows}


def step4_usage_api(window_start: str, window_end: str) -> dict:
    print("\n=== step 4: Baseten usage API for the probe window ===")
    base = "https://api.baseten.co/v1/model_apis/usage"
    attempts = [
        {"start": window_start, "end": window_end, "bucket": "1m", "group_by": "model"},
        {"start_time": window_start, "end_time": window_end, "granularity": "minute", "group_by": "model"},
    ]
    results = []
    for params in attempts:
        try:
            r = httpx.get(base, headers={"Authorization": f"Api-Key {KEY}"}, params=params, timeout=30)
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text
        except Exception as e:
            results.append({"params": params, "error": str(e)})
            continue
        results.append({"params": params, "status": r.status_code, "body": body})
        print(f"  params={list(params)} -> status={r.status_code}")
        if r.status_code == 200:
            print("  body:", json.dumps(body)[:800])
    return {"endpoint": base, "attempts": results}


def main() -> int:
    if not KEY:
        print("No Baseten key.")
        return 1
    window_start = datetime.now(timezone.utc).isoformat()

    s2 = step2_multi_slug()
    s3 = step3_openai_sdk()
    window_end = datetime.now(timezone.utc).isoformat()
    time.sleep(3)  # let usage aggregation catch up a little
    s4 = step4_usage_api(window_start, window_end)

    # step 5: collect up to 3 request IDs
    req_ids = []
    for slug, d in s2.items():
        for run in d["runs"]:
            for k, v in (run.get("request_id_headers") or {}).items():
                req_ids.append({"slug": slug, "header": k, "value": v})
    req_ids = req_ids[:3]

    out = {"window_start": window_start, "window_end": window_end,
           "step2_multi_slug": s2, "step3_openai_sdk": s3,
           "step4_usage_api": s4, "step5_request_ids": req_ids}
    os.makedirs(PREFLIGHT_DIR, exist_ok=True)
    with open(os.path.join(PREFLIGHT_DIR, "baseten_cache_deepdive.json"), "w") as f:
        json.dump(out, f, indent=2, default=str)

    print("\n=== step 5: request IDs saved (for support ticket) ===")
    for r in req_ids:
        print(f"  {r['slug']}  {r['header']}={r['value']}")

    print("\n=== verdict per slug (cached_tokens across 3 identical 20k reqs / TTFTs) ===")
    for slug, d in s2.items():
        cached = [r.get("cached_tokens") for r in d["runs"]]
        ttfts = [r.get("ttft_s") for r in d["runs"]]
        caches = any((c or 0) > 0 for c in cached[1:])
        print(f"  {slug}: cached={cached} ttft={ttfts} -> {'CACHES' if caches else 'no cached_tokens reported'}")
    print("\nWrote results/preflight/baseten_cache_deepdive.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
