# Codex-Only 运维 Runbook

适用范围：当前这套 `Open WebUI backend + Codex app server + Cloudflare HTTPS + Cloudflare Access 邮箱访问控制` 部署。

不适用范围：本手册不包含 sandbox worker、Docker sandbox、`127.0.0.1:8765` 终端文件工具链路。

## 1. 当前架构

- 用户流量先到 Cloudflare。
- Cloudflare Access 先做邮箱访问控制。
- Access 放行后，请求回源到本机 `Open WebUI` 后端。
- 本机后端监听 `127.0.0.1:8080`。
- Codex 能力通过后端内置的 Codex 集成提供，不需要单独启动 sandbox Docker。

最小链路：

```text
Browser
  -> Cloudflare HTTPS
  -> Cloudflare Access
  -> Open WebUI backend (127.0.0.1:8080)
  -> built-in Codex integration
```

## 2. 关键路径

- 仓库根目录：`/Users/clarencestark/code/open-webui`
- Python 解释器：`/Users/clarencestark/code/open-webui/.venv/bin/python`
- 后端工作目录：`/Users/clarencestark/code/open-webui/backend`
- SQLite 数据库：`/Users/clarencestark/code/open-webui/backend/data/webui.db`
- Open WebUI 专用 Codex Home：`~/.codex-openwebui/`
- 默认 Codex 工作区根目录：`~/.open-webui/codex/`

说明：

- `~/.codex-openwebui/config.toml` 是 Open WebUI 启动后自动引导出来的 Codex 专用配置。
- `~/.open-webui/codex/` 下是按聊天拆分的工作目录，通常不建议纳入常规备份。

## 3. 启动、停止、重启

### 3.1 前台启动

在排障或临时维护时，优先用前台启动：

```bash
cd /Users/clarencestark/code/open-webui/backend
/Users/clarencestark/code/open-webui/.venv/bin/python -m uvicorn open_webui.main:app \
  --host 127.0.0.1 \
  --port 8080 \
  --workers 1
```

### 3.2 后台启动并落日志

如果你不想一直挂着前台终端，可以把日志落到仓库内的 `.logs/`：

```bash
mkdir -p /Users/clarencestark/code/open-webui/.logs
cd /Users/clarencestark/code/open-webui/backend
nohup /Users/clarencestark/code/open-webui/.venv/bin/python -m uvicorn open_webui.main:app \
  --host 127.0.0.1 \
  --port 8080 \
  --workers 1 \
  > /Users/clarencestark/code/open-webui/.logs/open-webui-backend.log 2>&1 &
echo $! > /Users/clarencestark/code/open-webui/.logs/open-webui-backend.pid
```

查看实时日志：

```bash
tail -f /Users/clarencestark/code/open-webui/.logs/open-webui-backend.log
```

### 3.3 检查是否已启动

```bash
lsof -nP -iTCP:8080 -sTCP:LISTEN
curl -sS http://127.0.0.1:8080/health
curl -sS http://127.0.0.1:8080/health/db
```

期望结果：

- `lsof` 能看到 Python/uvicorn 监听 `127.0.0.1:8080`
- `/health` 返回 `{"status":true}`
- `/health/db` 返回 `{"status":true}`

### 3.4 停止后端

如果当前进程在前台，直接 `Ctrl+C`。

如果后端已在后台运行，先定位 PID：

```bash
lsof -tiTCP:8080 -sTCP:LISTEN
```

然后优雅停止：

```bash
kill <PID>
```

确认端口已释放：

```bash
lsof -nP -iTCP:8080 -sTCP:LISTEN
```

### 3.5 重启后端

当出现以下情况时，必须重启后端后再验收：

- 修改了 `backend/open_webui/**`
- 修改了后端读取的环境变量
- 修改了 Codex/Open WebUI 配置文件

标准动作：

1. 停止当前 `8080` 进程
2. 重新执行启动命令
3. 再跑 `/health` 与 `/health/db`

