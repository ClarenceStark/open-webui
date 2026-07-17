import logging
import os
import re
import shutil
from pathlib import Path


log = logging.getLogger(__name__)


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
CODEX_WEB_HOME = Path(
    os.path.expanduser(os.getenv("CODEX_WEB_HOME", "~/.codex-openwebui/"))
).expanduser()

_DEFAULT_CODEX_HOME = Path.home() / ".codex"
_CODEX_WEB_SOURCE_CONFIG = Path(
    os.path.expanduser(
        os.getenv("CODEX_WEB_CONFIG_SOURCE", str(_DEFAULT_CODEX_HOME / "config_web.toml"))
    )
).expanduser()


def _upsert_root_string(text: str, key: str, value: str) -> str:
    pattern = re.compile(rf"(?m)^(?P<prefix>\s*{re.escape(key)}\s*=\s*).*$")
    replacement = f'{key} = "{value}"'
    if pattern.search(text):
        return pattern.sub(replacement, text, count=1)

    stripped = text.lstrip("\n")
    prefix = "" if text == stripped else text[: len(text) - len(stripped)]
    body = stripped.rstrip()
    if body:
        body = f'{replacement}\n{body}\n'
    else:
        body = f"{replacement}\n"
    return f"{prefix}{body}"


def _upsert_table_bool(text: str, table: str, key: str, value: bool) -> str:
    header = f"[{table}]"
    lines = text.splitlines()
    value_literal = "true" if value else "false"

    for index, line in enumerate(lines):
        if line.strip() != header:
            continue

        insert_at = len(lines)
        for probe in range(index + 1, len(lines)):
            stripped = lines[probe].strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                insert_at = probe
                break
            if re.match(rf"^\s*{re.escape(key)}\s*=", lines[probe]):
                lines[probe] = f"{key} = {value_literal}"
                return "\n".join(lines) + "\n"

        lines.insert(insert_at, f"{key} = {value_literal}")
        return "\n".join(lines) + "\n"

    body = text.rstrip()
    if body:
        body += "\n\n"
    body += f"{header}\n{key} = {value_literal}\n"
    return body


def _resolve_codex_source_config() -> Path | None:
    candidates = [
        _CODEX_WEB_SOURCE_CONFIG,
        _DEFAULT_CODEX_HOME / "config.toml",
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def _copy_if_missing(source: Path, destination: Path) -> None:
    if destination.exists() or not source.exists():
        return

    if source.is_dir():
        shutil.copytree(source, destination)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _bootstrap_codex_web_home() -> None:
    CODEX_WEB_HOME.mkdir(parents=True, exist_ok=True)

    config_path = CODEX_WEB_HOME / "config.toml"
    source_config = _resolve_codex_source_config()
    if source_config is not None:
        source_home = source_config.parent
    else:
        source_home = _DEFAULT_CODEX_HOME

    _copy_if_missing(source_home / "agents", CODEX_WEB_HOME / "agents")

    if config_path.exists():
        return

    if source_config is not None:
        source_text = source_config.read_text(encoding="utf-8")
    else:
        source_text = ""

    config_text = (
        "# Open WebUI dedicated Codex config.\n"
        "# Edit this file to change Open WebUI-only Codex behavior.\n\n"
        + source_text.lstrip()
    )
    config_text = _upsert_root_string(config_text, "approval_policy", "never")
    config_text = _upsert_root_string(config_text, "sandbox_mode", "workspace-write")
    config_text = _upsert_table_bool(
        config_text,
        "sandbox_workspace_write",
        "network_access",
        True,
    )

    config_path.write_text(config_text.rstrip() + "\n", encoding="utf-8")

    log.info("Initialized Open WebUI Codex home at %s", CODEX_WEB_HOME)


def get_codex_app_server_env() -> dict[str, str]:
    _bootstrap_codex_web_home()
    return {"CODEX_HOME": str(CODEX_WEB_HOME)}


def get_workspace_path(chat_id: str) -> Path:
    return CODEX_WORKSPACE_BASE / chat_id


def get_model_override(model_id: str | None) -> str | None:
    if model_id in {"gpt-5.4", "gpt-5.5"}:
        return "gpt-5.5"
    if model_id and model_id.startswith("codex/"):
        override = model_id.split("/", 1)[1].strip()
        if override:
            if override == "gpt-5.4":
                return "gpt-5.5"
            return override
    return CODEX_MODEL
