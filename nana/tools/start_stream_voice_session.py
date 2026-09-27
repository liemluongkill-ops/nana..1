"""Operator entry for a bounded Nana Voice Host session; --demo is fully local."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import re
import signal
import sys
import tempfile
import types
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}\Z")
FLAGS = {
    "NANA_STREAM_CUM0_ENABLED": "1",
    "NANA_STREAM_CUM1_YOUTUBE_INGRESS_ENABLED": "1",
    "NANA_STREAM_CUM2_RESPONSE_ENABLED": "1",
    "NANA_STREAM_CUM3_YOUTUBE_OUTPUT_ENABLED": "0",
    "NANA_STREAM_CUM4_HOST_ENABLED": "1",
    "NANA_STREAM_CUM5_VOICE_PLAYBACK_ENABLED": "1",
    "NANA_MEMORY_SEMANTIC_RETRIEVAL_ENABLED": "0",
    "NANA_MEMORY_CONSOLIDATION_PREVIEW_ENABLED": "0",
    "NANA_MEMORY_PUBLIC_CROSS_SESSION_RECALL_ENABLED": "0",
    "NANA_MEMORY_PROMOTION_ENABLED": "0",
}
LABELS = {
    "connecting": "Dang ket noi", "waiting": "Dang cho chat",
    "thinking": "Dang nghi", "preparing_audio": "Dang tao giong noi",
    "speaking": "Dang noi", "stopping": "Dang dung - cho luot dang chay",
    "stopped": "Da dung", "error": "Loi - phien da dung",
}
REASONS = {
    "operator_stop": "Da dung theo yeu cau. Khong nhan them tin moi.",
    "session_timeout": "Da het thoi gian phien.",
    "turn_limit": "Da dat so cau tra loi toi da.",
    "poll_limit": "Da dat gioi han doc chat.",
    "provider_offline": "Livestream da ket thuc.",
    "live_chat_ended": "YouTube bao livestream/chat da ket thuc.",
    "live_chat_resolution_failed": "Chua doc duoc live chat. Kiem tra link, quyen truy cap va live dang bat.",
    "live_chat_unavailable": "Video chua co live chat dang hoat dong.",
    "youtube_quota_exceeded": "Da het han muc YouTube API. Phien dung, khong tu goi lai.",
    "youtube_rate_limited": "YouTube dang gioi han tan suat doc. Phien dung.",
    "youtube_unauthorized": "Credential doc YouTube khong con hop le.",
    "youtube_credentials_unavailable": "Thieu credential doc YouTube hoac credential da het han. Kiem tra nguon --read-auth da chon.",
    "youtube_forbidden": "Credential khong co quyen doc livestream nay.",
    "youtube_service_unavailable": "YouTube tam thoi khong phuc vu.",
    "runtime_setup_failed": "Chua khoi tao duoc phien. Kiem tra credential/runtime da cau hinh.",
    "session_already_running": "Da co mot Nana Voice Host dang chay. Dung cua so cu truoc.",
    "provider_error": "Model khong tra loi duoc. Phien da dung.",
    "provider_failed": "Model khong tra loi duoc. Phien da dung.",
    "demo_complete": "Demo hoan tat: ba cau hoi moi, khong phat lai tin trung.",
}


def parse_video_id(value: str) -> str:
    raw = str(value or "").strip()
    if VIDEO_ID.fullmatch(raw):
        return raw
    try:
        url = urlsplit(raw)
        if url.scheme not in {"http", "https"} or url.username or url.password or url.port:
            raise ValueError
        host = (url.hostname or "").lower()
        parts = url.path.strip('/').split('/')
        if host == "youtu.be" and len(parts) == 1:
            video = parts[0]
        elif host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
            if url.path == "/watch":
                ids = parse_qs(url.query).get("v", [])
                if len(ids) != 1:
                    raise ValueError
                video = ids[0]
            elif len(parts) == 2 and parts[0] in {"live", "shorts"}:
                video = parts[1]
            else:
                raise ValueError
        else:
            raise ValueError
        if not VIDEO_ID.fullmatch(video):
            raise ValueError
        return video
    except (ValueError, TypeError):
        raise ValueError("invalid_video_url") from None


@contextmanager
def session_flags(*, demo: bool = False):
    values = dict(FLAGS)
    if demo:
        values["NANA_STREAM_LOCAL_SIMULATOR_ENABLED"] = "1"
    saved = {key: os.environ.get(key) for key in values}
    try:
        os.environ.update(values)
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class SessionAlreadyRunning(RuntimeError):
    pass


class SessionLock:
    """One operator session per lock file; OS releases the lock on process exit."""

    def __init__(self, path: Path | None = None):
        self.path = path or Path(tempfile.gettempdir()) / "nana-voice-host-session.lock"
        self.file = None

    def __enter__(self):
        self.file = self.path.open("a+b")
        try:
            if self.file.seek(0, 2) == 0:
                self.file.write(b"0")
                self.file.flush()
            self.file.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            self.file = None
            raise SessionAlreadyRunning("session_already_running") from None
        return self

    def __exit__(self, *_args):
        if self.file is not None:
            self.file.close()
            self.file = None


@contextmanager
def graceful_signals(control):
    saved = {}
    def stop(_signum, _frame):
        control.request_stop("operator_stop")
    try:
        for name in ("SIGINT", "SIGBREAK"):
            number = getattr(signal, name, None)
            if number is not None:
                saved[number] = signal.signal(number, stop)
        yield
    finally:
        for number, handler in saved.items():
            signal.signal(number, handler)


def _namespace():
    sys.dont_write_bytecode = True
    if str(ROOT.parent) not in sys.path:
        sys.path.insert(0, str(ROOT.parent))
    for name in ("nana", "nana.runtime", "nana.tools"):
        if name not in sys.modules:
            package = types.ModuleType(name)
            package.__path__ = [str(ROOT.joinpath(*name.split('.')[1:]))]
            sys.modules[name] = package


def _guard_demo():
    """Fail closed if a future demo accidentally reaches production owners."""
    data = os.path.normcase(str(ROOT / "data")) + os.sep
    def guard(event, args):
        if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
            path = os.path.normcase(os.path.abspath(os.fsdecode(args[0])))
            if path.startswith(data) or os.path.basename(path) == ".env":
                raise PermissionError("demo_private_data_forbidden")
        if event in {"socket.connect", "socket.bind", "socket.sendto", "subprocess.Popen", "os.system"}:
            raise PermissionError("demo_external_io_forbidden")
    sys.addaudithook(guard)


def execute_session(args, video_id: str) -> dict:
    _namespace()
    if args.demo:
        _guard_demo()
    from nana.runtime.stream_session_control import StreamSessionControl
    from nana.tools.run_stream_voice_host import _LiveRuntimeSetupError, _build_live_runtime, run_bounded_voice_host
    previous_line = None
    def display(snapshot):
        nonlocal previous_line
        if args.json_status:
            print(json.dumps({"type": "session_status", **snapshot}, ensure_ascii=True), flush=True)
            return
        state = snapshot.get("state", "unknown")
        line = (f"{LABELS.get(state, state)} | Da tra loi: {snapshot.get('turns_delivered', 0)}"
                f" | Cho: {snapshot.get('queued', 0)} | Bo qua: {snapshot.get('skipped', 0)}"
                f" | Ly do: {snapshot.get('reason_code', snapshot.get('reason', ''))}")
        if line != previous_line:
            print(line, flush=True)
            previous_line = line
    control = StreamSessionControl(status_sink=display)
    observed = dict.fromkeys(("model_requests", "playback_dispatches", "tts_provider_requests", "local_audio_sink_writes"), 0)
    flags_before = {name: os.environ.get(name) for name in (*FLAGS, "NANA_STREAM_LOCAL_SIMULATOR_ENABLED")}
    with SessionLock(), session_flags(demo=args.demo), graceful_signals(control):
        control.stage("connecting")
        if args.demo:
            from nana.runtime.stream_session_demo import build_demo_runtime
            transport, factory, demo_evidence = build_demo_runtime(control, observed)
        else:
            demo_evidence = None
            try:
                transport, factory = _build_live_runtime(video_id=video_id, read_auth=args.read_auth,
                                                         observed_counts=observed, control=control)
            except _LiveRuntimeSetupError as exc:
                control.stage("error", reason_code=exc.reason_code)
                return {"status": "error", "reason_code": exc.reason_code, "evidence_level": "live_not_started"}
            except Exception:
                control.stage("error", reason_code="runtime_setup_failed")
                return {"status": "error", "reason_code": "runtime_setup_failed", "evidence_level": "live_not_started"}
        result = run_bounded_voice_host(video_id=video_id, transport=transport, voice_host_factory=factory,
                                        max_turns=args.max_turns, max_polls=args.max_polls,
                                        timeout_seconds=args.minutes * 60, observed_counts=observed,
                                        control=control)
        summary = result.to_dict()
        summary["evidence_level"] = "local_simulation" if args.demo else "owner_live"
        if demo_evidence is not None:
            summary.update(demo_evidence())
            if (summary["turns_delivered"] == 3 and summary["fake_playback_calls"] == 3
                    and summary.get("reason_code") in {"provider_offline", "live_chat_ended", "turn_limit"}):
                summary.update(status="completed", reason_code="demo_complete")
        summary["loaded_private_modules"] = [name for name in ("nana.memory", "nana.config") if name in sys.modules] if args.demo else []
    summary["flags_restored"] = all(os.environ.get(name) == value for name, value in flags_before.items())
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", nargs="?", help="YouTube live/watch URL or 11-character video ID")
    parser.add_argument("--demo", action="store_true", help="Local fake transport/model/audio; no account or speakers")
    parser.add_argument("--json-status", action="store_true")
    parser.add_argument("--max-turns", type=int, default=50)
    parser.add_argument("--minutes", type=float, default=30)
    parser.add_argument("--max-polls", type=int, default=3600)
    parser.add_argument("--read-auth", choices=("api-key", "oauth", "oauth-cache"), default="api-key")
    args = parser.parse_args(argv)
    if args.max_turns < 1 or args.max_polls < 1 or not math.isfinite(args.minutes) or args.minutes <= 0:
        print(json.dumps({"status": "rejected", "reason_code": "invalid_bounds"}))
        return 2
    value = args.video
    if not value and not args.demo:
        try:
            value = input("Dan link livestream YouTube (Enter de huy): ")
        except (EOFError, KeyboardInterrupt):
            return 0
        if not value.strip():
            return 0
    try:
        video_id = "DEMO0000001" if args.demo else parse_video_id(value)
    except ValueError:
        print(json.dumps({"status": "rejected", "reason_code": "invalid_video_url"}))
        return 2
    if not args.json_status:
        print("Nana Voice Host | Ctrl+C: dung nhan viec moi, cho luot dang noi ket thuc.", flush=True)
        print("OBS do ong dieu khien rieng. Demo: khong co am thanh that." if args.demo else
              "OBS thu cua so Nana Voice Host. Dung Nana khong dung OBS.", flush=True)
    try:
        summary = execute_session(args, video_id)
    except SessionAlreadyRunning:
        summary = {"status": "blocked", "reason_code": "session_already_running"}
    except Exception:
        summary = {"status": "error", "reason_code": "session_setup_failed"}
    if not args.json_status:
        reason = summary.get("reason_code", "unknown")
        print(REASONS.get(reason, f"Phien ket thuc: {reason}. Xem ket qua ben duoi."), flush=True)
    print(json.dumps(summary, ensure_ascii=True), flush=True)
    benign = {"operator_stop", "session_timeout", "timeout", "timeout_before_next_poll", "poll_limit", "demo_complete"}
    return 0 if summary.get("status") == "completed" or summary.get("reason_code") in benign else 2


if __name__ == "__main__":
    raise SystemExit(main())
