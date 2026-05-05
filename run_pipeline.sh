#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

SKIP_MAIL=0
for arg in "$@"; do
  case "$arg" in
    --help|-h)
      echo "Usage: ./run_pipeline.sh [--no-mail]"
      echo "  --no-mail   rebuild dashboard from existing mailru_pdfs without IMAP download"
      exit 0
      ;;
    --no-mail)
      SKIP_MAIL=1
      ;;
    *)
      echo "Unknown argument: $arg"
      echo "Usage: ./run_pipeline.sh [--no-mail]"
      exit 1
      ;;
  esac
done

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

if [[ "$SKIP_MAIL" -eq 0 ]]; then
  "$PYTHON_BIN" mail_collector.py
fi

"$PYTHON_BIN" build_dashboard.py --pdf-dir mailru_pdfs --history-dir reports_history

echo "Dashboard: $(pwd)/dashboard.html"
