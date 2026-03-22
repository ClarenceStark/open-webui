import asyncio
import json
import mimetypes
import os
import pty
import select
import shlex
import shutil
import subprocess
import sys
import tempfile
import uuid
import time
import logging

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Optional

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile, WebSocket
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field


APP_TITLE = "Open WebUI Sandbox Worker"
DEFAULT_SHELL = os.environ.get("SANDBOX_SHELL", "/bin/bash")
SANDBOX_RUNNER = os.environ.get("SANDBOX_RUNNER", "").strip()
SANDBOX_API_KEY = os.environ.get("SANDBOX_API_KEY", "").strip()

WORKSPACE_ROOT = Path(
    os.environ.get("SANDBOX_WORKSPACE_ROOT", os.getcwd())
).resolve()
TMP_ROOT = Path(
    os.environ.get(
        "SANDBOX_TMP_ROOT",
        "/tmp",
    )
).resolve()

TMP_ROOT.mkdir(parents=True, exist_ok=True)

app = FastAPI(title=APP_TITLE, version="0.1.0")
log = logging.getLogger(__name__)

def resolve_sandbox_python() -> tuple[Path, Path]:
    prefix_root = Path(sys.prefix).resolve()
    candidates = [
        prefix_root / "bin" / "python3",
        prefix_root / "bin" / "python",
        Path(sys.executable).resolve(),
    ]
    executable = next((candidate for candidate in candidates if candidate.exists()), candidates[-1])
    venv_root = prefix_root if (prefix_root / "pyvenv.cfg").exists() else executable.parent.parent
    return executable, venv_root


SANDBOX_PYTHON_EXECUTABLE, SANDBOX_VENV_ROOT = resolve_sandbox_python()
SANDBOX_PYTHON_BIN_DIR = SANDBOX_PYTHON_EXECUTABLE.parent
DEFAULT_SANDBOX_PYTHON_PACKAGES = {
    "pillow": "PIL",
    "python-docx": "docx",
    "matplotlib": "matplotlib",
    "pandas": "pandas",
    "pypandoc": "pypandoc",
}
SANDBOX_AUTO_INSTALL_PYTHON_PACKAGES = (
    os.environ.get("SANDBOX_AUTO_INSTALL_PYTHON_PACKAGES", "true").strip().lower()
    not in {"0", "false", "no", "off"}
)
PYTHON_BOOTSTRAP_STATUS = {
    "checked": False,
    "installed": [],
    "missing": [],
    "failed": [],
}
PYTHON_BOOTSTRAP_LOCK = asyncio.Lock()


@dataclass
class SessionState:
    id: str
    process: subprocess.Popen
    tty: bool
    master_fd: Optional[int]
    stdin_fd: Optional[int]
    stdout_fd: Optional[int]
    stderr_fd: Optional[int]
    user_id: str
    scope: str
    cwd: str
    command: str
    timeout_task: Optional[asyncio.Task] = None
    created_at: float = field(default_factory=time.time)


SESSIONS: dict[str, SessionState] = {}
USER_CWD: dict[str, str] = {}


class TerminalCreateResponse(BaseModel):
    id: str


class ExecCommandRequest(BaseModel):
    cmd: str
    workdir: Optional[str] = None
    timeout_seconds: int = Field(default=300, ge=1, le=3600)
    env: Optional[dict[str, str]] = None
    tty: bool = False
    yield_time_ms: int = Field(default=1000, ge=0, le=30000)


class WriteStdinRequest(BaseModel):
    session_id: str
    chars: str = ""
    yield_time_ms: int = Field(default=1000, ge=0, le=30000)


class PathRequest(BaseModel):
    path: str


class MoveRequest(BaseModel):
    source: str
    destination: str


class ApplyPatchRequest(BaseModel):
    patch: str
    workdir: Optional[str] = None


def require_auth(request: Request):
    if not SANDBOX_API_KEY:
        return

    header = request.headers.get("authorization", "")
    token = header.replace("Bearer ", "", 1).strip()
    if token != SANDBOX_API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")


