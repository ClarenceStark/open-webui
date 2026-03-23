# AGENTS.md

## Project Git Workflow

This repository uses a fork-based customization workflow. Keep the branch roles strict.

- `origin` must point to the personal fork: `https://github.com/ClarenceStark/open-webui`
- `upstream` must point to the official repo: `https://github.com/open-webui/open-webui`
- `main` is the official tracking branch and should track `upstream/main`
- `codex/custom-main` is the customization branch and should track `origin/codex/custom-main`

## Branch Rules

- Do not put product-specific or business-specific changes directly on `main`
- Do not commit custom features, experiments, or local deployment tweaks on `main`
- Use `codex/custom-main` as the default base branch for custom development and deployment
- Create short-lived feature branches from `codex/custom-main` when a task is large enough to isolate
- Merge finished custom work back into `codex/custom-main`

## Official Update Procedure

When pulling official updates, use this sequence:

```bash
cd ~/code/open-webui

git checkout main
git fetch upstream
git merge --ff-only upstream/main
git push origin main

git checkout codex/custom-main
git merge main
```

If a cleaner history is explicitly preferred and the branch is safe to rewrite, the last step may be replaced with:

```bash
git checkout codex/custom-main
git rebase main
```

## Daily Development Procedure

Use this sequence for normal custom work:

```bash
cd ~/code/open-webui
git checkout codex/custom-main
git pull --ff-only origin codex/custom-main
```

For a separate task branch:

```bash
git checkout -b codex/<task-name>
```

After finishing the task:

```bash
git checkout codex/custom-main
git merge <task-branch>
git push origin codex/custom-main
```

## Safety Rules

- Never change the meaning of `main`; it is reserved for syncing official upstream changes
- Never force-push `main`
- Avoid force-pushing `codex/custom-main` unless a rebase was intentional and the impact is understood
- Before merging official updates into `codex/custom-main`, resolve conflicts there instead of patching around them on `main`
- Keep customization commits focused and small so future upstream merges stay manageable
- If a customization is deployment-only, prefer configuration or wrapper scripts over deep source edits

## Deployment Note

- The actively deployed custom instance should run from `codex/custom-main`, not from `main`
- After any frontend code change, always run a fresh frontend production build before handing off the task
- Treat frontend work as incomplete until the updated `build/` output has been regenerated

## Local Startup Playbook (macOS)

To avoid repeated startup failures and environment mismatch issues, always use the project virtualenv Python for backend startup.

- Do not rely on system `python3`/`python` for this project startup.
- `backend/start.sh` may use Homebrew Python (for example 3.14) where `uvicorn` is not installed.
- Preferred startup command is explicit `.venv` Python + `uvicorn`.

### Runtime Shape

This customized local instance has three parts:

1. Frontend static build in `build/`
2. Open WebUI backend on `127.0.0.1:8080`
3. Sandboxed command/file worker in Docker on `127.0.0.1:8765`

The backend serves the built frontend. Command execution and file operations do **not** run in the backend process; they go through the Dockerized sandbox worker mounted to `~/.open-webui/workdir`.

Full request chain:

```text
browser -> frontend -> backend (:8080) -> terminal proxy -> sandbox worker (:8765) -> /workspace
```

Command execution only works when both are true:

- the Docker sandbox worker is up on `127.0.0.1:8765`
- Open WebUI terminal server config contains a `sandbox` connection targeting that worker

Session layout:

- host root: `~/.open-webui/workdir`
- per-chat cwd root: `~/.open-webui/workdir/sessions/chat_<chat_id>/`
- uploaded files: `~/.open-webui/workdir/sessions/chat_<chat_id>/inputs/<message_id>/`
- sandbox mount inside container: `/workspace`

The sandbox auto-creates `inputs/`, `outputs/`, `artifacts/`, and `tmp/` inside each chat directory.

Container Python environments exposed into the workspace:

- `/workspace/venvs/default`
- `/workspace/venvs/data`

### Sandbox Execution Rules

When the AI executes commands inside the sandbox, treat the current chat session directory as the only working boundary by default.

- For Python execution inside the sandbox, default to a project-local `.venv` in the current working directory.
- The AI may create and configure `.venv` on its own when needed, including `python3 -m venv .venv`, upgrading `pip`, and installing required packages into that `.venv`.
- After `.venv` exists, use `.venv/bin/python`, `.venv/bin/pip`, or `. .venv/bin/activate` for all Python/package commands instead of relying on system Python.
- Do not default to shared interpreters such as `/workspace/venvs/default` or `/workspace/venvs/data` when a task can run in the local `.venv`. Only use them if the user explicitly asks for them or the task already requires that exact environment.
- If a repository already contains a dedicated `.venv`, reuse it instead of creating a second environment.

Sandbox file access must stay inside the current chat session scope unless the user explicitly asks otherwise.

- Do not read from or write to sibling session directories such as `/workspace/sessions/chat_*` that belong to other chats.
- Do not inspect, reuse, or depend on files from another session's `inputs/`, `outputs/`, `artifacts/`, or `tmp/` directories.
- Do not treat files from other sessions as implicit context, even if they are visible from the filesystem.
- If required material is missing from the current session, ask the user to provide it again or copy it into the current session workspace first.
- When cleaning up or debugging, avoid commands that scan the whole `sessions/` tree unless the user explicitly requests cross-session investigation.

Key paths:

