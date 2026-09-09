#!/usr/bin/env bash
# Offline verification only. Does not push, build cloud images or deploy.
set -euo pipefail
cd "$(dirname "$0")/../.."
PYTHON_BIN="${PYTHON_BIN:-python}"
"$PYTHON_BIN" -m pytest tests/unit tests/boundary -q \
  --deselect tests/unit/test_smartbrowz_pdf_renderer.py::TestSmartBrowzPdfRenderer::test_fake_credentials_are_rejected_by_the_real_zoho_endpoint \
  --deselect tests/unit/test_smartbrowz_pdf_renderer.py::TestSmartBrowzPdfRenderer::test_verify_connection_surfaces_the_same_clear_errors
"$PYTHON_BIN" scripts/agent-test/run.py --json
for script in scripts/dev/check-*.py scripts/dev/simulate-day.py scripts/dev/simulate-edge-cases.py scripts/dev/simulate-phrasings.py; do
  "$PYTHON_BIN" "$script"
done
"$PYTHON_BIN" scripts/agent-test/evaluate-model.py
node scripts/agent-test/check-dashboard-home.cjs
(cd presentation && npm run typecheck && npm run build)
echo 'Offline checks finished. Review findings against baseline; real-model, DB, UI and runtime acceptance are still required.'
