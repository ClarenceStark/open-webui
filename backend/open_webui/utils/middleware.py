import copy
import time
import logging
import sys
import os
import base64
import io
import mimetypes
import textwrap

import asyncio
import aiohttp
from aiocache import cached
from typing import Any, Optional
import random
import json
import html
import inspect
import re
import ast

from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor


from fastapi import Request, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from starlette.responses import Response, StreamingResponse, JSONResponse


from open_webui.utils.misc import is_string_allowed
from open_webui.models.oauth_sessions import OAuthSessions
from open_webui.models.chats import Chats
from open_webui.models.files import Files
from open_webui.models.folders import Folders
from open_webui.models.groups import Groups
from open_webui.models.users import Users
from open_webui.socket.main import (
    get_event_call,
    get_event_emitter,
)
from open_webui.routers.tasks import (
    generate_queries,
    generate_title,
    generate_follow_ups,
    generate_image_prompt,
    generate_chat_tags,
)
from open_webui.routers.retrieval import (
    process_web_search,
    SearchForm,
)
from open_webui.utils.tools import get_builtin_tools
from open_webui.routers.images import (
    image_generations,
    CreateImageForm,
    image_edits,
    EditImageForm,
)
from open_webui.routers.pipelines import (
    process_pipeline_inlet_filter,
    process_pipeline_outlet_filter,
)
from open_webui.routers.memories import query_memory, QueryMemoryForm

from open_webui.utils.webhook import post_webhook
from open_webui.utils.files import (
    convert_markdown_base64_images,
    get_file_url_from_base64,
    get_image_base64_from_url,
    get_image_url_from_base64,
)
from open_webui.routers.files import upload_file_handler
from open_webui.storage.provider import Storage


from open_webui.models.users import UserModel
from open_webui.models.functions import Functions
from open_webui.models.models import Models

from open_webui.retrieval.utils import get_sources_from_items


from open_webui.utils.sanitize import sanitize_code
from open_webui.utils.chat import generate_chat_completion
from open_webui.utils.file_skills import (
    discover_file_skills,
    get_file_skills_dir,
)
from open_webui.utils.task import (
    get_task_model_id,
    rag_template,
    tools_function_calling_generation_template,
)
from open_webui.utils.misc import (
    deep_update,
    extract_urls,
    get_message_list,
    add_or_update_system_message,
    add_or_update_user_message,
    set_last_user_message_content,
    get_last_user_message,
    get_last_user_message_item,
    get_last_assistant_message,
    get_system_message,
    replace_system_message_content,
    prepend_to_first_user_message_content,
    convert_logit_bias_input_to_json,
    get_content_from_message,
    convert_output_to_messages,
)
from open_webui.utils.tools import (
    get_tools,
    get_updated_tool_function,
    get_terminal_tools,
)
from open_webui.utils.access_control import has_connection_access
from open_webui.utils.plugin import load_function_module_by_id
from open_webui.utils.filter import (
    get_sorted_filter_ids,
    process_filter_functions,
)
from open_webui.utils.code_interpreter import execute_code_jupyter
from open_webui.utils.payload import apply_system_prompt_to_body
from open_webui.utils.response import normalize_usage
from open_webui.utils.mcp.client import MCPClient


from open_webui.config import (
    CACHE_DIR,
    DEFAULT_VOICE_MODE_PROMPT_TEMPLATE,
    DEFAULT_TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE,
    DEFAULT_CODE_INTERPRETER_PROMPT,
    CODE_INTERPRETER_PYODIDE_PROMPT,
    CODE_INTERPRETER_BLOCKED_MODULES,
)
from open_webui.env import (
    AIOHTTP_CLIENT_TIMEOUT,
    GLOBAL_LOG_LEVEL,
    ENABLE_CHAT_RESPONSE_BASE64_IMAGE_URL_CONVERSION,
    CHAT_RESPONSE_STREAM_DELTA_CHUNK_SIZE,
    CHAT_RESPONSE_MAX_TOOL_CALL_RETRIES,
    BYPASS_MODEL_ACCESS_CONTROL,
    ENABLE_REALTIME_CHAT_SAVE,
    ENABLE_QUERIES_CACHE,
    RAG_SYSTEM_CONTEXT,
    ENABLE_FORWARD_USER_INFO_HEADERS,
    FORWARD_SESSION_INFO_HEADER_CHAT_ID,
    FORWARD_SESSION_INFO_HEADER_MESSAGE_ID,
)
from open_webui.utils.headers import include_user_info_headers
from open_webui.constants import TASKS

logging.basicConfig(stream=sys.stdout, level=GLOBAL_LOG_LEVEL)
log = logging.getLogger(__name__)


DEFAULT_REASONING_TAGS = [
    ("<think>", "</think>"),
    ("<thinking>", "</thinking>"),
    ("<reason>", "</reason>"),
    ("<reasoning>", "</reasoning>"),
    ("<thought>", "</thought>"),
    ("<Thought>", "</Thought>"),
    ("<|begin_of_thought|>", "<|end_of_thought|>"),
    ("◁think▷", "◁/think▷"),
]
DEFAULT_SOLUTION_TAGS = [("<|begin_of_solution|>", "<|end_of_solution|>")]
DEFAULT_CODE_INTERPRETER_TAGS = [("<code_interpreter>", "</code_interpreter>")]


def output_id(prefix: str) -> str:
    """Generate OR-style ID: prefix + 24-char hex UUID."""
    return f"{prefix}_{uuid4().hex[:24]}"


def _split_tool_calls(
    tool_calls: list[dict],
) -> list[dict]:
    """Expand tool calls whose arguments contain multiple back-to-back JSON objects.

    Some models (e.g. GPT-5.4) send multiple complete JSON argument objects
    under the same tool call index, producing concatenated invalid JSON like:
        '{"query":"A","count":5}{"query":"B","count":5}'

    Each such tool call is split into separate entries so each gets executed
    independently. Single-object arguments pass through unchanged.
    """

    def split_json_objects(raw: str) -> list[str]:
        decoder = json.JSONDecoder()
        results = []
        position = 0

        while position < len(raw):
            while position < len(raw) and raw[position].isspace():
                position += 1
            if position >= len(raw):
                break
            try:
                _, end = decoder.raw_decode(raw, position)
                results.append(raw[position:end].strip())
                position = end
            except json.JSONDecodeError:
                return [raw]

        return results or [raw]

    expanded = []
    for tool_call in tool_calls:
        arguments = tool_call.get("function", {}).get("arguments", "")
        split_arguments = split_json_objects(arguments)

        if len(split_arguments) <= 1:
            expanded.append(tool_call)
        else:
            for argument in split_arguments:
                cloned = copy.deepcopy(tool_call)
                cloned["id"] = f"call_{uuid4().hex[:24]}"
                cloned["function"]["arguments"] = argument
                expanded.append(cloned)

    return expanded


def get_model_api_config(request: Request, model: dict) -> dict:
    idx = model.get("urlIdx")
    if idx is None:
        return {}

    url = request.app.state.config.OPENAI_API_BASE_URLS[idx]
    return request.app.state.config.OPENAI_API_CONFIGS.get(
        str(idx),
        request.app.state.config.OPENAI_API_CONFIGS.get(url, {}),
    )


def is_responses_api_config(api_config: Optional[dict]) -> bool:
    api_type = (api_config or {}).get("api_type", "").lower()
    return api_type in ("responses", "responses_v1")


def get_native_web_search_tool(api_config: Optional[dict], user: Optional[UserModel]):
    if not is_responses_api_config(api_config):
        return None

    tool = {
        "type": "web_search",
        "search_context_size": "medium",
    }

    user_settings = getattr(user, "settings", None) if user else None
    if hasattr(user_settings, "model_dump"):
        user_settings = user_settings.model_dump(exclude_none=True)
    elif user_settings is None:
        user_settings = {}

    timezone = (
        user_settings.get("ui", {}).get("timezone")
        or getattr(user, "timezone", None)
        if user
        else None
    )
    if timezone:
        tool["user_location"] = {
            "type": "approximate",
            "timezone": timezone,
        }

    return tool


def get_default_terminal_id(
    request: Request,
    user: Optional[UserModel],
) -> Optional[str]:
    if user is None:
        return None

    connections = request.app.state.config.TERMINAL_SERVER_CONNECTIONS or []
    if not connections:
        return None

    try:
        user_group_ids = {group.id for group in Groups.get_groups_by_member_id(user.id)}
    except Exception:
        user_group_ids = set()

    accessible_connections = [
        connection
        for connection in connections
        if connection.get("id")
        and connection.get("url")
        and has_connection_access(user, connection, user_group_ids)
    ]
    if not accessible_connections:
        return None

    preferred_connection = next(
        (
            connection
            for connection in accessible_connections
            if connection.get("id") == "sandbox"
        ),
        None,
    )

    return (preferred_connection or accessible_connections[0]).get("id")


def get_web_search_status_from_response_item(item: dict) -> Optional[dict]:
    if item.get("type") != "web_search_call":
        return None

    action = item.get("action") or {}
    action_type = action.get("type", "search")
    status = item.get("status", "in_progress")
    done = status in {"completed", "failed", "cancelled"}

    if action_type == "open_page":
        url = action.get("url", "")
        return {
            "action": "open_page",
            "description": "Opening page",
            "done": done,
            "url": url,
            "urls": [url] if url else [],
        }

    if action_type == "find_in_page":
        url = action.get("url", "")
        pattern = action.get("pattern", "")
        return {
            "action": "find_in_page",
            "description": "Finding in page",
            "done": done,
            "url": url,
            "urls": [url] if url else [],
            "pattern": pattern,
        }

    queries = action.get("queries") or []
    query = action.get("query", "")
    if not query and not any(
        isinstance(candidate, str) and candidate.strip() for candidate in queries
    ):
        return None
    return {
        "action": "web_search",
        "description": "Searching the web",
        "done": done,
        "query": query,
        "queries": queries,
    }


def build_terminal_server_auth(
    request: Request,
    user: UserModel,
    connection: dict,
    extra_params: dict,
    metadata: Optional[dict] = None,
) -> tuple[dict, dict]:
    headers = {"X-User-Id": user.id}
    cookies = {}
    auth_type = connection.get("auth_type", "bearer")
    metadata = metadata or {}

    chat_id = metadata.get("chat_id")
    session_id = metadata.get("session_id")
    message_id = metadata.get("message_id")
    terminal_scope = None
    if chat_id:
        terminal_scope = f"chat_{chat_id}"
        headers["X-Chat-Id"] = str(chat_id)
    elif session_id:
        terminal_scope = f"session_{session_id}"

    if session_id:
        headers["X-Session-Id"] = str(session_id)
    if message_id:
        headers["X-Message-Id"] = str(message_id)
    if terminal_scope:
        headers["X-Terminal-Scope"] = terminal_scope

    if auth_type == "bearer":
        key = connection.get("key", "")
        if key:
            headers["Authorization"] = f"Bearer {key}"
    elif auth_type == "session":
        cookies = request.cookies
        token = getattr(getattr(request.state, "token", None), "credentials", None)
        if token:
            headers["Authorization"] = f"Bearer {token}"
    elif auth_type == "system_oauth":
        cookies = request.cookies
        oauth_token = extra_params.get("__oauth_token__", None) or {}
        access_token = oauth_token.get("access_token", "")
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"

    return headers, cookies


def resolve_terminal_server_binding(
    request: Request,
    user: UserModel,
    metadata: dict,
    extra_params: dict,
) -> Optional[dict]:
    terminal_id = metadata.get("terminal_id")
    if not terminal_id:
        return None

    connections = request.app.state.config.TERMINAL_SERVER_CONNECTIONS or []
    connection = next((c for c in connections if c.get("id") == terminal_id), None)
    if connection is not None:
        if not has_connection_access(user, connection):
            raise HTTPException(status_code=403, detail="Access denied to sandbox terminal")

        headers, cookies = build_terminal_server_auth(
            request, user, connection, extra_params, metadata
        )
        return {
            "url": connection.get("url", "").rstrip("/"),
            "headers": headers,
            "cookies": cookies,
            "source": "system",
        }

    direct_tool_servers = metadata.get("tool_servers", None) or []
    direct_server = next(
        (
            server
            for server in direct_tool_servers
            if server.get("url") == terminal_id
            and any(
                spec.get("name")
                in {
                    "exec_command",
                    "run_command",
                    "write_stdin",
                    "list_files",
                    "read_file",
                }
                for spec in server.get("specs", []) or []
            )
        ),
        None,
    )
    if direct_server is None:
        return None

    headers, cookies = build_terminal_server_auth(
        request, user, direct_server, extra_params, metadata
    )
    return {
        "url": direct_server.get("url", "").rstrip("/"),
        "headers": headers,
        "cookies": cookies,
        "source": "direct",
    }


def build_terminal_attachment_prompt(mounted_files: list[dict], target_dir: str) -> str:
    has_image_file = any(
        str(mounted_file.get("content_type") or "").startswith("image/")
        for mounted_file in mounted_files
    )
    lines = [
        "The user's uploaded Open WebUI files have been copied into the sandbox.",
        f"Sandbox attachment directory: {target_dir}",
        "Use these exact paths when reading or modifying the uploaded files:",
    ]
    for mounted_file in mounted_files:
        lines.append(f"- {mounted_file['name']}: {mounted_file['path']}")
    lines.append(
        "Prefer these sandbox paths over guessing host filesystem paths or asking for re-uploads."
    )
    lines.append(
        "Do not use guessed paths such as /mnt/data, /tmp, /Users/... or any host filesystem path unless it exactly matches one of the sandbox paths listed above."
    )
    lines.append(
        "The sandbox Python environment is persistent across commands. If your script fails because a package/module is missing, proactively install it with python3 -m pip install <package> in the sandbox, then retry."
    )
    lines.append(
        "If a required system runtime or CLI tool is missing (for example node, npm, ffmpeg, libreoffice, or poppler), proactively install it yourself with mkdir -p /tmp/apt-archives/partial && apt-get update && apt-get -o Dir::Cache::Archives=/tmp/apt-archives install -y <packages>, verify it with command -v <tool>, and then retry."
    )
    lines.append(
        "When you create, export, or modify a file for the user in the sandbox, return the final file to the user in chat before finishing. Prefer calling download_artifact for the final deliverable."
    )
    lines.append(
        "Do not claim that a file is attached, uploaded, sent, shared, or ready to download unless that file has actually been returned to the chat."
    )
    lines.append(
        "Automatic upload from printing an exact output path is only a fallback. For user-facing deliverables such as docx, pdf, pptx, xlsx, csv, zip, or generated images, call download_artifact explicitly unless the file is already confirmed in chat."
    )
    if has_image_file:
        lines.append(
            "Image-editing workflow: use the model's vision on the attached image to locate the target, then use exec_command with python3 plus Pillow/matplotlib to create a NEW annotated image file in the sandbox."
        )
        lines.append(
            "When generating Python, ensure control-flow blocks are correctly indented and the script is runnable as-is."
        )
        lines.append(
            "Do not stop after view_image when the user asked to circle, box, arrow, label, annotate, edit, or modify an image. view_image is read-only preview only."
        )
        lines.append(
            "After generating the new image file, call download_artifact to return it to the user. You may call view_image afterwards only to preview the generated output."
        )
        lines.append(
            "If your command prints the exact output file path on its own line, the system can auto-upload that generated file for the user; do not call list_files just to verify it exists."
        )
        lines.append(
            "If exec_command returns a non-zero exit_code or any stderr output, inspect the error, fix the script, and retry before answering the user."
        )
        lines.append(
            "When stderr shows ModuleNotFoundError or ImportError, install the missing package first, then rerun the image-editing script."
        )

    return "<sandbox_files>\n" + "\n".join(lines) + "\n</sandbox_files>"


def sanitize_terminal_scope_component(value: Optional[str], fallback: str) -> str:
    raw = str(value or fallback)
    return re.sub(r"[^A-Za-z0-9._-]", "_", raw)


def build_terminal_session_scope(metadata: Optional[dict]) -> str:
    metadata = metadata or {}
    if metadata.get("chat_id"):
        return f"chat_{sanitize_terminal_scope_component(metadata.get('chat_id'), 'chat')}"

    return (
        f"session_{sanitize_terminal_scope_component(metadata.get('session_id'), 'default')}"
    )


def build_terminal_session_root(metadata: Optional[dict]) -> str:
    return f"/workspace/sessions/{build_terminal_session_scope(metadata)}"


def build_terminal_execution_prompt(metadata: Optional[dict]) -> str:
    session_root = build_terminal_session_root(metadata)
    lines = [
        "You are operating inside a sandbox terminal scoped to the current chat session.",
        f"Treat this session root as your working boundary: {session_root}",
        "For Python work, prefer a project-local .venv inside the current working directory or repository within this session.",
        "If .venv does not exist yet, you may create and configure it yourself with python3 -m venv .venv, upgrade pip inside it, install the required packages there, and then run commands with .venv/bin/python or .venv/bin/pip.",
        "If a local .venv already exists, reuse it instead of creating another environment.",
        "This sandbox is allowed to install system packages. If a required runtime or CLI tool is missing, proactively run mkdir -p /tmp/apt-archives/partial && apt-get update && apt-get -o Dir::Cache::Archives=/tmp/apt-archives install -y <packages>, verify with command -v <tool>, and then retry the original task.",
        "Prefer local .venv installs for Python packages, and use apt-get for OS-level packages and runtimes that cannot be satisfied inside the local project environment.",
        "Do not rely on the system Python or shared environments such as /workspace/venvs/default or /workspace/venvs/data unless the user explicitly requests that exact environment.",
        "Do not read from or write to sibling session directories such as /workspace/sessions/chat_* or /workspace/sessions/session_* that are outside the current session root.",
        "Do not inspect, reuse, or depend on files from another session's inputs, outputs, artifacts, or tmp directories.",
        "If a required file is missing from the current session, ask the user to upload it again or copy it into the current session workspace before continuing.",
        "When you create, export, or modify a file for the user in the sandbox, you must return the final file to the user in chat before finishing. Prefer calling download_artifact for the final deliverable.",
        "Do not merely leave the file in the sandbox and mention its path. Do not claim that a file is attached, uploaded, sent, shared, or ready to download unless it has actually been returned to the chat.",
        "Automatic upload from printing an exact output path is a fallback, not the primary plan. For user-facing deliverables such as docx, pdf, pptx, xlsx, csv, zip, or generated images, call download_artifact explicitly unless the file is already confirmed in chat.",
        "The /skills/ directory contains shared skill definitions. You may read SKILL.md and related files from this directory when using a skill.",
        "The /skills/ directory is readable from both shell commands and file tools such as list_files and read_file.",
    ]
    return "<sandbox_rules>\n" + "\n".join(lines) + "\n</sandbox_rules>"


def render_file_skills_section(file_skills, skills_dir) -> Optional[str]:
    if not file_skills:
        return None

    skills_dir = skills_dir.expanduser().resolve()
    lines = [
        "## Skills",
        "A skill is a set of local instructions to follow that is stored in a `SKILL.md` file. Below is the list of skills that can be used. Each entry includes a name, description, and file path so you can open the source for full instructions when using a specific skill.",
        "### Available skills",
    ]

    for skill in file_skills:
        try:
            relative_path = skill.skill_md_path.relative_to(skills_dir).as_posix()
            display_path = f"/skills/{relative_path}"
        except ValueError:
            display_path = skill.skill_md_path.as_posix()
        lines.append(f"- {skill.name}: {skill.description} (file: {display_path})")

    lines.extend(
        [
            "### How to use skills",
            "- Discovery: The list above is the skills available in this session (name + description + file path). Skill bodies live on disk at the listed paths.",
            "- Trigger rules: If the user names a skill (with `$SkillName` or plain text) OR the task clearly matches a skill's description shown above, you must use that skill for that turn. Multiple mentions mean use them all. Do not carry skills across turns unless re-mentioned.",
            "- Missing/blocked: If a named skill isn't in the list or the path can't be read, say so briefly and continue with the best fallback.",
            "- How to use a skill (progressive disclosure):",
            "  1) After deciding to use a skill, open its `SKILL.md`. Read only enough to follow the workflow.",
            "  2) When `SKILL.md` references relative paths (e.g., `scripts/foo.py`), resolve them relative to the skill directory listed above first, and only consider other paths if needed.",
            "  3) If `SKILL.md` points to extra folders such as `references/`, load only the specific files needed for the request; don't bulk-load everything.",
            "  4) If `scripts/` exist, prefer running or patching them instead of retyping large code blocks.",
            "  5) If `assets/` or templates exist, reuse them instead of recreating from scratch.",
            "- Coordination and sequencing:",
            "  - If multiple skills apply, choose the minimal set that covers the request and state the order you'll use them.",
            "  - Announce which skill(s) you're using and why (one short line). If you skip an obvious skill, say why.",
            "- Context hygiene:",
            "  - Keep context small: summarize long sections instead of pasting them; only load extra files when needed.",
            "  - Avoid deep reference-chasing: prefer opening only files directly linked from `SKILL.md` unless you're blocked.",
            "  - When variants exist (frameworks, providers, domains), pick only the relevant reference file(s) and note that choice.",
            "- Safety and fallback: If a skill can't be applied cleanly (missing files, unclear instructions), state the issue, pick the next-best approach, and continue.",
        ]
    )

    return "<skills_instructions>\n" + "\n".join(lines) + "\n</skills_instructions>"


def get_triggered_file_skills(file_skills, prompt: Optional[str]):
    if not prompt:
        return []

    matched = []
    for skill in file_skills:
        pattern = rf"(?<![A-Za-z0-9_-])\${re.escape(skill.name)}(?![A-Za-z0-9_-])"
        if re.search(pattern, prompt, flags=re.IGNORECASE):
            matched.append(skill)
    return matched


TERMINAL_TOOL_NAMES = {
    "exec_command",
    "run_command",
    "write_stdin",
    "list_files",
    "read_file",
    "apply_patch",
    "view_image",
    "download_artifact",
    "display_file",
    "write_file",
    "replace_file_content",
}


def merge_chat_file_items(*groups: Optional[list[dict]]) -> list[dict]:
    merged = []
    seen = set()

    for group in groups:
        for item in group or []:
            if not isinstance(item, dict):
                continue

            identity = (
                item.get("id")
                or item.get("url")
                or (
                    item.get("file", {}) or {}
                ).get("id")
                or f"{item.get('name')}::{item.get('content_type')}"
            )

            if identity in seen:
                continue

            seen.add(identity)
            merged.append(item)

    return merged


def is_terminal_tool_name(tool_name: str) -> bool:
    return tool_name in TERMINAL_TOOL_NAMES


