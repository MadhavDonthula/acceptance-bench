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

## Arms (all three run, interleaved)
- **A. coral-fast** — `deepseek-v4.1-flash-fast` @ CoralBricks.
- **B. baseten-1017-fast** — `deepseek-ai/DeepSeek-V4.1-Flash-1017-Fast` @ Baseten
  (confirmed Oct-17 snapshot).
- **C. baseten-sept-fast** — `deepseek-ai/DeepSeek-V4.1-Flash-Fast` (Sept snapshot)
  @ Baseten. **Enabled unconditionally** (updated 2026-11-04). Wired as
  `[providers.baseten_sept]` in config/providers.toml.

**Why C is now unconditional:** the pilot showed Coral emitting **~38% more
reasoning tokens** than Baseten-1017 under *identical* settings (6,574 vs 4,778
per run), and reasoning volume drove ~15% of the model-time gap. That is exactly
the kind of behavioral difference a snapshot mismatch would produce, so the
snapshot confound is **material**, not hypothetical. Running B and C (two Baseten
snapshots on the same stack) directly measures the snapshot effect and lets us
tell a snapshot difference apart from a serving-stack difference. A vs B remains
primary; B vs C isolates the snapshot.

## Design
- **10 runs per arm** (vs 3 in the pilot) across **3 arms** = 30 runs, interleaved
  in **randomized A/B/C triples**, **strictly sequential, never concurrent**. Set
  `RUNS_PER_ARM=10 ARMS=coral,baseten,baseten_sept`.
- Order from a **recorded seed** (`PILOT_SEED`); `pilot_plan.json` stores seed +
  order + timestamps. The **triple index is the time slot** used for paired
  analysis (all three arms run adjacently, so time-of-day / load effects cancel
  within a triple).
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
- **Spend guard set to $18** (`SPEND_CAP_USD`, was $5 pilot → $12 two-arm → **$18
  three-arm**, now that C is unconditional and TRACE REPLAY is added). The guard
  stops the experiment before the next run if cumulative public-price spend would
  exceed the cap.

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
  bootstrap iterations, resample the 10 **triples** with replacement (keeping each
  triple's A, B, C together), recompute each arm's primary metric and the paired
  differences; report the 2.5/97.5 percentiles.
- Report CIs for each arm's primary metrics AND for **A−B (primary: stack effect)**
  and **B−C (snapshot effect)** differences. **No NHST / p-values** — CIs + effect
  sizes only.
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
| A coral-fast | 0.0455 | ~$0.46 | high cache hit → cheap |
| B baseten-1017-fast | 0.1987 | ~$1.99 | lower cache hit + higher prices |
| C baseten-sept-fast | ~0.1987 | ~$1.99 | assume ≈ B (same tier/prices) |
| **subtotal A+B+C (30 runs)** | | **~$4.44** | |
| + preflight + controlled probes + diagnostics | | ~$0.5 | |
| + TRACE REPLAY (see below) | | ~$2.5 | 6 sessions × 3 arms |
| + margin (runs may hit more rounds) | | ~$2 | |
| **spend guard** | | **$18** | covers 3 arms + trace replay |

## Secondary experiment: TRACE REPLAY (pre-registered)
**Purpose:** isolate *inference speed and caching* from *agent behavior*. The main
experiment confounds them — each arm's agent makes different calls (e.g. Coral
emits ~38% more reasoning), so wall/cost differences mix "the model/stack is
faster" with "the agent did different work." Trace replay fixes the request
sequence and varies only the provider.

**Capture (opt-in, during the main experiment):**
- Full request bodies (including `messages`) are captured for **3 arm-A (coral)
  runs and 3 arm-B (baseten-1017) runs** — the first 3 run_ids of each, recorded in
  `pilot_plan.json`.
- Bodies are written under `runs/<run_id>/bodies/` which is **gitignored and never
  committed** (they may contain task content; treat as potentially sensitive).
  Only non-body metadata (timings) goes to `results/raw/`.

**Replay protocol:**
- For each captured session (6 total), replay its **exact ordered request
  sequence** against **ALL three arms** (coral, baseten-1017, baseten-sept),
  reproducing the **original inter-call wall gaps** (from recorded timestamps).
- Each replay uses a **fresh session/affinity ID** (new uuid) so caching starts
  cold and is attributable to the provider, not carried state.
- Only the target arm's `model` is substituted; messages/params are the captured
  originals. Direct to the provider (no OpenCode), non-interactive.
- 6 sessions × 3 arms = **18 replays**.

**Metrics (per call & per session):** per-call TTFT, decode time (content-only),
cache hit rate, model time per session, cost per session (public prices). Reported
with the same paired-bootstrap CIs, resampling the 6 captured sessions.

**Budget:** each captured session ≈ the main run's token volume (~17k input +
~12k output). 18 replays ≈ 6 × (coral+1017+sept) ≈ ~$2.5 at public prices. Folded
into the $18 guard above.

**Interpretation:** if trace replay shows Baseten faster per identical session but
the main experiment shows a smaller end-to-end gap, the agent-behavior difference
(Coral's extra reasoning, more rounds) is offsetting raw inference speed — which is
the whole point of separating the two.

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
