#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$REPO_ROOT/docker-compose.sandbox.yaml"

log() {
  printf '[sandbox-container] %s\n' "$1"
}

wait_for_docker() {
  local tries=0
  until docker info >/dev/null 2>&1; do
    tries=$((tries + 1))
    if (( tries > 90 )); then
      log "Docker Desktop 未在预期时间内启动"
      return 1
    fi
    sleep 2
  done
}

log "确保 Docker Desktop 已启动"
open -a Docker >/dev/null 2>&1 || true
wait_for_docker

log "构建并启动受限 sandbox 容器"
docker compose -f "$COMPOSE_FILE" up -d --build

log "当前容器状态"
docker compose -f "$COMPOSE_FILE" ps