def filter_terminal_tool_history_output(output_items: list[dict]) -> list[dict]:
    if not output_items:
        return output_items

    terminal_call_ids = set()
    for item in output_items:
        if (
            isinstance(item, dict)
            and item.get("type") == "function_call"
            and is_terminal_tool_name(item.get("name", ""))
        ):
            call_id = item.get("call_id") or item.get("id")
            if call_id:
                terminal_call_ids.add(call_id)

    if not terminal_call_ids:
        return output_items

    filtered_items = []
    for item in output_items:
        if not isinstance(item, dict):
            filtered_items.append(item)
            continue

        item_type = item.get("type")
        if item_type == "function_call" and is_terminal_tool_name(item.get("name", "")):
            continue
        if item_type == "function_call_output" and item.get("call_id") in terminal_call_ids:
            continue

        filtered_items.append(item)

    return filtered_items


def resolve_terminal_path_guess(path: str, metadata: dict) -> str:
    if not isinstance(path, str) or not path:
        return path

    mounted_files = metadata.get("terminal_files") or []
    target_dir = metadata.get("terminal_files_dir")

    normalized_path = path.removeprefix("sandbox:")
    basename = os.path.basename(normalized_path)
    stem, _, _ext = basename.partition(".")
    basename_lower = basename.lower()

    for mounted_file in mounted_files:
        mounted_path = mounted_file.get("path")
        if not mounted_path:
            continue

        mounted_name = str(mounted_file.get("name") or "")
        mounted_id = str(mounted_file.get("id") or "")

        if normalized_path == mounted_path:
            return mounted_path

        if mounted_id and (
            basename == mounted_id
            or stem == mounted_id
            or basename.startswith(f"{mounted_id}.")
        ):
            return mounted_path

        if mounted_name and basename_lower == mounted_name.lower():
            return mounted_path

    if target_dir and normalized_path in {
        "/mnt/data",
        "/mnt/data/",
        "/tmp",
        "/tmp/",
    }:
        return target_dir

    return path


def normalize_terminal_command_paths(command: str, metadata: dict) -> str:
    if not isinstance(command, str) or not command:
        return command

    def replace_match(match: re.Match) -> str:
        candidate = match.group(0)
        resolved = resolve_terminal_path_guess(candidate, metadata)
        return resolved if resolved != candidate else candidate

    normalized = re.sub(r"(?:sandbox:)?/mnt/data/[^\s\"'`]+", replace_match, command)

    target_dir = metadata.get("terminal_files_dir")
    if target_dir:
        normalized = normalized.replace("/mnt/data/", f"{target_dir.rstrip('/')}/")
        normalized = normalized.replace("sandbox:/mnt/data/", f"{target_dir.rstrip('/')}/")
        normalized = re.sub(r"(?<!\S)/mnt/data(?!\S)", target_dir, normalized)

    return normalized


def normalize_terminal_tool_params(
    tool_name: str, tool_params: dict, metadata: dict
) -> dict:
    if not metadata.get("terminal_id") or not is_terminal_tool_name(tool_name):
        return tool_params

    normalized_params = copy.deepcopy(tool_params)

    for key in ("path", "directory", "workdir"):
        value = normalized_params.get(key)
        if isinstance(value, str):
            normalized_value = resolve_terminal_path_guess(value, metadata)
            if normalized_value != value:
                normalized_params[key] = normalized_value

    for key in ("cmd", "command"):
        value = normalized_params.get(key)
        if isinstance(value, str):
            normalized_value = normalize_terminal_command_paths(value, metadata)
            if normalized_value != value:
                normalized_params[key] = normalized_value

    return normalized_params


async def sync_chat_files_to_terminal(
    request: Request,
    files: list[dict],
    user: UserModel,
    metadata: dict,
    extra_params: dict,
) -> list[dict]:
    synced_sources = []
    seen_file_ids = set()
    for file_item in files or []:
        if not isinstance(file_item, dict):
            continue

        file_id = file_item.get("id")
        if not file_id or file_id in seen_file_ids:
            continue
        seen_file_ids.add(file_id)

        db_file = Files.get_file_by_id_and_user_id(file_id, user.id)
        if db_file is None and user.role == "admin":
            db_file = Files.get_file_by_id(file_id)
        if db_file is None or not db_file.path:
            continue

        filename = db_file.filename or file_item.get("name") or file_id
        content_type = (
            file_item.get("content_type")
            or (db_file.meta or {}).get("content_type")
            or mimetypes.guess_type(filename)[0]
            or "application/octet-stream"
        )

        synced_sources.append(
            {
                "id": file_id,
                "filename": filename,
                "content_type": content_type,
                "local_path": Storage.get_file(db_file.path),
            }
        )

    if not synced_sources:
        return []

    terminal_server = resolve_terminal_server_binding(
        request, user, metadata, extra_params
    )
    if terminal_server is None:
        raise HTTPException(
            status_code=400,
            detail="No sandbox terminal is available for attached files",
        )

    session_scope = build_terminal_session_scope(metadata)

    target_dir = (
        f"/workspace/sessions/"
        f"{session_scope}/"
        f"inputs/"
        f"{sanitize_terminal_scope_component(metadata.get('message_id'), 'message')}"
    )

    headers = copy.deepcopy(terminal_server.get("headers", {}) or {})
    cookies = copy.deepcopy(terminal_server.get("cookies", {}) or {})
    upload_headers = {
        key: value for key, value in headers.items() if key.lower() != "content-type"
    }

    mounted_files = []
    async with aiohttp.ClientSession(
        trust_env=True,
        timeout=aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT),
    ) as session:
        async with session.post(
            f"{terminal_server['url']}/files/mkdir",
            json={"path": target_dir},
            headers={**headers, "Content-Type": "application/json"},
            cookies=cookies,
        ) as response:
            if response.status >= 400:
                error_text = await response.text()
                raise HTTPException(
                    status_code=400,
                    detail=f"Failed to prepare sandbox attachment directory: HTTP {response.status}: {error_text}",
                )

        for source in synced_sources:
            form = aiohttp.FormData()
            with open(source["local_path"], "rb") as handle:
                form.add_field(
                    "file",
                    handle,
                    filename=source["filename"],
                    content_type=source["content_type"],
                )
                async with session.post(
                    f"{terminal_server['url']}/files/upload",
                    params={"directory": target_dir},
                    data=form,
                    headers=upload_headers,
                    cookies=cookies,
                ) as response:
                    if response.status >= 400:
                        error_text = await response.text()
                        raise HTTPException(
                            status_code=400,
                            detail=f"Failed to copy '{source['filename']}' into the sandbox: HTTP {response.status}: {error_text}",
                        )
                    payload = await response.json()

            mounted_path = payload.get("path") or (
                f"{target_dir.rstrip('/')}/{source['filename']}"
            )
            mounted_files.append(
                {
                    "id": source["id"],
                    "name": source["filename"],
                    "path": mounted_path,
                    "content_type": source["content_type"],
                }
            )

    metadata["terminal_files"] = mounted_files
    metadata["terminal_files_dir"] = target_dir
    return mounted_files


def build_tool_execution_status(
    tool_name: str,
    tool_params: dict,
    done: bool = False,
    tool_result: Any = None,
) -> Optional[dict]:
    descriptions = {
        "exec_command": ("Running command", "Command finished"),
        "run_command": ("Running command", "Command finished"),
        "write_stdin": ("Sending input to session", "Input sent"),
        "list_files": ("Listing files", "Files listed"),
        "read_file": ("Reading file", "File read"),
        "download_artifact": ("Preparing artifact", "Artifact ready"),
        "view_image": ("Preparing image", "Image ready"),
        "apply_patch": ("Applying patch", "Patch applied"),
        "write_file": ("Writing file", "File written"),
        "replace_file_content": ("Updating file", "File updated"),
        "display_file": ("Preparing file preview", "File preview ready"),
    }

    if tool_name not in descriptions:
        return None

    pending_text, done_text = descriptions[tool_name]
    status = {
        "action": tool_name,
        "description": done_text if done else pending_text,
        "done": done,
    }

    if tool_name in {"download_artifact", "view_image"}:
        status["hidden"] = True

    parsed_result = tool_result
    if isinstance(tool_result, tuple) and len(tool_result) >= 1:
        parsed_result = tool_result[0]

    if done and isinstance(parsed_result, dict):
        is_running = parsed_result.get("running") is True
        has_error = bool(parsed_result.get("error"))
        exit_code = parsed_result.get("exit_code")
        failed = exit_code not in (None, 0) or has_error

        if tool_name in {"exec_command", "run_command"}:
            if is_running:
                status["description"] = "Command still running"
                status["done"] = False
            elif failed:
                status["description"] = "Command failed"
        elif tool_name == "write_stdin":
            if is_running:
                status["description"] = "Command still running"
                status["done"] = False
            elif failed:
                status["description"] = "Session update failed"

    cmd = tool_params.get("cmd") or tool_params.get("command")
    if cmd:
        status["command"] = cmd
    path = tool_params.get("path") or tool_params.get("directory")
    if path:
        status["path"] = path
    workdir = tool_params.get("workdir")
    if workdir:
        status["workdir"] = workdir
    session_id = tool_params.get("session_id")
    if session_id:
        status["session_id"] = session_id
    pattern = tool_params.get("pattern")
    if pattern:
        status["pattern"] = pattern

    return status


async def emit_tool_execution_status(
    event_emitter,
    tool_name: str,
    tool_params: dict,
    done: bool = False,
    tool_result: Any = None,
):
    if not event_emitter:
        return

    status = build_tool_execution_status(
        tool_name, tool_params, done=done, tool_result=tool_result
    )
    if not status:
        return

    await event_emitter({"type": "status", "data": status})


def unpack_tool_server_result(tool_result: Any) -> tuple[Any, Optional[dict]]:
    if isinstance(tool_result, tuple):
        if len(tool_result) == 0:
            return None, None
        if len(tool_result) == 1:
            return tool_result[0], None
        return tool_result[0], tool_result[1]
    return tool_result, None


def pack_tool_server_result(tool_payload: Any, response_headers: Optional[dict]) -> Any:
    if response_headers is None:
        return tool_payload
    return (tool_payload, response_headers)


async def wait_for_terminal_command_completion(
    tools: dict[str, dict],
    tool_function_name: str,
    tool_result: Any,
    max_polls: int = 180,
    yield_time_ms: int = 1000,
) -> Any:
    if tool_function_name not in {"exec_command", "run_command"}:
        return tool_result

    tool_payload, response_headers = unpack_tool_server_result(tool_result)
    if not isinstance(tool_payload, dict):
        return tool_result

    session_id = tool_payload.get("session_id")
    if not session_id or tool_payload.get("running") is not True:
        return tool_result

    write_stdin_tool = tools.get("write_stdin")
    if not write_stdin_tool:
        return tool_result

    write_stdin_callable = write_stdin_tool.get("callable")
    if not write_stdin_callable:
        return tool_result

    latest_payload = tool_payload

    for _ in range(max_polls):
        poll_result = await write_stdin_callable(
            session_id=session_id,
            chars="",
            yield_time_ms=yield_time_ms,
        )
        polled_payload, polled_headers = unpack_tool_server_result(poll_result)

        if polled_headers is not None:
            response_headers = polled_headers

        if not isinstance(polled_payload, dict):
            return pack_tool_server_result(polled_payload, response_headers)

        latest_payload = polled_payload

        if latest_payload.get("running") is not True:
            return pack_tool_server_result(latest_payload, response_headers)

    latest_payload = {
        **latest_payload,
        "timed_out": True,
        "message": latest_payload.get("message")
        or f"Command is still running after waiting {max_polls} poll(s).",
    }
    return pack_tool_server_result(latest_payload, response_headers)


def get_citation_source_from_tool_result(
    tool_name: str, tool_params: dict, tool_result: str, tool_id: str = ""
) -> list[dict]:
    """
    Parse a tool's result and convert it to source dicts for citation display.

    Follows the source format conventions from get_sources_from_items:
    - source: file/item info object with id, name, type
    - document: list of document contents
    - metadata: list of metadata objects with source, file_id, name fields

    Returns a list of sources (usually one, but query_knowledge_files may return multiple).
    """
    _EXPECTS_LIST = {"search_web", "query_knowledge_files"}
    _EXPECTS_DICT = {"view_knowledge_file"}

    try:
        try:
            tool_result = json.loads(tool_result)
        except (json.JSONDecodeError, TypeError):
            pass  # keep tool_result as-is (e.g. fetch_url returns plain text)
        if isinstance(tool_result, dict) and "error" in tool_result:
            return []

        # Validate tool_result type based on what the branch expects
        if tool_name in _EXPECTS_LIST and not isinstance(tool_result, list):
            return []
        elif tool_name in _EXPECTS_DICT and not isinstance(tool_result, dict):
            return []

        if tool_name == "search_web":
            # Parse JSON array: [{"title": "...", "link": "...", "snippet": "..."}]
            results = tool_result
            documents = []
            metadata = []

            for result in results:
                title = result.get("title", "")
                link = result.get("link", "")
                snippet = result.get("snippet", "")

                documents.append(f"{title}\n{snippet}")
                metadata.append(
                    {
                        "source": link,
                        "name": title,
                        "url": link,
                    }
                )

            return [
                {
                    "source": {"name": "search_web", "id": "search_web"},
                    "document": documents,
                    "metadata": metadata,
                }
            ]

        elif tool_name == "view_knowledge_file":
            file_data = tool_result
            filename = file_data.get("filename", "Unknown File")
            file_id = file_data.get("id", "")
            knowledge_name = file_data.get("knowledge_name", "")

            return [
                {
                    "source": {
                        "id": file_id,
                        "name": filename,
                        "type": "file",
                    },
                    "document": [file_data.get("content", "")],
                    "metadata": [
                        {
                            "file_id": file_id,
                            "name": filename,
                            "source": filename,
                            **(
                                {"knowledge_name": knowledge_name}
                                if knowledge_name
                                else {}
                            ),
                        }
                    ],
                }
            ]

        elif tool_name == "fetch_url":
            url = tool_params.get("url", "")
            content = tool_result if isinstance(tool_result, str) else str(tool_result)
            snippet = content[:500] + ("..." if len(content) > 500 else "")

            return [
                {
                    "source": {"name": url or "fetch_url", "id": url or "fetch_url"},
                    "document": [snippet],
                    "metadata": [
                        {
                            "source": url,
                            "name": url,
                            "url": url,
                        }
                    ],
                }
            ]

        elif tool_name == "query_knowledge_files":
            chunks = tool_result

            # Group chunks by source for better citation display
            # Each unique source becomes a separate source entry
            sources_by_file = {}

            for chunk in chunks:
                source_name = chunk.get("source", "Unknown")
                file_id = chunk.get("file_id", "")
                note_id = chunk.get("note_id", "")
                chunk_type = chunk.get("type", "file")
                content = chunk.get("content", "")

                # Use file_id or note_id as the key
                key = file_id or note_id or source_name

                if key not in sources_by_file:
                    sources_by_file[key] = {
                        "source": {
                            "id": file_id or note_id,
                            "name": source_name,
                            "type": chunk_type,
                        },
                        "document": [],
                        "metadata": [],
                    }

                sources_by_file[key]["document"].append(content)
                sources_by_file[key]["metadata"].append(
                    {
                        "file_id": file_id,
                        "name": source_name,
                        "source": source_name,
                        **({"note_id": note_id} if note_id else {}),
                    }
                )

            # Return all grouped sources as a list
            if sources_by_file:
                return list(sources_by_file.values())

            # Empty result fallback
            return []

        else:
            # Fallback for other tools
            return [
                {
                    "source": {
                        "name": tool_name,
                        "type": "tool",
                        "id": tool_id or tool_name,
                    },
                    "document": [str(tool_result)],
                    "metadata": [{"source": tool_name, "name": tool_name}],
                }
            ]
    except Exception as e:
        log.exception(f"Error parsing tool result for {tool_name}: {e}")
        return [
            {
                "source": {"name": tool_name, "type": "tool"},
                "document": [str(tool_result)],
                "metadata": [{"source": tool_name}],
            }
        ]


def split_content_and_whitespace(content):
    content_stripped = content.rstrip()
    original_whitespace = (
        content[len(content_stripped) :] if len(content) > len(content_stripped) else ""
    )
    return content_stripped, original_whitespace


def is_opening_code_block(content):
    backtick_segments = content.split("```")
    # Even number of segments means the last backticks are opening a new block
    return len(backtick_segments) > 1 and len(backtick_segments) % 2 == 0


def serialize_output(output: list) -> str:
    """
    Convert OR-aligned output items to HTML for display.
    For LLM consumption, use convert_output_to_messages() instead.
    """
    content = ""
    hidden_tool_call_names = {"download_artifact", "view_image"}
    aggregated_reasoning_duration = 0
    aggregated_reasoning_texts = []
    aggregated_reasoning_started_at = None
    aggregated_reasoning_rendered = False

    def tool_result_has_error(result_text: str) -> bool:
        if not result_text:
            return False

        try:
            parsed = json.loads(result_text)
            if isinstance(parsed, dict):
                return bool(parsed.get("error"))
        except Exception:
            pass

        lowered = result_text.lower()
        return lowered.startswith("error") or '"error"' in lowered or "\nerror:" in lowered

    def build_reasoning_display(reasoning_text: str) -> str:
        return html.escape(
            "\n".join(
                (f"> {line}" if not line.startswith(">") else line)
                for line in reasoning_text.splitlines()
            )
        )

    def render_aggregated_reasoning() -> None:
        nonlocal content
        nonlocal aggregated_reasoning_rendered

        if aggregated_reasoning_rendered:
            return

        if aggregated_reasoning_duration <= 0 and not aggregated_reasoning_texts:
            return

        if content and not content.endswith("\n"):
            content += "\n"

        started_at_attr = (
            f' started_at="{aggregated_reasoning_started_at}"'
            if aggregated_reasoning_started_at is not None
            else ""
        )
        display = build_reasoning_display("\n\n".join(aggregated_reasoning_texts).strip())
        content += f'<details type="reasoning" done="true" duration="{aggregated_reasoning_duration}"{started_at_attr}>\n<summary>Thought for {aggregated_reasoning_duration} seconds</summary>\n{display}\n</details>\n'
        aggregated_reasoning_rendered = True

    # First pass: collect function_call_output items by call_id for lookup
    tool_outputs = {}
    for item in output:
        if item.get("type") == "function_call_output":
            tool_outputs[item.get("call_id")] = item

    # Second pass: render items in order
    for idx, item in enumerate(output):
        item_type = item.get("type", "")

        if item_type == "message":
            message_has_text = any(
                content_part.get("text", "").strip()
                for content_part in item.get("content", [])
                if isinstance(content_part, dict) and "text" in content_part
            )
            if message_has_text:
                render_aggregated_reasoning()

            for content_part in item.get("content", []):
                if "text" in content_part:
                    text = content_part.get("text", "").strip()
                    if text:
                        content = f"{content}{text}\n"

        elif item_type == "function_call":
            # Render tool call inline with its result (if available)
            if content and not content.endswith("\n"):
                content += "\n"

            call_id = item.get("call_id", "")
            name = item.get("name", "")
            arguments = item.get("arguments", "")

            result_item = tool_outputs.get(call_id)
            if result_item:
                result_text = ""
                for result_output in result_item.get("output", []):
                    if "text" in result_output:
                        output_text = result_output.get("text", "")
                        result_text += (
                            str(output_text)
                            if not isinstance(output_text, str)
                            else output_text
                        )
                files = result_item.get("files")
                embeds = result_item.get("embeds", "")

                if name in hidden_tool_call_names and not tool_result_has_error(result_text):
                    continue

                content += f'<details type="tool_calls" done="true" id="{call_id}" name="{name}" arguments="{html.escape(json.dumps(arguments))}" result="{html.escape(json.dumps(result_text, ensure_ascii=False))}" files="{html.escape(json.dumps(files)) if files else ""}" embeds="{html.escape(json.dumps(embeds))}">\n<summary>Tool Executed</summary>\n</details>\n'
            else:
                content += f'<details type="tool_calls" done="false" id="{call_id}" name="{name}" arguments="{html.escape(json.dumps(arguments))}">\n<summary>Executing...</summary>\n</details>\n'

        elif item_type == "function_call_output":
            # Already handled inline with function_call above
            pass

        elif item_type == "reasoning":
            reasoning_content = ""
            # Check for 'summary' (new structure) or 'content' (legacy/fallback)
            source_list = item.get("summary", []) or item.get("content", [])
            for content_part in source_list:
                if "text" in content_part:
                    reasoning_content += content_part.get("text", "")
                elif "summary" in content_part:  # Handle potential nested logic if any
                    pass

            reasoning_content = reasoning_content.strip()

            duration = item.get("duration")
            status = item.get("status", "in_progress")
            started_at = item.get("started_at")

            # Infer completion: if this reasoning item is NOT the last item,
            # render as done (a subsequent item means reasoning is complete)
            is_last_item = idx == len(output) - 1

            if status == "completed" or duration is not None or not is_last_item:
                aggregated_reasoning_duration += int(duration or 0)
                if reasoning_content:
                    aggregated_reasoning_texts.append(reasoning_content)
                if started_at is not None:
                    try:
                        started_at_value = float(started_at)
                        if (
                            aggregated_reasoning_started_at is None
                            or started_at_value < aggregated_reasoning_started_at
                        ):
                            aggregated_reasoning_started_at = int(started_at_value)
                    except (TypeError, ValueError):
                        pass
            else:
                if content and not content.endswith("\n"):
                    content += "\n"

                started_at_attr = (
                    f' started_at="{started_at}"' if started_at is not None else ""
                )
                display = build_reasoning_display(reasoning_content)
                content += f'<details type="reasoning" done="false"{started_at_attr}>\n<summary>Thinking…</summary>\n{display}\n</details>\n'

        elif item_type == "web_search_call":
            if content and not content.endswith("\n"):
                content += "\n"

            action = item.get("action") or {}
            action_type = action.get("type", "search")
            call_id = item.get("id", "") or output_id("ws")
            status = item.get("status", "in_progress")
            is_last_item = idx == len(output) - 1
            done = status in {"completed", "failed", "cancelled"} or not is_last_item

            if action_type == "open_page":
                name = "open_page"
                arguments = {
                    "url": action.get("url", ""),
                }
                result_text = "Opened page" if done else "Opening page"
            elif action_type == "find_in_page":
                name = "find_in_page"
                arguments = {
                    "url": action.get("url", ""),
                    "pattern": action.get("pattern", ""),
                }
                result_text = "Finished searching within page" if done else "Searching within page"
            else:
                query = action.get("query", "")
                queries = action.get("queries", [])
                if not query and not any(
                    isinstance(candidate, str) and candidate.strip()
                    for candidate in queries
                ):
                    continue
                name = "web_search"
                arguments = {
                    "query": query,
                    "queries": queries,
                }
                result_text = "Search completed" if done else "Searching the web"

            content += f'<details type="tool_calls" done="{"true" if done else "false"}" id="{call_id}" name="{name}" arguments="{html.escape(json.dumps(arguments, ensure_ascii=False))}" result="{html.escape(json.dumps(result_text, ensure_ascii=False))}">\n<summary>{"Tool Executed" if done else "Executing..."}</summary>\n</details>\n'

        elif item_type == "open_webui:code_interpreter":
            content_stripped, original_whitespace = split_content_and_whitespace(
                content
            )
            if is_opening_code_block(content_stripped):
                content = content_stripped.rstrip("`").rstrip() + original_whitespace
            else:
                content = content_stripped + original_whitespace

            if content and not content.endswith("\n"):
                content += "\n"

            # Render the code_interpreter item as a <details> block
            # so the frontend Collapsible renders "Analyzing..."/"Analyzed".
            code = item.get("code", "").strip()
            lang = item.get("lang", "python")
            status = item.get("status", "in_progress")
            duration = item.get("duration")
            is_last_item = idx == len(output) - 1

            # Build inner content: code block
            display = ""
            if code:
                display = f"```{lang}\n{code}\n```"

            # Build output attribute as HTML-escaped JSON for CodeBlock.svelte
            ci_output = item.get("output")
            output_attr = ""
            if ci_output:
                if isinstance(ci_output, dict):
                    output_json = json.dumps(ci_output, ensure_ascii=False)
                else:
                    output_json = json.dumps(
                        {"result": str(ci_output)}, ensure_ascii=False
                    )
                output_attr = f' output="{html.escape(output_json)}"'

            if status == "completed" or duration is not None or not is_last_item:
                content += f'<details type="code_interpreter" done="true" duration="{duration or 0}"{output_attr}>\n<summary>Analyzed</summary>\n{display}\n</details>\n'
            else:
                content += f'<details type="code_interpreter" done="false"{output_attr}>\n<summary>Analyzing…</summary>\n{display}\n</details>\n'

    render_aggregated_reasoning()

    return content.strip()


