# DECISION_LOG

Format per entry: **Observed / Options / Decision / Effect on next step / Surprises**.
Timestamps are UTC. This log is committed after each meaningful step so the git
history proves the order of decisions.

---

## 2026-11-03 — Seed entries (framing)

### Prior art: CoralBricks' Acceptance Loop page
- **Observed:** CoralBricks' "Acceptance Loop" page measures the path to accepted
  work, but it reports one run per task, has no baseline provider, leaves the
  model / harness / judge unspecified, and uses a wall-clock number that mixes
  model time with tool time.
- **Options:** (a) reproduce their single-run framing; (b) add a baseline,
  repeats, a time decomposition, and a deterministic judge.
- **Decision:** (b). Add a same-model baseline, repeat runs, decompose wall time
  into model / non-model agent / judge, and use a deterministic judge.
- **Effect on next step:** Drives the whole design: proxy for time + token
  decomposition, hidden-test judge, interleaved repeats.
- **Surprises:** none yet.

### Baseline choice
- **Observed:** Candidate baselines are Baseten Fast over DeepSeek first-party,
  OpenRouter, or a different model entirely.
- **Options:** Baseten Fast (same model, same tier) vs a cheaper/different target.
- **Decision:** Baseten Fast over the same DeepSeek snapshot. Reason: it is the
  strongest same-model, same-tier competitor, and Coral's own pricing card
  compares against it.
- **Effect on next step:** Must match the Baseten model slug to the snapshot
  Coral serves (see snapshot-match entry during preflight).
- **Surprises:** Risk acknowledged up front — Baseten may *decode* faster. The
  experiment is designed to report that honestly rather than hide it.

### Cache-hit assumption in Coral's comparison card
- **Observed:** Coral's comparison card assumes an equal 92% cache hit rate on
  both sides.
- **Options:** accept the assumption vs measure it.
- **Decision:** Measure actual per-call cache hit rates on both arms.
- **Effect on next step:** Proxy must parse and record cached-token fields for
  both providers; SUMMARY reports measured hit rates.
- **Surprises:** none yet.

### Baseten rate limits
- **Observed:** Baseten rate limits count cached and uncached input equally
  toward TPM. Documented defaults (Basic / unverified: 15 RPM / 100k TPM) would
  throttle a coding agent that re-sends large contexts.
- **Options:** run blind vs verify limits first.
- **Decision:** Verify the chosen slug's RPM/TPM in preflight before any pilot
  run; WARN loudly and ask the operator if TPM < 500,000.
- **Effect on next step:** Preflight step 4 queries the Baseten model_apis API.
- **Surprises:** none yet.

### Coral grandfathered pricing
- **Observed:** Coral grandfathers Fast pricing for orgs created before
  2026-10-16. The operator's account may be on that older pricing.
- **Options:** report the operator's actual price vs public list price.
- **Decision:** Report cost at public list prices for BOTH providers. The
  operator's grandfathered pricing is noted as a limitation and not used.
- **Effect on next step:** config/providers.toml carries public prices + date.
- **Surprises:** none yet.

### Harness choice
- **Observed:** Need a real developer coding agent that both providers support.
- **Options:** OpenCode, or a bespoke agent loop.
- **Decision:** OpenCode (npm `opencode-ai`). Both providers document it over
  Chat Completions, it is open source and version-pinnable, and it is a real
  developer tool rather than a toy loop.
- **Effect on next step:** Investigate OpenCode headless mode; pin the version.
- **Surprises:** none yet.

### Judge choice
- **Observed:** Need an acceptance signal that does not add noise.
- **Options:** LLM-as-judge vs deterministic hidden tests.
- **Decision:** Deterministic hidden tests with prioritized, developer-style
  feedback. No LLM judge, to remove judge noise and make ACCEPTED reproducible.
- **Effect on next step:** Build hidden_tests/ with 7 prioritized checks and a
  reference solution; prove starter FAILS and reference PASSES.
- **Surprises:** none yet.

---

## 2026-11-04 03:38 UTC — Step 1: scaffold

### Environment & toolchain
- **Observed:** No system opencode binary. Python 3.9 is the default `python3`,
  but Homebrew 3.11/3.12/3.13 and `uv 0.13.6` are present. `~/code/coral-probe`
  exists with a reusable `coralprobe/usage.py` (handles
  `prompt_tokens_details.cached_tokens` and other shapes). coral-probe's `.env`
  contains **only** `CORALBRICKS_API_KEY` — **no `BASETEN_API_KEY` anywhere**.
  CoralBricks `/v1/models` returns HTTP 200 (network OK).
- **Options:** (a) use system Python 3.9; (b) pin a modern Python via uv.
- **Decision:** Pin Python 3.12 via `uv venv`. Install OpenCode locally via
  `npm install` (pinned in package.json) rather than a global binary, so the
  harness version is recorded and isolated.
- **Effect on next step:** `make setup` builds the venv + installs opencode
  locally. Preflight will FAIL on the missing Baseten key — this is expected and
  will be surfaced to the operator before any pilot run (keys are the operator's
  to provide; they live only in `.env`).
- **Surprises:** No Baseten key is provisioned yet. The benchmark is built to be
  ready, but the Baseten arm cannot run until a key is added to `.env`.

### Copied usage parser (continued)
- **Observed:** `coralprobe/usage.py` already normalizes usage across Chat
  Completions / Responses / Anthropic / DeepSeek-native shapes.
- **Decision:** Copy it verbatim into `proxy/usage.py` to avoid drift.
- **Effect on next step:** Proxy uses it to parse cached/uncached/output/reasoning.
- **Surprises:** none.

---

## 2026-11-04 03:52 UTC — Step 2: task, reference, judge

### Task design
- **Observed:** Need a custom repo (not memorizable) where the starter FAILS and
  a reference PASSES. Built `tasks/payments_idempotency/`: FastAPI + SQLite
  charges/refunds service (~330 lines incl. tests), visible pytest suite (9
  tests, ~0.2s), bug = retries create duplicate charges.
- **Decision:** Drive all hidden checks at the HTTP boundary via Starlette
  TestClient, importing the same `payments.app:create_app` contract that the
  visible suite pins. This keeps the checks robust to internal refactors while
  staying fair (the agent must keep the visible suite green anyway).
- **Effect on next step:** Hidden tests are self-contained (own temp DB, own
  seeded customer); the judge runs them in an isolated throwaway copy.
- **Surprises:** none.

### Concurrency check determinism (check 4)
- **Observed:** SQLite under concurrent writers from TestClient's threadpool can
  hit "database is locked" and be nondeterministic/slow.
- **Options:** (a) rely on a UNIQUE constraint + catch IntegrityError;
  (b) serialize same-key requests with an in-process per-key lock.
- **Decision:** Reference uses a per-key `threading.Lock` (FastAPI sync endpoints
  share one process) so only one racing retry writes; the rest read the stored
  charge. A UNIQUE constraint on `idempotency_keys.key` + IntegrityError
  fallback is kept as defense-in-depth. Check 4 uses a `threading.Barrier` to
  release 10 requests together.
- **Effect on next step:** Concurrency check is deterministic and fast (~0.15s).
- **Surprises:** none.

### Verify result (proving the judge)
- **Observed:** `judge.verify_task`: STARTER rejected at check 2 (passes 1,5,7;
  fails 2,3,4,6), REFERENCE accepted (all 7 pass). Total judge runtime 1.21s
  (starter 0.66s + reference 0.54s), comfortably under the 10s budget.
- **Decision:** Judge accepted as the acceptance oracle. First-failing-check
  feedback is returned one problem per round, in priority order.
- **Effect on next step:** Build the recording proxy, then wire the outer loop
  to call this judge after each round.
