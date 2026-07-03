from __future__ import annotations

import asyncio
import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from dataclasses import dataclass, field
from getpass import getpass
from pathlib import Path
from typing import Any

from telethon import Button, TelegramClient, events
from telethon.errors import (
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneCodeHashEmptyError,
    SendCodeUnavailableError,
    SessionPasswordNeededError,
)

from .config import RuntimeConfig, RuntimeConfigStore, Settings, load_settings
from .telegram_utils import build_url_buttons, infer_media_file, parse_button_lines, resolve_entity, rewrite_mirror_links


def log_line(event: str, **payload: Any) -> None:
    print(json.dumps({"event": event, **payload}, ensure_ascii=False), flush=True)


def resolve_runtime_store() -> RuntimeConfigStore:
    return RuntimeConfigStore((Path.cwd() / "data" / "runtime-config.json").resolve())


def resolve_chat_refs(
    args: argparse.Namespace,
    settings: Settings,
    runtime_config: RuntimeConfig | None = None,
) -> tuple[str, str]:
    runtime = runtime_config or RuntimeConfig()
    source = str(getattr(args, "source", "") or runtime.source_chat or settings.source_chat or "").strip()
    target = str(getattr(args, "target", "") or runtime.target_chat or settings.target_chat or "").strip()
    if not source:
        raise RuntimeError("未提供源群；请先私聊机器人配置 A 群，或传 --source，或在 .env 里配置 SOURCE_CHAT")
    if not target:
        raise RuntimeError("未提供目标群；请先私聊机器人配置 B 群，或传 --target，或在 .env 里配置 TARGET_CHAT")
    return source, target


def resolve_listener_phone(
    args: argparse.Namespace,
    settings: Settings,
    runtime_config: RuntimeConfig | None = None,
) -> str:
    runtime = runtime_config or RuntimeConfig()
    return str(getattr(args, "phone", "") or runtime.listener_phone or settings.listener_phone or "").strip()


def resolve_listener_code(args: argparse.Namespace, settings: Settings) -> str:
    return str(getattr(args, "code", "") or settings.listener_code or "").strip()


def resolve_listener_password(
    args: argparse.Namespace,
    settings: Settings,
    runtime_config: RuntimeConfig | None = None,
) -> str:
    runtime = runtime_config or RuntimeConfig()
    return str(getattr(args, "password", "") or runtime.listener_password or settings.listener_password or "").strip()


def resolve_listener_session_path(
    args: argparse.Namespace,
    settings: Settings,
    runtime_config: RuntimeConfig | None = None,
    *,
    allow_missing: bool = False,
) -> Path:
    runtime = runtime_config or RuntimeConfig()
    session_raw = str(getattr(args, "session", "") or runtime.listener_session or settings.listener_session or "").strip()
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
    if allow_missing:
        return (session_dir / "listener.session").resolve()
    raise FileNotFoundError(f"未找到可用 session，请把 .session 文件放到目录: {session_dir}")


def reset_sender_session(session_path: Path) -> None:
    for candidate in session_path.parent.glob(f"{session_path.name}*"):
        if candidate.is_file():
            candidate.unlink(missing_ok=True)


def can_prompt() -> bool:
    return bool(getattr(sys.stdin, "isatty", lambda: False)())


def prompt_required(label: str, *, secret: bool = False) -> str:
    while True:
        value = (getpass(label) if secret else input(label)).strip()
        if value:
            return value
        print("输入不能为空，请重新输入。", flush=True)

MENU_SOURCE = "menu:source"
MENU_TARGET = "menu:target"
MENU_LISTENER = "menu:listener"
MENU_LISTENER_PHONE = "menu:listener_phone"
MENU_LISTENER_SENDCODE = "menu:listener_sendcode"
MENU_LISTENER_CODE = "menu:listener_code"
MENU_LISTENER_PASSWORD = "menu:listener_password"
MENU_LISTENER_SESSION = "menu:listener_session"
MENU_LISTENER_RELOAD = "menu:listener_reload"
MENU_LISTENER_BACK = "menu:listener_back"
MENU_MIRROR_START = "menu:mirror_start"
MENU_MIRROR_STOP = "menu:mirror_stop"
MENU_BUTTONS = "menu:buttons"
MENU_ADMINS = "menu:admins"
MENU_ADMIN_ADD = "menu:admin_add"
MENU_ADMIN_REMOVE = "menu:admin_remove"
MENU_ADMIN_BACK = "menu:admin_back"
MENU_PREVIEW = "menu:preview"
MENU_CONFIG = "menu:config"

PENDING_SOURCE = "source"
PENDING_TARGET = "target"
PENDING_LISTENER_PHONE = "listener_phone"
PENDING_LISTENER_CODE = "listener_code"
PENDING_LISTENER_PASSWORD = "listener_password"
PENDING_LISTENER_SESSION = "listener_session"
PENDING_BUTTONS = "buttons"
PENDING_ADMIN_ADD = "admin_add"
PENDING_ADMIN_REMOVE = "admin_remove"


def build_admin_menu_buttons() -> list[list[Any]]:
    return [
        [Button.inline("监听群", MENU_SOURCE), Button.inline("指定群", MENU_TARGET)],
        [Button.inline("监听号", MENU_LISTENER), Button.inline("按钮配置", MENU_BUTTONS)],
        [Button.inline("启动监听", MENU_MIRROR_START), Button.inline("停止监听", MENU_MIRROR_STOP)],
        [Button.inline("管理员", MENU_ADMINS), Button.inline("预览按钮", MENU_PREVIEW)],
        [Button.inline("查看当前配置", MENU_CONFIG)],
    ]


def build_listener_menu_buttons() -> list[list[Any]]:
    return [
        [Button.inline("设置手机号", MENU_LISTENER_PHONE), Button.inline("发送验证码", MENU_LISTENER_SENDCODE)],
        [Button.inline("输入验证码", MENU_LISTENER_CODE), Button.inline("两步密码", MENU_LISTENER_PASSWORD)],
        [Button.inline("session路径", MENU_LISTENER_SESSION), Button.inline("刷新监听", MENU_LISTENER_RELOAD)],
        [Button.inline("返回主菜单", MENU_LISTENER_BACK)],
    ]


def build_admin_manage_buttons() -> list[list[Any]]:
    return [
        [Button.inline("添加管理员", MENU_ADMIN_ADD), Button.inline("删除管理员", MENU_ADMIN_REMOVE)],
        [Button.inline("返回主菜单", MENU_ADMIN_BACK)],
    ]


