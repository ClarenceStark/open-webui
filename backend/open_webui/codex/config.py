import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import tomllib
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
CODEX_WEB_PROFILE = os.getenv("CODEX_WEB_PROFILE", "azure").strip() or None
CODEX_WEB_HOME = Path(
    os.path.expanduser(os.getenv("CODEX_WEB_HOME", "~/.codex-openwebui/"))
).expanduser()
CODEX_DANGER_FULL_ACCESS_EMAILS = frozenset(
    email.strip().lower()
    for email in os.getenv(
        "CODEX_DANGER_FULL_ACCESS_EMAILS",
        "ykl@gpt.com,cxh@gpt.com",
    ).split(",")
    if email.strip()
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_CODEX_HOME = Path.home() / ".codex"
_DEFAULT_AGENTS_HOME = Path.home() / ".agents"
_DEFAULT_PLAYWRIGHT_CACHE = Path.home() / "Library/Caches/ms-playwright"
_NODE_SHIMS_DIR = CODEX_WEB_HOME / "node-shims"
_SYSTEM_OPENSSL_CONFIG_DIR = Path("/System/Library/OpenSSL")
_SYSTEM_APPEARANCE_BUNDLE = Path("/System/Library/CoreServices/SystemAppearance.bundle")
_CODEX_WEB_SOURCE_CONFIG = Path(
    os.path.expanduser(
        os.getenv(
            "CODEX_WEB_CONFIG_SOURCE", str(_DEFAULT_CODEX_HOME / "config_web.toml")
        )
    )
).expanduser()
_BARE_TOML_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _upsert_root_string(text: str, key: str, value: str) -> str:
    replacement = f'{key} = "{value}"'
    return _upsert_root_literal(text, key, replacement)


def _upsert_root_literal(text: str, key: str, replacement: str) -> str:
    pattern = re.compile(rf"(?m)^(?P<prefix>\s*{re.escape(key)}\s*=\s*).*$")
    if pattern.search(text):
        return pattern.sub(replacement, text, count=1)

    stripped = text.lstrip("\n")
    prefix = "" if text == stripped else text[: len(text) - len(stripped)]
    body = stripped.rstrip()
    if body:
        body = f"{replacement}\n{body}\n"
    else:
        body = f"{replacement}\n"
    return f"{prefix}{body}"


def _delete_root_key(text: str, key: str) -> str:
    pattern = re.compile(rf"(?m)^\s*{re.escape(key)}\s*=.*(?:\n|$)")
    return pattern.sub("", text)


def _delete_table_tree(text: str, table: str) -> str:
    lines = text.splitlines()
    output: list[str] = []
    skipping = False
    table_prefix = f"{table}."

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            table_name = stripped.strip("[]").strip()
            skipping = table_name == table or table_name.startswith(table_prefix)
        if not skipping:
            output.append(line)

    return "\n".join(output) + ("\n" if output else "")


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


def _upsert_table_string(text: str, table: str, key: str, value: str) -> str:
    header = f"[{table}]"
    lines = text.splitlines()
    value_literal = f'"{value}"'

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


def _toml_key(key: str) -> str:
    if _BARE_TOML_KEY_RE.fullmatch(key):
        return key
    return json.dumps(key)


def _toml_value(value: object) -> str:
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value).lower()
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    raise TypeError(f"Unsupported TOML profile value: {type(value).__name__}")


def _render_toml_mapping(mapping: dict[str, object], table_prefix: str = "") -> str:
    lines: list[str] = []
    nested: list[tuple[str, dict[str, object]]] = []

    for key, value in mapping.items():
        if isinstance(value, dict):
            nested.append((key, value))
        else:
            lines.append(f"{_toml_key(key)} = {_toml_value(value)}")

    for key, value in nested:
        if lines and lines[-1] != "":
            lines.append("")
        table_name = (
            f"{table_prefix}.{_toml_key(key)}" if table_prefix else _toml_key(key)
        )
        lines.append(f"[{table_name}]")
        rendered = _render_toml_mapping(value, table_name).rstrip()
        if rendered:
            lines.extend(rendered.splitlines())

    return "\n".join(lines) + ("\n" if lines else "")


def _copy_profile_configs_if_missing(source_home: Path, codex_home: Path) -> None:
    if not source_home.exists():
        return

    for source in source_home.glob("*.config.toml"):
        _copy_if_missing(source, codex_home / source.name)


