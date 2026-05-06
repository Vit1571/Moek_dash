#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

LABEL="com.moek.eldis.scheduler"
PLIST_NAME="${LABEL}.plist"
SOURCE_PLIST="launchd/${PLIST_NAME}"
TARGET_DIR="${HOME}/Library/LaunchAgents"
TARGET_PLIST="${TARGET_DIR}/${PLIST_NAME}"
PROJECT_DIR="$(pwd)"

mkdir -p "$TARGET_DIR" logs
sed "s#__PROJECT_DIR__#${PROJECT_DIR}#g" "$SOURCE_PLIST" > "$TARGET_PLIST"

launchctl unload "$TARGET_PLIST" >/dev/null 2>&1 || true
launchctl load "$TARGET_PLIST"
launchctl start "$LABEL" >/dev/null 2>&1 || true

echo "Installed ${LABEL}"
echo "Plist: ${TARGET_PLIST}"
echo "Logs:"
echo "  ${PROJECT_DIR}/logs/eldis_scheduler.out.log"
echo "  ${PROJECT_DIR}/logs/eldis_scheduler.err.log"
