import asyncio
import random
import re
import time
import traceback

import pyvts

from nana.config import (
    TOKEN_PATH,
    VTS_EXPRESSION_COOLDOWN_SECONDS,
    VTS_EXPRESSION_DEFAULT_CHANCE,
    VTS_EXPRESSION_RESET_DELAY_SECONDS,
    VTS_EXPRESSION_RESET_FALLBACK_HOTKEY,
    VTS_EXPRESSION_RESET_TIMEOUT_SECONDS,
)
from nana.memory import memory, memory_lock
from nana.runtime.expression_router import ExpressionCatalog, ExpressionRouter, get_expression_router
from nana.runtime.logger import log_event

# Shared expression catalog (read-only reference for status)
_EXPRESSION_CATALOG = ExpressionCatalog()

vts_lock = asyncio.Lock()
VTS_CONNECT_TIMEOUT_SECONDS = 2.5
VTS_REQUEST_TIMEOUT_SECONDS = 1.5
VTS_AUTH_TIMEOUT_SECONDS = 2.5
VTS_RECONNECT_MIN_SECONDS = 8.0
VTS_RECONNECT_MAX_SECONDS = 60.0
VTS_AUTH_STATUS_LABELS = {
    -1: "invalid_token",
    0: "unauthenticated",
    1: "pending",
    2: "authenticated",
    None: "unknown",
}

EXPR_MAP = {
    "vui": ["星星眼", "爱心眼", "脸红"],
    "yêu": ["爱心眼", "脸红"],
    "thích": ["爱心眼", "脸红"],
    "cute": ["变Q", "抱小熊"],
    "ôm": ["抱小熊", "抱枕头"],
    "hehe": ["星星眼"],
    "wow": ["星星眼", "圈圈眼"],
    "tức": ["生气"],
    "bực": ["生气", "白眼"],
    "ghét": ["拿刀", "生气"],
    "coi thường": ["白眼"],
    "buồn": ["哭"],
    "khóc": ["哭"],
    "xin lỗi": ["哭", "创口贴"],
    "buồn ngủ": ["瞌睡"],
    "mệt": ["瞌睡", "晕"],
    "chóng mặt": ["晕", "圈圈眼"],
    "sợ": ["吐幽灵", "晕"],
    "bối rối": ["圈圈眼", "剪刀眼"],
    "chơi game": ["游戏机"],
    "code": ["键盘"],
    "gõ": ["键盘"],
    "hát": ["麦克风"],
    "bơi": ["泳装版", "泳镜"],
    "bắn": ["水枪手"],
    "tiêm": ["打针", "拿棉棒"],
    "tóc dài": ["长发"],
    "tóc ngắn": ["短发"],
    "kính": ["眼镜"],
    "chibi": ["变Q"],
    "áo khoác": ["外套"],
}

last_expr_time = 0


class ManagedVTS:
    def __init__(self):
        self.client = None
        self._connect_lock = asyncio.Lock()
        self._last_connect_attempt = 0.0
        self._reconnect_delay = VTS_RECONNECT_MIN_SECONDS
        self.last_error = None
        self.last_error_type = None
        self.last_error_repr = None
        self.last_auth_status = None
        self.connected = False

    async def connect(self, force=False):
        async with self._connect_lock:
            now = time.time()
            if self.connected and self.client and self.last_auth_status == 2 and not force:
                return
            if not force and now - self._last_connect_attempt < self._reconnect_delay:
                raise RuntimeError(f"VTS reconnect cooling down ({self._reconnect_delay:.0f}s)")

            self._last_connect_attempt = now
            old_client = self.client
            self.client = None
            self.connected = False
            await _close_vts_client(old_client)

            client = pyvts.vts(
                plugin_info={
                    "plugin_name": "AI Nana",
                    "developer": "ban",
                    "authentication_token_path": str(TOKEN_PATH),
                }
            )
            try:
                await asyncio.wait_for(client.connect(), timeout=VTS_CONNECT_TIMEOUT_SECONDS)
                await _authenticate_vts(client)
            except Exception as exc:
                self.last_error = exc
                self.last_error_type = type(exc).__name__
                self.last_error_repr = repr(exc)
                self.last_auth_status = _get_auth_status(client)
                await _close_vts_client(client)
                raise
            self.client = client
            self.connected = True
            self.last_error = None
            self.last_error_type = None
            self.last_error_repr = None
            self.last_auth_status = _get_auth_status(client)
            self._reconnect_delay = VTS_RECONNECT_MIN_SECONDS

    async def request(self, payload):
        if not self.connected or not self.client:
            await self.connect()
        try:
            return await asyncio.wait_for(self.client.request(payload), timeout=VTS_REQUEST_TIMEOUT_SECONDS)
        except Exception as exc:
            self.connected = False
            self.last_error = exc
            self.last_error_type = type(exc).__name__
            self.last_error_repr = repr(exc)
            self.last_auth_status = _get_auth_status(self.client)
            self._reconnect_delay = min(self._reconnect_delay * 2, VTS_RECONNECT_MAX_SECONDS)
            log_event("vts", f"Request failed, next reconnect delayed: {exc}")
            raise exc

    async def close(self):
        await _close_vts_client(self.client)
        self.client = None
        self.connected = False