def websocket_auth_ok(token: str) -> bool:
    if not SANDBOX_API_KEY:
        return True
    return token.strip() == SANDBOX_API_KEY


def get_user_id(request: Request) -> str:
    return request.headers.get("x-user-id", "anonymous")


def sanitize_scope_component(value: Optional[str], fallback: str) -> str:
    raw = str(value or fallback).strip()
    sanitized = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in raw)
    sanitized = sanitized.strip("._") or fallback
    return sanitized[:120]


def derive_request_scope(request: Request) -> str:
    explicit_scope = request.headers.get("x-terminal-scope", "").strip()
    if explicit_scope:
        return sanitize_scope_component(explicit_scope, "default")

    chat_id = request.headers.get("x-chat-id", "").strip()
    if chat_id:
        return f"chat_{sanitize_scope_component(chat_id, 'chat')}"

    session_id = request.headers.get("x-session-id", "").strip()
    if session_id:
        return f"session_{sanitize_scope_component(session_id, 'session')}"

    return "default"


def get_scope_root_virtual(scope: str) -> str:
    return str(PurePosixPath("/workspace") / "sessions" / scope)


def ensure_scope_workspace(scope: str) -> tuple[Path, str]:
    virtual_root = get_scope_root_virtual(scope)
    real_root = (WORKSPACE_ROOT / "sessions" / scope).resolve()
    real_root.mkdir(parents=True, exist_ok=True)
    for child in ("inputs", "outputs", "artifacts", "tmp"):
        (real_root / child).mkdir(exist_ok=True)
    return real_root, virtual_root


def get_user_scope_key(user_id: str, scope: str) -> str:
    return f"{user_id}:{scope}"


def get_user_cwd(user_id: str, scope: str) -> str:
    scope_key = get_user_scope_key(user_id, scope)
    if scope_key not in USER_CWD:
        _real_root, virtual_root = ensure_scope_workspace(scope)
        USER_CWD[scope_key] = virtual_root
    return USER_CWD[scope_key]


def set_user_cwd(user_id: str, scope: str, cwd: str):
    USER_CWD[get_user_scope_key(user_id, scope)] = cwd


def normalize_virtual_path(path: str, user_id: str, scope: str) -> str:
    base = PurePosixPath(get_user_cwd(user_id, scope))
    target = PurePosixPath(path)
    if not target.is_absolute():
        target = base / target

    normalized = PurePosixPath("/").joinpath(target).as_posix()
    normalized = str(PurePosixPath(normalized))
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    return normalized


def resolve_path(path: str, user_id: str, scope: str) -> tuple[Optional[Path], str]:
    virtual = normalize_virtual_path(path, user_id, scope)
    if virtual == "/":
        return None, virtual

    roots = {
        "/workspace": WORKSPACE_ROOT,
        "/tmp": TMP_ROOT,
    }

    for prefix, root in roots.items():
        if virtual == prefix or virtual.startswith(f"{prefix}/"):
            suffix = virtual[len(prefix) :].lstrip("/")
            real_path = (root / suffix).resolve()
            try:
                real_path.relative_to(root)
            except ValueError as exc:
                raise HTTPException(status_code=403, detail="Path escapes sandbox roots") from exc
            return real_path, virtual

    raise HTTPException(status_code=403, detail="Path is outside sandbox roots")


def ensure_directory(path: Path):
    if not path.exists() or not path.is_dir():
        raise HTTPException(status_code=404, detail="Directory not found")


def ensure_file(path: Path):
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")


def real_to_virtual_path(path: Path) -> str:
    try:
        relative = path.resolve().relative_to(WORKSPACE_ROOT)
        return str(PurePosixPath("/workspace") / PurePosixPath(relative.as_posix()))
    except ValueError:
        pass

    try:
        relative = path.resolve().relative_to(TMP_ROOT)
        return str(PurePosixPath("/tmp") / PurePosixPath(relative.as_posix()))
    except ValueError:
        pass

    raise HTTPException(status_code=403, detail="Path is outside sandbox roots")


