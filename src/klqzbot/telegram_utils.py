from __future__ import annotations

import re
from io import BytesIO
from typing import Any

from telethon import Button, TelegramClient
from telethon.helpers import add_surrogate, del_surrogate
from telethon.tl import functions, types

MIRROR_URL_REWRITES: dict[str, str] = {
    "https://t.me/UT666": "https://t.me/ghsjsvu",
}
MIRROR_TEXT_LINK_REWRITES: dict[str, str] = {
    "28u包月 60次": "https://oxcv.tronlink73.top/1900.html?xmhw3l-pty28-7706786383",
}


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


def rewrite_mirror_links(message_text: str, entities: Any) -> tuple[str, Any]:
    text = str(message_text or "")
    if MIRROR_URL_REWRITES:
        for old_url, new_url in MIRROR_URL_REWRITES.items():
            text = text.replace(old_url, new_url)

    if not entities:
        return text, entities

    surrogate_text = add_surrogate(text)
    for entity in entities:
        current_url = str(getattr(entity, "url", "") or "").strip()
        if current_url and current_url in MIRROR_URL_REWRITES:
            entity.url = MIRROR_URL_REWRITES[current_url]
            continue

        offset = int(getattr(entity, "offset", 0) or 0)
        length = int(getattr(entity, "length", 0) or 0)
        if length <= 0:
            continue
        entity_text = del_surrogate(surrogate_text[offset : offset + length]).strip()
        rewritten_url = MIRROR_TEXT_LINK_REWRITES.get(entity_text)
        if rewritten_url and current_url:
            entity.url = rewritten_url
    return text, entities


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


def parse_button_lines(message_text: str) -> list[list[dict[str, str]]]:
    rows: list[list[dict[str, str]]] = []
    for raw_line in str(message_text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        row: list[dict[str, str]] = []
        for part in re.split(r"\s*&&\s*", line):
            segment = part.strip()
            if not segment:
                continue
            matched = re.match(r"^(?P<label>[^|｜]+?)\s*[|｜]\s*(?P<url>https?://\S+)$", segment, re.I)
            if not matched:
                row = []
                break
            row.append(
                {
                    "text": matched.group("label").strip() or "按钮",
                    "url": matched.group("url").strip(),
                }
            )
        if row:
            rows.append(row)
    return rows


def build_url_buttons(button_specs: list[Any]) -> Any:
    rows: list[list[Any]] = []
    for raw_row in button_specs:
        items = raw_row if isinstance(raw_row, list) else [raw_row]
        row: list[Any] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text", "") or "").strip()
            url = str(item.get("url", "") or "").strip()
            if not text or not url:
                continue
            row.append(Button.url(text, url))
        if row:
            rows.append(row)
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