- Frontend build output: `~/code/open-webui/build`
- Backend data: `~/code/open-webui/backend/data`
- Sandbox compose file: `~/code/open-webui/docker-compose.sandbox.yaml`
- Sandbox image file: `~/code/open-webui/Dockerfile.sandbox`
- Sandbox workdir: `~/.open-webui/workdir`

Key ports:

- `8080`: Open WebUI backend + built frontend
- `8765`: sandbox worker for terminal/file tools
- `5173`: optional Vite dev frontend

### Full Local Runtime (production-like)

Use this shape when you want the complete local instance, including frontend, backend, and command execution.

#### 0) One-time prerequisites

```bash
cd ~/code/open-webui
npm install
/Users/clarencestark/code/open-webui/.venv/bin/pip install -r backend/requirements.txt
```

Docker Desktop must be running because terminal/file tools depend on the sandbox container.

#### 1) Build frontend

```bash
cd ~/code/open-webui
npm run build
```

After any frontend change, regenerate `build/` before treating the local instance as ready.

#### 2) Initialize sandbox workdir

```bash
cd ~/code/open-webui
./scripts/setup_open_webui_workdir.sh
```

#### 3) Start sandbox worker

```bash
cd ~/code/open-webui
docker compose -f docker-compose.sandbox.yaml up -d --build
```

This starts the command/file sandbox on `127.0.0.1:8765`.

Equivalent helper script:

```bash
cd ~/code/open-webui
./scripts/start_sandbox_container.sh
```

The Docker sandbox is intentionally constrained:

- host bind mount: `~/.open-webui/workdir -> /workspace`
- non-root user `10001:10001`
- read-only container root filesystem
- Linux capabilities dropped
- `no-new-privileges:true`

#### 4) Configure terminal server in Open WebUI

In Admin Settings -> Integrations -> Terminal Servers, add:

```json
{
  "id": "sandbox",
  "name": "Sandbox",
  "url": "http://127.0.0.1:8765",
  "path": "/openapi.json",
  "auth_type": "none",
  "enabled": true
}
```

Without this connection, the sandbox may be healthy but the chat UI still will not expose terminal/file tools.

#### 5) Start backend

```bash
cd ~/code/open-webui/backend
/Users/clarencestark/code/open-webui/.venv/bin/python -m uvicorn open_webui.main:app \
  --host 127.0.0.1 \
  --port 8080 \
  --forwarded-allow-ips '*' \
  --workers 1
```

Open:

```bash
http://127.0.0.1:8080
```

#### 6) Optional one-shot helper

```bash
cd ~/code/open-webui
./scripts/harden_open_webui_sandbox.sh
```

Use the explicit step-by-step commands above as the canonical procedure. The helper is only a convenience wrapper.

### Frontend Dev Mode

Use this shape when working on Svelte/Vite frontend code live.

Keep the sandbox worker running on `8765`, keep the backend running on `8080`, and run:

```bash
cd ~/code/open-webui
npm run dev -- --host
```

Then open the Vite frontend:

```bash
http://127.0.0.1:5173
```

In this mode:

- `5173` serves the live frontend
- `8080` still serves backend APIs
- `8765` still handles terminal/file tools

### Verification Checklist

```bash
lsof -nP -iTCP:8080 -sTCP:LISTEN
curl -sS http://127.0.0.1:8080/health
curl -sS http://127.0.0.1:8765/api/config
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
```

Expected backend health response:

```json
{"status":true}
```

Expected sandbox config should include:

- `workspace_root: /workspace`
- `tmp_root: /tmp`
- `python.executable: /opt/venvs/default/bin/python3`

Optional checks for session isolation:

```bash
ls -la ~/.open-webui/workdir
find ~/.open-webui/workdir/sessions -maxdepth 3 -type d | head
docker logs open-webui-sandbox --tail 100
```

### 1) Start Backend (local-only access)

```bash
cd ~/code/open-webui/backend
/Users/clarencestark/code/open-webui/.venv/bin/python -m uvicorn open_webui.main:app \
  --host 127.0.0.1 \
  --port 8080 \
  --forwarded-allow-ips '*' \
  --workers 1
```

### 2) Start Backend (same-WiFi/LAN access for phone)

```bash
cd ~/code/open-webui/backend
/Users/clarencestark/code/open-webui/.venv/bin/python -m uvicorn open_webui.main:app \
  --host 0.0.0.0 \
  --port 8080 \
  --forwarded-allow-ips '*' \
  --workers 1
```

Then open on phone:

```bash
IP=$(ipconfig getifaddr en0 || ipconfig getifaddr en1)
echo "http://$IP:8080"
```

### 3) Must-run verification after startup

```bash
lsof -nP -iTCP:8080 -sTCP:LISTEN
curl -sS http://127.0.0.1:8080/health
curl -sS "http://$(ipconfig getifaddr en0 || ipconfig getifaddr en1):8080/health"
```

Expected health response:

```json
{"status":true}
```

### 4) Fast failure diagnosis

- If you see `No module named uvicorn`, you are not using the project `.venv` Python.
- If startup command exits immediately and port `8080` is not listening, run in foreground first and read logs before daemonizing.
- If `127.0.0.1:8765` is down, terminal/file tools will fail even if the UI on `8080` loads normally.
- If command execution cannot see uploaded files, inspect `~/.open-webui/workdir/sessions/` and confirm the sandbox container is mounted to `/workspace`.
- If terminal tools do not appear in chat at all, re-check Admin Settings -> Integrations -> Terminal Servers and confirm the `sandbox` entry still points to `http://127.0.0.1:8765`.
- If sandbox tools fail, check `docker logs open-webui-sandbox`.
