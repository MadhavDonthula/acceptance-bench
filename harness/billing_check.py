"""Billing check: compare Baseten's actual billed usage to our computed cost.

Sums the pilot's Baseten token usage (from calls.jsonl), computes cost at public
prices WITH and WITHOUT the cache discount, then queries Baseten's usage and
billing APIs for the window and reports which computation billing matches.

Run:  python -m harness.billing_check
"""

from __future__ import annotations

import json
import os

import httpx

from harness.common import RAW_DIR, ROOT
from proxy.config import load_dotenv, load_providers

load_dotenv()


def pilot_baseten_tokens() -> dict:
    path = os.path.join(RAW_DIR, "calls.jsonl")
    cached = uncached = output = 0
    model = None
    for ln in open(path):
        r = json.loads(ln)
        if not str(r.get("run_id", "")).startswith("run"):
            continue
        if r.get("provider") != "baseten":
            continue
        cached += int(r.get("cached_tokens") or 0)
        uncached += int(r.get("uncached_tokens") or 0)
        output += int(r.get("output_tokens") or 0)
        model = r.get("model")
    return {"model": model, "cached": cached, "uncached": uncached, "output": output}


def main() -> int:
    P = load_providers()["baseten"]
    key = P.api_key()
    tok = pilot_baseten_tokens()
    inp = P.price_input, P.price_cached_read, P.price_output
    with_disc = (tok["uncached"] * inp[0] + tok["cached"] * inp[1] + tok["output"] * inp[2]) / 1e6
    no_disc = ((tok["uncached"] + tok["cached"]) * inp[0] + tok["output"] * inp[2]) / 1e6
    print("=== pilot Baseten token totals (from calls.jsonl) ===")
    print(f"  model={tok['model']} cached={tok['cached']} uncached={tok['uncached']} output={tok['output']}")
    print(f"  computed cost WITH cache discount:    ${with_disc:.4f}")
    print(f"  computed cost WITHOUT cache discount: ${no_disc:.4f}")

    out = {"token_totals": tok, "computed_with_discount": with_disc,
           "computed_no_discount": no_disc, "usage_api": None, "billing_api": None}

    print("\n=== Baseten usage API (today, grouped by model) ===")
    try:
        r = httpx.get("https://api.baseten.co/v1/model_apis/usage",
                      headers={"Authorization": f"Api-Key {key}"},
                      params={"start_time": "2026-11-05T00:00:00Z", "end_time": "2026-11-06T00:00:00Z",
                              "granularity": "minute", "group_by": "model"}, timeout=30)
        data = r.json()
        out["usage_api"] = {"status": r.status_code, "body": data}
        for it in data.get("items", []):
            for res in it.get("results", []):
                if "Flash" in (res.get("model") or "") or "GLM" in (res.get("model") or ""):
                    print(f"  {res['model']}: input={res['input_tokens']} "
                          f"cached_input={res['cached_input_tokens']} "
                          f"uncached_input={res['uncached_input_tokens']} out={res['output_tokens']} "
                          f"reqs={res['request_count']}")
    except Exception as e:
        out["usage_api"] = {"error": str(e)}
        print("  usage API error:", e)

    print("\n=== Baseten billing API (GET /v1/billing/model_apis) ===")
    for params in ({"start_time": "2026-11-05T00:00:00Z", "end_time": "2026-11-06T00:00:00Z"},
                   {}):
        try:
            r = httpx.get("https://api.baseten.co/v1/billing/model_apis",
                          headers={"Authorization": f"Api-Key {key}"}, params=params, timeout=30)
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text
            print(f"  params={params or '(none)'} -> status={r.status_code}")
            print("  body:", json.dumps(body)[:700] if isinstance(body, (dict, list)) else str(body)[:300])
            if out["billing_api"] is None or r.status_code == 200:
                out["billing_api"] = {"params": params, "status": r.status_code, "body": body}
            if r.status_code == 200:
                break
        except Exception as e:
            print(f"  params={params} -> error {e}")
            out["billing_api"] = {"params": params, "error": str(e)}

    with open(os.path.join(ROOT, "results", "preflight", "billing_check.json"), "w") as f:
        json.dump(out, f, indent=2, default=str)

    # verdict: does usage API show cached_input matching our discount assumption?
    usage = out.get("usage_api") or {}
    cached_billed = None
    if isinstance(usage.get("body"), dict):
        for it in usage["body"].get("items", []):
            for res in it.get("results", []):
                if res.get("model") == tok["model"]:
                    cached_billed = res.get("cached_input_tokens")
    print("\n=== verdict ===")
    print(f"  response cached_tokens (ours): {tok['cached']}")
    print(f"  usage-API cached_input_tokens for {tok['model']}: {cached_billed}")
    if cached_billed is not None:
        if cached_billed and cached_billed > 0:
            print("  -> billing DOES credit cached input; primary (with-discount) cost is the match.")
        else:
            print("  -> billing shows cached_input=0; the NO-discount cost is the honest match "
                  "for Baseten (cache helps latency, not billed cost). PENDING final billing API.")
    print("\nWrote results/preflight/billing_check.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
