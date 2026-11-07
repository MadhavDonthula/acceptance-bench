# PREREGISTRATION — CACHE SUBEXPERIMENT (Layer B)

**Status:** pre-registered, **committed BEFORE any Layer B call is made** so git
timestamps prove the design preceded the data. Written 2026-11-07.

This locks the Layer B design, rep counts, metrics, budget, and stop rules.

## Hard constraints (operator rules, non-negotiable)
- **CoralBricks ONLY.** Every Layer B API call goes to Coral
  (`deepseek-v4.1-flash-fast`). **ZERO Baseten calls** of any kind. All Layer B
  conclusions are therefore **Coral-only by choice** — any Baseten comparison is
  UNMEASURED, not failed.
- **Same proxy + same cost accounting.** Requests flow through the recording
  proxy (`proxy/server.py`); cost is computed by the same
  `ProviderCfg.cost(uncached, cached, output)` at public list prices as the main
  experiment. The experiment client sends a **dummy** Authorization; the proxy
  injects the real Coral key loaded ONLY from `~/code/acceptance-bench/.env`. The
  client never reads the secret.
- **`PROXY_INJECT_PARAMS=0`** for these probes (single-arm, so cross-arm fairness
  injection is irrelevant): each send sets `max_tokens=16`,
  `reasoning_effort="none"`, `temperature=0`. We are measuring *prefill/caching*,
  not generation, so output is deliberately minimal to protect the budget.
- **Dedicated run_ids** `cacheexp-<test>` so these calls are trivially excluded
  from the main experiment and from Layer C (which counts only pre-caching work).
  Calls are also recorded to `results/raw/cache_subexp_calls.jsonl`.

## Budget & stop rules
- **HARD budget: $2.50** (public prices). A running spend total is kept; before
  any expensive send the projected spend is checked and the run **stops and logs**
  if it would pass $2.50.
- **Stop and log** also on: repeated 429/5xx (≥3 in a window), or a result that
  contradicts the main experiment in a way that implies a measurement bug (e.g.
  Coral showing ~0% cache on an immediate identical resend — the main experiment
  measured ~96%).
- **No human-paced loop** this episode (would burn wall-clock budget); recorded
  as a future episode in `SERIES_NOTES.md`.

## Prompt construction
- Synthetic code-like text (ported from coral-probe `textgen.code_file`), seeded
  for reproducibility, with a per-logical-session **nonce** prepended so a "cold"
  prompt is genuinely unseen. Target token sizes are approximate; we **report the
  actual `prompt_tokens`** returned by the server, never the target.
- A one-shot calibration send at startup measures chars/token for this generator;
  its cost counts against the budget.

## Tests, rep counts, and metrics (all Coral-only)

**Test 1 — Review delay (cache survival vs idle).**
Unique ~30k-token prefix per (gap, rep). Prime (send once), idle for the gap,
re-send the identical prefix. Gaps: **10s, 60s, 300s, 900s, 1800s**; **3 reps per
gap** (15 logical sessions). **All gaps run in parallel**; no other test runs
during test 1's idle windows (shared-replica KV eviction would confound it).
*Metrics:* cached_tokens and cached fraction (cached/prompt) on the re-send; TTFT
of prime vs re-send. **Chart:** cached fraction vs idle time.

**Test 2 — Context growth (hit vs miss TTFT by size).**
Prefix sizes **~20k, ~100k, ~300k tokens**, **3 reps each**. For each: send once
(miss) then re-send identical (hit). *Metrics:* miss TTFT, hit TTFT, hit
cached-fraction, per size. 300k may exceed the context window; if a size 400s on
the first send, that size is recorded as `context_limit_exceeded` and its reps are
skipped (bounds spend).

**Test 3 — Warm fan-out.**
Prime a ~30k prefix (1 send), then **8 concurrent** requests sharing that exact
prefix. *Metrics:* per-request cache hit rate, TTFT spread (min/median/max).

**Test 4 — Cold fan-out.**
**8 concurrent identical** requests on a **NEW, unprimed** ~30k prefix (no prime).
*Metric:* do concurrent cold requests share one prefill (some hit) or each
prefill independently (all miss)? Report per-request cached fraction + TTFT.

**Test 5 — Branch / rollback.**
Build a **10-turn** conversation, **~3k tokens per turn**, each turn re-sending the
growing transcript (so the prefix is fully cached by turn 10). Then **branch from
turn 5** with a different user message. *Metric:* cached_tokens on the branch vs
the shared 5-turn prefix length (does the cache keep the shared prefix when the
tail diverges?).

**Test 6 — Prefix-bust position.**
On a cached ~30k prompt, change **one token** at **10%, 50%, 90%** of its length,
**3 reps each** (fresh base per rep). *Metrics:* cached_tokens vs bust position
(expectation: everything before the bust stays cached, everything at/after is
re-prefilled); check whether cache granularity is **256-token blocks** (cached
tokens should round to a block boundary near the bust). **Chart:** cached tokens
vs bust position.

## Analysis
- Descriptive, Coral-only. Report medians/spreads over the specified reps; no
  cross-provider claims. Charts for Test 1 (cached fraction vs idle) and Test 6
  (cached tokens vs bust position) as specified.
- Sanity cross-check against the main experiment: an immediate identical re-send
  must show a high cached fraction (main experiment: Coral ~96%); a large
  deviation triggers the measurement-bug stop rule.

## Output
`results/CACHE_SUBEXPERIMENT.md` (+ the two charts under `results/cache_charts/`),
raw calls in `results/raw/cache_subexp_calls.jsonl`. All conclusions labeled
Coral-only.
