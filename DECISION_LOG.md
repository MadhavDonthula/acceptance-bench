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