def shell_command_argv(cmd: str) -> list[str]:
    export_lines = [
        f"export PATH={shlex.quote(str(SANDBOX_PYTHON_BIN_DIR))}:$PATH",
    ]
    if (SANDBOX_VENV_ROOT / "pyvenv.cfg").exists():
        export_lines.append(f"export VIRTUAL_ENV={shlex.quote(str(SANDBOX_VENV_ROOT))}")

    wrapped_cmd = "\n".join([*export_lines, cmd])
    shell_argv = [DEFAULT_SHELL, "-lc", wrapped_cmd]
    if SANDBOX_RUNNER:
        return [*shlex.split(SANDBOX_RUNNER), *shell_argv]
    return shell_argv


def virtual_to_real_in_text(text: str) -> str:
    if not text:
        return text

    workspace_root = str(WORKSPACE_ROOT)
    tmp_root = str(TMP_ROOT)

    converted = text.replace("/workspace/", f"{workspace_root.rstrip('/')}/")
    converted = converted.replace("/tmp/", f"{tmp_root.rstrip('/')}/")

    if "/workspace" in converted:
        converted = converted.replace("/workspace", workspace_root)
    if "/tmp" in converted:
        converted = converted.replace("/tmp", tmp_root)

    return converted


def real_to_virtual_in_text(text: str) -> str:
    if not text:
        return text

    workspace_root = str(WORKSPACE_ROOT)
    tmp_root = str(TMP_ROOT)

    converted = text.replace(f"{workspace_root.rstrip('/')}/", "/workspace/")
    converted = converted.replace(f"{tmp_root.rstrip('/')}/", "/tmp/")

    if workspace_root in converted:
        converted = converted.replace(workspace_root, "/workspace")
    if tmp_root in converted:
        converted = converted.replace(tmp_root, "/tmp")

    return converted


def enrich_env_with_current_python(full_env: dict[str, str]) -> dict[str, str]:
    existing_path = full_env.get("PATH", "")
    full_env["PATH"] = (
        f"{SANDBOX_PYTHON_BIN_DIR}{os.pathsep}{existing_path}"
        if existing_path
        else str(SANDBOX_PYTHON_BIN_DIR)
    )

    if (SANDBOX_VENV_ROOT / "pyvenv.cfg").exists():
        full_env.setdefault("VIRTUAL_ENV", str(SANDBOX_VENV_ROOT))

    return full_env


async def probe_missing_python_packages() -> list[str]:
    missing = []
    for package_name, import_name in DEFAULT_SANDBOX_PYTHON_PACKAGES.items():
        process = await asyncio.create_subprocess_exec(
            str(SANDBOX_PYTHON_EXECUTABLE),
            "-c",
            f"import {import_name}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=enrich_env_with_current_python(os.environ.copy()),
        )
        await process.communicate()
        if process.returncode != 0:
            missing.append(package_name)
    return missing


async def ensure_default_python_packages() -> dict[str, Any]:
    async with PYTHON_BOOTSTRAP_LOCK:
        if PYTHON_BOOTSTRAP_STATUS["checked"]:
            return dict(PYTHON_BOOTSTRAP_STATUS)

        missing = await probe_missing_python_packages()
        PYTHON_BOOTSTRAP_STATUS["missing"] = list(missing)

        if missing and SANDBOX_AUTO_INSTALL_PYTHON_PACKAGES:
            log.info("Installing sandbox Python packages: %s", ", ".join(missing))
            install_process = await asyncio.create_subprocess_exec(
                str(SANDBOX_PYTHON_EXECUTABLE),
                "-m",
                "pip",
                "install",
                *missing,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=enrich_env_with_current_python(os.environ.copy()),
            )
            stdout, stderr = await install_process.communicate()
            if install_process.returncode == 0:
                PYTHON_BOOTSTRAP_STATUS["installed"] = list(missing)
                PYTHON_BOOTSTRAP_STATUS["missing"] = []
            else:
                PYTHON_BOOTSTRAP_STATUS["failed"] = list(missing)
                log.warning(
                    "Failed to install sandbox Python packages %s: %s %s",
                    ", ".join(missing),
                    stdout.decode("utf-8", "replace"),
                    stderr.decode("utf-8", "replace"),
                )
        elif missing:
            PYTHON_BOOTSTRAP_STATUS["failed"] = list(missing)

        PYTHON_BOOTSTRAP_STATUS["checked"] = True
        return dict(PYTHON_BOOTSTRAP_STATUS)