_VTS_RUNTIME = ManagedVTS()


def get_vts_runtime() -> ManagedVTS:
    return _VTS_RUNTIME


def vts_snapshot(vts=None):
    runtime = vts or _VTS_RUNTIME
    auth_status = getattr(runtime, "last_auth_status", None)
    client = getattr(runtime, "client", None)
    if client is not None:
        live_auth_status = _get_auth_status(client)
        if live_auth_status is not None:
            auth_status = live_auth_status
    return {
        "ready": bool(getattr(runtime, "connected", False) and client and auth_status == 2),
        "connected": bool(getattr(runtime, "connected", False)),
        "provided": runtime is not None,
        "last_error": getattr(runtime, "last_error_repr", None) or (repr(getattr(runtime, "last_error")) if getattr(runtime, "last_error", None) is not None else "none"),
        "last_error_type": getattr(runtime, "last_error_type", None) or (type(getattr(runtime, "last_error")).__name__ if getattr(runtime, "last_error", None) is not None else "none"),
        "auth_status": auth_status,
        "auth_status_label": VTS_AUTH_STATUS_LABELS.get(auth_status, f"unknown:{auth_status}"),
        "token_path": str(TOKEN_PATH),
    }


async def ensure_vts_ready(force=False, announce=False):
    runtime = _VTS_RUNTIME
    try:
        await runtime.connect(force=force)
        if announce:
            print("✅ Kết nối VTube Studio thành công!")
        log_event("vts", "VTube Studio authenticated")
        return {
            "ready": True,
            "connected": True,
            "error": None,
            "error_type": None,
            "snapshot": vts_snapshot(runtime),
        }
    except Exception as exc:
        runtime.last_error = exc
        runtime.last_error_type = type(exc).__name__
        runtime.last_error_repr = repr(exc)
        runtime.connected = False
        if announce:
            print(f"⚠️ VTS connect/auth failed: {type(exc).__name__}: {exc}")
        log_event("vts", f"VTS readiness failed: {exc}")
        return {
            "ready": False,
            "connected": False,
            "error": repr(exc),
            "error_type": type(exc).__name__,
            "snapshot": vts_snapshot(runtime),
        }


async def connect_vts():
    status = await ensure_vts_ready(force=True, announce=True)
    if not status["ready"]:
        error = status.get("error") or "unknown VTS error"
        raise RuntimeError(error)
    return get_vts_runtime()


async def _close_vts_client(client):
    if not client:
        return
    close = getattr(client, "close", None)
    if close:
        try:
            result = close()
            if asyncio.iscoroutine(result):
                await result
        except Exception as exc:
            log_event("vts", f"close old VTS client failed: {exc}")