- **Surprises:** The starter happens to PASS check 5 (no-key -> distinct
  charges) and check 7 (hygiene) as expected, so the only reason it's rejected
  is the missing idempotency + list endpoint — exactly the ticket's ask.

---

## 2026-11-04 04:05 UTC — Step 3: recording proxy

### Path routing
- **Observed:** OpenCode appends `/chat/completions` to its baseURL. With baseURL
  `.../{provider}/{run_id}/{round}/v1`, requests arrive as
  `/{provider}/{run_id}/{round}/v1/chat/completions`.
- **Decision:** Proxy strips the `{provider}/{run_id}/{round}` prefix and forwards
  to the provider ORIGIN (scheme+host from providers.toml) + the `/v1/...`
  remainder, preserving the query string. Headers forwarded unchanged except
  Authorization (set from .env), Host, and length/hop-by-hop headers.
- **Effect on next step:** OpenCode config just needs one baseURL per run/round.

### Untouched streaming + usage tee
- **Observed:** Need to stream bytes back untouched AND parse usage/timing.
- **Decision:** Forward `response.aiter_raw()` bytes verbatim; tee a decoded copy
  into an SSE parser that records first-byte, first-token, last-token, and the
  final `usage` block. Only allowed request mutations: add
  `stream_options.include_usage=true` if missing, and (PROXY_INJECT_PARAMS=1,
  default on) inject identical temperature=0 / max_tokens=8192 /
  reasoning_effort=high on both arms. Exactly what was sent is recorded under
  `request_params` (incl. `_injected` + `_body_modified`).
- **Effect on next step:** Decide in Step 4 whether OpenCode can set these itself;
  if so, flip PROXY_INJECT_PARAMS off. Either way both arms get identical values.
- **Surprises:** none.

### Offline validation + overhead
- **Observed:** Built `proxy/mock_upstream.py` (OpenAI-compatible SSE with
  cached-token usage). Ran `proxy/overhead.py`: 20 identical tiny streaming
  requests, direct vs through the proxy, against the mock (isolates proxy cost,
  no spend).
- **Result:** direct median 9.23 ms, proxy median 11.47 ms -> **~2.23 ms median
  proxy overhead** per call. Recorded call line verified: cached=800/uncached=200,
  cache_field=`prompt_tokens_details.cached_tokens`, cost=$0.0001062 (matches
  200·0.45+800·0.009+5·1.80 per-M), TTFT/decode captured, injection recorded.
- **Decision:** Proxy accepted. Overhead (~2 ms) is negligible vs multi-second
  agent rounds but is reported as a limitation anyway.
- **Effect on next step:** Wire OpenCode's baseURL at proxy; build outer loop.
- **Surprises:** none — overhead is tiny and parsing matched expectations.

---

## 2026-11-04 04:35 UTC — Step 4: OpenCode headless + harness