def read_available_from_fds(*fds: Optional[int]) -> bytes:
    valid_fds = [fd for fd in fds if fd is not None]
    if not valid_fds:
        return b""

    chunks: list[bytes] = []
    while True:
        ready, _, _ = select.select(valid_fds, [], [], 0)
        if not ready:
            break

        had_data = False
        for fd in ready:
            try:
                chunk = os.read(fd, 65536)
            except BlockingIOError:
                continue
            except OSError:
                continue

            if not chunk:
                continue

            had_data = True
            chunks.append(chunk)

        if not had_data:
            break

    return b"".join(chunks)


def spawn_session(
    command: str,
    cwd: Path,
    env: Optional[dict[str, str]],
    user_id: str,
    scope: str,
    tty: bool,
) -> SessionState:
    command = virtual_to_real_in_text(command)

    full_env = os.environ.copy()
    full_env.update(env or {})
    full_env = enrich_env_with_current_python(full_env)

    master_fd: Optional[int] = None
    stdin_fd: Optional[int] = None
    stdout_fd: Optional[int] = None
    stderr_fd: Optional[int] = None

    if tty:
        master_fd, slave_fd = pty.openpty()
        process = subprocess.Popen(
            shell_command_argv(command),
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=str(cwd),
            env=full_env,
            start_new_session=True,
        )
        os.close(slave_fd)
        os.set_blocking(master_fd, False)
    else:
        process = subprocess.Popen(
            shell_command_argv(command),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(cwd),
            env=full_env,
            start_new_session=True,
        )
        stdin_fd = process.stdin.fileno() if process.stdin else None
        stdout_fd = process.stdout.fileno() if process.stdout else None
        stderr_fd = process.stderr.fileno() if process.stderr else None

        for fd in (stdout_fd, stderr_fd):
            if fd is not None:
                os.set_blocking(fd, False)

    session_id = str(uuid.uuid4())
    return SessionState(
        id=session_id,
        process=process,
        tty=tty,
        master_fd=master_fd,
        stdin_fd=stdin_fd,
        stdout_fd=stdout_fd,
        stderr_fd=stderr_fd,
        user_id=user_id,
        scope=scope,
        cwd=real_to_virtual_path(cwd),
        command=command,
    )


async def terminate_session_later(session_id: str, timeout_seconds: int):
    await asyncio.sleep(timeout_seconds)
    session = SESSIONS.get(session_id)
    if not session or session.process.poll() is not None:
        return

    try:
        session.process.terminate()
        await asyncio.sleep(1)
        if session.process.poll() is None:
            session.process.kill()
    except Exception:
        pass


def build_session_message(running: bool, exit_code: Optional[int], pending: str, success: str) -> str:
    if running:
        return pending
    if exit_code in (None, 0):
        return success
    return "Command failed"


async def collect_session_output(
    session: SessionState, yield_time_ms: int
) -> tuple[str, str, bool, Optional[int]]:
    if yield_time_ms > 0:
        await asyncio.sleep(yield_time_ms / 1000)

    if session.tty:
        output = await asyncio.to_thread(read_available_from_fds, session.master_fd)
        stdout = real_to_virtual_in_text(output.decode("utf-8", "replace"))
        stderr = ""
    else:
        stdout_bytes, stderr_bytes = await asyncio.gather(
            asyncio.to_thread(read_available_from_fds, session.stdout_fd),
            asyncio.to_thread(read_available_from_fds, session.stderr_fd),
        )
        stdout = real_to_virtual_in_text(stdout_bytes.decode("utf-8", "replace"))
        stderr = real_to_virtual_in_text(stderr_bytes.decode("utf-8", "replace"))

    running = session.process.poll() is None
    exit_code = None if running else session.process.returncode
    return stdout, stderr, running, exit_code


