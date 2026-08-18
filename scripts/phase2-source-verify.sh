#!/usr/bin/env bash
# Phase 2 entry point over the shared deterministic source validation harness.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export CHANN_SOURCE_PHASE=2
exec "$ROOT/scripts/phase1-source-verify.sh"