### OpenCode investigation (opencode-ai 0.4.45, pinned in package.json)
- **Observed (empirically, offline against a mock upstream):**
  - `opencode run "<msg>"` runs headlessly and exits — **but only if stdin is
    /dev/null**. With an open stdin it blocks forever after logging startup args
    (this cost real debugging time; confirmed the fix repeatably).
  - `--continue` continues the project's last session → genuine SAME-session
    follow-up (round 1 carried 4 messages vs round 0's 3, proving context carry).
  - Custom provider via `@ai-sdk/openai-compatible` + `options.baseURL` works;
    the Baseten slug with slashes (`deepseek-ai/DeepSeek-V4.1-Flash-1017-Fast`)
    routes correctly and is forwarded verbatim as the API `model`.
  - `permission: {edit, bash, webfetch: "allow"}` auto-approves — no prompts.
- **Instruction correction (logged per hard rule):** the prompt suggested
  `OPENCODE_CONFIG_CONTENT`. That env var does **not** exist in 0.4.45 — the
  binary exposes `OPENCODE_CONFIG` (a path) and `OPENCODE_PERMISSION`. Chosen
  faithful alternative: write a per-run `opencode.json` and point `OPENCODE_CONFIG`
  at it. Same effect (isolated per-run config, no global leak), different
  mechanism.
- **Cosmetic error:** against the mock, OpenCode prints a post-response
  `DecimalError: Invalid argument: [object Object]` (its internal cost calc on the
  mock's usage shape) yet still exits 0 and delivers content. Flagged to re-check
  in the real-provider smoke (preflight step 5); treated as non-fatal.
- **Decision:** Launch via subprocess with `stdin=DEVNULL`, own process group
  (clean timeout kill), per-run `OPENCODE_CONFIG`. Follow-ups via `--continue`
  (same-session requirement satisfied — no fallback needed).

### Model settings mechanism
- **Observed:** The proxy can already inject identical temperature/max_tokens/
  reasoning_effort on both arms and record them.
- **Decision:** Keep `PROXY_INJECT_PARAMS=1` as the source of truth so both arms
  are provably identical regardless of what OpenCode emits; recorded per call in
  `request_params._injected`. (Satisfies HARNESS point 4's fallback as the
  primary mechanism.)

### Harness structure
- Built `harness/common.py` (paths, per-run opencode config, git, run plan),
  `proxy_manager.py` (start/stop proxy child, health-gated), `launcher.py`
  (one round), `outer_loop.py` (pilot), `preflight.py`, and `analyze.py`.
- **Run plan:** 3 C/B pairs, each pair order shuffled from a recorded seed
  (`PILOT_SEED`, default 20261103 → `['baseten','coral']×3`), strictly sequential.
- **Outcomes:** accepted | round_cap | timeout | agent_error | provider_error,
  one per run; failed runs always written to runs.jsonl.
- **Spend guard:** cumulative `cost_usd_public` across calls.jsonl; pilot aborts
  before the next run if it exceeds $5.
- **analyze.py** degrades honestly: with no pilot data it writes the banner,
  method, measured proxy overhead, and limitations, and leaves every unmeasured
  metric blank (never 0) — verified by running it now.
- **Surprises:** The stdin-blocking behavior of `opencode run` was the single
  biggest gotcha; nothing in `--help` hints at it.

---

## 2026-11-04 04:45 UTC — Step 5: preflight (BLOCKED on credentials)

### Preflight result
- **Observed:** Ran `make preflight`. Step 1 (keys + GET /v1/models) FAILED and
  the preflight STOPPED as designed:
  - **coral:** `/v1/models` → **HTTP 401**. The only CoralBricks key on this
    machine is in `~/code/coral-probe/.env`, and it is literally the example
    placeholder `cb_your_key_here` (16 chars, `cb_` prefix) — never filled with a
    real key. So there is no valid Coral credential.
  - **baseten:** no `BASETEN_API_KEY` present anywhere.
- **Options:** (a) fabricate/stub numbers to "complete" the pilot — FORBIDDEN by
  the hard rules; (b) stop at preflight and report that real keys are required.
- **Decision:** (b). The benchmark is fully built and validated **offline** (task
  + judge proven; proxy recording + overhead measured; OpenCode headless loop,
  same-session follow-ups, auto-approve, and both-provider routing all exercised
  through the proxy against a mock). It cannot run against real providers until
  the operator puts valid `CORALBRICKS_API_KEY` and `BASETEN_API_KEY` in `.env`.
  No pilot numbers are produced; `analyze.py` emits the banner + method +
  measured proxy overhead + limitations with every unmeasured metric left blank.
- **Effect on next step:** Hand back to the operator. Once real keys are in
  `.env`, `make preflight` will run steps 2–5 (incl. the Baseten rate-limit check
  and the snapshot-match warning), then — on go-ahead — `make pilot`.
- **Snapshot-match (unresolved confound, pre-registered):** Baseten defaults to
  `deepseek-ai/DeepSeek-V4.1-Flash-1017-Fast` (the Oct-17 snapshot) per the spec's
  instruction when the match is unknown. Preflight prints the slug; the true
  weight-snapshot identity vs Coral's `deepseek-v4.1-flash-fast` cannot be
  confirmed from model listings and is logged as a confound in SUMMARY.
- **Surprises:** The Coral key that looked available was the unfilled example
  placeholder — so BOTH arms are blocked on credentials, not just Baseten.

---

## 2026-11-04 (hardening) — Tasks 3 & 4

### Task 4: removed "known bug" comments
- **Observed:** `starter/payments/charges.py` and `app.py` carried explicit
  "known bug / not idempotent" comments that telegraphed the task to the agent.
- **Decision:** Removed both docstring notes. The duplicate-charge behavior is
  now something the agent must discover from the ticket + code, not a labeled
  bug. Starter behavior is unchanged (visible suite still green).
- **Effect:** Fairer task; no hint leakage. Confirmed starter still REJECTED at
  check 2, reference still ACCEPTED.

### Task 3: check 7 is now AST-based
- **Observed:** The old check 7 hashed whole files (tests/test_payments.py +
  conftest.py), so it forbade ANY edit — including legitimately adding new tests.
- **Decision:** New policy (judge/hygiene.py): allow new test files and new test
  functions; FAIL only if any of the original 9 test functions is removed or
  changed, compared by normalized `ast.dump` (no attributes → whitespace/comment/
  formatting edits pass, logic edits fail), OR if requirements.txt gains a new
  third-party package. Baseline moved from `baseline_hashes.json` (sha256) to
  `baseline.json` (per-function AST dumps + allowed_requirements). Feedback[7]
  rewritten to match.
- **Verified:** reference → no problems; reformat + new test fn + new test file
  → allowed; changing `test_create_charge`'s asserted value → caught
  (`test_create_charge (changed)`). verify_task still green.
- **Surprises:** none.

---

## 2026-11-04 (hardening) — Task 1: mutation-testing the judge

- **Observed:** The judge was only validated against starter (fail) and reference
  (pass). A judge that silently accepted a subtly-broken solution would go
  undetected.
- **Decision:** Added `judge/mutants/` with one deliberately-flawed solution per
  behavioral check, generated from the reference by a single targeted edit each
  (`judge/mutants/build_mutants.py`, materialized dirs + a MUTANT.md note each):
  - mutant2: idempotency key ignored (always new charge).
  - mutant3: reused key + different body returns stored charge instead of 409.
  - mutant4: non-atomic SELECT-then-INSERT — per-key lock removed AND unique
    constraint on `idempotency_keys.key` removed.
  - mutant5: keyless requests share one key and wrongly collapse.
  - mutant6: list endpoint ordered oldest-first instead of newest-first.
  - mutant7: correct solution but rewrites an original visible test function
    (still passing, so only check 7 catches it).
  `verify_task` now REBUILDS the mutants from the reference (so they can't drift)
  and asserts each one's first-failing check equals its intended check.
- **Result:** All 6 mutants fail EXACTLY their intended check
  (first_failed == intended); starter REJECTED@2; reference ACCEPTED. Total
  verify runtime 5.4s.
- **Surprises:** mutant5 also trips check 6 (its shared keyless key causes a 409
  on the differing-body charges the list test creates) — harmless, since check 5
  has higher priority so the judge still reports exactly check 5.

---

## 2026-11-04 (hardening) — Task 2: check-4 sensitivity

- **Observed:** Check 4 (concurrency) is the only inherently nondeterministic
  check. Measured its sensitivity with `judge/check4_sensitivity.py` — 50 runs
  of ONLY check 4 against mutant4 (non-atomic, no lock, no unique constraint) and
  50 against the reference.
- **Result:**
  - mutant4: check 4 CAUGHT the race **50/50 = 100%** (never missed).
  - reference: check 4 PASSED **50/50** — no flakiness.
- **Decision:** Catch rate (100%) already exceeds the ~95% bar and the reference
  is flake-free, so NO strengthening was needed. Current check 4 (10 threads
  released together via a `threading.Barrier`, assert exactly one charge id) is
  sufficient. Kept the knobs documented in the spec (more threads / repeated
  bursts / monkeypatched delay) in reserve if a future environment proves less
  contended. Added `make check4-sensitivity`.
- **Surprises:** The non-atomic mutant never once sneaked past — on this machine
  the SELECT→INSERT window under 10 barrier-released threads is wide enough that
  duplicates occur every time.

---

## 2026-11-04 (preflight run) — credentials live; loader hardened

- **Observed:** Operator added real keys. Baseten validated immediately (200, 21
  models). First Coral key (34 chars) returned 401 `invalid_api_key` (Coral said
  mint/rotate; new keys take ~30s). After rotation, a hand-edited `.env` contained
  a stray `=` line (empty variable name), which crashed `load_dotenv` with
  `OSError: [Errno 22]` from `os.environ.setdefault('', ...)`.
- **Decision:** Hardened `load_dotenv` to skip lines with an empty key (never
  crash on a malformed line). Removed the stray line from `.env` (only lines with
  an empty key-part were dropped; real KEY=VALUE lines untouched).
- **Result:** Coral `/v1/models` now 200 (2 models), Baseten 200 (21 models).
  Both arms authenticated; proceeding to full preflight steps 2–5.
- **Surprises:** The 401 was a genuine inactive/old key, and a text-editor save
  introduced a stray `=` line — a good reason the loader must be crash-proof.

---

## 2026-11-04 (preflight complete) — findings + rate-limit parse fix

### Preflight results (both arms authenticated)
- **[1] models:** coral 200 (2 models), baseten 200 (21 models).
- **[2] raw usage shapes:** BOTH expose `prompt_tokens_details.cached_tokens`
  (parser already handles it). Coral also returns `cache_write_tokens`,
  `billable_cache_write_tokens`, a native `cost`, and `reasoning_tokens`.
  **Baseten returns speculative-decoding counts** in
  `completion_tokens_details`: `accepted_prediction_tokens` /
  `rejected_prediction_tokens` — the proxy's `_prediction_tokens` captures these,
  so SUMMARY can report Baseten speculative acceptance.
- **[3] cache probe (header-naive, direct httpx through proxy):** coral 2nd
  request cached=4352 (**cache works**); baseten 2nd request cached=0 (**no cache
  growth observed**). Caveat: this probe does NOT send OpenCode's session /
  cache-affinity headers, and back-to-back calls may hit different Baseten
  replicas. The REAL per-call hit rate under the agent (which does send those
  headers) is what the pilot measures and `analyze.py` reports — this is exactly
  the cache-story gap the benchmark was built to quantify.
- **[4] Baseten rate limits (chosen slug):** TPM=**2,097,154** (~2.1M),
  RPM=**120**. TPM ≫ 500k → no throttling-bias concern; no operator decision
  needed. Fixed the parser to read Baseten's `rate_limits:[{type,unit,threshold}]`
  schema (heuristic had returned None). The API also confirms public prices
  (input 0.60 / output 2.40) and `release_date=2026-10-17` for the slug.
- **[5] OpenCode smoke:** coral rc=0 + hello.txt created; baseten rc=0 +
  hello.txt created. The REAL agent works end-to-end through the proxy on both
  arms, and the mock-only `DecimalError` did NOT occur with real usage shapes.

### Snapshot match (still a confound)
- Baseten slug `...-1017-Fast` is confirmed the Oct-17 snapshot. Coral serves the
  opaque `deepseek-v4.1-flash-fast`; still cannot prove weight-identity. Keeping
  1017 as the closest faithful default (pre-registered).

### Spend
- Preflight total so far ~\$0.05 at public prices (2 preflight passes). Well under
  the \$5 cap.

---

## 2026-11-04 (cache investigation) — resolving Baseten cached=0

### Observed
- **OpenCode header provenance (Part A, proxy now records header NAMES + non-auth
  session-header values):** OpenCode 0.4.45 sends only `accept`, `accept-encoding`,
  `authorization`, `connection`, `content-length`, `content-type`, `host`,
  `user-agent`. **There is NO session / affinity / conversation header at all**
  (`session_headers = {}`) on either arm. The proxy forwards everything except its
  documented drop list (`authorization` reset from .env, plus hop-by-hop
  `host`/`content-length`/`connection`) — confirmed unchanged forwarding; there
  was simply no session header to preserve.
- **In-session caching (OpenCode smoke):** Coral's 2nd call cached **9984** tokens;
  Baseten's 2nd call cached **0** — Baseten cached nothing even within one agent
  session.
- **Affinity probe (Part B, stable `x-session-affinity: <uuid>` on all calls):**
  - Coral — 5k: cached `[0, 4352, 4352]`; 20k: `[4352, 17664, 17664]` → caches.
  - Baseten — 5k: `[0, 0, 0]`; 20k: `[0, 0, 0]` → **zero caching even with identical
    bodies AND a fixed affinity uuid**.
- **Cost cross-check (Coral native vs our computed public):** over 17 Coral calls,
  native billed `$0.02188758` vs computed public `$0.02188755` → rel diff
  **0.0001%** (floating-point noise). Coral is billed at EXACTLY public Fast
  prices here. Baseten returns no native `cost` field (0/17 calls).

### Options
- Treat Baseten's zero-cache as a harness artifact vs accept it as measured reality.
- Switch headline cost to native billed vs keep public list prices.

### Decision
- The pre-registered premise ("Baseten uses session IDs that OpenCode sends for
  cache affinity; stripping headers would bias results") is **empirically false
  for this harness**: OpenCode sends no session header, and Baseten does not cache
  even under an explicit `x-session-affinity` uuid with byte-identical bodies. Keep
  forwarding all headers unchanged (still correct and harmless), but it is NOT
  cache-critical here. Baseten's ~0% cache hit rate is a REAL property of this
  slug/account via the public OpenAI-compatible endpoint, not a harness artifact.
- Keep headline cost at PUBLIC list prices (pre-registered). Also persist
  `provider_native_cost_usd` per call (Coral reports it; equals public, so NO
  grandfathering on this account — record that fact). Baseten reports no native
  cost, so public-price is the only basis there.

### Effect on next step
- Pilot will show Coral with a high measured cache hit rate and Baseten ~0%; this
  is exactly the cost-story difference the benchmark was built to quantify, and
  `analyze.py` already reports measured per-provider hit rates + Baseten
  speculative acceptance. Proxy now records `request_header_names`,
  `forwarded_header_names`, `session_headers`, and `provider_native_cost_usd` for
  the pilot too. No settings change needed before the pilot.

### Surprises
- OpenCode sends ZERO session headers — the entire cache-affinity-header concern
  is moot for this harness.
- Baseten returned cached=0 under identical bodies + fixed affinity uuid — no
  prompt caching observed for `...-1017-Fast` on this account/endpoint. (Caveat:
  Baseten caching may require separate enablement; what we can claim is "not
  observed here, with or without affinity.")
- Coral's account is billed at exactly PUBLIC list prices — contrary to the
  pre-registered worry that it might be grandfathered cheaper. Public-price
  headline is therefore faithful to Coral's actual billing in this account.

---

## 2026-11-04 (Baseten caching deep-dive) — resolved: Baseten DOES cache

### Observed
- **OpenCode version:** installed 0.4.45 (from `^0.4.0`), latest published **1.19.5**
  (a major-line jump). 0.4.45 sends **no** session/affinity header. The 1.19.5
  binary contains `prompt_cache_key`, `sessionId`, `affinity`; an empirical smoke
  showed 1.19.5 sends `x-session-affinity`, `x-session-id`, `x-opencode-session-id`
  (all = the stable per-session id) on every call, and the proxy forwards them
  unchanged. Baseten docs: "Claude Code, Codex, and OpenCode include recognized
  session IDs" — true for 1.19.x, not for 0.4.45.
- **Multi-slug affinity probe (3× ~20k, same uuid, DIRECT to Baseten):** all four
  slugs cache and report `cached_tokens` in response usage, with TTFT dropping on
  hits:
  - 1017-Fast: cached [4352, 17664, 4352], ttft [0.41, 0.33, 0.68]
  - Flash-Fast: cached [17664, 17664, 17664], ttft [0.87, 0.72, 0.53]
  - Flash (std): cached [0, 17664, 17664], ttft [1.38, 0.52, 0.34]
  - GLM-5.3:     cached [0, 17728, 17728], ttft [3.13, 1.47, 1.20]
- **OpenAI Python SDK direct (step 3):** 1017-Fast cached [17664, 17664, 4352] —
  same as httpx; rules out client/proxy quirks.
- **Proxy is NOT the cause (controlled test):** identical 20k streaming reqs,
  direct cached [4352,17664,17664] vs THROUGH-PROXY cached [17664,17664,17664].
  The proxy caches fine. The earlier "Baseten cached=0" was **cold-cache /
  first-sighting warmth + no affinity header (OpenCode 0.4.45)**, not the proxy
  and not an inability to cache.
- **Billing usage API (step 4) disagrees with response usage:** for warm calls
  where response `cached_tokens`=17664, `GET /v1/model_apis/usage` (grouped by
  model, today) still shows `cached_input_tokens: 0`, `uncached_input_tokens`=all.
  So Baseten reports cache hits per-response (latency benefit) but is **not
  crediting cached input in billing** for this slug/tier — in contrast to Coral,
  whose native cost bills cached input at the discounted rate.
- **Request IDs (step 5)** saved for a possible support ticket (all 1017-Fast):
  `x-baseten-request-id` = 54ca701a1a9cc5bb…, 54ca701a1a9cc5d4…, 54ca701a1a9cc5f8…
  (full values in results/preflight/baseten_cache_deepdive.json).
- **OpenCode 1.19.5 does real tool work** (created hello.txt via Write, rc=0).

### Options
- (headers) stay on 0.4.45 (no affinity → unfair to Baseten) vs upgrade to 1.19.5
  (sends affinity → fair).
- (cost) keep pre-registered public-price formula using response cached_tokens vs
  switch Baseten to "no cache discount" per the billing API.

### Decision
- **Upgrade OpenCode to pinned 1.19.5** so Baseten gets the same cache-affinity
  treatment Coral gets — essential for a fair comparison. Also gains
  `--format json` (machine-readable events). Must re-verify the full pilot round
  (same-session `--continue` + file edits THROUGH the proxy) under 1.19.5 before
  running the pilot (a through-proxy 1.19.5 smoke once showed no file write; a
  direct smoke wrote the file, so re-confirm the proxy path).
- **Cost:** keep headline at public list prices using response `cached_tokens`
  (pre-registered), but FLAG the response-vs-billing cache discrepancy prominently
  in SUMMARY: Baseten's billing API shows cached_input=0, so a cache cost discount
  for Baseten is NOT confirmed and real Baseten cost may be higher than our
  public-price computation implies. (Coral's billing matches its public-price
  cached discount, verified earlier.)

### Effect on next step
- Re-verify the pilot round under 1.19.5 (through proxy). Keep
  BASETEN_MODEL=1017-Fast. The pilot's measured Baseten cache hit rate will now be
  realistic (affinity header sent), though warmth-dependent and more variable than
  Coral's eager content cache.

### Surprises
- The "Baseten never caches" conclusion from the first probe was wrong — it was
  cold-cache + missing affinity header (OpenCode 0.4.45). With 1.19.5's affinity
  header and warm replicas, Baseten caches across ALL tested slugs.
- Response `cached_tokens` and the billing usage API DISAGREE on Baseten (hits
  reported per-response, but cached_input=0 in billing) — the cache's cost benefit
  is unconfirmed even though its latency benefit is real.

---

## 2026-11-04 (CORRECTION + pre-pilot verification)

### Correction to the earlier "Baseten never caches" finding
- **Keep for the record:** the 2026-11-04 cache-investigation entry concluded
  "NO caching even under affinity" for Baseten. **That conclusion was wrong.**
- **Root cause:** (1) OpenCode **0.4.45 sent no session/affinity header at all**,
  so Baseten could not pin requests to a replica; and (2) **cold cache /
  first-sighting** — the probes hit replicas whose KV prefix wasn't warm.
- **Correction (measured):** with OpenCode **1.19.5** (which sends
  `x-session-affinity`/`x-session-id`) and warm replicas, Baseten caches on all
  four tested slugs and reports `cached_tokens` in response usage, with TTFT
  dropping on hits. The proxy was ruled out (direct == through-proxy).

### Harness change: OpenCode upgrade invalidates earlier smoke results
- **Observed/Decision:** Upgraded the pinned harness from **0.4.45 → 1.19.5**.
  This is a behavior-changing harness swap: it adds session/affinity headers and
  changes project-root resolution. **All smoke/cache results recorded under
  0.4.45 are therefore invalidated** and must not be mixed with 1.19.5 data. Only
  1.19.5 results feed the pilot.

### Observation: 1017-Fast affinity cache pattern
- With a stable `x-session-affinity` uuid, 3 identical ~20k requests to
  `DeepSeek-V4.1-Flash-1017-Fast` returned cached_tokens **4352 / 17664 / 4352**
  (and the OpenAI SDK run gave 17664 / 17664 / 4352). Caching is real but
  **warmth/replica-dependent and variable** (occasional partial hits), unlike
  Coral's eager, consistent content cache. Logged as a property to expect in the
  pilot's Baseten cache-hit-rate variance.

### Pre-pilot verification bug: OpenCode project root from $PWD
- **Observed:** First 1.19.5 verification: every round rc=0 and the Write tool
  reported success, but files landed in the REPO ROOT, not the per-run workspace
  (`context_carried=False`, empty workspace). Root-caused from the transcript:
  the agent wrote to an absolute `.../acceptance-bench/notes.txt`.
- **Cause:** `subprocess.Popen(cwd=ws)` changes the child's working directory but
  does NOT update the inherited `$PWD`; OpenCode 1.19.x resolves its project root
  from `$PWD`, so it used the parent's repo instead of the workspace. (0.4.45 used
  getcwd(), which is why this never showed before — another reason the upgrade
  invalidates old results.)
- **Fix:** `launcher.run_round` now sets `env["PWD"] = abspath(workspace)`. This
  fixes BOTH the verification and the pilot (outer_loop uses the same launcher) —
  caught here before any pilot run, which would otherwise have written all runs
  into the repo root and judged empty workspaces.
- **Result:** Re-ran verification — BOTH providers **PASS**: 3 same-session
  `--continue` rounds, file grows alpha→beta→gamma inside the workspace, nothing
  escapes to the repo root, all calls 200, affinity header forwarded, tool-call
  streaming intact (edits land) on BOTH arms. Coral works unchanged with the new
  session headers.
- **Surprises:** The real blocker was never the model/permissions/tool-streaming
  — it was a `$PWD`-vs-cwd mismatch introduced by the major OpenCode upgrade.

### SUMMARY cost sensitivity row
- Added a "cost — NO cache discount [pending billing verification]" row + cost
  per accepted (no discount) to analyze.py, computed by billing all input at the
  full input price. Primary cost stays public-price with the cached discount on
  response `cached_tokens` (pre-registered). Billing comparison to run post-pilot.

---

## 2026-11-04 (PILOT RUN + billing resolved)

### Pilot executed (OpenCode 1.19.5, PWD fix, affinity header live)
- **Observed:** 6 runs, interleaved order `[baseten, coral] × 3` (seed 20261103),
  strictly sequential. **All 6 ACCEPTED** (coral 3/3, baseten 3/3). No 429/5xx/529.
  Pilot spend $0.95 at public prices (total incl. investigation ≈ $1.17, well
  under the $5 cap).
- **Recorded observations (n=3, descriptive only):**
  - time per ACCEPTED: **baseten 33.7s vs coral 48.2s** (baseten faster end-to-end).
  - cost per ACCEPTED (primary, with cache discount): **coral $0.046 vs baseten
    $0.199** (coral ~4× cheaper).
  - decode tok/s p50: **baseten 2165 vs coral 817** (baseten decodes faster).
  - cache hit rate: **coral 94.5% vs baseten 50.8%**.
  - median rounds: coral 3, baseten 2.
  - TTFT p50 ~0.32s both; baseten p95 higher (1.35s). Baseten speculative
    acceptance 0.64.
  - Amdahl: per-call speedup (coral/baseten decode) 0.38×; predicted end-to-end
    0.47× vs observed 0.72× — non-model agent + judge time dilute the decode gap.
- **Decision:** Report as-is. The pilot's value is the DECOMPOSITION: "tokens/sec"
  (baseten wins) and "time-to-ACCEPTED" (baseten wins here) and "cost-to-ACCEPTED"
  (coral wins, via ~95% cache) point in different directions — exactly what a
  single wall-clock or a tokens/sec number would hide. NO statistical claims (n=3).
- **Surprises:** Baseten's much faster decode did NOT make it cheaper — Coral's
  ~95% cache hit vs Baseten's ~51% (plus lower unit prices) made Coral ~4× cheaper
  per accepted result despite being slower. Coral also needed more review rounds.

### Billing verification (resolved the earlier cached_input=0 worry)
- **Observed:** After the pilot settled, Baseten usage API shows
  `cached_input_tokens=1,116,160` for 1017-Fast today (NOT 0 — the earlier 0 was
  aggregation lag). Billing API (`GET /v1/billing/model_apis`) daily subtotal
  **$0.8503** matches the WITH-cache-discount computation (~$0.83 + GLM) and is far
  below the no-discount figure (~$1.39+).
- **Decision:** Baseten DOES bill cached input at the discounted rate, like Coral.
  Primary (with-discount) public-price cost is the correct basis for both arms.
  Kept the "no cache discount" row only as a labeled worst-case upper bound.
  `harness/billing_check.py` records the comparison.
- **Surprises:** The "Baseten billing ignores cache" hypothesis was also a lag
  artifact — once aggregation settled, billing credited the cache.

---

## 2026-11-04 (pilot diagnostics, no new pilot runs)

### 1. Decode-speed validity — pilot per-call decode is UNRELIABLE
- **Observed:** Both providers STREAM reasoning deltas (`reasoning_content`); the
  proxy timestamps them (reasoning-on probe: coral 46 / baseten 34 reasoning
  chunks). With `reasoning_effort=high`, output_tokens includes a large hidden
  reasoning count while visible content streams in a short burst, so per-call
  `decode_s` is tiny and noisy. Recomputed over calls with ≥200 visible output
  tokens, decode tok/s is absurd (coral p95 26,850; baseten p95 625,250). The
  earlier SUMMARY figure (817 / 2165) was noise-dominated.
- **Decision:** Mark per-call pilot decode tok/s UNRELIABLE in SUMMARY; use the
  controlled probe (below) as authoritative and feed it into the Amdahl check.
- **Added:** proxy now records `n_content_deltas`, `n_reasoning_deltas`,
  `content_decode_s`, `reasoning_decode_s`, `ttft_content_s`.

### 2. Controlled long-generation probe (authoritative decode)
- **Observed:** same prompt, ~750 output tokens, `reasoning_effort=none` (0
  reasoning), 5 reps: **coral 370 tok/s, baseten 685 tok/s** → Baseten decodes
  **1.85× faster** (same direction as the crude pilot ratio, far smaller
  magnitude). ~2 tokens/content-chunk both; neither streamed reasoning here.

### 3. Speed vs rounds
- **Observed:** model latency/call p50 coral 0.70s vs baseten 0.48s (p95 3.66 vs
  1.85) — consistent with Baseten's faster decode. OpenCode startup/round ~0.66–
  0.69s both (small, provider-independent). Agent time/round p50 coral 11.7s vs
  baseten 15.3s; coral ran more rounds (median 3 vs 2), so Coral's higher wall is
  partly MORE REVIEW ROUNDS, not slower model calls.

### 4. Cost decomposition
- **Observed:** cost gap (baseten − coral) = $0.459. **Price effect $0.150 (33%)**
  (Baseten's higher unit prices); **token/cache effect $0.309 (67%)** dominates —
  Baseten billed 849k uncached input vs Coral 117k (Coral cached 2.02M). Baseten's
  tokens even at Coral's prices ($0.446) still far exceed Coral's own ($0.137).
- **Decision:** Coral's cost advantage here is mostly its CACHE behavior, not
  price. Reported in SUMMARY §4.

### 5. Cache misses — not a cold-start artifact
- **Observed:** excluding each run's first call changes hit rate negligibly
  (coral 94.5%→94.6%, baseten 50.8%→50.8%); first calls are 0.15–0.23% of uncached
  tokens. Baseten's lower hit rate is pervasive mid-session, not session-start.

### 6. Optional third arm
- Added `[providers.baseten_sept]` (DeepSeek-V4.1-Flash-Fast, Sept snapshot,
  `enabled=false`) to config/providers.toml for a future snapshot-sensitivity
  comparison. NOT in the run plan; not run.

### Surprises
- The single biggest headline-risk was the decode number: the pilot's "2165 tok/s"
  was an artifact of reasoning-contaminated, bursty `decode_s`. Clean measurement
  halves the gap (1.85×, not 2.6×) — and Coral's slower per-call decode is real.
- Coral's cost win is a CACHE story (67% of the gap), not mainly a price story.

---

## 2026-11-04 (diagnostics round 2 + pre-registration)

### Diagnostic 1: work per run — Baseten's speed is per-call, not fewer calls
- **Observed (pilot, per run averages):**
  - coral: calls/run 33.3, model_time/run **38.4s**, visible_out/run 5636, reasoning/run **6574**.
  - baseten: calls/run 28.0, model_time/run **21.5s**, visible_out/run 5536, reasoning/run **4778**.
- **Decision/finding:** Baseten's faster time-to-accept (33.7s vs 48.2s) comes from
  **less model time per run** (21.5 vs 38.4s), driven by faster per-call decode
  (probe: 1.85×) AND **lighter reasoning** (4778 vs 6574 tokens) — NOT from fewer
  calls (28 vs 33, similar) nor lighter visible output (~5600 both). Coral both
  reasons more and decodes slower.
- **Surprise:** visible output is nearly identical across arms; the model-time gap
  is almost entirely decode speed + reasoning volume.

### Diagnostic 2: cache locality + affinity stability
- **Observed (2a):** uncached input is overwhelmingly **later-in-round**, not
  round-starts (coral 98.7% later; baseten 96.8% later). So Baseten's misses are on
  the growing mid-round context, not session/round starts.
- **Observed (2b):** the `x-session-affinity` value (sha256[:8]) is **identical
  across all `--continue` rounds within every run** on both arms (distinct=1 per
  run). **OpenCode keeps one stable session/affinity ID across --continue
  processes.**
- **Decision:** No harness correction needed — affinity is already stable per run
  on both arms (the proposed "one stable affinity ID per run" fix is satisfied by
  OpenCode's `--continue`). Baseten's ~51% hit rate is therefore a genuine KV-cache
  property for this long-agent-session workload, not affinity churn, cold start, or
  a harness artifact. (Hashes only; raw affinity values never logged.)

### Pre-registration drafted (not run)
- Wrote `PREREGISTRATION.md`: arms A=coral-fast, B=baseten-1017-fast, conditional
  C=baseten-sept-fast (if Coral's snapshot = September); **10 runs/arm** in
  randomized interleaved C/B pairs (pair index = time slot); caps unchanged;
  primary metrics time & cost per accepted; secondary rounds/calls/model-latency/
  cache-hit/controlled-probe decode; analysis = **paired bootstrap 95% CIs
  resampling the 10 pairs** (no NHST); budget from pilot per-run cost.
- **Harness changes to support it:** `build_run_plan(seed, runs_per_arm)` + env
  `RUNS_PER_ARM` (default 3); **spend guard raised $5 → $12** (env `SPEND_CAP_USD`;
  bump to $18 if arm C enabled). Decode locked to the controlled probe (pilot
  per-call decode is unreliable).
- **Not executed.** Awaiting go-ahead to run the main experiment.

---

## 2026-11-04 (pre-registration update — arm C unconditional + trace replay)

### Enable arm C (baseten-sept-fast) unconditionally
- **Observed:** The pilot showed Coral emitting **~38% more reasoning tokens**
  than Baseten-1017 under IDENTICAL settings (6,574 vs 4,778 /run), and this
  reasoning-volume difference drove ~15% of the model-time gap (below).
- **Options:** keep C conditional on resolving Coral's snapshot vs run C always.
- **Decision:** Run C unconditionally. A behavioral divergence this large under
  identical settings is exactly what a snapshot mismatch produces, so the snapshot
  confound is **material**. B and C are two Baseten snapshots on the same serving
  stack, so B−C isolates the snapshot effect while A−B stays the primary
  (stack) comparison. Spend guard raised to **$18**; run plan generalized to 3
  arms interleaved in randomized triples.
- **Effect:** `ARMS=coral,baseten,baseten_sept RUNS_PER_ARM=10`.

### Model-time gap decomposition (pilot)
- **Observed:** per-run model time coral 38.4s vs baseten 21.5s (gap **16.9s**).
  Using controlled-probe decode rates (coral 370, baseten 685 tok/s) on each arm's
  total output tokens (coral 12,210 incl 6,574 reasoning; baseten 10,314 incl
  4,778 reasoning): estimated decode 33.0s vs 15.1s (est gap 18.0s, ≈ observed).
  Decomposition of that gap: **decode-SPEED effect ≈ 15.2s (≈85%)**,
  **token-VOLUME effect ≈ 2.8s (≈15%)** (Coral's +1,796 reasoning / +1,896 total
  tokens). Baseten's per-run prefill/TTFT sum is actually HIGHER (12.9s vs 7.8s),
  partially offsetting, so the net model-time advantage is decode-dominated.
- **Decision:** Report decode speed as the dominant driver of the model-time gap;
  reasoning-volume (snapshot-sensitive) is secondary but real — another reason to
  run arm C.

### Secondary experiment: TRACE REPLAY (pre-registered, not run)
- Added to PREREGISTRATION.md: opt-in full-body capture for the first 3 coral and
  first 3 baseten-1017 runs (bodies gitignored, never committed); replay each
  captured session's exact request sequence with original inter-call gaps against
  all 3 arms, fresh affinity per replay; measure TTFT/decode/cache/model-time/cost
  per session; paired bootstrap over the 6 sessions. Purpose: isolate inference
  speed + caching from agent behavior. Budget ~$2.5, inside the $18 guard.

### Order of operations
- PREREGISTRATION.md is committed BEFORE any main-experiment run so git timestamps
  prove the design preceded the data.

---

## 2026-11-04/05 (MAIN EXPERIMENT executed + trace replay)

### Result: 30 runs (10/arm, 3 arms), all ACCEPTED
- **Headline (pre-registered bootstrap 95% CIs, 10 time-slot triples):**
  - **A−B (coral vs baseten-1017):** Δtime/accepted **[+8.4, +13.5]s** (excludes 0
    → coral robustly SLOWER to accept); Δcost/accepted **[−$0.145, −$0.089]**
    (excludes 0 → coral robustly CHEAPER).
  - **B−C (snapshot: baseten-1017 vs baseten-sept):** Δtime **[−2.7, +4.0]s** and
    Δcost **[−$0.020, +$0.041]** both **INCLUDE 0** → no robust snapshot difference.
    So the A−B gaps are a serving-stack/model story, NOT a snapshot artifact. This
    is exactly why arm C was worth running.
  - time/accepted: coral 41.3s, baseten 30.5s, sept 29.6s. cost/accepted: coral
    $0.044, baseten $0.159, sept $0.148. cache hit: coral 95.9%, baseten 72.3%,
    sept 70.6% (up from the pilot's ~51% — warmer/steadier under 30 runs).
- **Controlled-probe decode:** baseten 646 vs coral 389 tok/s (**1.66×**).
- **Cost decomposition (30 runs):** gap 65% token/cache behavior, 35% price.
- **Spend:** main $3.6 + trace replay ~$2.5 ≈ $6 (guard $18).

### Trace replay (inference isolated from agent behavior)
- 6 captured sessions × 3 arms, identical request sequences, original gaps, fresh
  affinity. Median per session: model_time coral 40.4s vs baseten 26.5s vs sept
  24.1s; TTFT coral 0.96s vs baseten 0.48s vs sept 0.44s; cache coral 99% vs
  baseten 71% vs sept 78%; cost coral $0.031 vs baseten $0.206 vs sept $0.160.
- **Conclusion:** the speed/cost tradeoff holds with agent behavior held FIXED —
  Baseten is genuinely faster per identical workload (decode + TTFT), Coral
  genuinely cheaper (cache + price). The main experiment's end-to-end gap is NOT
  merely Coral's extra reasoning; it's inference-level.

### Operational issues hit + fixes (logged for honesty)
- **run_id collision:** pilot + main reuse run_ids; calls.jsonl was append-only →
  contamination. Fix: archive calls/runs/rounds jsonl at experiment start; clear
  runs/<id>/.
- **Stale-call bursts:** a dying OpenCode from an aborted run wrote a few calls
  into the next experiment's fresh calls.jsonl under a reused run_id. Fix:
  per-run [wall_start, wall_end] window filter in _calls_for and analyze; plus a
  one-time physical clean of 14 stale calls from calls.jsonl (archived the dirty
  copy). All totals/bootstrap use windowed calls only.
- **Trace-replay 429s:** baseten + baseten_sept share one Baseten account TPM;
  replaying both back-to-back right after the main run throttled (all baseten
  replays failed first pass). Fix: 429/5xx retry with backoff + 5s inter-replay
  pacing; re-ran → 18/18 succeeded, 0 failures.
- OpenCode 1.19.5 `$PWD` project-root fix (earlier) held across all 30 runs (no
  escaped files).

### SUMMARY
- analyze.py now auto-labels n≥10 as the PRE-REGISTERED MAIN EXPERIMENT (title,
  banner, limitations), reports 3-arm table + bootstrap CIs + trace-replay +
  refreshed diagnostics. Pilot artifacts archived under results/raw/archive/.

---

## 2026-11-07 — CACHING EXTENSION (why, how bounded, Coral-only by choice)

### Why extend into caching
- **Observed:** The main experiment's headline is a cache story: Coral's cost
  advantage is ~65% token/cache behavior vs ~35% price, and Coral's cache hit rate
  (95.9%) far exceeds Baseten's (72.3%). But the experiment never characterized the
  cache *mechanism*: how the miss penalty scales with prompt size, how long a cache
  entry survives idle, how it behaves under fan-out / branching / single-token
  busts. Those determine whether caching helps the *acceptance loop* (latency),
  not just the bill.
- **Decision:** Add a bounded caching study in three layers. **LAYER A** = offline
  regression/classification of ALREADY-RECORDED data (the only new calls are Coral
  `/v1/models` network pings). **LAYER B** = NEW active probes, **CoralBricks
  ONLY**. **LAYER C** = billing reconciliation, NO API calls.
- **Effect on next step:** Build harness/cache_analysis.py (A),
  harness/cache_subexperiment.py (B), harness/billing_reconciliation.py (C). New
  committable charts under results/cache_charts/ (results/charts/ is gitignored).

### How the work is bounded
- **Scope:** effects on the ACCEPTANCE LOOP only. We do NOT modify the
  main-experiment results, SUMMARY.md, or PREREGISTRATION.md — only NEW files are
  written (CACHE_ANALYSIS.md, PREREGISTRATION_CACHE.md, CACHE_SUBEXPERIMENT.md,
  BILLING_RECONCILIATION.md, CACHE_REPORT_SHORT.md, SERIES_NOTES.md).
- **Budget:** Layer B carries a HARD $2.50 budget with stop rules (abort on
  projected overspend, repeated 429/5xx, or a result that contradicts the main
  experiment in a way that implies a measurement bug). Layer B is pre-registered
  (PREREGISTRATION_CACHE.md committed BEFORE any Layer B call).
- **No human-paced loop** in this episode (would burn wall-clock budget); recorded
  as a future episode in SERIES_NOTES.md.

### New experiments are CoralBricks-only BY CHOICE
- **HARD RULE (operator):** ZERO new API calls to Baseten — no inference, no
  /v1/models, no usage/billing endpoints. Existing recorded Baseten data in
  results/raw/ may be analyzed offline, but every NEW call goes to Coral only.
- **Consequence (logged honestly):** any network-adjusted or active-probe
  comparison for Baseten is therefore **UNMEASURED by choice**, not by failure.
  Layer A offline regressions still cover all three arms from recorded data; only
  the live network baseline (Layer A step 2) and all of Layer B are Coral-only.
- **Key handling:** the Coral key is loaded ONLY from
  ~/code/acceptance-bench/.env via proxy.config.load_dotenv (never copied
  elsewhere). coral-probe timing logic is ported into acceptance-bench rather than
  importing the external repo.

### Layer A executed (committed 569a49e)
- **Result (recorded data, 904 windowed main-run 200-calls):**
  - Miss penalty (joint TTFT regression): **~25 ms per 1k uncached** (Coral) vs
    **~9 ms/1k** (Baseten 1017 / Sept). **Cached slope ≈ 0** on all three arms
    (Coral slightly negative) → cache hits are essentially free in TTFT. CIs
    bootstrap over calls.
  - Coral network baseline (CORAL ONLY, 20× GET /v1/models): median RTT ~115 ms;
    Coral TTFT p50 270 ms → ~155 ms server-side after RTT. Baseten net-adjusted
    = UNMEASURED by choice.
  - Miss rate (cacheable calls): Coral **12.5%** vs Baseten **~58%**, both
    **eviction-dominated** (partial-tail) not routing (near-total) misses.
- **Selection caveat logged:** the proxy only timestamps content/reasoning
  deltas, so Coral's 126 tool-call-only completions (of 319) have no TTFT and are
  excluded from its regression; Baseten has 0 such. §3/§4 use all 200-calls.

### Layer B PRE-REGISTERED (PREREGISTRATION_CACHE.md committed before any call)
- **Decision:** Route Layer B through the recording proxy (honors "same proxy +
  cost accounting") with `PROXY_INJECT_PARAMS=0` and `max_tokens=16` /
  `reasoning_effort=none` (we measure prefill/caching, not generation, to protect
  the $2.50 budget). The client sends a dummy key; the proxy injects the real
  Coral key from `.env`, so the experiment client never reads the secret.
- **Bounding:** dedicated `cacheexp-*` run_ids (excluded from main + Layer C);
  hard $2.50 stop + 429/5xx stop + measurement-bug stop (immediate resend must
  show high cached fraction, per the main experiment's ~96%). Test 1's idle
  windows run in isolation (no other sends) so shared-replica eviction can't
  confound cache-survival. No human-paced loop (deferred to SERIES_NOTES.md).
- **Effect on next step:** run harness/cache_subexperiment.py in the background.

### Layer B smoke → Coral account caps in-flight at 5 (logged deviation)
- **Observed (smoke, a few Coral calls):** proxy path + SSE parse + cost
  accounting + generator all work; calibration 3.29 chars/token; immediate
  identical resend of a fresh 30k prompt cached **79%** (sanity threshold 0.5
  passed — the cache path behaves; the main experiment's ~96% is over many warm
  calls). BUT an 8-way warm fan-out returned **HTTP 429**: "this account may have
  5 in flight at once." So the Coral account has a **hard 5-concurrent cap**.
- **Decision:** (1) add a global `BoundedSemaphore(5)` around every send so
  parallel sections (test 1's 15-session prime burst, fan-out) never exceed the
  cap and never self-429; (2) add 429/5xx retry-with-backoff in `send()` so a
  transient contention retries instead of counting toward the 3-in-a-row error
  stop; (3) reduce fan-out width from the pre-registered **8 → 5** (the account
  max). This is a **logged deviation** from PREREGISTRATION_CACHE.md, forced by
  the account limit; lifting it would need account credit (out of scope, and would
  still be Coral-only). 5-concurrent still answers the warm/cold prefill-sharing
  question. All other Layer B parameters are as pre-registered.
- **Effect:** launch the full run in the background (~30 min wall; test 1 idles
  1800s). Budget + stop rules unchanged.

### Layer C executed (billing reconciliation, NO API calls)
- **Method:** dedup all proxy-recorded calls across the live `calls.jsonl` + 3
  archived snapshots by (provider, timestamp, run_id, round, call_index) so each
  physical call counts once; exclude `cacheexp-*` (this caching work, which is
  AFTER the dashboard snapshot); bucket run-id'd calls by the main-experiment wall
  window into pilot/pre-main vs main vs stale, and non-run-id'd calls into
  probes/preflight. Replays bypassed the proxy → summed from `trace_replay.jsonl`.
- **Result (recorded total vs dashboard):**
  - **Baseten 1017-Fast: $4.89 vs $4.95 (99%)**, remainder $0.06.
  - **Baseten Sept-Fast: $3.44 vs $3.48 (99%)**, remainder $0.04.
  - **Coral: $1.00 vs $1.25 (80%)**, remainder $0.25 — dominated by **5.35M
    unexplained cached tokens**, consistent with prior `coral-probe` use of the
    SAME Coral account plus non-replay direct probes (not through the proxy).
- **Decision:** report the remainder as-is (not forced to zero); attribute to
  direct-probe calls that bypassed the proxy, prior same-account usage (Coral),
  dashboard token-counting semantics (cache-write/aborted), and aggregation lag.
  The Baseten arms reconcile to 99%, a strong check that the proxy's per-call
  cost/token accounting matches the provider's billing.
- **Surprise:** the Baseten reconciliation is tight (99%) because almost all
  Baseten traffic went through our harness; Coral's looser 80% is explained by the
  shared account's earlier life, not by a measurement error.

### Layer B EXECUTED (Coral-only; $1.02 / $2.50; 84 calls; no stop triggered)
- **Test 1 — review delay (cache survival):** mean cached fraction on re-send is
  **84–99% across ALL idle gaps 10s→1800s** with no downward trend (900s even
  99%). **Coral's prefix cache survives ≥30 min idle** — a human review pause does
  not cost a re-prefill. (Chart: cache_review_delay.png.)
- **Test 2 — context growth:** miss TTFT scales with size (0.74 / 2.03 / 4.62 s at
  ~20k / 100k / 300k) while hit TTFT stays low (0.30 / 0.45 / 0.91 s); hit cached
  fraction 81 / 97 / 100%. **300k context works** (no context-limit error) and the
  cache's latency win GROWS with context size.
- **Test 3 — warm fan-out:** 5 concurrent on a primed prefix → **95% mean hit
  rate**, tight TTFT (0.29–0.83 s). A warmed prefix absorbs concurrent fan-out.
- **Test 4 — cold fan-out:** 5 concurrent identical on a NEW prefix → **3/5 shared
  a prefill** (cached 82–97%), 2 missed. Concurrent cold requests partially
  in-flight-dedup (first to prefill warms the rest), not all-independent.
- **Test 5 — branch/rollback:** 10-turn convo, branch from turn 5 → branch cached
  **15,104 / 18,305** tokens ≈ the shared 5-turn prefix (15,243). **The cache keeps
  the common ancestor when a conversation branches** — rollback re-prefills only the
  divergent tail.
- **Test 6 — prefix-bust:** cached tokens are **exactly 256-aligned**, clustering
  at multiples of **8192** (=32×256): bust@10% → 0, @50% → 8192, @90% → ~24576. So
  cached is NOT proportional to bust position; an **early edit re-prefills far more
  than its position implies** and the cache retains whole 256-aligned blocks before
  the first changed token. (Chart: cache_prefix_bust.png.)
- **Deviation (logged):** Coral account caps in-flight at 5 → fan-out width 5 (not
  pre-registered 8); global semaphore + 429-retry kept us under the cap with no
  stop-rule trip. Sanity immediate-resend cached 79% (> 0.5 threshold; main
  experiment's ~96% is a many-warm-call average), so no measurement-bug stop.
- **Consistency with main experiment:** cache hits are cheap in latency (hit TTFT
  ≪ miss TTFT) and durable — consistent with the main experiment's finding that
  Coral's cost/latency edge is a cache story. No contradiction → no stop.

### FINAL synthesis + future episodes
- **Wrote `results/CACHE_REPORT_SHORT.md`:** miss-penalty slopes, Coral
  network-adjusted TTFT (Baseten UNMEASURED by choice), review-delay survival
  curve, warm/cold fan-out, branch/rollback, prefix-bust, billing headline, and a
  plain-English verdict — **caching helps the acceptance loop on latency, not just
  cost** (hit TTFT ≪ miss TTFT, durable across a 30-min pause, branch- and
  fan-out-friendly; fragile only to early prompt-head edits and to Coral's steep
  per-token miss penalty). Every number traces to a recorded Layer A/B/C file;
  unmeasured items are labeled.
- **`SERIES_NOTES.md`:** deferred the **human-paced acceptance loop** to a future
  episode (would exceed the $2.50 unattended budget), plus the Baseten
  network/active-cache comparison (blocked by the Coral-only rule) and fan-out
  beyond the 5-in-flight cap.
- **Integrity check:** the 95 `cacheexp-*` calls the proxy appended to the
  gitignored `calls.jsonl` are excluded from main windowing by run_id — the main
  experiment still resolves to exactly its 904 windowed calls. SUMMARY.md,
  PREREGISTRATION.md, and the main results were NOT modified; only new files were
  written. No Baseten API call was made in any layer of this extension.

### Detailed report + local web page (operator request)
- **`harness/build_cache_report.py`** reads the recorded Layer-A/B/C JSON (no
  hardcoded metrics) and emits BOTH an exhaustive consolidated markdown
  **`results/CACHE_FULL_REPORT.md`** (11 sections: methodology, all Layer-A
  regression tables + CIs, network baseline, hit curves, miss anatomy, all six
  Layer-B tests with full per-rep data, full Layer-C per-bucket billing,
  verdict, reproduction/manifest) and a self-contained **`site/caching.html`**
  (the 4 charts embedded as base64 so it serves/opens from anywhere; nav link to
  the existing `site/index.html`).
- **Served locally:** `python -m http.server 8000 --directory site` →
  http://localhost:8000/caching.html (verified HTTP 200). Still Coral-only /
  recorded-data-only; no Baseten calls; main results untouched.