def artifact_descriptor(path: str, content_type: Optional[str] = None) -> dict[str, Any]:
    filename = os.path.basename(path)
    guessed = content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return {
        "path": path,
        "name": filename,
        "content_type": guessed,
    }


@app.get("/api/config")
async def api_config():
    return {
        "features": {
            "terminal": True,
        },
        "sandbox": {
            "workspace_root": str(WORKSPACE_ROOT),
            "tmp_root": str(TMP_ROOT),
            "runner_configured": bool(SANDBOX_RUNNER),
        },
        "python": {
            "executable": str(SANDBOX_PYTHON_EXECUTABLE),
            "venv_root": (
                str(SANDBOX_VENV_ROOT)
                if (SANDBOX_VENV_ROOT / "pyvenv.cfg").exists()
                else None
            ),
            "auto_install_enabled": SANDBOX_AUTO_INSTALL_PYTHON_PACKAGES,
            "bootstrap": PYTHON_BOOTSTRAP_STATUS,
        },
    }


@app.on_event("startup")
async def bootstrap_python_environment():
    await ensure_default_python_packages()


@app.post("/api/terminals", response_model=TerminalCreateResponse, dependencies=[Depends(require_auth)])
async def create_terminal(request: Request):
    user_id = get_user_id(request)
    scope = derive_request_scope(request)
    cwd_real, _ = resolve_path(get_user_cwd(user_id, scope), user_id, scope)
    cwd_real = cwd_real or WORKSPACE_ROOT

    session = spawn_session(
        f"exec {shlex.quote(DEFAULT_SHELL)} -l",
        cwd_real,
        None,
        user_id,
        scope,
        tty=True,
    )
    SESSIONS[session.id] = session
    return {"id": session.id}


@app.websocket("/api/terminals/{session_id}")
async def terminal_socket(websocket: WebSocket, session_id: str):
    await websocket.accept()

    auth_payload = await websocket.receive_text()
    try:
        auth = json.loads(auth_payload)
    except json.JSONDecodeError:
        await websocket.close(code=4001, reason="Invalid auth payload")
        return

    if auth.get("type") != "auth" or not websocket_auth_ok(auth.get("token", "")):
        await websocket.close(code=4001, reason="Unauthorized")
        return

    session = SESSIONS.get(session_id)
    if not session:
        await websocket.close(code=4004, reason="Session not found")
        return

    async def pump_output():
        while True:
            chunk = await asyncio.to_thread(read_available_from_fds, session.master_fd)
            if chunk:
                await websocket.send_bytes(chunk)
            elif session.process.poll() is not None:
                break
            await asyncio.sleep(0.05)

    async def receive_input():
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break

            text = message.get("text")
            data = message.get("bytes")

            if text:
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    payload = None

                if payload and payload.get("type") == "resize":
                    continue
                if payload and payload.get("type") == "ping":
                    continue
                if session.master_fd is not None:
                    os.write(session.master_fd, text.encode("utf-8"))
            elif data:
                if session.master_fd is not None:
                    os.write(session.master_fd, data)

    try:
        await asyncio.gather(pump_output(), receive_input())
    finally:
        await websocket.close()


@app.get("/files/cwd", dependencies=[Depends(require_auth)])
async def get_cwd(request: Request):
    user_id = get_user_id(request)
    scope = derive_request_scope(request)
    return {"cwd": get_user_cwd(user_id, scope)}


