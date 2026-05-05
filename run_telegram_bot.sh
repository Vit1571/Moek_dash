#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

BUNDLED_PYTHON="/Users/vitaliigudelev/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"

if [ -x "$BUNDLED_PYTHON" ]; then
  "$BUNDLED_PYTHON" telegram_bot.py
else
  python3 telegram_bot.py
fi
