import asyncio
import logging
import os
import re
import shlex
import time
import uuid
from typing import Any

from codex_app_server import TextInput
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
from open_webui.socket.main import get_event_call, get_event_emitter

log = logging.getLogger(__name__)

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

    session = await manager.get_or_create(chat_id, codex_thread_id)
    session.last_active = time.monotonic()

    if session.thread_id != codex_thread_id:
        await _persist_codex_thread_id(chat_id, session.thread_id)

    messages = form_data.get("messages", [])
    if not messages:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Codex chat requires at least one message",
        )

    user_message = _coerce_user_message(messages[-1].get("content"))
    event_emitter = get_event_emitter(metadata)
    event_caller = get_event_call(metadata)
    session.request_bridge.configure(
        loop=request.app.state.main_loop,
        event_caller=event_caller,
    )

    root_message = await asyncio.to_thread(
        Chats.get_message_by_id_and_message_id,
        chat_id,
        message_id,
    ) or {}
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

    async with session.turn_lock:
        try:
            turn_handle = await session.thread.turn(
                TextInput(user_message),
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
        finally:
            session.current_turn = None
            session.current_turn_id = None
            session.last_active = time.monotonic()
            session.request_bridge.clear()

    return JSONResponse({"status": True})