@app.post("/files/cwd", dependencies=[Depends(require_auth)])
async def set_cwd(request: Request, body: PathRequest):
    user_id = get_user_id(request)
    scope = derive_request_scope(request)
    real_path, virtual_path = resolve_path(body.path, user_id, scope)
    if real_path is None:
        raise HTTPException(status_code=400, detail="Root path cannot be current working directory")
    ensure_directory(real_path)
    set_user_cwd(user_id, scope, virtual_path)
    return {"cwd": virtual_path}


@app.get("/files/list", dependencies=[Depends(require_auth)])
async def list_directory(
    request: Request,
    directory: str = Query("/", description="Virtual sandbox directory"),
):
    user_id = get_user_id(request)
    scope = derive_request_scope(request)
    real_path, virtual_path = resolve_path(directory, user_id, scope)

    if real_path is None:
        return {
            "entries": [
                {"name": "workspace", "type": "directory"},
                {"name": "tmp", "type": "directory"},
            ]
        }

    ensure_directory(real_path)
    entries = []
    for child in sorted(real_path.iterdir(), key=lambda item: (item.is_file(), item.name.lower())):
        stat = child.stat()
        entries.append(
            {
                "name": child.name,
                "type": "file" if child.is_file() else "directory",
                "size": stat.st_size if child.is_file() else None,
                "modified": int(stat.st_mtime),
            }
        )
    return {"entries": entries, "cwd": virtual_path}


@app.get("/files/read", dependencies=[Depends(require_auth)])
async def read_file_endpoint(
    request: Request,
    path: str,
    max_bytes: int = Query(default=200000, ge=1, le=5_000_000),
):
    user_id = get_user_id(request)
    scope = derive_request_scope(request)
    real_path, virtual_path = resolve_path(path, user_id, scope)
    if real_path is None:
        raise HTTPException(status_code=400, detail="Cannot read the root directory")

    ensure_file(real_path)
    content_type = mimetypes.guess_type(real_path.name)[0] or "text/plain"
    if not (
        content_type.startswith("text/")
        or content_type in {"application/json", "application/xml", "application/x-sh"}
    ):
        raise HTTPException(status_code=400, detail="Binary files should be opened via /files/view")

    raw = real_path.read_bytes()[:max_bytes]
    content = raw.decode("utf-8", "replace")
    return {
        "path": virtual_path,
        "total_lines": content.count("\n") + (1 if content else 0),
        "content": content,
        "truncated": real_path.stat().st_size > max_bytes,
    }


@app.get("/files/view", dependencies=[Depends(require_auth)])
async def view_file(request: Request, path: str):
    user_id = get_user_id(request)
    scope = derive_request_scope(request)
    real_path, _ = resolve_path(path, user_id, scope)
    if real_path is None:
        raise HTTPException(status_code=400, detail="Cannot view the root directory")
    ensure_file(real_path)
    content_type = mimetypes.guess_type(real_path.name)[0] or "application/octet-stream"
    return FileResponse(real_path, media_type=content_type, filename=real_path.name)


@app.post("/files/upload", dependencies=[Depends(require_auth)])
async def upload_to_directory(
    request: Request,
    directory: str = Query("/workspace"),
    file: UploadFile = File(...),
):
    user_id = get_user_id(request)
    scope = derive_request_scope(request)
    real_path, virtual_path = resolve_path(directory, user_id, scope)
    if real_path is None:
        raise HTTPException(status_code=400, detail="Cannot upload into the sandbox root")
    ensure_directory(real_path)

    filename = os.path.basename(file.filename or "upload.bin")
    target = real_path / filename
    with target.open("wb") as handle:
        shutil.copyfileobj(file.file, handle)

    return {
        "path": f"{virtual_path.rstrip('/')}/{filename}",
        "size": target.stat().st_size,
    }


@app.post("/files/mkdir", dependencies=[Depends(require_auth)])
async def create_directory(request: Request, body: PathRequest):
    user_id = get_user_id(request)
    scope = derive_request_scope(request)
    real_path, virtual_path = resolve_path(body.path, user_id, scope)
    if real_path is None:
        raise HTTPException(status_code=400, detail="Cannot create the root directory")
    real_path.mkdir(parents=True, exist_ok=True)
    return {"path": virtual_path}


