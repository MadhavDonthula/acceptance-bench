"""Resolve the Baseten cache-probe anomaly (cached=0 on identical repeats).

Part A: capture which headers OpenCode actually sends through the proxy (names
only; non-auth session-header values shown) by running one trivial opencode turn
per provider, and report in-session cached tokens.

Part B: re-run the cache probe WITH the documented `x-session-affinity: <uuid>`
header (same uuid across all calls): 3 identical ~5k-token requests then 3
identical ~20k-token requests, on Baseten and Coral. Record cached_tokens each.

Run:  python -m harness.cache_investigation
Writes results/preflight/cache_investigation.json.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import uuid

import httpx

from harness.common import OPENCODE_BIN, PREFLIGHT_DIR, ROOT, load_providers, proxy_base_url, write_opencode_config
from harness.proxy_manager import ProxyManager
from proxy.config import load_dotenv

load_dotenv()


def _big_prompt(approx_tokens: int) -> str:
    unit = "The quick brown fox jumps over the lazy dog. "
    body = unit * (approx_tokens * 4 // len(unit))
    return "CACHE PROBE CONTEXT.\n" + body


def _calls(run_id: str) -> list[dict]:
    out = []
    path = os.path.join(ROOT, "results", "raw", "calls.jsonl")
    for ln in open(path):
        try:
            r = json.loads(ln)
        except Exception:
            continue
        if r.get("run_id") == run_id:
            out.append(r)
    return out


def capture_opencode_headers(providers) -> dict:
    print("\n=== Part A: headers OpenCode sends (per provider) ===")
    result = {}
    for name in providers:
        ws = tempfile.mkdtemp(prefix=f"hdrcap_{name}_")
        cfg = os.path.join(ws, "opencode.json")
        write_opencode_config(cfg, name, "hdrcap", 0)
        env = {**os.environ, "OPENCODE_CONFIG": cfg, "OPENCODE_DISABLE_AUTOUPDATE": "1",
               "PATH": os.path.join(ROOT, "node_modules", ".bin") + os.pathsep + os.environ.get("PATH", "")}
        subprocess.run([OPENCODE_BIN, "run", "Reply with the single word READY. Do not use any tools."],
                       cwd=ws, env=env, stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180)
        calls = _calls("hdrcap")
        mine = [c for c in calls if c["provider"] == name]
        if mine:
            c0 = mine[0]
            result[name] = {
                "n_calls": len(mine),
                "request_header_names": c0.get("request_header_names"),
                "forwarded_header_names": c0.get("forwarded_header_names"),
                "session_headers": c0.get("session_headers"),
                "cached_per_call": [c.get("cached_tokens") for c in mine],
                "uncached_per_call": [c.get("uncached_tokens") for c in mine],
            }
            print(f"\n  {name}: {len(mine)} call(s)")
            print(f"    header names: {c0.get('request_header_names')}")
            print(f"    session headers: {c0.get('session_headers')}")
            print(f"    forwarded names: {c0.get('forwarded_header_names')}")
            print(f"    cached per call: {result[name]['cached_per_call']}  "
                  f"uncached: {result[name]['uncached_per_call']}")
    return result


def _probe_once(provider: str, run_id: str, rnd: int, prompt: str, affinity: str) -> dict:
    url = proxy_base_url(provider, run_id, rnd) + "/chat/completions"
    body = {"model": load_providers()[provider].model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False, "max_tokens": 16}
    r = httpx.post(url, json=body, headers={"x-session-affinity": affinity}, timeout=120)
    data = r.json()
    u = data.get("usage") or {}
    det = u.get("prompt_tokens_details") or {}
    return {"status": r.status_code,
            "prompt_tokens": u.get("prompt_tokens"),
            "cached_tokens": det.get("cached_tokens")}


def affinity_probe(providers) -> dict:
    print("\n=== Part B: cache probe WITH x-session-affinity (same uuid per provider) ===")
    result = {}
    prompts = {"5k": _big_prompt(5000) + "\nReply DONE.",
               "20k": _big_prompt(20000) + "\nReply DONE."}
    for name in providers:
        aff = str(uuid.uuid4())
        result[name] = {"affinity_uuid": aff, "5k": [], "20k": []}
        print(f"\n  {name} (affinity={aff}):")
        for size, rnd in (("5k", 0), ("20k", 1)):
            for i in range(3):
                res = _probe_once(name, "affprobe", rnd, prompts[size], aff)
                result[name][size].append(res)
                print(f"    {size} #{i+1}: status={res['status']} "
                      f"prompt_tokens={res['prompt_tokens']} cached_tokens={res['cached_tokens']}")
    return result


def main() -> int:
    providers = load_providers()
    os.environ["PROXY_INJECT_PARAMS"] = "0"  # keep probe outputs tiny/cheap; measure INPUT caching
    with ProxyManager():
        headers = capture_opencode_headers(providers)
        probe = affinity_probe(providers)

    out = {"opencode_headers": headers, "affinity_probe": probe}
    os.makedirs(PREFLIGHT_DIR, exist_ok=True)
    with open(os.path.join(PREFLIGHT_DIR, "cache_investigation.json"), "w") as f:
        json.dump(out, f, indent=2)

    print("\n=== verdict ===")
    for name in providers:
        p = probe[name]
        c5 = [x["cached_tokens"] for x in p["5k"]]
        c20 = [x["cached_tokens"] for x in p["20k"]]
        caches = any((c or 0) > 0 for c in c5[1:] + c20[1:])
        print(f"  {name}: 5k cached={c5}  20k cached={c20}  -> "
              f"{'CACHES under affinity' if caches else 'NO caching even under affinity'}")
    print("\nWrote results/preflight/cache_investigation.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
