#!/usr/bin/env bash
# One-command validation. A convenience layer over the real tier tests — it
# must never weaken or replace official CI (playbook 02 section 4.3).
set -euo pipefail
cd "$(dirname "$0")/.."

BASE="${1:-origin/main}"
echo "=== impact ==="
./scripts/detect-impact.sh "$BASE" || true

echo
echo "=== boundary tests (always run) ==="
python3 -m pytest tests/boundary -q

echo
echo "=== unit tests ==="
python3 -m pytest tests/unit -q

echo
echo "=== database integration ==="
if [ -n "${TEST_DATABASE_URL:-}" ]; then
  python3 -m pytest tests/integration -q
else
  echo "SKIPPED — TEST_DATABASE_URL unset. Database integration is NOT_VERIFIED."
fi

echo
echo "REMINDER: CI PASS is not Feature Complete. Phase closure needs runtime"
echo "business acceptance in the target environment (CLAUDE.md section 9)."
