import asyncio
import base64
import logging
import mimetypes
import os
import re
import shlex
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from codex_app_server import ImageInput, LocalImageInput, TextInput
from codex_app_server.generated.v2_all import (
    AgentMessageThreadItem,
    CommandExecutionThreadItem,
    ErrorNotification,
    FileChangeThreadItem,
    ItemCompletedNotification,
    ItemStartedNotification,
    PlanThreadItem,
    PlanDeltaNotification,
    ThreadItem,
    ThreadTokenUsageUpdatedNotification,
    TurnCompletedNotification,
    TurnPlanUpdatedNotification,
)
from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse

from open_webui.codex.config import get_model_override
from open_webui.models.chats import Chats
from open_webui.models.files import Files
from open_webui.socket.main import get_event_call, get_event_emitter
from open_webui.storage.provider import Storage
from open_webui.utils.middleware import (
    background_tasks_handler,
    upload_artifact_to_chat,
)

log = logging.getLogger(__name__)

_MAX_WORKSPACE_ARTIFACT_UPLOADS = 10
_IGNORED_WORKSPACE_ARTIFACT_PARTS = {
    ".git",
    ".pytest_cache",
    "__pycache__",
    ".mypy_cache",
}

_FRIENDLY_TYPE_MAP = {
    "userMessage": "理解问题",
    "reasoning": "深度思考",
    "tool_call": "调用工具",
    "tool_result": "处理结果",
}

_TRIVIAL_COMMANDS = {
    "cd",
    "echo",
    "export",
    "printf",
    "pwd",
    "set",
    "source",
    "test",
    "true",
}


def _unwrap_item(item: ThreadItem | Any) -> Any:
    return item.root if isinstance(item, ThreadItem) else item


def _coerce_user_message(content: Any) -> str:
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            elif item.get("type") == "input_text":
                parts.append(str(item.get("text", "")))
        return "\n".join(part for part in parts if part)

    return str(content or "")


def _collect_text_segments(content: Any) -> list[str]:
    if isinstance(content, str):
        return [content]

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type in {"text", "input_text"}:
                text = str(item.get("text", ""))
                if text:
                    parts.append(text)
        return parts

    if content is None:
        return []

    return [str(content)]


def _extract_image_references(content: Any) -> list[str]:
    references: list[str] = []
    if not isinstance(content, list):
        return references

    for item in content:
        if not isinstance(item, dict) or item.get("type") != "image_url":
            continue

        image_url = item.get("image_url", {})
        if isinstance(image_url, dict):
            url = image_url.get("url")
        else:
            url = image_url

        if isinstance(url, str) and url:
            references.append(url)

    return references


def _merge_file_items(*groups: Any) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    for group in groups:
        if not isinstance(group, list):
            continue

        for item in group:
            if not isinstance(item, dict):
                continue

            key = str(
                item.get("id")
                or item.get("url")
                or f"{item.get('name', '')}:{item.get('content_type', '')}"
            )
            if not key or key in seen_keys:
                continue

            seen_keys.add(key)
            merged.append(dict(item))

    return merged


def _sanitize_workspace_component(value: Any, fallback: str) -> str:
    raw = str(value or fallback)
    sanitized = re.sub(r"[^A-Za-z0-9._-]", "_", raw).strip("._")
    return sanitized or fallback


def _sanitize_workspace_filename(value: Any, fallback: str) -> str:
    raw_name = os.path.basename(str(value or "")).strip()
    if not raw_name:
        raw_name = fallback

    sanitized = re.sub(r"[^A-Za-z0-9._-]", "_", raw_name)
    sanitized = sanitized.lstrip(".")
    return sanitized or fallback


def _decode_data_url(
    data_url: str,
) -> tuple[bytes, str]:
    header, encoded = data_url.split(",", 1)
    mime_match = re.match(r"data:(?P<mime>[^;]+);base64$", header, re.IGNORECASE)
    if not mime_match:
        raise ValueError("Unsupported data URL format")

    mime_type = mime_match.group("mime") or "application/octet-stream"
    return base64.b64decode(encoded), mime_type


