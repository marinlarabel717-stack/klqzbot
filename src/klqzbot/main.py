from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from telethon import TelegramClient
from telethon.errors import FloodWaitError, UserAlreadyParticipantError, UserPrivacyRestrictedError
from telethon.tl import functions, types

from .config import load_settings
from .mirror import login_listener_session, run_mirror
from .models import CloneStats
from .telegram_utils import resolve_entity


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="klqzbot", description="Telegram 群组工具")
    subparsers = parser.add_subparsers(dest="command", required=True)

    clone_parser = subparsers.add_parser("clone", help="采集源群成员并邀请进目标群")
    clone_parser.add_argument("--session", required=True, help="Telethon session 文件路径")
    clone_parser.add_argument("--source", required=True, help="源群引用")
    clone_parser.add_argument("--target", required=True, help="目标群引用")
    clone_parser.add_argument("--limit", type=int, default=100, help="最多处理多少成员")
    clone_parser.add_argument("--interval", type=float, default=45.0, help="每次邀请间隔秒数")
    clone_parser.add_argument("--dry-run", action="store_true", help="只采集不邀请")
    clone_parser.add_argument("--include-bots", action="store_true", help="默认跳过 bot，开启后包含 bot")

    mirror_parser = subparsers.add_parser("mirror", help="监听 A 群并用 bot 重发到 B 群")
    mirror_parser.add_argument("--session", help="监听账号的 .session 文件路径")
    mirror_parser.add_argument("--session-dir", default="session", help="自动查找 .session 的目录，默认是 ./session")
    mirror_parser.add_argument("--source", help="源群引用；不填则读取 .env 的 SOURCE_CHAT")
    mirror_parser.add_argument("--target", help="目标群引用；不填则读取 .env 的 TARGET_CHAT")
    mirror_parser.add_argument("--phone", help="监听账号手机号；不填则读取 .env 的 LISTENER_PHONE")
    mirror_parser.add_argument("--code", help="监听账号短信/接码验证码；不填则读取 .env 的 LISTENER_CODE")
    mirror_parser.add_argument("--password", help="监听账号两步验证密码；不填则读取 .env 的 LISTENER_PASSWORD")

    login_parser = subparsers.add_parser("login", help="手机号登录监听账号并生成 .session")
    login_parser.add_argument("--session", help="监听账号 .session 写入路径；不填则读取 .env 的 LISTENER_SESSION")
    login_parser.add_argument("--session-dir", default="session", help="未指定 --session 时默认写入的目录")
    login_parser.add_argument("--phone", help="监听账号手机号；不填则读取 .env 的 LISTENER_PHONE")
    login_parser.add_argument("--code", help="短信/接码验证码；不填则读取 .env 的 LISTENER_CODE 或交互输入")
    login_parser.add_argument("--password", help="两步验证密码；不填则读取 .env 的 LISTENER_PASSWORD 或交互输入")
    return parser


def can_invite_user(user: types.User, include_bots: bool) -> bool:
    if not isinstance(user, types.User):
        return False
    if getattr(user, "deleted", False):
        return False
    if getattr(user, "self", False):
        return False
    if getattr(user, "bot", False) and not include_bots:
        return False
    return True


async def collect_members(
    client: TelegramClient,
    source_entity: Any,
    limit: int,
    include_bots: bool,
) -> list[types.User]:
    users: list[types.User] = []
    async for user in client.iter_participants(source_entity):
        if len(users) >= limit:
            break
        if can_invite_user(user, include_bots):
            users.append(user)
    return users


async def invite_user(client: TelegramClient, target_entity: Any, user: types.User) -> str:
    if isinstance(target_entity, types.Channel):
        await client(functions.channels.InviteToChannelRequest(channel=target_entity, users=[user]))
        return "invited"
    if isinstance(target_entity, types.Chat):
        await client(
            functions.messages.AddChatUserRequest(
                chat_id=target_entity.id,
                user_id=user,
                fwd_limit=10,
            )
        )
        return "invited"
    raise RuntimeError("目标群类型不支持邀请")


async def run_clone(args: argparse.Namespace) -> dict[str, Any]:
    settings = load_settings()
    session_path = Path(args.session).expanduser().resolve()
    if not session_path.exists():
        raise FileNotFoundError(f"session 文件不存在: {session_path}")

    stats = CloneStats()
    results: list[dict[str, Any]] = []

    client = TelegramClient(str(session_path), settings.api_id, settings.api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("当前 session 未授权")

        source_entity = await resolve_entity(client, args.source)
        target_entity = await resolve_entity(client, args.target)
        members = await collect_members(
            client=client,
            source_entity=source_entity,
            limit=max(1, int(args.limit)),
            include_bots=bool(args.include_bots),
        )
        stats.scanned = len(members)

        for user in members:
            username = getattr(user, "username", "") or ""
            display_name = " ".join(
                part for part in [getattr(user, "first_name", "") or "", getattr(user, "last_name", "") or ""] if part
            ).strip()
            label = username and f"@{username}" or display_name or str(getattr(user, "id", "unknown"))
            stats.eligible += 1

            if args.dry_run:
                stats.skipped += 1
                results.append({"user": label, "status": "dry-run"})
                continue

            try:
                status = await invite_user(client, target_entity, user)
                stats.invited += 1
                results.append({"user": label, "status": status})
            except UserAlreadyParticipantError:
                stats.skipped += 1
                results.append({"user": label, "status": "already"})
            except UserPrivacyRestrictedError:
                stats.failed += 1
                results.append({"user": label, "status": "privacy-restricted"})
            except FloodWaitError as exc:
                stats.failed += 1
                results.append({"user": label, "status": f"flood-wait:{exc.seconds}"})
                break
            except Exception as exc:
                stats.failed += 1
                results.append({"user": label, "status": f"failed:{exc}"})

            if args.interval > 0:
                await asyncio.sleep(float(args.interval))

        return {
            "ok": True,
            "source": args.source,
            "target": args.target,
            "dryRun": bool(args.dry_run),
            "stats": {
                "scanned": stats.scanned,
                "eligible": stats.eligible,
                "invited": stats.invited,
                "skipped": stats.skipped,
                "failed": stats.failed,
            },
            "results": results,
        }
    finally:
        await client.disconnect()


async def async_main(args: argparse.Namespace) -> int:
    if args.command == "clone":
        result = await run_clone(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "login":
        result = await login_listener_session(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "mirror":
        return await run_mirror(args)
    raise RuntimeError(f"未知命令: {args.command}")


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    raise SystemExit(asyncio.run(async_main(args)))


if __name__ == "__main__":
    main()
