"""Real bridge/session/reducer with fake responder and real temporary outbox."""
from __future__ import annotations

import importlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import types
from unittest.mock import patch

from smoke_memory_v2_phase1 import _isolated_nana_imports, _module


def _setup(directory):
    def result(text="", **extra):
        return types.SimpleNamespace(text=text, actions=[], max_chars=420, violations=[],
                                     was_firewalled=False, to_dict=lambda: {}, **extra)
    # External style/diagnostic owners are fake; the bridge/session under test are real.
    fakes = {
        "public_fallback_recovery": dict(recover_public_fallback=lambda **kw: result()),
        "public_fluency_polish": dict(polish_public_vietnamese=lambda text, **kw: result(text)),
        "public_memory_filter": dict(get_public_memory_filter=lambda: types.SimpleNamespace(snapshot=lambda: {})),
        "public_quality": dict(get_public_quality_guard=lambda: types.SimpleNamespace(guard_reply=lambda text, **kw: result(text))),
        "public_reply_evaluator": dict(evaluate_public_reply=lambda *a, **kw: result()),
        "public_stage_identity": dict(get_public_stage_identity_guard=lambda: types.SimpleNamespace(
            public_command_firewall=lambda *a, **kw: None,
            rewrite_public_stage_reply=lambda text, **kw: result(text))),
        "stream_event_timeline": dict(record_stream_event=lambda *a, **kw: None),
        "avatar_event_bridge": dict(get_avatar_event_bridge=lambda: types.SimpleNamespace(observe_public_event=lambda **kw: result())),
        "avatar_live_hook": dict(get_avatar_live_hook=lambda: types.SimpleNamespace(evaluate_latest=lambda **kw: None)),
    }
    for name, values in fakes.items():
        full = "nana.runtime." + name
        sys.modules[full] = _module(full, **values)
    for name in ("nana.runtime.viewer_chat", "nana.runtime.livestream_identity"):
        sys.modules.pop(name, None)
    bridge = importlib.import_module("nana.runtime.external_bridge")
    social = importlib.import_module("nana.runtime.social_session")
    viewer = importlib.import_module("nana.runtime.viewer_chat")
    bridge._repair_public_core_self_reply = lambda text, *a: text
    bridge._safe_public_full_reply_polish = lambda text, **kw: text
    cache = social.SocialSessionCache(priority_viewers=())
    runtime = bridge.ExternalBridgeRuntime(request_dir=directory / "requests", reply_dir=directory / "replies",
        queue=viewer.ViewerChatQueue(priority_viewers=()), social_session=cache, enabled=True)
    return bridge, runtime, cache


def _request(bridge, request_id="req-1", event="e1", room="room-A", actor="actor-opaque", session="session-1"):
    return bridge.ExternalBridgeRequest.from_payload({
        "request_id": request_id, "source": "youtube", "text": "VIEWER_INPUT_"+room,
        "author_id": actor, "author_name": "SameName", "channel_id": room,
        "metadata": {"event_id": event, "stream_session_id": session},
    })


def _receipt(record, state, revision):
    return {"event_id": record.event_id, "output_id": record.output_id,
            "attempt_id": record.attempt_id, "revision": revision, "state": state,
            "platform": record.scope.platform, "room_id": record.scope.room_id,
            "stream_session_id": record.scope.stream_session_id, "timestamp": record.updated_at + 1}


def test_real_process_and_outbox_require_delivery():
    with _isolated_nana_imports(), tempfile.TemporaryDirectory() as raw:
        bridge, runtime, cache = _setup(Path(raw))
        request = _request(bridge)
        reply = runtime.process_request(request, responder=lambda req: "REPLY_UNTIL_CONFIRMED")
        assert reply["ok"] and reply["reply_text"] == "REPLY_UNTIL_CONFIRMED", reply
        scope = runtime._canonical_scope(request)
        assert "VIEWER_INPUT_room-A" in cache.format_public_room_context(scope)
        assert "REPLY_UNTIL_CONFIRMED" not in cache.format_public_room_context(scope)
        runtime._write_reply(request.request_id, reply)
        published = runtime._output_records[request.request_id]
        assert published.state == "published"
        emitted = json.loads((Path(raw) / "replies" / "req-1.json").read_text(encoding="utf-8"))["metadata"]["delivery"]
        assert emitted["state"] == "published" and emitted["revision"] == published.revision
        assert emitted["room_id"] == scope.room_id and emitted["attempt_id"] == published.attempt_id
        assert "REPLY_UNTIL_CONFIRMED" not in cache.format_public_room_context(scope)
        assert not runtime.record_delivery_receipt(_receipt(published, "delivered", 3))
        assert runtime.record_delivery_receipt(_receipt(published, "playback_started", 3))
        assert "REPLY_UNTIL_CONFIRMED" not in cache.format_public_room_context(scope)
        started = runtime._output_records[request.request_id]
        assert not runtime.record_delivery_receipt({**_receipt(started, "delivered", 4), "room_id": "wrong"})
        delivered = _receipt(started, "delivered", 4)
        assert runtime.record_delivery_receipt(delivered)
        assert not runtime.record_delivery_receipt(delivered)
        assert cache.format_public_room_context(scope).count("REPLY_UNTIL_CONFIRMED") == 1
        assert not runtime.record_delivery_receipt(_receipt(started, "generated", 9))
        terminal = runtime._output_records[request.request_id]
        runtime._seen_event_keys.clear()  # Simulate dedupe TTL expiry while output still exists.
        replay = runtime.process_request(request, responder=lambda req: (_ for _ in ()).throw(AssertionError("replayed model")))
        runtime._write_reply(request.request_id, replay)
        assert runtime._output_records[request.request_id] is terminal