def _build_workspace_attachment_prompt(
    mounted_files: list[dict[str, Any]],
    target_dir: Path,
) -> str:
    lines = [
        "The user's uploaded Open WebUI files have been copied into your current workspace.",
        f"Workspace attachment directory: {target_dir}",
        "Use these exact local paths when reading or modifying the uploaded files:",
    ]
    for mounted_file in mounted_files:
        lines.append(f"- {mounted_file['name']}: {mounted_file['path']}")
    lines.append(
        "Prefer these exact paths over guessing /mnt/data, /tmp, /Users/... or other host filesystem paths."
    )
    lines.append(
        "Treat files under this attachment directory as user inputs; write derived outputs to a separate location when possible."
    )
    return "\n".join(lines)


def _iter_workspace_files(workspace: Path):
    for path in workspace.rglob("*"):
        if not path.is_file():
            continue

        relative_parts = path.relative_to(workspace).parts
        if any(
            part in _IGNORED_WORKSPACE_ARTIFACT_PARTS or part.startswith(".")
            for part in relative_parts
        ):
            continue

        yield path


def _snapshot_workspace_files(workspace: Path) -> dict[str, tuple[int, int]]:
    snapshot: dict[str, tuple[int, int]] = {}
    if not workspace.exists():
        return snapshot

    for path in _iter_workspace_files(workspace):
        try:
            stat = path.stat()
        except FileNotFoundError:
            continue
        snapshot[path.relative_to(workspace).as_posix()] = (stat.st_mtime_ns, stat.st_size)

    return snapshot


