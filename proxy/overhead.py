"""Measure proxy overhead: N identical tiny requests direct vs through the proxy.

Uses the offline mock upstream so the measurement isolates proxy cost (no network
jitter, no spend). Reports per-request latency stats and writes
results/raw/proxy_overhead.json.
"""

from __future__ import annotations

import json
import os
import statistics
import threading
import time

import httpx
import uvicorn

from proxy import server
from proxy.config import ROOT
from proxy.mock_upstream import app as mock_app

N = 20
MOCK_PORT = 8799
PROXY_PORT = 8798


def _serve(app, port) -> uvicorn.Server:
    cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    srv = uvicorn.Server(cfg)
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    while not srv.started:
        time.sleep(0.02)
    return srv


def _tiny_payload():
    return {
        "model": "mock-model",
        "messages": [{"role": "user", "content": "ping"}],
        "stream": True,
    }


def _time_request(client: httpx.Client, url: str) -> float:
    t0 = time.perf_counter()
    with client.stream("POST", url, json=_tiny_payload()) as r:
        for _ in r.iter_raw():
            pass
    return time.perf_counter() - t0


def main() -> int:
    # Point the proxy's "coral" provider at the mock and give it a dummy key.
    os.environ.setdefault("CORALBRICKS_API_KEY", "mock-key")
    server.PROVIDERS["coral"].origin = f"http://127.0.0.1:{MOCK_PORT}"
    server.PROVIDERS["coral"].api_key_env = "CORALBRICKS_API_KEY"

    mock_srv = _serve(mock_app, MOCK_PORT)
    proxy_srv = _serve(server.app, PROXY_PORT)
    try:
        direct_url = f"http://127.0.0.1:{MOCK_PORT}/v1/chat/completions"
        proxy_url = f"http://127.0.0.1:{PROXY_PORT}/coral/overhead/0/v1/chat/completions"

        with httpx.Client(timeout=30) as client:
            # warmup
            _time_request(client, direct_url)
            _time_request(client, proxy_url)

            direct = [_time_request(client, direct_url) for _ in range(N)]
            through = [_time_request(client, proxy_url) for _ in range(N)]
    finally:
        mock_srv.should_exit = True
        proxy_srv.should_exit = True
        time.sleep(0.3)

    def ms(xs):
        return [round(x * 1000, 3) for x in xs]

    result = {
        "n": N,
        "direct_ms": {
            "median": round(statistics.median(direct) * 1000, 3),
            "mean": round(statistics.mean(direct) * 1000, 3),
            "min": round(min(direct) * 1000, 3),
            "max": round(max(direct) * 1000, 3),
        },
        "proxy_ms": {
            "median": round(statistics.median(through) * 1000, 3),
            "mean": round(statistics.mean(through) * 1000, 3),
            "min": round(min(through) * 1000, 3),
            "max": round(max(through) * 1000, 3),
        },
        "overhead_ms_median": round(
            (statistics.median(through) - statistics.median(direct)) * 1000, 3
        ),
        "raw_direct_ms": ms(direct),
        "raw_proxy_ms": ms(through),
    }

    out_dir = os.path.join(ROOT, "results", "raw")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "proxy_overhead.json"), "w") as f:
        json.dump(result, f, indent=2)

    print(json.dumps({k: v for k, v in result.items()
                      if k not in ("raw_direct_ms", "raw_proxy_ms")}, indent=2))
    print(f"\nWrote results/raw/proxy_overhead.json "
          f"(median proxy overhead: {result['overhead_ms_median']} ms over {N} tiny requests)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