def test_duplicate_scope_and_failed_outbox():
    with _isolated_nana_imports(), tempfile.TemporaryDirectory() as raw:
        bridge, runtime, cache = _setup(Path(raw))
        calls = []
        def responder(request):
            calls.append(request.scope)
            return "PUBLIC_RESPONSE"
        one = _request(bridge)
        runtime.process_request(one, responder=responder)
        duplicate = runtime.process_request(_request(bridge, "retry", event="e1"), responder=responder)
        assert duplicate["reply_text"] == "" and len(calls) == 1
        two = _request(bridge, "req-2", event="e1", room="room-B", actor="different")
        reply = runtime.process_request(two, responder=responder)
        assert len(calls) == 2 and calls[0].identity.actor_key != calls[1].identity.actor_key
        assert "room-A" not in cache.format_public_room_context(calls[1])
        with patch.object(bridge, "_atomic_json_write", side_effect=OSError("injected")):
            try:
                runtime._write_reply(two.request_id, reply)
            except OSError:
                pass
            else:
                raise AssertionError("outbox fault hidden")
        assert runtime._output_records[two.request_id].state == "interrupted"
        assert "PUBLIC_RESPONSE" not in cache.format_public_room_context(calls[1])


def test_concurrent_receipts_stay_ordered_through_context():
    with _isolated_nana_imports(), tempfile.TemporaryDirectory() as raw:
        bridge, runtime, cache = _setup(Path(raw))
        request = _request(bridge)
        reply = runtime.process_request(request, responder=lambda req: "ORDERED_REPLY")
        runtime._write_reply(request.request_id, reply)
        published = runtime._output_records[request.request_id]
        reached, release, attempting = threading.Event(), threading.Event(), threading.Event()
        original = cache.record_reply_context
        def delayed(*args, **kwargs):
            if kwargs.get("delivery_record").state == "playback_started":
                reached.set()
                assert release.wait(3), "test synchronization timeout"
            return original(*args, **kwargs)
        failures = []
        def deliver_started():
            try:
                runtime.record_delivery_receipt(_receipt(published, "playback_started", 3))
            except Exception as exc:
                failures.append(exc)
        def deliver_done():
            attempting.set()
            runtime.record_delivery_receipt(_receipt(published, "delivered", 4))
        with patch.object(cache, "record_reply_context", delayed):
            first = threading.Thread(target=deliver_started)
            first.start()
            assert reached.wait(3)
            second = threading.Thread(target=deliver_done)
            second.start()
            assert attempting.wait(3)
            release.set()
            first.join(3); second.join(3)
            assert not first.is_alive() and not second.is_alive() and not failures
        assert "ORDERED_REPLY" in cache.format_public_room_context(published.scope)


def test_evaluation_is_room_and_session_scoped():
    with _isolated_nana_imports(), tempfile.TemporaryDirectory() as raw:
        bridge, runtime, cache = _setup(Path(raw))
        one = runtime._canonical_scope(_request(bridge))
        two = runtime._canonical_scope(_request(bridge, "req-2", room="room-B"))
        cache.record_public_turn(scope=one, text="first")
        cache.record_evaluation(one, {"grade": "weak", "issue_kinds": ["menu_loop"]})
        assert cache._partition(two)._last_public_eval == {"grade": "none", "issue_kinds": []}
        assert cache._partition(one)._last_public_eval["grade"] == "weak"
        new = runtime._canonical_scope(_request(bridge, "req-3", session="new-session"))
        cache.start_session(new)
        assert cache._partition(new)._last_public_eval["grade"] == "none"


if __name__ == "__main__":
    for test in (test_real_process_and_outbox_require_delivery, test_duplicate_scope_and_failed_outbox,
                 test_concurrent_receipts_stay_ordered_through_context, test_evaluation_is_room_and_session_scoped):
        test()
        print("PASS", test.__name__)
