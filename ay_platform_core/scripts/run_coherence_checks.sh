#!/usr/bin/env bash
# =============================================================================
# File: run_coherence_checks.sh
# Version: 1
# Path: ay_platform_core/scripts/run_coherence_checks.sh
# Description: Orchestrates all Coherence-2 (code<->code interface) checks.
#              Runs every standalone Python script under scripts/checks/ and
#              aggregates results. Exits 0 only if every check passes.
#
# Usage (from ay_platform_core/):
#   ./scripts/run_coherence_checks.sh
#
# Exit codes:
#   0: all coherence checks passed
#   1: one or more checks failed
#   4: environment error (python not found)
# =============================================================================

set -uo pipefail

SUB_PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SUB_PROJECT_ROOT"

if ! command -v python >/dev/null 2>&1; then
    echo "ERROR: python not found on PATH" >&2
    exit 4
fi

CHECKS_DIR="scripts/checks"
OVERALL_EXIT=0

echo "==> Coherence checks ($(date +%Y-%m-%d\ %H:%M:%S))"
echo "    Working directory: $SUB_PROJECT_ROOT"
echo ""

for script in \
    check_pydantic_schemas_valid.py \
    check_schema_isolation.py \
    check_router_typing.py \
    check_no_parallel_definitions.py \
    check_canonical_imports.py
do
    echo "--- $script"
    if python "$CHECKS_DIR/$script"; then
        :
    else
        OVERALL_EXIT=1
    fi
    echo ""
done

if [[ $OVERALL_EXIT -eq 0 ]]; then
    echo "==> All coherence checks PASSED"
else
    echo "==> One or more coherence checks FAILED" >&2
fi

exit $OVERALL_EXIT