@app.delete("/files/delete", dependencies=[Depends(require_auth)])
async def delete_entry(request: Request, path: str):
    user_id = get_user_id(request)
    scope = derive_request_scope(request)
    real_path, virtual_path = resolve_path(path, user_id, scope)
    if real_path is None:
        raise HTTPException(status_code=400, detail="Cannot delete the root directory")

    if real_path.is_dir():
        shutil.rmtree(real_path)
        entry_type = "directory"
    elif real_path.exists():
        real_path.unlink()
        entry_type = "file"
    else:
        raise HTTPException(status_code=404, detail="Entry not found")

    return {"path": virtual_path, "type": entry_type}


@app.post("/files/move", dependencies=[Depends(require_auth)])
async def move_entry(request: Request, body: MoveRequest):
    user_id = get_user_id(request)
    scope = derive_request_scope(request)
    source_real, source_virtual = resolve_path(body.source, user_id, scope)
    destination_real, destination_virtual = resolve_path(body.destination, user_id, scope)
    if source_real is None or destination_real is None:
        raise HTTPException(status_code=400, detail="Root path cannot be moved")

    source_real.rename(destination_real)
    return {"source": source_virtual, "destination": destination_virtual}


@app.get("/ports", dependencies=[Depends(require_auth)])
async def list_ports():
    return {"ports": []}


@app.api_route("/proxy/{port}/{path:path}", methods=["GET", "POST"], dependencies=[Depends(require_auth)])
async def proxy_port(port: int, path: str = ""):
    return JSONResponse(
        status_code=501,
        content={"detail": f"Port proxying is not implemented in this sandbox worker ({port}/{path})."},
    )


@app.post("/tools/exec", operation_id="exec_command", dependencies=[Depends(require_auth)])
async def exec_command(request: Request, body: ExecCommandRequest):
    user_id = get_user_id(request)
    scope = derive_request_scope(request)
    cwd_real, cwd_virtual = resolve_path(
        body.workdir or get_user_cwd(user_id, scope), user_id, scope
    )
    cwd_real = cwd_real or WORKSPACE_ROOT

    session = spawn_session(body.cmd, cwd_real, body.env, user_id, scope, tty=body.tty)
    session.cwd = cwd_virtual
    SESSIONS[session.id] = session
    session.timeout_task = asyncio.create_task(
        terminate_session_later(session.id, body.timeout_seconds)
    )

    stdout, stderr, running, exit_code = await collect_session_output(
        session, body.yield_time_ms
    )
    return {
        "status": "success",
        "message": build_session_message(
            running,
            exit_code,
            pending="Command started",
            success="Command finished",
        ),
        "session_id": session.id,
        "stdout": stdout,
        "stderr": stderr,
        "running": running,
        "exit_code": exit_code,
        "cwd": session.cwd,
    }


