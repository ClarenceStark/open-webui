# Open WebUI launchd 常驻说明

当前机器使用 macOS 原生 `launchd` 常驻这个仓库的后端进程，而不是 `pm2`。

原因：

- `pm2 startup` 在这台机器上需要 `sudo`
- 当前账号不是免密 sudo
- `launchd` 是 macOS 原生守护方案，用户态即可稳定常驻

服务标签：

- `com.clarencestark.open-webui`

配置文件：

- `~/Library/LaunchAgents/com.clarencestark.open-webui.plist`

当前常驻启动参数：

- `HOST=127.0.0.1`
- `PORT=8080`
- `UVICORN_WORKERS=4`
- 入口脚本：`/Users/clarencestark/code/open-webui/ops/open-webui-launchd.sh`
- 实际执行：`/Users/clarencestark/code/open-webui/.venv/bin/python -m open_webui serve --host 127.0.0.1 --port 8080`

常用命令：

```bash
launchctl print gui/$(id -u)/com.clarencestark.open-webui
launchctl kickstart -k gui/$(id -u)/com.clarencestark.open-webui
launchctl bootout gui/$(id -u)/com.clarencestark.open-webui
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.clarencestark.open-webui.plist
tail -f ~/Library/Logs/open-webui/stdout.log
tail -f ~/Library/Logs/open-webui/stderr.log
curl http://127.0.0.1:8080/health
```

当前默认监听：

- `127.0.0.1:8080`

说明：

- 这是更安全的默认值，只允许本机访问，公网通过 Cloudflare Tunnel 转发
- 公网场景不要长期使用前台 `uvicorn --workers 1`，否则单个阻塞请求可能导致整站卡住并触发 Cloudflare `524`
