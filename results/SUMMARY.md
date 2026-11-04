# Pilot results — CoralBricks vs Baseten (ACCEPTED-work benchmark)
> ## ⚠️ PILOT, n=3 per arm. NO STATISTICAL CLAIMS.
> These are real, recorded numbers from a handful of runs. They are **not** powered to prove a difference between providers. Read them as a smoke test of the method, not as a verdict.

## No pilot data recorded yet
`results/raw/runs.jsonl` is empty — the pilot (`make pilot`) has not run, or was blocked at preflight. Everything below the method/limitations is therefore unmeasured and deliberately left blank rather than fabricated.

## Per-run table
_No runs recorded._

## Per-provider pilot summary
_No runs recorded._

## Time decomposition chart
_No chart — no run data._

## Proxy overhead (measured)
- 20 identical tiny requests, direct vs through the proxy (offline mock upstream).
- direct median **9.231 ms**, proxy median **11.465 ms** → **2.234 ms** median added latency per call.

## Preflight notes
_Preflight not run._

## Limitations (read before citing anything above)
- **n=3 per arm.** Pilot scale. No confidence intervals, no significance — do not treat any difference as real.
- **Snapshot match UNRESOLVED.** Coral serves `deepseek-v4.1-flash-fast` (opaque snapshot); Baseten was run on `(see .env BASETEN_MODEL)`. We could not confirm the two are the identical weights snapshot. This is a real confound: a decode-speed or quality gap could be a snapshot gap, not a serving-stack gap.
- **Precision differs / undisclosed.** Coral documents FP4 serving; Baseten's precision is undisclosed. Token-for-token quality may differ for reasons unrelated to speed.
- **One task, one location.** Single task (payments idempotency), single machine/region. TTFT and rate limits are location-sensitive.
- **Public pricing, not account pricing.** Cost uses published list prices for BOTH arms (see config/providers.toml + date). Coral may grandfather cheaper Fast pricing; that is deliberately NOT used.
- **Proxy overhead** (~see above) is added to every call on both arms equally.
- **Identical settings enforced by the proxy** (temp 0 / max_tokens 8192 / reasoning_effort high) injected identically on both arms; recorded per call in calls.jsonl.
- **Cross-process clocks:** model-time union uses the proxy's monotonic clock (consistent within a run); non-model agent time is a derived difference and inherits that assumption.
