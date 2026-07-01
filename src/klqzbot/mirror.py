from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from getpass import getpass
from pathlib import Path
from typing import Any

from telethon import TelegramClient, events
from telethon.errors import PhoneCodeExpiredError, PhoneCodeInvalidError, SessionPasswordNeededError

from .config import Settings, load_settings
from .telegram_utils import clone_buttons, infer_media_file, resolve_entity


def log_line(event: str, **payload: Any) -> None:
    print(json.dumps({"event": event, **payload}, ensure_ascii=False), flush=True)


def resolve_chat_refs(args: argparse.Namespace) -> tuple[str, str]:
    settings = load_settings()
    source = str(getattr(args, "source", "") or settings.source_chat or "").strip()
    target = str(getattr(args, "target", "") or settings.target_chat or "").strip()
    if not source:
        raise RuntimeError("未提供源群；请传 --source 或在 .env 里配置 SOURCE_CHAT")
    if not target:
        raise RuntimeError("未提供目标群；请传 --target 或在 .env 里配置 TARGET_CHAT")
    return source, target


def resolve_listener_phone(args: argparse.Namespace, settings: Settings) -> str:
    return str(getattr(args, "phone", "") or settings.listener_phone or "").strip()


def resolve_listener_code(args: argparse.Namespace, settings: Settings) -> str:
    return str(getattr(args, "code", "") or settings.listener_code or "").strip()


def resolve_listener_password(args: argparse.Namespace, settings: Settings) -> str:
    return str(getattr(args, "password", "") or settings.listener_password or "").strip()


def resolve_listener_session_path(
    args: argparse.Namespace,
    settings: Settings,
    *,
    allow_missing: bool = False,
) -> Path:
    session_raw = str(getattr(args, "session", "") or settings.listener_session or "").strip()
    if session_raw:
        session_path = Path(session_raw).expanduser().resolve()
        session_path.parent.mkdir(parents=True, exist_ok=True)
        if allow_missing or session_path.exists():
            return session_path
        raise FileNotFoundError(f"session 文件不存在: {session_path}")

    session_dir_raw = str(getattr(args, "session_dir", "") or "session").strip() or "session"
    session_dir = Path(session_dir_raw).expanduser().resolve()
    session_dir.mkdir(parents=True, exist_ok=True)
    candidates = sorted(session_dir.glob("*.session"))
    if candidates:
        return candidates[0]
    if allow_missing and resolve_listener_phone(args, settings):
        return (session_dir / "listener.session").resolve()
    raise FileNotFoundError(f"未找到可用 session，请把 .session 文件放到目录: {session_dir}")


def reset_sender_session(session_path: Path) -> None:
    for candidate in session_path.parent.glob(f"{session_path.name}*"):
        if candidate.is_file():
            candidate.unlink(missing_ok=True)


def can_prompt() -> bool:
    return bool(getattr(sys.stdin, "isatty", lambda: False)())


async def authorize_listener_client(
    client: TelegramClient,
    args: argparse.Namespace,
    settings: Settings,
    session_path: Path,
) -> None:
    phone = resolve_listener_phone(args, settings)
    if not phone:
        raise RuntimeError(
            "未找到可用的监听账号 session，且未配置 LISTENER_PHONE。"
            " 请在 .env 中设置 LISTENER_PHONE，或先执行 python bot.py login 生成 session。"
        )

    sent_code = await client.send_code_request(phone)
    code = resolve_listener_code(args, settings)
    if not code:
        if not can_prompt():
            raise RuntimeError(
                "LISTENER_CODE 未配置，当前也不是可交互终端。"
                " 请在 .env 里设置 LISTENER_CODE 后重试，或先在可交互环境执行 python bot.py login。"
            )
        code = input("请输入监听账号的短信/接码验证码: ").strip()

    try:
        await client.sign_in(phone=phone, code=code, phone_code_hash=sent_code.phone_code_hash)
    except PhoneCodeInvalidError as exc:
        raise RuntimeError("LISTENER_CODE 错误，请检查验证码后重试。") from exc
    except PhoneCodeExpiredError as exc:
        raise RuntimeError("LISTENER_CODE 已过期，请重新获取验证码后重试。") from exc
    except SessionPasswordNeededError:
        password = resolve_listener_password(args, settings)
        if not password:
            if not can_prompt():
                raise RuntimeError("该监听账号开启了两步验证，请在 .env 里设置 LISTENER_PASSWORD 后重试。") from None
            password = getpass("请输入监听账号的两步验证密码: ").strip()
        await client.sign_in(password=password)

    if not await client.is_user_authorized():
        raise RuntimeError(f"监听账号登录失败: {session_path}")


async def create_listener_client(args: argparse.Namespace) -> TelegramClient:
    settings = load_settings()
    session_path = resolve_listener_session_path(args, settings, allow_missing=True)
    client = TelegramClient(str(session_path), settings.api_id, settings.api_hash)
    try:
        await client.connect()
    except sqlite3.OperationalError as exc:
        message = str(exc).lower()
        if "database is locked" in message:
            raise RuntimeError(
                "监听账号的 session 文件正被别的进程占用；请先停止其他正在使用这个 .session 的程序后再重试。"
                f" 当前 session: {session_path}"
            ) from exc
        raise
    if not await client.is_user_authorized():
        await authorize_listener_client(client, args, settings, session_path)
    return client


async def create_sender_bot_client() -> TelegramClient:
    settings = load_settings()
    bot_token = settings.bot_token.strip()
    if not bot_token:
        raise RuntimeError("BOT_TOKEN 未配置；B 群同步发送必须使用机器人 token")

    session_path = (Path.cwd() / "data" / "sender-bot.session").resolve()
    session_path.parent.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(str(session_path), settings.api_id, settings.api_hash)
    await client.connect()
    me = await client.get_me() if await client.is_user_authorized() else None
    if me is not None and not getattr(me, "bot", False):
        await client.disconnect()
        reset_sender_session(session_path)
        client = TelegramClient(str(session_path), settings.api_id, settings.api_hash)

    await client.start(bot_token=bot_token)
    me = await client.get_me()
    if me is None or not getattr(me, "bot", False):
        raise RuntimeError("发送端没有成功登录成 bot；请删除 data/sender-bot.session 后重试，并确认 .env 里的 BOT_TOKEN 正确。")
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
    settings = load_settings()
    listener_client = await create_listener_client(args)
    sender_client = await create_sender_bot_client()
    try:
        source_ref, target_ref = resolve_chat_refs(args)
        source_entity = await resolve_entity(listener_client, source_ref)
        target_entity = await resolve_entity(sender_client, target_ref)
        listener_session = resolve_listener_session_path(args, settings, allow_missing=True)

        source_title = getattr(source_entity, "title", None) or getattr(source_entity, "username", None) or source_ref
        target_title = getattr(target_entity, "title", None) or getattr(target_entity, "username", None) or target_ref
        log_line(
            "mirror_started",
            source=source_title,
            target=target_title,
            listener_session=str(listener_session),
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


async def login_listener_session(args: argparse.Namespace) -> dict[str, Any]:
    settings = load_settings()
    client = await create_listener_client(args)
    try:
        me = await client.get_me()
        session_path = resolve_listener_session_path(args, settings, allow_missing=True)
        return {
            "ok": True,
            "session": str(session_path),
            "user_id": getattr(me, "id", None),
            "phone": getattr(me, "phone", None),
            "username": getattr(me, "username", None),
            "display_name": " ".join(
                part
                for part in [getattr(me, "first_name", "") or "", getattr(me, "last_name", "") or ""]
                if part
            ).strip(),
        }
    finally:
        await client.disconnect()