@dataclass(slots=True)
class ButtonConfigStore:
    path: Path
    button_specs: list[list[dict[str, str]]] = field(default_factory=list)

    def load(self) -> None:
        if not self.path.exists():
            self.button_specs = []
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            self.button_specs = []
            return
        items = payload.get("buttons", []) if isinstance(payload, dict) else []
        normalized: list[list[dict[str, str]]] = []
        for raw_row in items:
            row_items = raw_row if isinstance(raw_row, list) else [raw_row]
            row: list[dict[str, str]] = []
            for item in row_items:
                if not isinstance(item, dict):
                    continue
                text = str(item.get("text", "") or "").strip()
                url = str(item.get("url", "") or "").strip()
                if text and url:
                    row.append({"text": text, "url": url})
            if row:
                normalized.append(row)
        self.button_specs = normalized

    def save(self, button_specs: list[list[dict[str, str]]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        normalized: list[list[dict[str, str]]] = []
        for raw_row in button_specs:
            row: list[dict[str, str]] = []
            for item in raw_row:
                text = str(item.get("text", "") or "").strip()
                url = str(item.get("url", "") or "").strip()
                if text and url:
                    row.append({"text": text, "url": url})
            if row:
                normalized.append(row)
        self.button_specs = normalized
        payload = {"buttons": self.button_specs}
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def clear(self) -> None:
        self.button_specs = []
        if self.path.exists():
            self.path.unlink(missing_ok=True)

    def render_buttons(self) -> Any:
        return build_url_buttons(self.button_specs)

    def render_text(self) -> str:
        if not self.button_specs:
            return "当前没有配置按钮。"
        return "\n".join(
            " && ".join(f"{item['text']}｜{item['url']}" for item in row)
            for row in self.button_specs
        )

    def count_buttons(self) -> int:
        return sum(len(row) for row in self.button_specs)


@dataclass(slots=True)
class LoginCodeState:
    phone: str = ""
    phone_code_hash: str = ""
    session_path: str = ""
    password_needed: bool = False


class LoginCodeStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> LoginCodeState:
        if not self.path.exists():
            return LoginCodeState()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return LoginCodeState()
        if not isinstance(payload, dict):
            return LoginCodeState()
        return LoginCodeState(
            phone=str(payload.get("phone", "") or "").strip(),
            phone_code_hash=str(payload.get("phone_code_hash", "") or "").strip(),
            session_path=str(payload.get("session_path", "") or "").strip(),
            password_needed=bool(payload.get("password_needed", False)),
        )

    def save(self, state: LoginCodeState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "phone": state.phone,
            "phone_code_hash": state.phone_code_hash,
            "session_path": state.session_path,
            "password_needed": state.password_needed,
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink(missing_ok=True)


@dataclass(slots=True)
class AdminInputState:
    pending_by_user: dict[int, str] = field(default_factory=dict)

    def get(self, user_id: int) -> str:
        return self.pending_by_user.get(int(user_id), "")

    def set(self, user_id: int, pending: str) -> None:
        self.pending_by_user[int(user_id)] = pending

    def clear(self, user_id: int) -> None:
        self.pending_by_user.pop(int(user_id), None)


class MirrorRuntime:
    def __init__(
        self,
        *,
        args: argparse.Namespace,
        settings: Settings,
        runtime_store: RuntimeConfigStore,
        sender_client: TelegramClient,
        button_store: ButtonConfigStore,
        admin_state: AdminInputState,
    ) -> None:
        self.args = args
        self.settings = settings
        self.runtime_store = runtime_store
        self.sender_client = sender_client
        self.button_store = button_store
        self.admin_state = admin_state
        self.listener_client: TelegramClient | None = None
        self.listener_handler: Any = None
        self.listener_key: tuple[str, str, str] | None = None
        self.lock = asyncio.Lock()
        self.active_mirror_tasks: set[asyncio.Task[Any]] = set()

    async def stop_listener(self) -> None:
        if self.listener_client is None:
            return
        try:
            if self.listener_handler is not None:
                self.listener_client.remove_event_handler(self.listener_handler)
            await self.listener_client.disconnect()
        finally:
            self.listener_client = None
            self.listener_handler = None
            self.listener_key = None
            for task in list(self.active_mirror_tasks):
                task.cancel()
            if self.active_mirror_tasks:
                await asyncio.gather(*self.active_mirror_tasks, return_exceptions=True)
            self.active_mirror_tasks.clear()

    def _track_mirror_task(self, task: asyncio.Task[Any]) -> None:
        self.active_mirror_tasks.add(task)

        def _cleanup(done: asyncio.Task[Any]) -> None:
            self.active_mirror_tasks.discard(done)

        task.add_done_callback(_cleanup)

    async def _mirror_one(self, target_entity: Any, message: Any) -> None:
        received_at = datetime.now(timezone.utc)
        source_date = getattr(message, "date", None)
        source_delay_ms: int | None = None
        if isinstance(source_date, datetime):
            if source_date.tzinfo is None:
                source_date = source_date.replace(tzinfo=timezone.utc)
            source_delay_ms = max(0, int((received_at - source_date).total_seconds() * 1000))

        started = received_at
        try:
            sent = await mirror_message(
                self.sender_client,
                target_entity,
                message,
                button_store=self.button_store,
            )
            elapsed_ms = max(0, int((datetime.now(timezone.utc) - started).total_seconds() * 1000))
            if sent is None:
                log_line(
                    "message_skipped",
                    source_message_id=getattr(message, "id", None),
                    reason="empty_or_service",
                    has_media=bool(getattr(message, "media", None)),
                    source_delay_ms=source_delay_ms,
                )
                return
            log_line(
                "message_mirrored",
                source_message_id=getattr(message, "id", None),
                target_message_id=getattr(sent, "id", None),
                has_media=bool(getattr(message, "media", None)),
                source_delay_ms=source_delay_ms,
                mirror_elapsed_ms=elapsed_ms,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            elapsed_ms = max(0, int((datetime.now(timezone.utc) - started).total_seconds() * 1000))
            log_line(
                "message_failed",
                source_message_id=getattr(message, "id", None),
                has_media=bool(getattr(message, "media", None)),
                source_delay_ms=source_delay_ms,
                mirror_elapsed_ms=elapsed_ms,
                error=str(exc) or exc.__class__.__name__,
            )

    async def reload(self) -> str:
        async with self.lock:
            runtime = self.runtime_store.load()
            if not runtime.mirror_enabled:
                await self.stop_listener()
                return "监听已停止。点【启动监听】后才会开始同步。"
            try:
                source_ref, target_ref = resolve_chat_refs(self.args, self.settings, runtime)
            except Exception as exc:
                await self.stop_listener()
                return str(exc)

            session_path = resolve_listener_session_path(
                self.args,
                self.settings,
                runtime,
                allow_missing=True,
            )
            current_key = (source_ref, target_ref, str(session_path))
            if self.listener_client is not None and self.listener_key == current_key:
                return f"监听中：{source_ref} -> {target_ref}"

            await self.stop_listener()
            try:
                listener_client = await create_listener_client(
                    self.args,
                    runtime_config=runtime,
                    allow_prompt=False,
                    login_if_needed=False,
                )
                source_entity = await resolve_entity(listener_client, source_ref)
                target_entity = await resolve_entity(self.sender_client, target_ref)
            except Exception as exc:
                if str(exc) == "LISTENER_LOGIN_REQUIRED":
                    return "监听号还没登录。私聊机器人点【监听号】后，用按钮完成手机号、验证码和两步密码设置即可。"
                if "未找到可用的监听账号 session" in str(exc) or "LISTENER_PHONE" in str(exc):
                    return "监听账号还没配置好。先私聊机器人点【监听号】设置手机号，再发送验证码完成登录。"
                return f"监听未启动：{exc}"

            async def on_new_message(event: Any) -> None:
                message = event.message
                task = asyncio.create_task(self._mirror_one(target_entity, message))
                self._track_mirror_task(task)

            listener_client.add_event_handler(on_new_message, events.NewMessage(chats=source_entity))
            self.listener_client = listener_client
            self.listener_handler = on_new_message
            self.listener_key = current_key
            source_title = getattr(source_entity, "title", None) or getattr(source_entity, "username", None) or source_ref
            target_title = getattr(target_entity, "title", None) or getattr(target_entity, "username", None) or target_ref
            log_line(
                "mirror_started",
                source=source_title,
                target=target_title,
                listener_session=str(session_path),
                sender_mode="bot",
                button_admin_ids=sorted(get_allowed_admin_ids(self.settings, runtime)),
                configured_button_count=self.button_store.count_buttons(),
            )
            return f"已开始监听：{source_title} -> {target_title}"


async def authorize_listener_client(
    client: TelegramClient,
    args: argparse.Namespace,
    settings: Settings,
    session_path: Path,
    runtime_config: RuntimeConfig | None = None,
    *,
    allow_prompt: bool = True,
) -> None:
    phone = resolve_listener_phone(args, settings, runtime_config)
    if not phone:
        if not allow_prompt or not can_prompt():
            raise RuntimeError(
                "未找到可用的监听账号 session，且未配置 LISTENER_PHONE。"
                " 请在 .env 中设置 LISTENER_PHONE，或先执行 python bot.py login 生成 session。"
            )
        phone = prompt_required("请输入监听账号手机号: ")

    try:
        sent_code = await client.send_code_request(phone)
    except SendCodeUnavailableError as exc:
        raise RuntimeError(
            "这个号码当前可用的验证码发送方式已经用完了。"
            " 请稍后再试，或者先在 Telegram 官方客户端完成一次登录。"
        ) from exc

    code = resolve_listener_code(args, settings)
    if not code:
        if not allow_prompt or not can_prompt():
            raise RuntimeError(
                "LISTENER_CODE 未配置，当前也不是可交互终端。"
                " 请在 .env 里设置 LISTENER_CODE 后重试，或先在可交互环境执行 python bot.py login。"
            )
        code = prompt_required("请输入监听账号的短信/接码验证码: ")

    try:
        await client.sign_in(phone=phone, code=code, phone_code_hash=sent_code.phone_code_hash)
    except PhoneCodeInvalidError as exc:
        raise RuntimeError("LISTENER_CODE 错误，请检查验证码后重试。") from exc
    except PhoneCodeExpiredError as exc:
        raise RuntimeError("LISTENER_CODE 已过期，请重新获取验证码后重试。") from exc
    except SessionPasswordNeededError:
        password = resolve_listener_password(args, settings, runtime_config)
        if not password:
            if not allow_prompt or not can_prompt():
                raise RuntimeError("该监听账号开启了两步验证，请在 .env 里设置 LISTENER_PASSWORD 后重试。") from None
            password = prompt_required("请输入监听账号的两步验证密码: ", secret=True)
        await client.sign_in(password=password)

    if not await client.is_user_authorized():
        raise RuntimeError(f"监听账号登录失败: {session_path}")


async def create_listener_client(
    args: argparse.Namespace,
    runtime_config: RuntimeConfig | None = None,
    *,
    allow_prompt: bool = True,
    login_if_needed: bool = True,
) -> TelegramClient:
    settings = load_settings()
    session_path = resolve_listener_session_path(args, settings, runtime_config, allow_missing=True)
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
        if not login_if_needed:
            await client.disconnect()
            raise RuntimeError("LISTENER_LOGIN_REQUIRED")
        await authorize_listener_client(
            client,
            args,
            settings,
            session_path,
            runtime_config,
            allow_prompt=allow_prompt,
        )
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
    *,
    button_store: ButtonConfigStore,
) -> Any:
    if getattr(message, "action", None) is not None:
        return None

    original_text = str(getattr(message, "message", None) or "")
    text = original_text
    buttons = button_store.render_buttons()
    entities = getattr(message, "entities", None)
    text, entities = rewrite_mirror_links(text, entities)
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


def extract_command_arg(text: str, command: str) -> str:
    parts = str(text or "").split(maxsplit=1)
    if parts and parts[0].lower() == command.lower():
        return parts[1].strip() if len(parts) > 1 else ""
    return ""


def get_allowed_admin_ids(settings: Settings, runtime_config: RuntimeConfig | None = None) -> frozenset[int]:
    runtime = runtime_config or RuntimeConfig()
    if runtime.admin_ids:
        return frozenset(runtime.admin_ids)
    return settings.button_admin_ids


def format_admin_ids(admin_ids: frozenset[int]) -> str:
    if not admin_ids:
        return "未绑定"
    return ", ".join(str(item) for item in sorted(admin_ids))


def format_runtime_config(
    runtime_config: RuntimeConfig,
    button_store: ButtonConfigStore,
    mirror_runtime: MirrorRuntime,
) -> str:
    admin_ids = get_allowed_admin_ids(mirror_runtime.settings, runtime_config)
    mirror_switch = "已开启" if runtime_config.mirror_enabled else "已停止"
    runtime_status = "运行中" if mirror_runtime.listener_client is not None else "未启动"
    return (
        "当前配置：\n"
        f"管理员：{format_admin_ids(admin_ids)}\n"
        f"A群：{runtime_config.source_chat or '未配置'}\n"
        f"B群：{runtime_config.target_chat or '未配置'}\n"
        f"监听手机号：{runtime_config.listener_phone or '未配置'}\n"
        f"监听session：{runtime_config.listener_session or 'session/listener.session'}\n"
        f"按钮数量：{button_store.count_buttons()}\n"
        f"监听开关：{mirror_switch}\n"
        f"监听状态：{runtime_status}"
    )


def format_listener_runtime(
    runtime_config: RuntimeConfig,
    code_store: LoginCodeStore,
    mirror_runtime: MirrorRuntime,
) -> str:
    pending = code_store.load()
    if pending.password_needed:
        login_state = "待输入两步密码"
    elif pending.phone_code_hash:
        login_state = "待输入验证码"
    elif mirror_runtime.listener_client is not None:
        login_state = "已登录并运行"
    else:
        login_state = "未完成登录"
    return (
        "监听号配置：\n"
        f"手机号：{runtime_config.listener_phone or '未配置'}\n"
        f"session：{runtime_config.listener_session or 'session/listener.session'}\n"
        f"两步密码：{'已保存' if runtime_config.listener_password else '未配置'}\n"
        f"登录进度：{login_state}\n"
        f"监听开关：{'已开启' if runtime_config.mirror_enabled else '已停止'}\n"
        f"监听状态：{'运行中' if mirror_runtime.listener_client is not None else '未启动'}"
    )


def format_admin_panel(
    runtime_config: RuntimeConfig,
    button_store: ButtonConfigStore,
    mirror_runtime: MirrorRuntime,
    *,
    hint: str = "",
) -> str:
    lines = [
        "管理面板",
        "",
        format_runtime_config(runtime_config, button_store, mirror_runtime),
        "",
        "点下面按钮选择操作；点完后机器人会单独发一条引导消息。",
        "现在 `python bot.py` 只负责启动；管理员、A/B群、监听号这些都在机器人里配。",
        "参数配好后，再手动点【启动监听】；不想同步时点【停止监听】。",
        "清空按钮仍支持：/clearbuttons",
    ]
    if hint:
        lines.extend(["", hint])
    return "\n".join(lines)


def format_listener_panel(
    runtime_config: RuntimeConfig,
    code_store: LoginCodeStore,
    mirror_runtime: MirrorRuntime,
    *,
    hint: str = "",
) -> str:
    lines = [
        "监听号中心",
        "",
        format_listener_runtime(runtime_config, code_store, mirror_runtime),
        "",
        "推荐顺序：先设置手机号，再点发送验证码，然后点输入验证码完成登录。",
        "整个流程会写入本地 runtime-config.json，不需要每次靠 .env。",
    ]
    if hint:
        lines.extend(["", hint])
    return "\n".join(lines)


def format_admin_manage_panel(runtime_config: RuntimeConfig, settings: Settings, *, hint: str = "") -> str:
    lines = [
        "管理员配置",
        "",
        f"当前管理员：{format_admin_ids(get_allowed_admin_ids(settings, runtime_config))}",
        "",
        "点按钮后直接发 Telegram 用户 ID 就行。",
        "首个私聊机器人的人会自动成为管理员，后面都在这里增删，不用再改 .env。",
    ]
    if hint:
        lines.extend(["", hint])
    return "\n".join(lines)


def format_admin_flow_message(action: str) -> str:
    if action == MENU_SOURCE:
        return "已进入【监听群】设置流程。\n请直接发送 A 群链接、@用户名，或 t.me/+ 邀请链接。\n发送 /cancel 可取消。"
    if action == MENU_TARGET:
        return "已进入【指定群】设置流程。\n请直接发送 B 群链接、@用户名，或 t.me/+ 邀请链接。\n发送 /cancel 可取消。"
    if action == MENU_LISTENER_PHONE:
        return "已进入【监听号】设置流程。\n请直接发送监听手机号，例如：+8613800000000\n发送 /cancel 可取消。"
    if action == MENU_LISTENER_CODE:
        return "请直接发送这次收到的验证码，例如：12345\n发送 /cancel 可取消。"
    if action == MENU_LISTENER_PASSWORD:
        return "请直接发送监听号的两步密码。\n发送 /cancel 可取消。"
    if action == MENU_LISTENER_SESSION:
        return "请直接发送监听 session 路径；不发的话默认使用 session/listener.session\n发送 /cancel 可取消。"
    if action == MENU_BUTTONS:
        return (
            "已进入【按钮配置】设置流程。\n"
            "请直接发送按钮配置：\n"
            "按钮文字|https://example.com\n"
            "同一行多个按钮：按钮A|https://a.com && 按钮B|https://b.com\n"
            "发送 /cancel 可取消。"
        )
    return ""


async def reply_admin_panel(
    event: Any,
    runtime_store: RuntimeConfigStore,
    button_store: ButtonConfigStore,
    mirror_runtime: MirrorRuntime,
    *,
    hint: str = "",
) -> None:
    runtime_config = runtime_store.load()
    await event.reply(
        format_admin_panel(runtime_config, button_store, mirror_runtime, hint=hint),
        buttons=build_admin_menu_buttons(),
    )


async def reply_listener_panel(
    event: Any,
    runtime_store: RuntimeConfigStore,
    code_store: LoginCodeStore,
    mirror_runtime: MirrorRuntime,
    *,
    hint: str = "",
) -> None:
    runtime_config = runtime_store.load()
    await event.reply(
        format_listener_panel(runtime_config, code_store, mirror_runtime, hint=hint),
        buttons=build_listener_menu_buttons(),
    )


async def reply_listener_hint(
    event: Any,
    hint: str,
    *,
    buttons: Any = None,
) -> None:
    await event.reply(str(hint or "").strip(), buttons=buttons)


async def reply_admin_manage_panel(
    event: Any,
    runtime_store: RuntimeConfigStore,
    settings: Settings,
    *,
    hint: str = "",
) -> None:
    runtime_config = runtime_store.load()
    await event.reply(
        format_admin_manage_panel(runtime_config, settings, hint=hint),
        buttons=build_admin_manage_buttons(),
    )


def parse_admin_id(text: str) -> int:
    raw = str(text or "").strip()
    if not raw:
        raise ValueError("请输入管理员 Telegram 用户 ID。")
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError("管理员必须是纯数字 Telegram 用户 ID。") from exc


async def bootstrap_first_admin(
    event: Any,
    settings: Settings,
    runtime_store: RuntimeConfigStore,
    button_store: ButtonConfigStore,
    mirror_runtime: MirrorRuntime,
) -> bool:
    if not getattr(event, "is_private", False):
        return False
    sender_id = getattr(event, "sender_id", None)
    if sender_id is None:
        return False

    runtime_config = runtime_store.load()
    if runtime_config.admin_ids:
        return False

    runtime_store.add_admin_id(int(sender_id))
    log_line("admin_bootstrap", sender_id=int(sender_id), admin_ids=[int(sender_id)])
    await reply_admin_panel(
        event,
        runtime_store,
        button_store,
        mirror_runtime,
        hint="已把你绑定为首个管理员。后续所有业务配置都直接在机器人里完成，不用再改 .env。",
    )
    return True


async def send_listener_code(
    *,
    args: argparse.Namespace,
    settings: Settings,
    runtime_store: RuntimeConfigStore,
    code_store: LoginCodeStore,
    phone_override: str = "",
) -> str:
    runtime = runtime_store.load()
    phone = phone_override.strip() or runtime.listener_phone or settings.listener_phone
    if not phone:
        return "还没设置监听手机号。请先点【设置手机号】并发送手机号。"
    if phone_override.strip():
        runtime = runtime_store.update(listener_phone=phone_override.strip())

    session_path = resolve_listener_session_path(args, settings, runtime, allow_missing=True)
    runtime_store.update(listener_session=str(session_path))
    client = TelegramClient(str(session_path), settings.api_id, settings.api_hash)
    await client.connect()
    try:
        if await client.is_user_authorized():
            me = await client.get_me()
            return f"监听号已经登录：{getattr(me, 'phone', None) or getattr(me, 'username', None) or 'unknown'}"
        sent_code = await client.send_code_request(phone)
        code_store.save(
            LoginCodeState(
                phone=phone,
                phone_code_hash=sent_code.phone_code_hash,
                session_path=str(session_path),
                password_needed=False,
            )
        )
        return "验证码已发送，请直接发送收到的验证码。"
    except SendCodeUnavailableError:
        return "这个号码当前暂时发不了验证码，请稍后再试，或先去 Telegram 官方客户端登录一次。"
    finally:
        await client.disconnect()


async def finish_listener_login_with_password(
    *,
    password: str,
    settings: Settings,
    runtime_store: RuntimeConfigStore,
    code_store: LoginCodeStore,
) -> str:
    state = code_store.load()
    if not state.session_path:
        return "当前没有待完成的验证码登录。"
    client = TelegramClient(state.session_path, settings.api_id, settings.api_hash)
    await client.connect()
    try:
        await client.sign_in(password=password)
        me = await client.get_me()
        runtime_store.update(
            listener_password=password,
            listener_phone=state.phone,
            listener_session=state.session_path,
        )
        code_store.clear()
        return (
            "监听号登录成功："
            f"{getattr(me, 'phone', None) or getattr(me, 'username', None) or getattr(me, 'id', 'unknown')}"
        )
    finally:
        await client.disconnect()


async def finish_listener_code_login(
    *,
    code: str,
    args: argparse.Namespace,
    settings: Settings,
    runtime_store: RuntimeConfigStore,
    code_store: LoginCodeStore,
) -> str:
    state = code_store.load()
    if not state.phone or not state.phone_code_hash or not state.session_path:
        return "当前没有待完成的验证码流程。请先点【发送验证码】。"

    runtime = runtime_store.load()
    client = TelegramClient(state.session_path, settings.api_id, settings.api_hash)
    await client.connect()
    try:
        try:
            await client.sign_in(phone=state.phone, code=code, phone_code_hash=state.phone_code_hash)
        except SessionPasswordNeededError:
            password = resolve_listener_password(args, settings, runtime)
            if password:
                await client.sign_in(password=password)
            else:
                code_store.save(
                    LoginCodeState(
                        phone=state.phone,
                        phone_code_hash=state.phone_code_hash,
                        session_path=state.session_path,
                        password_needed=True,
                    )
                )
                return "这个账号需要两步验证，请直接发送两步密码。"
        except PhoneCodeInvalidError:
            return "验证码错误，请重新发送验证码。"
        except PhoneCodeExpiredError:
            code_store.clear()
            return "验证码已过期，请重新点【发送验证码】获取新的验证码。"
        except PhoneCodeHashEmptyError:
            code_store.clear()
            return "登录状态已失效，请重新点【发送验证码】。"

        me = await client.get_me()
        runtime_store.update(listener_phone=state.phone, listener_session=state.session_path)
        code_store.clear()
        return (
            "监听号登录成功："
            f"{getattr(me, 'phone', None) or getattr(me, 'username', None) or getattr(me, 'id', 'unknown')}"
        )
    finally:
        await client.disconnect()


async def handle_admin_button_message(
    event: Any,
    settings: Settings,
    button_store: ButtonConfigStore,
    runtime_store: RuntimeConfigStore,
    code_store: LoginCodeStore,
    mirror_runtime: MirrorRuntime,
    args: argparse.Namespace,
) -> None:
    if not getattr(event, "is_private", False):
        return

    sender_id = getattr(event, "sender_id", None)
    if sender_id is None:
        return

    if await bootstrap_first_admin(event, settings, runtime_store, button_store, mirror_runtime):
        return

    runtime_config = runtime_store.load()
    allowed_admin_ids = get_allowed_admin_ids(settings, runtime_config)
    if int(sender_id) not in allowed_admin_ids:
        log_line(
            "admin_message_ignored",
            sender_id=sender_id,
            reason="not_in_button_admin_ids",
            allowed_admin_ids=sorted(allowed_admin_ids),
        )
        return

    text = str(getattr(event, "raw_text", "") or "").strip()
    if not text:
        return

    lowered = text.lower()
    if lowered in {"/start", "/help"}:
        await event.reply(
            "可用命令：\n"
            "/config 查看当前配置\n"
            "/source A群链接或@用户名\n"
            "/target B群链接或@用户名\n"
            "/listener_phone 手机号\n"
            "/listener_password 两步密码\n"
            "/listener_session session路径\n"
            "/sendcode [手机号]\n"
            "/code 12345\n"
            "/buttons 查看当前按钮\n"
            "/clearbuttons 清空当前按钮\n\n"
            "按钮配置格式：\n"
            "按钮文字｜https://example.com\n"
            "按钮文字2｜https://example.com/2\n"
            "按钮A｜https://a.com && 按钮B｜https://b.com\n\n"
            "配置会优先写入本地 runtime-config.json，不用每次改 .env。"
        )
        return

    runtime_config = runtime_store.load()

    if lowered == "/config":
        await event.reply(format_runtime_config(runtime_config, button_store, mirror_runtime))
        return

    if lowered.startswith("/source"):
        value = extract_command_arg(text, "/source")
        if not value:
            await event.reply(f"当前 A群：{runtime_config.source_chat or '未配置'}")
            return
        runtime_store.update(source_chat=value)
        status = await mirror_runtime.reload()
        await event.reply(f"A群已更新为：{value}\n{status}")
        return

    if lowered.startswith("/target"):
        value = extract_command_arg(text, "/target")
        if not value:
            await event.reply(f"当前 B群：{runtime_config.target_chat or '未配置'}")
            return
        runtime_store.update(target_chat=value)
        status = await mirror_runtime.reload()
        await event.reply(f"B群已更新为：{value}\n{status}")
        return

    if lowered.startswith("/listener_phone"):
        value = extract_command_arg(text, "/listener_phone")
        if not value:
            await event.reply(f"当前监听手机号：{runtime_config.listener_phone or '未配置'}")
            return
        runtime_store.update(listener_phone=value)
        await event.reply(f"监听手机号已保存：{value}")
        return

    if lowered.startswith("/listener_session"):
        value = extract_command_arg(text, "/listener_session")
        if not value:
            await event.reply(f"当前监听session：{runtime_config.listener_session or 'session/listener.session'}")
            return
        runtime_store.update(listener_session=value)
        status = await mirror_runtime.reload()
        await event.reply(f"监听session路径已保存：{value}\n{status}")
        return

    if lowered.startswith("/listener_password"):
        value = extract_command_arg(text, "/listener_password")
        if not value:
            await event.reply("请这样发：`/listener_password 你的两步密码`")
            return
        runtime_store.update(listener_password=value)
        pending = code_store.load()
        if pending.password_needed:
            result = await finish_listener_login_with_password(
                password=value,
                settings=settings,
                runtime_store=runtime_store,
                code_store=code_store,
            )
            status = await mirror_runtime.reload()
            await event.reply(f"{result}\n{status}")
            return
        await event.reply("两步密码已保存。")
        return

    if lowered.startswith("/sendcode"):
        value = extract_command_arg(text, "/sendcode")
        result = await send_listener_code(
            args=args,
            settings=settings,
            runtime_store=runtime_store,
            code_store=code_store,
            phone_override=value,
        )
        await event.reply(result)
        return

    if lowered.startswith("/code"):
        value = extract_command_arg(text, "/code")
        if not value:
            await event.reply("请这样发：`/code 12345`")
            return
        result = await finish_listener_code_login(
            code=value,
            args=args,
            settings=settings,
            runtime_store=runtime_store,
            code_store=code_store,
        )
        status = await mirror_runtime.reload()
        await event.reply(f"{result}\n{status}")
        return

    if lowered in {"/buttons", "按钮", "查看按钮"}:
        await event.reply(button_store.render_text())
        return

    if lowered in {"/clearbuttons", "/clear", "清空按钮"}:
        button_store.clear()
        await event.reply("已清空按钮配置。")
        return

    button_specs = parse_button_lines(text)
    if not button_specs:
        await event.reply(
            "未识别到有效按钮格式。\n"
            "请按这个格式发送：按钮文字｜https://example.com\n"
            "同一行多个按钮可用 && 连接。"
        )
        return

    button_store.save(button_specs)
    await event.reply(f"按钮已更新，共 {button_store.count_buttons()} 个：\n{button_store.render_text()}")


async def apply_pending_admin_input(
    *,
    sender_id: int,
    text: str,
    event: Any,
    settings: Settings,
    button_store: ButtonConfigStore,
    runtime_store: RuntimeConfigStore,
    code_store: LoginCodeStore,
    mirror_runtime: MirrorRuntime,
    args: argparse.Namespace,
) -> bool:
    pending = mirror_runtime.admin_state.get(sender_id)
    if not pending:
        return False

    if pending == PENDING_SOURCE:
        runtime_store.update(source_chat=text)
        mirror_runtime.admin_state.clear(sender_id)
        status = await mirror_runtime.reload()
        await reply_admin_panel(event, runtime_store, button_store, mirror_runtime, hint=f"A群已更新为：{text}\n{status}")
        return True

    if pending == PENDING_TARGET:
        runtime_store.update(target_chat=text)
        mirror_runtime.admin_state.clear(sender_id)
        status = await mirror_runtime.reload()
        await reply_admin_panel(event, runtime_store, button_store, mirror_runtime, hint=f"B群已更新为：{text}\n{status}")
        return True

    if pending == PENDING_LISTENER_PHONE:
        runtime_store.update(listener_phone=text)
        mirror_runtime.admin_state.clear(sender_id)
        await reply_listener_hint(
            event,
            f"监听手机号已保存：{text}\n下一步请点【发送验证码】。",
            buttons=[[Button.inline("发送验证码", MENU_LISTENER_SENDCODE)]],
        )
        return True

    if pending == PENDING_LISTENER_CODE:
        mirror_runtime.admin_state.clear(sender_id)
        result = await finish_listener_code_login(
            code=text,
            args=args,
            settings=settings,
            runtime_store=runtime_store,
            code_store=code_store,
        )
        await mirror_runtime.reload()
        await reply_listener_hint(event, result)
        return True

    if pending == PENDING_LISTENER_PASSWORD:
        runtime_store.update(listener_password=text)
        mirror_runtime.admin_state.clear(sender_id)
        pending_state = code_store.load()
        if pending_state.password_needed:
            result = await finish_listener_login_with_password(
                password=text,
                settings=settings,
                runtime_store=runtime_store,
                code_store=code_store,
            )
            await mirror_runtime.reload()
            await reply_listener_hint(event, result)
            return True
        await reply_listener_hint(event, "两步密码已保存。")
        return True

    if pending == PENDING_LISTENER_SESSION:
        runtime_store.update(listener_session=text)
        mirror_runtime.admin_state.clear(sender_id)
        status = await mirror_runtime.reload()
        await reply_listener_hint(event, f"监听 session 路径已保存：{text}\n{status}")
        return True

    if pending == PENDING_BUTTONS:
        button_specs = parse_button_lines(text)
        if not button_specs:
            await reply_admin_panel(
                event,
                runtime_store,
                button_store,
                mirror_runtime,
                hint="按钮格式不对，请重新发一次：\n按钮文字|https://example.com\n按钮A|https://a.com && 按钮B|https://b.com",
            )
            return True
        button_store.save(button_specs)
        mirror_runtime.admin_state.clear(sender_id)
        await reply_admin_panel(event, runtime_store, button_store, mirror_runtime, hint=f"按钮已更新，共 {button_store.count_buttons()} 个。\n{button_store.render_text()}")
        return True

    if pending == PENDING_ADMIN_ADD:
        try:
            admin_id = parse_admin_id(text)
        except ValueError as exc:
            await reply_admin_manage_panel(event, runtime_store, settings, hint=str(exc))
            return True
        runtime_store.add_admin_id(admin_id, seed_ids=settings.button_admin_ids)
        mirror_runtime.admin_state.clear(sender_id)
        await reply_admin_manage_panel(event, runtime_store, settings, hint=f"已添加管理员：{admin_id}")
        return True

    if pending == PENDING_ADMIN_REMOVE:
        try:
            admin_id = parse_admin_id(text)
        except ValueError as exc:
            await reply_admin_manage_panel(event, runtime_store, settings, hint=str(exc))
            return True
        current_admin_ids = set(get_allowed_admin_ids(settings, runtime_store.load()))
        if admin_id not in current_admin_ids:
            mirror_runtime.admin_state.clear(sender_id)
            await reply_admin_manage_panel(event, runtime_store, settings, hint=f"管理员 {admin_id} 不在当前列表里。")
            return True
        if len(current_admin_ids) <= 1:
            await reply_admin_manage_panel(event, runtime_store, settings, hint="至少要保留 1 个管理员，不能删空。")
            return True
        runtime_store.remove_admin_id(admin_id, seed_ids=current_admin_ids)
        mirror_runtime.admin_state.clear(sender_id)
        await reply_admin_manage_panel(event, runtime_store, settings, hint=f"已删除管理员：{admin_id}")
        return True

    return False


async def handle_admin_callback(
    event: Any,
    settings: Settings,
    button_store: ButtonConfigStore,
    runtime_store: RuntimeConfigStore,
    code_store: LoginCodeStore,
    mirror_runtime: MirrorRuntime,
    args: argparse.Namespace,
) -> None:
    sender_id = getattr(event, "sender_id", None)
    runtime_config = runtime_store.load()
    allowed_admin_ids = get_allowed_admin_ids(settings, runtime_config)
    if sender_id is None or int(sender_id) not in allowed_admin_ids:
        await event.answer("无权限", alert=True)
        return

    action = (getattr(event, "data", b"") or b"").decode("utf-8", errors="ignore")

    if action == MENU_SOURCE:
        mirror_runtime.admin_state.set(int(sender_id), PENDING_SOURCE)
        await event.reply(format_admin_flow_message(action))
        await event.answer("等待输入 A 群")
        return

    if action == MENU_TARGET:
        mirror_runtime.admin_state.set(int(sender_id), PENDING_TARGET)
        await event.reply(format_admin_flow_message(action))
        await event.answer("等待输入 B 群")
        return

    if action == MENU_LISTENER:
        mirror_runtime.admin_state.clear(int(sender_id))
        await reply_listener_panel(
            event,
            runtime_store,
            code_store,
            mirror_runtime,
            hint="监听号改成按钮流程了。按顺序点【设置手机号】->【发送验证码】->【输入验证码】就行。",
        )
        await event.answer("已打开监听号中心")
        return

    if action == MENU_LISTENER_PHONE:
        mirror_runtime.admin_state.set(int(sender_id), PENDING_LISTENER_PHONE)
        await event.reply(format_admin_flow_message(action))
        await event.answer("等待输入监听号")
        return

    if action == MENU_LISTENER_SENDCODE:
        mirror_runtime.admin_state.clear(int(sender_id))
        result = await send_listener_code(
            args=args,
            settings=settings,
            runtime_store=runtime_store,
            code_store=code_store,
        )
        await reply_listener_hint(
            event,
            result,
            buttons=[[Button.inline("输入验证码", MENU_LISTENER_CODE)]],
        )
        await event.answer("已处理发送验证码")
        return

    if action == MENU_LISTENER_CODE:
        mirror_runtime.admin_state.set(int(sender_id), PENDING_LISTENER_CODE)
        await event.reply(format_admin_flow_message(action))
        await event.answer("等待输入验证码")
        return

    if action == MENU_LISTENER_PASSWORD:
        mirror_runtime.admin_state.set(int(sender_id), PENDING_LISTENER_PASSWORD)
        await event.reply(format_admin_flow_message(action))
        await event.answer("等待输入两步密码")
        return

    if action == MENU_LISTENER_SESSION:
        mirror_runtime.admin_state.set(int(sender_id), PENDING_LISTENER_SESSION)
        await event.reply(format_admin_flow_message(action))
        await event.answer("等待输入session路径")
        return

    if action == MENU_LISTENER_RELOAD:
        mirror_runtime.admin_state.clear(int(sender_id))
        status = await mirror_runtime.reload()
        await reply_listener_hint(event, status)
        await event.answer("已刷新监听状态")
        return

    if action == MENU_MIRROR_START:
        mirror_runtime.admin_state.clear(int(sender_id))
        runtime_store.update(mirror_enabled=True)
        status = await mirror_runtime.reload()
        await reply_admin_panel(event, runtime_store, button_store, mirror_runtime, hint=status)
        await event.answer("已尝试启动监听")
        return

    if action == MENU_MIRROR_STOP:
        mirror_runtime.admin_state.clear(int(sender_id))
        runtime_store.update(mirror_enabled=False)
        status = await mirror_runtime.reload()
        await reply_admin_panel(event, runtime_store, button_store, mirror_runtime, hint=status)
        await event.answer("已停止监听")
        return

    if action == MENU_LISTENER_BACK:
        mirror_runtime.admin_state.clear(int(sender_id))
        await reply_admin_panel(event, runtime_store, button_store, mirror_runtime, hint="已返回主菜单。")
        await event.answer("已返回主菜单")
        return

    if action == MENU_BUTTONS:
        mirror_runtime.admin_state.set(int(sender_id), PENDING_BUTTONS)
        await event.reply(format_admin_flow_message(action))
        await event.answer("等待输入按钮配置")
        return

    if action == MENU_ADMINS:
        mirror_runtime.admin_state.clear(int(sender_id))
        await reply_admin_manage_panel(
            event,
            runtime_store,
            settings,
            hint="管理员也改成机器人内配置了；点下面按钮直接增删即可。",
        )
        await event.answer("已打开管理员配置")
        return

    if action == MENU_ADMIN_ADD:
        mirror_runtime.admin_state.set(int(sender_id), PENDING_ADMIN_ADD)
        await event.reply("请直接发送要添加的管理员 Telegram 用户 ID。\n发送 /cancel 可取消。")
        await event.answer("等待输入管理员 ID")
        return

    if action == MENU_ADMIN_REMOVE:
        mirror_runtime.admin_state.set(int(sender_id), PENDING_ADMIN_REMOVE)
        await event.reply("请直接发送要删除的管理员 Telegram 用户 ID。\n发送 /cancel 可取消。")
        await event.answer("等待输入管理员 ID")
        return

    if action == MENU_ADMIN_BACK:
        mirror_runtime.admin_state.clear(int(sender_id))
        await reply_admin_panel(event, runtime_store, button_store, mirror_runtime, hint="已返回主菜单。")
        await event.answer("已返回主菜单")
        return

    if action == MENU_PREVIEW:
        mirror_runtime.admin_state.clear(int(sender_id))
        if button_store.count_buttons() <= 0:
            await event.answer("当前还没有按钮配置", alert=True)
            return
        await event.reply("当前按钮预览如下：", buttons=button_store.render_buttons())
        await event.answer("已发送按钮预览")
        return

    if action == MENU_CONFIG:
        mirror_runtime.admin_state.clear(int(sender_id))
        await event.reply(f"这是当前配置快照。\n\n{format_runtime_config(runtime_config, button_store, mirror_runtime)}")
        await event.answer("已刷新配置")
        return

    await event.answer("未知操作")


async def handle_admin_button_message(
    event: Any,
    settings: Settings,
    button_store: ButtonConfigStore,
    runtime_store: RuntimeConfigStore,
    code_store: LoginCodeStore,
    mirror_runtime: MirrorRuntime,
    args: argparse.Namespace,
) -> None:
    if not getattr(event, "is_private", False):
        return

    sender_id = getattr(event, "sender_id", None)
    if sender_id is None or int(sender_id) not in settings.button_admin_ids:
        log_line(
            "admin_message_ignored",
            sender_id=sender_id,
            reason="not_in_button_admin_ids",
            allowed_admin_ids=sorted(settings.button_admin_ids),
        )
        return

    text = str(getattr(event, "raw_text", "") or "").strip()
    if not text:
        return

    lowered = text.lower()
    if lowered in {"/start", "/help", "/menu"}:
        mirror_runtime.admin_state.clear(int(sender_id))
        await reply_admin_panel(event, runtime_store, button_store, mirror_runtime, hint="现在只需要启动机器人；后面的配置都在这里完成。")
        return

    if lowered in {"/cancel", "取消"}:
        mirror_runtime.admin_state.clear(int(sender_id))
        await reply_admin_panel(event, runtime_store, button_store, mirror_runtime, hint="已取消当前输入。")
        return

    if not text.startswith("/"):
        handled = await apply_pending_admin_input(
            sender_id=int(sender_id),
            text=text,
            event=event,
            settings=settings,
            button_store=button_store,
            runtime_store=runtime_store,
            code_store=code_store,
            mirror_runtime=mirror_runtime,
            args=args,
        )
        if handled:
            return

    runtime_config = runtime_store.load()

    if lowered == "/config":
        await reply_admin_panel(event, runtime_store, button_store, mirror_runtime, hint="这是当前配置快照。")
        return

    if lowered in {"/startmirror", "/runmirror", "启动监听"}:
        runtime_store.update(mirror_enabled=True)
        status = await mirror_runtime.reload()
        await reply_admin_panel(event, runtime_store, button_store, mirror_runtime, hint=status)
        return

    if lowered in {"/stopmirror", "停止监听"}:
        runtime_store.update(mirror_enabled=False)
        status = await mirror_runtime.reload()
        await reply_admin_panel(event, runtime_store, button_store, mirror_runtime, hint=status)
        return

    if lowered in {"/admins", "/admin", "管理员"}:
        await reply_admin_manage_panel(event, runtime_store, settings, hint="这里可以直接增删管理员，不用再改 .env。")
        return

    if lowered.startswith("/addadmin"):
        value = extract_command_arg(text, "/addadmin")
        if not value:
            await reply_admin_manage_panel(event, runtime_store, settings, hint="请这样发：/addadmin 5991190607")
            return
        try:
            admin_id = parse_admin_id(value)
        except ValueError as exc:
            await reply_admin_manage_panel(event, runtime_store, settings, hint=str(exc))
            return
        runtime_store.add_admin_id(admin_id, seed_ids=allowed_admin_ids)
        await reply_admin_manage_panel(event, runtime_store, settings, hint=f"已添加管理员：{admin_id}")
        return

    if lowered.startswith("/deladmin"):
        value = extract_command_arg(text, "/deladmin")
        if not value:
            await reply_admin_manage_panel(event, runtime_store, settings, hint="请这样发：/deladmin 5991190607")
            return
        try:
            admin_id = parse_admin_id(value)
        except ValueError as exc:
            await reply_admin_manage_panel(event, runtime_store, settings, hint=str(exc))
            return
        current_admin_ids = set(get_allowed_admin_ids(settings, runtime_store.load()))
        if admin_id not in current_admin_ids:
            await reply_admin_manage_panel(event, runtime_store, settings, hint=f"管理员 {admin_id} 不在当前列表里。")
            return
        if len(current_admin_ids) <= 1:
            await reply_admin_manage_panel(event, runtime_store, settings, hint="至少要保留 1 个管理员，不能删空。")
            return
        runtime_store.remove_admin_id(admin_id, seed_ids=current_admin_ids)
        await reply_admin_manage_panel(event, runtime_store, settings, hint=f"已删除管理员：{admin_id}")
        return

    if lowered in {"/listener", "/listener_menu"}:
        await reply_listener_panel(
            event,
            runtime_store,
            code_store,
            mirror_runtime,
            hint="这里是监听号专用面板。直接点按钮配置，不需要再去改 .env。",
        )
        return

    if lowered.startswith("/source"):
        value = extract_command_arg(text, "/source")
        if not value:
            await reply_admin_panel(event, runtime_store, button_store, mirror_runtime, hint=f"当前 A 群：{runtime_config.source_chat or '未配置'}")
            return
        runtime_store.update(source_chat=value)
        status = await mirror_runtime.reload()
        await reply_admin_panel(event, runtime_store, button_store, mirror_runtime, hint=f"A群已更新为：{value}\n{status}")
        return

    if lowered.startswith("/target"):
        value = extract_command_arg(text, "/target")
        if not value:
            await reply_admin_panel(event, runtime_store, button_store, mirror_runtime, hint=f"当前 B 群：{runtime_config.target_chat or '未配置'}")
            return
        runtime_store.update(target_chat=value)
        status = await mirror_runtime.reload()
        await reply_admin_panel(event, runtime_store, button_store, mirror_runtime, hint=f"B群已更新为：{value}\n{status}")
        return

    if lowered.startswith("/listener_phone"):
        value = extract_command_arg(text, "/listener_phone")
        if not value:
            await reply_listener_hint(event, f"当前监听手机号：{runtime_config.listener_phone or '未配置'}")
            return
        runtime_store.update(listener_phone=value)
        await reply_listener_hint(
            event,
            f"监听手机号已保存：{value}\n下一步请点【发送验证码】。",
            buttons=[[Button.inline("发送验证码", MENU_LISTENER_SENDCODE)]],
        )
        return

    if lowered.startswith("/listener_session"):
        value = extract_command_arg(text, "/listener_session")
        if not value:
            await reply_listener_hint(event, f"当前监听 session：{runtime_config.listener_session or 'session/listener.session'}")
            return
        runtime_store.update(listener_session=value)
        status = await mirror_runtime.reload()
        await reply_listener_hint(event, f"监听 session 路径已保存：{value}\n{status}")
        return

    if lowered.startswith("/listener_password"):
        value = extract_command_arg(text, "/listener_password")
        if not value:
            await reply_listener_hint(event, "请直接发送两步密码。")
            return
        runtime_store.update(listener_password=value)
        pending = code_store.load()
        if pending.password_needed:
            result = await finish_listener_login_with_password(password=value, settings=settings, runtime_store=runtime_store, code_store=code_store)
            await mirror_runtime.reload()
            await reply_listener_hint(event, result)
            return
        await reply_listener_hint(event, "两步密码已保存。")
        return

    if lowered.startswith("/sendcode"):
        value = extract_command_arg(text, "/sendcode")
        result = await send_listener_code(args=args, settings=settings, runtime_store=runtime_store, code_store=code_store, phone_override=value)
        await reply_listener_hint(
            event,
            result,
            buttons=[[Button.inline("输入验证码", MENU_LISTENER_CODE)]],
        )
        return

    if lowered.startswith("/code"):
        value = extract_command_arg(text, "/code")
        if not value:
            await reply_listener_hint(event, "请直接发送验证码。")
            return
        result = await finish_listener_code_login(code=value, args=args, settings=settings, runtime_store=runtime_store, code_store=code_store)
        await mirror_runtime.reload()
        await reply_listener_hint(event, result)
        return

    if lowered in {"/buttons", "按钮", "查看按钮"}:
        await reply_admin_panel(event, runtime_store, button_store, mirror_runtime, hint=button_store.render_text())
        return

    if lowered in {"/clearbuttons", "/clear", "清空按钮"}:
        button_store.clear()
        await reply_admin_panel(event, runtime_store, button_store, mirror_runtime, hint="已清空按钮配置。")
        return

    button_specs = parse_button_lines(text)
    if not button_specs:
        await reply_admin_panel(event, runtime_store, button_store, mirror_runtime, hint="未识别到有效操作。\n你可以点面板按钮，或者继续用 /source /target /sendcode /code 这些命令。")
        return

    button_store.save(button_specs)
    await reply_admin_panel(event, runtime_store, button_store, mirror_runtime, hint=f"按钮已更新，共 {button_store.count_buttons()} 个。\n{button_store.render_text()}")


async def run_mirror(args: argparse.Namespace) -> int:
    settings = load_settings()
    sender_client = await create_sender_bot_client()
    runtime_store = resolve_runtime_store()
    code_store = LoginCodeStore((Path.cwd() / "data" / "login-code.json").resolve())
    button_store = ButtonConfigStore((Path.cwd() / "data" / "mirror-buttons.json").resolve())
    admin_state = AdminInputState()
    button_store.load()
    mirror_runtime = MirrorRuntime(
        args=args,
        settings=settings,
        runtime_store=runtime_store,
        sender_client=sender_client,
        button_store=button_store,
        admin_state=admin_state,
    )
    try:
        status = await mirror_runtime.reload()
        bot_me = await sender_client.get_me()
        log_line(
            "bot_ready",
            bot_id=getattr(bot_me, "id", None),
            bot_username=getattr(bot_me, "username", None),
            allowed_admin_ids=sorted(get_allowed_admin_ids(settings, runtime_store.load())),
            mirror_status=status,
        )
        log_line("mirror_boot", status=status)

        @sender_client.on(events.NewMessage(incoming=True))
        async def on_admin_message(event: Any) -> None:
            try:
                await handle_admin_button_message(
                    event,
                    settings,
                    button_store,
                    runtime_store,
                    code_store,
                    mirror_runtime,
                    args,
                )
            except Exception as exc:
                log_line(
                    "admin_command_failed",
                    sender_id=getattr(event, "sender_id", None),
                    error=str(exc) or exc.__class__.__name__,
                )

        @sender_client.on(events.CallbackQuery())
        async def on_admin_callback(event: Any) -> None:
            try:
                await handle_admin_callback(
                    event,
                    settings,
                    button_store,
                    runtime_store,
                    code_store,
                    mirror_runtime,
                    args,
                )
            except Exception as exc:
                log_line(
                    "admin_callback_failed",
                    sender_id=getattr(event, "sender_id", None),
                    error=str(exc) or exc.__class__.__name__,
                )

        await sender_client.run_until_disconnected()
        return 0
    finally:
        await mirror_runtime.stop_listener()
        await sender_client.disconnect()


async def login_listener_session(args: argparse.Namespace) -> dict[str, Any]:
    settings = load_settings()
    runtime = resolve_runtime_store().load()
    client = await create_listener_client(args, runtime_config=runtime, allow_prompt=True)
    try:
        me = await client.get_me()
        session_path = resolve_listener_session_path(args, settings, runtime, allow_missing=True)
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
