import asyncio
from pathlib import Path
from types import SimpleNamespace

from codex_app_server import LocalImageInput, TextInput

from open_webui.codex import handler
from open_webui.codex.instructions import build_codex_browser_runtime_prompt
from open_webui.utils.misc import API_KEY_NON_DISCLOSURE_SYSTEM_PROMPT


def test_prepare_codex_turn_input_mounts_files_and_uses_local_image_inputs(
    tmp_path, monkeypatch
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    image_source = tmp_path / "source-image.png"
    image_source.write_bytes(b"png-data")

    doc_source = tmp_path / "source-notes.pdf"
    doc_source.write_bytes(b"pdf-data")

    file_map = {
        "img-file": SimpleNamespace(
            path=str(image_source),
            filename="image.png",
            meta={"content_type": "image/png"},
        ),
        "doc-file": SimpleNamespace(
            path=str(doc_source),
            filename="notes.pdf",
            meta={"content_type": "application/pdf"},
        ),
    }

    monkeypatch.setattr(
        handler.Files,
        "get_file_by_id_and_user_id",
        lambda file_id, user_id: file_map.get(file_id),
    )
    monkeypatch.setattr(
        handler.Files,
        "get_file_by_id",
        lambda file_id: file_map.get(file_id),
    )
    monkeypatch.setattr(handler.Storage, "get_file", lambda file_path: file_path)

    input_items = handler._prepare_codex_turn_input(
        workspace=workspace,
        message_id="message-1",
        content=[
            {"type": "text", "text": "请解释图片并参考 PDF"},
            {"type": "image_url", "image_url": {"url": "img-file"}},
        ],
        attached_files=[
            {"id": "img-file", "content_type": "image/png", "name": "image.png"},
            {"id": "doc-file", "content_type": "application/pdf", "name": "notes.pdf"},
        ],
        user=SimpleNamespace(id="user-1", role="user"),
    )

    assert isinstance(input_items[0], TextInput)
    assert "Workspace attachment directory" in input_items[0].text
    assert "image.png" in input_items[0].text
    assert "notes.pdf" in input_items[0].text

    local_images = [item for item in input_items if isinstance(item, LocalImageInput)]
    assert len(local_images) == 1

    mounted_image = Path(local_images[0].path)
    mounted_doc = workspace / "inputs" / "message-1" / "doc-file__notes.pdf"
    assert mounted_image.exists()
    assert mounted_doc.exists()
    assert mounted_image.read_bytes() == b"png-data"
    assert mounted_doc.read_bytes() == b"pdf-data"


def test_prepare_codex_turn_input_decodes_inline_images(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    input_items = handler._prepare_codex_turn_input(
        workspace=workspace,
        message_id="message-inline",
        content=[
            {"type": "text", "text": "描述这张图"},
            {
                "type": "image_url",
                "image_url": {
                    "url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO7Z0ioAAAAASUVORK5CYII="
                },
            },
        ],
        attached_files=[],
        user=SimpleNamespace(id="user-1", role="user"),
    )

    assert isinstance(input_items[0], TextInput)

    local_images = [item for item in input_items if isinstance(item, LocalImageInput)]
    assert len(local_images) == 1
    assert Path(local_images[0].path).exists()


def test_codex_image_generation_prompt_mentions_azure_skill_and_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    prompt = handler._build_codex_image_generation_prompt(workspace)

    assert "Azure OpenAI image generation" in prompt
    assert "image-generation" in prompt
    assert str(workspace / "outputs" / "image-generation") in prompt
    assert "render it inline" in prompt


def test_codex_turn_does_not_send_internal_runtime_prompts_as_user_text(
    tmp_path, monkeypatch
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    captured = {}

    class FakeTurnHandle:
        id = "turn-1"

        async def stream(self):
            if False:
                yield None

    class FakeThread:
        async def turn(self, turn_input, **kwargs):
            captured["turn_input"] = turn_input
            captured["kwargs"] = kwargs
            return FakeTurnHandle()

    class FakeRequestBridge:
        def configure(self, **kwargs):
            pass

        def clear(self):
            pass

    fake_session = SimpleNamespace(
        workspace=workspace,
        thread=FakeThread(),
        thread_id=None,
        last_active=0,
        turn_lock=asyncio.Lock(),
        request_bridge=FakeRequestBridge(),
        current_turn=None,
        current_turn_id=None,
    )

    class FakeManager:
        async def get_or_create(self, chat_id, codex_thread_id, user_email=None):
            return fake_session

    async def noop(*args, **kwargs):
        return None

    async def load_message(chat_id, message_id):
        return {"id": message_id, "content": "hi", "files": []}

    monkeypatch.setattr(
        handler.Chats,
        "get_chat_by_id",
        lambda chat_id: SimpleNamespace(chat={}),
    )
    monkeypatch.setattr(handler, "get_event_emitter", lambda metadata: noop)
    monkeypatch.setattr(handler, "get_event_call", lambda metadata: noop)
    monkeypatch.setattr(handler, "_load_chat_message", load_message)
    monkeypatch.setattr(handler, "_persist_codex_turn_state", noop)
    monkeypatch.setattr(handler, "_emit_to_message", noop)
    monkeypatch.setattr(handler, "_upload_codex_workspace_artifacts", noop)

    async def run_completion():
        request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    codex_manager=FakeManager(),
                    main_loop=asyncio.get_running_loop(),
                )
            )
        )
        return await handler.codex_chat_completion(
            request=request,
            form_data={
                "model": "gpt-5.5",
                "messages": [{"role": "user", "content": "hi"}],
            },
            user=SimpleNamespace(id="user-1", role="user"),
            metadata={"chat_id": "chat-1", "message_id": "message-1"},
        )

    response = asyncio.run(run_completion())

    assert response.status_code == 200
    texts = [
        item.text for item in captured["turn_input"] if isinstance(item, TextInput)
    ]
    assert texts == ["hi"]
    assert API_KEY_NON_DISCLOSURE_SYSTEM_PROMPT not in "\n\n".join(texts)
    assert build_codex_browser_runtime_prompt(workspace) not in "\n\n".join(texts)


def test_upload_codex_workspace_artifacts_does_not_emit_duplicate_files_event_for_final_output(
    tmp_path, monkeypatch
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    screenshot = workspace / "bizgeneval-landing.png"
    screenshot.write_bytes(b"png-data")

    uploaded_file = {
        "id": "file-1",
        "url": "/api/v1/files/file-1/content",
        "name": screenshot.name,
        "type": "image",
        "content_type": "image/png",
    }

    async def upload_artifact_to_chat(*args, **kwargs):
        return uploaded_file

    monkeypatch.setattr(handler, "upload_artifact_to_chat", upload_artifact_to_chat)
    monkeypatch.setattr(
        handler.Chats,
        "get_message_by_id_and_message_id",
        lambda chat_id, message_id: {
            "content": f"截图已保存：[landing page]({screenshot})"
        },
    )

    emitted = []

    async def event_emitter(event):
        emitted.append(event)

    uploaded = asyncio.run(
        handler._upload_codex_workspace_artifacts(
            request=SimpleNamespace(),
            event_emitter=event_emitter,
            workspace=workspace,
            before_snapshot={},
            message_id="message-1",
            metadata={"chat_id": "chat-1"},
            user=SimpleNamespace(id="user-1"),
        )
    )

    assert uploaded == [{**uploaded_file, "is_final_output": True}]
    assert [event["type"] for event in emitted] == ["chat:message:update"]
    assert emitted[0]["data"]["message"]["files"] == uploaded
