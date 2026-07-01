from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(slots=True)
class Settings:
    api_id: int
    api_hash: str
    bot_token: str


def load_settings() -> Settings:
    load_dotenv()
    api_id_raw = os.getenv("API_ID", "2040").strip()
    api_hash = os.getenv("API_HASH", "b18441a1ff607e10a989891a5462e627").strip()
    bot_token = os.getenv("BOT_TOKEN", "").strip()
    try:
        api_id = int(api_id_raw)
    except ValueError as exc:
        raise ValueError(f"API_ID 无法解析为整数: {api_id_raw}") from exc
    if not api_hash:
        raise ValueError("API_HASH 未配置")
    return Settings(api_id=api_id, api_hash=api_hash, bot_token=bot_token)
