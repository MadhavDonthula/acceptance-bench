# acceptance-bench benchmark driver.
#
# No API keys needed
#   make setup        create the uv venv and install deps + pinned opencode
#   make verify-task  prove starter FAILS, reference PASSES, 6 mutants fail their check
#   make check4-sensitivity  run the race check 50x against mutant + reference
#   make overhead     measure proxy overhead (direct vs through proxy, mock upstream)
#   make analyze      rebuild results/SUMMARY.md purely from results/raw/
#   make cache-report rebuild the cache reports + site/caching.html from recorded data
#   make site         rebuild site/index.html
#
# Needs API keys in .env (spends money)
#   make preflight    key check, /v1/models, cache probe, rate limits, smoke
#   make pilot        3 runs per arm, coral + baseten (about $1)
#   make main         pre-registered main experiment, 3 arms x 10 runs (about $3.60)
#   make replay       trace replay of captured sessions against all 3 arms (about $2.50)

PY := uv run python
UV := uv

.PHONY: setup verify-task check4-sensitivity overhead preflight pilot main replay analyze cache-report clean site

setup:
	$(UV) venv --python 3.12
	$(UV) pip install -e .
	@echo "Installing pinned OpenCode (npm opencode-ai)..."
	npm install
	@./node_modules/.bin/opencode --version || true

verify-task:
	$(PY) -m judge.verify_task

check4-sensitivity:
	$(PY) -m judge.check4_sensitivity 50

overhead:
	$(PY) -m proxy.overhead

preflight:
	$(PY) -m harness.preflight

pilot:
	$(PY) -m harness.outer_loop

main:
	RUNS_PER_ARM=10 ARMS=coral,baseten,baseten_sept SPEND_CAP_USD=18 PILOT_SEED=20261103 $(PY) -m harness.outer_loop

replay:
	$(PY) -m harness.trace_replay

analyze:
	$(PY) analyze.py

clean:
	rm -rf runs results/raw/*.jsonl results/charts/*

cache-report:
	$(PY) -m harness.billing_reconciliation
	$(PY) -m harness.build_cache_report

site:
	$(PY) -m harness.build_site
