"""Start/stop the recording proxy as a child process, shared by preflight + pilot."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

import httpx

from harness.common import PROXY_HOST, PROXY_PORT, ROOT


class ProxyManager:
    def __init__(self, host: str = PROXY_HOST, port: int = PROXY_PORT):
        self.host = host
        self.port = port
        self.proc: subprocess.Popen | None = None

    def __enter__(self) -> "ProxyManager":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    @property
    def health_url(self) -> str:
        return f"http://{self.host}:{self.port}/healthz"

    def start(self, wait_s: float = 20.0) -> None:
        env = {**os.environ, "PROXY_HOST": self.host, "PROXY_PORT": str(self.port)}
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "proxy.server"],
            cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            try:
                r = httpx.get(self.health_url, timeout=2.0)
                if r.status_code == 200:
                    return
            except Exception:
                pass
            time.sleep(0.2)
        self.stop()
        raise RuntimeError(f"proxy failed to come up on {self.host}:{self.port}")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
            except Exception:
                self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except Exception:
                try:
                    os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
                except Exception:
                    self.proc.kill()
        self.proc = None