def deep_merge(target, source):
    """
    Merge source into target recursively (returning new structure).
    - Dicts: Recursive merge.
    - Strings: Concatenation.
    - Others: Overwrite.
    """
    if isinstance(target, dict) and isinstance(source, dict):
        new_target = target.copy()
        for k, v in source.items():
            if k in new_target:
                new_target[k] = deep_merge(new_target[k], v)
            else:
                new_target[k] = v
        return new_target
    elif isinstance(target, str) and isinstance(source, str):
        return target + source
    else:
        return source


def handle_responses_streaming_event(
    data: dict,
    current_output: list,
    response_started_at: float | None = None,
    response_output_start_index: int = 0,
) -> tuple[list, dict | None]:
    """
    Handle Responses API streaming events in a pure functional way.

    Args:
        data: The event data
        current_output: List of output items (treated as immutable)

    Returns:
        tuple[list, dict | None]: (new_output, metadata)
        - new_output: The updated output list.
        - metadata: Metadata to emit (e.g. usage), {} if update occurred, None if skip.
    """
    # Default: no change
    # Note: treating current_output as immutable, but avoiding full deepcopy for perf.
    # We will shallow copy only if we need to modify the list structure or items.

    def with_reasoning_timing(item: dict, previous_item: dict | None = None) -> dict:
        if item.get("type") != "reasoning":
            return item

        updated_item = item.copy()
        started_at = updated_item.get("started_at") or (
            previous_item.get("started_at") if previous_item else None
        )

        if started_at is None:
            started_at = response_started_at or time.time()

        updated_item["started_at"] = started_at

        status = updated_item.get("status")
        is_completed = status == "completed"

        if is_completed:
            ended_at = updated_item.get("ended_at") or time.time()
            updated_item["ended_at"] = ended_at
            computed_duration = max(0, int(ended_at - started_at))
            existing_duration = updated_item.get("duration")

            # Responses API may report reasoning duration as 0 or omit it
            # entirely. Prefer our locally measured elapsed wall time once the
            # reasoning item is complete so the UI reflects the real wait.
            if existing_duration is None or existing_duration <= 0:
                updated_item["duration"] = computed_duration
            else:
                updated_item["duration"] = max(existing_duration, computed_duration)

        return updated_item

    def get_absolute_output_index(raw_index: Any) -> int:
        if not isinstance(raw_index, int):
            raw_index = len(current_output) - response_output_start_index - 1
        return response_output_start_index + max(raw_index, 0)

    event_type = data.get("type", "")

    if event_type == "response.output_item.added":
        item = data.get("item", {})
        if item:
            new_output = list(current_output)
            absolute_output_index = get_absolute_output_index(data.get("output_index"))
            timed_item = with_reasoning_timing(item)
            if absolute_output_index < len(new_output):
                new_output.insert(absolute_output_index, timed_item)
            else:
                new_output.append(timed_item)
            return new_output, None
        return current_output, None

    elif event_type == "response.content_part.added":
        part = data.get("part", {})
        output_index = get_absolute_output_index(data.get("output_index"))

        if current_output and 0 <= output_index < len(current_output):
            new_output = list(current_output)
            # Copy the item to mutate it
            item = new_output[output_index].copy()
            new_output[output_index] = item

            if "content" not in item:
                item["content"] = []
            else:
                # Copy content list
                item["content"] = list(item["content"])

            if item.get("type") == "reasoning":
                # Reasoning items should not have content parts
                pass
            else:
                item["content"].append(part)
            return new_output, None
        return current_output, None

    elif event_type == "response.reasoning_summary_part.added":
        part = data.get("part", {})
        output_index = get_absolute_output_index(data.get("output_index"))

        if current_output and 0 <= output_index < len(current_output):
            new_output = list(current_output)
            item = new_output[output_index].copy()
            new_output[output_index] = item

            if "summary" not in item:
                item["summary"] = []
            else:
                item["summary"] = list(item["summary"])

            item["summary"].append(part)
            return new_output, None
        return current_output, None

    elif event_type.startswith("response.") and event_type.endswith(".delta"):
        # Generic Delta Handling
        parts = event_type.split(".")
        if len(parts) >= 3:
            delta_type = parts[1]
            delta = data.get("delta", "")

            output_index = get_absolute_output_index(data.get("output_index"))

            if current_output and 0 <= output_index < len(current_output):
                new_output = list(current_output)
                item = new_output[output_index].copy()
                new_output[output_index] = item
                item_type = item.get("type", "")

                # Determine target field and object based on delta_type and item_type
                if delta_type == "function_call_arguments":
                    key = "arguments"
                    if item_type == "function_call":
                        # Function call args are usually strings
                        item[key] = item.get(key, "") + str(delta)
                else:
                    # Generic handling, refined by item type below
                    pass

                    if item_type == "message":
                        # Message items: "text"/"output_text" -> "text"
                        # "reasoning_text" -> Skipped (should use reasoning item)
                        if delta_type in ["text", "output_text"]:
                            key = "text"
                        elif delta_type in ["reasoning_text", "reasoning_summary_text"]:
                            # Skip reasoning updates for message items
                            return new_output, None
                        else:
                            key = delta_type

                        content_index = data.get("content_index", 0)
                        if "content" not in item:
                            item["content"] = []
                        else:
                            item["content"] = list(item["content"])
                        content_list = item["content"]

                        while len(content_list) <= content_index:
                            content_list.append({"type": "text", "text": ""})

                        # Copy the part to mutate it
                        part = content_list[content_index].copy()
                        content_list[content_index] = part

                        current_val = part.get(key)
                        if current_val is None:
                            # Initialize based on delta type
                            current_val = {} if isinstance(delta, dict) else ""

                        part[key] = deep_merge(current_val, delta)

                    elif item_type == "reasoning":
                        # Reasoning items: "reasoning_text"/"reasoning_summary_text" -> "text"
                        # "text"/"output_text" -> Skipped (should use message item)
                        if delta_type == "reasoning_summary_text":
                            # Summary updates -> item['summary']
                            key = "text"
                            summary_index = data.get("summary_index", 0)
                            if "summary" not in item:
                                item["summary"] = []
                            else:
                                item["summary"] = list(item["summary"])
                            summary_list = item["summary"]

                            while len(summary_list) <= summary_index:
                                summary_list.append(
                                    {"type": "summary_text", "text": ""}
                                )

                            part = summary_list[summary_index].copy()
                            summary_list[summary_index] = part

                            target_val = part.get(key, "")
                            part[key] = deep_merge(target_val, delta)

                        elif delta_type == "reasoning_text":
                            # Reasoning body updates -> item['content']
                            key = "text"
                            content_index = data.get("content_index", 0)
                            if "content" not in item:
                                item["content"] = []
                            else:
                                item["content"] = list(item["content"])
                            content_list = item["content"]

                            while len(content_list) <= content_index:
                                # Reasoning content parts default to text
                                content_list.append({"type": "text", "text": ""})

                            part = content_list[content_index].copy()
                            content_list[content_index] = part

                            target_val = part.get(key, "")
                            part[key] = deep_merge(target_val, delta)

                        elif delta_type in ["text", "output_text"]:
                            return new_output, None
                        else:
                            # Fallback just in case other deltas target reasoning?
                            pass

                    else:
                        # Fallback for other item types
                        if delta_type in ["text", "output_text"]:
                            key = "text"
                        else:
                            key = delta_type

                        current_val = item.get(key)
                        if current_val is None:
                            current_val = {} if isinstance(delta, dict) else ""
                        item[key] = deep_merge(current_val, delta)

            return new_output, None

    elif event_type == "response.output_item.done":
        # Delta Event: Output item complete
        item = data.get("item")
        output_index = get_absolute_output_index(data.get("output_index"))

        new_output = list(current_output)
        if item and 0 <= output_index < len(current_output):
            new_output[output_index] = with_reasoning_timing(
                item, current_output[output_index]
            )
        elif item:
            new_output.append(with_reasoning_timing(item))
        return new_output, {}

    elif event_type.startswith("response.") and event_type.endswith(".done"):
        # Delta Events: response.content_part.done, response.text.done, etc.
        parts = event_type.split(".")
        if len(parts) >= 3:
            type_name = parts[1]

            # 1. Handle specific Delta "done" signals
            if type_name == "content_part":
                # "Signaling that no further changes will occur to a content part"
                # If payloads contains the full part, we could update it.
                # Usually purely signaling in standard implementation, but we check payload.
                part = data.get("part")
                output_index = get_absolute_output_index(data.get("output_index"))

                if part and current_output and 0 <= output_index < len(current_output):
                    new_output = list(current_output)
                    item = new_output[output_index].copy()
                    new_output[output_index] = item

                    if "content" in item:
                        item["content"] = list(item["content"])
                        content_index = data.get(
                            "content_index", len(item["content"]) - 1
                        )
                        if 0 <= content_index < len(item["content"]):
                            item["content"][content_index] = part
                            return new_output, {}
                return current_output, None

            elif type_name == "reasoning_summary_part":
                part = data.get("part")
                output_index = get_absolute_output_index(data.get("output_index"))

                if part and current_output and 0 <= output_index < len(current_output):
                    new_output = list(current_output)
                    item = new_output[output_index].copy()
                    new_output[output_index] = item

                    if "summary" in item:
                        item["summary"] = list(item["summary"])
                        summary_index = data.get(
                            "summary_index", len(item["summary"]) - 1
                        )
                        if 0 <= summary_index < len(item["summary"]):
                            item["summary"][summary_index] = part
                            return new_output, {}
                return current_output, None

            # 2. Skip Output Item done (handled specifically below)
            if type_name == "output_item":
                pass

            # 3. Generic Field Done (text.done, audio.done)
            elif type_name not in ["completed", "failed"]:
                output_index = get_absolute_output_index(data.get("output_index"))
                if current_output and 0 <= output_index < len(current_output):
                    previous_item = current_output[output_index]

                    key = (
                        "text"
                        if type_name
                        in [
                            "text",
                            "output_text",
                            "reasoning_text",
                            "reasoning_summary_text",
                        ]
                        else type_name
                    )
                    if type_name == "function_call_arguments":
                        key = "arguments"

                    if key in data:
                        final_value = data[key]
                        new_output = list(current_output)
                        item = new_output[output_index].copy()
                        new_output[output_index] = item
                        item_type = item.get("type", "")

                        if type_name == "function_call_arguments":
                            if item_type == "function_call":
                                item["arguments"] = final_value
                        elif item_type == "message":
                            content_index = data.get("content_index", 0)
                            if "content" in item:
                                item["content"] = list(item["content"])
                                if len(item["content"]) > content_index:
                                    part = item["content"][content_index].copy()
                                    item["content"][content_index] = part
                                    part[key] = final_value
                        elif item_type == "reasoning":
                            item["status"] = "completed"
                            item = with_reasoning_timing(item, previous_item)
                            new_output[output_index] = item
                        else:
                            item[key] = final_value

                        return new_output, {}

        return current_output, None

    elif event_type == "response.completed":
        # State Machine Event: Completed
        response_data = data.get("response", {})
        final_output = response_data.get("output")

        if final_output is not None:
            prefix_output = current_output[:response_output_start_index]
            new_output = prefix_output + final_output
        else:
            new_output = current_output

        # Ensure reasoning items are marked as completed in the final output
        if new_output:
            for index, item in enumerate(new_output):
                previous_item = None
                if index < len(current_output):
                    previous_item = current_output[index]

                if item.get("type") == "reasoning":
                    if item.get("status") != "completed":
                        item["status"] = "completed"

                    new_output[index] = with_reasoning_timing(item, previous_item)

        return new_output, {"usage": response_data.get("usage"), "done": True}

    elif event_type == "response.in_progress":
        # State Machine Event: In Progress
        # We could extract metadata if needed, but for now just acknowledge iteration
        return current_output, None

    elif event_type == "response.failed":
        # State Machine Event: Failed
        error = data.get("response", {}).get("error", {})
        return current_output, {"error": error}

    else:
        return current_output, None


def response_item_has_visible_content(item: dict) -> bool:
    item_type = item.get("type")

    if item_type == "message":
        for content_part in item.get("content", []) or []:
            if content_part.get("text"):
                return True
        return False

    return item_type in {
        "function_call",
        "function_call_output",
        "open_webui:code_interpreter",
    }


def get_source_context(
    sources: list, source_ids: dict = None, include_content: bool = True
) -> str:
    """
    Build <source> tag context string from citation sources.
    """
    context_string = ""
    if source_ids is None:
        source_ids = {}
    for source in sources:
        for doc, meta in zip(source.get("document", []), source.get("metadata", [])):
            source_id = (
                meta.get("source") or source.get("source", {}).get("id") or "N/A"
            )
            if source_id not in source_ids:
                source_ids[source_id] = len(source_ids) + 1
            src_name = source.get("source", {}).get("name")
            body = doc if include_content else ""
            context_string += (
                f'<source id="{source_ids[source_id]}"'
                + (f' name="{src_name}"' if src_name else "")
                + f">{body}</source>\n"
            )
    return context_string


def apply_source_context_to_messages(
    request: Request,
    messages: list,
    sources: list,
    user_message: str,
    include_content: bool = True,
) -> list:
    """
    Build source context from citation sources and apply to messages.
    Uses RAG template to format context for model consumption.

    When include_content is False, emit <source> tags with id/name but no
    document body — useful when the content is already present elsewhere
    (e.g. in a tool result message) and only citation markers are needed.
    """
    if not sources or not user_message:
        return messages

    context = get_source_context(sources, include_content=include_content)

    context = context.strip()
    if not context:
        return messages

    if RAG_SYSTEM_CONTEXT:
        return add_or_update_system_message(
            rag_template(request.app.state.config.RAG_TEMPLATE, context, user_message),
            messages,
            append=True,
        )
    else:
        return add_or_update_user_message(
            rag_template(request.app.state.config.RAG_TEMPLATE, context, user_message),
            messages,
            append=False,
        )


async def upload_artifact_to_chat(
    request: Request,
    artifact: dict,
    metadata: dict,
    user: UserModel,
    tool_info: Optional[dict] = None,
):
    filename = artifact.get("name") or artifact.get("filename")
    path = artifact.get("path", "")
    content_type = artifact.get("content_type")
    content = artifact.get("content")

    artifact_cache = metadata.setdefault("_uploaded_artifacts_by_key", {})
    artifact_cache_key = None

    if not filename and path:
        filename = os.path.basename(path)
    if not filename:
        filename = "artifact"

    if path:
        artifact_cache_key = f"path:{path}"
    elif isinstance(content, str) and content.startswith("data:"):
        artifact_cache_key = f"content:{filename}:{content_type or ''}:{hash(content)}"

    if artifact_cache_key and artifact_cache_key in artifact_cache:
        return copy.deepcopy(artifact_cache[artifact_cache_key])

    file_bytes = None
    file_url = None

    if isinstance(content, str) and content.startswith("data:"):
        file_url = get_file_url_from_base64(
            request,
            content,
            {
                "chat_id": metadata.get("chat_id"),
                "message_id": metadata.get("message_id"),
                "session_id": metadata.get("session_id"),
                "result": artifact,
            },
            user,
        )
        guessed_type = content.split(";", 1)[0].replace("data:", "", 1)
        content_type = content_type or guessed_type
    elif path and tool_info and tool_info.get("server", {}).get("url"):
        server = tool_info.get("server", {})
        base_url = server.get("url", "").rstrip("/")
        headers = copy.deepcopy(server.get("headers", {}) or {})
        cookies = copy.deepcopy(server.get("cookies", {}) or {})

        async with aiohttp.ClientSession(
            trust_env=True,
            timeout=aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT),
        ) as session:
            async with session.get(
                f"{base_url}/files/view",
                params={"path": path},
                headers=headers,
                cookies=cookies,
            ) as response:
                if response.status >= 400:
                    error_text = await response.text()
                    raise Exception(
                        f"Failed to download artifact '{path}': HTTP {response.status}: {error_text}"
                    )

                file_bytes = await response.read()
                content_type = (
                    content_type
                    or response.headers.get("content-type")
                    or mimetypes.guess_type(filename)[0]
                    or "application/octet-stream"
                )

    if file_url is None and file_bytes is None:
        return None

    if file_url is None:
        upload = UploadFile(
            file=io.BytesIO(file_bytes),
            filename=filename,
            headers={"content-type": content_type or "application/octet-stream"},
        )
        file_item = upload_file_handler(
            request,
            file=upload,
            metadata={
                "chat_id": metadata.get("chat_id"),
                "message_id": metadata.get("message_id"),
                "session_id": metadata.get("session_id"),
                "source": "sandbox_artifact",
            },
            process=False,
            user=user,
        )
        file_url = request.app.url_path_for("get_file_content_by_id", id=file_item.id)

    resolved_content_type = content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    file_type = "image" if resolved_content_type.startswith("image/") else "file"
    file_item = {
        "type": file_type,
        "url": file_url,
        "name": filename,
        "filename": filename,
        "content_type": resolved_content_type,
        **({"size": len(file_bytes)} if file_bytes is not None else {}),
        **(
            {"auto_uploaded": bool(artifact.get("auto_uploaded"))}
            if artifact.get("auto_uploaded") is not None
            else {}
        ),
        **(
            {"artifact_origin": artifact.get("artifact_origin")}
            if artifact.get("artifact_origin")
            else {}
        ),
        **(
            {"is_final_output": bool(artifact.get("is_final_output"))}
            if artifact.get("is_final_output") is not None
            else {}
        ),
        **(
            {"omitted_artifact_count": int(artifact.get("omitted_artifact_count", 0))}
            if artifact.get("omitted_artifact_count")
            else {}
        ),
    }

    if metadata.get("chat_id") and metadata.get("message_id"):
        db_files = Chats.add_message_files_by_id_and_message_id(
            metadata["chat_id"],
            metadata["message_id"],
            [file_item],
        )
        if db_files:
            file_item = db_files[-1]

    if artifact_cache_key:
        artifact_cache[artifact_cache_key] = copy.deepcopy(file_item)

    return file_item


def infer_artifacts_from_command_result(
    tool_function_name: str,
    tool_result: Any,
    metadata: Optional[dict],
) -> list[dict]:
    if tool_function_name not in {"exec_command", "run_command"}:
        return []

    if not isinstance(tool_result, dict):
        return []

    if tool_result.get("exit_code") not in (0, None) or tool_result.get("running") is True:
        return []

    stdout = tool_result.get("stdout")
    if not isinstance(stdout, str) or not stdout.strip():
        return []

    input_paths = {
        str(item.get("path"))
        for item in (metadata or {}).get("terminal_files", []) or []
        if item.get("path")
    }

    artifacts = []
    seen_paths = set()
    for raw_line in stdout.splitlines():
        line = raw_line.strip().strip("\"'`")
        if not line or line in seen_paths:
            continue
        if not (line.startswith("/workspace/") or line.startswith("/tmp/")):
            continue
        if line.endswith("/") or line in input_paths:
            continue

        filename = os.path.basename(line)
        content_type = mimetypes.guess_type(filename)[0]
        if content_type is None and "." not in filename:
            continue

        seen_paths.add(line)
        artifacts.append(
            {
                "path": line,
                "name": filename,
                "auto_uploaded": True,
                "artifact_origin": "inferred",
                **({"content_type": content_type} if content_type else {}),
            }
        )

    return artifacts


def select_inferred_artifacts_for_chat(artifacts: list[dict]) -> tuple[list[dict], int]:
    if len(artifacts) <= 1:
        return artifacts, 0

    image_artifacts = [
        artifact
        for artifact in artifacts
        if isinstance(artifact, dict)
        and str(artifact.get("content_type", "")).startswith("image/")
    ]
    selected = image_artifacts[-1] if image_artifacts else artifacts[-1]
    omitted_count = max(0, len(artifacts) - 1)

    return [
        {
            **selected,
            "is_final_output": True,
            "omitted_artifact_count": omitted_count,
        }
    ], omitted_count


def dedupe_tool_result_files(files: list[dict]) -> list[dict]:
    deduped_files = []
    seen_keys = set()

    for item in files:
        if not isinstance(item, dict):
            continue

        if item.get("url"):
            key = ("url", item.get("url"))
        elif item.get("content"):
            key = ("content", item.get("content"))
        else:
            key = (
                "json",
                json.dumps(item, sort_keys=True, ensure_ascii=False, default=str),
            )

        if key in seen_keys:
            continue

        seen_keys.add(key)
        deduped_files.append(item)

    return deduped_files


