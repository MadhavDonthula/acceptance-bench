# Accepted-Work Benchmark — CoralBricks vs Baseten

**Question:** does fast inference reach *ACCEPTED* coding work faster/cheaper? Same model (DeepSeek V4.1 Flash Fast), same harness (OpenCode 1.19.5), same task (payments idempotency), same settings (temp 0 / max_tokens 8192 / reasoning_effort high). Only the provider changes. Acceptance = all 7 hidden tests pass.

**Design:** pre-registered; n=10 runs/arm × 3 arms, interleaved in randomized triples, sequential; deterministic judge; cost at public list prices (billing-verified); paired bootstrap 95% CIs (resampling time-slot triples).

## TL;DR

- **Baseten reaches accepted ~11s faster** and decodes **~1.66× faster** (controlled probe).
- **CoralBricks is ~3.6× cheaper per accepted result**, driven by a 96% cache hit rate.
- Token throughput and *time/cost to accepted work* point in opposite directions — a single tokens/sec number hides this.

## Per-provider results (n=10/arm)

| Provider | Time/accepted (95% CI) | Cost/accepted (95% CI) | Cache hit | Decode (probe) | Accepted |
|---|---|---|---|---|---|
| CoralBricks Fast | 41.3s [38.6, 44.5] | $0.0440 [$0.0396, $0.0491] | 95.9% | 389 tok/s | 10/10 |
| Baseten V4.1-Flash-1017-Fast | 30.5s [28.2, 33.3] | $0.1592 [$0.1347, $0.1882] | 72.3% | 646 tok/s | 10/10 |
| Baseten V4.1-Flash-Fast (Sept) | 29.6s [26.2, 32.9] | $0.1480 [$0.1300, $0.1700] | 70.6% | — | 10/10 |

## Paired differences (CI excluding 0 ⇒ robust at this n)

- **CoralBricks − Baseten-1017 (serving-stack gap):** Δtime/accepted [8.4, 13.5]s (**excludes 0**); Δcost/accepted [$-0.1445, $-0.0892] (**excludes 0**).
- **Baseten-1017 − Baseten-Sept (snapshot):** Δtime/accepted [-2.7, 4.0]s (includes 0); Δcost/accepted [$-0.0202, $0.0408] (includes 0). → snapshot is not the driver.

## Trace replay — identical request sequences (inference isolated from agent behavior)

| Provider | Model time/session | TTFT (content) | Cache hit | Cost/session |
|---|---|---|---|---|
| CoralBricks Fast | 39.2s | 0.953s | 99.5% | $0.0289 |
| Baseten V4.1-Flash-1017-Fast | 26.1s | 0.473s | 68.5% | $0.1953 |
| Baseten V4.1-Flash-Fast (Sept) | 23.4s | 0.440s | 77.7% | $0.1477 |

With agent behavior held fixed, the speed/cost tradeoff persists → it is inference-level, not just "Coral reasons more".

## Why the cost gap

- **65%** token/cache behavior (Baseten bills far more uncached input — lower cache hit); **35%** unit price. Even at Coral's prices, Baseten's tokens cost more.

## Method & limitations

- Local recording proxy on every call (overhead ~2.23 ms/call, both arms); identical settings injected both arms; deterministic mutation-tested judge.
- One task, one machine/region — CIs quantify run-to-run variance, not external validity.
- Coral serves an opaque snapshot (both Baseten snapshots run, no robust B−C difference); Coral FP4 vs Baseten undisclosed precision.
- All numbers regenerated from `results/raw/`; pre-registration committed before any main run.
