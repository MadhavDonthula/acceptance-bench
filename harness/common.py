"""Shared harness helpers: paths, config, git, run-plan, opencode config."""

from __future__ import annotations

import json
import os
import random
import subprocess

from proxy.config import ROOT, load_providers, load_settings, price_date  # noqa: F401

RUNS_DIR = os.path.join(ROOT, "runs")
RESULTS_DIR = os.path.join(ROOT, "results")
RAW_DIR = os.path.join(RESULTS_DIR, "raw")
PREFLIGHT_DIR = os.path.join(RESULTS_DIR, "preflight")
CALLS_PATH = os.path.join(RAW_DIR, "calls.jsonl")
RUNS_JSONL = os.path.join(RAW_DIR, "runs.jsonl")
ROUNDS_JSONL = os.path.join(RAW_DIR, "rounds.jsonl")

TASK_DIR = os.path.join(ROOT, "tasks", "payments_idempotency")
STARTER_DIR = os.path.join(TASK_DIR, "starter")
TICKET_PATH = os.path.join(TASK_DIR, "ticket.md")

OPENCODE_BIN = os.path.join(ROOT, "node_modules", ".bin", "opencode")

PROXY_HOST = os.environ.get("PROXY_HOST", "127.0.0.1")
PROXY_PORT = int(os.environ.get("PROXY_PORT", "8787"))

# Caps (minutes/seconds)
MAX_ROUNDS = 6
MAX_ROUND_SECONDS = 10 * 60
MAX_RUN_SECONDS = 25 * 60
SPEND_CAP_USD = 5.0


def proxy_base_url(provider: str, run_id: str, rnd: int) -> str:
    return f"http://{PROXY_HOST}:{PROXY_PORT}/{provider}/{run_id}/{rnd}/v1"


def read_ticket() -> str:
    with open(TICKET_PATH) as f:
        return f.read().strip()


def opencode_config(provider: str, run_id: str, rnd: int) -> dict:
    """Per-run/round OpenCode config: one custom openai-compatible provider whose
    baseURL points at the recording proxy, edits+bash auto-approved."""
    providers = load_providers()
    cfg = providers[provider]
    model_key = cfg.model  # the API model name == the opencode model id key
    return {
        "$schema": "https://opencode.ai/config.json",
        "provider": {
            provider: {
                "npm": "@ai-sdk/openai-compatible",
                "name": f"{provider} (acceptance-bench proxy)",
                "options": {
                    "baseURL": proxy_base_url(provider, run_id, rnd),
                    # Dummy key; the proxy replaces Authorization from .env.
                    "apiKey": "acceptance-bench-proxy",
                },
                "models": {
                    model_key: {
                        "name": model_key,
                        # zeros: our cost is computed by the proxy at public prices
                        "cost": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0},
                        "limit": {"context": 131072, "output": 8192},
                    }
                },
            }
        },
        "permission": {"edit": "allow", "bash": "allow", "webfetch": "allow"},
        "model": f"{provider}/{model_key}",
    }


def write_opencode_config(path: str, provider: str, run_id: str, rnd: int) -> None:
    with open(path, "w") as f:
        json.dump(opencode_config(provider, run_id, rnd), f, indent=2)


def git(workspace: str, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=workspace, check=True,
        capture_output=True, text=True,
        env={**os.environ, "GIT_AUTHOR_NAME": "acceptance-bench",
             "GIT_AUTHOR_EMAIL": "bench@local", "GIT_COMMITTER_NAME": "acceptance-bench",
             "GIT_COMMITTER_EMAIL": "bench@local"},
    )


def git_init_commit(workspace: str, message: str) -> None:
    git(workspace, "init", "-q")
    git(workspace, "add", "-A")
    git(workspace, "commit", "-q", "-m", message)


def git_commit_all(workspace: str, message: str) -> None:
    git(workspace, "add", "-A")
    # allow empty: a round may change nothing
    git(workspace, "commit", "-q", "--allow-empty", "-m", message)


def build_run_plan(seed: int) -> list[str]:
    """3 runs per provider, interleaved in randomized pairs (sequential, never
    concurrent). Returns an ordered list of provider names, length 6."""
    rng = random.Random(seed)
    plan: list[str] = []
    for _ in range(3):  # three C/B pairs
        pair = ["coral", "baseten"]
        rng.shuffle(pair)
        plan.extend(pair)
    return plan
