# PREREGISTRATION — main experiment

**Status:** pre-registered, **NOT yet run**. Written 2026-11-04, after the n=3
pilot + diagnostics (see DECISION_LOG.md). Nothing in this file has been executed.

This locks the design, metrics, and analysis BEFORE running, so the main
experiment can't be reverse-rationalized from its results.

## Question & hypotheses
Does CoralBricks' fast inference help a real coding agent reach **ACCEPTED** work
*faster* and/or *cheaper* than Baseten, holding model, harness, task, and settings
fixed? Only the inference provider changes.

- **H1 (time):** difference in **time per accepted result** between arms.
- **H2 (cost):** difference in **cost per accepted result** (public prices).

Direction is NOT assumed. The pilot (n=3, not powered) hinted Baseten faster to
accept, Coral ~4× cheaper via cache — the main experiment tests whether those
hold with CIs. Pilot results do not constrain H1/H2 here.

## Arms
- **A. coral-fast** — `deepseek-v4.1-flash-fast` @ CoralBricks.
- **B. baseten-1017-fast** — `deepseek-ai/DeepSeek-V4.1-Flash-1017-Fast` @ Baseten
  (confirmed Oct-17 snapshot).
- **C. baseten-sept-fast (CONDITIONAL)** — `deepseek-ai/DeepSeek-V4.1-Flash-Fast`
  (Sept snapshot). Run **only if** Coral's served snapshot turns out to be the
  September build (snapshot match currently UNRESOLVED). Pre-wired as
  `[providers.baseten_sept]` (`enabled=false`) in config/providers.toml.

Snapshot match is a pre-registered confound: if unresolved at run time, report B
vs A as primary and treat C as an exploratory snapshot-sensitivity arm.

## Design
- **10 runs per arm** (vs 3 in the pilot), interleaved in **randomized C/B pairs**,
  **strictly sequential, never concurrent**. Set `RUNS_PER_ARM=10`.
- Order from a **recorded seed** (`PILOT_SEED`); `pilot_plan.json` stores seed +
  order + timestamps. The **pair index is the time slot** used for paired analysis
  (both arms run adjacently, so time-of-day / load effects cancel within a pair).
- Fresh starter copy per run → `git init` → OpenCode on `ticket.md` → commit →
  deterministic judge → feedback into the SAME session (`--continue`) next round.

## Fixed conditions (identical both arms)
- Settings injected identically by the proxy: `temperature=0`, `max_tokens=8192`,
  `reasoning_effort=high` (recorded per call).
- **Harness: OpenCode 1.19.5** (pinned). Sends a stable `x-session-affinity` /
  session id that is **constant across `--continue` rounds within a run** (verified
  in diagnostics) → fair cache affinity on both arms. `$PWD` set to the workspace
  (project-root fix). `stdin=/dev/null`.
- Deterministic judge: 7 prioritized hidden checks; ACCEPTED iff all pass.
- Recording proxy on the path for every call.

## Caps & spend guard
- 6 rounds/run, 10 min/round, 25 min/run (unchanged).
- Each run ends with exactly one outcome: accepted | round_cap | timeout |
  agent_error | provider_error. Failed runs are **recorded, never dropped**.
- **Spend guard raised to $12** (`SPEND_CAP_USD`, was $5). If arm C is enabled,
  raise to **$18**. The guard stops the experiment before the next run if
  cumulative public-price spend would exceed the cap.

## Metrics
**Primary (pre-specified):**
1. **time per accepted result** = (Σ wall time over ALL that arm's runs) ÷ (#accepted).
2. **cost per accepted result** = (Σ cost over ALL runs) ÷ (#accepted), at public
   list prices with the cache discount on response `cached_tokens` (billing-verified
   for both arms in the pilot).

**Secondary (pre-specified):** rounds to accept; calls per run; model time per call
(p50/p95); cache hit rate (measured); **controlled-probe decode tok/s**.

**Decode measurement (locked):** decode speed is measured by the **controlled
long-generation probe** (`reasoning_effort=none`, ~750 output tokens, ≥5 reps/arm),
NOT by per-call pilot decode — the latter is reasoning-contaminated and unreliable
(diagnostics §1). Report probe decode p50 with CIs.

## Analysis (pre-specified)
- **Bootstrap 95% CIs by resampling runs**, paired by time slot: for each of 10,000
  bootstrap iterations, resample the 10 **pairs** with replacement (keeping each
  pair's A and B together), recompute each arm's primary metric and the **paired
  A−B difference**; report the 2.5/97.5 percentiles.
- Report CIs for each arm's primary metrics AND for the A−B (and, if enabled, C−B)
  differences. **No NHST / p-values** — CIs + effect sizes only.
- Acceptance is binary (all 7 checks). Report acceptance counts per arm; if an arm
  has 0 accepts, cost/time-per-accepted is undefined (reported as such, not 0).
- Non-accepted outcomes (timeout/round_cap/agent_error/provider_error) reported in
  a breakdown table; `provider_error` runs excluded from time/cost-per-accepted but
  counted in a sensitivity re-analysis.
- All numbers regenerated purely from `results/raw/` by `analyze.py` (+ the
  diagnostics + bootstrap module). No hand-entered values.

## Budget estimate (from recorded pilot per-run cost, public prices)
| arm | pilot mean $/run | 10 runs | notes |
|---|---|---|---|
| coral-fast | 0.0455 | ~$0.46 | high cache hit → cheap |
| baseten-1017-fast | 0.1987 | ~$1.99 | lower cache hit + higher prices |
| **subtotal A+B** | | **~$2.44** | |
| + preflight + controlled probes + diagnostics | | ~$0.5 | |
| + ~50% margin (runs may hit more rounds) | | | |
| **planned guard (A+B)** | | **$12** | comfortable headroom |
| conditional C (sept-fast) | ~0.1987 | ~$1.99 | only if Coral=Sept snapshot |
| **guard if C enabled** | | **$18** | |

## Carried-over confounds / limitations
Snapshot match (A vs B) unresolved; Coral FP4 vs Baseten undisclosed precision;
single task (payments idempotency); single machine/region; public pricing (billing-
verified equal to account billing in the pilot). These are reported in SUMMARY.

## What would change the conclusion
- If the A−B CI for time-per-accepted includes 0 → "no detectable difference at
  n=10/arm."
- If cost-per-accepted CIs separate cleanly → report the cache-driven cost gap as
  the robust finding (pilot: ~67% token/cache, ~33% price).
- If snapshot resolves to September → arm C becomes primary vs A.
