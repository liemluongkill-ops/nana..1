from __future__ import annotations

import asyncio
import os
from typing import Any

from nana.game.osu.aim_status import read_aim_status
from nana.integrations.vts import get_vts_runtime, vts_lock, vts_snapshot


REACTION_BY_STATUS = {
    "tracking": "focused",
    "fast_jump": "surprised_focused",
    "dense_pattern": "intense",
    "idle": "relaxed",
    "uncalibrated": "confused",
    "stale": "waiting",
    "unavailable": "offline",
}

OSU_VTS_REACTION_ENABLED_ENV = "NANA_OSU_VTS_REACTION_ENABLED"
OSU_VTS_REACTION_APPROVAL_TOKEN = "I_APPROVE_OSU_VTS_REACTION_SEND"


def read_vts_reaction_preview(*, source_path, stale_ms: int) -> dict[str, Any]:
    status = read_aim_status(source_path, stale_ms=stale_ms)
    aim_status = str(status.get("status") or "unavailable")
    reaction = REACTION_BY_STATUS.get(aim_status, "offline")
    return {
        "decision": "reaction_preview_only_no_vts",
        "source": status.get("source"),
        "aim_status": aim_status,
        "reaction": reaction,
        "reason": status.get("reason"),
        "vts_call": False,
        "real_input": False,
        "submit": False,
    }


async def send_vts_reaction(*, source_path, stale_ms: int, operator_approval_token: str) -> dict[str, Any]:
    preview = read_vts_reaction_preview(source_path=source_path, stale_ms=stale_ms)
    env_enabled = os.getenv(OSU_VTS_REACTION_ENABLED_ENV) == "1"
    token_ok = operator_approval_token == OSU_VTS_REACTION_APPROVAL_TOKEN
    result = {
        "decision": "hold",
        "source": preview.get("source"),
        "aim_status": preview.get("aim_status"),
        "reaction": preview.get("reaction"),
        "reason": preview.get("reason"),
        "gates": {
            "env": env_enabled,
            "token": token_ok,
        },
        "vts_call": False,
        "vts_error_type": None,
        "vts_error": None,
        "real_input": False,
        "submit": False,
    }
    if not env_enabled or not token_ok:
        return result

    vts = get_vts_runtime()
    snapshot = vts_snapshot(vts)
    if not snapshot.get("ready"):
        result["decision"] = "hold"
        result["reason"] = "vts_not_ready"
        result["vts_error_type"] = getattr(vts, "last_error_type", None)
        result["vts_error"] = getattr(vts, "last_error_repr", None) or f"auth_status={snapshot.get('auth_status_label')}"
        return result

    hotkey_id = str(preview.get("reaction") or "offline")
    send_error: Exception | None = None
    try:
        async with vts_lock:
            await vts.request(
                {
                    "apiName": "VTubeStudioPublicAPI",
                    "apiVersion": "1.0",
                    "requestID": "osu_reaction_send",
                    "messageType": "HotkeyTriggerRequest",
                    "data": {"hotkeyID": hotkey_id},
                }
            )
    except Exception as exc:
        send_error = exc

    if send_error is not None:
        result["decision"] = "error"
        result["reason"] = f"vts_send_failed: {type(send_error).__name__}"
        result["vts_error_type"] = type(send_error).__name__
        result["vts_error"] = repr(send_error)
        return result

    result["decision"] = "sent"
    result["vts_call"] = True
    return result
