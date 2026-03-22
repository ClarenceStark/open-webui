#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$REPO_ROOT/backend"
PYTHON_BIN="$REPO_ROOT/.venv/bin/python"
LOG_DIR="$REPO_ROOT/.logs"
LOG_FILE="$LOG_DIR/open-webui-backend.log"

mkdir -p "$LOG_DIR"

log() {
  printf '[restart-backend] %s\n' "$1"
}

log "停止现有 Open WebUI 进程"
pkill -f 'uvicorn open_webui.main:app' || true
sleep 1

log "以最小环境变量重启 Open WebUI"
(
  cd "$BACKEND_DIR"
  nohup env -i \
    HOME="$HOME" \
    USER="${USER:-clarencestark}" \
    LOGNAME="${LOGNAME:-${USER:-clarencestark}}" \
    SHELL="${SHELL:-/bin/zsh}" \
    PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin" \
    LANG="C.UTF-8" \
    LC_ALL="C.UTF-8" \
    TMPDIR="${TMPDIR:-/tmp}" \
    "$PYTHON_BIN" -m uvicorn open_webui.main:app \
      --host 127.0.0.1 \
      --port 8080 \
      --forwarded-allow-ips '*' \
      --workers 1 \
      >"$LOG_FILE" 2>&1 < /dev/null &
)

sleep 3
log "后端日志: $LOG_FILE"