async def process_tool_result(
    request,
    tool_function_name,
    tool_result,
    tool_type,
    direct_tool=False,
    metadata=None,
    user=None,
    tool_info=None,
):
    tool_result_embeds = []
    EXTERNAL_TOOL_TYPES = ("external", "action", "terminal")

    if isinstance(tool_result, HTMLResponse):
        content_disposition = tool_result.headers.get("Content-Disposition", "")
        if "inline" in content_disposition:
            content = tool_result.body.decode("utf-8", "replace")
            tool_result_embeds.append(content)

            if 200 <= tool_result.status_code < 300:
                tool_result = {
                    "status": "success",
                    "code": "ui_component",
                    "message": f"{tool_function_name}: Embedded UI result is active and visible to the user.",
                }
            elif 400 <= tool_result.status_code < 500:
                tool_result = {
                    "status": "error",
                    "code": "ui_component",
                    "message": f"{tool_function_name}: Client error {tool_result.status_code} from embedded UI result.",
                }
            elif 500 <= tool_result.status_code < 600:
                tool_result = {
                    "status": "error",
                    "code": "ui_component",
                    "message": f"{tool_function_name}: Server error {tool_result.status_code} from embedded UI result.",
                }
            else:
                tool_result = {
                    "status": "error",
                    "code": "ui_component",
                    "message": f"{tool_function_name}: Unexpected status code {tool_result.status_code} from embedded UI result.",
                }
        else:
            tool_result = tool_result.body.decode("utf-8", "replace")

    elif (tool_type in EXTERNAL_TOOL_TYPES and isinstance(tool_result, tuple)) or (
        direct_tool and isinstance(tool_result, list) and len(tool_result) == 2
    ):
        tool_result, tool_response_headers = tool_result

        try:
            if not isinstance(tool_response_headers, dict):
                tool_response_headers = dict(tool_response_headers)
        except Exception as e:
            tool_response_headers = {}
            log.debug(e)

        if tool_response_headers and isinstance(tool_response_headers, dict):
            content_disposition = tool_response_headers.get(
                "Content-Disposition",
                tool_response_headers.get("content-disposition", ""),
            )

            if "inline" in content_disposition:
                content_type = tool_response_headers.get(
                    "Content-Type",
                    tool_response_headers.get("content-type", ""),
                )
                location = tool_response_headers.get(
                    "Location",
                    tool_response_headers.get("location", ""),
                )

                if "text/html" in content_type:
                    # Display as iframe embed
                    tool_result_embeds.append(tool_result)
                    tool_result = {
                        "status": "success",
                        "code": "ui_component",
                        "message": f"{tool_function_name}: Embedded UI result is active and visible to the user.",
                    }
                elif location:
                    tool_result_embeds.append(location)
                    tool_result = {
                        "status": "success",
                        "code": "ui_component",
                        "message": f"{tool_function_name}: Embedded UI result is active and visible to the user.",
                    }

    tool_result_files = []

    if isinstance(tool_result, list):
        if tool_type == "mcp":  # MCP
            tool_response = []
            for item in tool_result:
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        text = item.get("text", "")
                        if isinstance(text, str):
                            try:
                                text = json.loads(text)
                            except json.JSONDecodeError:
                                pass
                        tool_response.append(text)
                    elif item.get("type") in ["image", "audio"]:
                        file_url = get_file_url_from_base64(
                            request,
                            f"data:{item.get('mimeType')};base64,{item.get('data', item.get('blob', ''))}",
                            {
                                "chat_id": metadata.get("chat_id", None),
                                "message_id": metadata.get("message_id", None),
                                "session_id": metadata.get("session_id", None),
                                "result": item,
                            },
                            user,
                        )

                        tool_result_files.append(
                            {
                                "type": item.get("type", "data"),
                                "url": file_url,
                            }
                        )
            tool_result = tool_response[0] if len(tool_response) == 1 else tool_response
        else:  # OpenAPI
            for item in tool_result:
                if isinstance(item, str) and item.startswith("data:"):
                    tool_result_files.append(
                        {
                            "type": "data",
                            "content": item,
                        }
                    )
                    tool_result.remove(item)

    if isinstance(tool_result, dict) and not tool_result.get("artifacts"):
        inferred_artifacts = infer_artifacts_from_command_result(
            tool_function_name, tool_result, metadata
        )
        if inferred_artifacts:
            selected_artifacts, omitted_artifact_count = select_inferred_artifacts_for_chat(
                inferred_artifacts
            )
            tool_result = {
                **tool_result,
                "artifacts": selected_artifacts,
                "summary": tool_result.get("summary")
                or (
                    f"{tool_function_name}: Generated file ready for the user. "
                    "The system uploaded the printed output path automatically."
                    if omitted_artifact_count == 0
                    else (
                        f"{tool_function_name}: Uploaded only the most recent generated file and "
                        f"skipped {omitted_artifact_count} likely intermediate artifact(s)."
                    )
                ),
            }

    artifact_summary = None
    if isinstance(tool_result, dict) and tool_result.get("artifacts"):
        artifacts = tool_result.get("artifacts", []) or []
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                continue

            try:
                uploaded_artifact = await upload_artifact_to_chat(
                    request,
                    artifact,
                    metadata or {},
                    user,
                    tool_info=tool_info,
                )
            except Exception as e:
                log.warning("Failed to upload tool artifact %s: %s", artifact, e)
                continue
            if uploaded_artifact:
                tool_result_files.append(uploaded_artifact)

        tool_result_files = dedupe_tool_result_files(tool_result_files)
        artifact_summary = (
            tool_result.get("summary")
            or tool_result.get("message")
            or f"{tool_function_name}: Generated {len(tool_result_files)} artifact(s)."
        )
        tool_result = {
            key: value
            for key, value in tool_result.items()
            if key not in {"artifacts", "summary"}
        }
        if artifact_summary and (
            not tool_result
            or set(tool_result.keys()).issubset({"status", "message"})
        ):
            tool_result = artifact_summary

    if isinstance(tool_result, list):
        tool_result = {"results": tool_result}

    if isinstance(tool_result, dict) or isinstance(tool_result, list):
        tool_result = json.dumps(tool_result, indent=2, ensure_ascii=False)

    # Safety: ensure tool_result is always a string (or None) to prevent
    # downstream TypeError when concatenating (e.g. if an upstream callable
    # returned a tuple that was not unpacked by the branches above).
    if tool_result is not None and not isinstance(tool_result, str):
        if isinstance(tool_result, tuple):
            # execute_tool_server returns (data, headers); unpack the data part
            tool_result = (
                json.dumps(tool_result[0], indent=2, ensure_ascii=False)
                if len(tool_result) > 0
                else ""
            )
        else:
            tool_result = str(tool_result)

    tool_result_files = dedupe_tool_result_files(tool_result_files)

    return tool_result, tool_result_files, tool_result_embeds


async def terminal_event_handler(
    tool_function_name: str,
    tool_function_params: dict,
    tool_result,
    event_emitter,
):
    """Emit terminal:* events for Open Terminal tools.

    - display_file  → emits 'terminal:display_file' to open the file preview.
    - write_file / replace_file_content → emits 'terminal:write_file' to refresh.
    - run_command / exec_command → emits 'terminal:run_command' with cwd to refresh if relevant.
    """
    if not event_emitter:
        return

    if tool_function_name == "display_file":
        path = tool_function_params.get("path", "")
        if not path:
            return
        # Only emit if the file actually exists
        parsed = tool_result
        if isinstance(parsed, str):
            try:
                parsed = json.loads(parsed)
            except (json.JSONDecodeError, TypeError):
                pass
        if isinstance(parsed, dict) and parsed.get("exists") is False:
            return

        await event_emitter(
            {
                "type": f"terminal:{tool_function_name}",
                "data": {"path": path},
            }
        )
    elif tool_function_name in ("write_file", "replace_file_content"):
        path = tool_function_params.get("path", "")
        if not path:
            return
        await event_emitter(
            {
                "type": f"terminal:{tool_function_name}",
                "data": {"path": path},
            }
        )
    elif tool_function_name in ("run_command", "exec_command"):
        await event_emitter(
            {
                "type": "terminal:run_command",
                "data": {},
            }
        )


async def chat_completion_tools_handler(
    request: Request, body: dict, extra_params: dict, user: UserModel, models, tools
) -> tuple[dict, dict]:
    async def get_content_from_response(response) -> Optional[str]:
        content = None
        if hasattr(response, "body_iterator"):
            async for chunk in response.body_iterator:
                data = json.loads(chunk.decode("utf-8", "replace"))
                content = data["choices"][0]["message"]["content"]

            # Cleanup any remaining background tasks if necessary
            if response.background is not None:
                await response.background()
        else:
            content = response["choices"][0]["message"]["content"]
        return content

    def get_tools_function_calling_payload(messages, task_model_id, content):
        user_message = get_last_user_message(messages)

        if user_message and messages and messages[-1]["role"] == "user":
            # Remove the last user message to avoid duplication
            messages = messages[:-1]

        recent_messages = messages[-4:] if len(messages) > 4 else messages
        chat_history = "\n".join(
            f"{message['role'].upper()}: \"\"\"{get_content_from_message(message)}\"\"\""
            for message in recent_messages
        )

        prompt = (
            f"History:\n{chat_history}\nQuery: {user_message}"
            if chat_history
            else f"Query: {user_message}"
        )

        return {
            "model": task_model_id,
            "messages": [
                {"role": "system", "content": content},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "metadata": {"task": str(TASKS.FUNCTION_CALLING)},
        }

    event_caller = extra_params["__event_call__"]
    event_emitter = extra_params["__event_emitter__"]
    metadata = extra_params["__metadata__"]

    task_model_id = get_task_model_id(
        body["model"],
        request.app.state.config.TASK_MODEL,
        request.app.state.config.TASK_MODEL_EXTERNAL,
        models,
    )

    skip_files = False
    sources = []

    specs = [tool["spec"] for tool in tools.values()]
    tools_specs = json.dumps(specs, ensure_ascii=False)

    if request.app.state.config.TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE != "":
        template = request.app.state.config.TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE
    else:
        template = DEFAULT_TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE

    tools_function_calling_prompt = tools_function_calling_generation_template(
        template, tools_specs
    )
    payload = get_tools_function_calling_payload(
        body["messages"], task_model_id, tools_function_calling_prompt
    )

    try:
        response = await generate_chat_completion(request, form_data=payload, user=user)
        log.debug(f"{response=}")
        content = await get_content_from_response(response)
        log.debug(f"{content=}")

        if not content:
            return body, {}

        try:
            content = content[content.find("{") : content.rfind("}") + 1]
            if not content:
                raise Exception("No JSON object found in the response")

            result = json.loads(content)

            async def tool_call_handler(tool_call):
                nonlocal skip_files

                log.debug(f"{tool_call=}")

                tool_function_name = tool_call.get("name", None)
                if tool_function_name not in tools:
                    return body, {}

                tool_function_params = tool_call.get("parameters", {})

                tool = None
                tool_type = ""
                direct_tool = False

                try:
                    tool = tools[tool_function_name]
                    tool_type = tool.get("type", "")
                    direct_tool = tool.get("direct", False)

                    spec = tool.get("spec", {})
                    allowed_params = (
                        spec.get("parameters", {}).get("properties", {}).keys()
                    )
                    tool_function_params = {
                        k: v
                        for k, v in tool_function_params.items()
                        if k in allowed_params
                    }
                    normalized_tool_function_params = normalize_terminal_tool_params(
                        tool_function_name,
                        tool_function_params,
                        metadata,
                    )
                    if normalized_tool_function_params != tool_function_params:
                        log.debug(
                            "Normalized terminal tool params for %s: %s -> %s",
                            tool_function_name,
                            tool_function_params,
                            normalized_tool_function_params,
                        )
                    tool_function_params = normalized_tool_function_params

                    await emit_tool_execution_status(
                        event_emitter,
                        tool_function_name,
                        tool_function_params,
                        done=False,
                    )

                    if tool.get("direct", False):
                        tool_result = await event_caller(
                            {
                                "type": "execute:tool",
                                "data": {
                                    "id": str(uuid4()),
                                    "name": tool_function_name,
                                    "params": tool_function_params,
                                    "server": tool.get("server", {}),
                                    "session_id": metadata.get("session_id", None),
                                },
                            }
                        )
                    else:
                        tool_function = tool["callable"]
                        tool_result = await tool_function(**tool_function_params)

                    tool_result = await wait_for_terminal_command_completion(
                        tools,
                        tool_function_name,
                        tool_result,
                    )

                except Exception as e:
                    tool_result = str(e)

                raw_tool_result = tool_result

                tool_result, tool_result_files, tool_result_embeds = (
                    await process_tool_result(
                        request,
                        tool_function_name,
                        tool_result,
                        tool_type,
                        direct_tool,
                        metadata,
                        user,
                        tool_info=tool,
                    )
                )
                tool_result_files = dedupe_tool_result_files(tool_result_files)

                if event_emitter:
                    await emit_tool_execution_status(
                        event_emitter,
                        tool_function_name,
                        tool_function_params,
                        done=True,
                        tool_result=raw_tool_result,
                    )
                    await terminal_event_handler(
                        tool_function_name,
                        tool_function_params,
                        tool_result,
                        event_emitter,
                    )

                    if tool_result_files:
                        await event_emitter(
                            {
                                "type": "files",
                                "data": {
                                    "files": tool_result_files,
                                },
                            }
                        )

                    if tool_result_embeds:
                        await event_emitter(
                            {
                                "type": "embeds",
                                "data": {
                                    "embeds": tool_result_embeds,
                                },
                            }
                        )

                if tool_result:
                    tool = tools[tool_function_name]
                    tool_id = tool.get("tool_id", "")

                    tool_name = (
                        f"{tool_id}/{tool_function_name}"
                        if tool_id
                        else f"{tool_function_name}"
                    )

                    # Citation is enabled for this tool
                    sources.append(
                        {
                            "source": {
                                "name": (f"{tool_name}"),
                            },
                            "document": [str(tool_result)],
                            "metadata": [
                                {
                                    "source": (f"{tool_name}"),
                                    "parameters": tool_function_params,
                                }
                            ],
                            "tool_result": True,
                        }
                    )

                    if (
                        tools[tool_function_name]
                        .get("metadata", {})
                        .get("file_handler", False)
                    ):
                        skip_files = True

            # check if "tool_calls" in result
            if result.get("tool_calls"):
                for tool_call in result.get("tool_calls"):
                    await tool_call_handler(tool_call)
            else:
                await tool_call_handler(result)

        except Exception as e:
            log.debug(f"Error: {e}")
            content = None
    except Exception as e:
        log.debug(f"Error: {e}")
        content = None

    log.debug(f"tool_contexts: {sources}")

    if skip_files and "files" in body.get("metadata", {}):
        del body["metadata"]["files"]

    return body, {"sources": sources}


async def chat_memory_handler(
    request: Request, form_data: dict, extra_params: dict, user
):
    try:
        results = await query_memory(
            request,
            QueryMemoryForm(
                **{
                    "content": get_last_user_message(form_data["messages"]) or "",
                    "k": 3,
                }
            ),
            user,
        )
    except Exception as e:
        log.debug(e)
        results = None

    user_context = ""
    if results and hasattr(results, "documents"):
        if results.documents and len(results.documents) > 0:
            for doc_idx, doc in enumerate(results.documents[0]):
                created_at_date = "Unknown Date"

                if results.metadatas[0][doc_idx].get("created_at"):
                    created_at_timestamp = results.metadatas[0][doc_idx]["created_at"]
                    created_at_date = time.strftime(
                        "%Y-%m-%d", time.localtime(created_at_timestamp)
                    )

                user_context += f"{doc_idx + 1}. [{created_at_date}] {doc}\n"

    form_data["messages"] = add_or_update_system_message(
        f"User Context:\n{user_context}\n", form_data["messages"], append=True
    )

    return form_data


async def chat_web_search_handler(
    request: Request, form_data: dict, extra_params: dict, user
):
    event_emitter = extra_params["__event_emitter__"]
    await event_emitter(
        {
            "type": "status",
            "data": {
                "action": "web_search",
                "description": "Searching the web",
                "done": False,
            },
        }
    )

    messages = form_data["messages"]
    user_message = get_last_user_message(messages)

    queries = []
    try:
        res = await generate_queries(
            request,
            {
                "model": form_data["model"],
                "messages": messages,
                "prompt": user_message,
                "type": "web_search",
                "chat_id": extra_params.get("__chat_id__"),
            },
            user,
        )

        response = res["choices"][0]["message"]["content"]

        try:
            bracket_start = response.find("{")
            bracket_end = response.rfind("}") + 1

            if bracket_start == -1 or bracket_end == -1:
                raise Exception("No JSON object found in the response")

            response = response[bracket_start:bracket_end]
            queries = json.loads(response)
            queries = queries.get("queries", [])
        except Exception as e:
            queries = [response]

        if ENABLE_QUERIES_CACHE:
            request.state.cached_queries = queries

    except Exception as e:
        log.exception(e)
        queries = [user_message]

    # Check if generated queries are empty
    if len(queries) == 1 and queries[0].strip() == "":
        queries = [user_message]

    # Check if queries are not found
    if len(queries) == 0:
        await event_emitter(
            {
                "type": "status",
                "data": {
                    "action": "web_search",
                    "description": "No search query generated",
                    "done": True,
                },
            }
        )
        return form_data

    await event_emitter(
        {
            "type": "status",
            "data": {
                "action": "web_search_queries_generated",
                "queries": queries,
                "done": False,
            },
        }
    )

    try:
        results = await process_web_search(
            request,
            SearchForm(queries=queries),
            user=user,
        )

        if results:
            files = form_data.get("files", [])

            if results.get("collection_names"):
                for col_idx, collection_name in enumerate(
                    results.get("collection_names")
                ):
                    files.append(
                        {
                            "collection_name": collection_name,
                            "name": ", ".join(queries),
                            "type": "web_search",
                            "urls": results["filenames"],
                            "queries": queries,
                        }
                    )
            elif results.get("docs"):
                # Invoked when bypass embedding and retrieval is set to True
                docs = results["docs"]
                files.append(
                    {
                        "docs": docs,
                        "name": ", ".join(queries),
                        "type": "web_search",
                        "urls": results["filenames"],
                        "queries": queries,
                    }
                )

            form_data["files"] = files

            await event_emitter(
                {
                    "type": "status",
                    "data": {
                        "action": "web_search",
                        "description": "Searched {{count}} sites",
                        "urls": results["filenames"],
                        "items": results.get("items", []),
                        "done": True,
                    },
                }
            )
        else:
            await event_emitter(
                {
                    "type": "status",
                    "data": {
                        "action": "web_search",
                        "description": "No search results found",
                        "done": True,
                        "error": True,
                    },
                }
            )

    except Exception as e:
        log.exception(e)
        await event_emitter(
            {
                "type": "status",
                "data": {
                    "action": "web_search",
                    "description": "An error occurred while searching the web",
                    "queries": queries,
                    "done": True,
                    "error": True,
                },
            }
        )

    return form_data


def get_images_from_messages(message_list):
    images = []

    for message in reversed(message_list):

        message_images = []
        for file in message.get("files", []):
            if file.get("type") == "image":
                message_images.append(file.get("url"))
            elif file.get("content_type", "").startswith("image/"):
                message_images.append(file.get("url"))

        if message_images:
            images.append(message_images)

    return images


def get_image_urls(delta_images, request, metadata, user) -> list[str]:
    if not isinstance(delta_images, list):
        return []

    image_urls = []
    for img in delta_images:
        if not isinstance(img, dict) or img.get("type") != "image_url":
            continue

        url = img.get("image_url", {}).get("url")
        if not url:
            continue

        if url.startswith("data:image/png;base64"):
            url = get_image_url_from_base64(request, url, metadata, user)

        image_urls.append(url)

    return image_urls


def add_file_context(messages: list, chat_id: str, user) -> list:
    """
    Add file URLs to messages for native function calling.
    """
    if not chat_id or chat_id.startswith("local:"):
        return messages

    chat = Chats.get_chat_by_id_and_user_id(chat_id, user.id)
    if not chat:
        return messages

    history = chat.chat.get("history", {})
    stored_messages = get_message_list(
        history.get("messages", {}), history.get("currentId")
    )

    def format_file_tag(file):
        attrs = f'type="{file.get("type", "file")}" url="{file["url"]}"'
        if file.get("content_type"):
            attrs += f' content_type="{file["content_type"]}"'
        if file.get("name"):
            attrs += f' name="{file["name"]}"'
        return f"<file {attrs}/>"

    for message, stored_message in zip(messages, stored_messages):
        files_with_urls = [
            file
            for file in stored_message.get("files", [])
            if file.get("url") and not file.get("url").startswith("data:")
        ]
        if not files_with_urls:
            continue

        file_tags = [format_file_tag(file) for file in files_with_urls]
        file_context = (
            "<attached_files>\n" + "\n".join(file_tags) + "\n</attached_files>\n\n"
        )

        content = message.get("content", "")
        if isinstance(content, list):
            message["content"] = [{"type": "text", "text": file_context}] + content
        else:
            message["content"] = file_context + content

    return messages


async def chat_image_generation_handler(
    request: Request, form_data: dict, extra_params: dict, user
):
    metadata = extra_params.get("__metadata__", {})
    chat_id = metadata.get("chat_id", None)
    __event_emitter__ = extra_params.get("__event_emitter__", None)

    if not chat_id or not isinstance(chat_id, str) or not __event_emitter__:
        return form_data

    if chat_id.startswith("local:"):
        message_list = form_data.get("messages", [])
    else:
        chat = Chats.get_chat_by_id_and_user_id(chat_id, user.id)
        await __event_emitter__(
            {
                "type": "status",
                "data": {"description": "Creating image", "done": False},
            }
        )

        messages_map = chat.chat.get("history", {}).get("messages", {})
        message_id = chat.chat.get("history", {}).get("currentId")
        message_list = get_message_list(messages_map, message_id)

    user_message = get_last_user_message(message_list)

    prompt = user_message
    message_images = get_images_from_messages(message_list)

    # Limit to first 2 sets of images
    # We may want to change this in the future to allow more images
    input_images = []
    for idx, images in enumerate(message_images):
        if idx >= 2:
            break
        for image in images:
            input_images.append(image)

    system_message_content = ""

    if len(input_images) > 0 and request.app.state.config.ENABLE_IMAGE_EDIT:
        # Edit image(s)
        try:
            images = await image_edits(
                request=request,
                form_data=EditImageForm(**{"prompt": prompt, "image": input_images}),
                metadata={
                    "chat_id": metadata.get("chat_id", None),
                    "message_id": metadata.get("message_id", None),
                },
                user=user,
            )

            await __event_emitter__(
                {
                    "type": "status",
                    "data": {"description": "Image created", "done": True},
                }
            )

            await __event_emitter__(
                {
                    "type": "files",
                    "data": {
                        "files": [
                            {
                                "type": "image",
                                "url": image["url"],
                            }
                            for image in images
                        ]
                    },
                }
            )

            system_message_content = "<context>The requested image has been edited and created and is now being shown to the user. Let them know that it has been generated.</context>"
        except Exception as e:
            log.debug(e)

            error_message = ""
            if isinstance(e, HTTPException):
                if e.detail and isinstance(e.detail, dict):
                    error_message = e.detail.get("message", str(e.detail))
                else:
                    error_message = str(e.detail)

            await __event_emitter__(
                {
                    "type": "status",
                    "data": {
                        "description": f"An error occurred while generating an image",
                        "done": True,
                    },
                }
            )

            system_message_content = f"<context>Image generation was attempted but failed. The system is currently unable to generate the image. Tell the user that the following error occurred: {error_message}</context>"

    else:
        # Create image(s)
        if request.app.state.config.ENABLE_IMAGE_PROMPT_GENERATION:
            try:
                res = await generate_image_prompt(
                    request,
                    {
                        "model": form_data["model"],
                        "messages": form_data["messages"],
                        "chat_id": metadata.get("chat_id"),
                    },
                    user,
                )

                response = res["choices"][0]["message"]["content"]

                try:
                    bracket_start = response.find("{")
                    bracket_end = response.rfind("}") + 1

                    if bracket_start == -1 or bracket_end == -1:
                        raise Exception("No JSON object found in the response")

                    response = response[bracket_start:bracket_end]
                    response = json.loads(response)
                    prompt = response.get("prompt", [])
                except Exception as e:
                    prompt = user_message

            except Exception as e:
                log.exception(e)
                prompt = user_message

        try:
            images = await image_generations(
                request=request,
                form_data=CreateImageForm(**{"prompt": prompt}),
                metadata={
                    "chat_id": metadata.get("chat_id", None),
                    "message_id": metadata.get("message_id", None),
                },
                user=user,
            )

            await __event_emitter__(
                {
                    "type": "status",
                    "data": {"description": "Image created", "done": True},
                }
            )

            await __event_emitter__(
                {
                    "type": "files",
                    "data": {
                        "files": [
                            {
                                "type": "image",
                                "url": image["url"],
                            }
                            for image in images
                        ]
                    },
                }
            )

            system_message_content = "<context>The requested image has been created by the system successfully and is now being shown to the user. Let the user know that the image they requested has been generated and is now shown in the chat.</context>"
        except Exception as e:
            log.debug(e)

            error_message = ""
            if isinstance(e, HTTPException):
                if e.detail and isinstance(e.detail, dict):
                    error_message = e.detail.get("message", str(e.detail))
                else:
                    error_message = str(e.detail)

            await __event_emitter__(
                {
                    "type": "status",
                    "data": {
                        "description": f"An error occurred while generating an image",
                        "done": True,
                    },
                }
            )

            system_message_content = f"<context>Image generation was attempted but failed because of an error. The system is currently unable to generate the image. Tell the user that the following error occurred: {error_message}</context>"

    if system_message_content:
        form_data["messages"] = add_or_update_system_message(
            system_message_content, form_data["messages"]
        )

    return form_data


async def chat_completion_files_handler(
    request: Request, body: dict, extra_params: dict, user: UserModel
) -> tuple[dict, dict[str, list]]:
    __event_emitter__ = extra_params["__event_emitter__"]
    sources = []

    metadata = body.get("metadata", {}) or {}
    if (
        metadata.get("terminal_id")
        and (metadata.get("params", {}) or {}).get("function_calling") == "native"
    ):
        # Sandbox-terminal chats should use mounted sandbox files instead of
        # the retrieval/RAG file pipeline.
        return body, {"sources": sources}

    if files := body.get("metadata", {}).get("files", None):
        # Check if all files are in full context mode
        all_full_context = all(item.get("context") == "full" for item in files)

        queries = []
        if not all_full_context:
            try:
                queries_response = await generate_queries(
                    request,
                    {
                        "model": body["model"],
                        "messages": body["messages"],
                        "type": "retrieval",
                        "chat_id": body.get("metadata", {}).get("chat_id"),
                    },
                    user,
                )
                queries_response = queries_response["choices"][0]["message"]["content"]

                try:
                    bracket_start = queries_response.find("{")
                    bracket_end = queries_response.rfind("}") + 1

                    if bracket_start == -1 or bracket_end == -1:
                        raise Exception("No JSON object found in the response")

                    queries_response = queries_response[bracket_start:bracket_end]
                    queries_response = json.loads(queries_response)
                except Exception as e:
                    queries_response = {"queries": [queries_response]}

                queries = queries_response.get("queries", [])
            except:
                pass

            await __event_emitter__(
                {
                    "type": "status",
                    "data": {
                        "action": "queries_generated",
                        "queries": queries,
                        "done": False,
                    },
                }
            )

        if len(queries) == 0:
            queries = [get_last_user_message(body["messages"])]

        try:
            # Directly await async get_sources_from_items (no thread needed - fully async now)
            sources = await get_sources_from_items(
                request=request,
                items=files,
                queries=queries,
                embedding_function=lambda query, prefix: request.app.state.EMBEDDING_FUNCTION(
                    query, prefix=prefix, user=user
                ),
                k=request.app.state.config.TOP_K,
                reranking_function=(
                    (
                        lambda query, documents: request.app.state.RERANKING_FUNCTION(
                            query, documents, user=user
                        )
                    )
                    if request.app.state.RERANKING_FUNCTION
                    else None
                ),
                k_reranker=request.app.state.config.TOP_K_RERANKER,
                r=request.app.state.config.RELEVANCE_THRESHOLD,
                hybrid_bm25_weight=request.app.state.config.HYBRID_BM25_WEIGHT,
                hybrid_search=request.app.state.config.ENABLE_RAG_HYBRID_SEARCH,
                full_context=all_full_context
                or request.app.state.config.RAG_FULL_CONTEXT,
                user=user,
            )
        except Exception as e:
            log.exception(e)

        log.debug(f"rag_contexts:sources: {sources}")

        unique_ids = set()
        for source in sources or []:
            if not source or len(source.keys()) == 0:
                continue

            documents = source.get("document") or []
            metadatas = source.get("metadata") or []
            src_info = source.get("source") or {}

            for index, _ in enumerate(documents):
                metadata = metadatas[index] if index < len(metadatas) else None
                _id = (
                    (metadata or {}).get("source")
                    or (src_info or {}).get("id")
                    or "N/A"
                )
                unique_ids.add(_id)

        sources_count = len(unique_ids)
        await __event_emitter__(
            {
                "type": "status",
                "data": {
                    "action": "sources_retrieved",
                    "count": sources_count,
                    "done": True,
                },
            }
        )

    return body, {"sources": sources}


def apply_params_to_form_data(form_data, model):
    params = form_data.pop("params", {})
    custom_params = params.pop("custom_params", {})

    open_webui_params = {
        "stream_response": bool,
        "stream_delta_chunk_size": int,
        "function_calling": str,
        "reasoning_tags": list,
        "system": str,
    }

    for key in list(params.keys()):
        if key in open_webui_params:
            del params[key]

    if custom_params:
        # Attempt to parse custom_params if they are strings
        for key, value in custom_params.items():
            if isinstance(value, str):
                try:
                    # Attempt to parse the string as JSON
                    custom_params[key] = json.loads(value)
                except json.JSONDecodeError:
                    # If it fails, keep the original string
                    pass

        # If custom_params are provided, merge them into params
        params = deep_update(params, custom_params)

    if model.get("owned_by") == "ollama":
        # Ollama specific parameters
        form_data["options"] = params
    else:
        if isinstance(params, dict):
            for key, value in params.items():
                if value is not None:
                    form_data[key] = value

        if "logit_bias" in params and params["logit_bias"] is not None:
            try:
                logit_bias = convert_logit_bias_input_to_json(params["logit_bias"])

                if logit_bias:
                    form_data["logit_bias"] = json.loads(logit_bias)
            except Exception as e:
                log.exception(f"Error parsing logit_bias: {e}")

    return form_data


async def convert_url_images_to_base64(form_data):
    messages = form_data.get("messages", [])

    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue

        new_content = []

        for item in content:
            if not isinstance(item, dict) or item.get("type") != "image_url":
                new_content.append(item)
                continue

            image_url = item.get("image_url", {}).get("url", "")
            if image_url.startswith("data:image/"):
                new_content.append(item)
                continue

            try:
                base64_data = await asyncio.to_thread(
                    get_image_base64_from_url, image_url
                )
                new_content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": base64_data},
                    }
                )
            except Exception as e:
                log.debug(f"Error converting image URL to base64: {e}")
                new_content.append(item)

        message["content"] = new_content

    return form_data