def _profile_path(codex_home: Path, profile_name: str) -> Path | None:
    if not _PROFILE_NAME_RE.fullmatch(profile_name):
        return None
    return codex_home / f"{profile_name}.config.toml"


def _migrate_legacy_profiles(config_text: str, codex_home: Path) -> str:
    try:
        parsed = tomllib.loads(config_text)
    except tomllib.TOMLDecodeError:
        log.warning(
            "Skipping legacy Codex profile migration for invalid TOML at %s",
            codex_home,
        )
        return _delete_root_key(config_text, "profile")

    profiles = parsed.get("profiles")
    if isinstance(profiles, dict):
        for profile_name, profile_config in profiles.items():
            if not isinstance(profile_name, str) or not isinstance(
                profile_config, dict
            ):
                continue

            profile_path = _profile_path(codex_home, profile_name)
            if profile_path is None:
                log.warning(
                    "Skipping unsupported legacy Codex profile name %r at %s",
                    profile_name,
                    codex_home,
                )
                continue

            if profile_path.exists():
                continue

            try:
                profile_text = _render_toml_mapping(profile_config)
            except TypeError as exc:
                log.warning(
                    "Skipping legacy Codex profile %r at %s: %s",
                    profile_name,
                    codex_home,
                    exc,
                )
                continue

            profile_path.write_text(profile_text, encoding="utf-8")

    config_text = _delete_root_key(config_text, "profile")
    config_text = _delete_table_tree(config_text, "profiles")
    return config_text


def _apply_profile_scalars_to_base_config(
    config_text: str,
    profile_name: str,
    profile_config: dict[str, object],
    codex_home: Path,
) -> str:
    for key, value in profile_config.items():
        if isinstance(value, dict):
            log.info(
                "Skipping nested Codex profile setting %s.%s while flattening %s for app-server",
                profile_name,
                key,
                codex_home,
            )
            continue

        try:
            value_literal = _toml_value(value)
        except TypeError as exc:
            log.warning(
                "Skipping Codex profile setting %s.%s while flattening %s: %s",
                profile_name,
                key,
                codex_home,
                exc,
            )
            continue

        config_text = _upsert_root_literal(
            config_text,
            key,
            f"{_toml_key(key)} = {value_literal}",
        )

    return config_text


