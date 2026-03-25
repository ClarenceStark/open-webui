from pathlib import Path
from types import SimpleNamespace

from codex_app_server import LocalImageInput, TextInput

from open_webui.codex import handler


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