def load_messages_from_db(chat_id: str, message_id: str) -> Optional[list[dict]]:
    """
    Load the message chain from DB up to message_id,
    keeping only LLM-relevant fields (role, content, output).
    """
    messages_map = Chats.get_messages_map_by_chat_id(chat_id)
    if not messages_map:
        return None

    db_messages = get_message_list(messages_map, message_id)
    if not db_messages:
        return None

    return [
        {k: v for k, v in msg.items() if k in ("role", "content", "output", "files")}
        for msg in db_messages
    ]


def process_messages_with_output(
    messages: list[dict], strip_terminal_calls: bool = False
) -> list[dict]:
    """
    Process messages with OR-aligned output items for LLM consumption.

    For assistant messages with 'output' field, produces properly formatted
    OpenAI-style messages (tool_calls + tool results). Strips 'output' before LLM.
    """
    processed = []

    for message in messages:
        if message.get("role") == "assistant" and message.get("output"):
            # Use output items for clean OpenAI-format messages
            output_items = message["output"]
            if strip_terminal_calls:
                output_items = filter_terminal_tool_history_output(output_items)

            output_messages = convert_output_to_messages(output_items, raw=True)
            if output_messages:
                processed.extend(output_messages)
                continue

        # Strip 'output' field before adding (LLM shouldn't see it)
        clean_message = {k: v for k, v in message.items() if k != "output"}
        processed.append(clean_message)

    return processed


async def process_chat_payload(request, form_data, user, metadata, model):
    # Pipeline Inlet -> Filter Inlet -> Chat Memory -> Chat Web Search -> Chat Image Generation
    # -> Chat Code Interpreter (Form Data Update) -> (Default) Chat Tools Function Calling
    # -> Chat Files

    form_data = apply_params_to_form_data(form_data, model)
    log.debug(f"form_data: {form_data}")

    requested_terminal_id = form_data.get("terminal_id", None)
    default_terminal_id = None
    if metadata.get("params", {}).get("function_calling") == "native":
        default_terminal_id = get_default_terminal_id(request, user)
    active_terminal_id = requested_terminal_id or default_terminal_id
    historical_terminal_files = []

    # Load messages from DB when available — DB preserves structured 'output' items
    # which the frontend strips, causing tool calls to be merged into content.
    chat_id = metadata.get("chat_id")
    parent_message_id = metadata.get("parent_message_id")

    if chat_id and parent_message_id and not chat_id.startswith("local:"):
        db_messages = load_messages_from_db(chat_id, parent_message_id)
        if db_messages:
            system_message = get_system_message(form_data.get("messages", []))
            form_data["messages"] = (
                [system_message, *db_messages] if system_message else db_messages
            )

            # Inject image files into content as image_url parts (mirrors frontend logic)
            # unless an attached sandbox terminal is active. In sandbox mode we
            # want the model to rely on the mounted /workspace paths instead of
            # inventing provider-specific local paths such as /mnt/data/...
            for message in form_data["messages"]:
                if active_terminal_id and message.get("role") == "user":
                    historical_terminal_files = merge_chat_file_items(
                        historical_terminal_files, message.get("files", [])
                    )

                image_files = [
                    f
                    for f in message.get("files", [])
                    if f.get("type") == "image"
                    or (f.get("content_type") or "").startswith("image/")
                ]
                if (
                    message.get("role") == "user"
                    and image_files
                ):
                    text_content = message.get("content", "")
                    if isinstance(text_content, str):
                        message["content"] = [
                            {"type": "text", "text": text_content},
                            *[
                                {
                                    "type": "image_url",
                                    "image_url": {"url": f["url"]},
                                }
                                for f in image_files
                                if f.get("url")
                            ],
                        ]
                # Strip files field — it's been incorporated into content
                message.pop("files", None)

    # Process messages with OR-aligned output items for clean LLM messages
    form_data["messages"] = process_messages_with_output(
        form_data.get("messages", []), strip_terminal_calls=bool(active_terminal_id)
    )

    system_message = get_system_message(form_data.get("messages", []))
    if system_message:  # Chat Controls/User Settings
        try:
            form_data = apply_system_prompt_to_body(
                system_message.get("content"), form_data, metadata, user, replace=True
            )  # Required to handle system prompt variables
        except:
            pass

    form_data = await convert_url_images_to_base64(form_data)

    event_emitter = get_event_emitter(metadata)
    event_caller = get_event_call(metadata)

    extra_params = {
        "__event_emitter__": event_emitter,
        "__event_call__": event_caller,
        "__user__": user.model_dump() if isinstance(user, UserModel) else {},
        "__metadata__": metadata,
        "__oauth_token__": await get_system_oauth_token(request, user),
        "__request__": request,
        "__model__": model,
        "__chat_id__": metadata.get("chat_id"),
        "__message_id__": metadata.get("message_id"),
    }
    # Initialize events to store additional event to be sent to the client
    # Initialize contexts and citation
    if getattr(request.state, "direct", False) and hasattr(request.state, "model"):
        models = {
            request.state.model["id"]: request.state.model,
        }
    else:
        models = request.app.state.MODELS

    task_model_id = get_task_model_id(
        form_data["model"],
        request.app.state.config.TASK_MODEL,
        request.app.state.config.TASK_MODEL_EXTERNAL,
        models,
    )

    events = []
    sources = []

    # Folder "Project" handling
    # Check if the request has chat_id and is inside of a folder
    # Uses lightweight column query — only fetches folder_id, not the full chat JSON blob
    chat_id = metadata.get("chat_id", None)
    if chat_id and user:
        folder_id = Chats.get_chat_folder_id(chat_id, user.id)
        if folder_id:
            folder = Folders.get_folder_by_id_and_user_id(folder_id, user.id)

            if folder and folder.data:
                if "system_prompt" in folder.data:
                    form_data = apply_system_prompt_to_body(
                        folder.data["system_prompt"], form_data, metadata, user
                    )
                if "files" in folder.data:
                    if metadata.get("params", {}).get("function_calling") != "native":
                        form_data["files"] = [
                            *folder.data["files"],
                            *form_data.get("files", []),
                        ]
                    else:
                        # Native FC: skip RAG injection, builtin tools
                        # will read folder knowledge from metadata.
                        metadata["folder_knowledge"] = folder.data["files"]

    # Model "Knowledge" handling
    user_message = get_last_user_message(form_data["messages"])
    model_knowledge = model.get("info", {}).get("meta", {}).get("knowledge", False)

    if (
        model_knowledge
        and metadata.get("params", {}).get("function_calling") != "native"
    ):
        await event_emitter(
            {
                "type": "status",
                "data": {
                    "action": "knowledge_search",
                    "query": user_message,
                    "done": False,
                },
            }
        )

        knowledge_files = []
        for item in model_knowledge:
            if item.get("collection_name"):
                knowledge_files.append(
                    {
                        "id": item.get("collection_name"),
                        "name": item.get("name"),
                        "legacy": True,
                    }
                )
            elif item.get("collection_names"):
                knowledge_files.append(
                    {
                        "name": item.get("name"),
                        "type": "collection",
                        "collection_names": item.get("collection_names"),
                        "legacy": True,
                    }
                )
            else:
                knowledge_files.append(item)

        files = form_data.get("files", [])
        files.extend(knowledge_files)
        form_data["files"] = files

    variables = form_data.pop("variables", None)

    # Process the form_data through the pipeline
    try:
        form_data = await process_pipeline_inlet_filter(
            request, form_data, user, models
        )
    except Exception as e:
        raise e

    try:
        filter_ids = get_sorted_filter_ids(
            request, model, metadata.get("filter_ids", [])
        )
        filter_functions = Functions.get_functions_by_ids(filter_ids)

        form_data, flags = await process_filter_functions(
            request=request,
            filter_functions=filter_functions,
            filter_type="inlet",
            form_data=form_data,
            extra_params=extra_params,
        )
    except Exception as e:
        raise Exception(f"{e}")

    features = form_data.pop("features", None) or {}
    api_config = get_model_api_config(request, model)
    native_responses_web_search_tool = None
    native_responses_web_search_enabled = False
    if features.get("web_search"):
        native_responses_web_search_tool = get_native_web_search_tool(
            api_config, user
        )

        if (
            native_responses_web_search_tool is not None
            and metadata.get("params", {}).get("function_calling") != "native"
        ):
            metadata["params"]["function_calling"] = "native"

        native_responses_web_search_enabled = (
            native_responses_web_search_tool is not None
            and metadata.get("params", {}).get("function_calling") == "native"
        )

    extra_params["__features__"] = features
    if features:
        if "voice" in features and features["voice"]:
            if request.app.state.config.VOICE_MODE_PROMPT_TEMPLATE != None:
                if request.app.state.config.VOICE_MODE_PROMPT_TEMPLATE != "":
                    template = request.app.state.config.VOICE_MODE_PROMPT_TEMPLATE
                else:
                    template = DEFAULT_VOICE_MODE_PROMPT_TEMPLATE

                form_data["messages"] = add_or_update_system_message(
                    template,
                    form_data["messages"],
                )

        if "memory" in features and features["memory"]:
            # Skip forced memory injection when native FC is enabled - model can use memory tools
            if metadata.get("params", {}).get("function_calling") != "native":
                form_data = await chat_memory_handler(
                    request, form_data, extra_params, user
                )

        if "web_search" in features and features["web_search"]:
            # Skip forced RAG web search when native FC is enabled - model can use web_search tool
            if metadata.get("params", {}).get("function_calling") != "native":
                form_data = await chat_web_search_handler(
                    request, form_data, extra_params, user
                )

        if "image_generation" in features and features["image_generation"]:
            # Skip forced image generation when native FC is enabled - model can use generate_image tool
            if metadata.get("params", {}).get("function_calling") != "native":
                form_data = await chat_image_generation_handler(
                    request, form_data, extra_params, user
                )

        if "code_interpreter" in features and features["code_interpreter"]:
            engine = getattr(
                request.app.state.config, "CODE_INTERPRETER_ENGINE", "pyodide"
            )

            if (
                metadata.get("params", {}).get("function_calling") == "native"
                and active_terminal_id
            ):
                # A sandbox terminal supersedes the builtin code interpreter for
                # this request. Keep the feature flag untouched for the caller,
                # but do not inject pyodide- or jupyter-specific prompts/tools.
                pass

            # Skip XML-tag prompt injection when native FC is enabled —
            # execute_code will be injected as a builtin tool instead
            elif metadata.get("params", {}).get("function_calling") != "native":
                prompt = (
                    request.app.state.config.CODE_INTERPRETER_PROMPT_TEMPLATE
                    if request.app.state.config.CODE_INTERPRETER_PROMPT_TEMPLATE != ""
                    else DEFAULT_CODE_INTERPRETER_PROMPT
                )

                # Append filesystem awareness only for pyodide engine
                if engine != "jupyter":
                    prompt += CODE_INTERPRETER_PYODIDE_PROMPT

                form_data["messages"] = add_or_update_user_message(
                    prompt,
                    form_data["messages"],
                )
            else:
                # Native FC: tool docstring can't be dynamic, so inject
                # filesystem context into messages for pyodide engine
                if engine != "jupyter":
                    form_data["messages"] = add_or_update_user_message(
                        CODE_INTERPRETER_PYODIDE_PROMPT,
                        form_data["messages"],
                    )

    tool_ids = form_data.pop("tool_ids", None)
    terminal_id = form_data.pop("terminal_id", None) or active_terminal_id
    files = form_data.pop("files", None)

    # Caller-provided OpenAI-style tools take precedence over server-side
    # tool resolution (tool_ids, MCP servers, builtin tools).
    payload_tools = form_data.get("tools", None)

    # Skills
    user_skill_ids = set(form_data.pop("skill_ids", None) or [])
    model_skill_ids = set(model.get("info", {}).get("meta", {}).get("skillIds", []))

    all_skill_ids = user_skill_ids | model_skill_ids
    available_skills = []
    if all_skill_ids:
        from open_webui.models.skills import Skills as SkillsModel

        accessible_skill_ids = {
            s.id for s in SkillsModel.get_skills_by_user_id(user.id, "read")
        }
        available_skills = [
            s
            for sid in all_skill_ids
            if sid in accessible_skill_ids
            and (s := SkillsModel.get_skill_by_id(sid))
            and s.is_active
        ]

        skill_descriptions = ""
        for skill in available_skills:
            if skill.id in user_skill_ids:
                # User-selected: inject full content
                form_data["messages"] = add_or_update_system_message(
                    f'<skill name="{skill.name}">\n{skill.content}\n</skill>',
                    form_data["messages"],
                    append=True,
                )
            else:
                # Model-attached: name+description only
                skill_descriptions += f"<skill>\n<name>{skill.name}</name>\n<description>{skill.description or ''}</description>\n</skill>\n"

        if skill_descriptions:
            form_data["messages"] = add_or_update_system_message(
                f"<available_skills>\n{skill_descriptions}</available_skills>",
                form_data["messages"],
                append=True,
            )

    file_skills_dir = get_file_skills_dir()
    file_skills = discover_file_skills(file_skills_dir)
    file_skills_section = render_file_skills_section(file_skills, file_skills_dir)
    if file_skills_section:
        form_data["messages"] = add_or_update_system_message(
            file_skills_section,
            form_data["messages"],
            append=True,
        )

    prompt = get_last_user_message(form_data["messages"])
    for file_skill in get_triggered_file_skills(file_skills, prompt):
        form_data["messages"] = add_or_update_system_message(
            f'<skill name="{file_skill.name}" source="file">\n{file_skill.content}\n</skill>',
            form_data["messages"],
            append=True,
        )

    # TODO: re-enable URL extraction from prompt
    # urls = []
    # if prompt and len(prompt or "") < 500 and (not files or len(files) == 0):
    #     urls = extract_urls(prompt)

    if files:
        if not files:
            files = []

        for file_item in files:
            if file_item.get("type", "file") == "folder":
                # Get folder files
                folder_id = file_item.get("id", None)
                if folder_id:
                    folder = Folders.get_folder_by_id_and_user_id(folder_id, user.id)
                    if folder and folder.data and "files" in folder.data:
                        files = [f for f in files if f.get("id", None) != folder_id]
                        files = [*files, *folder.data["files"]]

        # files = [*files, *[{"type": "url", "url": url, "name": url} for url in urls]]
        # Remove duplicate files based on their content
        files = list({json.dumps(f, sort_keys=True): f for f in files}.values())

    if active_terminal_id and historical_terminal_files:
        files = merge_chat_file_items(historical_terminal_files, files or [])

    metadata = {
        **metadata,
        "tool_ids": tool_ids,
        "terminal_id": terminal_id,
        "files": files,
    }
    form_data["metadata"] = metadata

    if terminal_id:
        form_data["messages"] = add_or_update_system_message(
            build_terminal_execution_prompt(metadata),
            form_data["messages"],
            append=True,
        )

    if terminal_id and files:
        mounted_terminal_files = await sync_chat_files_to_terminal(
            request,
            files,
            user,
            metadata,
            extra_params,
        )
        if mounted_terminal_files:
            form_data["messages"] = add_or_update_system_message(
                build_terminal_attachment_prompt(
                    mounted_terminal_files,
                    metadata.get("terminal_files_dir", "/workspace"),
                ),
                form_data["messages"],
                append=True,
            )

    # When the caller provides an explicit OpenAI-style `tools` array in the
    # request body, skip all server-side tool resolution and pass the caller's
    # tools through to the model unchanged.
    if not payload_tools:
        # Server side tools
        tool_ids = metadata.get("tool_ids", None)
        # Client side tools
        direct_tool_servers = metadata.get("tool_servers", None)

        log.debug(f"{tool_ids=}")
        log.debug(f"{direct_tool_servers=}")

        tools_dict = {}

        mcp_clients = {}
        mcp_tools_dict = {}

        if tool_ids:
            for tool_id in tool_ids:
                if tool_id.startswith("server:mcp:"):
                    try:
                        server_id = tool_id[len("server:mcp:") :]

                        mcp_server_connection = None
                        for (
                            server_connection
                        ) in request.app.state.config.TOOL_SERVER_CONNECTIONS:
                            if (
                                server_connection.get("type", "") == "mcp"
                                and server_connection.get("info", {}).get("id")
                                == server_id
                            ):
                                mcp_server_connection = server_connection
                                break

                        if not mcp_server_connection:
                            log.error(f"MCP server with id {server_id} not found")
                            continue

                        # Check access control for MCP server
                        if not has_connection_access(user, mcp_server_connection):
                            log.warning(
                                f"Access denied to MCP server {server_id} for user {user.id}"
                            )
                            continue

                        auth_type = mcp_server_connection.get("auth_type", "")
                        headers = {}
                        if auth_type == "bearer":
                            headers["Authorization"] = (
                                f"Bearer {mcp_server_connection.get('key', '')}"
                            )
                        elif auth_type == "none":
                            # No authentication
                            pass
                        elif auth_type == "session":
                            headers["Authorization"] = (
                                f"Bearer {request.state.token.credentials}"
                            )
                        elif auth_type == "system_oauth":
                            oauth_token = extra_params.get("__oauth_token__", None)
                            if oauth_token:
                                headers["Authorization"] = (
                                    f"Bearer {oauth_token.get('access_token', '')}"
                                )
                        elif auth_type == "oauth_2.1":
                            try:
                                splits = server_id.split(":")
                                server_id = splits[-1] if len(splits) > 1 else server_id

                                oauth_token = await request.app.state.oauth_client_manager.get_oauth_token(
                                    user.id, f"mcp:{server_id}"
                                )

                                if oauth_token:
                                    headers["Authorization"] = (
                                        f"Bearer {oauth_token.get('access_token', '')}"
                                    )
                            except Exception as e:
                                log.error(f"Error getting OAuth token: {e}")
                                oauth_token = None

                        connection_headers = mcp_server_connection.get("headers", None)
                        if connection_headers and isinstance(connection_headers, dict):
                            for key, value in connection_headers.items():
                                headers[key] = value

                        # Add user info headers if enabled
                        if ENABLE_FORWARD_USER_INFO_HEADERS and user:
                            headers = include_user_info_headers(headers, user)
                            if metadata and metadata.get("chat_id"):
                                headers[FORWARD_SESSION_INFO_HEADER_CHAT_ID] = (
                                    metadata.get("chat_id")
                                )
                            if metadata and metadata.get("message_id"):
                                headers[FORWARD_SESSION_INFO_HEADER_MESSAGE_ID] = (
                                    metadata.get("message_id")
                                )

                        mcp_clients[server_id] = MCPClient()
                        await mcp_clients[server_id].connect(
                            url=mcp_server_connection.get("url", ""),
                            headers=headers if headers else None,
                        )

                        function_name_filter_list = mcp_server_connection.get(
                            "config", {}
                        ).get("function_name_filter_list", "")

                        if isinstance(function_name_filter_list, str):
                            function_name_filter_list = function_name_filter_list.split(
                                ","
                            )

                        tool_specs = await mcp_clients[server_id].list_tool_specs()
                        for tool_spec in tool_specs:

                            def make_tool_function(client, function_name):
                                async def tool_function(**kwargs):
                                    return await client.call_tool(
                                        function_name,
                                        function_args=kwargs,
                                    )

                                return tool_function

                            if function_name_filter_list:
                                if not is_string_allowed(
                                    tool_spec["name"], function_name_filter_list
                                ):
                                    # Skip this function
                                    continue

                            tool_function = make_tool_function(
                                mcp_clients[server_id], tool_spec["name"]
                            )

                            mcp_tools_dict[f"{server_id}_{tool_spec['name']}"] = {
                                "spec": {
                                    **tool_spec,
                                    "name": f"{server_id}_{tool_spec['name']}",
                                },
                                "callable": tool_function,
                                "type": "mcp",
                                "client": mcp_clients[server_id],
                                "direct": False,
                            }
                    except Exception as e:
                        log.debug(e)
                        if event_emitter:
                            await event_emitter(
                                {
                                    "type": "chat:message:error",
                                    "data": {
                                        "error": {
                                            "content": f"Failed to connect to MCP server '{server_id}'"
                                        }
                                    },
                                }
                            )
                        continue

            tools_dict = await get_tools(
                request,
                tool_ids,
                user,
                {
                    **extra_params,
                    "__model__": models[task_model_id],
                    "__messages__": form_data["messages"],
                    "__files__": metadata.get("files", []),
                },
            )

            if mcp_tools_dict:
                tools_dict = {**tools_dict, **mcp_tools_dict}

        # Resolve terminal tools if terminal_id is set (outside tool_ids check
        # so system terminals work even when no other tools are selected)
        if terminal_id:
            try:
                terminal_tools = await get_terminal_tools(
                    request,
                    terminal_id,
                    user,
                    extra_params,
                    metadata,
                )
                if terminal_tools:
                    tools_dict = {**tools_dict, **terminal_tools}
            except Exception as e:
                log.exception(e)

        if direct_tool_servers:
            for tool_server in direct_tool_servers:
                tool_specs = tool_server.pop("specs", [])

                for tool in tool_specs:
                    tools_dict[tool["name"]] = {
                        "spec": tool,
                        "direct": True,
                        "server": tool_server,
                    }

        if mcp_clients:
            metadata["mcp_clients"] = mcp_clients

        # Inject builtin tools for native function calling based on enabled features and model capability
        # Check if builtin_tools capability is enabled for this model (defaults to True if not specified)
        builtin_tools_enabled = (
            model.get("info", {}).get("meta", {}).get("capabilities") or {}
        ).get("builtin_tools", True)
        if (
            metadata.get("params", {}).get("function_calling") == "native"
            and builtin_tools_enabled
        ):
            # Only inject generic attached_files URL context when sandbox is not
            # active. In sandbox mode the authoritative file hints come from the
            # <sandbox_files> prompt with exact mounted /workspace paths.
            if not terminal_id:
                chat_id = metadata.get("chat_id")
                form_data["messages"] = add_file_context(
                    form_data.get("messages", []), chat_id, user
                )
            builtin_features = (
                {**features, "web_search": False}
                if native_responses_web_search_enabled
                else {**features}
            )
            if terminal_id:
                builtin_features["code_interpreter"] = False

            builtin_tools = get_builtin_tools(
                request,
                {
                    **extra_params,
                    "__event_emitter__": event_emitter,
                    "__skill_ids__": [
                        s.id for s in available_skills if s.id not in user_skill_ids
                    ],
                    "__file_skills_enabled__": bool(file_skills),
                },
                builtin_features,
                model,
            )
            for name, tool_dict in builtin_tools.items():
                if name not in tools_dict:
                    tools_dict[name] = tool_dict

        if tools_dict:
            if metadata.get("params", {}).get("function_calling") == "native":
                # If the function calling is native, then call the tools function calling handler
                metadata["tools"] = tools_dict
                form_data["tools"] = [
                    {"type": "function", "function": tool.get("spec", {})}
                    for tool in tools_dict.values()
                ]
            else:
                # If the function calling is not native, then call the tools function calling handler
                try:
                    form_data, flags = await chat_completion_tools_handler(
                        request, form_data, extra_params, user, models, tools_dict
                    )
                    sources.extend(flags.get("sources", []))
                except Exception as e:
                    log.exception(e)

        if (
            metadata.get("params", {}).get("function_calling") == "native"
            and native_responses_web_search_tool
        ):
            existing_tools = form_data.get("tools", [])
            if not any(
                isinstance(tool, dict)
                and tool.get("type")
                in {"web_search", "web_search_preview"}
                for tool in existing_tools
            ):
                form_data["tools"] = [
                    *existing_tools,
                    native_responses_web_search_tool,
                ]

    # Check if file context extraction is enabled for this model (default True)
    file_context_enabled = (
        model.get("info", {}).get("meta", {}).get("capabilities") or {}
    ).get("file_context", True)

    if file_context_enabled:
        try:
            form_data, flags = await chat_completion_files_handler(
                request, form_data, extra_params, user
            )
            sources.extend(flags.get("sources", []))
        except Exception as e:
            log.exception(e)

    # Save the pre-RAG message state so the native tool call loop can
    # restore to the true original (before file-source injection) rather
    # than a snapshot that already has the RAG template baked in.
    system_message = get_system_message(form_data["messages"])
    metadata["system_prompt"] = (
        get_content_from_message(system_message) if system_message else None
    )
    metadata["user_prompt"] = get_last_user_message(form_data["messages"])
    metadata["sources"] = sources[:] if sources else []

    # If context is not empty, insert it into the messages
    if sources and prompt:
        form_data["messages"] = apply_source_context_to_messages(
            request, form_data["messages"], sources, prompt
        )

    # If there are citations, add them to the data_items
    sources = [
        source
        for source in sources
        if source.get("source", {}).get("name", "")
        or source.get("source", {}).get("id", "")
    ]

    if len(sources) > 0:
        events.append({"sources": sources})

    if model_knowledge:
        await event_emitter(
            {
                "type": "status",
                "data": {
                    "action": "knowledge_search",
                    "query": user_message,
                    "done": True,
                    "hidden": True,
                },
            }
        )

    return form_data, metadata, events


