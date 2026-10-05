"""Owner-operated text window for Task 6 real-provider shadow observation.

Uses the actual GPT entrypoints and existing provider configuration. No UI,
voice, watcher or livestream services start. New dialogue stays in RAM; only
metadata is exported. Production data writes are denied for this process.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import types
import uuid

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = ROOT / "docs" / "context_observations"
SESSION_FLAGS = {
    "NANA_CONTEXT_PRIVATE_MODE": "shadow",
    "NANA_CONTEXT_PUBLIC_GPT_MODE": "legacy",
    "NANA_CONTEXT_CUM2_MODE": "legacy",
    "NANA_CONTEXT_AUTONOMY_MODE": "legacy",
    "NANA_CONTEXT_BUDGET_POLICY_REVISION": "",
    "NANA_OPENAI_FALLBACK_ENABLED": "0",
    "NANA_MEMORY_SEMANTIC_RETRIEVAL_ENABLED": "0",
    "NANA_MEMORY_CONSOLIDATION_PREVIEW_ENABLED": "0",
    "NANA_MEMORY_PUBLIC_CROSS_SESSION_RECALL_ENABLED": "0",
    "NANA_MEMORY_PROMOTION_ENABLED": "0",
}


def _hash_messages(messages):
    projection = [[item["role"], item["content"]] for item in messages]
    return hashlib.sha256(json.dumps(
        projection, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")).hexdigest()


def _data_manifest():
    result = {}
    for name in ("memory.json", "chat_history.txt", "identity.json", "users.json"):
        path = ROOT / "data" / name
        result[name] = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
    return result


def install_write_guard(blocked):
    protected = (ROOT / "data", ROOT / "runtime_logs")

    def within(value):
        if not isinstance(value, (str, bytes, os.PathLike)):
            return False
        path = Path(os.fsdecode(value)).resolve()
        return any(path == parent or parent in path.parents for parent in protected)

    def audit(event, args):
        if event == "open" and args and within(args[0]):
            mode = args[1] if len(args) > 1 else None
            flags = args[2] if len(args) > 2 else 0
            writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
                isinstance(flags, int) and bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
            )
            if writing:
                blocked.append("production_write")
                raise PermissionError("observation is production-read-only")
        if event in {"os.remove", "os.rename", "os.rmdir", "os.mkdir"} and any(within(p) for p in args[:2]):
            blocked.append("production_mutation")
            raise PermissionError("observation is production-read-only")
        if event in {"subprocess.Popen", "os.system", "os.startfile"}:
            raise PermissionError("observation cannot start services")

    sys.addaudithook(audit)


def prepare_runtime(blocked):
    os.environ.update(SESSION_FLAGS)
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT.parent))
    # Avoid Nana root's eager autonomy/vision composition; source owners below
    # GPT remain real, with the existing persisted private state read normally.
    for name, directory in (("nana", ROOT), ("nana.runtime", ROOT / "runtime"),
                            ("nana.brain", ROOT / "brain")):
        module = types.ModuleType(name)
        module.__path__ = [str(directory)]
        sys.modules[name] = module
    install_write_guard(blocked)
    from nana import config
    if config.NANA_CHAT_PROVIDER != "llmgate":
        raise RuntimeError("observation_requires_existing_llmgate_config")
    from nana.runtime import logger
    logger.log_event = lambda *_a, **_k: None  # never export runtime error bodies
    from nana.runtime import mood_continuity
    observe_mood = mood_continuity.observe_mood_text

    def observe_ram(*args, **kwargs):
        kwargs["persist"] = False
        return observe_mood(*args, **kwargs)

    mood_continuity.observe_mood_text = observe_ram
    from nana.brain import gpt, llmgate_client
    from nana.runtime import context_shadow
    assert config.NANA_CONTEXT_PRIVATE_MODE == "shadow"
    return gpt, llmgate_client, context_shadow


class HttpObserver:
    def __init__(self, original, *, limit=30):
        self.original = original
        self.limit = limit
        self.rows = []

    def __call__(self, url, **kwargs):
        if len(self.rows) >= self.limit:
            raise RuntimeError("observation_request_limit")
        payload = kwargs.get("json", {})
        row = {
            "attempt": len(self.rows) + 1,
            "model": str(payload.get("model", "unknown")),
            "stream": bool(payload.get("stream", False)),
            "full_context_hash": _hash_messages(payload.get("messages", [])),
            "http_status": None,
        }
        self.rows.append(row)
        started = time.perf_counter()
        try:
            response = self.original(url, **kwargs)
            row["http_status"] = int(response.status_code)
            return response
        except Exception as exc:
            row["error_type"] = type(exc).__name__
            raise
        finally:
            row["headers_ms"] = round((time.perf_counter() - started) * 1000, 1)


def write_packet(path, *, state, started, turns, http_rows, shadow, before, blocked):
    after = _data_manifest()
    packet = {
        "schema_version": 1, "state": state, "pid": os.getpid(),
        "started_utc": started, "updated_utc": datetime.now(timezone.utc).isoformat(),
        "session_flags": SESSION_FLAGS, "turns": turns, "http_attempts": http_rows,
        "observations": [asdict(row) for row in shadow.shadow_telemetry_snapshot()],
        "distributions": [asdict(row) for row in shadow.shadow_distribution_snapshot()],
        "production_data_before": before, "production_data_after": after,
        "production_data_unchanged": before == after,
        "blocked_write_count": len(blocked),
        "limitations": ["text-only GPT entrypoints; no CLI side-effect pipeline",
                        "existing private memory read; new dialogue RAM-only",
                        "no live awareness poller or voice", "budget not approved"],
    }
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(packet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="preflight imports only; no provider request")
    parser.add_argument("--max-turns", type=int, default=10, choices=range(1, 21))
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    before = _data_manifest()
    blocked = []
    gpt, provider, shadow = prepare_runtime(blocked)
    if args.check:
        assert before == _data_manifest()
        print("READY: private=shadow; real_provider_calls=0; production_data_unchanged=true")
        return 0
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    session = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    path = OUTPUT_ROOT / (session + ".json")
    started = datetime.now(timezone.utc).isoformat()
    observer = HttpObserver(provider._http_post)
    provider._http_post = observer
    turns = []
    mode = "stream"
    story = False
    casual = False

    def flush(state):
        write_packet(path, state=state, started=started, turns=turns,
                     http_rows=observer.rows, shadow=shadow, before=before, blocked=blocked)

    flush("ready")
    print("\nNANA - TASK 6 SHADOW | text-only | model/config hiện có")
    print("Chat bình thường; /sync, /stream, /story, /normal, /casual; /done để kết thúc.")
    print("Không bật mic/TTS/live. Tin mới chỉ giữ RAM; file chỉ lưu metadata.")
    print(f"Evidence: {path}\n")
    try:
        while len(turns) < args.max_turns:
            text = input("Ba > ").strip()
            if not text:
                continue
            if text.casefold() in {"/done", "exit", "quit"}:
                break
            if text.casefold() in {"/sync", "/stream", "/story", "/normal", "/casual"}:
                command = text.casefold()
                if command in {"/sync", "/stream"}:
                    mode = command[1:]
                else:
                    story, casual = command == "/story", command == "/casual"
                print(f"Mode: {mode}; story={story}; casual={casual}")
                continue
            began = time.perf_counter()
            http_start = len(observer.rows)
            obs_start = len(shadow.shadow_telemetry_snapshot())
            reply = ""
            status = "complete"
            print("Nana > ", end="", flush=True)
            try:
                if mode == "sync":
                    reply = gpt.ask_gpt(text, story_mode=story, casual_mode=casual)
                    print(reply)
                else:
                    async def consume():
                        chunks = []
                        async for chunk in gpt.ask_gpt_stream(text, story_mode=story, casual_mode=casual):
                            chunks.append(chunk)
                            print(chunk, end="", flush=True)
                        return "".join(chunks)
                    reply = asyncio.run(consume())
                    print()
            except Exception as exc:
                status = "error_" + type(exc).__name__
                print(f"[Lượt này lỗi: {type(exc).__name__}; không ghi nội dung lỗi.]")
            observations = shadow.shadow_telemetry_snapshot()[obs_start:]
            attempts = observer.rows[http_start:]
            hashes = {row.sent_context.manifest.full_context_hash for row in observations}
            turns.append({
                "index": len(turns) + 1, "mode": mode, "story": story, "casual": casual,
                "status": status, "reply_chars": len(reply),
                "total_ms": round((time.perf_counter() - began) * 1000, 1),
                "provider_attempts": len(attempts), "shadow_observations": len(observations),
                "candidate_status": [row.candidate_status for row in observations],
                "sent_hash_matches_http": all(row["full_context_hash"] in hashes for row in attempts) if attempts else None,
            })
            # Same history line shape as Core; never persist this observation conversation.
            if reply and status == "complete":
                from nana.memory import memory, memory_lock
                from nana.runtime.history_privacy import redact_history_text
                with memory_lock:
                    memory["short_term"].extend(["ba: " + redact_history_text(text), "nana: " + redact_history_text(reply)])
                    memory["short_term"][:] = memory["short_term"][-12:]
            flush("observing")
            print(f"[shadow: {len(observations)}; HTTP: {[r['http_status'] for r in attempts]}; lượt {len(turns)}/{args.max_turns}]\n")
    except (EOFError, KeyboardInterrupt):
        print("\nKết thúc phiên shadow.")
    finally:
        flush("closed")
    print(f"Đã lưu metadata: {path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"SHADOW START FAILED: {type(error).__name__}; no credential details printed")
        raise SystemExit(1) from None
