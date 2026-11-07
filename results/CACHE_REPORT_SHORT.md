# CACHE_REPORT_SHORT — does caching help the acceptance loop?

A short synthesis of the caching extension. **Every number below is from recorded
data** (Layer A: offline analysis of the main experiment's `calls.jsonl`; Layer B:
Coral-only active probes; Layer C: offline billing reconciliation). Anything not
measured is marked **UNMEASURED**. New active experiments are **CoralBricks-only
by choice** (hard rule: zero Baseten calls), so Baseten appears only where it was
already recorded in the main experiment.

Sources: `CACHE_ANALYSIS.md` (A), `CACHE_SUBEXPERIMENT.md` (B),
`BILLING_RECONCILIATION.md` (C), `PREREGISTRATION_CACHE.md`.

---

## 1. Miss-penalty slopes (recorded main-run calls, Layer A)
TTFT (ms) regressed jointly on uncached + cached tokens, per provider; slope =
ms per 1,000 tokens, bootstrap 95% CI:

| provider | miss penalty (ms/1k **uncached**) | ms/1k **cached** |
|---|---|---|
| Coral | **25.0** [16.9, 28.9] | −1.93 [−4.06, 0.13] |
| Baseten 1017-Fast | **9.3** [7.8, 10.9] | 1.02 [0.12, 1.94] |
| Baseten Sept-Fast | **9.0** [7.3, 10.7] | 1.60 [0.22, 2.93] |

A cached token adds **≈0 ms** of TTFT on every arm; an uncached (freshly
prefilled) token adds **9–25 ms per 1k**. So a cache hit is essentially free in
latency, and a *miss* is what you pay for — Coral's per-token miss penalty is
actually the steepest, which makes its high hit rate (below) matter most.

## 2. Coral network-adjusted TTFT (Layer A, CORAL ONLY)
20× `GET /v1/models`: **median RTT ≈ 115 ms**. Coral recorded TTFT p50 = 270 ms →
**≈155 ms of server-side queue+prefill** after subtracting RTT.
**Baseten network-adjusted TTFT: UNMEASURED (by choice)** — the hard rule forbids
any new Baseten call, including `/v1/models`.

## 3. Coral review-delay survival curve (Layer B, Test 1)
Unique ~30k prefix, idle, re-send; mean cached fraction on the re-send:

| idle | 10s | 60s | 300s | 900s | 1800s |
|---|---|---|---|---|---|
| cached | 85% | 91% | 84% | 99% | 93% |

**Flat at 84–99% with no decay out to 30 minutes.** A human review pause between
agent turns does **not** cost a re-prefill on Coral. (Chart:
`cache_charts/cache_review_delay.png`.)

## 4. Warm & cold fan-out (Layer B, Tests 3–4)
- **Warm** (prime, then 5 concurrent sharing the prefix): **95% mean hit rate**,
  TTFT 0.29–0.83 s. A warmed prefix fully absorbs concurrent fan-out.
- **Cold** (5 concurrent identical on a NEW prefix): **3/5 shared a prefill**
  (cached 82–97%), 2 missed. Concurrent cold requests **partially in-flight
  dedup** — the first to prefill warms the rest, rather than all prefilling
  independently.
- _Fan-out width was 5, not the pre-registered 8: the Coral account caps in-flight
  requests at 5 (logged deviation)._

## 5. Branch / rollback (Layer B, Test 5)
10-turn conversation, then branch from turn 5 with a different message: the branch
reused **15,104 / 18,305** prompt tokens cached (83%) ≈ the shared 5-turn prefix
(15,243 tokens). **The cache keeps the common ancestor when a conversation
branches** — a rollback/retry re-prefills only the divergent tail, not the whole
history.

## 6. Prefix-bust position (Layer B, Test 6)
Change one token at 10% / 50% / 90% of a cached ~30k prompt:

| bust position | 10% | 50% | 90% |
|---|---|---|---|
| mean cached tokens | 0 | 8,192 | ~23,467 |

Cached counts are **exactly 256-aligned**, clustering at multiples of **8,192
(=32×256)**. Cached is **not** proportional to bust position: an edit at 10% drops
the cache to **0**, while 90% still keeps three 8,192 blocks. **An early edit
re-prefills far more than its position implies**, and the cache retains whole
256-aligned blocks before the first changed token. (Chart:
`cache_charts/cache_prefix_bust.png`.)

## 7. Billing reconciliation headline (Layer C, no API calls)
Proxy-recorded spend vs provider dashboards (public prices), after dedup across
snapshots and folding in direct replays:

| provider | our recorded | dashboard | match | remainder |
|---|---|---|---|---|
| Baseten 1017-Fast | $4.89 | $4.95 | **99%** | $0.06 |
| Baseten Sept-Fast | $3.44 | $3.48 | **99%** | $0.04 |
| Coral | $1.00 | $1.25 | **80%** | $0.25 |

The Baseten arms reconcile to **99%** — a strong check that the proxy's per-call
cost/token accounting matches real billing. Coral's looser 80% is **5.35M
unexplained cached tokens**, attributed to prior `coral-probe` use of the *same
Coral account* plus non-replay direct probes; reported as-is, never forced to zero.

## Miss anatomy (Layer A, recorded)
Among cacheable calls, Coral misses **12.5%** vs Baseten **~58%**, both
**eviction-dominated** (partial-tail, not cold-replica routing).

---

## Does caching help the acceptance loop — beyond cost?

**Yes, and the help is latency, not just dollars.** The acceptance loop re-sends a
large, slowly-growing context every turn, and the recorded data shows a cache hit
costs ≈0 ms of TTFT while a miss costs 9–25 ms per 1,000 uncached tokens — so on a
~30k-token agent prompt, caching is the difference between a sub-second first token
and a multi-second prefill (Layer B measured hit vs miss TTFT of 0.30s vs 0.74s at
20k, widening to 0.91s vs 4.62s at 300k). That benefit is **durable** (it survives
a 30-minute review pause), **branch-friendly** (a rollback keeps the shared
ancestor), and **concurrency-friendly** (a warmed prefix absorbs fan-out at 95%
hit). The two real fragilities for a coding agent are (a) an **early edit** near the
top of the prompt, which re-prefills far more than its position suggests because
the cache quantizes to 256-aligned blocks, and (b) Coral's comparatively **steep
per-token miss penalty**, which makes its high hit rate (Layer A: 12.5% miss vs
Baseten's ~58%) the thing actually buying the loop its speed. Net: for this
workload, caching helps the acceptance loop on **latency and cost together**, and
keeping edits append-only (not rewriting the prompt head) is how an agent keeps
that benefit. _All active conclusions here are Coral-only by choice; the equivalent
Baseten network-adjusted and active-cache behavior is **UNMEASURED**._
