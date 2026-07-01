from __future__ import annotations

import re
from io import BytesIO
from typing import Any

from telethon import Button, TelegramClient
from telethon.tl import functions, types


def extract_invite_hash(raw: str) -> str:
    matched = re.search(r"(?:https?://)?t\.me/(?:joinchat/|\+)([^/?#]+)", raw, re.I)
    return matched.group(1).strip() if matched else ""


def normalize_public_ref(raw: str) -> str:
    value = raw.strip()
    if not value:
        return ""
    if value.startswith("@"):
        return value
    public_matched = re.search(r"(?:https?://)?t\.me/([A-Za-z0-9_]{3,})", value, re.I)
    if public_matched:
        return f"@{public_matched.group(1).strip()}"
    return value


async def resolve_entity(client: TelegramClient, raw: str) -> Any:
    invite_hash = extract_invite_hash(raw)
    if invite_hash:
        invite = await client(functions.messages.CheckChatInviteRequest(hash=invite_hash))
        if isinstance(invite, types.ChatInviteAlready):
            return invite.chat
        imported = await client(functions.messages.ImportChatInviteRequest(hash=invite_hash))
        chats = list(getattr(imported, "chats", None) or [])
        if chats:
            return chats[0]
        raise RuntimeError(f"无法加入/解析邀请链接: {raw}")
    return await client.get_entity(normalize_public_ref(raw))


def clone_buttons(message_buttons: Any) -> Any:
    rows = list(message_buttons or [])
    if not rows:
        return None

    cloned_rows: list[list[Any]] = []
    for row in rows:
        cloned_row: list[Any] = []
        for button in row:
            text = str(getattr(button, "text", "") or "").strip() or "按钮"
            url = getattr(button, "url", None)
            data = getattr(button, "data", None)
            native_button = getattr(button, "button", None)

            if url:
                cloned_row.append(Button.url(text, url))
                continue
            if isinstance(native_button, types.KeyboardButtonSwitchInline):
                cloned_row.append(
                    Button.switch_inline(
                        text,
                        query=str(getattr(native_button, "query", "") or ""),
                        same_peer=bool(getattr(native_button, "same_peer", False)),
                    )
                )
                continue
            if data is not None:
                cloned_row.append(Button.inline(text, data))
                continue
            cloned_row.append(Button.text(text))
        if cloned_row:
            cloned_rows.append(cloned_row)
    return cloned_rows or None


def infer_media_file(message: Any, media_bytes: bytes | None) -> BytesIO | None:
    if media_bytes is None:
        return None
    buffer = BytesIO(media_bytes)
    name = (
        getattr(getattr(message, "file", None), "name", None)
        or f"message_{getattr(message, 'id', 'unknown')}"
    )
    if "." not in name:
        if getattr(message, "photo", None):
            name = f"{name}.jpg"
        elif getattr(message, "video", None):
            name = f"{name}.mp4"
        else:
            name = f"{name}.bin"
    buffer.name = name
    buffer.seek(0)
    return buffer
