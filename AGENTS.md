# Repository Guidelines

## Project Structure & Module Organization

`src/` contains the SvelteKit frontend: route entry points live in `src/routes`, while shared UI, API clients, stores, and utilities live in `src/lib`. `backend/open_webui/` contains the FastAPI backend, including routers, models, retrieval, tools, and this fork's Codex-specific integrations. Runtime data is stored under `backend/data/`. Frontend tests usually sit next to the code they cover, such as `src/lib/utils/index.test.ts`; backend tests live under `backend/open_webui/test/`. Treat `build/` as generated output and regenerate it after frontend changes.

## Build, Test, and Development Commands

Use `npm install` for frontend dependencies and `/Users/clarencestark/code/open-webui/.venv/bin/pip install -r backend/requirements.txt` for backend dependencies. `npm run dev -- --host` starts the Vite frontend on `5173`. `npm run build` produces the production frontend bundle in `build/`.

Default local startup is Codex-only: start just the Open WebUI backend with `cd backend && /Users/clarencestark/code/open-webui/.venv/bin/python -m uvicorn open_webui.main:app --host 127.0.0.1 --port 8080 --workers 1`. This repo's built-in Codex backend runs through the backend process and local Codex app server integration, so sandbox worker and Docker are not required for normal Codex usage.

For persistent local hosting on this macOS machine, do not default to `pm2`. The current persistent setup uses the user LaunchAgent `com.clarencestark.open-webui` at `/Users/clarencestark/Library/LaunchAgents/com.clarencestark.open-webui.plist`, which runs `/Users/clarencestark/code/open-webui/ops/open-webui-launchd.sh`. That wrapper invokes `/Users/clarencestark/code/open-webui/.venv/bin/python -m open_webui serve --host 127.0.0.1 --port 8080` from `/Users/clarencestark/code/open-webui/backend` with `UVICORN_WORKERS=4`.

Important: `launchd` does not inherit shell exports from `~/.zshrc` or terminal sessions. If the backend needs secrets such as `AZURE_OPENAI_API_KEY_1` or `OPENAI_API_KEY_PLAIN`, start the LaunchAgent through the wrapper script `/Users/clarencestark/code/open-webui/ops/open-webui-launchd.sh`, which invokes `/bin/zsh -lic` before the repository startup script so the exported shell environment is available to the backend process. If the app reports missing environment variables after reboot or relaunch, first verify the LaunchAgent still points to that wrapper script rather than directly launching Python.

For public access behind Cloudflare Tunnel, do not use a foreground `uvicorn --workers 1` process as the steady-state runtime. Keep the single-worker command for local debugging only. The public tunnel should use the persistent LaunchAgent wrapper with `UVICORN_WORKERS=4` to reduce the chance that one blocked request stalls the entire site and triggers Cloudflare `524` host timeouts.

Use these commands for persistent-service operations:

- `launchctl print gui/$(id -u)/com.clarencestark.open-webui`
- `launchctl kickstart -k gui/$(id -u)/com.clarencestark.open-webui`
- `launchctl bootout gui/$(id -u)/com.clarencestark.open-webui`
- `launchctl bootstrap gui/$(id -u) /Users/clarencestark/Library/LaunchAgents/com.clarencestark.open-webui.plist`
- `tail -f /Users/clarencestark/Library/Logs/open-webui/stdout.log`
- `tail -f /Users/clarencestark/Library/Logs/open-webui/stderr.log`
- `curl http://127.0.0.1:8080/health`

This LaunchAgent starts automatically after the user logs in and keeps the service alive if it exits. Unless the task explicitly requires LAN/public exposure, keep the listener on `127.0.0.1`. If true boot-after-restart without user login is required, that is a separate system-level `LaunchDaemon` task and requires `sudo`.

Unless a task explicitly states otherwise, discussions about features, bugs, behavior, architecture, or validation in this repository should default to the branch/path where Codex app server is the backend. Do not default to the legacy non-Codex chat backend when interpreting user reports or implementing fixes.

Only start the sandbox worker with `docker compose -f docker-compose.sandbox.yaml up -d --build` when the task explicitly requires Open WebUI's native terminal/file tools on `127.0.0.1:8765`. Unless sandbox or Docker is explicitly requested, do not start them.

Whenever a task changes backend code and validation depends on that new backend code taking effect, proactively restart the backend before validating. Do not leave backend restart as an implicit/manual follow-up step.

## Coding Style & Naming Conventions

Frontend code follows Prettier and ESLint; run `npm run format` and `npm run lint`. Keep Svelte components in `PascalCase.svelte` and TypeScript helpers in `camelCase.ts`. Python follows Black formatting and 4-space indentation; use `snake_case` for modules, functions, and variables. Keep customizations isolated and focused; do not place fork-specific work directly on `main`.

## Testing Guidelines

Run `npm run check`, `npm run test:frontend`, and `npm run lint` for frontend-facing changes. Run backend tests with `/Users/clarencestark/code/open-webui/.venv/bin/python -m pytest backend/open_webui/test`. Name frontend tests `*.test.ts` or `*.spec.ts`; name backend tests `test_*.py`. For UI changes, always rerun `npm run build` before handoff.

## Commit & Pull Request Guidelines

This fork uses `main` to track `upstream/main` and `codex/custom-main` for local customization. Create short-lived branches such as `codex/<task-name>` for larger changes. Recent commits favor short, imperative subjects, often in Chinese, for example `修复 Codex 多段 assistant 消息的同步落库与流式渲染`. Pull requests should include scope, impacted areas, validation commands, linked issues, and screenshots or GIFs for visible UI changes.

## Security & Configuration Tips

Do not commit secrets, `backend/data/` contents, or files from `~/.open-webui/workdir/`. Use the project `.venv` instead of system Python. Treat Codex-only startup as the default local mode. If a task explicitly depends on Open WebUI native terminal/file tools and they fail locally, verify the sandbox container is running on `127.0.0.1:8765` and that Open WebUI has a terminal server entry pointing to `sandbox`.
