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
