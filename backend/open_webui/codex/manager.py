import asyncio
import logging
import re
import threading
import time
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from codex_app_server import AsyncCodex, AsyncThread, AsyncTurnHandle
from codex_app_server.client import AppServerConfig
from codex_app_server.generated.v2_all import AskForApproval, SandboxMode

from open_webui.codex.config import (
    CODEX_BIN_PATH,
    CODEX_MODEL,
    CODEX_MODEL_PROVIDER,
    CODEX_SESSION_IDLE_TIMEOUT,
    get_thread_config,
    get_workspace_path,
)

log = logging.getLogger(__name__)

_DEFAULT_INPUT_TIMEOUT_SECONDS = 60 * 60
_APPROVAL_NEVER = AskForApproval("never")


def _format_question_message(question: dict[str, Any]) -> str:
    lines = [question.get("question", "")]

    options = question.get("options") or []
    if options:
        lines.append("")
        lines.append("可选项：")
        for index, option in enumerate(options, start=1):
            label = str(option.get("label", "")).strip()
            description = str(option.get("description", "")).strip()
            line = f"{index}. {label}"
            if description:
                line += f" - {description}"
            lines.append(line)

    if question.get("isSecret"):
        lines.append("")
        lines.append("此输入将按敏感信息处理。")

    return "\n".join(line for line in lines if line is not None)


def _normalize_answer(question: dict[str, Any], response: Any) -> list[str]:
    options = question.get("options") or []

    if response in (None, False):
        return []

    if response is True:
        return [options[0]["label"]] if options else []

    if isinstance(response, dict):
        response = response.get("value") or response.get("answer") or ""

    raw_text = str(response).strip()
    if not raw_text:
        return [options[0]["label"]] if options else []

    parts = [
        part.strip()
        for part in re.split(r"[\n,]+", raw_text)
        if part and part.strip()
    ]

    if not options:
        return parts or [raw_text]

    normalized: list[str] = []
    for part in parts or [raw_text]:
        if part.isdigit():
            index = int(part) - 1
            if 0 <= index < len(options):
                normalized.append(str(options[index].get("label", "")).strip())
                continue
        normalized.append(part)

    return [answer for answer in normalized if answer]


