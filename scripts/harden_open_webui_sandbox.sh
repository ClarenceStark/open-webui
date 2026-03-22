#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
WORKDIR="${OPEN_WEBUI_SANDBOX_WORKDIR:-$HOME/.open-webui/workdir}"

log() {
  printf '[harden-sandbox] %s\n' "$1"
}

log "初始化最小权限工作目录"
"$REPO_ROOT/scripts/setup_open_webui_workdir.sh" "$WORKDIR"

log "停止当前裸跑 sandbox worker"
pkill -f 'uvicorn sandbox_worker.app:app' || true
sleep 1

log "启动容器化 sandbox worker"
"$REPO_ROOT/scripts/start_sandbox_container.sh"

log "最小环境重启 Open WebUI 主后端"
"$REPO_ROOT/scripts/restart_open_webui_minenv.sh"

log "加固切换完成"
