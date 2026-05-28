#!/usr/bin/env bash
# =============================================================================
# File: run_tests.sh
# Version: 2
# Path: ay_platform_core/scripts/run_tests.sh
# Description: Orchestrates the full test suite for ay_platform_core and
#              persists all artifacts under
#              ay_platform_core/reports/YYYY-MM-DD_HHMM_<tag>/, refreshing
#              the reports/latest symlink on success.
#
#              The script resolves its own location to cd into the
#              sub-project root; it is safe to invoke from anywhere.
#
# Usage:
#   ay_platform_core/scripts/run_tests.sh [tag] [pytest-args...]
#
# Examples:
#   ./scripts/run_tests.sh                    # Full suite, default tag
#   ./scripts/run_tests.sh c2-auth            # Tag the run with a component
#   ./scripts/run_tests.sh c2-auth -k jwt     # Filter tests via pytest
#
# Exit codes:
#   0: all stages passed
#   1: pytest failures
#   2: mypy failures
#   3: ruff failures
#   4: environment error (missing dependency, etc.)
# =============================================================================

set -uo pipefail

# -----------------------------------------------------------------------------
# Setup: cd into ay_platform_core/ (the sub-project root)
# -----------------------------------------------------------------------------
SUB_PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SUB_PROJECT_ROOT"

TAG="${1:-run}"
shift || true
PYTEST_EXTRA_ARGS=("$@")

TIMESTAMP="$(date +%Y-%m-%d_%H%M)"
REPORT_DIR="reports/${TIMESTAMP}_${TAG}"
mkdir -p "$REPORT_DIR"

echo "==> Sub-project: $SUB_PROJECT_ROOT"
echo "==> Report directory: $REPORT_DIR"

# Check required tools
for cmd in python pytest ruff mypy; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        echo "ERROR: required command '$cmd' not found on PATH" >&2
        exit 4
    fi
done

# -----------------------------------------------------------------------------
# Ruff
# -----------------------------------------------------------------------------
echo "==> Running ruff check"
ruff check src tests > "$REPORT_DIR/ruff.txt" 2>&1
RUFF_EXIT=$?
if [[ $RUFF_EXIT -ne 0 ]]; then
    echo "    ruff: FAIL (see $REPORT_DIR/ruff.txt)"
else
    echo "    ruff: OK"
fi

# -----------------------------------------------------------------------------
# Mypy
# -----------------------------------------------------------------------------
echo "==> Running mypy"
mypy src tests > "$REPORT_DIR/mypy.txt" 2>&1
MYPY_EXIT=$?
if [[ $MYPY_EXIT -ne 0 ]]; then
    echo "    mypy: FAIL (see $REPORT_DIR/mypy.txt)"
else
    echo "    mypy: OK"
fi

# -----------------------------------------------------------------------------
# Pytest
# -----------------------------------------------------------------------------
echo "==> Running pytest"
pytest \
    --junit-xml="$REPORT_DIR/pytest_junit.xml" \
    --cov=src \
    --cov-report="xml:$REPORT_DIR/coverage.xml" \
    --cov-report="term" \
    "${PYTEST_EXTRA_ARGS[@]}" \
    > "$REPORT_DIR/pytest_summary.txt" 2>&1
PYTEST_EXIT=$?
if [[ $PYTEST_EXIT -ne 0 ]]; then
    echo "    pytest: FAIL (see $REPORT_DIR/pytest_summary.txt)"
else
    echo "    pytest: OK"
fi

# Coverage text summary (best-effort)
if command -v coverage >/dev/null 2>&1; then
    coverage report > "$REPORT_DIR/coverage.txt" 2>&1 || true
fi

# -----------------------------------------------------------------------------
# Metadata
# -----------------------------------------------------------------------------
COMMIT_HASH="$(git rev-parse --short HEAD 2>/dev/null || echo 'no-git')"
cat > "$REPORT_DIR/metadata.json" <<EOF
{
  "sub_project": "ay_platform_core",
  "tag": "${TAG}",
  "timestamp": "${TIMESTAMP}",
  "commit": "${COMMIT_HASH}",
  "exit_codes": {
    "ruff": ${RUFF_EXIT},
    "mypy": ${MYPY_EXIT},
    "pytest": ${PYTEST_EXIT}
  }
}
EOF

# -----------------------------------------------------------------------------
# Refresh 'latest' symlink
# -----------------------------------------------------------------------------
ln -sfn "${TIMESTAMP}_${TAG}" "reports/latest"
echo "==> reports/latest -> ${TIMESTAMP}_${TAG}"

# -----------------------------------------------------------------------------
# Exit with first non-zero code (pytest > mypy > ruff priority)
# -----------------------------------------------------------------------------
if [[ $PYTEST_EXIT -ne 0 ]]; then exit 1; fi
if [[ $MYPY_EXIT   -ne 0 ]]; then exit 2; fi
if [[ $RUFF_EXIT   -ne 0 ]]; then exit 3; fi

echo "==> All stages OK"
exit 0
