"""Nana application — main() orchestration."""
import asyncio
from dataclasses import dataclass, field
import os
import threading
import time
from uuid import uuid4

from nana.autonomy import AUTONOMY_EXPRESS, AUTONOMY_LOOP
from nana.runtime.context import context_lock, context_state
from nana.runtime.logger import log_event
from nana.runtime.startup_config_contract import (
    build_startup_config_contract,
    format_startup_config_line,
)

from nana.browser.vision import VisionPreviewer
from nana.cli.globals import set_runtime_turn_state

vision_previewer = VisionPreviewer()


def _create_voice_engine():
    from nana.voice.engine import VoiceEngine

    return VoiceEngine()


def _get_vts_runtime():
    from nana.integrations.vts import get_vts_runtime

    return get_vts_runtime()


def _start_external_bridge_worker(loop):
    from nana.runtime.external_bridge import start_external_bridge_worker

    return start_external_bridge_worker(loop)


def _stop_external_bridge_worker():
    from nana.runtime.external_bridge import stop_external_bridge_worker

    return stop_external_bridge_worker()


def _get_avatar_gateway():
    from nana.runtime.avatar_intent_gateway import get_avatar_intent_gateway

    return get_avatar_intent_gateway()


def _start_avatar_gateway():
    gateway = _get_avatar_gateway()
    if not gateway.enabled:
        return None
    if not gateway.start():
        raise RuntimeError("avatar_gateway_start_failed")
    return gateway


def _stop_avatar_gateway(gateway=None):
    owner = gateway if gateway is not None else _get_avatar_gateway()
    if owner is not None:
        owner.stop()


def _get_nana_web_launcher():
    from nana.runtime.nana_web_launcher import get_nana_web_launcher

    return get_nana_web_launcher()


def _start_nana_web_launcher(launcher=None):
    from nana.runtime.nana_web_launcher import format_nana_web_result

    owner = launcher if launcher is not None else _get_nana_web_launcher()
    result = owner.ensure_open()
    print(format_nana_web_result(result))
    return owner


def _stop_nana_web_launcher(launcher=None):
    owner = launcher if launcher is not None else _get_nana_web_launcher()
    if owner is not None:
        owner.close()