如果你使用的是后台日志方案，重启时顺手清理旧 PID 文件：

```bash
rm -f /Users/clarencestark/code/open-webui/.logs/open-webui-backend.pid
```

## 4. 每日巡检

最小巡检命令：

```bash
curl -sS http://127.0.0.1:8080/health
curl -sS http://127.0.0.1:8080/health/db
lsof -nP -iTCP:8080 -sTCP:LISTEN
```

公网链路巡检顺序：

1. 本机 `127.0.0.1:8080/health`
2. Cloudflare Tunnel 状态正常
3. 公网域名可访问
4. 未授权邮箱仍被 Cloudflare Access 拦截
5. 授权邮箱可进入站点并真实发出一条消息

这 5 步缺一不可。不要只测“页面能打开”。

## 5. 发布后验收

每次改配置、升级代码、替换模型、调整访问控制后，至少做下面这组验收：

### 5.1 本机健康检查

```bash
curl -sS http://127.0.0.1:8080/health
curl -sS http://127.0.0.1:8080/health/db
```

### 5.2 公网入口检查

- 用未授权邮箱访问一次，确认仍被 Cloudflare Access 拦截。
- 用授权邮箱访问一次，确认能进入站点。

### 5.3 应用功能检查

- 登录后打开聊天页。
- 选择一个实际可用模型。
- 发一条最简单的消息，例如 `hi`。
- 确认不是只有模型列表正常，而是真正拿到一条成功回复。

### 5.4 如有前端改动

如果本次变更涉及 `src/**`：

```bash
cd /Users/clarencestark/code/open-webui
npm run build
```

前端构建成功后，再重启后端并重新验收。

## 6. 备份策略

至少备份下面两类数据：

- 数据库：`/Users/clarencestark/code/open-webui/backend/data/webui.db`
- 配置：环境变量来源文件、Cloudflare 配置记录、`~/.codex-openwebui/config.toml`

手工备份数据库：

```bash
mkdir -p /Users/clarencestark/code/open-webui/backups
cp /Users/clarencestark/code/open-webui/backend/data/webui.db \
  /Users/clarencestark/code/open-webui/backups/webui-$(date +%Y%m%d-%H%M%S).db
```

建议在以下操作前先备份：

- 拉新代码前
- 升级依赖前
- 修改数据库相关逻辑前
- 批量创建/修改用户前
- 调整模型访问权限前

### 6.1 恢复数据库

恢复前先停后端。

```bash
cp /Users/clarencestark/code/open-webui/backups/<backup-file>.db \
  /Users/clarencestark/code/open-webui/backend/data/webui.db
```

然后重启后端，再跑：

```bash
curl -sS http://127.0.0.1:8080/health
curl -sS http://127.0.0.1:8080/health/db
```

## 7. 升级与回滚

### 7.1 升级前

```bash
cd /Users/clarencestark/code/open-webui
git status --short
git branch --show-current
```

要求：

- 工作区干净，或者你明确知道哪些本地修改需要保留。
- 先备份 `webui.db`。

### 7.2 升级代码

示例：

```bash
cd /Users/clarencestark/code/open-webui
git pull --ff-only
```

如果依赖发生变化，再执行：

```bash
cd /Users/clarencestark/code/open-webui
npm install
/Users/clarencestark/code/open-webui/.venv/bin/pip install -r backend/requirements.txt
```

如果前端代码有变化，再执行：

```bash
cd /Users/clarencestark/code/open-webui
npm run build
```

最后重启后端并跑完整验收链。

### 7.3 快速回滚

如果升级后出现功能回退：

1. 停后端
2. 回到上一个稳定 commit
3. 恢复数据库备份（如果升级过程动过数据）
4. 重启后端
5. 重新跑本机健康检查和公网验收

示例：

```bash
cd /Users/clarencestark/code/open-webui
git log --oneline -n 5
git checkout <stable-commit>
```

注意：只有在你明确知道本地改动不需要保留时，才做更激进的 git 操作。

## 8. 常见故障处理

