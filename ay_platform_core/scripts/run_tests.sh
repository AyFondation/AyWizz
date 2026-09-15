#!/usr/bin/env bash
# =============================================================================
# File: run_tests.sh
# Version: 3
# Path: ay_platform_core/scripts/run_tests.sh
# Description: Orchestrates the full test suite for ay_platform_core and
#              persists all artifacts under
#              ay_platform_core/reports/YYYY-MM-DD_HHMM_<tag>/, refreshing
#              the reports/latest symlink on success.
#
#              The script resolves its own location to cd into the
#              sub-project root; it is safe to invoke from anywhere.
#
#              v3 (2026-09-15): the three tools are invoked as `python -m`
#              instead of as bare console scripts. A console script in
#              /usr/local/bin and its module in the USER site-packages can
#              be different versions — the user site takes sys.path
#              precedence, so `pip install --user` upgrades the module while
#              the old launcher stays on PATH. That skew took down the
#              dependency-refresh run with
#                ImportError: cannot import name '_console_main'
#                             from '_pytest.config'
#              reported by this script as "pytest: FAIL", i.e. as a test
#              failure, when in fact NOT ONE TEST HAD RUN. `python -m`
#              resolves the module through the same interpreter that will
#              import the code under test, so the two can never diverge.
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

# Check required tools. `python` is the only PATH lookup that remains
# meaningful: the other three are invoked as modules of THIS interpreter
# (see v3 note), so their importability, not their presence on PATH, is
# what matters.
if ! command -v python >/dev/null 2>&1; then
    echo "ERROR: required command 'python' not found on PATH" >&2
    exit 4
fi
for mod in pytest ruff mypy; do
    if ! python -c "import $mod" >/dev/null 2>&1; then
        echo "ERROR: required module '$mod' not importable by $(command -v python)" >&2
        echo "       install it with: pip install -e .[all]" >&2
        exit 4
    fi
done

# -----------------------------------------------------------------------------
# Ruff
# -----------------------------------------------------------------------------
echo "==> Running ruff check"
python -m ruff check src tests > "$REPORT_DIR/ruff.txt" 2>&1
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
python -m mypy src tests > "$REPORT_DIR/mypy.txt" 2>&1
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
python -m pytest \
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
