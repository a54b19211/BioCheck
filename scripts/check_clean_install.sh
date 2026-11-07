#!/bin/sh
# Install requirements.lock (every distribution, the extraction-contract wheel included, pinned by sha256) into a
# new, empty virtualenv, import every module under app/ and run the test suite there: a pin that lacks what the
# code uses fails here, not on the first request. `make check-install` runs it with the Makefile defaults.
#
#   scripts/check_clean_install.sh
#
# EXTRACTION_CONTRACT_WHEELS  directory holding the contract wheel (default ../extraction-contract/dist)
# LLM_TRANSPORT_WHEELS        directory holding the zk-llm-transport wheel (default ../llm-transport/dist)
# UV                          the uv binary (default: uv on PATH)
# PYTHON                      interpreter for the virtualenv (default 3.12: the lock is resolved for it)
# UV_OFFLINE=1                install from the local uv cache only
set -eu
cd "$(dirname "$0")/.."
UV="${UV:-uv}"
WHEELS="${EXTRACTION_CONTRACT_WHEELS:-../extraction-contract/dist}"
TRANSPORT_WHEELS="${LLM_TRANSPORT_WHEELS:-../llm-transport/dist}"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
"$UV" venv --quiet --python "${PYTHON:-3.12}" "$work/venv"
"$UV" pip sync --quiet --python "$work/venv/bin/python" --require-hashes --find-links "$WHEELS" --find-links "$TRANSPORT_WHEELS" requirements.lock
"$work/venv/bin/python" - <<'EOF'
import importlib
import importlib.metadata
import pathlib
import sys

names = sorted("app." + p.stem for p in pathlib.Path("app").glob("*.py") if p.stem != "__init__")
for name in names:
    importlib.import_module(name)
print(f"python {sys.version.split()[0]}, extraction-contract {importlib.metadata.version('extraction-contract')}:"
      f" imported {len(names)} modules ({', '.join(names)})")
EOF
# a module attribute is only read when a rule runs: the suite (offline, fake model) exercises every checker
"$work/venv/bin/python" -m pytest -q -p no:cacheprovider