def get_event_emitter_and_caller(metadata):
    event_emitter = None
    event_caller = None
    if (
        "session_id" in metadata
        and metadata["session_id"]
        and "chat_id" in metadata
        and metadata["chat_id"]
        and "message_id" in metadata
        and metadata["message_id"]
    ):
        event_emitter = get_event_emitter(metadata)
        event_caller = get_event_call(metadata)
    return event_emitter, event_caller


def build_chat_response_context(
    request, form_data, user, model, metadata, tasks, events
):
    event_emitter, event_caller = get_event_emitter_and_caller(metadata)
    return {
        "request": request,
        "form_data": form_data,
        "user": user,
        "model": model,
        "metadata": metadata,
        "tasks": tasks,
        "events": events,
        "event_emitter": event_emitter,
        "event_caller": event_caller,
    }


def get_response_data(response):
    if isinstance(response, list) and len(response) == 1:
        # If the response is a single-item list, unwrap it #17213
        response = response[0]

    if isinstance(response, JSONResponse):
        if isinstance(response.body, bytes):
            try:
                response_data = json.loads(response.body.decode("utf-8", "replace"))
            except json.JSONDecodeError:
                response_data = {"error": {"detail": "Invalid JSON response"}}
        else:
            response_data = response
    elif isinstance(response, dict):
        response_data = response
    else:
        response_data = None

    return response, response_data


def merge_events_into_response(response_data, events):
    if events and isinstance(events, list):
        extra_response = {}
        for event in events:
            if isinstance(event, dict):
                extra_response.update(event)
            else:
                extra_response[event] = True

        return {
            **extra_response,
            **response_data,
        }
    return response_data


def build_response_object(response, response_data):
    if isinstance(response, dict):
        return response_data
    if isinstance(response, JSONResponse):
        return JSONResponse(
            content=response_data,
            headers=response.headers,
            status_code=response.status_code,
        )
    return response


async def get_system_oauth_token(request, user):
    oauth_token = None
    try:
        if request.cookies.get("oauth_session_id", None):
            oauth_token = await request.app.state.oauth_manager.get_oauth_token(
                user.id,
                request.cookies.get("oauth_session_id", None),
            )
    except Exception as e:
        log.error(f"Error getting OAuth token: {e}")
    return oauth_token


async def background_tasks_handler(ctx):
    request = ctx["request"]
    form_data = ctx["form_data"]
    user = ctx["user"]
    metadata = ctx["metadata"]
    tasks = ctx["tasks"]
    event_emitter = ctx["event_emitter"]

    message = None
    messages = []

    if "chat_id" in metadata and not metadata["chat_id"].startswith("local:"):
        messages_map = Chats.get_messages_map_by_chat_id(metadata["chat_id"])
        message = messages_map.get(metadata["message_id"]) if messages_map else None

        message_list = get_message_list(messages_map, metadata["message_id"])

        # Remove details tags and files from the messages.
        # as get_message_list creates a new list, it does not affect
        # the original messages outside of this handler

        messages = []
        for message in message_list:
            content = message.get("content", "")
            if isinstance(content, list):
                for item in content:
                    if item.get("type") == "text":
                        content = item["text"]
                        break

            if isinstance(content, str):
                content = re.sub(
                    r"<details\b[^>]*>.*?<\/details>|!\[.*?\]\(.*?\)",
                    "",
                    content,
                    flags=re.S | re.I,
                ).strip()

            messages.append(
                {
                    **message,
                    "role": message.get(
                        "role", "assistant"
                    ),  # Safe fallback for missing role
                    "content": content,
                }
            )
    else:
        # Local temp chat, get the model and message from the form_data
        message = get_last_user_message_item(form_data.get("messages", []))
        messages = form_data.get("messages", [])
        if message:
            message["model"] = form_data.get("model")

    if message and "model" in message:
        if tasks and messages:
            if (
                TASKS.FOLLOW_UP_GENERATION in tasks
                and tasks[TASKS.FOLLOW_UP_GENERATION]
            ):
                res = await generate_follow_ups(
                    request,
                    {
                        "model": message["model"],
                        "messages": messages,
                        "message_id": metadata["message_id"],
                        "chat_id": metadata["chat_id"],
                    },
                    user,
                )

                if res and isinstance(res, dict):
                    if len(res.get("choices", [])) == 1:
                        response_message = res.get("choices", [])[0].get("message", {})

                        follow_ups_string = response_message.get(
                            "content"
                        ) or response_message.get("reasoning_content", "")
                    else:
                        follow_ups_string = ""

                    follow_ups_string = follow_ups_string[
                        follow_ups_string.find("{") : follow_ups_string.rfind("}") + 1
                    ]

                    try:
                        follow_ups = json.loads(follow_ups_string).get("follow_ups", [])
                        await event_emitter(
                            {
                                "type": "chat:message:follow_ups",
                                "data": {
                                    "follow_ups": follow_ups,
                                },
                            }
                        )

                        if not metadata.get("chat_id", "").startswith("local:"):
                            Chats.upsert_message_to_chat_by_id_and_message_id(
                                metadata["chat_id"],
                                metadata["message_id"],
                                {
                                    "followUps": follow_ups,
                                },
                            )

                    except Exception as e:
                        pass

            if not metadata.get("chat_id", "").startswith(
                "local:"
            ):  # Only update titles and tags for non-temp chats
                if TASKS.TITLE_GENERATION in tasks:
                    user_message = get_last_user_message(messages)
                    if user_message and len(user_message) > 100:
                        user_message = user_message[:100] + "..."

                    title = None
                    if tasks[TASKS.TITLE_GENERATION]:
                        res = await generate_title(
                            request,
                            {
                                "model": message["model"],
                                "messages": messages,
                                "chat_id": metadata["chat_id"],
                            },
                            user,
                        )

                        if res and isinstance(res, dict):
                            if len(res.get("choices", [])) == 1:
                                response_message = res.get("choices", [])[0].get(
                                    "message", {}
                                )

                                title_string = (
                                    response_message.get("content")
                                    or response_message.get(
                                        "reasoning_content",
                                    )
                                    or message.get("content", user_message)
                                )
                            else:
                                title_string = ""

                            title_string = title_string[
                                title_string.find("{") : title_string.rfind("}") + 1
                            ]

                            try:
                                title = json.loads(title_string).get(
                                    "title", user_message
                                )
                            except Exception as e:
                                title = ""

                            if not title:
                                title = messages[0].get("content", user_message)

                            Chats.update_chat_title_by_id(metadata["chat_id"], title)

                            await event_emitter(
                                {
                                    "type": "chat:title",
                                    "data": title,
                                }
                            )

                    if title == None and len(messages) == 2:
                        title = messages[0].get("content", user_message)

                        Chats.update_chat_title_by_id(metadata["chat_id"], title)

                        await event_emitter(
                            {
                                "type": "chat:title",
                                "data": message.get("content", user_message),
                            }
                        )

                if TASKS.TAGS_GENERATION in tasks and tasks[TASKS.TAGS_GENERATION]:
                    res = await generate_chat_tags(
                        request,
                        {
                            "model": message["model"],
                            "messages": messages,
                            "chat_id": metadata["chat_id"],
                        },
                        user,
                    )

                    if res and isinstance(res, dict):
                        if len(res.get("choices", [])) == 1:
                            response_message = res.get("choices", [])[0].get(
                                "message", {}
                            )

                            tags_string = response_message.get(
                                "content"
                            ) or response_message.get("reasoning_content", "")
                        else:
                            tags_string = ""

                        tags_string = tags_string[
                            tags_string.find("{") : tags_string.rfind("}") + 1
                        ]

                        try:
                            tags = json.loads(tags_string).get("tags", [])
                            Chats.update_chat_tags_by_id(
                                metadata["chat_id"], tags, user
                            )

                            await event_emitter(
                                {
                                    "type": "chat:tags",
                                    "data": tags,
                                }
                            )
                        except Exception as e:
                            pass


async def non_streaming_chat_response_handler(response, ctx):
    request = ctx["request"]

    user = ctx["user"]
    metadata = ctx["metadata"]
    events = ctx["events"]

    event_emitter = ctx["event_emitter"]

    response, response_data = get_response_data(response)
    if response_data is None:
        return response

    if event_emitter:
        try:
            if "error" in response_data:
                error = response_data.get("error")

                if isinstance(error, dict):
                    error = error.get("detail", error)
                else:
                    error = str(error)

                Chats.upsert_message_to_chat_by_id_and_message_id(
                    metadata["chat_id"],
                    metadata["message_id"],
                    {
                        "error": {"content": error},
                    },
                )
                if isinstance(error, str) or isinstance(error, dict):
                    await event_emitter(
                        {
                            "type": "chat:message:error",
                            "data": {"error": {"content": error}},
                        }
                    )

            if "selected_model_id" in response_data:
                Chats.upsert_message_to_chat_by_id_and_message_id(
                    metadata["chat_id"],
                    metadata["message_id"],
                    {
                        "selectedModelId": response_data["selected_model_id"],
                    },
                )

            choices = response_data.get("choices", [])
            if choices and choices[0].get("message", {}).get("content"):
                content = response_data["choices"][0]["message"]["content"]

                if content:
                    await event_emitter(
                        {
                            "type": "chat:completion",
                            "data": response_data,
                        }
                    )

                    title = Chats.get_chat_title_by_id(metadata["chat_id"])

                    # Use output from backend if provided (OR-compliant backends),
                    # otherwise generate from response content
                    response_output = response_data.get("output")
                    if not response_output:
                        response_output = [
                            {
                                "type": "message",
                                "id": output_id("msg"),
                                "status": "completed",
                                "role": "assistant",
                                "content": [{"type": "output_text", "text": content}],
                            }
                        ]

                    await event_emitter(
                        {
                            "type": "chat:completion",
                            "data": {
                                "done": True,
                                "content": content,
                                "output": response_output,
                                "title": title,
                            },
                        }
                    )

                    # Save message in the database
                    usage = normalize_usage(response_data.get("usage", {}) or {})

                    Chats.upsert_message_to_chat_by_id_and_message_id(
                        metadata["chat_id"],
                        metadata["message_id"],
                        {
                            "role": "assistant",
                            "content": content,
                            "output": response_output,
                            **({"usage": usage} if usage else {}),
                        },
                    )

                    # Send a webhook notification if the user is not active
                    if not Users.is_user_active(user.id):
                        webhook_url = Users.get_user_webhook_url_by_id(user.id)
                        if webhook_url:
                            await post_webhook(
                                request.app.state.WEBUI_NAME,
                                webhook_url,
                                f"{title} - {request.app.state.config.WEBUI_URL}/c/{metadata['chat_id']}\n\n{content}",
                                {
                                    "action": "chat",
                                    "message": content,
                                    "title": title,
                                    "url": f"{request.app.state.config.WEBUI_URL}/c/{metadata['chat_id']}",
                                },
                            )

                    await background_tasks_handler(ctx)

            response = build_response_object(
                response, merge_events_into_response(response_data, events)
            )
        except Exception as e:
            log.debug(f"Error occurred while processing request: {e}")
            pass

        return response

    if isinstance(response, dict):
        response = merge_events_into_response(response_data, events)

    return response


