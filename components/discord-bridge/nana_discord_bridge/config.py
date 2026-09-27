"""Configuration for the external Discord bridge."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


BRIDGE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
NANA_DATA_ROOT = REPOSITORY_ROOT / "nana" / "data"


def _load_env() -> None:
    load_dotenv(BRIDGE_ROOT / ".env")


def _get_path(name: str, default: Path) -> Path:
    raw = (os.getenv(name) or "").strip()
    return Path(raw).expanduser() if raw else default


def _get_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _get_int(name: str) -> Optional[int]:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{name} must be an integer Discord snowflake")


@dataclass(frozen=True)
class BridgeConfig:
    discord_token: str
    allowed_guild_id: Optional[int]
    allowed_text_channel_id: Optional[int]
    allowed_voice_channel_id: Optional[int]
    outbox_guild_id: Optional[int]
    outbox_channel_id: Optional[int]
    outbox_channel_name: str
    debug_messages: bool
    trigger_prefix: str
    auto_join_user_voice: bool
    mirror_text_reply: bool
    request_dir: Path
    reply_dir: Path
    outbox_dir: Path
    outbox_enabled: bool
    outbox_poll_seconds: float
    route_config_path: Path
    reply_timeout_seconds: float
    poll_interval_seconds: float

    @classmethod
    def load(cls) -> "BridgeConfig":
        _load_env()
        token = (os.getenv("DISCORD_BOT_TOKEN") or "").strip()
        request_dir = _get_path(
            "NANA_REQUEST_DIR",
            NANA_DATA_ROOT / "external_bridge" / "requests",
        )
        reply_dir = _get_path(
            "NANA_REPLY_DIR",
            NANA_DATA_ROOT / "external_bridge" / "replies",
        )
        outbox_dir = _get_path(
            "NANA_STAGE_OUTBOX_DIR",
            NANA_DATA_ROOT / "external_bridge" / "outbox",
        )
        return cls(
            discord_token=token,
            allowed_guild_id=_get_int("DISCORD_ALLOWED_GUILD_ID"),
            allowed_text_channel_id=_get_int("DISCORD_ALLOWED_TEXT_CHANNEL_ID"),
            allowed_voice_channel_id=_get_int("DISCORD_ALLOWED_VOICE_CHANNEL_ID"),
            outbox_guild_id=_get_int("DISCORD_OUTBOX_GUILD_ID") or _get_int("DISCORD_ALLOWED_GUILD_ID"),
            outbox_channel_id=_get_int("DISCORD_OUTBOX_CHANNEL_ID") or _get_int("DISCORD_ALLOWED_TEXT_CHANNEL_ID"),
            outbox_channel_name=(
                os.getenv("DISCORD_OUTBOX_CHANNEL_NAME")
                or os.getenv("DISCORD_ALLOWED_TEXT_CHANNEL_NAME")
                or ""
            ).strip().lstrip("#"),
            debug_messages=_get_bool("DISCORD_DEBUG_MESSAGES", False),
            trigger_prefix=os.getenv("DISCORD_TRIGGER_PREFIX", "!nana").strip() or "!nana",
            auto_join_user_voice=_get_bool("DISCORD_AUTO_JOIN_USER_VOICE", True),
            mirror_text_reply=_get_bool("DISCORD_MIRROR_TEXT_REPLY", True),
            request_dir=request_dir,
            reply_dir=reply_dir,
            outbox_dir=outbox_dir,
            outbox_enabled=_get_bool("DISCORD_OUTBOX_ENABLED", False),
            outbox_poll_seconds=float(os.getenv("DISCORD_OUTBOX_POLL_SECONDS", "0.5")),
            route_config_path=_get_path(
                "NANA_DISCORD_ROUTES_PATH",
                BRIDGE_ROOT / "routes.json",
            ),
            reply_timeout_seconds=float(os.getenv("NANA_REPLY_TIMEOUT_SECONDS", "120")),
            poll_interval_seconds=float(os.getenv("NANA_REPLY_POLL_SECONDS", "0.25")),
        )

    def require_runtime_ready(self) -> None:
        if not self.discord_token:
            raise RuntimeError("DISCORD_BOT_TOKEN is missing in .env")