async def _authenticate_vts(client):
    status = _get_auth_status(client)
    log_event("vts", f"Initial auth status: {status}")
    if status == 2:
        return

    if status == -1:
        _delete_invalid_token()

    token = await asyncio.wait_for(client.read_token(), timeout=VTS_AUTH_TIMEOUT_SECONDS)
    if token:
        authenticated = await asyncio.wait_for(client.request_authenticate(), timeout=VTS_AUTH_TIMEOUT_SECONDS)
        status = _get_auth_status(client)
        log_event("vts", f"Auth with stored token: authenticated={authenticated}, status={status}")
        if authenticated or status == 2:
            return
        if status != -1:
            raise RuntimeError(f"VTube Studio authentication failed with stored token, status={status}")
        _delete_invalid_token()

    print("🔐 Nana đang xin quyền VTube Studio mới. Bấm Allow trong VTube Studio nếu popup hiện lên.")
    await asyncio.wait_for(client.request_authenticate_token(force=True), timeout=VTS_AUTH_TIMEOUT_SECONDS)
    authenticated = await asyncio.wait_for(client.request_authenticate(), timeout=VTS_AUTH_TIMEOUT_SECONDS)
    status = _get_auth_status(client)
    log_event("vts", f"Auth with new token: authenticated={authenticated}, status={status}")
    if authenticated or status == 2:
        return

    raise RuntimeError(f"VTube Studio authentication failed, status={status}")


def _response_authenticated(response):
    return _response_data_value(response, "authenticated", False)


def _response_reason(response):
    return _response_data_value(response, "reason", "unknown reason")


def _response_data_value(response, key, default=None):
    try:
        data = response.get("data", {})
        if isinstance(data, dict):
            return data.get(key, default)
        return default
    except Exception:
        return default


def _get_auth_status(client):
    try:
        return client.get_authentic_status()
    except Exception as exc:
        log_event("vts", f"get_authentic_status failed: {exc}")
        return None


def _delete_invalid_token():
    try:
        if TOKEN_PATH.exists():
            TOKEN_PATH.unlink()
            log_event("vts", "Deleted invalid VTS token file")
    except Exception as exc:
        log_event("vts", f"Could not delete invalid VTS token: {exc}")


async def trigger_expression(vts, reply):
    """
    Trigger VTS expression with graceful degradation.

    Uses ExpressionRouter for routing decision:
    - No scary terminal spam when VTS is off
    - Unknown expressions map to safe fallback or no-op
    - Repeated failures summarized in status
    """
    router = get_expression_router()

    # Update VTS state from runtime
    if vts is not None:
        router.update_vts_state(
            available=True,
            connected=getattr(vts, "connected", False),
            error=getattr(vts, "last_error_repr", None),
        )
    else:
        router.update_vts_state(available=False, connected=False)

    text_lower = reply.lower()

    # Match keyword -> expression using EXPR_MAP
    matched = None
    best_len = 0
    for keyword, expressions in EXPR_MAP.items():
        if keyword in text_lower and len(keyword) > best_len:
            matched = random.choice(expressions)
            best_len = len(keyword)

    if not matched and should_skip_cheerful_expression(text_lower):
        return None

    # Get emotion values for fallback selection
    emotion_affection = 0.0
    emotion_annoyance = 0.0
    emotion_playfulness = 0.0
    try:
        with memory_lock:
            emotion = dict(memory.get("emotion", {}))
        emotion_affection = float(emotion.get("affection", 0.0))
        emotion_annoyance = float(emotion.get("annoyance", 0.0))
        emotion_playfulness = float(emotion.get("playfulness", 0.0))
    except Exception:
        pass

    if not matched:
        if random.random() > VTS_EXPRESSION_DEFAULT_CHANCE:
            return None
        if emotion_affection > 0.6:
            matched = random.choice(["爱心眼", "脸红", "星星眼"])
        elif emotion_annoyance > 0.5:
            matched = random.choice(["生气", "白眼"])
        elif emotion_playfulness > 0.6:
            matched = random.choice(["变Q", "星星眼"])

    # Route through ExpressionRouter
    result = router.route(
        requested=matched,
        text_lower=text_lower,
        emotion_affection=emotion_affection,
        emotion_annoyance=emotion_annoyance,
        emotion_playfulness=emotion_playfulness,
    )

    if not result.allowed:
        # Silent fail - no user-visible message
        log_event("vts", f"Expression route blocked: {result.reason.value} - {result.log_message}")
        return None

    final_expr = result.final_expression
    if not final_expr:
        return None

    # Use global cooldown check (original behavior)
    global last_expr_time
    if time.time() - last_expr_time < VTS_EXPRESSION_COOLDOWN_SECONDS:
        return None
    last_expr_time = time.time()

    try:
        if not vts:
            return None
        if getattr(vts, "last_error", None) and not getattr(vts, "connected", True):
            log_event("vts", f"Skip expression while disconnected: {final_expr}")
            return None

        async with vts_lock:
            await vts.request({
                "apiName": "VTubeStudioPublicAPI",
                "apiVersion": "1.0",
                "requestID": "expr_trigger",
                "messageType": "HotkeyTriggerRequest",
                "data": {"hotkeyID": final_expr},
            })
        if result.user_visible:
            print(f"🎭 Trigger: {final_expr}")
        print(f"😊 Expression OK: {final_expr}")
        return final_expr
    except Exception as exc:
        # Graceful error handling - log/status only, no terminal spam.
        log_event("vts", f"Expression error {final_expr}: {exc}")
        router.record_runtime_failure(final_expr, exc)
        return None


