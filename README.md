# acceptance-bench

A small, **honest pilot** benchmark.

**Question:** does CoralBricks' fast inference help a real coding agent reach
**ACCEPTED** work *faster* than Baseten — not just emit tokens faster — holding
model, harness, task, and settings fixed? Only the inference provider changes.

> **PILOT, n=3 per arm. No statistical claims.** This produces real, recorded
> numbers from 3 runs per provider. It is not powered to prove a difference.

## How it works

- **Same model**: DeepSeek V4.1 Flash Fast. Coral serves `deepseek-v4.1-flash-fast`;
  Baseten serves a matching snapshot slug (see `config/providers.toml` / `.env`).
- **Same harness**: [OpenCode](https://github.com/sst/opencode) (`opencode-ai`),
  pinned in `package.json`, driven headlessly.
- **Same task**: `tasks/payments_idempotency/` — a custom FastAPI+SQLite service
  with a duplicate-charge bug. Not from the internet, so it can't be memorized.
- **Same settings**: temperature 0, max_tokens 8192, reasoning_effort "high",
  applied identically to both arms.
- **Deterministic judge**: 7 prioritized hidden tests. ACCEPTED only if all pass;
  otherwise the agent gets developer-style feedback for the first failing check.
- **Recording proxy**: a local streaming pass-through that every request flows
  through. It records timing (TTFT, decode), tokens (cached/uncached/output),
  cost at public list prices, and rate-limit headers — without altering the
  stream (one allowed tweak: `stream_options.include_usage=true`).

### Time decomposition

Wall time per run is split into **model time** (union of API call intervals),
**non-model agent time** (tool calls, planning), and **judge time**. This is the
core thing the benchmark adds over a single mixed wall-clock number.

## Layout

```
proxy/     recording streaming proxy + overhead probe + usage parser
harness/   OpenCode launcher, outer review loop, preflight
judge/     deterministic hidden-test judge + verify_task
tasks/payments_idempotency/  starter/ ticket.md hidden_tests/ reference_solution/
config/providers.toml        base URLs, model slugs, public prices + date
analyze.py                   builds results/SUMMARY.md from raw files only
results/   raw/ (jsonl)  preflight/  SUMMARY.md  charts/
```

## Running

```bash
cp .env.example .env     # fill in CORALBRICKS_API_KEY and BASETEN_API_KEY
make setup               # uv venv + deps + pinned opencode
make verify-task         # prove starter FAILS, reference PASSES
make overhead            # measure proxy overhead
make preflight           # key check, /v1/models, cache probe, rate limits, smoke
#   -> review preflight output and give go-ahead
make pilot               # 3 runs/provider, interleaved, sequential
make analyze             # build results/SUMMARY.md
```

## Hard rules honored

- **No fabricated numbers.** Every metric in the outputs comes from recorded
  data in `results/raw/`. Anything unmeasurable is called out in the summary.
- **Keys live only in `.env`** (gitignored); never printed or committed.
- **Spend guard**: the harness stops if estimated spend across both providers
  would exceed **$5**.
- **Decisions logged**: see `DECISION_LOG.md`.

See the limitations section in `results/SUMMARY.md` for confounds (snapshot match,
precision differences, single task/location, public vs account pricing, proxy
overhead).
