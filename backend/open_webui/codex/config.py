import os
import shutil
from pathlib import Path


def _resolve_codex_bin_path() -> str:
    configured = os.getenv("CODEX_BIN_PATH")
    if configured:
        return os.path.expanduser(configured)

    try:
        from codex_cli_bin import bundled_codex_path

        bundled = bundled_codex_path()
        if bundled:
            return bundled
    except Exception:
        pass

    discovered = shutil.which("codex")
    if discovered:
        return discovered

    fallback_paths = [
        Path.home() / ".nvm/versions/node/v22.12.0/bin/codex",
        Path.home() / ".cargo/bin/codex",
    ]
    for path in fallback_paths:
        if path.exists():
            return str(path)

    return "codex"


CODEX_BIN_PATH = _resolve_codex_bin_path()
CODEX_WORKSPACE_BASE = Path(
    os.path.expanduser(os.getenv("CODEX_WORKSPACE_BASE", "~/.open-webui/codex/"))
).expanduser()
CODEX_SESSION_IDLE_TIMEOUT = int(os.getenv("CODEX_SESSION_IDLE_TIMEOUT", "1800"))
CODEX_MODEL = os.getenv("CODEX_MODEL") or None
CODEX_MODEL_PROVIDER = os.getenv("CODEX_MODEL_PROVIDER") or None


def get_workspace_path(chat_id: str) -> Path:
    return CODEX_WORKSPACE_BASE / chat_id


def get_model_override(model_id: str | None) -> str | None:
    if model_id == "gpt-5.4":
        return "gpt-5.4"
    if model_id and model_id.startswith("codex/"):
        override = model_id.split("/", 1)[1].strip()
        if override:
            return override
    return CODEX_MODEL


def get_thread_config() -> dict:
    return {
        "sandbox_workspace_write": {
            "network_access": True,
        }
    }


def get_sandbox_policy() -> dict:
    return {
        "type": "workspaceWrite",
        "networkAccess": True,
    }