async def trigger_expression_lifecycle(vts, reply, voice=None, reason="chat_reply", *, avatar_reply=None, avatar_turn=None):
    from nana.runtime.avatar_intent_gateway import publish_reply_avatar

    raw_reply = avatar_reply if avatar_reply is not None else reply
    if avatar_turn is not None:
        avatar_turn.consider(raw_reply)
    else:
        publish_reply_avatar(raw_reply, source=reason)
    matched = await trigger_expression(vts, reply)
    await reset_expression_after_voice(vts, voice=voice, fallback_hotkey=matched, reason=reason)


async def reset_expression_after_voice(vts, voice=None, fallback_hotkey=None, reason="chat_reply"):
    if not vts:
        return False
    await _wait_for_voice_idle(voice, timeout=VTS_EXPRESSION_RESET_TIMEOUT_SECONDS)
    if VTS_EXPRESSION_RESET_DELAY_SECONDS > 0:
        await asyncio.sleep(VTS_EXPRESSION_RESET_DELAY_SECONDS)
    return await reset_all_expressions(vts, fallback_hotkey=fallback_hotkey, reason=reason)


async def _wait_for_voice_idle(voice, timeout=60.0):
    if not voice:
        return
    started = time.time()
    observed_work = False
    while time.time() - started < timeout:
        try:
            snapshot = voice.snapshot()
        except Exception as exc:
            log_event("vts", f"Voice snapshot unavailable for expression reset: {exc}")
            return
        speaking = bool(snapshot.get("speaking"))
        queue_size = int(snapshot.get("queue_size") or 0)
        observed_work = observed_work or speaking or queue_size > 0
        if observed_work and not speaking and queue_size == 0:
            return
        if not observed_work and time.time() - started > 1.5:
            return
        await asyncio.sleep(0.2)


async def reset_all_expressions(vts, fallback_hotkey=None, reason="chat_reply"):
    if not vts:
        return False
    if getattr(vts, "last_error", None) and not getattr(vts, "connected", True):
        log_event("vts", f"Skip expression reset while disconnected: {reason}")
        return False
    try:
        expressions = await _get_active_expressions(vts)
        reset_count = 0
        for expression_file in expressions:
            if await _set_expression_active(vts, expression_file, False):
                reset_count += 1
        if reset_count:
            print(f"😐 Expression OFF: {reset_count}")
            return True
        if fallback_hotkey and VTS_EXPRESSION_RESET_FALLBACK_HOTKEY:
            async with vts_lock:
                await vts.request({
                    "apiName": "VTubeStudioPublicAPI",
                    "apiVersion": "1.0",
                    "requestID": "expr_reset_fallback",
                    "messageType": "HotkeyTriggerRequest",
                    "data": {"hotkeyID": fallback_hotkey},
                })
            print(f"😐 Expression OFF: {fallback_hotkey}")
            return True
        return False
    except Exception as exc:
        log_event("vts", f"Expression reset failed: {exc}")
        if fallback_hotkey and VTS_EXPRESSION_RESET_FALLBACK_HOTKEY:
            try:
                async with vts_lock:
                    await vts.request({
                        "apiName": "VTubeStudioPublicAPI",
                        "apiVersion": "1.0",
                        "requestID": "expr_reset_fallback_after_error",
                        "messageType": "HotkeyTriggerRequest",
                        "data": {"hotkeyID": fallback_hotkey},
                    })
                print(f"😐 Expression OFF: {fallback_hotkey}")
                return True
            except Exception as fallback_exc:
                log_event("vts", f"Expression reset fallback failed: {fallback_exc}")
        return False


