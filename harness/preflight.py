"""Preflight checks. Run BEFORE any benchmark run; stop and report on failure.

1. Both keys present. GET /v1/models on both providers -> results/preflight/.
2. One tiny chat request to each provider THROUGH the proxy; print raw usage.
3. Two identical ~5k-token requests back to back per provider; confirm cached
   tokens appear on the second.
4. Baseten rate limits for the chosen slug (GET api.baseten.co model_apis).
   WARN loudly if TPM < 500,000 and ask whether to proceed.
5. OpenCode smoke test on each provider end-to-end through the proxy.

Exit code 0 only if all hard checks pass. Writes results/preflight/report.json.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from urllib.parse import quote

import httpx

from harness.common import (
    OPENCODE_BIN, PREFLIGHT_DIR, ROOT, load_providers, load_settings,
    proxy_base_url, write_opencode_config,
)
from harness.proxy_manager import ProxyManager
from proxy.config import load_dotenv
from proxy.usage import parse_usage

load_dotenv()
TPM_THRESHOLD = 500_000


def _save(name: str, obj) -> None:
    os.makedirs(PREFLIGHT_DIR, exist_ok=True)
    with open(os.path.join(PREFLIGHT_DIR, name), "w") as f:
        if isinstance(obj, (dict, list)):
            json.dump(obj, f, indent=2)
        else:
            f.write(str(obj))


def _big_prompt(approx_tokens: int = 5000) -> str:
    # ~4 chars/token; deterministic, identical across the two calls.
    unit = "The quick brown fox jumps over the lazy dog. "
    return ("CACHE PROBE CONTEXT.\n" + unit * (approx_tokens * 4 // len(unit)))


def check_keys(providers) -> tuple[bool, dict]:
    print("\n[1] API keys present + GET /v1/models")
    detail = {}
    ok = True
    for name, p in providers.items():
        has = bool(p.api_key())
        detail[name] = {"key_present": has, "key_env": p.api_key_env}
        if not has:
            print(f"    FAIL  {name}: missing {p.api_key_env} in .env")
            ok = False
            continue
        try:
            r = httpx.get(f"{p.origin}/v1/models",
                          headers={"Authorization": f"Bearer {p.api_key()}"}, timeout=30)
            _save(f"models_{name}.json",
                  r.json() if r.headers.get("content-type", "").startswith("application/json")
                  else r.text)
            detail[name]["models_status"] = r.status_code
            ids = [m.get("id") for m in (r.json().get("data") or [])] if r.status_code == 200 else []
            detail[name]["model_ids_sample"] = ids[:20]
            print(f"    {'OK  ' if r.status_code==200 else 'FAIL'} {name}: /v1/models -> {r.status_code}, "
                  f"{len(ids)} models")
            ok = ok and r.status_code == 200
        except Exception as e:
            detail[name]["models_error"] = str(e)
            print(f"    FAIL  {name}: /v1/models error: {e}")
            ok = False
    return ok, detail


def _chat_through_proxy(provider_name: str, run_tag: str, step: int, messages: list,
                        stream: bool = False) -> dict:
    url = proxy_base_url(provider_name, run_tag, step) + "/chat/completions"
    providers = load_providers()
    body = {"model": providers[provider_name].model, "messages": messages, "stream": stream}
    if stream:
        body["stream_options"] = {"include_usage": True}
    r = httpx.post(url, json=body, timeout=120)
    out = {"status": r.status_code}
    try:
        data = r.json()
        out["usage_raw"] = data.get("usage")
        out["usage_parsed"] = parse_usage(data.get("usage")).to_dict()
    except Exception:
        out["body_tail"] = r.text[-500:]
    return out


def check_tiny_chat(providers) -> tuple[bool, dict]:
    print("\n[2] Tiny chat through the proxy (raw usage object)")
    detail = {}
    ok = True
    for name, p in providers.items():
        if not p.api_key():
            detail[name] = {"skipped": "no key"}
            print(f"    SKIP {name}: no key")
            ok = False
            continue
        res = _chat_through_proxy(name, "preflight", 2,
                                  [{"role": "user", "content": "Reply with the single word OK."}])
        detail[name] = res
        print(f"    {name}: status={res['status']}  raw usage={json.dumps(res.get('usage_raw'))}")
        ok = ok and res["status"] == 200
    return ok, detail


def check_cache(providers) -> tuple[bool, dict]:
    print("\n[3] Two identical ~5k-token requests back-to-back (cached tokens on 2nd)")
    detail = {}
    ok = True
    big = _big_prompt()
    msgs = [{"role": "user", "content": big + "\n\nReply with the single word DONE."}]
    for name, p in providers.items():
        if not p.api_key():
            detail[name] = {"skipped": "no key"}
            print(f"    SKIP {name}: no key")
            ok = False
            continue
        first = _chat_through_proxy(name, "preflight", 3, msgs)
        second = _chat_through_proxy(name, "preflight", 3, msgs)
        c1 = (first.get("usage_parsed") or {}).get("cached_tokens", 0)
        c2 = (second.get("usage_parsed") or {}).get("cached_tokens", 0)
        detail[name] = {"first": first, "second": second,
                        "cached_first": c1, "cached_second": c2,
                        "cache_grew": c2 > c1}
        print(f"    {name}: cached 1st={c1}  2nd={c2}  "
              f"field={(second.get('usage_parsed') or {}).get('cache_field')!r}  "
              f"{'CACHE OK' if c2 > c1 else 'no cache growth observed'}")
        ok = ok and first.get("status") == 200 and second.get("status") == 200
    return ok, detail


def check_baseten_rate_limits(providers) -> tuple[bool, dict]:
    print("\n[4] Baseten rate limits for the chosen slug")
    p = providers["baseten"]
    if not p.api_key():
        print("    SKIP: no Baseten key")
        return False, {"skipped": "no key"}
    slug = p.model
    url = f"https://api.baseten.co/v1/model_apis/{quote(slug, safe='')}"
    try:
        r = httpx.get(url, headers={"Authorization": f"Api-Key {p.api_key()}"}, timeout=30)
        data = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text
    except Exception as e:
        print(f"    FAIL: {e}")
        return False, {"error": str(e), "url": url}
    _save("baseten_rate_limits.json", {"slug": slug, "status": r.status_code, "body": data})
    # Baseten returns rate_limits: [{type: TOKEN|REQUEST, unit: MINUTE, threshold: N}, ...]
    rpm = tpm = None
    if isinstance(data, dict):
        for rl in (data.get("rate_limits") or []):
            if not isinstance(rl, dict):
                continue
            t = str(rl.get("type", "")).upper()
            unit = str(rl.get("unit", "")).upper()
            thr = rl.get("threshold")
            if t == "TOKEN" and unit == "MINUTE":
                tpm = thr
            elif t == "REQUEST" and unit == "MINUTE":
                rpm = thr
    print(f"    slug={slug}  status={r.status_code}  RPM={rpm}  TPM={tpm}")
    detail = {"slug": slug, "status": r.status_code, "rpm": rpm, "tpm": tpm, "raw": data}
    low_tpm = isinstance(tpm, (int, float)) and tpm < TPM_THRESHOLD
    if low_tpm:
        print(f"    *** WARNING: TPM={tpm} < {TPM_THRESHOLD}. A coding agent re-sends large "
              f"contexts and will hit 429s, biasing the comparison AGAINST Baseten for "
              f"account reasons. Operator decision required before proceeding. ***")
    detail["tpm_below_threshold"] = low_tpm
    return r.status_code == 200, detail


def check_opencode_smoke(providers) -> tuple[bool, dict]:
    print("\n[5] OpenCode smoke test per provider (end-to-end through the proxy)")
    detail = {}
    ok = True
    for name, p in providers.items():
        if not p.api_key():
            detail[name] = {"skipped": "no key"}
            print(f"    SKIP {name}: no key")
            ok = False
            continue
        ws = tempfile.mkdtemp(prefix=f"preflight_{name}_")
        cfg = os.path.join(ws, "opencode.json")
        write_opencode_config(cfg, name, "preflight", 5)
        env = {**os.environ, "OPENCODE_CONFIG": cfg, "OPENCODE_DISABLE_AUTOUPDATE": "1",
               "PATH": os.path.join(ROOT, "node_modules", ".bin") + os.pathsep + os.environ.get("PATH", "")}
        try:
            proc = subprocess.run(
                [OPENCODE_BIN, "run", "Create a file called hello.txt containing exactly: smoke"],
                cwd=ws, env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=180,
            )
            created = os.path.exists(os.path.join(ws, "hello.txt"))
            detail[name] = {"rc": proc.returncode, "hello_created": created,
                            "stdout_tail": proc.stdout[-800:]}
            print(f"    {name}: rc={proc.returncode}  hello.txt created={created}")
            ok = ok and proc.returncode == 0
        except subprocess.TimeoutExpired:
            detail[name] = {"timeout": True}
            print(f"    FAIL {name}: smoke timed out")
            ok = False
        finally:
            shutil.rmtree(ws, ignore_errors=True)
    return ok, detail


def main() -> int:
    providers = load_providers()
    report = {"started": datetime.now(timezone.utc).isoformat(),
              "settings": load_settings().__dict__,
              "models": {p: {"model": providers[p].model} for p in providers}}

    print("=" * 70)
    print("ACCEPTANCE-BENCH PREFLIGHT")
    print("=" * 70)

    keys_ok, report["keys"] = check_keys(providers)
    if not keys_ok:
        report["result"] = "STOP: key/models check failed"
        _save("report.json", report)
        print("\n" + "=" * 70)
        print("PREFLIGHT STOPPED: fix the key/models failures above before continuing.")
        print("(Steps 2-5 need both providers reachable; not run.)")
        print("=" * 70)
        return 1

    with ProxyManager():
        _, report["tiny_chat"] = check_tiny_chat(providers)
        _, report["cache"] = check_cache(providers)
        _, report["baseten_rate_limits"] = check_baseten_rate_limits(providers)
        _, report["opencode_smoke"] = check_opencode_smoke(providers)

    report["result"] = "completed (review output; operator go-ahead required for pilot)"
    _save("report.json", report)
    print("\n" + "=" * 70)
    print("PREFLIGHT COMPLETE. Review results/preflight/ and the warnings above.")
    print("Do NOT start the pilot until the operator gives the go-ahead.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
