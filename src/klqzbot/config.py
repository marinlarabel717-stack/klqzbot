from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(slots=True)
class Settings:
    api_id: int
    api_hash: str
    bot_token: str
    source_chat: str
    target_chat: str
    listener_session: str
    listener_phone: str
    listener_code: str
    listener_password: str
    button_admin_ids: frozenset[int]


def parse_int_set(raw: str) -> frozenset[int]:
    values: set[int] = set()
    for part in str(raw or "").replace("\n", ",").split(","):
        item = part.strip()
        if not item:
            continue
        try:
            values.add(int(item))
        except ValueError as exc:
            raise ValueError(f"BUTTON_ADMIN_IDS contains invalid user id: {item}") from exc
    return frozenset(values)


def load_settings() -> Settings:
    load_dotenv()
    api_id_raw = os.getenv("API_ID", "2040").strip()
    api_hash = os.getenv("API_HASH", "b18441a1ff607e10a989891a5462e627").strip()
    bot_token = os.getenv("BOT_TOKEN", "").strip()
    source_chat = os.getenv("SOURCE_CHAT", "").strip()
    target_chat = os.getenv("TARGET_CHAT", "").strip()
    listener_session = os.getenv("LISTENER_SESSION", "").strip()
    listener_phone = os.getenv("LISTENER_PHONE", "").strip()
    listener_code = os.getenv("LISTENER_CODE", "").strip()
    listener_password = os.getenv("LISTENER_PASSWORD", "").strip()
    button_admin_ids = parse_int_set(os.getenv("BUTTON_ADMIN_IDS", ""))
    try:
        api_id = int(api_id_raw)
    except ValueError as exc:
        raise ValueError(f"API_ID 无法解析为整数: {api_id_raw}") from exc
    if not api_hash:
        raise ValueError("API_HASH 未配置")
    return Settings(
        api_id=api_id,
        api_hash=api_hash,
        bot_token=bot_token,
        source_chat=source_chat,
        target_chat=target_chat,
        listener_session=listener_session,
        listener_phone=listener_phone,
        listener_code=listener_code,
        listener_password=listener_password,
        button_admin_ids=button_admin_ids,
    )
