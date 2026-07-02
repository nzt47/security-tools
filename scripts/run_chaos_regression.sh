#!/usr/bin/env bash
# =============================================================================
# Chaos regression test script (Linux/Mac/CI)
#
# Wraps tests/chaos/ with three modes: quick / full / ci.
# Generates JUnit XML report and structured logs.
#
# Usage:
#   ./scripts/run_chaos_regression.sh --mode quick
#   ./scripts/run_chaos_regression.sh --mode full
#   ./scripts/run_chaos_regression.sh --mode ci
#
# CRITICAL: --override-ini=testpaths= required, else pytest.ini testpaths=tests
# overrides the tests/chaos/ arg and collects the entire tests/ tree.
# =============================================================================

set -euo pipefail

MODE="quick"
VERBOSE=true

while [[ $# -gt 0 ]]; do
    case "$1" in
        --mode)
            MODE="$2"
            shift 2
            ;;
        --mode=*)
            MODE="${1#*=}"
            shift
            ;;
        --quiet)
            VERBOSE=false
            shift
            ;;
        -h|--help)
            echo "Usage: $0 --mode {quick|full|ci} [--quiet]"
            exit 0
            ;;
        *)
            echo "ERROR: Unknown argument: $1"
            exit 1
            ;;
    esac
done

if [[ ! "$MODE" =~ ^(quick|full|ci)$ ]]; then
    echo "ERROR: Invalid mode '$MODE'. Must be one of: quick, full, ci"
    exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="${REPO_ROOT}/logs"
mkdir -p "${LOG_DIR}"

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
LOG_FILE="${LOG_DIR}/chaos_regression_${TIMESTAMP}.log"
JUNIT_FILE="${LOG_DIR}/chaos_report_${TIMESTAMP}.xml"

START_TIME=$(date +%s)
START_TIME_FMT=$(date '+%Y-%m-%d %H:%M:%S')

echo "========== Chaos Regression Test =========="
echo "Mode:        ${MODE}"
echo "Start time:  ${START_TIME_FMT}"
echo "Log file:    ${LOG_FILE}"
echo "JUnit report: ${JUNIT_FILE}"
echo ""

if command -v python >/dev/null 2>&1; then
    PY_CMD="python"
elif command -v python3 >/dev/null 2>&1; then
    PY_CMD="python3"
else
    echo "FATAL: Neither python nor python3 found in PATH"
    exit 127
fi

PYTEST_ARGS=("tests/chaos/")

case "${MODE}" in
    quick)
        echo "=== Mode: quick (core chaos tests only, ~15s) ==="
        PYTEST_ARGS+=(-v --tb=short -p no:cacheprovider)
        ;;
    full)
        echo "=== Mode: full (including slow tests, ~70s) ==="
        PYTEST_ARGS+=(-v --tb=short --runslow -p no:cacheprovider)
        ;;
    ci)
        echo "=== Mode: ci (simulate GitHub Actions chaos-tests job, scope=chaos-and-p2) ==="
        PYTEST_ARGS+=("tests/unit/test_impact_analysis_cache.py" -v --tb=short -m "chaos or p2" -p no:cacheprovider)
        ;;
esac

PYTEST_ARGS+=(--junitxml="${JUNIT_FILE}" -o junit_logging=all --override-ini=testpaths=)

if [[ "${VERBOSE}" == "true" ]]; then
    echo "=== Pytest args: ${PYTEST_ARGS[*]} ==="
    echo ""
fi

EXIT_CODE=0
"${PY_CMD}" -m pytest "${PYTEST_ARGS[@]}" 2>&1 | tee "${LOG_FILE}" || EXIT_CODE=${PIPESTATUS[0]}

END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))
END_TIME_FMT=$(date '+%Y-%m-%d %H:%M:%S')

echo ""
echo "========== Regression Test Summary =========="
echo "Mode:        ${MODE}"
echo "Duration:    ${DURATION}s"
echo "Exit code:   ${EXIT_CODE}"
echo "Log file:    ${LOG_FILE}"
echo "JUnit report: ${JUNIT_FILE}"
echo "============================================="
echo ""
echo "Tip: Filter structured logs by module:"
echo "  grep '\[CB_CHAOS\]'      ${LOG_FILE}  # Circuit Breaker"
echo "  grep '\[RL_CHAOS\]'      ${LOG_FILE}  # Rate Limiter"
echo "  grep '\[DEGRADE_CHAOS\]' ${LOG_FILE}  # Degradation"
echo "  grep '\[DR_CHAOS\]'      ${LOG_FILE}  # Disaster Recovery"

exit ${EXIT_CODE}
