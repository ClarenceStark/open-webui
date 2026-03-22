#!/usr/bin/env bash

set -euo pipefail

WORKDIR="${1:-$HOME/.open-webui/workdir}"

log() {
  printf '[setup-workdir] %s\n' "$1"
}

log "准备工作目录: $WORKDIR"
mkdir -p \
  "$WORKDIR"/{projects,uploads,tmp,outputs,bin,logs,cache,artifacts} \
  "$WORKDIR/venvs"

cat > "$WORKDIR/README.md" <<'EOF'
# Open WebUI Sandbox Workdir

这是 Open WebUI 终端工具的受限工作目录。

- `projects/`: 需要让模型操作的项目副本
- `uploads/`: 聊天附件或手工上传文件
- `outputs/`: 生成的结果文件
- `artifacts/`: 需要在 UI 中查看或下载的工件
- `venvs/default`: 容器内预装的通用 Python 环境链接
- `venvs/data`: 容器内预装的数据处理 Python 环境链接

常用命令:

```bash
source /workspace/venvs/default/bin/activate
source /workspace/venvs/data/bin/activate
```
EOF

log "工作目录初始化完成"
