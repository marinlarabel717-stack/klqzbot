from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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


@dataclass(slots=True)
class RuntimeConfig:
    source_chat: str = ""
    target_chat: str = ""
    listener_session: str = ""
    listener_phone: str = ""
    listener_password: str = ""
    admin_ids: tuple[int, ...] = ()


class RuntimeConfigStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> RuntimeConfig:
        if not self.path.exists():
            return RuntimeConfig()
        try:
            import json

            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return RuntimeConfig()
        if not isinstance(payload, dict):
            return RuntimeConfig()
        return RuntimeConfig(
            source_chat=str(payload.get("source_chat", "") or "").strip(),
            target_chat=str(payload.get("target_chat", "") or "").strip(),
            listener_session=str(payload.get("listener_session", "") or "").strip(),
            listener_phone=str(payload.get("listener_phone", "") or "").strip(),
            listener_password=str(payload.get("listener_password", "") or "").strip(),
            admin_ids=normalize_admin_ids(payload.get("admin_ids", [])),
        )

    def save(self, config: RuntimeConfig) -> None:
        import json

        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "source_chat": config.source_chat,
            "target_chat": config.target_chat,
            "listener_session": config.listener_session,
            "listener_phone": config.listener_phone,
            "listener_password": config.listener_password,
            "admin_ids": list(config.admin_ids),
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def update(self, **changes: Any) -> RuntimeConfig:
        config = self.load()
        for key, value in changes.items():
            if hasattr(config, key):
                if key == "admin_ids":
                    setattr(config, key, normalize_admin_ids(value))
                else:
                    setattr(config, key, str(value or "").strip())
        self.save(config)
        return config

    def add_admin_id(self, user_id: int, *, seed_ids: Any = None) -> RuntimeConfig:
        config = self.load()
        current = set(config.admin_ids or normalize_admin_ids(seed_ids))
        current.add(int(user_id))
        config.admin_ids = tuple(sorted(current))
        self.save(config)
        return config

    def remove_admin_id(self, user_id: int, *, seed_ids: Any = None) -> RuntimeConfig:
        config = self.load()
        current = set(config.admin_ids or normalize_admin_ids(seed_ids))
        current.discard(int(user_id))
        config.admin_ids = tuple(sorted(current))
        self.save(config)
        return config


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


def normalize_admin_ids(raw: Any) -> tuple[int, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str):
        return tuple(sorted(parse_int_set(raw)))
    if isinstance(raw, (list, tuple, set, frozenset)):
        values: set[int] = set()
        for item in raw:
            text = str(item or "").strip()
            if not text:
                continue
            try:
                values.add(int(text))
            except ValueError as exc:
                raise ValueError(f"admin_ids contains invalid user id: {item}") from exc
        return tuple(sorted(values))
    return ()


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
