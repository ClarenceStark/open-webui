# Sandbox Worker

This service provides a Claude-style external sandbox for Open WebUI without
executing shell commands inside the main WebUI backend process.

It is intentionally aligned with the existing Open Terminal integration points:

- interactive shell: `POST /api/terminals` and `WS /api/terminals/{session_id}`
- file navigation: `/files/*`
- model tools: `/tools/*` exposed through the generated OpenAPI schema

When a chat turn includes uploaded Open WebUI files and a sandbox terminal is
active, the backend mirrors those files into the worker under a sandbox path
like `/workspace/open-webui-inputs/<chat>/<message>/...` before the model starts
calling tools.

## Run

```bash
cd /Users/clarencestark/code/open-webui
/Users/clarencestark/code/open-webui/.venv/bin/python -m uvicorn sandbox_worker.app:app --host 127.0.0.1 --port 8765
```

## Optional environment

- `SANDBOX_API_KEY`: bearer token required by HTTP and WebSocket requests
- `SANDBOX_WORKSPACE_ROOT`: writable workspace root, default is the current directory
- `SANDBOX_TMP_ROOT`: writable temp root, default is `/tmp`
- `SANDBOX_SHELL`: shell binary, default `/bin/bash`
- `SANDBOX_RUNNER`: optional prefix command used to wrap every shell command

`SANDBOX_RUNNER` is the intended hook for a stronger runtime sandbox such as
container, namespace, or OS-level isolation. The worker already constrains file
operations to `/workspace` and `/tmp`, but network isolation depends on the
runner you supply.
