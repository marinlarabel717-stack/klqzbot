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
            if isinstance(
                native_button,
                (
                    types.KeyboardButtonUrl,
                    types.KeyboardButtonWebView,
                    types.KeyboardButtonSimpleWebView,
                    types.KeyboardButtonUrlAuth,
                ),
            ):
                native_url = str(getattr(native_button, "url", "") or "").strip()
                if native_url:
                    cloned_row.append(Button.url(text, native_url))
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
            if isinstance(native_button, types.KeyboardButtonUserProfile):
                user_id = getattr(native_button, "user_id", None)
                if user_id is not None:
                    cloned_row.append(Button.url(text, f"tg://user?id={user_id}"))
                    continue
            if isinstance(native_button, types.KeyboardButtonBuy):
                cloned_row.append(Button.buy(text))
                continue
            if isinstance(native_button, types.KeyboardButtonGame):
                cloned_row.append(Button.game(text))
                continue
            if data is not None:
                cloned_row.append(Button.inline(text, data))
                continue
            cloned_row.append(Button.text(text))
        if cloned_row:
            cloned_rows.append(cloned_row)
    return cloned_rows or None


def extract_configured_buttons(message_text: str, *, enabled: bool) -> tuple[str, Any]:
    text = str(message_text or "")
    if not text:
        return "", None

    body_lines: list[str] = []
    button_rows: list[list[Any]] = []
    saw_button_syntax = False

    for raw_line in text.splitlines():
        line = raw_line.strip()
        matched = re.match(r"^(?P<label>[^|｜]+?)\s*[|｜]\s*(?P<url>https?://\S+)$", line, re.I)
        if matched:
            saw_button_syntax = True
            if enabled:
                label = matched.group("label").strip() or "按钮"
                url = matched.group("url").strip()
                button_rows.append([Button.url(label, url)])
                continue
        body_lines.append(raw_line)

    if not enabled or not saw_button_syntax:
        return text, None

    trimmed_body = "\n".join(body_lines).strip()
    return trimmed_body, button_rows or None


def parse_button_lines(message_text: str) -> list[dict[str, str]]:
    buttons: list[dict[str, str]] = []
    for raw_line in str(message_text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        matched = re.match(r"^(?P<label>[^|｜]+?)\s*[|｜]\s*(?P<url>https?://\S+)$", line, re.I)
        if not matched:
            continue
        buttons.append(
            {
                "text": matched.group("label").strip() or "按钮",
                "url": matched.group("url").strip(),
            }
        )
    return buttons


def build_url_buttons(button_specs: list[dict[str, str]]) -> Any:
    rows: list[list[Any]] = []
    for item in button_specs:
        text = str(item.get("text", "") or "").strip()
        url = str(item.get("url", "") or "").strip()
        if not text or not url:
            continue
        rows.append([Button.url(text, url)])
    return rows or None


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
