#!/usr/bin/env bash
# 매일 00:10 에 전날 데이터를 수집·적재하는 Cron 작업을 등록한다. (이미 있으면 교체)
# 사용: ./scripts/install_cron.sh [시 분]   예) ./scripts/install_cron.sh 0 10
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
