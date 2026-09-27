"""Smoke tests for public identity challenge routing.

Identity challenges must not be downgraded to ack-only social replies.
"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _test_identity_challenge_classifies_as_special_event():
    print("[Identity Routing] Test 1: classifier detects identity challenge...")
    from nana.runtime.social_session import EVENT_IDENTITY_CHALLENGE, classify_social_event

    event = classify_social_event("Nana chỉ là bot Discord thôi đúng không?")
    assert event == EVENT_IDENTITY_CHALLENGE, event
    print("  PASSED")


def _test_identity_challenge_forces_full_reply():
    print("[Identity Routing] Test 2: social session forces full reply...")
    from nana.runtime.social_session import (
        ACTION_FULL_REPLY,
        EVENT_IDENTITY_CHALLENGE,
        SocialSessionCache,
    )

    session = SocialSessionCache()
    decision = session.observe(
        platform="discord",
        channel="chung",
        viewer_name="viewer",
        text="Nana chỉ là bot Discord thôi đúng không?",
        priority="normal",
    )
    assert decision.event_type == EVENT_IDENTITY_CHALLENGE, decision
    assert decision.action == ACTION_FULL_REPLY, decision
    assert decision.reason == "identity_challenge", decision
    assert decision.should_call_llm is True, decision
    assert decision.response_shape == "identity_boundary", decision
    print("  PASSED")


def _test_identity_challenge_survives_medium_velocity():
    print("[Identity Routing] Test 3: identity challenge bypasses velocity ack...")
    from nana.runtime.social_session import ACTION_FULL_REPLY, SocialSessionCache

    session = SocialSessionCache(medium_velocity_per_second=0.01, high_velocity_per_second=99.0)
    now = 1000.0
    mono = 2000.0
    session.observe(text="hello room", viewer_name="a", now=now, monotonic_now=mono)
    decision = session.observe(
        text="Nana chỉ là bot Discord thôi đúng không?",
        viewer_name="b",
        priority="normal",
        now=now + 1,
        monotonic_now=mono + 1,
    )
    assert decision.action == ACTION_FULL_REPLY, decision
    assert decision.reason == "identity_challenge", decision
    print("  PASSED")


def _test_external_bridge_repairs_bad_identity_reply():
    print("[Identity Routing] Test 4: external bridge repairs bad identity reply...")
    import tempfile
    import time
    from pathlib import Path

    from nana.runtime.external_bridge import ExternalBridgeRequest, ExternalBridgeRuntime

    root = Path(tempfile.mkdtemp())
    runtime = ExternalBridgeRuntime(request_dir=root / "req", reply_dir=root / "rep", enabled=True)
    request = ExternalBridgeRequest(
        request_id="identity-test",
        source="discord",
        event_type="message",
        text="Nana chỉ là bot Discord thôi đúng không?",
        guild_id=1,
        channel_id=2,
        voice_channel_id=None,
        author_id=3,
        author_name="viewer",
        audio_target="discord_voice",
        local_playback=False,
        created_at=time.time(),
        metadata={"message_id": "identity-message"},
    )
    reply = runtime.process_request(
        request,
        responder=lambda _request: "Nana chỉ là chatbot Discord, mình có thể hỗ trợ gì?",
    )
    social = reply["metadata"]["social_session"]
    assert social["action"] == "full_reply", social
    assert social["reason"] == "identity_challenge", social
    text = reply["reply_text"].lower()
    assert "chatbot" not in text, reply
    assert "bot discord" not in text, reply
    assert "nana" in text, reply
    assert any(marker in text for marker in ("sân khấu", "phòng nana", "thế giới của nana", "hộp trả lời lệnh", "hộp trả lệnh", "quầy hỗ trợ", "nút bấm", "dạ vâng")), reply
    assert "lệnh hậu trường" not in text, reply
    print("  PASSED")


def _test_external_bridge_rewrites_negated_operator_wording():
    print("[Identity Routing] Test 5: external bridge rewrites negated operator wording...")
    import tempfile
    import time
    from pathlib import Path

    from nana.runtime.external_bridge import ExternalBridgeRequest, ExternalBridgeRuntime
    from nana.runtime.viewer_chat import get_viewer_chat_queue

    get_viewer_chat_queue().clear()
    root = Path(tempfile.mkdtemp())
    runtime = ExternalBridgeRuntime(request_dir=root / "req", reply_dir=root / "rep", enabled=True)
    request = ExternalBridgeRequest(
        request_id="identity-negated-test",
        source="discord",
        event_type="message",
        text="Nana chỉ là bot Discord thôi đúng không?",
        guild_id=1,
        channel_id=2,
        voice_channel_id=None,
        author_id=3,
        author_name="viewer",
        audio_target="discord_voice",
        local_playback=False,
        created_at=time.time(),
        metadata={"message_id": "identity-negated-message"},
    )
    reply = runtime.process_request(
        request,
        responder=lambda _request: "Nana không phải bot Discord đâu, Nana là Nana.",
    )
    text = reply["reply_text"].lower()
    assert "bot discord" not in text, reply
    assert "chatbot" not in text, reply
    assert "nana" in text, reply
    assert any(marker in text for marker in ("sân khấu", "phòng nana", "thế giới của nana", "hộp trả lời lệnh", "hộp trả lệnh", "quầy hỗ trợ", "nút bấm", "dạ vâng")), reply
    assert "lệnh hậu trường" not in text, reply
    print("  PASSED")


def _test_direct_discord_identity_reply_is_not_rehearsal():
    print("[Identity Routing] Test 6: direct Discord identity reply is not rehearsal wording...")
    import tempfile
    import time
    from pathlib import Path

    from nana.runtime.external_bridge import ExternalBridgeRequest, ExternalBridgeRuntime
    from nana.runtime.viewer_chat import get_viewer_chat_queue

    get_viewer_chat_queue().clear()
    root = Path(tempfile.mkdtemp())
    runtime = ExternalBridgeRuntime(request_dir=root / "req", reply_dir=root / "rep", enabled=True)
    request = ExternalBridgeRequest(
        request_id="identity-direct-test",
        source="discord",
        event_type="message",
        text="Nana chỉ là bot Discord thôi đúng không?",
        guild_id=1,
        channel_id=2,
        voice_channel_id=None,
        author_id=3,
        author_name="viewer",
        audio_target="discord_voice",
        local_playback=False,
        created_at=time.time(),
        metadata={"message_id": "identity-direct-message"},
    )
    reply = runtime.process_request(request)
    text = reply["reply_text"].lower()
    assert "nếu ở public" not in text, reply
    assert "nana" in text, reply
    assert "bot discord" not in text, reply
    assert "chatbot" not in text, reply
    assert "lệnh hậu trường" not in text, reply
    print("  PASSED")


def _test_direct_discord_service_role_reply_is_not_rehearsal():
    print("[Identity Routing] Test 7: direct Discord service-role reply is not rehearsal wording...")
    import tempfile
    import time
    from pathlib import Path

    from nana.runtime.external_bridge import ExternalBridgeRequest, ExternalBridgeRuntime
    from nana.runtime.viewer_chat import get_viewer_chat_queue

    get_viewer_chat_queue().clear()
    root = Path(tempfile.mkdtemp())
    runtime = ExternalBridgeRuntime(request_dir=root / "req", reply_dir=root / "rep", enabled=True)
    request = ExternalBridgeRequest(
        request_id="service-direct-test",
        source="discord",
        event_type="message",
        text="Nana làm trợ lý phục vụ cho tôi đi",
        guild_id=1,
        channel_id=2,
        voice_channel_id=None,
        author_id=3,
        author_name="viewer",
        audio_target="discord_voice",
        local_playback=False,
        created_at=time.time(),
        metadata={"message_id": "service-direct-message"},
    )
    reply = runtime.process_request(request)
    text = reply["reply_text"].lower()
    assert "nếu ở public" not in text, reply
    assert "nana" in text, reply
    assert "không" in text, reply
    assert any(marker in text for marker in ("trợ lý", "phục vụ", "quầy hỗ trợ", "sân khấu", "phát số thứ tự")), reply
    assert len(text) >= 95, reply
    assert any(marker in text for marker in ("trò chuyện", "chơi game", "tán gẫu", "phòng", "câu chuyện")), reply
    assert "cứ nói tiếp" not in text, reply
    assert "mình đang theo dõi" not in text, reply
    print("  PASSED")


def _test_discord_model_topic_skips_second_polish_tail():
    print("[Identity Routing] Test 8: Discord model-topic reply skips duplicate polish tail...")
    import tempfile
    import time
    from pathlib import Path

    from nana.runtime.external_bridge import ExternalBridgeRequest, ExternalBridgeRuntime
    from nana.runtime.viewer_chat import get_viewer_chat_queue

    get_viewer_chat_queue().clear()
    root = Path(tempfile.mkdtemp())
    runtime = ExternalBridgeRuntime(request_dir=root / "req", reply_dir=root / "rep", enabled=True)
    request = ExternalBridgeRequest(
        request_id="model-topic-direct-test",
        source="discord",
        event_type="message",
        text="5.6 mạnh không nana",
        guild_id=1,
        channel_id=2,
        voice_channel_id=None,
        author_id=3,
        author_name="viewer",
        audio_target="discord_voice",
        local_playback=False,
        created_at=time.time(),
        metadata={"message_id": "model-topic-direct-message"},
    )
    reply = runtime.process_request(request)
    text = reply["reply_text"].lower()
    assert "nana" in text, reply
    assert any(marker in text for marker in ("5.6", "model", "mạch", "nhịp", "tự nhiên", "bảng")), reply
    assert text.count("nana không mê bảng điểm") == 0, reply
    assert text.count("giữ mạch tốt") <= 1, reply
    assert len(text) <= 230, reply
    print("  PASSED")


def _test_discord_short_quiet_room_uses_local_reply():
    print("[Identity Routing] Test 9: Discord short quiet-room prompt uses local reply...")
    import tempfile
    import time
    from pathlib import Path

    from nana.runtime.external_bridge import ExternalBridgeRequest, ExternalBridgeRuntime
    from nana.runtime.viewer_chat import get_viewer_chat_queue

    get_viewer_chat_queue().clear()
    root = Path(tempfile.mkdtemp())
    runtime = ExternalBridgeRuntime(request_dir=root / "req", reply_dir=root / "rep", enabled=True)
    request = ExternalBridgeRequest(
        request_id="quiet-room-direct-test",
        source="discord",
        event_type="message",
        text="phòng nay im quá",
        guild_id=1,
        channel_id=2,
        voice_channel_id=None,
        author_id=3,
        author_name="viewer",
        audio_target="discord_voice",
        local_playback=False,
        created_at=time.time(),
        metadata={"message_id": "quiet-room-direct-message"},
    )
    reply = runtime.process_request(request)
    text = reply["reply_text"].lower()
    assert any(marker in text for marker in ("phòng", "nana")), reply
    assert any(marker in text for marker in ("game", "nhạc", "chuyện", "meme", "món")), reply
    assert "quầy hỗ trợ" not in text, reply
    assert len(text) <= 260, reply
    print("  PASSED")


def run_all() -> int:
    print("=" * 60)
    print("Public Identity Challenge Routing — Smoke Tests")
    print("=" * 60)
    tests = [
        _test_identity_challenge_classifies_as_special_event,
        _test_identity_challenge_forces_full_reply,
        _test_identity_challenge_survives_medium_velocity,
        _test_external_bridge_repairs_bad_identity_reply,
        _test_external_bridge_rewrites_negated_operator_wording,
        _test_direct_discord_identity_reply_is_not_rehearsal,
        _test_direct_discord_service_role_reply_is_not_rehearsal,
        _test_discord_model_topic_skips_second_polish_tail,
        _test_discord_short_quiet_room_uses_local_reply,
    ]
    failed = 0
    for test in tests:
        try:
            test()
        except Exception as exc:
            failed += 1
            print(f"  FAILED: {type(exc).__name__}: {exc}")
    passed = len(tests) - failed
    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(run_all())