async def _get_active_expressions(vts):
    async with vts_lock:
        response = await vts.request({
            "apiName": "VTubeStudioPublicAPI",
            "apiVersion": "1.0",
            "requestID": "expr_state",
            "messageType": "ExpressionStateRequest",
            "data": {"details": True},
        })
    data = response.get("data", {}) if isinstance(response, dict) else {}
    expressions = data.get("expressions", []) if isinstance(data, dict) else []
    active = []
    for row in expressions:
        if not isinstance(row, dict) or not row.get("active"):
            continue
        expression_file = row.get("file") or row.get("expressionFile")
        if expression_file:
            active.append(expression_file)
    return active


async def _set_expression_active(vts, expression_file, active):
    if not expression_file:
        return False
    async with vts_lock:
        await vts.request({
            "apiName": "VTubeStudioPublicAPI",
            "apiVersion": "1.0",
            "requestID": "expr_reset",
            "messageType": "ExpressionActivationRequest",
            "data": {
                "expressionFile": expression_file,
                "active": bool(active),
            },
        })
    return True


def should_skip_cheerful_expression(text_lower):
    serious_markers = [
        "không ổn",
        "ko ổn",
        "hơi lệch",
        "kéo xa",
        "hạ lại nhịp",
        "đáng lo",
        "nguy hiểm",
        "thót tim",
        "xin chia buồn",
        "nhạy cảm",
        "căng thật",
        "chờ thông tin chính thức",
    ]
    return any(marker in text_lower for marker in serious_markers)


async def vts_mouth_loop(vts, lipsync):
    retry_count = 0
    retry_delay = 1.0
    max_retries = 1
    next_attempt_at = 0.0
    last_sent = None
    last_idle_ping = 0
    while True:
        try:
            now = time.time()
            if now < next_attempt_at:
                await asyncio.sleep(min(1.0, next_attempt_at - now))
                continue
            mouth_value = min(max(lipsync.mouth * 0.75, 0.0), 0.65)
            is_idle = mouth_value < 0.01
            if is_idle and not getattr(vts, "connected", False):
                await asyncio.sleep(1.0)
                continue
            if is_idle and last_sent == 0.0 and now - last_idle_ping < 1.0:
                await asyncio.sleep(0.1)
                continue
            if last_sent is not None and abs(mouth_value - last_sent) < 0.01 and not is_idle:
                await asyncio.sleep(0.033)
                continue

            async with vts_lock:
                await vts.request({
                    "apiName": "VTubeStudioPublicAPI",
                    "apiVersion": "1.0",
                    "requestID": "mouth_lipsync",
                    "messageType": "InjectParameterDataRequest",
                    "data": {
                        "faceFound": True,
                        "mode": "set",
                        "parameterValues": [{"id": "MouthOpen", "value": float(mouth_value), "weight": 1.0}],
                    },
                })
            last_sent = 0.0 if is_idle else mouth_value
            if is_idle:
                last_idle_ping = now
            retry_count = 0
            retry_delay = 1.0
        except Exception as exc:
            log_event("vts", f"Mouth loop error attempt={retry_count + 1}: {exc}")
            retry_count += 1
            if hasattr(vts, "connected"):
                vts.connected = False
            if hasattr(vts, "last_error"):
                vts.last_error = exc
            if retry_count >= max_retries:
                next_attempt_at = time.time() + max(retry_delay, VTS_RECONNECT_MIN_SECONDS)
                log_event("vts", f"Mouth loop paused {int(max(retry_delay, VTS_RECONNECT_MIN_SECONDS))}s after error: {exc}")
                retry_delay = min(retry_delay * 2, VTS_RECONNECT_MAX_SECONDS)
                retry_count = 0
            await asyncio.sleep(retry_delay)
            continue
        await asyncio.sleep(0.1 if is_idle else 0.033)
