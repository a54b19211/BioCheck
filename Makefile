# The one entry point for this checkout (README: 开发环境与测试).
# uv 0.13.0 from PATH (~/.local/bin/uv) or UV=<path>. requirements.lock is resolved for CPython 3.12 on
# x86_64-unknown-linux-gnu with a sha256 for every distribution; `make venv` installs only from it.
UV ?= uv
PYTHON ?= 3.12
# the shared contract wheel (built in the sibling extraction-contract checkout; hash in the lock)
EXTRACTION_CONTRACT_WHEELS ?= ../extraction-contract/dist
VENV ?= .venv
PY = $(VENV)/bin/python
LOCK_INPUTS = requirements.txt requirements-contract.txt
LOCK_FLAGS = --generate-hashes --python-version 3.12 --python-platform x86_64-unknown-linux-gnu --find-links $(EXTRACTION_CONTRACT_WHEELS)

.PHONY: help venv test lint check check-install check-lock lock
help:
	@echo 'make venv           $(VENV) from requirements.lock only (hash-checked, Python $(PYTHON))'
	@echo 'make test           the full test suite in $(VENV)'
	@echo 'make lint           ruff over the checkout'
	@echo 'make check          test + lint + check-lock'
	@echo 'make check-install  a throwaway venv from the lock: import every module, run the suite'
	@echo 'make check-lock     requirements.lock still matches $(LOCK_INPUTS) (reads the index)'
	@echo 'make lock           re-resolve requirements.lock after a change of $(LOCK_INPUTS)'
venv:
	test -x $(PY) || $(UV) venv --quiet --python $(PYTHON) $(VENV)
	$(UV) pip sync --quiet --python $(PY) --require-hashes --find-links $(EXTRACTION_CONTRACT_WHEELS) requirements.lock
test: venv
	$(PY) -m pytest -q
lint: venv
	$(PY) -m ruff check .
check: test lint check-lock
check-install:
	UV=$(UV) PYTHON=$(PYTHON) EXTRACTION_CONTRACT_WHEELS=$(EXTRACTION_CONTRACT_WHEELS) sh scripts/check_clean_install.sh
# the lock is re-resolved with itself as the preference: unchanged inputs give the same pins.
# make reports Error 1: resolved, and the lock differs (stale); Error 2: the resolution itself failed (no
# index or network, or inputs that do not resolve), so nothing was compared - that is no reason to re-lock
check-lock:
	@tmp=$$(mktemp -d) && trap 'rm -rf $$tmp' EXIT && cp requirements.lock $$tmp/lock && \
	if ! $(UV) pip compile --quiet $(LOCK_INPUTS) $(LOCK_FLAGS) -o $$tmp/lock; then \
	  echo 'check-lock did not run: resolving $(LOCK_INPUTS) failed (see the uv error above: package index or' \
	    'network unreachable - on zktest see the README for the proxy - or inputs that do not resolve);' \
	    'requirements.lock was not compared.' >&2; \
	  exit 2; \
	fi; \
	grep -v '^#' requirements.lock > $$tmp/a && grep -v '^#' $$tmp/lock > $$tmp/b && \
	if cmp -s $$tmp/a $$tmp/b; then echo 'requirements.lock is current'; \
	else diff $$tmp/a $$tmp/b; echo 'requirements.lock is stale: run make lock'; exit 1; fi
lock:
	$(UV) pip compile $(LOCK_INPUTS) $(LOCK_FLAGS) -o requirements.lock