def _nonnegative_env_float(name, default):
    try:
        return max(0.0, float(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return float(default)


BRIDGE_SHUTDOWN_GRACE_SECONDS = 1.0
# LLMGate defaults to a 60s request timeout; leave bounded shutdown headroom.
AUTONOMY_STOP_JOIN_TIMEOUT_SECONDS = _nonnegative_env_float(
    "NANA_AUTONOMY_STOP_JOIN_TIMEOUT_S",
    65.0,
)
POLLER_JOIN_TIMEOUT_SECONDS = _nonnegative_env_float(
    "NANA_AUTONOMY_POLLER_JOIN_TIMEOUT_S",
    2.0,
)


@dataclass
class _RuntimeHandles:
    """Top-level handles owned by the application lifecycle."""

    pulse_task: asyncio.Task | None = None
    vts_mouth_task: asyncio.Task | None = None
    bridge_task: asyncio.Task | None = None
    avatar_gateway: object | None = None
    nana_web_launcher: object | None = None
    presence_session_task: asyncio.Task | None = None
    presence_session_server: object | None = None
    poller_threads: set[threading.Thread] = field(default_factory=set)
    shutdown_event: threading.Event = field(default_factory=threading.Event)
    poller_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    shutdown_lock: asyncio.Lock | None = field(default=None, repr=False)
    shutdown_complete: bool = False
    startup_config: object | None = None
    private_web_chat: object | None = None
    private_voice_receipts: object | None = None


from nana.runtime.private_web_chat_runtime import _build_private_web_chat_runtime



# ------------------------------------------------------------------
# Autonomy backend helpers (copied from main.py lines 305-430)
# ------------------------------------------------------------------

def _once(callback):
    if callback is None:
        return lambda: None
    lock = threading.Lock()
    called = False

    def _wrapped():
        nonlocal called
        with lock:
            if called:
                return
            called = True
        try:
            callback()
        except Exception:
            pass

    return _wrapped


def _real_autonomy_tts(voice, text, on_done=None, *, runtime_handles=None):
    finish_once = _once(on_done)
    if runtime_handles is not None and runtime_handles.shutdown_event.is_set():
        finish_once()
        return None
    if voice is None:
        finish_once()
        return None
    try:
        if not text or not text.strip():
            finish_once()
            return None
        voice.say(text)
    except Exception as exc:
        print(f"  [autonomy] TTS enqueue failed: {exc}")
        finish_once()
        return None
    from nana.runtime.avatar_intent_gateway import publish_reply_avatar

    publish_reply_avatar(text, source="autonomy_reply")
    if on_done is None:
        return None

    def _poller():
        try:
            deadline = time.time() + 30.0
            while time.time() < deadline:
                if runtime_handles is not None and runtime_handles.shutdown_event.is_set():
                    return
                try:
                    snap = voice.snapshot() if voice else None
                    if snap is not None and not snap.get("speaking"):
                        return
                except Exception:
                    return
                wait_seconds = min(0.1, max(0.0, deadline - time.time()))
                if runtime_handles is not None:
                    if runtime_handles.shutdown_event.wait(wait_seconds):
                        return
                else:
                    time.sleep(wait_seconds)
        finally:
            finish_once()

    thread = threading.Thread(target=_poller, daemon=True, name="autonomy-tts-poller")
    if runtime_handles is None:
        thread.start()
        return thread

    with runtime_handles.poller_lock:
        if runtime_handles.shutdown_event.is_set():
            finish_once()
            return None
        runtime_handles.poller_threads = {
            existing
            for existing in runtime_handles.poller_threads
            if existing.ident is None or existing.is_alive()
        }
        runtime_handles.poller_threads.add(thread)
        try:
            thread.start()
        except Exception:
            runtime_handles.poller_threads.discard(thread)
            finish_once()
            raise
    return thread


def _join_registered_pollers(
    runtime_handles,
    join_timeout_s=POLLER_JOIN_TIMEOUT_SECONDS,
):
    with runtime_handles.poller_lock:
        threads = tuple(runtime_handles.poller_threads)
    for thread in threads:
        if thread.ident is not None:
            thread.join(timeout=max(0.0, float(join_timeout_s)))
    with runtime_handles.poller_lock:
        runtime_handles.poller_threads = {
            thread for thread in runtime_handles.poller_threads if thread.is_alive()
        }
        return tuple(runtime_handles.poller_threads)


async def _cancel_and_await_tasks(*tasks):
    owned_tasks = [task for task in tasks if task is not None]
    for task in owned_tasks:
        if not task.done():
            task.cancel()
    if owned_tasks:
        await asyncio.gather(*owned_tasks, return_exceptions=True)


async def _shutdown_runtime(
    runtime_handles,
    *,
    voice,
    vts,
    autonomy_loop=None,
    stop_bridge=None,
    avatar_gateway=None,
    bridge_grace_seconds=BRIDGE_SHUTDOWN_GRACE_SECONDS,
    autonomy_join_timeout_s=AUTONOMY_STOP_JOIN_TIMEOUT_SECONDS,
    poller_join_timeout_s=POLLER_JOIN_TIMEOUT_SECONDS,
):
    if runtime_handles.shutdown_lock is None:
        runtime_handles.shutdown_lock = asyncio.Lock()

    async with runtime_handles.shutdown_lock:
        if runtime_handles.shutdown_complete:
            return True

        with runtime_handles.poller_lock:
            runtime_handles.shutdown_event.set()

        owner_loop = autonomy_loop if autonomy_loop is not None else AUTONOMY_LOOP
        bridge_stopper = stop_bridge if stop_bridge is not None else _stop_external_bridge_worker

        autonomy_stopped = False
        autonomy_error = None
        try:
            autonomy_stopped = bool(
                owner_loop.stop(join_timeout_s=autonomy_join_timeout_s)
            )
        except Exception as exc:
            autonomy_error = exc
            log_event("runtime", f"Autonomy shutdown failed: {exc}")

        try:
            bridge_stopper()
        except Exception as exc:
            log_event("runtime", f"External bridge stop signal failed: {exc}")

        try:
            _stop_avatar_gateway(
                avatar_gateway
                if avatar_gateway is not None
                else getattr(runtime_handles, "avatar_gateway", None)
            )
        except Exception as exc:
            log_event("runtime", f"Avatar gateway stop signal failed: {exc}")

        private_runtime = getattr(runtime_handles, "private_web_chat", None)
        if private_runtime is not None:
            try:
                await private_runtime.stop(
                    float(
                        getattr(
                            getattr(runtime_handles, "startup_config", None),
                            "private_web_chat_shutdown_drain_seconds",
                            15.0,
                        )
                    )
                )
            except Exception as exc:
                log_event("runtime", f"Private web chat shutdown failed: {exc}")

        nana_web_launcher = getattr(runtime_handles, "nana_web_launcher", None)
        if nana_web_launcher is not None:
            try:
                _stop_nana_web_launcher(nana_web_launcher)
            except Exception as exc:
                log_event("runtime", f"Nana Web shutdown failed: {exc}")

        presence_session_server = runtime_handles.presence_session_server
        if voice is not None:
            try:
                clear_presence_output = getattr(
                    voice,
                    "set_presence_pcm_output",
                    None,
                )
                if callable(clear_presence_output):
                    clear_presence_output()
            except Exception as exc:
                log_event("runtime", f"Presence PCM adapter cleanup failed: {exc}")
        if presence_session_server is not None:
            try:
                presence_session_server.stop()
            except Exception as exc:
                log_event(
                    "runtime",
                    f"Presence session server stop signal failed: {exc}",
                )
        try:
            from nana.runtime.presence_session_server import (
                set_active_presence_session_server,
            )

            set_active_presence_session_server(None)
        except Exception as exc:
            log_event("runtime", f"Presence session status cleanup failed: {exc}")

        await _cancel_and_await_tasks(
            runtime_handles.pulse_task,
            runtime_handles.vts_mouth_task,
            runtime_handles.presence_session_task,
        )

        bridge_task = runtime_handles.bridge_task
        if bridge_task is not None:
            if not bridge_task.done():
                done, _ = await asyncio.wait(
                    {bridge_task},
                    timeout=max(0.0, float(bridge_grace_seconds)),
                )
                if bridge_task not in done:
                    bridge_task.cancel()
            await asyncio.gather(bridge_task, return_exceptions=True)

        alive_pollers = _join_registered_pollers(
            runtime_handles,
            join_timeout_s=poller_join_timeout_s,
        )

        incomplete_reasons = []
        if not autonomy_stopped:
            if autonomy_error is not None:
                incomplete_reasons.append(
                    f"autonomy_stop_error={type(autonomy_error).__name__}: {autonomy_error}"
                )
            else:
                timeout_label = (
                    "unbounded"
                    if autonomy_join_timeout_s is None
                    else f"{float(autonomy_join_timeout_s):.3f}s"
                )
                incomplete_reasons.append(
                    f"autonomy_thread_alive_after_{timeout_label}"
                )
        if alive_pollers:
            names = ",".join(thread.name for thread in alive_pollers)
            incomplete_reasons.append(f"poller_threads_alive={names}")
        if incomplete_reasons:
            message = "Runtime shutdown incomplete: " + "; ".join(incomplete_reasons)
            log_event("runtime", message)
            raise RuntimeError(message)

        if voice is not None:
            try:
                voice.shutdown()
            except Exception as exc:
                log_event("runtime", f"Voice shutdown failed: {exc}")

        if vts is not None:
            try:
                await vts.close()
            except Exception as exc:
                log_event("runtime", f"VTS close failed: {exc}")

        runtime_handles.shutdown_complete = True
        return True


def _get_last_keystroke_time():
    with context_lock:
        return context_state.get("last_keystroke", 0.0)


def _real_autonomy_lipsync_stop(voice):
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
    try:
        vts = _get_vts_runtime()
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
        except RuntimeError:
            pass
    except Exception as exc:
        print(f"  [autonomy] VTS hotkey {hotkey_id} failed: {exc}")


def _real_autonomy_subtitle(text):
    try:
        log_event("autonomy_subtitle", text or "")
    except Exception:
        pass


def _autonomy_set_mode(mode: str) -> str:
    mode = (mode or "").strip().lower()
    if mode in ("ultra-short", "ultra_short", "ultrashort", "short"):
        AUTONOMY_EXPRESS.set_prefer_ultra_short(True)
        return "ultra_short"
    if mode in ("full", "normal", "all"):
        AUTONOMY_EXPRESS.set_prefer_ultra_short(False)
        return "full"
    return "unknown"


# ------------------------------------------------------------------
# async def main() — copied from main.py lines 41727-41863
# ------------------------------------------------------------------

async def main():
    startup_contract = build_startup_config_contract()
    print(format_startup_config_line(startup_contract))
    if startup_contract.status == "degraded":
        for warning in startup_contract.warnings:
            print(f"Startup config warning: {warning}")
    if not startup_contract.is_valid:
        for error in startup_contract.errors:
            print(f"Startup config error: {error}")
        return

    startup_config = startup_contract.snapshot

    from nana.autonomy.llm_banter import get_banter, init_banter
    from nana.autonomy.web_context import init_scraper
    from nana.cli.handle_text import handle_text
    from nana.integrations.vts import ensure_vts_ready, vts_mouth_loop
    from nana.runtime.presence_session_server import (
        PresenceCaptureOutcome,
        PresenceDisplayError,
        PresenceSessionServer,
        configure_presence_session_status,
        set_active_presence_session_server,
    )
    from nana.runtime.presence_audio_quality import evaluate_presence_audio
    from nana.runtime.pulse import nana_pulse

    global LAST_VISION_DESCRIPTION
    LAST_VISION_DESCRIPTION = None

    runtime_handles = _RuntimeHandles()
    runtime_handles.startup_config = startup_config
    voice = None
    vts = None

    try:
        voice = _create_voice_engine()
        vts = _get_vts_runtime()
        removed = vision_previewer.cleanup_preview_cache()
        if removed:
            print(f"Vision cache cleanup: removed {removed} old preview(s)")
        loop = asyncio.get_running_loop()
        turn_lock = asyncio.Lock()

        presence_face_by_audio_tag = {
            "happy": "happy",
            "sad": "pleading",
            "excited": "glee",
            "angry": "squint",
            "annoyed": "skeptical",
            "appalled": "shocked",
            "thoughtful": "pondering",
            "surprised": "surprised",
            "curious": "curious",
            "sarcastic": "skeptical",
            "confused": "curious",
            "nervous": "shy",
            "confidently": "proud",
            "crying": "pleading",
            "mischievously": "playful",
            "frustrated": "squint",
            "cute": "playful",
            "sympathetic": "pleading",
            "questioning": "curious",
            "reassuring": "relieved",
            "impressed": "awe",
            "delighted": "glee",
            "amazed": "awe",
            "alarmed": "shocked",
            "sheepishly": "shy",
            "panicking": "scared",
            "energetic": "glee",
            "relaxed": "relieved",
        }

        async def set_presence_face(tag, *, event=False):
            server = runtime_handles.presence_session_server
            if server is None or not server.display_available():
                return False
            try:
                if event:
                    await server.trigger_display_event_async(tag)
                else:
                    await server.set_display_state_async(tag)
                return True
            except PresenceDisplayError as exc:
                print(f"Presence display soft-fail: {exc}")
                return False

        def reply_face_from_voice_state():
            raw_tags = voice.snapshot().get("last_inline_tags") or ()
            tags = (raw_tags,) if isinstance(raw_tags, str) else tuple(raw_tags)
            for tag in tags:
                face = presence_face_by_audio_tag.get(str(tag))
                if face:
                    return face
            return "happy"

        async def dispatch_text_unlocked(dispatch_voice, text, *, observer=None):
            if observer is None:
                return await handle_text(vts, dispatch_voice, text, loop)
            return await handle_text(vts, dispatch_voice, text, loop, observer=observer)

        async def dispatch_text(dispatch_voice, text, *, observer=None):
            async with turn_lock:
                return await dispatch_text_unlocked(
                    dispatch_voice,
                    text,
                    observer=observer,
                )

        try:
            runtime_handles.nana_web_launcher = _get_nana_web_launcher()
            from nana.runtime.nana_web_ownership import NanaWebOwnershipVerifier

            launcher = runtime_handles.nana_web_launcher
            lease_provider = launcher.ownership_lease
            verifier = NanaWebOwnershipVerifier(
                current_core_boot_id=lambda: launcher.core_boot_id,
                active_lease_provider=lease_provider,
            )
            async def private_dispatch(text, observer):
                return await dispatch_text_unlocked(
                    voice,
                    text,
                    observer=observer,
                )

            runtime_handles.private_web_chat = _build_private_web_chat_runtime(
                enabled=bool(startup_config.private_web_chat_enabled),
                voice=voice,
                turn_lock=turn_lock,
                dispatch_turn_unlocked=private_dispatch,
                launcher=launcher,
                ownership_verifier=verifier,
                server_epoch=launcher.core_boot_id,
                host=startup_config.private_web_chat_host,
                port=startup_config.private_web_chat_port,
                lock_wait_seconds=startup_config.private_web_chat_lock_wait_seconds,
                turn_timeout_seconds=startup_config.private_web_chat_turn_timeout_seconds,
                shutdown_drain_seconds=startup_config.private_web_chat_shutdown_drain_seconds,
                loop=loop,
            )
            if runtime_handles.private_web_chat is not None:
                await runtime_handles.private_web_chat.start()
        except Exception as exc:
            print(f"Private web chat startup soft-fail: {type(exc).__name__}")
        try:
            _start_nana_web_launcher(runtime_handles.nana_web_launcher)
        except Exception as exc:
            print(f"Nana Web startup soft-fail: {type(exc).__name__}")

        async def handle_presence_capture(capture):
            turn_started = time.perf_counter()
            gate_ms = None
            stt_ms = None
            core_ms = None
            voice_ms = None
            llm_metrics = None
            outcome = "failed:exception"
            try:
                phase_started = time.perf_counter()
                quality = await loop.run_in_executor(
                    None,
                    lambda: evaluate_presence_audio(
                        capture.pcm,
                        sample_rate=capture.sample_rate,
                        sample_width=capture.sample_width,
                        firmware_noise_dbfs=capture.noise_dbfs,
                    ),
                )
                gate_ms = (time.perf_counter() - phase_started) * 1000.0
                gate_label = "PASS" if quality.accepted else "REJECT"
                print(
                    "Presence Session audio gate: "
                    f"{gate_label} | score={quality.score:.2f} | "
                    f"reason={quality.reason} | speech={quality.speech_ms:.0f}ms/"
                    f"{quality.speech_ratio:.0%} | snr={quality.snr_db:.1f}dB | "
                    f"peak={quality.peak_dbfs:.1f}dBFS"
                )
                if not quality.accepted:
                    await set_presence_face("mic_unclear", event=True)
                    await set_presence_face("listening")
                    outcome = f"skipped:{quality.reason}"
                    return PresenceCaptureOutcome("skipped", quality.reason)

                await set_presence_face("core_loading")
                try:
                    phase_started = time.perf_counter()
                    transcription = await loop.run_in_executor(
                        None,
                        lambda: voice.transcribe_pcm_detailed(
                            capture.pcm,
                            sample_rate=capture.sample_rate,
                            sample_width=capture.sample_width,
                        ),
                    )
                    stt_ms = (time.perf_counter() - phase_started) * 1000.0
                    if not transcription.text:
                        await set_presence_face("mic_unclear", event=True)
                        outcome = "skipped:stt_empty"
                        return PresenceCaptureOutcome("skipped", "stt_empty")
                    if (
                        transcription.confidence is not None
                        and transcription.confidence < 0.25
                    ):
                        await set_presence_face("mic_unclear", event=True)
                        outcome = "skipped:stt_low_confidence"
                        return PresenceCaptureOutcome(
                            "skipped", "stt_low_confidence"
                        )

                    print(f"Ba noi (Presence Session): {transcription.text}")
                    before_ticket = voice.latest_voice_ticket()
                    llm_sequence_before = 0
                    try:
                        from nana.brain.llmgate_client import llmgate_transport_snapshot

                        llm_sequence_before = int(
                            llmgate_transport_snapshot().get("sequence") or 0
                        )
                    except Exception:
                        pass
                    phase_started = time.perf_counter()
                    should_exit = await dispatch_text(voice, transcription.text)
                    core_ms = (time.perf_counter() - phase_started) * 1000.0
                    try:
                        from nana.brain.llmgate_client import llmgate_transport_snapshot

                        candidate = llmgate_transport_snapshot()
                        if int(candidate.get("sequence") or 0) > llm_sequence_before:
                            llm_metrics = candidate
                    except Exception:
                        pass
                    reply_ticket = voice.latest_voice_ticket()
                    if reply_ticket <= before_ticket:
                        status = "complete" if should_exit else "skipped"
                        outcome = f"{status}:no_voice_reply"
                        return PresenceCaptureOutcome(status, "no_voice_reply")

                    await set_presence_face(reply_face_from_voice_state())
                    phase_started = time.perf_counter()
                    drained = await loop.run_in_executor(
                        None,
                        lambda: voice.wait_for_voice_ticket(
                            reply_ticket, timeout=600.0
                        ),
                    )
                    voice_ms = (time.perf_counter() - phase_started) * 1000.0
                    if not drained:
                        await set_presence_face("mic_unclear", event=True)
                        outcome = "failed:voice_drain_timeout"
                        return PresenceCaptureOutcome(
                            "failed", "voice_drain_timeout"
                        )
                    voice_status = voice.snapshot()
                    if voice_status.get("last_presence_pcm_status") == "failed":
                        await set_presence_face("mic_unclear", event=True)
                        outcome = "failed:playback_failed"
                        return PresenceCaptureOutcome("failed", "playback_failed")
                    outcome = "complete:reply_drained"
                    return PresenceCaptureOutcome("complete", "reply_drained")
                finally:
                    await set_presence_face("listening")
            finally:
                total_ms = (time.perf_counter() - turn_started) * 1000.0
                try:
                    voice_status = voice.snapshot()
                except Exception:
                    voice_status = {}

                def timing(value):
                    if value is None:
                        return "n/a"
                    try:
                        return f"{float(value):.0f}ms"
                    except (TypeError, ValueError):
                        return "n/a"

                print(
                    "Presence interaction timing: "
                    f"gate={timing(gate_ms)} | stt={timing(stt_ms)} | "
                    f"core={timing(core_ms)} | voice={timing(voice_ms)} | "
                    f"llm_model={(llm_metrics or {}).get('model', 'none')} | "
                    f"llm_prompt={(llm_metrics or {}).get('prompt_chars', 0)}ch | "
                    f"llm_first_text={timing((llm_metrics or {}).get('first_text_ms'))} | "
                    f"llm_total={timing((llm_metrics or {}).get('total_ms'))} | "
                    f"tts_audio={timing(voice_status.get('last_presence_pcm_provider_duration_ms'))} | "
                    f"tts_first_byte={timing(voice_status.get('last_presence_pcm_first_byte_ms'))} | "
                    f"provider_wait={timing(voice_status.get('last_presence_pcm_max_network_gap_ms'))} | "
                    f"prefetch={voice_status.get('last_presence_pcm_prefetch_high_water', 0)}/"
                    f"{voice_status.get('presence_pcm_stream_prefetch_chunks', 0)} "
                    f"starved={voice_status.get('last_presence_pcm_prefetch_starvations', 0)} | "
                    f"first_audio={timing(voice_status.get('last_presence_pcm_first_audio_ms'))} | "
                    f"playback={timing(voice_status.get('last_presence_pcm_elapsed_ms'))} | "
                    f"playback_grade={voice_status.get('last_presence_pcm_playback_grade', 'none')} | "
                    f"node_underruns={voice_status.get('last_presence_pcm_underruns')} | "
                    f"total={total_ms:.0f}ms | outcome={outcome}"
                )

        configure_presence_session_status(
            enabled=startup_config.presence_session_enabled,
            host=startup_config.presence_session_host,
            port=startup_config.presence_session_port,
            token_present=startup_config.presence_session_token_present,
            heartbeat_seconds=startup_config.presence_session_heartbeat_seconds,
            timeout_seconds=startup_config.presence_session_timeout_seconds,
        )
        # Wire the real observer into the autonomy loop (Phase A5)
        try:
            AUTONOMY_LOOP.observer.set_voice_snapshot_fn(lambda: voice.snapshot())
            AUTONOMY_LOOP.observer.wire(
                last_keystroke_fn=lambda: _get_last_keystroke_time(),
            )
            print("Autonomy observer wired: real runtime signals.")
        except Exception as exc:
            print(f"  [autonomy] observer wire failed: {exc}")

        # Phase A7: init the LLM banter generator and web context scraper
        try:
            import os
            init_scraper()
            init_banter()
            banter = get_banter()
            llm_state = "on" if banter.is_enabled() else "off"
            print(
                f"Autonomy LLM banter wired: generator={llm_state} | "
                f"auto_output={'on' if startup_config.autonomy_auto_output_enabled else 'off'} | "
                f"model={startup_config.autonomy_model}"
            )
            print(
                "Web context scraper wired: "
                "browser snapshots feed LLM context + boost relevance."
            )
        except Exception as exc:
            print(f"  [autonomy] LLM/web wire failed: {exc}")
            print("  [autonomy] banter will fall back to template pool.")

        # Wire real backends into the autonomy express (Phase A4)
        try:
            AUTONOMY_EXPRESS.set_tts(
                lambda text, on_done=None: _real_autonomy_tts(
                    voice,
                    text,
                    on_done,
                    runtime_handles=runtime_handles,
                )
            )
            AUTONOMY_EXPRESS.set_vts(_real_autonomy_vts)
            AUTONOMY_EXPRESS.set_subtitle(_real_autonomy_subtitle)
            AUTONOMY_EXPRESS.set_lipsync_stop(
                lambda: _real_autonomy_lipsync_stop(voice)
            )
            print("Autonomy backends wired: TTS, VTS, subtitle, lipsync_stop.")
        except Exception as exc:
            print(f"  [autonomy] backend wire failed: {exc}")

        # Start the autonomy loop (Phase A5)
        try:
            start_paused = not startup_config.autonomy_auto_output_enabled
            AUTONOMY_LOOP.start(paused=start_paused)
            if start_paused:
                print(
                    "Autonomy loop started paused. Idle autonomous output is off; "
                    "use /autonomy-resume to enable it for this session."
                )
            else:
                print("Autonomy loop started. Use /autonomy-pause to silence idle banter.")
        except Exception as exc:
            print(f"  [autonomy] start failed: {exc}")

        if startup_config.vts_startup_enabled:
            status = await ensure_vts_ready(force=True, announce=False)
            if status["ready"]:
                print("VTS startup ready.")
            else:
                log_event("vts", f"Startup VTS soft-fail: {status['error']}")
                print(f"VTS startup soft-fail: {status['error_type']}: {status['error']}")
        else:
            log_event("vts", "Startup VTS skipped by NANA_VTS_STARTUP_ENABLED=0")
            print("VTS startup skip de giam delay. Bat NANA_VTS_STARTUP_ENABLED=1 neu can avatar.")

        if getattr(vts, 'connected', False):
            runtime_handles.vts_mouth_task = asyncio.create_task(
                vts_mouth_loop(vts, voice.lipsync),
                name="nana-vts-mouth",
            )
        runtime_handles.pulse_task = asyncio.create_task(
            nana_pulse(vts, loop, voice),
            name="nana-pulse",
        )
        try:
            runtime_handles.bridge_task = _start_external_bridge_worker(loop)
            if runtime_handles.bridge_task is not None:
                print("External bridge worker wired: Discord/file requests -> core viewer queue -> text reply JSON.")
            else:
                print("External bridge worker disabled by NANA_EXTERNAL_BRIDGE_ENABLED=0.")
        except Exception as exc:
            print(f"External bridge worker soft-fail: {type(exc).__name__}: {exc}")

        try:
            runtime_handles.avatar_gateway = _start_avatar_gateway()
            if runtime_handles.avatar_gateway is not None:
                gateway_snapshot = runtime_handles.avatar_gateway.snapshot()
                print(
                    "Avatar intent gateway wired: "
                    f"listen={gateway_snapshot['host']}:{gateway_snapshot['port']} | "
                    f"transport={gateway_snapshot['transport']} | "
                    "semantic-only"
                )
        except Exception as exc:
            print(f"Avatar intent gateway soft-fail: {type(exc).__name__}: {exc}")

        if startup_config.presence_session_enabled:
            try:
                presence_session_server = PresenceSessionServer(
                    host=startup_config.presence_session_host,
                    port=startup_config.presence_session_port,
                    token=os.environ["NANA_PRESENCE_SESSION_TOKEN"],
                    heartbeat_seconds=(
                        startup_config.presence_session_heartbeat_seconds
                    ),
                    timeout_seconds=startup_config.presence_session_timeout_seconds,
                    audio_uplink_handler=handle_presence_capture,
                    initial_display_state="listening",
                )
                runtime_handles.presence_session_server = presence_session_server
                set_active_presence_session_server(presence_session_server)
                runtime_handles.presence_session_task = asyncio.create_task(
                    presence_session_server.run(),
                    name="nana-presence-session-server",
                )
                await presence_session_server.wait_started()
                if runtime_handles.presence_session_task.done():
                    await runtime_handles.presence_session_task
                voice.set_presence_pcm_output(
                    available_fn=presence_session_server.audio_downlink_available,
                    playback_fn=presence_session_server.play_pcm16,
                    stream_available_fn=(
                        presence_session_server.audio_downlink_stream_available
                    ),
                    stream_playback_fn=presence_session_server.play_pcm16_stream,
                )
                print(
                    "Presence session wired: "
                    f"listen={startup_config.presence_session_host}:"
                    f"{presence_session_server.bound_port} | "
                    "path=/presence/v1 | "
                    "authenticated bounded half-duplex PCM16 audio with "
                    "progressive stream + display events + one-shot RAM-only "
                    "camera snapshots."
                )
            except Exception as exc:
                try:
                    voice.set_presence_pcm_output()
                except Exception:
                    pass
                set_active_presence_session_server(None)
                runtime_handles.presence_session_server = None
                task = runtime_handles.presence_session_task
                if task is not None and not task.done():
                    task.cancel()
                runtime_handles.presence_session_task = None
                print(
                    "Presence session soft-fail: "
                    f"{type(exc).__name__}: {exc}"
                )

        chat_hotkey = "down"
        print("Giu ESC: voice | Mu ten xuong: chat | /run <cmd>: watch terminal | /status: debug | exit: thoat")
        last_chat_hotkey_time = 0

        import keyboard

        while True:
            if keyboard.is_pressed("esc"):
                set_runtime_turn_state("listening", "esc_voice", "voice")
                text = await loop.run_in_executor(None, voice.listen_voice)
                set_runtime_turn_state("idle", "voice_captured" if text else "voice_empty", "voice")
                while keyboard.is_pressed("esc"):
                    await asyncio.sleep(0.05)
                if not text:
                    await asyncio.sleep(0.2)
                    continue
                print(f"Ba noi: {text}")
                should_exit = await dispatch_text(voice, text)
                if should_exit:
                    break
                await asyncio.sleep(0.2)
                continue

            if keyboard.is_pressed(chat_hotkey) and time.time() - last_chat_hotkey_time > 0.5:
                last_chat_hotkey_time = time.time()
                while keyboard.is_pressed(chat_hotkey):
                    await asyncio.sleep(0.05)
                text = await loop.run_in_executor(None, input, "Chat: ")
                should_exit = await dispatch_text(voice, text)
                if should_exit:
                    break
                await asyncio.sleep(0.2)
                continue

            await asyncio.sleep(0.05)

    except asyncio.CancelledError:
        log_event("runtime", "Main cancelled")
        raise
    except Exception as exc:
        log_event("errors", f"Main error: {exc}")
        print(f"Main error: {exc}")
    finally:
        try:
            removed = vision_previewer.clear_preview_cache()
        except Exception as exc:
            removed = 0
            log_event("runtime", f"Vision cache cleanup failed: {exc}")
        LAST_VISION_DESCRIPTION = None
        if removed:
            print(f"Vision cache cleared on exit: {removed} file(s)")
        await _shutdown_runtime(
            runtime_handles,
            voice=voice,
            vts=vts,
            avatar_gateway=runtime_handles.avatar_gateway,
            autonomy_join_timeout_s=(
                startup_config.autonomy_stop_join_timeout_seconds
            ),
            poller_join_timeout_s=(
                startup_config.autonomy_poller_join_timeout_seconds
            ),
        )
        print("Nana tam biet!")
