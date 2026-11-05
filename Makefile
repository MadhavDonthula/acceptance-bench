# acceptance-bench — pilot benchmark driver.
#
# Targets:
#   make setup       create the uv venv and install deps + opencode
#   make verify-task prove starter FAILS and reference PASSES the judge
#   make overhead    measure proxy overhead (direct vs through proxy)
#   make preflight   run all preflight checks; STOP and report (needs keys)
#   make pilot       run 3 runs per provider, interleaved (needs go-ahead)
#   make analyze     build results/SUMMARY.md purely from recorded raw files

PY := uv run python
UV := uv

.PHONY: setup verify-task check4-sensitivity overhead preflight pilot analyze clean

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

analyze:
	$(PY) analyze.py

clean:
	rm -rf runs results/raw/*.jsonl results/charts/*
