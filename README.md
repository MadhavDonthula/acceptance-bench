# acceptance-bench

**Question.** Does CoralBricks' fast inference help a real coding agent reach *accepted* work faster, not just generate tokens faster?

Same model, same harness, same task, same settings. Only the inference provider changes. A run counts only when a deterministic judge accepts the agent's patch.

## Result (pre-registered, n=10 runs per arm, 30/30 accepted)

| Provider | Time per accepted (95% CI) | Cost per accepted (95% CI) | Cache hit | Decode (probe) |
|---|---|---|---|---|
| CoralBricks Fast | 41.3s [38.6, 44.5] | $0.0440 [$0.0396, $0.0491] | 95.9% | 389 tok/s |
| Baseten Fast, Oct snapshot (1017) | 30.5s [28.2, 33.3] | $0.1592 [$0.1347, $0.1882] | 72.3% | 646 tok/s |
| Baseten Fast, Sept snapshot | 29.6s [26.2, 32.9] | $0.1480 [$0.1300, $0.1700] | 70.6% | not measured |

- **Baseten reaches accepted work faster.** CoralBricks is 8.4 to 13.5s slower per accepted result (paired CI excludes 0). The gap is model time; tools, tests and judge take about 13s on both.
- **CoralBricks is about 3.6× cheaper per accepted result** ($0.089 to $0.145 less, CI excludes 0). 65% of that gap is cache behavior and 35% is list price.
- **The model snapshot is not the driver.** Baseten Oct vs Sept: both CIs include 0.
- **It holds with agent behavior fixed.** Trace replay of identical request sequences: Baseten still faster, CoralBricks still cheaper.
- **Faster tokens shrink on the way to accepted work.** 1.66× faster decode became 1.53× faster model time and only 1.35× faster time to accepted.

Full numbers and per-run tables: [`results/SUMMARY.md`](results/SUMMARY.md). Cache study: [`results/CACHE_REPORT_SHORT.md`](results/CACHE_REPORT_SHORT.md). Billing check: [`results/BILLING_RECONCILIATION.md`](results/BILLING_RECONCILIATION.md).

## Reproduce

### A. Verify the results without API keys (about 2 minutes, free)

Every number in `results/SUMMARY.md` is regenerated from the raw files committed in `results/raw/`.

```bash
make setup          # uv venv (Python 3.12) + deps + pinned OpenCode 1.19.5
make analyze        # rebuilds results/SUMMARY.md and results/charts/ from results/raw/
git diff results/SUMMARY.md   # should match apart from timestamps
make verify-task    # starter rejected, reference accepted, 6 mutants each fail their own check
make check4-sensitivity   # race check run 50x against the non-atomic mutant and the reference
make overhead       # proxy overhead against a local mock upstream
```

### B. Re-run the experiment (needs keys, spends money)

```bash
cp .env.example .env    # add CORALBRICKS_API_KEY and BASETEN_API_KEY
make preflight          # key check, models, cache probe, rate limits, OpenCode smoke
make main               # 3 arms x 10 runs, shuffled triples, sequential (about $3.60, ~20 min)
make replay             # trace replay of 6 captured sessions on all 3 arms (about $2.50)
make analyze            # rebuild SUMMARY.md from the new raw data
```

`make main` archives any previous `calls/runs/rounds.jsonl` into `results/raw/archive/` before starting. A spend guard stops the run if public-price spend would exceed `SPEND_CAP_USD` (default $18).

## How it works