@app.post("/tools/stdin", operation_id="write_stdin", dependencies=[Depends(require_auth)])
async def write_stdin(body: WriteStdinRequest):
    session = SESSIONS.get(body.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if body.chars:
        input_fd = session.master_fd if session.tty else session.stdin_fd
        if input_fd is None:
            raise HTTPException(status_code=400, detail="Session does not accept stdin")
        os.write(input_fd, body.chars.encode("utf-8"))

    stdout, stderr, running, exit_code = await collect_session_output(
        session, body.yield_time_ms
    )
    return {
        "status": "success",
        "message": build_session_message(
            running,
            exit_code,
            pending="Session updated",
            success="Command finished",
        ),
        "session_id": session.id,
        "stdout": stdout,
        "stderr": stderr,
        "running": running,
        "exit_code": exit_code,
        "cwd": session.cwd,
    }


@app.get("/tools/files/list", operation_id="list_files", dependencies=[Depends(require_auth)])
async def list_files_tool(
    request: Request,
    path: Optional[str] = Query(default=None),
):
    user_id = get_user_id(request)
    scope = derive_request_scope(request)
    target = path or get_user_cwd(user_id, scope)
    real_path, virtual_path = resolve_path(target, user_id, scope)

    if real_path is None:
        entries = [
            {"name": "workspace", "type": "directory"},
            {"name": "tmp", "type": "directory"},
        ]
    else:
        ensure_directory(real_path)
        entries = [
            {
                "name": child.name,
                "type": "file" if child.is_file() else "directory",
                "size": child.stat().st_size if child.is_file() else None,
            }
            for child in sorted(real_path.iterdir(), key=lambda item: (item.is_file(), item.name.lower()))
        ]

    return {
        "status": "success",
        "message": f"Listed files under {virtual_path}",
        "cwd": get_user_cwd(user_id, scope),
        "entries": entries,
    }


@app.get("/tools/files/read", operation_id="read_file", dependencies=[Depends(require_auth)])
async def read_file_tool(
    request: Request,
    path: str,
    max_bytes: int = Query(default=200000, ge=1, le=5_000_000),
):
    result = await read_file_endpoint(request, path=path, max_bytes=max_bytes)
    return {
        "status": "success",
        "message": f"Read file {result['path']}",
        "path": result["path"],
        "content": result["content"],
        "truncated": result["truncated"],
    }


@app.post("/tools/apply_patch", operation_id="apply_patch", dependencies=[Depends(require_auth)])
async def apply_patch_tool(request: Request, body: ApplyPatchRequest):
    user_id = get_user_id(request)
    scope = derive_request_scope(request)
    cwd_real, cwd_virtual = resolve_path(
        body.workdir or get_user_cwd(user_id, scope), user_id, scope
    )
    cwd_real = cwd_real or WORKSPACE_ROOT

    patch_binary = shutil.which("patch")
    if not patch_binary:
        raise HTTPException(status_code=500, detail="The 'patch' binary is not available")

    process = await asyncio.create_subprocess_exec(
        patch_binary,
        "-p0",
        cwd=str(cwd_real),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate(body.patch.encode("utf-8"))

    return {
        "status": "success" if process.returncode == 0 else "error",
        "message": "Patch applied" if process.returncode == 0 else "Patch failed",
        "cwd": cwd_virtual,
        "stdout": stdout.decode("utf-8", "replace"),
        "stderr": stderr.decode("utf-8", "replace"),
        "exit_code": process.returncode,
    }


@app.get("/tools/files/download", operation_id="download_artifact", dependencies=[Depends(require_auth)])
async def download_artifact_tool(
    request: Request,
    path: str,
    content_type: Optional[str] = Query(default=None),
):
    user_id = get_user_id(request)
    scope = derive_request_scope(request)
    real_path, virtual_path = resolve_path(path, user_id, scope)
    if real_path is None:
        raise HTTPException(status_code=400, detail="Cannot download the root directory")
    ensure_file(real_path)

    return {
        "status": "success",
        "message": f"Prepared artifact {virtual_path}",
        "summary": f"Artifact ready: {real_path.name}",
        "artifacts": [artifact_descriptor(virtual_path, content_type=content_type)],
    }


@app.get("/tools/files/view_image", operation_id="view_image", dependencies=[Depends(require_auth)])
async def view_image_tool(request: Request, path: str):
    user_id = get_user_id(request)
    scope = derive_request_scope(request)
    real_path, virtual_path = resolve_path(path, user_id, scope)
    if real_path is None:
        raise HTTPException(status_code=400, detail="Cannot preview the root directory")
    ensure_file(real_path)

    content_type = mimetypes.guess_type(real_path.name)[0] or "application/octet-stream"
    if not content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="The requested file is not an image")

    return {
        "status": "success",
        "message": f"Prepared image {virtual_path}",
        "summary": f"Image ready: {real_path.name}",
        "artifacts": [artifact_descriptor(virtual_path, content_type=content_type)],
    }
