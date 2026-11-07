# BILLING_RECONCILIATION (Layer C — NO API calls)
> Pure offline reconciliation. Sums **proxy-recorded** cost + tokens per provider/model from `results/raw/calls.jsonl` and its archived snapshots, for runs **before** this caching work (the `cacheexp-*` Layer-B calls are excluded). Calls are **deduplicated** across snapshots by (provider, timestamp, run_id, round, call_index). **Replays bypassed the proxy**, so they are summed separately from `trace_replay.jsonl`. Public prices as of 2026-11-03.

## Baseten 1017-Fast  (`baseten` / `deepseek-ai/DeepSeek-V4.1-Flash-1017-Fast`)

| bucket | calls | uncached | cached | output | cost (public) |
|---|---|---|---|---|---|
| pilot + earlier/aborted (pre-main) | 286 | 2.318M | 6.210M | 101.0K | $1.7200 |
| main | 303 | 2.098M | 5.479M | 107.0K | $1.5920 |
| probes | 51 | 0.235M | 0.099M | 9.9K | $0.1663 |
| smoke/preflight | 10 | 0.058M | 0.000M | 0.3K | $0.0358 |
| stale (run-id'd, outside window) | 14 | 0.160M | 0.321M | 4.9K | $0.1123 |
| replay (direct, not via proxy) | 205 (6 sess) | 1.767M | 3.727M | 64.0K | $1.2658 |
| **our recorded total** | **869** | **6.636M** | **15.835M** | **287.1K** | **$4.8922** |
| **provider dashboard** | — | **6.730M** | **16.000M** | **287.6K** | **$4.95** |
| **unexplained remainder** | — | 0.094M | 0.165M | 0.5K | $0.06 |

_Our recorded spend is **99%** of the dashboard $4.95; remainder **$0.06**._

## Baseten Sept-Fast  (`baseten_sept` / `deepseek-ai/DeepSeek-V4.1-Flash-Fast`)

| bucket | calls | uncached | cached | output | cost (public) |
|---|---|---|---|---|---|
| pilot + earlier/aborted (pre-main) | 176 | 1.307M | 2.809M | 69.6K | $0.9904 |
| main | 282 | 1.989M | 4.778M | 91.4K | $1.4798 |
| replay (direct, not via proxy) | 205 (6 sess) | 1.228M | 4.265M | 73.0K | $0.9719 |
| **our recorded total** | **663** | **4.524M** | **11.852M** | **234.0K** | **$3.4421** |
| **provider dashboard** | — | **4.562M** | **12.236M** | **236.7K** | **$3.48** |
| **unexplained remainder** | — | 0.038M | 0.384M | 2.7K | $0.04 |

_Our recorded spend is **99%** of the dashboard $3.48; remainder **$0.04**._

## Coral  (`coral` / `deepseek-v4.1-flash-fast`)

| bucket | calls | uncached | cached | output | cost (public) |
|---|---|---|---|---|---|
| pilot + earlier/aborted (pre-main) | 241 | 0.229M | 5.007M | 94.1K | $0.3175 |
| main | 319 | 0.318M | 7.405M | 127.7K | $0.4396 |
| probes | 48 | 0.048M | 0.232M | 8.8K | $0.0395 |
| smoke/preflight | 10 | 0.017M | 0.041M | 0.3K | $0.0086 |
| replay (direct, not via proxy) | 205 (6 sess) | 0.031M | 5.462M | 72.3K | $0.1934 |
| **our recorded total** | **823** | **0.644M** | **18.146M** | **303.2K** | **$0.9987** |
| **provider dashboard** | — | **0.800M** | **23.500M** | **370.5K** | **$1.25** |
| **unexplained remainder** | — | 0.156M | 5.354M | 67.3K | $0.25 |

_Our recorded spend is **80%** of the dashboard $1.25; remainder **$0.25**._

## Where the remainder comes from (attribution)
The dashboard bills **every** call the account ever made; our proxy records only calls routed through it. Known un-recorded (or differently-counted) sources of the remainder, in rough order:

1. **Direct probes that bypassed the proxy.** Several investigation scripts called the providers directly (the Baseten cache deep-dive, affinity/multi-slug probes, billing checks) and the trace-replay (folded in above from `trace_replay.jsonl`). The non-replay direct probes are **not** in `calls.jsonl` at all.
2. **Prior use of the same account.** The Coral account was also used by the earlier `coral-probe` project; those tokens are on the Coral dashboard but were never seen by this harness — the single largest driver of Coral's remainder.
3. **Dashboard token semantics.** Providers may count cache-**write** tokens and failed/aborted requests toward dashboard totals that our per-call `usage` parsing attributes differently (we bill cached reads at the discounted rate; the dashboard may show them under a different column).
4. **Aggregation timing.** Dashboard subtotals settle with lag (observed in the main experiment's billing check), so a snapshot can lead or trail our recorded total.

_No fabricated numbers: every 'our recorded' value is summed from recorded call rows; the remainder is reported as-is, not forced to zero._
