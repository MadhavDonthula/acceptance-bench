# SERIES_NOTES — future episodes

Running notes on experiments deliberately **deferred** out of the current
episodes, with enough design captured to pick them up later. Nothing here has
been run.

---

## Episode: human-paced acceptance loop (DEFERRED from the caching extension)

**Why deferred:** the caching extension (Layer B) has a hard **$2.50** budget and
is meant to run unattended in the background. A human-paced loop idles for
minutes between agent turns to mimic a real reviewer, which multiplies wall-clock
time (and, if it holds large contexts warm, spend) well past that envelope. It was
split out rather than squeezed in.

**Question:** when a developer reviews between turns at a realistic cadence
(seconds-to-minutes idle, occasionally longer), does the provider's cache still
hold the session prefix — i.e. does the *measured* cache benefit survive real
human latency, not just back-to-back agent calls?

**Design sketch (Coral-only, to honor the current hard rule; add a Baseten arm
only if/when that rule is lifted):**
- Reuse the acceptance task + OpenCode harness unchanged, but insert an idle
  **review delay** between rounds drawn from a realistic distribution (e.g.
  lognormal, median ~90s, tail to ~15 min), instead of firing `--continue`
  immediately.
- Record, per round, the cached fraction and TTFT of the first call after the
  idle gap — the real-world analogue of Layer B **Test 1** (cache survival vs
  idle), but on the *actual* growing agent context rather than a synthetic 30k
  prefix.
- Cross the two: Layer B Test 1 gives the *survival curve* for a fixed prefix;
  this episode gives the *acceptance-loop impact* (does a cache miss after a long
  review add a user-visible stall to the next turn?).

**Metrics:** per-round post-idle cached fraction + TTFT; extra wall time
attributable to post-idle cache misses; whether time-to-ACCEPTED changes vs the
no-idle main experiment (idle time excluded from the model-time decomposition).

**Budget/guard:** needs its own budget line (likely > $2.50 because warm contexts
may be re-prefilled after long idles). Set a fresh spend cap and the same
429/5xx + measurement-bug stop rules. Pre-register before running.

**Link:** builds directly on `results/CACHE_SUBEXPERIMENT.md` Test 1 and
`results/CACHE_ANALYSIS.md` §1 (the miss-penalty slope converts a post-idle miss
into milliseconds of added TTFT).

---

## Episode: Baseten network-adjusted + active-cache comparison (BLOCKED by choice)

**Why deferred:** the current operator rule forbids **any** new Baseten call
(inference, `/v1/models`, usage/billing). So the Layer A network baseline and all
of Layer B are Coral-only, and every Baseten network-adjusted / active-cache
figure is **UNMEASURED by choice**.

**If the rule is lifted:** re-run Layer A §2 (20× `GET /v1/models`) against Baseten
for a matching RTT baseline, and replay the six Layer B cache tests against the two
Baseten snapshots, so the survival curve, fan-out behavior, branch/rollback, and
prefix-bust granularity can be compared across providers rather than asserted for
Coral alone.

---

## Episode: fan-out beyond the Coral 5-in-flight cap

**Why deferred:** the Coral account caps in-flight requests at **5** (discovered in
the Layer B smoke: an 8-way fan-out returned HTTP 429). Layer B tests 3 & 4 were
run at width 5 instead of the pre-registered 8.

**If account credit is added (lifting the cap):** re-run warm/cold fan-out at 8+
to see whether prefill-sharing and TTFT spread change with wider concurrency, and
find the concurrency at which the shared-prefix cache stops absorbing the fan-out.
