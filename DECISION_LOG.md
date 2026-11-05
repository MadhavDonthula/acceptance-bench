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
