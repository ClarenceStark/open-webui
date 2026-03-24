import base64
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status

from open_webui.codex.config import get_workspace_path
from open_webui.models.chats import Chats
from open_webui.utils.auth import get_verified_user

router = APIRouter()


def _get_owned_chat(chat_id: str, user):
    chat = Chats.get_chat_by_id(chat_id)
    if chat is None or (chat.user_id != user.id and user.role != "admin"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat not found",
        )
    return chat


def _resolve_workspace_path(chat_id: str, path: str | None = None) -> Path:
    workspace = get_workspace_path(chat_id).resolve()
    target = workspace if not path else (workspace / path).resolve()

    if target != workspace and workspace not in target.parents:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid workspace path",
        )

    return target


@router.post("/interrupt/{chat_id}")
async def interrupt_codex_turn(
    request: Request,
    chat_id: str,
    user=Depends(get_verified_user),
):
    _get_owned_chat(chat_id, user)
    interrupted = await request.app.state.codex_manager.interrupt(chat_id)
    return {"status": interrupted}


@router.get("/workspace/{chat_id}")
async def list_codex_workspace(chat_id: str, user=Depends(get_verified_user)):
    _get_owned_chat(chat_id, user)
    workspace = _resolve_workspace_path(chat_id)
    workspace.mkdir(parents=True, exist_ok=True)

    entries = []
    for entry in sorted(workspace.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
        stat = entry.stat()
        entries.append(
            {
                "name": entry.name,
                "path": entry.relative_to(workspace).as_posix(),
                "is_dir": entry.is_dir(),
                "size": stat.st_size,
                "modified_at": int(stat.st_mtime),
            }
        )

    return {
        "chat_id": chat_id,
        "workspace": str(workspace),
        "entries": entries,
    }


@router.get("/workspace/{chat_id}/{path:path}")
async def read_codex_workspace_file(
    chat_id: str,
    path: str,
    user=Depends(get_verified_user),
):
    _get_owned_chat(chat_id, user)
    target = _resolve_workspace_path(chat_id, path)

    if not target.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace file not found",
        )

    if target.is_dir():
        entries = []
        for entry in sorted(target.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
            entries.append(
                {
                    "name": entry.name,
                    "path": entry.relative_to(_resolve_workspace_path(chat_id)).as_posix(),
                    "is_dir": entry.is_dir(),
                }
            )
        return {
            "path": path,
            "is_dir": True,
            "entries": entries,
        }

    data = target.read_bytes()
    try:
        content = data.decode("utf-8")
        return {
            "path": path,
            "is_dir": False,
            "encoding": "utf-8",
            "content": content,
        }
    except UnicodeDecodeError:
        return {
            "path": path,
            "is_dir": False,
            "encoding": "base64",
            "content": base64.b64encode(data).decode("ascii"),
        }
