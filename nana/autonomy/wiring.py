"""Autonomy backend wiring — kết nối AutonomyExpress với real backends.

Tách từ main.py:
- 4 backend functions: _real_autonomy_tts, _real_autonomy_vts, _real_autonomy_subtitle, _real_autonomy_lipsync_stop
- Wiring block (dòng 41775-41790)
"""
import asyncio
import threading
import time

from nana.autonomy.loop import AUTONOMY_LOOP


# -----------------------------------------------------------------------------
# 4 backend functions — copy CHÍNH XÁC từ main.py
# -----------------------------------------------------------------------------

def _real_autonomy_tts(voice, text, on_done=None):
    """Send text to voice.say and poll voice.snapshot() to fire
    on_done() after playback ends.

    Polling (rather than a callback) is the safest way to wire into
    the existing voice engine, which has no on_done hook. Polling
    at 100ms is light; the worst case is a 30s timeout that still
    calls on_done so lipsync_stop is guaranteed.
    """
    if voice is None:
        if on_done is not None:
            try:
                on_done()
            except Exception:
                pass
        return
    try:
        if not text or not text.strip():
            if on_done is not None:
                on_done()
            return
        voice.say(text)
    except Exception as exc:
        print(f"  [autonomy] TTS enqueue failed: {exc}")
        if on_done is not None:
            try:
                on_done()
            except Exception:
                pass
        return
    if on_done is None:
        return
    # Polling thread
    def _poller():
        deadline = time.time() + 30.0
        while time.time() < deadline:
            try:
                snap = voice.snapshot() if voice else None
                if snap is not None and not snap.get("speaking"):
                    try:
                        on_done()
                    except Exception:
                        pass
                    return
            except Exception:
                # snapshot raised; treat as 'idle' to be safe
                try:
                    on_done()
                except Exception:
                    pass
                return
            time.sleep(0.1)
        # Timeout fallback: still call on_done so lipsync stops.
        try:
            on_done()
        except Exception:
            pass
    threading.Thread(target=_poller, daemon=True, name="autonomy-tts-poller").start()


def _real_autonomy_lipsync_stop(voice):
    """Force the lipsync mouth value to 0.0 so Nana stops moving her
    mouth. Critical for VTS-only payloads where there is no TTS to
    drive on_done.
    """
    if voice is None:
        return
    try:
        stop_if_idle = getattr(voice, "stop_lipsync_if_idle", None)
        if callable(stop_if_idle):
            stop_if_idle()
            return
        snapshot = voice.snapshot() if callable(getattr(voice, "snapshot", None)) else {}
        if snapshot.get("speaking") or snapshot.get("listening"):
            return
        lipsync = getattr(voice, "lipsync", None)
        if lipsync is not None:
            lipsync.stop()
    except Exception as exc:
        print(f"  [autonomy] lipsync_stop failed: {exc}")


def _real_autonomy_vts(hotkey_id):
    """Trigger a VTS hotkey via the existing VTS runtime. The async
    request is scheduled on the running event loop; autonomy never
    blocks waiting for VTS to reply.
    """
    try:
        vts = get_vts_runtime()
        if vts is None:
            return
        if not getattr(vts, "connected", False):
            return
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(vts.request({
                    "apiName": "VTubeStudioPublicAPI",
                    "apiVersion": "1.0",
                    "requestID": f"autonomy_hotkey_{hotkey_id}_{int(time.time()*1000)}",
                    "messageType": "HotkeyTriggerRequest",
                    "data": {"hotkeyID": hotkey_id},
                }))
            else:
                # No running loop (rare in main path): skip silently.
                pass
        except RuntimeError:
            # No event loop in this thread; autonomy runs in a daemon
            # thread. We catch here rather than crashing the loop.
            pass
    except Exception as exc:
        print(f"  [autonomy] VTS hotkey {hotkey_id} failed: {exc}")


def _real_autonomy_subtitle(text):
    """Write a single line to the public subtitle stream. In A4 we
    just log; a real OBS-readable file write is wired in a later
    phase. We deliberately do not raise; the autonomy loop should
    never block on subtitle IO.
    """
    try:
        from nana.log_event import log_event
        log_event("autonomy_subtitle", text or "")
    except Exception:
        pass


# -----------------------------------------------------------------------------
# Wiring function — copy CHÍNH XÁC từ main.py dòng 41775-41790
# -----------------------------------------------------------------------------

def wire_autonomy_backends(voice, vts, loop, autonomy_loop, autonomy_express=None):
    """
    Wire autonomy express với real implementations.
    Tương đương block dòng 41775-41790 trong main.py.

    Args:
        voice: VoiceEngine instance
        vts: ManagedVTS instance (currently unused here, but kept for signature parity)
        loop: asyncio event loop
        autonomy_loop: AutonomyLoop instance (currently unused here, but kept for signature parity)
        autonomy_express: AutonomyExpress instance. If None, uses AUTONOMY_LOOP.express.
    """
    # Wire real backends into the autonomy express (Phase A4).
    # These backends are best-effort: every one of them catches
    # its own exceptions, so a misbehaving backend can never
    # crash the autonomy thread.
    express = autonomy_express if autonomy_express is not None else AUTONOMY_LOOP.express

    try:
        express.set_tts(
            lambda text, on_done=None: _real_autonomy_tts(voice, text, on_done)
        )
        express.set_vts(_real_autonomy_vts)
        express.set_subtitle(_real_autonomy_subtitle)
        express.set_lipsync_stop(
            lambda: _real_autonomy_lipsync_stop(voice)
        )
        print("  [autonomy] Backends wired: TTS, VTS, subtitle, lipsync_stop.")
    except Exception as exc:
        print(f"  [autonomy] backend wire failed: {exc}")
