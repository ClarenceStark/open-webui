import asyncio
import logging
import time
from typing import Any

from codex_app_server import TextInput
from codex_app_server.generated.v2_all import (
    AgentMessageThreadItem,
    AskForApproval,
    CommandExecutionThreadItem,
    ErrorNotification,
    FileChangeThreadItem,
    ItemCompletedNotification,
    ItemStartedNotification,
    PlanThreadItem,
    PlanDeltaNotification,
    SandboxPolicy,
    ThreadItem,
    ThreadTokenUsageUpdatedNotification,
    TurnCompletedNotification,
    TurnPlanUpdatedNotification,
)
from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse

from open_webui.codex.config import get_model_override, get_sandbox_policy
from open_webui.models.chats import Chats
from open_webui.socket.main import get_event_call, get_event_emitter

log = logging.getLogger(__name__)
_APPROVAL_NEVER = AskForApproval("never")


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


def describe_item(item: ThreadItem | Any) -> str:
    item = _unwrap_item(item)

    if isinstance(item, AgentMessageThreadItem):
        return "生成回复"

    if isinstance(item, PlanThreadItem):
        return "更新计划"

    if isinstance(item, CommandExecutionThreadItem):
        command = item.command.strip()
        if len(command) > 120:
            command = f"{command[:117]}..."
        return f"执行命令: {command}"

    if isinstance(item, FileChangeThreadItem):
        count = len(item.changes or [])
        return f"修改文件 ({count})"

    item_type = getattr(item, "type", "codex")
    return f"Codex: {item_type}"


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


async def translate_and_emit(
    notification,
    event_emitter,
    full_response: list[str],
    session,
) -> None:
    method = notification.method
    payload = notification.payload

    if method == "item/agentMessage/delta":
        full_response.append(payload.delta)
        await event_emitter({"type": "message", "data": {"content": payload.delta}})
        return

    if method == "item/started" and isinstance(payload, ItemStartedNotification):
        await event_emitter(
            {
                "type": "status",
                "data": {
                    "action": "codex",
                    "description": describe_item(payload.item),
                    "done": False,
                },
            }
        )
        return

    if method == "item/completed" and isinstance(payload, ItemCompletedNotification):
        item = _unwrap_item(payload.item)
        await event_emitter(
            {
                "type": "status",
                "data": {
                    "action": "codex",
                    "description": describe_item(item),
                    "done": True,
                },
            }
        )

        if isinstance(item, AgentMessageThreadItem) and item.text:
            current = "".join(full_response)
            if not current:
                full_response[:] = [item.text]
                await event_emitter({"type": "replace", "data": {"content": item.text}})
            elif item.text.startswith(current):
                suffix = item.text[len(current) :]
                if suffix:
                    full_response.append(suffix)
                    await event_emitter({"type": "message", "data": {"content": suffix}})
            elif item.text != current:
                full_response[:] = [item.text]
                await event_emitter({"type": "replace", "data": {"content": item.text}})
        return

    if method == "item/commandExecution/outputDelta":
        await event_emitter(
            {
                "type": "status",
                "data": {
                    "action": "command_output",
                    "description": payload.delta,
                    "done": False,
                },
            }
        )
        return

    if method == "item/fileChange/outputDelta":
        await event_emitter(
            {
                "type": "status",
                "data": {
                    "action": "file_change",
                    "description": payload.delta,
                    "done": False,
                },
            }
        )
        return

    if method == "item/plan/delta" and isinstance(payload, PlanDeltaNotification):
        await event_emitter(
            {
                "type": "status",
                "data": {
                    "action": "planning",
                    "description": payload.delta,
                    "done": False,
                },
            }
        )
        return

    if method == "turn/plan/updated" and isinstance(payload, TurnPlanUpdatedNotification):
        await event_emitter(
            {
                "type": "status",
                "data": {
                    "action": "planning",
                    "description": _build_plan_summary(payload),
                    "done": False,
                },
            }
        )
        return

    if method == "thread/tokenUsage/updated" and isinstance(
        payload, ThreadTokenUsageUpdatedNotification
    ):
        session.token_usage = payload.model_dump(mode="json")
        return

    if method == "error" and isinstance(payload, ErrorNotification):
        await event_emitter(
            {
                "type": "chat:message:error",
                "data": {"error": {"content": payload.error.message}},
            }
        )
        return

    if method == "turn/completed" and isinstance(payload, TurnCompletedNotification):
        if payload.turn.error:
            await event_emitter(
                {
                    "type": "chat:message:error",
                    "data": {"error": {"content": payload.turn.error.message}},
                }
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

    full_response: list[str] = []
    turn_handle = None

    async with session.turn_lock:
        try:
            turn_handle = await session.thread.turn(
                TextInput(user_message),
                approval_policy=_APPROVAL_NEVER,
                model=get_model_override(form_data.get("model")),
                sandbox_policy=SandboxPolicy.model_validate(get_sandbox_policy()),
            )
            session.current_turn = turn_handle
            session.current_turn_id = turn_handle.id

            async for notification in turn_handle.stream():
                await translate_and_emit(
                    notification,
                    event_emitter,
                    full_response,
                    session,
                )
        finally:
            session.current_turn = None
            session.current_turn_id = None
            session.last_active = time.monotonic()
            session.request_bridge.clear()

    return JSONResponse({"status": True})