- **Task.** [`tasks/payments_idempotency/`](tasks/payments_idempotency/): a FastAPI + SQLite payments service where client retries create duplicate charges. The agent gets a three-sentence [`ticket.md`](tasks/payments_idempotency/ticket.md). Written from scratch so it cannot be memorized.
- **Harness.** [OpenCode](https://github.com/sst/opencode) 1.19.5, pinned in `package.json`, run headless. Judge feedback goes back into the same session (`--continue`). Caps are 6 rounds and 25 minutes per run.
- **Judge.** Seven hidden checks in priority order ([`hidden_tests/test_hidden.py`](tasks/payments_idempotency/hidden_tests/test_hidden.py)). Accepted only if all pass; otherwise the first failing check's reviewer-style feedback ([`feedback.json`](tasks/payments_idempotency/hidden_tests/feedback.json)) is sent to the agent. No LLM judge. Mutation-tested: [`judge/mutants/`](judge/mutants/).
- **Recording proxy.** [`proxy/`](proxy/) sits between OpenCode and the provider. It forwards the stream untouched, forces identical settings on every call (temperature 0, max_tokens 8192, reasoning_effort high), and logs timing, tokens, cached tokens and cost per call. Overhead about 2.2 ms per call.
- **Metrics.** Time per accepted = total wall time of all runs ÷ accepted runs. Cost per accepted = total cost of all runs ÷ accepted runs, at public list prices dated 2026-11-03 ([`config/providers.toml`](config/providers.toml)), with the cache discount. Failed runs always count.
- **Statistics.** Paired bootstrap 95% CIs resampling the 10 time-slot triples.

## Pre-registration and decision log

- [`PREREGISTRATION.md`](PREREGISTRATION.md) was committed **before** the first main run. Check with `git log --format='%h %ad %s' --date=iso -- PREREGISTRATION.md` and compare with `wall_start` in `results/raw/runs.jsonl`.
- [`PREREGISTRATION_CACHE.md`](PREREGISTRATION_CACHE.md) was committed before the CoralBricks-only cache experiments.
- [`DECISION_LOG.md`](DECISION_LOG.md) is the full chronological log (observed, options, decision, effect, surprises), committed step by step. Git history is the proof of order, so it has not been squashed.

## Raw results

| File | Contents |
|---|---|
| `results/raw/runs.jsonl` | one row per run: provider, outcome, rounds, wall time, token and cost totals |
| `results/raw/rounds.jsonl` | one row per judge round: checks passed/failed, feedback sent, timings |
| `results/raw/calls.jsonl` | one row per model call: timing, tokens, cached tokens, cost, injected settings, header names (never values) |
| `results/raw/pilot_plan.json` | seed, run order, caps |
| `results/raw/trace_replay.jsonl` | per-session replay aggregates for each arm |
| `results/raw/proxy_overhead.json` | proxy overhead measurement |
| `results/raw/cache_*` | CoralBricks-only cache sub-experiment |
| `results/raw/archive/` | pilot data and the pre-cleaning snapshot of the main run, kept for audit |
| `results/preflight/` | preflight evidence: usage shapes, rate limits, billing check, cache deep-dive |

Not committed: `runs/` (per-run workspaces, OpenCode transcripts, captured request bodies) and `.env`.

## Layout

```
tasks/payments_idempotency/  ticket.md  starter/  hidden_tests/  reference_solution/
judge/        judge.py  hygiene.py  verify_task.py  check4_sensitivity.py  mutants/
proxy/        recording streaming proxy, usage parser, overhead probe
harness/      launcher, outer_loop, preflight, trace_replay, cache studies, billing, site builders
config/       providers.toml (base URLs, model slugs, public prices + date)
analyze.py    builds results/SUMMARY.md from results/raw/ only
results/      SUMMARY.md, cache reports, billing reconciliation, raw/, charts
site/         static results pages (index.html, caching.html)
```

## Limitations

One task, one model, one machine and region: the CIs cover run-to-run noise, not other workloads. CoralBricks serves an opaque snapshot (identical weights cannot be proven; two Baseten snapshots were run to bound this). CoralBricks serves FP4 while Baseten's precision is undisclosed. The cache sub-experiment is CoralBricks-only by design (no new Baseten calls). See the limitations section of [`results/SUMMARY.md`](results/SUMMARY.md).

## Keys

Keys are read only from `.env` (gitignored) and are never logged; the proxy records header names, not values.
