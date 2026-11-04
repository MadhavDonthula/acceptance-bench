"""A tiny OpenAI-compatible mock upstream for testing the proxy offline.

Emits a streaming chat-completion (SSE) with a usage block that mimics the
prompt_tokens_details.cached_tokens shape, so the proxy's usage parsing and
timing capture can be exercised without spending on a real provider.
"""

from __future__ import annotations

import json
import time

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route


async def chat_completions(request: Request):
    body = await request.json()
    streaming = bool(body.get("stream"))
    model = body.get("model", "mock-model")

    usage = {
        "prompt_tokens": 1000,
        "completion_tokens": 5,
        "total_tokens": 1005,
        "prompt_tokens_details": {"cached_tokens": 800},
        "completion_tokens_details": {"reasoning_tokens": 2},
    }

    if not streaming:
        return JSONResponse({
            "id": "cmpl_mock", "object": "chat.completion", "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"},
                         "finish_reason": "stop"}],
            "usage": usage,
        })

    def sse():
        chunks = ["He", "llo", " wo", "rld", "!"]
        for i, c in enumerate(chunks):
            d = {"id": "cmpl_mock", "object": "chat.completion.chunk", "model": model,
                 "choices": [{"index": 0, "delta": {"content": c}, "finish_reason": None}]}
            yield f"data: {json.dumps(d)}\n\n"
            time.sleep(0.001)
        final = {"id": "cmpl_mock", "object": "chat.completion.chunk", "model": model,
                 "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                 "usage": usage}
        yield f"data: {json.dumps(final)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream")


async def models(_request: Request):
    return JSONResponse({"object": "list", "data": [{"id": "mock-model", "object": "model"}]})


app = Starlette(routes=[
    Route("/v1/chat/completions", chat_completions, methods=["POST"]),
    Route("/v1/models", models, methods=["GET"]),
])