def _collect_workspace_artifacts(
    workspace: Path,
    before_snapshot: dict[str, tuple[int, int]],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []

    for path in _iter_workspace_files(workspace):
        try:
            stat = path.stat()
        except FileNotFoundError:
            continue

        relative_path = path.relative_to(workspace).as_posix()
        signature = (stat.st_mtime_ns, stat.st_size)
        if before_snapshot.get(relative_path) == signature:
            continue

        candidates.append(
            {
                "path": str(path),
                "name": path.name,
                "content_type": mimetypes.guess_type(path.name)[0]
                or "application/octet-stream",
                "artifact_origin": "codex_workspace",
                "is_final_output": True,
                "_mtime_ns": stat.st_mtime_ns,
            }
        )

    candidates.sort(key=lambda item: item.get("_mtime_ns", 0), reverse=True)
    selected = candidates[:_MAX_WORKSPACE_ARTIFACT_UPLOADS]
    omitted_count = max(0, len(candidates) - len(selected))

    for index, artifact in enumerate(selected):
        artifact.pop("_mtime_ns", None)
        if omitted_count and index == 0:
            artifact["omitted_artifact_count"] = omitted_count

    return selected


def _normalize_local_path(path: str) -> str:
    return os.path.normpath(path).replace("\\", "/")


def _extract_referenced_local_paths(content: str) -> set[str]:
    if not isinstance(content, str) or not content:
        return set()

    referenced: set[str] = set()

    for match in re.finditer(r"\[[^\]]+\]\((/[^)\s]+)\)", content):
        href = match.group(1)
        if href.startswith("/Users/") or href.startswith("/tmp/"):
            referenced.add(_normalize_local_path(href))

    for match in re.finditer(r"(?<!\()(/(?:Users|tmp)/[^\s)]+)", content):
        referenced.add(_normalize_local_path(match.group(1)))

    return referenced


def _rewrite_content_file_links(
    content: str,
    link_map: dict[str, str],
) -> str:
    if not isinstance(content, str) or not content or not link_map:
        return content

    rewritten = content
    normalized_map = {
        _normalize_local_path(local_path): remote_url
        for local_path, remote_url in link_map.items()
        if remote_url
    }

    def replace_markdown_link(match: re.Match) -> str:
        label = match.group(1)
        href = _normalize_local_path(match.group(2))
        remote_url = normalized_map.get(href)
        if not remote_url:
            return match.group(0)
        return f"[{label}]({remote_url})"

    rewritten = re.sub(
        r"\[([^\]]+)\]\((/[^)\s]+)\)",
        replace_markdown_link,
        rewritten,
    )

    for local_path, remote_url in sorted(
        normalized_map.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        rewritten = rewritten.replace(local_path, remote_url)

    return rewritten


async def _upload_codex_workspace_artifacts(
    request: Request,
    event_emitter,
    workspace: Path,
    before_snapshot: dict[str, tuple[int, int]],
    message_id: str,
    metadata: dict[str, Any],
    user,
) -> list[dict[str, Any]]:
    artifacts = await asyncio.to_thread(
        _collect_workspace_artifacts,
        workspace,
        before_snapshot,
    )
    if not artifacts:
        return []

    uploaded_files: list[dict[str, Any]] = []
    uploaded_pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    upload_metadata = {**metadata, "message_id": message_id}

    for artifact in artifacts:
        try:
            uploaded_artifact = await upload_artifact_to_chat(
                request,
                artifact,
                upload_metadata,
                user,
            )
        except Exception:
            log.exception("Failed to upload Codex workspace artifact: %s", artifact)
            continue

        if uploaded_artifact:
            uploaded_files.append(uploaded_artifact)
            uploaded_pairs.append((artifact, uploaded_artifact))

    if uploaded_pairs:
        message = await asyncio.to_thread(
            Chats.get_message_by_id_and_message_id,
            metadata.get("chat_id"),
            message_id,
        ) or {}
        content = message.get("content", "")
        referenced_local_paths = _extract_referenced_local_paths(content)

        link_map: dict[str, str] = {}
        for artifact, uploaded_artifact in uploaded_pairs:
            artifact_path = str(artifact.get("path") or "")
            uploaded_url = str(uploaded_artifact.get("url") or "")
            if artifact_path and uploaded_url:
                link_map[artifact_path] = uploaded_url

        rewritten_content = _rewrite_content_file_links(content, link_map)

        final_files = []
        fallback_files = []
        for artifact, uploaded_artifact in uploaded_pairs:
            local_path = _normalize_local_path(str(artifact.get("path") or ""))
            file_copy = {**uploaded_artifact}
            if local_path and local_path in referenced_local_paths:
                file_copy["is_final_output"] = True
                final_files.append(file_copy)
            else:
                file_copy["is_final_output"] = False
                fallback_files.append(file_copy)

        if final_files:
            uploaded_files = [*final_files, *fallback_files]

        if rewritten_content != content or final_files:
            await _emit_to_message(
                event_emitter,
                message_id,
                "chat:message:update",
                {
                    "message": {
                        **({"content": rewritten_content} if rewritten_content != content else {}),
                        **({"files": uploaded_files} if final_files else {}),
                    }
                },
            )

    if uploaded_files:
        await _emit_to_message(
            event_emitter,
            message_id,
            "files",
            {"files": uploaded_files},
        )

    return uploaded_files


def _prepare_codex_turn_input(
    workspace: Path,
    message_id: str,
    content: Any,
    attached_files: list[dict[str, Any]],
    user,
) -> list[Any]:
    text_segments = _collect_text_segments(content)
    image_references = _extract_image_references(content)

    attachment_dir = workspace / "inputs" / _sanitize_workspace_component(
        message_id, "message"
    )
    mounted_files: list[dict[str, Any]] = []
    copied_file_paths: dict[str, str] = {}

    if attached_files:
        attachment_dir.mkdir(parents=True, exist_ok=True)

    for file_item in attached_files:
        file_id = str(file_item.get("id") or "").strip()
        if not file_id:
            continue

        db_file = Files.get_file_by_id_and_user_id(file_id, user.id)
        if db_file is None and getattr(user, "role", None) == "admin":
            db_file = Files.get_file_by_id(file_id)
        if db_file is None or not getattr(db_file, "path", None):
            continue

        filename = (
            getattr(db_file, "filename", None)
            or file_item.get("name")
            or file_item.get("filename")
            or file_id
        )
        content_type = (
            file_item.get("content_type")
            or (getattr(db_file, "meta", None) or {}).get("content_type")
            or mimetypes.guess_type(filename)[0]
            or "application/octet-stream"
        )
        source_path = Path(Storage.get_file(db_file.path))
        destination = attachment_dir / (
            f"{_sanitize_workspace_component(file_id, 'file')}"
            f"__{_sanitize_workspace_filename(filename, 'file')}"
        )
        if source_path.resolve() != destination.resolve():
            shutil.copy2(source_path, destination)

        mounted_file = {
            "id": file_id,
            "name": filename,
            "path": str(destination),
            "content_type": content_type,
        }
        mounted_files.append(mounted_file)
        copied_file_paths[file_id] = str(destination)

    local_image_paths: list[str] = []
    remote_image_urls: list[str] = []

    if image_references and not attachment_dir.exists():
        attachment_dir.mkdir(parents=True, exist_ok=True)

    for index, reference in enumerate(image_references, start=1):
        if reference in copied_file_paths:
            local_image_paths.append(copied_file_paths[reference])
            continue

        if reference.startswith("data:image/"):
            image_bytes, mime_type = _decode_data_url(reference)
            extension = mimetypes.guess_extension(mime_type) or ".png"
            destination = attachment_dir / (
                f"inline_image_{index}{extension}"
            )
            destination.write_bytes(image_bytes)
            local_image_paths.append(str(destination))
            mounted_files.append(
                {
                    "id": f"inline-image-{index}",
                    "name": destination.name,
                    "path": str(destination),
                    "content_type": mime_type,
                }
            )
            continue

        if reference.startswith(("http://", "https://")):
            remote_image_urls.append(reference)
            continue

        if os.path.isabs(reference) and Path(reference).exists():
            local_image_paths.append(reference)

    if mounted_files:
        prompt = _build_workspace_attachment_prompt(mounted_files, attachment_dir)
        text_segments.insert(0, prompt)

    input_items: list[Any] = []
    text_content = "\n\n".join(segment for segment in text_segments if segment).strip()
    if text_content:
        input_items.append(TextInput(text_content))

    seen_local_images: set[str] = set()
    for path in local_image_paths:
        if path in seen_local_images:
            continue
        seen_local_images.add(path)
        input_items.append(LocalImageInput(path))

    seen_remote_images: set[str] = set()
    for url in remote_image_urls:
        if url in seen_remote_images:
            continue
        seen_remote_images.add(url)
        input_items.append(ImageInput(url))

    if not input_items:
        input_items.append(TextInput(_coerce_user_message(content)))

    return input_items


def _unwrap_shell_command(command: str) -> str:
    try:
        parts = shlex.split(command)
    except ValueError:
        return command.strip()

    if not parts:
        return command.strip()

    executable = os.path.basename(parts[0]).lower()
    if executable in {"bash", "sh", "zsh"}:
        for idx, arg in enumerate(parts[1:], start=1):
            if arg in {"-c", "-lc"} and idx + 1 < len(parts):
                return str(parts[idx + 1]).strip()

    return command.strip()


def _split_command_segments(command: str) -> list[str]:
    normalized = _unwrap_shell_command(command).replace("\n", " && ")
    return [segment.strip() for segment in re.split(r"\s*(?:&&|\|\||;|\|)\s*", normalized) if segment.strip()]


def _tokenize_command(segment: str) -> list[str]:
    try:
        tokens = shlex.split(segment)
    except ValueError:
        tokens = segment.split()

    if not tokens:
        return []

    if tokens[0] == "env":
        tokens = tokens[1:]

    while tokens and "=" in tokens[0] and not tokens[0].startswith(("/", ".")):
        key, _, value = tokens[0].partition("=")
        if key and value:
            tokens = tokens[1:]
            continue
        break

    if len(tokens) >= 2 and tokens[0] == "command" and tokens[1] == "-v":
        return ["command", "-v"]

    return tokens


def _describe_command_segment(segment: str) -> tuple[str, int]:
    tokens = _tokenize_command(segment)
    if not tokens:
        return "执行系统命令", 0

    executable = os.path.basename(tokens[0]).lower()
    args = [token.lower() for token in tokens[1:]]

    if executable in _TRIVIAL_COMMANDS:
        return "获取当前路径", 0 if executable == "pwd" else -1

    if executable in {"rg", "grep"}:
        return "搜索文件内容", 90

    if executable in {"find", "fd", "ls", "tree"}:
        return "浏览文件目录", 70

    if executable in {"cat", "head", "tail", "bat", "less", "more"}:
        return "读取文件", 80

    if executable == "sed" and "-n" in args:
        return "读取文件", 80

    if executable in {"python", "python3", "node", "nodejs", "tsx", "ts-node"}:
        return "运行脚本", 85

    if executable in {"pip", "pip3", "npm", "pnpm", "yarn", "bun", "uv", "poetry"}:
        install_like = {"install", "add", "ci", "sync"}
        if install_like.intersection(args):
            return "安装依赖", 85
        return "运行项目任务", 75

    if executable == "git":
        return "执行 Git 操作", 75

    if executable in {"libreoffice", "soffice", "convert", "magick"}:
        return "转换文件格式", 80

    if executable == "pwd":
        return "获取当前路径", 10

    if executable == "command" and len(tokens) >= 2 and tokens[1] == "-v":
        return "检查命令可用性", 20

    return "执行系统命令", 30


def _describe_command(command: str) -> str:
    best_description = "执行系统命令"
    best_score = -1

    for segment in _split_command_segments(command):
        description, score = _describe_command_segment(segment)
        if score > best_score:
            best_description = description
            best_score = score

    return best_description


def describe_item(item: ThreadItem | Any) -> str:
    item = _unwrap_item(item)

    if isinstance(item, AgentMessageThreadItem):
        return "生成回复"

    if isinstance(item, PlanThreadItem):
        return "更新计划"

    if isinstance(item, CommandExecutionThreadItem):
        return _describe_command(item.command)

    if isinstance(item, FileChangeThreadItem):
        count = len(item.changes or [])
        return f"修改文件 ({count})"

    item_type = getattr(item, "type", "codex")
    return _FRIENDLY_TYPE_MAP.get(item_type, "处理中")


def _build_plan_summary(notification: TurnPlanUpdatedNotification) -> str:
    steps = []
    for step in notification.plan:
        prefix = {
            "completed": "[x]",
            "in_progress": "[>]",
            "pending": "[ ]",
        }.get(step.status.value, "[ ]")
        steps.append(f"{prefix} {step.step}")
    if notification.explanation:
        return f"{notification.explanation}\n" + "\n".join(steps)
    return "\n".join(steps)


async def _persist_codex_thread_id(chat_id: str, thread_id: str) -> None:
    chat = await asyncio.to_thread(Chats.get_chat_by_id, chat_id)
    if chat is None:
        return

    payload = dict(chat.chat or {})
    if payload.get("codex_thread_id") == thread_id:
        return

    payload["codex_thread_id"] = thread_id
    await asyncio.to_thread(Chats.update_chat_by_id, chat_id, payload)


async def _load_chat_message(chat_id: str, message_id: str | None) -> dict[str, Any]:
    if not chat_id or not message_id:
        return {}

    return await asyncio.to_thread(
        Chats.get_message_by_id_and_message_id,
        chat_id,
        message_id,
    ) or {}


def _build_codex_message(base_message: dict[str, Any], message_id: str, parent_id: str | None) -> dict[str, Any]:
    message = {
        "id": message_id,
        "parentId": parent_id,
        "childrenIds": [],
        "role": "assistant",
        "content": "",
        "done": False,
        "timestamp": int(time.time()),
    }

    for key in ("model", "modelName", "modelIdx", "selectedModelId", "arena"):
        if key in base_message:
            message[key] = base_message[key]

    return message


def _normalize_unix_seconds(value: Any) -> int | None:
    if value is None:
        return None

    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        return None

    if not numeric_value or numeric_value <= 0:
        return None

    if numeric_value > 1_000_000_000_000:
        numeric_value /= 1000

    return int(numeric_value)


async def _ensure_agent_message(
    item_id: str,
    state: dict[str, Any],
    event_emitter,
) -> str:
    mapped_id = state["item_message_ids"].get(item_id)
    if mapped_id:
        return mapped_id

    if state["last_message_id"] is None:
        mapped_id = state["root_message_id"]
        state["item_message_ids"][item_id] = mapped_id
        state["last_message_id"] = mapped_id
        return mapped_id

    mapped_id = str(uuid.uuid4())
    message = _build_codex_message(
        state["root_message"],
        mapped_id,
        state["last_message_id"],
    )
    state["message_timestamps"][mapped_id] = message["timestamp"]
    state["item_message_ids"][item_id] = mapped_id
    state["last_message_id"] = mapped_id

    await event_emitter(
        {
            "type": "chat:message:create",
            "message_id": mapped_id,
            "data": {"message": message},
        }
    )

    return mapped_id


async def _emit_to_message(
    event_emitter,
    message_id: str | None,
    event_type: str,
    data: dict[str, Any],
) -> None:
    payload: dict[str, Any] = {"type": event_type, "data": data}
    if message_id:
        payload["message_id"] = message_id
    await event_emitter(payload)


async def translate_and_emit(
    notification,
    event_emitter,
    message_state: dict[str, Any],
    session,
) -> None:
    method = notification.method
    payload = notification.payload

    if method == "item/agentMessage/delta":
        message_id = await _ensure_agent_message(payload.item_id, message_state, event_emitter)
        current = message_state["item_contents"].get(payload.item_id, "")
        message_state["item_contents"][payload.item_id] = current + payload.delta
        await _emit_to_message(
            event_emitter,
            message_id,
            "message",
            {"content": payload.delta},
        )
        return

    if method == "item/started" and isinstance(payload, ItemStartedNotification):
        item = _unwrap_item(payload.item)
        started_at = int(time.time())
        message_state["item_started_at"][item.id] = started_at
        message_id = (
            await _ensure_agent_message(item.id, message_state, event_emitter)
            if isinstance(item, AgentMessageThreadItem)
            else (message_state["last_message_id"] or message_state["root_message_id"])
        )
        await event_emitter(
            {
                "type": "status",
                "message_id": message_id,
                "data": {
                    "action": "codex",
                    "description": describe_item(item),
                    "done": False,
                    "started_at": started_at,
                },
            }
        )
        return

    if method == "item/completed" and isinstance(payload, ItemCompletedNotification):
        item = _unwrap_item(payload.item)
        ended_at = int(time.time())
        started_at = message_state["item_started_at"].get(item.id) or ended_at
        duration = max(0, ended_at - started_at)
        message_id = (
            await _ensure_agent_message(item.id, message_state, event_emitter)
            if isinstance(item, AgentMessageThreadItem)
            else (message_state["last_message_id"] or message_state["root_message_id"])
        )
        await event_emitter(
            {
                "type": "status",
                "message_id": message_id,
                "data": {
                    "action": "codex",
                    "description": describe_item(item),
                    "done": True,
                    "started_at": started_at,
                    "ended_at": ended_at,
                    "duration": duration,
                },
            }
        )

        if isinstance(item, AgentMessageThreadItem) and item.text:
            current = message_state["item_contents"].get(item.id, "")
            if not current:
                message_state["item_contents"][item.id] = item.text
                await _emit_to_message(
                    event_emitter,
                    message_id,
                    "replace",
                    {"content": item.text},
                )
            elif item.text.startswith(current):
                suffix = item.text[len(current) :]
                if suffix:
                    message_state["item_contents"][item.id] = item.text
                    await _emit_to_message(
                        event_emitter,
                        message_id,
                        "message",
                        {"content": suffix},
                    )
            elif item.text != current:
                message_state["item_contents"][item.id] = item.text
                await _emit_to_message(
                    event_emitter,
                    message_id,
                    "replace",
                    {"content": item.text},
                )

            await _emit_to_message(
                event_emitter,
                message_id,
                "chat:message:update",
                {
                    "message": {
                        "done": True,
                        "pseudoDoneDurationSeconds": max(
                            1,
                            ended_at
                            - (
                                message_state["message_timestamps"].get(message_id)
                                or _normalize_unix_seconds(message_state["root_message"].get("timestamp"))
                                or ended_at
                            ),
                        ),
                    }
                },
            )
        return

    if method == "item/commandExecution/outputDelta":
        return

    if method == "item/fileChange/outputDelta":
        return

    if method == "item/plan/delta" and isinstance(payload, PlanDeltaNotification):
        await _emit_to_message(
            event_emitter,
            message_state["last_message_id"] or message_state["root_message_id"],
            "status",
            {
                "action": "planning",
                "description": payload.delta,
                "done": False,
            },
        )
        return

    if method == "turn/plan/updated" and isinstance(payload, TurnPlanUpdatedNotification):
        await _emit_to_message(
            event_emitter,
            message_state["last_message_id"] or message_state["root_message_id"],
            "status",
            {
                "action": "planning",
                "description": _build_plan_summary(payload),
                "done": False,
            },
        )
        return

    if method == "thread/tokenUsage/updated" and isinstance(
        payload, ThreadTokenUsageUpdatedNotification
    ):
        session.token_usage = payload.model_dump(mode="json")
        return

    if method == "error" and isinstance(payload, ErrorNotification):
        await _emit_to_message(
            event_emitter,
            message_state["last_message_id"] or message_state["root_message_id"],
            "chat:message:error",
            {
                "error": {"content": payload.error.message},
            },
        )
        return

    if method == "turn/completed" and isinstance(payload, TurnCompletedNotification):
        if payload.turn.error:
            await _emit_to_message(
                event_emitter,
                message_state["last_message_id"] or message_state["root_message_id"],
                "chat:message:error",
                {
                    "error": {"content": payload.turn.error.message},
                },
            )


async def codex_chat_completion(
    request: Request,
    form_data: dict,
    user,
    metadata: dict,
    tasks: dict | None = None,
) -> JSONResponse:
    chat_id = metadata.get("chat_id")
    message_id = metadata.get("message_id")

    if not chat_id or not message_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Codex chat requires chat_id and message_id metadata",
        )

    manager = request.app.state.codex_manager
    chat = await asyncio.to_thread(Chats.get_chat_by_id, chat_id)
    codex_thread_id = None
    if chat is not None:
        codex_thread_id = (chat.chat or {}).get("codex_thread_id")

    session = await manager.get_or_create(
        chat_id,
        codex_thread_id,
        user_email=getattr(user, "email", None),
    )
    session.last_active = time.monotonic()

    if session.thread_id != codex_thread_id:
        await _persist_codex_thread_id(chat_id, session.thread_id)

    messages = form_data.get("messages", [])
    if not messages:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Codex chat requires at least one message",
        )

    event_emitter = get_event_emitter(metadata)
    event_caller = get_event_call(metadata)
    session.request_bridge.configure(
        loop=request.app.state.main_loop,
        event_caller=event_caller,
    )

    root_message = await _load_chat_message(chat_id, message_id)
    parent_message = metadata.get("parent_message") or {}
    parent_message_id = metadata.get("parent_message_id") or parent_message.get("id")
    stored_parent_message = await _load_chat_message(chat_id, parent_message_id)

    turn_input = await asyncio.to_thread(
        _prepare_codex_turn_input,
        session.workspace,
        parent_message_id or message_id,
        messages[-1].get("content"),
        _merge_file_items(
            parent_message.get("files"),
            stored_parent_message.get("files"),
            root_message.get("files"),
            metadata.get("files"),
        ),
        user,
    )
    message_state = {
        "root_message_id": message_id,
        "root_message": root_message,
        "last_message_id": None,
        "item_message_ids": {},
        "item_contents": {},
        "item_started_at": {},
        "message_timestamps": {
            message_id: _normalize_unix_seconds(root_message.get("timestamp")) or int(time.time())
        },
    }
    turn_handle = None
    workspace_snapshot = await asyncio.to_thread(
        _snapshot_workspace_files,
        session.workspace,
    )

    async with session.turn_lock:
        try:
            turn_handle = await session.thread.turn(
                turn_input,
                model=get_model_override(form_data.get("model")),
            )
            session.current_turn = turn_handle
            session.current_turn_id = turn_handle.id

            async for notification in turn_handle.stream():
                await translate_and_emit(
                    notification,
                    event_emitter,
                    message_state,
                    session,
                )

            final_message_id = (
                message_state["last_message_id"] or message_state["root_message_id"]
            )
            await _upload_codex_workspace_artifacts(
                request,
                event_emitter,
                session.workspace,
                workspace_snapshot,
                final_message_id,
                metadata,
                user,
            )
            if tasks:
                task_metadata = {**metadata, "message_id": final_message_id}
                await background_tasks_handler(
                    {
                        "request": request,
                        "form_data": form_data,
                        "user": user,
                        "metadata": task_metadata,
                        "tasks": tasks,
                        "event_emitter": event_emitter,
                    }
                )
        finally:
            session.current_turn = None
            session.current_turn_id = None
            session.last_active = time.monotonic()
            session.request_bridge.clear()

    return JSONResponse({"status": True})
