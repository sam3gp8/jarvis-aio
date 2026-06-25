#!/usr/bin/env bash
# Run the leaf-module test suite under coverage and compute CRAP scores.
#
#   scripts/run_crap.sh [threshold]   # default threshold 4.0
#
# Exits non-zero if the average CRAP exceeds the threshold, so it works as a CI
# gate. Add more fully-testable leaf modules to TARGETS as their tests land.
set -euo pipefail
cd "$(dirname "$0")/.."

THRESHOLD="${1:-4.0}"
SRC="jarvis_assistant/jarvis_component"
TARGETS=("${SRC}/persona.py")

coverage run --source="${SRC}" -m pytest tests/ -q
coverage json -o coverage.json -q
python3 scripts/crap.py --coverage coverage.json --threshold "${THRESHOLD}" "${TARGETS[@]}"
