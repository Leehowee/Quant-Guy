#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "Usage: bash bin/run_scheduled.sh industry|etf" >&2
    exit 2
fi

case "$1" in
    industry)
        workflow="industry"
        entrypoint="run_daily.py"
        ;;
    etf)
        workflow="etf"
        entrypoint="run_etf_flow_daily.py"
        ;;
    *)
        echo "Unknown workflow '$1'; expected industry or etf" >&2
        exit 2
        ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ -n "${PYTHON_BIN:-}" ]]; then
    PYTHON_CMD="$PYTHON_BIN"
elif [[ -x "$PROJECT_ROOT/.venv/bin/python" ]]; then
    PYTHON_CMD="$PROJECT_ROOT/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_CMD="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
    PYTHON_CMD="$(command -v python)"
else
    echo "Python was not found. Set PYTHON_BIN or create .venv in the project." >&2
    exit 127
fi

if ! command -v "$PYTHON_CMD" >/dev/null 2>&1; then
    echo "Python executable not found: $PYTHON_CMD" >&2
    exit 127
fi

LOG_DIR="$PROJECT_ROOT/data/processed"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/${workflow}_scheduled_$(date +%Y%m%d_%H%M%S).log"
exec >>"$LOG_FILE" 2>&1

printf '[%s] starting %s workflow\n' "$(date '+%Y-%m-%d %H:%M:%S %z')" "$workflow"
cd "$PROJECT_ROOT"
exec "$PYTHON_CMD" "$PROJECT_ROOT/bin/$entrypoint" --send
