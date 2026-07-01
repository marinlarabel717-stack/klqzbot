from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from telethon import TelegramClient, events

from .config import load_settings
from .telegram_utils import clone_buttons, infer_media_file, resolve_entity


def log_line(event: str, **payload: Any) -> None:
    print(json.dumps({"event": event, **payload}, ensure_ascii=False), flush=True)


async def create_mirror_client(args: argparse.Namespace) -> tuple[TelegramClient, str]:
    settings = load_settings()
    bot_token = str(getattr(args, "bot_token", "") or settings.bot_token or "").strip()
    session_raw = str(getattr(args, "session", "") or "").strip()
    session_path = Path(session_raw).expanduser().resolve() if session_raw else (Path.cwd() / "data" / "mirror-bot.session")
    session_path.parent.mkdir(parents=True, exist_ok=True)

    client = TelegramClient(str(session_path), settings.api_id, settings.api_hash)
    if bot_token:
        await client.start(bot_token=bot_token)
        return client, "bot"

    if not session_raw:
        raise RuntimeError("未提供 --session，且 BOT_TOKEN 也未配置")
    await client.connect()
    if not await client.is_user_authorized():
        raise RuntimeError("当前 session 未授权")
    return client, "user"


async def mirror_message(
    client: TelegramClient,
    target_entity: Any,
    message: Any,
) -> Any:
    if getattr(message, "action", None) is not None:
        return None

    buttons = clone_buttons(getattr(message, "buttons", None))
    text = str(getattr(message, "message", None) or "")
    entities = getattr(message, "entities", None)
    has_media = getattr(message, "media", None) is not None

    if has_media:
        media_bytes = await message.download_media(file=bytes)
        media_file = infer_media_file(message, media_bytes)
        if media_file is not None:
            return await client.send_file(
                entity=target_entity,
                file=media_file,
                caption=text or "",
                formatting_entities=entities,
                buttons=buttons,
                force_document=bool(getattr(message, "document", None) and not getattr(message, "photo", None)),
            )

    if not text and not buttons:
        return None

    return await client.send_message(
        entity=target_entity,
        message=text or "",
        formatting_entities=entities,
        buttons=buttons,
        link_preview=bool(getattr(message, "web_preview", None)),
    )


async def run_mirror(args: argparse.Namespace) -> int:
    client, auth_mode = await create_mirror_client(args)
    try:
        source_entity = await resolve_entity(client, args.source)
        target_entity = await resolve_entity(client, args.target)

        source_title = getattr(source_entity, "title", None) or getattr(source_entity, "username", None) or args.source
        target_title = getattr(target_entity, "title", None) or getattr(target_entity, "username", None) or args.target
        log_line("mirror_started", source=source_title, target=target_title, auth_mode=auth_mode)

        @client.on(events.NewMessage(chats=source_entity))
        async def on_new_message(event: Any) -> None:
            message = event.message
            try:
                sent = await mirror_message(client, target_entity, message)
                if sent is None:
                    log_line(
                        "message_skipped",
                        source_message_id=getattr(message, "id", None),
                        reason="empty_or_service",
                    )
                    return
                log_line(
                    "message_mirrored",
                    source_message_id=getattr(message, "id", None),
                    target_message_id=getattr(sent, "id", None),
                )
            except Exception as exc:
                log_line(
                    "message_failed",
                    source_message_id=getattr(message, "id", None),
                    error=str(exc) or exc.__class__.__name__,
                )

        await client.run_until_disconnected()
        return 0
    finally:
        await client.disconnect()