class CodexInteractionBridge:
    def __init__(self) -> None:
        self._event_caller: Callable[[dict[str, Any]], Awaitable[Any]] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()

    def configure(
        self,
        *,
        loop: asyncio.AbstractEventLoop,
        event_caller: Callable[[dict[str, Any]], Awaitable[Any]] | None,
    ) -> None:
        with self._lock:
            self._loop = loop
            self._event_caller = event_caller

    def clear(self) -> None:
        with self._lock:
            self._event_caller = None

    def handle_request(
        self,
        method: str,
        params: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if method == "item/commandExecution/requestApproval":
            return {"decision": "accept"}

        if method == "item/fileChange/requestApproval":
            return {"decision": "accept"}

        if method == "item/tool/requestUserInput":
            return self._handle_request_user_input(params or {})

        return {}

    def _handle_request_user_input(self, params: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            event_caller = self._event_caller
            loop = self._loop

        questions = params.get("questions") or []
        if not isinstance(questions, list):
            questions = []

        if event_caller is None or loop is None:
            log.warning("Codex request_user_input received without an active websocket caller")
            return {
                "answers": {
                    str(question.get("id", f"question_{index}")): {
                        "answers": _normalize_answer(question, True),
                    }
                    for index, question in enumerate(questions)
                }
            }

        answers: dict[str, dict[str, list[str]]] = {}
        for index, question in enumerate(questions):
            question_id = str(question.get("id", f"question_{index}"))
            header = str(question.get("header", "需要输入")).strip() or "需要输入"
            prompt = {
                "type": "input",
                "data": {
                    "title": header,
                    "message": _format_question_message(question),
                    "placeholder": (
                        "输入选项序号、标签或自定义答案；多个答案用逗号分隔"
                        if question.get("options")
                        else "输入答案"
                    ),
                    "value": "",
                    "type": "password" if question.get("isSecret") else "text",
                },
            }
            future = asyncio.run_coroutine_threadsafe(event_caller(prompt), loop)
            try:
                response = future.result(timeout=_DEFAULT_INPUT_TIMEOUT_SECONDS)
            except FutureTimeoutError:
                log.warning("Timed out waiting for request_user_input answer")
                response = None
            except Exception:
                log.exception("Failed to collect request_user_input answer from websocket")
                response = None

            answers[question_id] = {
                "answers": _normalize_answer(question, response),
            }

        return {"answers": answers}


@dataclass(slots=True)
class CodexSession:
    codex: AsyncCodex
    thread: AsyncThread
    thread_id: str
    chat_id: str
    workspace: Path
    request_bridge: CodexInteractionBridge
    last_active: float = field(default_factory=time.monotonic)
    current_turn: AsyncTurnHandle | None = None
    current_turn_id: str | None = None
    token_usage: dict[str, Any] | None = None
    turn_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class CodexSessionManager:
    def __init__(self) -> None:
        self._sessions: dict[str, CodexSession] = {}
        self._lock = asyncio.Lock()

    async def get_or_create(
        self,
        chat_id: str,
        codex_thread_id: str | None = None,
    ) -> CodexSession:
        async with self._lock:
            existing = self._sessions.get(chat_id)
            if existing is not None:
                existing.last_active = time.monotonic()
                return existing

            workspace = get_workspace_path(chat_id)
            workspace.mkdir(parents=True, exist_ok=True)

            bridge = CodexInteractionBridge()
            codex = AsyncCodex(
                config=AppServerConfig(
                    codex_bin=CODEX_BIN_PATH,
                    cwd=str(workspace),
                )
            )
            codex._client._sync._approval_handler = bridge.handle_request

            try:
                thread = await self._resume_or_start_thread(
                    codex,
                    codex_thread_id=codex_thread_id,
                    workspace=workspace,
                )
            except Exception:
                await codex.close()
                raise

            session = CodexSession(
                codex=codex,
                thread=thread,
                thread_id=thread.id,
                chat_id=chat_id,
                workspace=workspace,
                request_bridge=bridge,
            )
            self._sessions[chat_id] = session
            return session

    async def close_session(self, chat_id: str) -> None:
        async with self._lock:
            session = self._sessions.pop(chat_id, None)

        if session is not None:
            session.request_bridge.clear()
            await session.codex.close()

    async def close_all(self) -> None:
        async with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()

        for session in sessions:
            session.request_bridge.clear()
            await session.codex.close()

    async def interrupt(self, chat_id: str) -> bool:
        async with self._lock:
            session = self._sessions.get(chat_id)

        if session is None or session.current_turn_id is None:
            return False

        controller = AsyncCodex(
            config=AppServerConfig(
                codex_bin=CODEX_BIN_PATH,
                cwd=str(session.workspace),
            )
        )
        try:
            await controller.thread_resume(
                session.thread_id,
                approval_policy=_APPROVAL_NEVER,
                sandbox=SandboxMode.workspace_write,
                cwd=str(session.workspace),
                config=get_thread_config(),
                model=CODEX_MODEL,
                model_provider=CODEX_MODEL_PROVIDER,
            )
            await controller._client.turn_interrupt(
                session.thread_id,
                session.current_turn_id,
            )
            session.last_active = time.monotonic()
            return True
        finally:
            await controller.close()

    async def periodic_reap(self, timeout: int = CODEX_SESSION_IDLE_TIMEOUT) -> None:
        sleep_interval = max(5, min(timeout, 60))
        try:
            while True:
                await asyncio.sleep(sleep_interval)
                now = time.monotonic()
                stale: list[CodexSession] = []

                async with self._lock:
                    for chat_id, session in list(self._sessions.items()):
                        if session.current_turn_id is not None:
                            continue
                        if now - session.last_active <= timeout:
                            continue
                        stale.append(self._sessions.pop(chat_id))

                for session in stale:
                    session.request_bridge.clear()
                    await session.codex.close()
        except asyncio.CancelledError:
            raise

    async def _resume_or_start_thread(
        self,
        codex: AsyncCodex,
        *,
        codex_thread_id: str | None,
        workspace: Path,
    ) -> AsyncThread:
        kwargs = {
            "approval_policy": _APPROVAL_NEVER,
            "sandbox": SandboxMode.workspace_write,
            "cwd": str(workspace),
            "config": get_thread_config(),
            "model": CODEX_MODEL,
            "model_provider": CODEX_MODEL_PROVIDER,
        }

        if codex_thread_id:
            try:
                return await codex.thread_resume(codex_thread_id, **kwargs)
            except Exception:
                log.exception("Failed to resume Codex thread %s; starting a new one", codex_thread_id)

        return await codex.thread_start(**kwargs)