async def streaming_chat_response_handler(response, ctx):
    request = ctx["request"]

    form_data = ctx["form_data"]

    user = ctx["user"]
    model = ctx["model"]

    metadata = ctx["metadata"]
    events = ctx["events"]

    event_emitter = ctx["event_emitter"]
    event_caller = ctx["event_caller"]

    extra_params = {
        "__event_emitter__": event_emitter,
        "__event_call__": event_caller,
        "__user__": user.model_dump() if isinstance(user, UserModel) else {},
        "__metadata__": metadata,
        "__oauth_token__": await get_system_oauth_token(request, user),
        "__request__": request,
        "__model__": model,
    }

    filter_functions = [
        Functions.get_function_by_id(filter_id)
        for filter_id in get_sorted_filter_ids(
            request, model, metadata.get("filter_ids", [])
        )
    ]

    # Standard streaming response handler
    if event_emitter and event_caller:
        task_id = str(uuid4())  # Create a unique task ID.
        model_id = form_data.get("model", "")

        # Handle as a background task
        async def response_handler(response, events):
            def tag_output_handler(content_type, tags, output):
                """
                Detect special tags (reasoning, solution, code_interpreter) in streaming
                content and create corresponding OR-aligned output items directly.
                Operates on output items instead of content_blocks.

                Uses the text from the output items themselves for tag detection,
                eliminating state divergence between accumulated content and items.
                """
                end_flag = False

                def extract_attributes(tag_content):
                    """Extract attributes from a tag if they exist."""
                    attributes = {}
                    if not tag_content:
                        return attributes
                    matches = re.findall(r'(\w+)\s*=\s*"([^"]+)"', tag_content)
                    for key, value in matches:
                        attributes[key] = value
                    return attributes

                def get_last_text(out):
                    """Get text from last message item, or empty string."""
                    if out and out[-1].get("type") == "message":
                        parts = out[-1].get("content", [])
                        if parts and parts[-1].get("type") == "output_text":
                            return parts[-1].get("text", "")
                    return ""

                def set_last_text(out, text):
                    """Set text on last message item's output_text."""
                    if out and out[-1].get("type") == "message":
                        parts = out[-1].get("content", [])
                        if parts and parts[-1].get("type") == "output_text":
                            parts[-1]["text"] = text

                # Map content_type to output item type
                output_type_map = {
                    "reasoning": "reasoning",
                    "solution": "message",  # solution tags just produce text
                    "code_interpreter": "open_webui:code_interpreter",
                }
                output_item_type = output_type_map.get(content_type, content_type)

                last_type = output[-1].get("type", "") if output else ""

                if last_type == "message":
                    # Use the output item's own text for tag detection
                    item_text = get_last_text(output)
                    for start_tag, end_tag in tags:

                        start_tag_pattern = rf"{re.escape(start_tag)}"
                        if start_tag.startswith("<") and start_tag.endswith(">"):
                            start_tag_pattern = (
                                rf"<{re.escape(start_tag[1:-1])}(\s.*?)?>"
                            )

                        match = re.search(start_tag_pattern, item_text)
                        if match:
                            try:
                                attr_content = match.group(1) if match.group(1) else ""
                            except:
                                attr_content = ""

                            attributes = extract_attributes(attr_content)

                            before_tag = item_text[: match.start()]
                            after_tag = item_text[match.end() :]

                            # Keep only text before the tag in the message
                            set_last_text(output, before_tag)

                            if not before_tag.strip():
                                # Remove empty message item
                                if output and output[-1].get("type") == "message":
                                    output.pop()

                            # Append the new output item
                            if output_item_type == "reasoning":
                                output.append(
                                    {
                                        "type": "reasoning",
                                        "id": output_id("r"),
                                        "status": "in_progress",
                                        "start_tag": start_tag,
                                        "end_tag": end_tag,
                                        "attributes": attributes,
                                        "content": [],
                                        "summary": None,
                                        "started_at": time.time(),
                                    }
                                )
                            elif output_item_type == "open_webui:code_interpreter":
                                output.append(
                                    {
                                        "type": "open_webui:code_interpreter",
                                        "id": output_id("ci"),
                                        "status": "in_progress",
                                        "start_tag": start_tag,
                                        "end_tag": end_tag,
                                        "attributes": attributes,
                                        "lang": attributes.get("lang", "python"),
                                        "code": "",
                                        "output": None,
                                        "started_at": time.time(),
                                    }
                                )
                            else:
                                # solution or other text-producing tag
                                output.append(
                                    {
                                        "type": "message",
                                        "id": output_id("msg"),
                                        "status": "in_progress",
                                        "role": "assistant",
                                        "content": [
                                            {"type": "output_text", "text": ""}
                                        ],
                                        "_tag_type": content_type,
                                        "start_tag": start_tag,
                                        "end_tag": end_tag,
                                        "attributes": attributes,
                                        "started_at": time.time(),
                                    }
                                )

                            if after_tag:
                                # Set the after_tag content on the new item
                                if output_item_type == "reasoning":
                                    output[-1]["content"] = [
                                        {"type": "output_text", "text": after_tag}
                                    ]
                                elif output_item_type == "open_webui:code_interpreter":
                                    output[-1]["code"] = after_tag
                                else:
                                    set_last_text(output, after_tag)

                                _, recursive_end = tag_output_handler(
                                    content_type, tags, output
                                )
                                if recursive_end:
                                    end_flag = True

                            break

                elif (
                    (last_type == "reasoning" and content_type == "reasoning")
                    or (
                        last_type == "open_webui:code_interpreter"
                        and content_type == "code_interpreter"
                    )
                    or (
                        last_type == "message"
                        and output[-1].get("_tag_type") == content_type
                    )
                ):
                    item = output[-1]
                    start_tag = item.get("start_tag", "")
                    end_tag = item.get("end_tag", "")

                    end_tag_pattern = rf"{re.escape(end_tag)}"

                    # Get the block content from the item itself
                    if last_type == "reasoning":
                        parts = item.get("content", [])
                        block_content = ""
                        if parts and parts[-1].get("type") == "output_text":
                            block_content = parts[-1].get("text", "")
                    elif last_type == "open_webui:code_interpreter":
                        block_content = item.get("code", "")
                    else:
                        block_content = get_last_text(output)

                    if re.search(end_tag_pattern, block_content):
                        end_flag = True

                        # Strip start and end tags from content
                        start_tag_pattern = rf"{re.escape(start_tag)}"
                        if start_tag.startswith("<") and start_tag.endswith(">"):
                            start_tag_pattern = (
                                rf"<{re.escape(start_tag[1:-1])}(\s.*?)?>"
                            )
                        block_content = re.sub(
                            start_tag_pattern, "", block_content
                        ).strip()

                        end_tag_regex = re.compile(end_tag_pattern, re.DOTALL)
                        split_content = end_tag_regex.split(block_content, maxsplit=1)

                        block_content = (
                            split_content[0].strip() if split_content else ""
                        )
                        leftover_content = (
                            split_content[1].strip() if len(split_content) > 1 else ""
                        )

                        if block_content:
                            # Update the item with final content
                            if last_type == "reasoning":
                                item["content"] = [
                                    {"type": "output_text", "text": block_content}
                                ]
                                item["ended_at"] = time.time()
                                item["duration"] = int(
                                    item["ended_at"] - item["started_at"]
                                )
                                item["status"] = "completed"
                            elif last_type == "open_webui:code_interpreter":
                                item["code"] = block_content
                                item["ended_at"] = time.time()
                                item["duration"] = int(
                                    item["ended_at"] - item["started_at"]
                                )
                            else:
                                set_last_text(output, block_content)
                                item["ended_at"] = time.time()

                            # Reset by appending a new message item for leftover
                            output.append(
                                {
                                    "type": "message",
                                    "id": output_id("msg"),
                                    "status": "in_progress",
                                    "role": "assistant",
                                    "content": [
                                        {
                                            "type": "output_text",
                                            "text": leftover_content,
                                        }
                                    ],
                                }
                            )
                        else:
                            # Remove the block if content is empty
                            output.pop()
                            output.append(
                                {
                                    "type": "message",
                                    "id": output_id("msg"),
                                    "status": "in_progress",
                                    "role": "assistant",
                                    "content": [
                                        {
                                            "type": "output_text",
                                            "text": leftover_content,
                                        }
                                    ],
                                }
                            )

                return output, end_flag

            message = Chats.get_message_by_id_and_message_id(
                metadata["chat_id"], metadata["message_id"]
            )

            tool_calls = []

            last_assistant_message = None
            try:
                if form_data["messages"][-1]["role"] == "assistant":
                    last_assistant_message = get_last_assistant_message(
                        form_data["messages"]
                    )
            except Exception as e:
                pass

            content = (
                message.get("content", "")
                if message
                else last_assistant_message if last_assistant_message else ""
            )

            # Initialize output: use existing from message if continuing, else create new
            existing_output = message.get("output") if message else None
            if existing_output:
                output = existing_output
            else:
                # Only create an initial message item if there is content to initialize with
                if content:
                    output = [
                        {
                            "type": "message",
                            "id": output_id("msg"),
                            "status": "in_progress",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": content}],
                        }
                    ]
                else:
                    output = []

            usage = None

            reasoning_tags_param = metadata.get("params", {}).get("reasoning_tags")
            DETECT_REASONING_TAGS = reasoning_tags_param is not False
            DETECT_CODE_INTERPRETER = metadata.get("features", {}).get(
                "code_interpreter", False
            )

            reasoning_tags = []
            if DETECT_REASONING_TAGS:
                if (
                    isinstance(reasoning_tags_param, list)
                    and len(reasoning_tags_param) == 2
                ):
                    reasoning_tags = [
                        (reasoning_tags_param[0], reasoning_tags_param[1])
                    ]
                else:
                    reasoning_tags = DEFAULT_REASONING_TAGS

            try:
                for event in events:
                    await event_emitter(
                        {
                            "type": "chat:completion",
                            "data": event,
                        }
                    )

                    # Save message in the database
                    Chats.upsert_message_to_chat_by_id_and_message_id(
                        metadata["chat_id"],
                        metadata["message_id"],
                        {
                            **event,
                        },
                    )

                async def stream_body_handler(response, form_data):
                    nonlocal content
                    nonlocal usage
                    nonlocal output

                    response_started_at = time.time()
                    response_output_start_index = len(output)
                    response_tool_calls = []
                    response_has_reasoning = False
                    first_visible_response_output_at = None

                    delta_count = 0
                    delta_chunk_size = max(
                        CHAT_RESPONSE_STREAM_DELTA_CHUNK_SIZE,
                        int(
                            metadata.get("params", {}).get("stream_delta_chunk_size")
                            or 1
                        ),
                    )
                    last_delta_data = None
                    responses_emit_interval = 1 / 60
                    last_responses_emit_at = 0.0
                    pending_responses_data = None

                    async def flush_pending_delta_data(threshold: int = 0):
                        nonlocal delta_count
                        nonlocal last_delta_data

                        if delta_count >= threshold and last_delta_data:
                            await event_emitter(
                                {
                                    "type": "chat:completion",
                                    "data": last_delta_data,
                                }
                            )
                            delta_count = 0
                            last_delta_data = None

                    async def flush_pending_responses_data(force: bool = False):
                        nonlocal last_responses_emit_at
                        nonlocal pending_responses_data

                        if not pending_responses_data:
                            return

                        now = time.monotonic()
                        if force or (
                            now - last_responses_emit_at >= responses_emit_interval
                        ):
                            await event_emitter(
                                {
                                    "type": "chat:completion",
                                    "data": pending_responses_data,
                                }
                            )
                            last_responses_emit_at = now
                            pending_responses_data = None

                    async for line in response.body_iterator:
                        line = (
                            line.decode("utf-8", "replace")
                            if isinstance(line, bytes)
                            else line
                        )
                        data = line

                        # Skip empty lines
                        if not data.strip():
                            continue

                        # "data:" is the prefix for each event
                        if not data.startswith("data:"):
                            continue

                        # Remove the prefix
                        data = data[len("data:") :].strip()

                        try:
                            data = json.loads(data)

                            data, _ = await process_filter_functions(
                                request=request,
                                filter_functions=filter_functions,
                                filter_type="stream",
                                form_data=data,
                                extra_params={"__body__": form_data, **extra_params},
                            )

                            if data:
                                if "event" in data and not getattr(
                                    request.state, "direct", False
                                ):
                                    await event_emitter(data.get("event", {}))

                                if "selected_model_id" in data:
                                    model_id = data["selected_model_id"]
                                    Chats.upsert_message_to_chat_by_id_and_message_id(
                                        metadata["chat_id"],
                                        metadata["message_id"],
                                        {
                                            "selectedModelId": model_id,
                                        },
                                    )
                                    await event_emitter(
                                        {
                                            "type": "chat:completion",
                                            "data": data,
                                        }
                                    )
                                # Check for Responses API events (type field starts with "response.")
                                elif data.get("type", "").startswith("response."):
                                    event_type = data.get("type", "")
                                    event_item = data.get("item", {})

                                    if (
                                        event_type
                                        in {
                                            "response.output_item.added",
                                            "response.output_item.done",
                                        }
                                        and event_item.get("type") == "web_search_call"
                                    ):
                                        status_data = get_web_search_status_from_response_item(
                                            event_item
                                        )
                                        if status_data:
                                            await event_emitter(
                                                {
                                                    "type": "status",
                                                    "data": status_data,
                                                }
                                            )

                                    if (
                                        event_type.startswith("response.reasoning")
                                        or event_type
                                        == "response.reasoning_summary_part.added"
                                        or event_item.get("type") == "reasoning"
                                    ):
                                        response_has_reasoning = True

                                    if (
                                        first_visible_response_output_at is None
                                        and event_type == "response.output_text.delta"
                                    ):
                                        first_visible_response_output_at = time.time()
                                    elif (
                                        first_visible_response_output_at is None
                                        and event_type
                                        in {
                                            "response.output_item.added",
                                            "response.output_item.done",
                                        }
                                        and response_item_has_visible_content(event_item)
                                    ):
                                        first_visible_response_output_at = time.time()

                                    output, response_metadata = (
                                        handle_responses_streaming_event(
                                            data,
                                            output,
                                            response_started_at=response_started_at,
                                            response_output_start_index=response_output_start_index,
                                        )
                                    )

                                    processed_data = {
                                        "output": output,
                                        "content": serialize_output(output),
                                    }

                                    # print(data)
                                    # print(processed_data)

                                    # Merge any metadata (usage, done, etc.)
                                    if response_metadata:
                                        processed_data.update(response_metadata)

                                    if response_metadata and (
                                        response_metadata.get("done")
                                        or response_metadata.get("error")
                                    ):
                                        pending_responses_data = processed_data
                                        await flush_pending_responses_data(force=True)
                                    else:
                                        pending_responses_data = processed_data
                                        await flush_pending_responses_data()
                                    continue
                                else:
                                    choices = data.get("choices", [])

                                    # Normalize usage data to standard format
                                    raw_usage = data.get("usage", {}) or {}
                                    raw_usage.update(
                                        data.get("timings", {})
                                    )  # llama.cpp
                                    if raw_usage:
                                        usage = normalize_usage(raw_usage)
                                        await event_emitter(
                                            {
                                                "type": "chat:completion",
                                                "data": {
                                                    "usage": usage,
                                                },
                                            }
                                        )

                                    if not choices:
                                        error = data.get("error", {})
                                        if error:
                                            await event_emitter(
                                                {
                                                    "type": "chat:completion",
                                                    "data": {
                                                        "error": error,
                                                    },
                                                }
                                            )
                                        continue

                                    delta = choices[0].get("delta", {})

                                    # Handle delta annotations
                                    annotations = delta.get("annotations")
                                    if annotations:
                                        for annotation in annotations:
                                            if (
                                                annotation.get("type") == "url_citation"
                                                and "url_citation" in annotation
                                            ):
                                                url_citation = annotation[
                                                    "url_citation"
                                                ]

                                                url = url_citation.get("url", "")
                                                title = url_citation.get("title", url)

                                                await event_emitter(
                                                    {
                                                        "type": "source",
                                                        "data": {
                                                            "source": {
                                                                "name": title,
                                                                "url": url,
                                                            },
                                                            "document": [title],
                                                            "metadata": [
                                                                {
                                                                    "source": url,
                                                                    "name": title,
                                                                }
                                                            ],
                                                        },
                                                    }
                                                )

                                    delta_tool_calls = delta.get("tool_calls", None)
                                    if delta_tool_calls:
                                        for delta_tool_call in delta_tool_calls:
                                            tool_call_index = delta_tool_call.get(
                                                "index"
                                            )

                                            if tool_call_index is not None:
                                                # Check if the tool call already exists
                                                current_response_tool_call = None
                                                for (
                                                    response_tool_call
                                                ) in response_tool_calls:
                                                    if (
                                                        response_tool_call.get("index")
                                                        == tool_call_index
                                                    ):
                                                        current_response_tool_call = (
                                                            response_tool_call
                                                        )
                                                        break

                                                if current_response_tool_call is None:
                                                    # Add the new tool call
                                                    delta_tool_call.setdefault(
                                                        "function", {}
                                                    )
                                                    delta_tool_call[
                                                        "function"
                                                    ].setdefault("name", "")
                                                    delta_tool_call[
                                                        "function"
                                                    ].setdefault("arguments", "")
                                                    response_tool_calls.append(
                                                        delta_tool_call
                                                    )
                                                else:
                                                    # Update the existing tool call
                                                    delta_name = delta_tool_call.get(
                                                        "function", {}
                                                    ).get("name")
                                                    delta_arguments = (
                                                        delta_tool_call.get(
                                                            "function", {}
                                                        ).get("arguments")
                                                    )

                                                    if delta_name:
                                                        current_response_tool_call[
                                                            "function"
                                                        ]["name"] = delta_name

                                                    if delta_arguments:
                                                        current_response_tool_call[
                                                            "function"
                                                        ][
                                                            "arguments"
                                                        ] += delta_arguments

                                        # Emit pending tool calls in real-time
                                        if response_tool_calls:
                                            # Flush any pending text first
                                            await flush_pending_delta_data()
                                            await flush_pending_responses_data(
                                                force=True
                                            )

                                            # Build pending function_call output items for display
                                            pending_fc_items = []
                                            for tc in response_tool_calls:
                                                call_id = tc.get("id", "")
                                                func = tc.get("function", {})
                                                pending_fc_items.append(
                                                    {
                                                        "type": "function_call",
                                                        "id": call_id
                                                        or output_id("fc"),
                                                        "call_id": call_id,
                                                        "name": func.get("name", ""),
                                                        "arguments": func.get(
                                                            "arguments", "{}"
                                                        ),
                                                        "status": "in_progress",
                                                    }
                                                )
                                            pending_output = output + pending_fc_items
                                            await event_emitter(
                                                {
                                                    "type": "chat:completion",
                                                    "data": {
                                                        "content": serialize_output(
                                                            pending_output
                                                        ),
                                                    },
                                                }
                                            )

                                    image_urls = get_image_urls(
                                        delta.get("images", []), request, metadata, user
                                    )
                                    if image_urls:
                                        image_file_list = [
                                            {"type": "image", "url": url}
                                            for url in image_urls
                                        ]
                                        message_files = Chats.add_message_files_by_id_and_message_id(
                                            metadata["chat_id"],
                                            metadata["message_id"],
                                            image_file_list,
                                        )
                                        if message_files is None:
                                            message_files = image_file_list

                                        await event_emitter(
                                            {
                                                "type": "files",
                                                "data": {"files": message_files},
                                            }
                                        )

                                    value = delta.get("content")

                                    reasoning_content = (
                                        delta.get("reasoning_content")
                                        or delta.get("reasoning")
                                        or delta.get("thinking")
                                    )
                                    if reasoning_content:
                                        if (
                                            not output
                                            or output[-1].get("type") != "reasoning"
                                        ):
                                            reasoning_item = {
                                                "type": "reasoning",
                                                "id": output_id("r"),
                                                "status": "in_progress",
                                                "start_tag": "<think>",
                                                "end_tag": "</think>",
                                                "attributes": {
                                                    "type": "reasoning_content"
                                                },
                                                "content": [],
                                                "summary": None,
                                                "started_at": time.time(),
                                            }
                                            output.append(reasoning_item)
                                        else:
                                            reasoning_item = output[-1]

                                        # Append to reasoning content
                                        parts = reasoning_item.get("content", [])
                                        if (
                                            parts
                                            and parts[-1].get("type") == "output_text"
                                        ):
                                            parts[-1]["text"] += reasoning_content
                                        else:
                                            reasoning_item["content"] = [
                                                {
                                                    "type": "output_text",
                                                    "text": reasoning_content,
                                                }
                                            ]

                                        data = {"content": serialize_output(output)}

                                    if value:
                                        if (
                                            output
                                            and output[-1].get("type") == "reasoning"
                                            and output[-1]
                                            .get("attributes", {})
                                            .get("type")
                                            == "reasoning_content"
                                        ):
                                            reasoning_item = output[-1]
                                            reasoning_item["ended_at"] = time.time()
                                            reasoning_item["duration"] = int(
                                                reasoning_item["ended_at"]
                                                - reasoning_item["started_at"]
                                            )
                                            reasoning_item["status"] = "completed"

                                            output.append(
                                                {
                                                    "type": "message",
                                                    "id": output_id("msg"),
                                                    "status": "in_progress",
                                                    "role": "assistant",
                                                    "content": [
                                                        {
                                                            "type": "output_text",
                                                            "text": "",
                                                        }
                                                    ],
                                                }
                                            )

                                        if ENABLE_CHAT_RESPONSE_BASE64_IMAGE_URL_CONVERSION:
                                            value = convert_markdown_base64_images(
                                                request,
                                                value,
                                                {
                                                    "chat_id": metadata.get(
                                                        "chat_id", None
                                                    ),
                                                    "message_id": metadata.get(
                                                        "message_id", None
                                                    ),
                                                },
                                                user,
                                            )

                                        content = f"{content}{value}"

                                        # Check if we're inside a tag-based block
                                        # (reasoning, code_interpreter, or solution).
                                        # If so, append to the existing in-progress
                                        # item instead of creating a new message —
                                        # otherwise tag_output_handler re-detects the
                                        # start tag on every chunk and fragments the
                                        # output.
                                        last_item = output[-1] if output else None
                                        last_item_type = (
                                            last_item.get("type", "")
                                            if last_item
                                            else ""
                                        )
                                        inside_tag_block = (
                                            last_item is not None
                                            and last_item.get("status") == "in_progress"
                                            and last_item.get("attributes", {}).get(
                                                "type"
                                            )
                                            != "reasoning_content"
                                            and (
                                                last_item_type == "reasoning"
                                                or last_item_type
                                                == "open_webui:code_interpreter"
                                                or (
                                                    last_item_type == "message"
                                                    and last_item.get("_tag_type")
                                                    is not None
                                                )
                                            )
                                        )

                                        if inside_tag_block:
                                            # Append to the existing tag-based item
                                            if (
                                                last_item_type
                                                == "open_webui:code_interpreter"
                                            ):
                                                last_item["code"] = (
                                                    last_item.get("code", "") + value
                                                )
                                            elif last_item_type == "reasoning":
                                                parts = last_item.get("content", [])
                                                if (
                                                    parts
                                                    and parts[-1].get("type")
                                                    == "output_text"
                                                ):
                                                    parts[-1]["text"] += value
                                                else:
                                                    last_item["content"] = [
                                                        {
                                                            "type": "output_text",
                                                            "text": value,
                                                        }
                                                    ]
                                            else:
                                                # solution or other _tag_type message
                                                msg_parts = last_item.get("content", [])
                                                if (
                                                    msg_parts
                                                    and msg_parts[-1].get("type")
                                                    == "output_text"
                                                ):
                                                    msg_parts[-1]["text"] += value
                                                else:
                                                    last_item["content"] = [
                                                        {
                                                            "type": "output_text",
                                                            "text": value,
                                                        }
                                                    ]
                                        else:
                                            if (
                                                not output
                                                or output[-1].get("type") != "message"
                                            ):
                                                output.append(
                                                    {
                                                        "type": "message",
                                                        "id": output_id("msg"),
                                                        "status": "in_progress",
                                                        "role": "assistant",
                                                        "content": [
                                                            {
                                                                "type": "output_text",
                                                                "text": "",
                                                            }
                                                        ],
                                                    }
                                                )

                                            # Append value to last message item's text
                                            msg_parts = output[-1].get("content", [])
                                            if (
                                                msg_parts
                                                and msg_parts[-1].get("type")
                                                == "output_text"
                                            ):
                                                msg_parts[-1]["text"] += value
                                            else:
                                                output[-1]["content"] = [
                                                    {
                                                        "type": "output_text",
                                                        "text": value,
                                                    }
                                                ]

                                        if DETECT_REASONING_TAGS:
                                            output, _ = tag_output_handler(
                                                "reasoning",
                                                reasoning_tags,
                                                output,
                                            )

                                            output, _ = tag_output_handler(
                                                "solution",
                                                DEFAULT_SOLUTION_TAGS,
                                                output,
                                            )

                                        if DETECT_CODE_INTERPRETER:
                                            output, end = tag_output_handler(
                                                "code_interpreter",
                                                DEFAULT_CODE_INTERPRETER_TAGS,
                                                output,
                                            )

                                            if end:
                                                break

                                        if ENABLE_REALTIME_CHAT_SAVE:
                                            # Save message in the database
                                            Chats.upsert_message_to_chat_by_id_and_message_id(
                                                metadata["chat_id"],
                                                metadata["message_id"],
                                                {
                                                    "content": serialize_output(output),
                                                    "output": output,
                                                },
                                            )
                                        else:
                                            data = {
                                                "content": serialize_output(output),
                                            }

                                if delta:
                                    delta_count += 1
                                    last_delta_data = data
                                    if delta_count >= delta_chunk_size:
                                        await flush_pending_delta_data(delta_chunk_size)
                                else:
                                    await event_emitter(
                                        {
                                            "type": "chat:completion",
                                            "data": data,
                                        }
                                    )
                        except Exception as e:
                            done = "data: [DONE]" in line
                            if done:
                                pass
                            else:
                                log.debug(f"Error: {e}")
                                continue
                    await flush_pending_delta_data()
                    await flush_pending_responses_data(force=True)

                    if output:
                        # Clean up the last message item
                        if output[-1].get("type") == "message":
                            parts = output[-1].get("content", [])
                            if parts and parts[-1].get("type") == "output_text":
                                parts[-1]["text"] = parts[-1]["text"].strip()

                                if not parts[-1]["text"]:
                                    output.pop()

                                    if not output:
                                        output.append(
                                            {
                                                "type": "message",
                                                "id": output_id("msg"),
                                                "status": "in_progress",
                                                "role": "assistant",
                                                "content": [
                                                    {"type": "output_text", "text": ""}
                                                ],
                                            }
                                        )

                        if output[-1].get("type") == "reasoning":
                            reasoning_item = output[-1]
                            if reasoning_item.get("ended_at") is None:
                                reasoning_item["ended_at"] = time.time()
                                reasoning_item["duration"] = int(
                                    reasoning_item["ended_at"]
                                    - reasoning_item["started_at"]
                                )
                                reasoning_item["status"] = "completed"

                    response_segment = output[response_output_start_index:]
                    has_reasoning_in_segment = any(
                        item.get("type") == "reasoning" for item in response_segment
                    )
                    has_visible_output_in_segment = any(
                        response_item_has_visible_content(item)
                        for item in response_segment
                    )

                    if (
                        has_visible_output_in_segment
                        and not response_has_reasoning
                        and not has_reasoning_in_segment
                    ):
                        ended_at = first_visible_response_output_at or time.time()
                        started_at = response_started_at
                        output.insert(
                            response_output_start_index,
                            {
                                "type": "reasoning",
                                "id": output_id("r"),
                                "status": "completed",
                                "started_at": started_at,
                                "ended_at": ended_at,
                                "duration": max(0, int(ended_at - started_at)),
                                "summary": [],
                                "content": [],
                            },
                        )

                    resolved_response_tool_calls = list(response_tool_calls)
                    if not resolved_response_tool_calls:
                        # Responses API streams function calls as output items rather
                        # than Chat Completions-style delta.tool_calls. Convert the
                        # new function_call items from this response segment back into
                        # the OpenAI-compatible structure expected by the tool loop.
                        seen_call_ids = set()
                        for index, item in enumerate(response_segment):
                            if item.get("type") != "function_call":
                                continue

                            call_id = item.get("call_id") or item.get("id", "")
                            if not call_id or call_id in seen_call_ids:
                                continue

                            seen_call_ids.add(call_id)
                            resolved_response_tool_calls.append(
                                {
                                    "id": call_id,
                                    "index": index,
                                    "function": {
                                        "name": item.get("name", ""),
                                        "arguments": item.get("arguments", "{}"),
                                    },
                                }
                            )

                    if resolved_response_tool_calls:
                        tool_calls.append(
                            _split_tool_calls(resolved_response_tool_calls)
                        )

                    if response.background:
                        await response.background()

                await stream_body_handler(response, form_data)

                tool_call_retries = 0
                tool_call_sources = []  # Track citation sources from tool results
                all_tool_call_sources = []  # Accumulated sources across all iterations
                user_message = get_last_user_message(form_data["messages"])

                # Check if citations are enabled for this model
                citations_enabled = (
                    model.get("info", {}).get("meta", {}).get("capabilities") or {}
                ).get("citations", True)

                # Use the pre-RAG system content captured before the
                # initial file-source injection in process_chat_payload.
                # This ensures restore truly undoes the RAG template.
                original_system_content = metadata.get("system_prompt")
                if original_system_content is None:
                    original_system_message = get_system_message(form_data["messages"])
                    original_system_content = (
                        get_content_from_message(original_system_message)
                        if original_system_message
                        else None
                    )

                while (
                    len(tool_calls) > 0
                    and tool_call_retries < CHAT_RESPONSE_MAX_TOOL_CALL_RETRIES
                ):

                    tool_call_retries += 1

                    response_tool_calls = tool_calls.pop(0)

                    # Append function_call items for each tool call
                    for tc in response_tool_calls:
                        call_id = tc.get("id", "")
                        func = tc.get("function", {})
                        existing_item = next(
                            (
                                item
                                for item in output
                                if item.get("type") == "function_call"
                                and item.get("call_id") == call_id
                            ),
                            None,
                        )
                        if existing_item is not None:
                            existing_item["name"] = func.get("name", "")
                            existing_item["arguments"] = func.get(
                                "arguments", "{}"
                            )
                            existing_item["status"] = "in_progress"
                        else:
                            output.append(
                                {
                                    "type": "function_call",
                                    "id": call_id or output_id("fc"),
                                    "call_id": call_id,
                                    "name": func.get("name", ""),
                                    "arguments": func.get("arguments", "{}"),
                                    "status": "in_progress",
                                }
                            )

                    await event_emitter(
                        {
                            "type": "chat:completion",
                            "data": {
                                "content": serialize_output(output),
                                "output": output,
                            },
                        }
                    )

                    tools = metadata.get("tools", {})

                    results = []

                    for tool_call in response_tool_calls:
                        tool_call_id = tool_call.get("id", "")
                        tool_function_name = tool_call.get("function", {}).get(
                            "name", ""
                        )
                        tool_args = tool_call.get("function", {}).get("arguments", "{}")

                        tool_function_params = {}
                        if tool_args and tool_args.strip():
                            try:
                                # json.loads cannot be used because some models do not produce valid JSON
                                tool_function_params = ast.literal_eval(tool_args)
                            except Exception as e:
                                log.debug(e)
                                # Fallback to JSON parsing
                                try:
                                    tool_function_params = json.loads(tool_args)
                                except Exception as e:
                                    log.error(
                                        f"Error parsing tool call arguments: {tool_args}"
                                    )
                                    results.append(
                                        {
                                            "tool_call_id": tool_call_id,
                                            "content": f"Error: Tool call arguments could not be parsed. The model generated malformed or incomplete JSON for `{tool_function_name}`. Please try again.",
                                        }
                                    )
                                    continue

                        # Ensure arguments are valid JSON for downstream LLM integrations
                        log.debug(
                            f"Parsed args from {tool_args} to {tool_function_params}"
                        )
                        tool_call.setdefault("function", {})["arguments"] = json.dumps(
                            tool_function_params
                        )

                        tool_result = None
                        tool = None
                        tool_type = None
                        direct_tool = False

                        if tool_function_name in tools:
                            tool = tools[tool_function_name]
                            spec = tool.get("spec", {})

                            tool_type = tool.get("type", "")
                            direct_tool = tool.get("direct", False)

                            try:
                                allowed_params = (
                                    spec.get("parameters", {})
                                    .get("properties", {})
                                    .keys()
                                )

                                tool_function_params = {
                                    k: v
                                    for k, v in tool_function_params.items()
                                    if k in allowed_params
                                }
                                tool_function_params = normalize_terminal_tool_params(
                                    tool_function_name,
                                    tool_function_params,
                                    metadata,
                                )
                                tool_call.setdefault("function", {})[
                                    "arguments"
                                ] = json.dumps(
                                    tool_function_params, ensure_ascii=False
                                )

                                await emit_tool_execution_status(
                                    event_emitter,
                                    tool_function_name,
                                    tool_function_params,
                                    done=False,
                                )

                                if direct_tool:
                                    tool_result = await event_caller(
                                        {
                                            "type": "execute:tool",
                                            "data": {
                                                "id": str(uuid4()),
                                                "name": tool_function_name,
                                                "params": tool_function_params,
                                                "server": tool.get("server", {}),
                                                "session_id": metadata.get(
                                                    "session_id", None
                                                ),
                                            },
                                        }
                                    )

                                else:
                                    tool_function = get_updated_tool_function(
                                        function=tool["callable"],
                                        extra_params={
                                            "__messages__": form_data.get(
                                                "messages", []
                                            ),
                                            "__files__": metadata.get("files", []),
                                        },
                                    )

                                    tool_result = await tool_function(
                                        **tool_function_params
                                    )

                                tool_result = await wait_for_terminal_command_completion(
                                    tools,
                                    tool_function_name,
                                    tool_result,
                                )

                            except Exception as e:
                                tool_result = str(e)

                        raw_tool_result = tool_result

                        tool_result, tool_result_files, tool_result_embeds = (
                            await process_tool_result(
                                request,
                                tool_function_name,
                                tool_result,
                                tool_type,
                                direct_tool,
                                metadata,
                                user,
                                tool_info=tool,
                            )
                        )
                        tool_result_files = dedupe_tool_result_files(
                            tool_result_files
                        )

                        await emit_tool_execution_status(
                            event_emitter,
                            tool_function_name,
                            tool_function_params,
                            done=True,
                            tool_result=raw_tool_result,
                        )
                        await terminal_event_handler(
                            tool_function_name,
                            tool_function_params,
                            tool_result,
                            event_emitter,
                        )

                        if tool_result_files:
                            await event_emitter(
                                {
                                    "type": "files",
                                    "data": {"files": tool_result_files},
                                }
                            )

                        if tool_result_embeds:
                            await event_emitter(
                                {
                                    "type": "embeds",
                                    "data": {"embeds": tool_result_embeds},
                                }
                            )

                        # Extract citation sources from tool results
                        if (
                            citations_enabled
                            and tool_function_name
                            in [
                                "search_web",
                                "fetch_url",
                                "view_knowledge_file",
                                "query_knowledge_files",
                            ]
                            and tool_result
                        ):
                            try:
                                citation_sources = get_citation_source_from_tool_result(
                                    tool_name=tool_function_name,
                                    tool_params=tool_function_params,
                                    tool_result=tool_result,
                                    tool_id=tool.get("tool_id", "") if tool else "",
                                )
                                tool_call_sources.extend(citation_sources)
                            except Exception as e:
                                log.exception(f"Error extracting citation source: {e}")

                        results.append(
                            {
                                "tool_call_id": tool_call_id,
                                "content": str(tool_result) if tool_result else "",
                                **(
                                    {
                                        "files": dedupe_tool_result_files(
                                            tool_result_files
                                        )
                                    }
                                    if tool_result_files
                                    else {}
                                ),
                                **(
                                    {"embeds": tool_result_embeds}
                                    if tool_result_embeds
                                    else {}
                                ),
                            }
                        )

                    # Update function_call statuses and append function_call_output items
                    for tc in response_tool_calls:
                        call_id = tc.get("id", "")
                        # Mark function_call as completed
                        for item in output:
                            if (
                                item.get("type") == "function_call"
                                and item.get("call_id") == call_id
                            ):
                                item["status"] = "completed"
                                # Update arguments with parsed/sanitized version
                                item["arguments"] = tc.get("function", {}).get(
                                    "arguments", "{}"
                                )
                                break

                    for result in results:
                        result_files = dedupe_tool_result_files(
                            result.get("files") or []
                        )
                        output.append(
                            {
                                "type": "function_call_output",
                                "id": output_id("fco"),
                                "call_id": result.get("tool_call_id", ""),
                                "output": [
                                    {
                                        "type": "input_text",
                                        "text": result.get("content", ""),
                                    }
                                ],
                                "status": "completed",
                                **(
                                    {"files": result_files}
                                    if result_files
                                    else {}
                                ),
                                **(
                                    {"embeds": result.get("embeds")}
                                    if result.get("embeds")
                                    else {}
                                ),
                            }
                        )

                    # Append a new empty message item for the next response
                    output.append(
                        {
                            "type": "message",
                            "id": output_id("msg"),
                            "status": "in_progress",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": ""}],
                        }
                    )

                    # Emit citation sources to the frontend for display
                    if citations_enabled:
                        for source in tool_call_sources:
                            await event_emitter({"type": "source", "data": source})

                        # Apply tool source context to messages for the model.
                        # Restoring to pre-RAG original prevents duplicating
                        # the RAG template across file and tool sources.
                        all_tool_call_sources.extend(tool_call_sources)
                        if all_tool_call_sources and user_message:
                            # Restore pre-RAG message state before re-applying
                            # to prevent RAG template duplication.
                            original_user_message = (
                                metadata.get("user_prompt") or user_message
                            )
                            set_last_user_message_content(
                                original_user_message,
                                form_data["messages"],
                            )
                            replace_system_message_content(
                                original_system_content or "",
                                form_data["messages"],
                            )

                            # Build context: file sources with content,
                            # tool sources as citation markers only.
                            source_ids = {}
                            source_context = get_source_context(
                                metadata.get("sources", []), source_ids
                            ) + get_source_context(
                                all_tool_call_sources,
                                source_ids,
                                include_content=False,
                            )
                            source_context = source_context.strip()
                            if source_context:
                                rag_content = rag_template(
                                    request.app.state.config.RAG_TEMPLATE,
                                    source_context,
                                    user_message,
                                )
                                if RAG_SYSTEM_CONTEXT:
                                    form_data["messages"] = (
                                        add_or_update_system_message(
                                            rag_content,
                                            form_data["messages"],
                                            append=True,
                                        )
                                    )
                                else:
                                    form_data["messages"] = add_or_update_user_message(
                                        rag_content,
                                        form_data["messages"],
                                        append=False,
                                    )
                        tool_call_sources.clear()

                    await event_emitter(
                        {
                            "type": "chat:completion",
                            "data": {
                                "content": serialize_output(output),
                                "output": output,
                            },
                        }
                    )

                    if ENABLE_REALTIME_CHAT_SAVE:
                        Chats.upsert_message_to_chat_by_id_and_message_id(
                            metadata["chat_id"],
                            metadata["message_id"],
                            {
                                "content": serialize_output(output),
                                "output": output,
                            },
                        )

                    try:
                        new_form_data = {
                            **form_data,
                            "model": model_id,
                            "stream": True,
                            "messages": [
                                *form_data["messages"],
                                *convert_output_to_messages(output, raw=True),
                            ],
                        }

                        res = await generate_chat_completion(
                            request,
                            new_form_data,
                            user,
                            bypass_system_prompt=True,
                        )

                        if isinstance(res, StreamingResponse):
                            await stream_body_handler(res, new_form_data)
                        else:
                            break
                    except Exception as e:
                        log.debug(e)
                        break

                if DETECT_CODE_INTERPRETER:
                    MAX_RETRIES = 5
                    retries = 0

                    while (
                        output
                        and output[-1].get("type") == "open_webui:code_interpreter"
                        and retries < MAX_RETRIES
                    ):

                        await event_emitter(
                            {
                                "type": "chat:completion",
                                "data": {
                                    "content": serialize_output(output),
                                    "output": output,
                                },
                            }
                        )

                        retries += 1
                        log.debug(f"Attempt count: {retries}")

                        ci_item = output[-1]
                        ci_output = ""
                        try:
                            if ci_item.get("attributes", {}).get("type") == "code":
                                code = ci_item.get("code", "")
                                # Sanitize code (strips ANSI codes and markdown fences)
                                code = sanitize_code(code)

                                if CODE_INTERPRETER_BLOCKED_MODULES:
                                    blocking_code = textwrap.dedent(f"""
                                        import builtins
    
                                        BLOCKED_MODULES = {CODE_INTERPRETER_BLOCKED_MODULES}
    
                                        _real_import = builtins.__import__
                                        def restricted_import(name, globals=None, locals=None, fromlist=(), level=0):
                                            if name.split('.')[0] in BLOCKED_MODULES:
                                                importer_name = globals.get('__name__') if globals else None
                                                if importer_name == '__main__':
                                                    raise ImportError(
                                                        f"Direct import of module {{name}} is restricted."
                                                    )
                                            return _real_import(name, globals, locals, fromlist, level)
    
                                        builtins.__import__ = restricted_import
                                    """)
                                    code = blocking_code + "\n" + code

                                if (
                                    request.app.state.config.CODE_INTERPRETER_ENGINE
                                    == "pyodide"
                                ):
                                    ci_output = await event_caller(
                                        {
                                            "type": "execute:python",
                                            "data": {
                                                "id": str(uuid4()),
                                                "code": code,
                                                "session_id": metadata.get(
                                                    "session_id", None
                                                ),
                                                "files": metadata.get("files", []),
                                            },
                                        }
                                    )
                                elif (
                                    request.app.state.config.CODE_INTERPRETER_ENGINE
                                    == "jupyter"
                                ):
                                    ci_output = await execute_code_jupyter(
                                        request.app.state.config.CODE_INTERPRETER_JUPYTER_URL,
                                        code,
                                        (
                                            request.app.state.config.CODE_INTERPRETER_JUPYTER_AUTH_TOKEN
                                            if request.app.state.config.CODE_INTERPRETER_JUPYTER_AUTH
                                            == "token"
                                            else None
                                        ),
                                        (
                                            request.app.state.config.CODE_INTERPRETER_JUPYTER_AUTH_PASSWORD
                                            if request.app.state.config.CODE_INTERPRETER_JUPYTER_AUTH
                                            == "password"
                                            else None
                                        ),
                                        request.app.state.config.CODE_INTERPRETER_JUPYTER_TIMEOUT,
                                    )
                                else:
                                    ci_output = {
                                        "stdout": "Code interpreter engine not configured."
                                    }

                                log.debug(f"Code interpreter output: {ci_output}")

                                if isinstance(ci_output, dict):
                                    stdout = ci_output.get("stdout", "")

                                    if isinstance(stdout, str):
                                        stdoutLines = stdout.split("\n")
                                        for idx, line in enumerate(stdoutLines):

                                            if "data:image/png;base64" in line:
                                                image_url = get_image_url_from_base64(
                                                    request,
                                                    line,
                                                    metadata,
                                                    user,
                                                )
                                                if image_url:
                                                    stdoutLines[idx] = (
                                                        f"![Output Image]({image_url})"
                                                    )

                                        ci_output["stdout"] = "\n".join(stdoutLines)

                                    result = ci_output.get("result", "")

                                    if isinstance(result, str):
                                        resultLines = result.split("\n")
                                        for idx, line in enumerate(resultLines):
                                            if "data:image/png;base64" in line:
                                                image_url = get_image_url_from_base64(
                                                    request,
                                                    line,
                                                    metadata,
                                                    user,
                                                )
                                                resultLines[idx] = (
                                                    f"![Output Image]({image_url})"
                                                )
                                        ci_output["result"] = "\n".join(resultLines)
                        except Exception as e:
                            ci_output = str(e)

                        ci_item["output"] = ci_output
                        ci_item["status"] = "completed"

                        output.append(
                            {
                                "type": "message",
                                "id": output_id("msg"),
                                "status": "in_progress",
                                "role": "assistant",
                                "content": [{"type": "output_text", "text": ""}],
                            }
                        )

                        await event_emitter(
                            {
                                "type": "chat:completion",
                                "data": {
                                    "content": serialize_output(output),
                                    "output": output,
                                },
                            }
                        )

                        try:
                            new_form_data = {
                                **form_data,
                                "model": model_id,
                                "stream": True,
                                "messages": [
                                    *form_data["messages"],
                                    *convert_output_to_messages(output, raw=True),
                                ],
                            }

                            res = await generate_chat_completion(
                                request,
                                new_form_data,
                                user,
                                bypass_system_prompt=True,
                            )

                            if isinstance(res, StreamingResponse):
                                await stream_body_handler(res, new_form_data)
                            else:
                                break
                        except Exception as e:
                            log.debug(e)
                            break

                # Mark all in-progress items as completed
                for item in output:
                    if item.get("status") == "in_progress":
                        item["status"] = "completed"

                title = Chats.get_chat_title_by_id(metadata["chat_id"])
                data = {
                    "done": True,
                    "content": serialize_output(output),
                    "output": output,
                    "title": title,
                }

                Chats.upsert_message_to_chat_by_id_and_message_id(
                    metadata["chat_id"],
                    metadata["message_id"],
                    {
                        "content": serialize_output(output),
                        "output": output,
                        **({"usage": usage} if usage else {}),
                    },
                )

                # Send a webhook notification if the user is not active
                if not Users.is_user_active(user.id):
                    webhook_url = Users.get_user_webhook_url_by_id(user.id)
                    if webhook_url:
                        await post_webhook(
                            request.app.state.WEBUI_NAME,
                            webhook_url,
                            f"{title} - {request.app.state.config.WEBUI_URL}/c/{metadata['chat_id']}\n\n{content}",
                            {
                                "action": "chat",
                                "message": content,
                                "title": title,
                                "url": f"{request.app.state.config.WEBUI_URL}/c/{metadata['chat_id']}",
                            },
                        )

                await event_emitter(
                    {
                        "type": "chat:completion",
                        "data": data,
                    }
                )

                await background_tasks_handler(ctx)
            except asyncio.CancelledError:
                log.warning("Task was cancelled!")
                await event_emitter({"type": "chat:tasks:cancel"})

                if not ENABLE_REALTIME_CHAT_SAVE:
                    # Save message in the database
                    Chats.upsert_message_to_chat_by_id_and_message_id(
                        metadata["chat_id"],
                        metadata["message_id"],
                        {
                            "content": serialize_output(output),
                            "output": output,
                        },
                    )

            if response.background is not None:
                await response.background()

        return await response_handler(response, events)

    else:
        # Fallback to the original response
        async def stream_wrapper(original_generator, events):
            def wrap_item(item):
                return f"data: {item}\n\n"

            for event in events:
                event, _ = await process_filter_functions(
                    request=request,
                    filter_functions=filter_functions,
                    filter_type="stream",
                    form_data=event,
                    extra_params=extra_params,
                )

                if event:
                    yield wrap_item(json.dumps(event))

            async for data in original_generator:
                data, _ = await process_filter_functions(
                    request=request,
                    filter_functions=filter_functions,
                    filter_type="stream",
                    form_data=data,
                    extra_params=extra_params,
                )

                if data:
                    yield data

        return StreamingResponse(
            stream_wrapper(response.body_iterator, events),
            headers=dict(response.headers),
            background=response.background,
        )


async def process_chat_response(response, ctx):
    # Non-streaming response
    if not isinstance(response, StreamingResponse):
        return await non_streaming_chat_response_handler(response, ctx)

    # Non standard response
    if not any(
        content_type in response.headers["Content-Type"]
        for content_type in ["text/event-stream", "application/x-ndjson"]
    ):
        return response

    # Streaming response
    return await streaming_chat_response_handler(response, ctx)
