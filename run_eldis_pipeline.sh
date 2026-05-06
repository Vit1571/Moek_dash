#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

PYTHON_BIN="${PYTHON_BIN:-python3}"
CODEX_PYTHON="/Users/vitaliigudelev/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"

if ! "$PYTHON_BIN" -c "import pypdf" >/dev/null 2>&1; then
  if [[ -x "$CODEX_PYTHON" ]]; then
    PYTHON_BIN="$CODEX_PYTHON"
  else
    echo "Python dependency pypdf is missing."
    echo "Run: python3 -m pip install -r requirements.txt"
    exit 1
  fi
fi

"$PYTHON_BIN" eldis_collector.py --collect
"$PYTHON_BIN" build_dashboard.py --reports-json eldis_reports.json --history-dir reports_history

echo "Dashboard: $(pwd)/dashboard.html"