def _apply_app_server_profile_to_base_config(config_text: str, codex_home: Path) -> str:
    if CODEX_WEB_PROFILE is None:
        return config_text

    profile_path = _profile_path(codex_home, CODEX_WEB_PROFILE)
    if profile_path is None or not profile_path.exists():
        return config_text

    try:
        profile_config = tomllib.loads(profile_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError:
        log.warning("Skipping invalid Codex profile config at %s", profile_path)
        return config_text

    return _apply_profile_scalars_to_base_config(
        config_text,
        CODEX_WEB_PROFILE,
        profile_config,
        codex_home,
    )


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


def _sandbox_temp_paths() -> list[Path]:
    paths: list[Path] = []
    for value in (os.getenv("TMPDIR"), tempfile.gettempdir()):
        if not value:
            continue

        path = Path(os.path.expanduser(value)).expanduser()
        candidates = [path]
        try:
            resolved = path.resolve()
            if resolved != path:
                candidates.append(resolved)
        except Exception:
            pass

        for candidate in candidates:
            if candidate not in paths:
                paths.append(candidate)

    return paths


def _real_node_module_paths() -> list[Path]:
    paths: list[Path] = []

    for value in os.getenv("NODE_PATH", "").split(os.pathsep):
        if value:
            path = Path(os.path.expanduser(value)).expanduser()
            if path != _NODE_SHIMS_DIR:
                paths.append(path)

    npm_bin = shutil.which("npm")
    if npm_bin:
        try:
            result = subprocess.run(
                [npm_bin, "root", "-g"],
                capture_output=True,
                check=False,
                text=True,
                timeout=5,
            )
            npm_root = result.stdout.strip()
            if npm_root:
                paths.append(Path(os.path.expanduser(npm_root)).expanduser())
        except Exception:
            pass

    paths.extend(
        [
            _REPO_ROOT / "node_modules",
            Path.home() / ".nvm/versions/node/v22.12.0/lib/node_modules",
        ]
    )

    unique_paths: list[Path] = []
    for path in paths:
        if path.exists() and path not in unique_paths:
            unique_paths.append(path)

    return unique_paths


def _node_module_paths() -> list[Path]:
    paths: list[Path] = []
    if _NODE_SHIMS_DIR.exists():
        paths.append(_NODE_SHIMS_DIR)
    paths.extend(_real_node_module_paths())

    unique_paths: list[Path] = []
    for path in paths:
        if path.exists() and path not in unique_paths:
            unique_paths.append(path)

    return unique_paths


def _node_package_dir(package_name: str) -> Path | None:
    for node_modules_path in _real_node_module_paths():
        package_path = node_modules_path / package_name
        if package_path.exists():
            return package_path
    return None


def _write_playwright_shims() -> None:
    _NODE_SHIMS_DIR.mkdir(parents=True, exist_ok=True)
    for package_name in ("playwright", "playwright-core"):
        real_package_dir = _node_package_dir(package_name)
        if real_package_dir is None:
            continue

        shim_dir = _NODE_SHIMS_DIR / package_name
        shim_dir.mkdir(parents=True, exist_ok=True)
        escaped_real_package_dir = (
            str(real_package_dir).replace("\\", "\\\\").replace("'", "\\'")
        )
        shim_code = f"""'use strict';

const realPlaywright = require('{escaped_real_package_dir}');
const extraChromiumArgs = ['--single-process', '--disable-crash-reporter', '--disable-crashpad'];

const withSandboxChromiumArgs = (options = {{}}) => {{
\tconst existingArgs = Array.isArray(options.args) ? options.args : [];
\treturn {{
\t\t...options,
\t\targs: [...new Set([...existingArgs, ...extraChromiumArgs])]
\t}};
}};

const patchChromium = (chromium) => {{
\tif (!chromium || chromium.__openWebuiSandboxPatch) {{
\t\treturn chromium;
\t}}

\tconst launch = chromium.launch?.bind(chromium);
\tif (launch) {{
\t\tchromium.launch = (options = {{}}) => launch(withSandboxChromiumArgs(options));
\t}}

\tconst launchPersistentContext = chromium.launchPersistentContext?.bind(chromium);
\tif (launchPersistentContext) {{
\t\tchromium.launchPersistentContext = (userDataDir, options = {{}}) =>
\t\t\tlaunchPersistentContext(userDataDir, withSandboxChromiumArgs(options));
\t}}

\tObject.defineProperty(chromium, '__openWebuiSandboxPatch', {{ value: true }});
\treturn chromium;
}};

patchChromium(realPlaywright.chromium);

Object.defineProperties(module.exports, Object.getOwnPropertyDescriptors(realPlaywright));
module.exports.chromium = realPlaywright.chromium;
module.exports.default = module.exports;
"""
        (shim_dir / "index.js").write_text(shim_code, encoding="utf-8")
        (shim_dir / "package.json").write_text(
            f'{{"name":"{package_name}","main":"index.js","private":true}}\n',
            encoding="utf-8",
        )


def is_codex_danger_full_access_user(user_email: str | None) -> bool:
    if not user_email:
        return False
    return user_email.strip().lower() in CODEX_DANGER_FULL_ACCESS_EMAILS


def _safe_user_home_component(user_email: str) -> str:
    normalized = user_email.strip().lower()
    sanitized = re.sub(r"[^A-Za-z0-9._-]", "_", normalized).strip("._-")
    return sanitized or "user"


def _get_codex_home_for_user(user_email: str | None) -> Path:
    if is_codex_danger_full_access_user(user_email):
        return CODEX_WEB_HOME / "users" / _safe_user_home_component(user_email or "")
    return CODEX_WEB_HOME


def _get_codex_sandbox_mode_for_user(user_email: str | None) -> str:
    if is_codex_danger_full_access_user(user_email):
        return "danger-full-access"
    return "workspace-write"


def _bootstrap_codex_web_home(
    codex_home: Path = CODEX_WEB_HOME,
    sandbox_mode: str = "workspace-write",
) -> None:
    codex_home.mkdir(parents=True, exist_ok=True)
    _write_playwright_shims()

    config_path = codex_home / "config.toml"
    source_config = _resolve_codex_source_config()
    if source_config is not None:
        source_home = source_config.parent
    else:
        source_home = _DEFAULT_CODEX_HOME

    _copy_if_missing(source_home / "agents", codex_home / "agents")
    _copy_profile_configs_if_missing(source_home, codex_home)

    if config_path.exists():
        config_text = config_path.read_text(encoding="utf-8")
    else:
        if source_config is not None:
            source_text = source_config.read_text(encoding="utf-8")
        else:
            source_text = ""

        config_text = (
            "# Open WebUI dedicated Codex config.\n"
            "# Edit this file to change Open WebUI-only Codex behavior.\n\n"
            + source_text.lstrip()
        )

    config_text = _migrate_legacy_profiles(config_text, codex_home)
    config_text = _apply_app_server_profile_to_base_config(config_text, codex_home)
    config_text = _upsert_root_string(config_text, "approval_policy", "never")
    config_text = _upsert_root_string(config_text, "sandbox_mode", sandbox_mode)
    if sandbox_mode == "danger-full-access":
        config_text = _delete_root_key(config_text, "default_permissions")
    else:
        config_text = _upsert_root_string(
            config_text, "default_permissions", "openwebui_workspace"
        )
    config_text = _upsert_table_bool(
        config_text,
        "sandbox_workspace_write",
        "network_access",
        True,
    )
    config_text = _upsert_table_string(
        config_text,
        "permissions.openwebui_workspace.filesystem",
        '":minimal"',
        "read",
    )
    config_text = _upsert_table_string(
        config_text,
        "permissions.openwebui_workspace.filesystem",
        f'"{_DEFAULT_CODEX_HOME}"',
        "read",
    )
    config_text = _upsert_table_string(
        config_text,
        "permissions.openwebui_workspace.filesystem",
        f'"{_DEFAULT_AGENTS_HOME}"',
        "read",
    )
    config_text = _upsert_table_string(
        config_text,
        "permissions.openwebui_workspace.filesystem",
        f'"{CODEX_WEB_HOME}"',
        "read",
    )
    if codex_home != CODEX_WEB_HOME:
        config_text = _upsert_table_string(
            config_text,
            "permissions.openwebui_workspace.filesystem",
            f'"{codex_home}"',
            "read",
        )
    config_text = _upsert_table_string(
        config_text,
        "permissions.openwebui_workspace.filesystem",
        f'"{_SYSTEM_OPENSSL_CONFIG_DIR}"',
        "read",
    )
    if _SYSTEM_APPEARANCE_BUNDLE.exists():
        config_text = _upsert_table_string(
            config_text,
            "permissions.openwebui_workspace.filesystem",
            f'"{_SYSTEM_APPEARANCE_BUNDLE}"',
            "read",
        )
    for node_module_path in _node_module_paths():
        config_text = _upsert_table_string(
            config_text,
            "permissions.openwebui_workspace.filesystem",
            f'"{node_module_path}"',
            "read",
        )
    for temp_path in _sandbox_temp_paths():
        config_text = _upsert_table_string(
            config_text,
            "permissions.openwebui_workspace.filesystem",
            f'"{temp_path}"',
            "write",
        )
    if _DEFAULT_PLAYWRIGHT_CACHE.exists():
        config_text = _upsert_table_string(
            config_text,
            "permissions.openwebui_workspace.filesystem",
            f'"{_DEFAULT_PLAYWRIGHT_CACHE}"',
            "write",
        )
    config_text = _upsert_table_string(
        config_text,
        'permissions.openwebui_workspace.filesystem.":project_roots"',
        '"."',
        "write",
    )
    config_text = _upsert_table_bool(
        config_text,
        "permissions.openwebui_workspace.network",
        "enabled",
        True,
    )

    config_path.write_text(config_text.rstrip() + "\n", encoding="utf-8")

    log.info(
        "Initialized Open WebUI Codex home at %s with sandbox_mode=%s",
        codex_home,
        sandbox_mode,
    )


def get_codex_app_server_env(user_email: str | None = None) -> dict[str, str]:
    codex_home = _get_codex_home_for_user(user_email)
    sandbox_mode = _get_codex_sandbox_mode_for_user(user_email)
    _bootstrap_codex_web_home(codex_home, sandbox_mode)
    env = {"CODEX_HOME": str(codex_home)}
    node_paths = [str(path) for path in _node_module_paths()]
    if node_paths:
        env["NODE_PATH"] = os.pathsep.join(node_paths)
    if _DEFAULT_PLAYWRIGHT_CACHE.exists():
        env["PLAYWRIGHT_BROWSERS_PATH"] = str(_DEFAULT_PLAYWRIGHT_CACHE)
    return env


def get_codex_app_server_config_overrides(
    user_email: str | None = None,
) -> tuple[str, ...]:
    if not is_codex_danger_full_access_user(user_email):
        return ()
    return (
        'sandbox_mode="danger-full-access"',
        'approval_policy="never"',
    )


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