### 8.1 8080 不通

先查监听：

```bash
lsof -nP -iTCP:8080 -sTCP:LISTEN
```

如果没有监听，重新启动后端。

如果有监听但 `/health` 超时，前台重启后端并直接看报错。

如果是后台跑的，再加一步看日志：

```bash
tail -n 200 /Users/clarencestark/code/open-webui/.logs/open-webui-backend.log
```

### 8.2 `/health` 正常但 `/health/db` 失败

优先怀疑：

- `webui.db` 损坏
- SQLite 文件权限异常
- 进程未正确释放旧连接

先备份当前数据库副本，再做恢复或替换。

### 8.3 公网打不开，但本地 8080 正常

按顺序排查：

1. Cloudflare Tunnel 是否在线
2. 域名回源配置是否正确
3. Cloudflare Access 策略是否误拦截

不要先怀疑 Open WebUI 本体。

### 8.4 公网能打开，但未授权用户也能进

这是高优先级安全事件。先处理 Cloudflare Access，不要继续暴露。

至少确认：

- Access policy 仍绑定目标应用
- 允许规则仍是指定邮箱或邮箱域
- 没有意外添加 bypass / allow all 规则

### 8.5 页面能进，但聊天失败

先区分是哪一层：

- 模型列表为空
- 模型列表正常，但发送时报错
- 消息发出后长时间卡住

最短思路：

1. 先验证本机 `/health` 和 `/health/db`
2. 再用授权账号登录后实际发送一条最小消息
3. 必要时查看 `backend/data/webui.db` 中聊天记录和状态

## 9. 数据库检查

在线实例可能会锁住 SQLite。排查时优先复制到 `/tmp` 再查：

```bash
cp /Users/clarencestark/code/open-webui/backend/data/webui.db /tmp/webui.db
sqlite3 /tmp/webui.db '.tables'
```

例如查看用户：

```bash
sqlite3 /tmp/webui.db "select id,email,role from user order by created_at desc limit 20;"
```

例如查看最近消息：

```bash
sqlite3 /tmp/webui.db "select id,chat_id,role,substr(content,1,80) from chat_message order by created_at desc limit 20;"
```

说明：

- 线上活跃时直接查询原库，容易碰到 `database is locked`
- 复制快照后查询更稳妥

## 10. 建议保留的固定命令

```bash
# 健康检查
curl -sS http://127.0.0.1:8080/health
curl -sS http://127.0.0.1:8080/health/db

# 监听检查
lsof -nP -iTCP:8080 -sTCP:LISTEN

# 后台日志
tail -f /Users/clarencestark/code/open-webui/.logs/open-webui-backend.log

# 前台启动
cd /Users/clarencestark/code/open-webui/backend
/Users/clarencestark/code/open-webui/.venv/bin/python -m uvicorn open_webui.main:app --host 127.0.0.1 --port 8080 --workers 1

# 构建前端
cd /Users/clarencestark/code/open-webui
npm run build

# 备份数据库
mkdir -p /Users/clarencestark/code/open-webui/backups
cp /Users/clarencestark/code/open-webui/backend/data/webui.db /Users/clarencestark/code/open-webui/backups/webui-$(date +%Y%m%d-%H%M%S).db
```

## 11. 变更记录建议

每次做下面这些操作时，建议至少记一条简短操作记录：

- 修改 Cloudflare Access 规则
- 升级 Open WebUI 代码
- 改模型供应商或默认模型
- 批量增删用户
- 恢复数据库

最少记录这 4 项：

- 时间
- 操作人
- 改了什么
- 如何回滚

## 12. 本手册的边界

本手册默认以下前提已经成立：

- HTTPS 已由 Cloudflare 提供
- Cloudflare Access 邮箱访问控制已配置完成
- 你当前仅使用 Codex app server 路径，不使用 Open WebUI 的 sandbox worker

如果未来你重新启用 Open WebUI 原生终端/文件工具，再单独补一份 sandbox 运维手册，不要把两套链路混在一起。
