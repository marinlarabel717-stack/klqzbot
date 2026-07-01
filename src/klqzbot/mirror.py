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


def resolve_listener_session_path(args: argparse.Namespace) -> Path:
    session_raw = str(getattr(args, "session", "") or "").strip()
    if session_raw:
        session_path = Path(session_raw).expanduser().resolve()
        if not session_path.exists():
            raise FileNotFoundError(f"session 文件不存在: {session_path}")
        return session_path

    session_dir_raw = str(getattr(args, "session_dir", "") or "session").strip() or "session"
    session_dir = Path(session_dir_raw).expanduser().resolve()
    session_dir.mkdir(parents=True, exist_ok=True)
    candidates = sorted(session_dir.glob("*.session"))
    if not candidates:
        raise FileNotFoundError(f"未找到可用 session，请把 .session 文件放到目录: {session_dir}")
    return candidates[0]


async def create_listener_client(args: argparse.Namespace) -> TelegramClient:
    settings = load_settings()
    session_path = resolve_listener_session_path(args)
    client = TelegramClient(str(session_path), settings.api_id, settings.api_hash)
    await client.connect()
    if not await client.is_user_authorized():
        raise RuntimeError(f"监听账号 session 未授权: {session_path}")
    return client


async def create_sender_bot_client() -> TelegramClient:
    settings = load_settings()
    bot_token = settings.bot_token.strip()
    if not bot_token:
        raise RuntimeError("BOT_TOKEN 未配置；B 群同步发送必须使用机器人 token")

    session_path = (Path.cwd() / "data" / "sender-bot.session").resolve()
    session_path.parent.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(str(session_path), settings.api_id, settings.api_hash)
    await client.start(bot_token=bot_token)
    return client


async def mirror_message(
    sender_client: TelegramClient,
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
            return await sender_client.send_file(
                entity=target_entity,
                file=media_file,
                caption=text or "",
                formatting_entities=entities,
                buttons=buttons,
                force_document=bool(getattr(message, "document", None) and not getattr(message, "photo", None)),
            )

    if not text and not buttons:
        return None

    return await sender_client.send_message(
        entity=target_entity,
        message=text or "",
        formatting_entities=entities,
        buttons=buttons,
        link_preview=bool(getattr(message, "web_preview", None)),
    )


async def run_mirror(args: argparse.Namespace) -> int:
    listener_client = await create_listener_client(args)
    sender_client = await create_sender_bot_client()
    try:
        source_entity = await resolve_entity(listener_client, args.source)
        target_entity = await resolve_entity(sender_client, args.target)

        source_title = getattr(source_entity, "title", None) or getattr(source_entity, "username", None) or args.source
        target_title = getattr(target_entity, "title", None) or getattr(target_entity, "username", None) or args.target
        log_line(
            "mirror_started",
            source=source_title,
            target=target_title,
            listener_session=str(resolve_listener_session_path(args)),
            sender_mode="bot",
        )

        @listener_client.on(events.NewMessage(chats=source_entity))
        async def on_new_message(event: Any) -> None:
            message = event.message
            try:
                sent = await mirror_message(sender_client, target_entity, message)
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

        await listener_client.run_until_disconnected()
        return 0
    finally:
        await listener_client.disconnect()
        await sender_client.disconnect()
