#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
HOUR="${1:-0}"
MINUTE="${2:-10}"
UV_BIN="$(command -v uv)"
MARKER="# productivity-collect"

mkdir -p "$PROJECT_DIR/logs"
LINE="$MINUTE $HOUR * * * cd \"$PROJECT_DIR\" && \"$UV_BIN\" run productivity collect --date yesterday >> \"$PROJECT_DIR/logs/cron.log\" 2>&1 $MARKER"

{ crontab -l 2>/dev/null | grep -v "$MARKER" || true; echo "$LINE"; } | crontab -
echo "등록 완료:"
crontab -l | grep "$MARKER"
