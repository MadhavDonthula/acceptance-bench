"""Recording streaming proxy.

OpenCode points its OpenAI-compatible baseURL at:

    http://127.0.0.1:8787/{provider}/{run_id}/{round}/v1

so a chat request arrives as
    /{provider}/{run_id}/{round}/v1/chat/completions
and is forwarded verbatim to the real provider's origin + /v1/chat/completions.

Guarantees:
  * ALL request headers are forwarded unchanged EXCEPT Authorization, which the
    proxy sets from .env. (Baseten's cache-affinity session headers are kept.)
  * The response body is streamed back untouched (raw bytes, no buffering) via
    a tee: each chunk is forwarded immediately and a copy is parsed for usage.
  * The ONLY request mutation allowed is adding stream_options.include_usage=true
    when missing, and — only if PROXY_INJECT_PARAMS=1 — injecting identical
    temperature / max_tokens / reasoning_effort on BOTH arms. What was actually
    sent is recorded.

One JSON line per call is appended to results/raw/calls.jsonl.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections import defaultdict
from datetime import datetime, timezone

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from proxy.config import ROOT, load_dotenv, load_providers, load_settings
from proxy.usage import parse_usage

load_dotenv()

RAW_DIR = os.path.join(ROOT, "results", "raw")
CALLS_PATH = os.path.join(RAW_DIR, "calls.jsonl")
RUNS_DIR = os.path.join(ROOT, "runs")

PROVIDERS = load_providers()
SETTINGS = load_settings()
INJECT_PARAMS = os.environ.get("PROXY_INJECT_PARAMS", "1") not in ("0", "", "false", "False")
# TRACE REPLAY: run_ids whose FULL request bodies (incl messages) are captured to
# runs/<run_id>/bodies/ (gitignored, never committed). Opt-in via env.
CAPTURE_RUN_IDS = {x for x in os.environ.get("PROXY_CAPTURE_RUN_IDS", "").split(",") if x}

_client = httpx.AsyncClient(timeout=httpx.Timeout(None, connect=30.0))
_write_lock = asyncio.Lock()
_idx_lock = asyncio.Lock()
_call_index: dict = defaultdict(int)

# Response headers we must not copy verbatim (hop-by-hop / length handled by us).
_DROP_RESP_HEADERS = {"content-length", "transfer-encoding", "connection", "keep-alive"}
# Request headers the proxy owns.
_DROP_REQ_HEADERS = {"authorization", "host", "content-length", "connection", "keep-alive"}


def _now_wall() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rate_limit_headers(headers) -> dict:
    out = {}
    for k, v in headers.items():
        lk = k.lower()
        if "ratelimit" in lk or "rate-limit" in lk or lk == "retry-after":
            out[k] = v
    return out


def _prediction_tokens(usage_raw: dict | None) -> dict:
    """Baseten speculative-decoding accepted/rejected prediction tokens, if any."""
    out = {}
    if not isinstance(usage_raw, dict):
        return out
    sources = [usage_raw, usage_raw.get("completion_tokens_details") or {}]
    for src in sources:
        if not isinstance(src, dict):
            continue
        for k, v in src.items():
            if "prediction" in k.lower() and isinstance(v, (int, float)):
                out[k] = int(v)
    return out


async def _next_call_index(provider: str, run_id: str, rnd: str) -> int:
    async with _idx_lock:
        key = (provider, run_id, rnd)
        i = _call_index[key]
        _call_index[key] = i + 1
        return i


def _modify_body(raw_body: bytes) -> tuple[bytes, dict, int]:
    """Apply the only allowed mutations. Returns (new_body, recorded_params, msg_count)."""
    try:
        obj = json.loads(raw_body)
    except Exception:
        return raw_body, {"_unparsed": True}, 0
    if not isinstance(obj, dict):
        return raw_body, {"_unparsed": True}, 0

    changed = False
    streaming = bool(obj.get("stream"))
    if streaming:
        so = obj.get("stream_options")
        if not isinstance(so, dict):
            so = {}
        if not so.get("include_usage"):
            so["include_usage"] = True
            obj["stream_options"] = so
            changed = True

    injected = {}
    if INJECT_PARAMS:
        for field, value, present_default in (
            ("temperature", SETTINGS.temperature, None),
            ("max_tokens", SETTINGS.max_tokens, None),
            ("reasoning_effort", SETTINGS.reasoning_effort, None),
        ):
            was_present = field in obj
            if obj.get(field) != value:
                obj[field] = value
                changed = True
            injected[field] = {"was_present": was_present, "final": value}

    messages = obj.get("messages") or []
    msg_count = len(messages) if isinstance(messages, list) else 0

    recorded = {k: v for k, v in obj.items() if k != "messages"}
    recorded["_injected"] = injected
    recorded["_body_modified"] = changed

    new_body = json.dumps(obj).encode() if changed else raw_body
    return new_body, recorded, msg_count


def _parse_sse_events(text_buf: str):
    """Yield (event_str, remaining_buffer). Splits on blank-line event boundaries."""
    while "\n\n" in text_buf:
        event, text_buf = text_buf.split("\n\n", 1)
        yield event, text_buf


async def proxy_endpoint(request: Request):
    path_provider = request.path_params["provider"]
    run_id = request.path_params["run_id"]
    rnd = request.path_params["round"]
    rest = request.path_params["rest"]  # e.g. "chat/completions"

    provider = PROVIDERS.get(path_provider)
    if provider is None:
        return JSONResponse({"error": f"unknown provider {path_provider}"}, status_code=404)
    api_key = provider.api_key()
    if not api_key:
        return JSONResponse(
            {"error": f"missing API key env {provider.api_key_env}"}, status_code=500
        )

    call_index = await _next_call_index(path_provider, run_id, rnd)
    raw_body = await request.body()
    new_body, recorded_params, msg_count = _modify_body(raw_body)

    body_capture_path = None
    if run_id in CAPTURE_RUN_IDS and f"/v1/{rest}".endswith("chat/completions"):
        bdir = os.path.join(RUNS_DIR, run_id, "bodies")
        os.makedirs(bdir, exist_ok=True)
        body_capture_path = os.path.join(bdir, f"{rnd}_{call_index:04d}.json")
        try:
            with open(body_capture_path, "wb") as bf:
                bf.write(new_body)
        except Exception:
            body_capture_path = None

    # Forward headers unchanged except the ones the proxy owns.
    fwd_headers = {
        k: v for k, v in request.headers.items() if k.lower() not in _DROP_REQ_HEADERS
    }
    fwd_headers["Authorization"] = f"Bearer {api_key}"

    url = f"{provider.origin}/v1/{rest}"
    if request.url.query:
        url += f"?{request.url.query}"

    # Header provenance (names only; values ONLY for non-auth session headers, so
    # we can confirm cache-affinity behavior without ever logging a secret).
    incoming_names = sorted({k.lower() for k in request.headers.keys()})
    forwarded_names = sorted({k.lower() for k in fwd_headers.keys()})
    _SESSION_HINTS = ("session", "affinity", "x-request-id", "x-conversation")
    session_headers = {
        k.lower(): v for k, v in request.headers.items()
        if any(h in k.lower() for h in _SESSION_HINTS) and k.lower() != "authorization"
    }

    rec = {
        "run_id": run_id,
        "round": rnd,
        "call_index": call_index,
        "provider": path_provider,
        "model": recorded_params.get("model") or provider.model,
        "path": f"/v1/{rest}",
        "request_params": recorded_params,
        "message_count": msg_count,
        "request_bytes": len(new_body),
        "request_header_names": incoming_names,
        "forwarded_header_names": forwarded_names,
        "session_headers": session_headers,
        "wall_request_start": _now_wall(),
    }

    t_start = time.monotonic()
    rec["mono_request_start"] = t_start

    try:
        cm = _client.stream(
            request.method, url, headers=fwd_headers, content=new_body
        )
        resp = await cm.__aenter__()
    except Exception as e:  # upstream connection error
        rec.update({
            "http_status": None,
            "error_body": f"proxy upstream error: {type(e).__name__}: {e}",
            "mono_request_end": time.monotonic(),
            "wall_request_end": _now_wall(),
        })
        await _write_record(rec)
        return JSONResponse({"error": str(e)}, status_code=502)

    status = resp.status_code
    resp_ct = resp.headers.get("content-type", "")
    rate_headers = _rate_limit_headers(resp.headers)

    state = {
        "first_byte": None,
        "first_token": None,
        "last_token": None,
        # split content vs reasoning deltas so decode-speed can be validated
        "first_content": None, "last_content": None, "n_content": 0,
        "first_reasoning": None, "last_reasoning": None, "n_reasoning": 0,
        "usage_raw": None,
        "buf": "",
        "err_bytes": bytearray(),
        "is_error": status >= 400,
        "content_encoding": resp.headers.get("content-encoding", ""),
    }

    def _handle_event(event: str):
        for line in event.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if data == "[DONE]" or not data:
                continue
            try:
                obj = json.loads(data)
            except Exception:
                continue
            u = obj.get("usage")
            if isinstance(u, dict):
                state["usage_raw"] = u
            choices = obj.get("choices") or []
            if choices:
                delta = choices[0].get("delta") or {}
                c = delta.get("content") or ""
                rc = delta.get("reasoning_content") or ""
                now = time.monotonic()
                if c:
                    state["n_content"] += 1
                    if state["first_content"] is None:
                        state["first_content"] = now
                    state["last_content"] = now
                if rc:
                    state["n_reasoning"] += 1
                    if state["first_reasoning"] is None:
                        state["first_reasoning"] = now
                    state["last_reasoning"] = now
                if c or rc:
                    if state["first_token"] is None:
                        state["first_token"] = now
                    state["last_token"] = now

    async def body_iter():
        try:
            async for chunk in resp.aiter_raw():
                if state["first_byte"] is None:
                    state["first_byte"] = time.monotonic()
                yield chunk  # untouched passthrough

                if state["is_error"]:
                    if len(state["err_bytes"]) < 8192:
                        state["err_bytes"].extend(chunk)
                    continue
                # tee a copy for parsing (does not affect the forwarded bytes)
                try:
                    text = chunk.decode("utf-8", errors="ignore")
                except Exception:
                    text = ""
                if "event-stream" in resp_ct:
                    state["buf"] += text
                    for event, remaining in _parse_sse_events(state["buf"]):
                        state["buf"] = remaining
                        _handle_event(event)
                else:
                    state["buf"] += text
        finally:
            await cm.__aexit__(None, None, None)
            await _finalize(rec, state, status, rate_headers, provider, t_start, resp_ct)

    out_headers = {
        k: v for k, v in resp.headers.items() if k.lower() not in _DROP_RESP_HEADERS
    }
    return StreamingResponse(
        body_iter(), status_code=status, headers=out_headers,
        media_type=resp_ct or None,
    )


async def _finalize(rec, state, status, rate_headers, provider, t_start, resp_ct):
    # Non-streaming JSON: parse usage from the whole accumulated body.
    if not state["is_error"] and "event-stream" not in resp_ct and state["buf"].strip():
        try:
            obj = json.loads(state["buf"])
            if isinstance(obj, dict) and isinstance(obj.get("usage"), dict):
                state["usage_raw"] = obj["usage"]
                # a non-streamed completion has no per-token timing
        except Exception:
            pass

    usage_raw = state["usage_raw"]
    usage = parse_usage(usage_raw)
    cost = provider.cost(usage.uncached_tokens, usage.cached_tokens, usage.output_tokens)
    # Provider's own reported cost, if any (Coral returns `cost`), so we can
    # cross-check our public-price computation against the account's billed cost.
    native_cost = usage_raw.get("cost") if isinstance(usage_raw, dict) else None

    end = time.monotonic()
    rec.update({
        "http_status": status,
        "error_body": (
            bytes(state["err_bytes"]).decode("utf-8", errors="ignore")
            if state["is_error"] else None
        ),
        "content_encoding": state["content_encoding"],
        "rate_limit_headers": rate_headers,
        "mono_first_byte": state["first_byte"],
        "mono_first_token": state["first_token"],
        "mono_last_token": state["last_token"],
        "mono_request_end": end,
        "wall_request_end": _now_wall(),
        "ttft_s": (state["first_token"] - t_start) if state["first_token"] else None,
        "decode_s": (
            (state["last_token"] - state["first_token"])
            if (state["first_token"] and state["last_token"]) else None
        ),
        # split diagnostics: does the provider stream reasoning deltas, and how
        # many content vs reasoning chunks, so decode tok/s can be validated.
        "n_content_deltas": state["n_content"],
        "n_reasoning_deltas": state["n_reasoning"],
        "content_decode_s": (
            (state["last_content"] - state["first_content"])
            if (state["first_content"] and state["last_content"]) else None
        ),
        "reasoning_decode_s": (
            (state["last_reasoning"] - state["first_reasoning"])
            if (state["first_reasoning"] and state["last_reasoning"]) else None
        ),
        "ttft_content_s": (state["first_content"] - t_start) if state["first_content"] else None,
        "usage_raw": usage_raw,
        "usage_parsed": usage.to_dict(),
        "uncached_tokens": usage.uncached_tokens,
        "cached_tokens": usage.cached_tokens,
        "output_tokens": usage.output_tokens,
        "reasoning_tokens": usage.reasoning_tokens,
        "prediction_tokens": _prediction_tokens(usage_raw),
        "cost_usd_public": cost,
        "provider_native_cost_usd": native_cost,
    })
    await _write_record(rec)


async def _write_record(rec: dict):
    os.makedirs(RAW_DIR, exist_ok=True)
    line = json.dumps(rec, default=str)
    async with _write_lock:
        with open(CALLS_PATH, "a") as f:
            f.write(line + "\n")


async def health(_request: Request):
    return JSONResponse({"ok": True, "providers": list(PROVIDERS), "inject_params": INJECT_PARAMS})


app = Starlette(routes=[
    Route("/healthz", health, methods=["GET"]),
    Route(
        "/{provider}/{run_id}/{round}/v1/{rest:path}",
        proxy_endpoint,
        methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    ),
])


def main():
    import uvicorn

    host = os.environ.get("PROXY_HOST", "127.0.0.1")
    port = int(os.environ.get("PROXY_PORT", "8787"))
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
