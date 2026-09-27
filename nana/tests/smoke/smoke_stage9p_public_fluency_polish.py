"""Smoke tests for STAGE-9P public Vietnamese fluency polish."""

from __future__ import annotations

import io
from contextlib import redirect_stdout
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _test_typo_and_punctuation_polish():
    print("[9P Smoke] Test 1: typo and punctuation polish...")
    from nana.runtime.public_fluency_polish import polish_public_vietnamese

    result = polish_public_vietnamese(
        "Kết quả là mở đúng cái cửa sổ test. Nana đứng hình bạn giây như NPC bị lag. 5. 6 nghe cũng được à ?"
    )
    lowered = result.text.lower()
    assert result.phase == "STAGE-9P", result
    assert result.read_only is True and result.can_act is False, result
    assert result.memory_write is False and result.api_call is False, result
    assert "vài giây" in lowered, result
    assert "cửa sổ thử nghiệm" in lowered, result
    assert "5.6" in result.text, result
    assert "à?" in result.text, result
    assert result.changed is True, result
    print("  PASSED")


def _test_role_wording_and_meta_frame_polish():
    print("[9P Smoke] Test 2: awkward role wording and public meta frame are fixed...")
    from nana.runtime.public_fluency_polish import polish_public_vietnamese

    result = polish_public_vietnamese(
        'Nếu ở public, Nana sẽ nói thế này: "Nana không làm vai phục vụ. Muốn trò chuyện thì ngồi xuống."'
    )
    lowered = result.text.lower()
    assert "nếu ở public" not in lowered, result
    assert "nana sẽ nói thế này" not in lowered, result
    assert "không nhận vai phục vụ" in lowered, result
    assert result.changed is True, result
    print("  PASSED")


def _test_label_and_voice_tags_are_removed():
    print("[9P Smoke] Test 3: label prefixes and banned voice tags are removed...")
    from nana.runtime.public_fluency_polish import polish_public_vietnamese

    result = polish_public_vietnamese("**Casual response:** [softly] Nana kéo nhịp phòng lại nè. [laughs]")
    lowered = result.text.lower()
    assert "casual response" not in lowered, result
    assert "[softly]" not in lowered and "[laughs]" not in lowered, result
    assert "nana kéo nhịp" in lowered, result
    print("  PASSED")


def _test_bridge_metadata_and_reply_are_polished():
    print("[9P Smoke] Test 4: external bridge reply metadata includes fluency polish...")
    from nana.runtime.external_bridge import ExternalBridgeRequest, ExternalBridgeRuntime
    from nana.runtime.social_session import SocialSessionCache
    from nana.runtime.viewer_chat import ViewerChatQueue

    runtime = ExternalBridgeRuntime(
        enabled=True,
        queue=ViewerChatQueue(rate_limit_max=99),
        social_session=SocialSessionCache(priority_viewers=("linhcute2746",)),
    )
    request = ExternalBridgeRequest.from_payload(
        {
            "request_id": "9p-bridge-polish",
            "source": "discord",
            "event_type": "message",
            "text": "chuyện ngáo",
            "author_name": "linhcute2746",
            "metadata": {"message_id": "9p-bridge-polish", "route": {"chat_channel_name": "chung"}},
        }
    )
    reply = runtime.process_request(
        request,
        responder=lambda _request: "Nana đứng hình bạn giây như NPC bị lag. 5. 6 nghe cũng được à ?",
    )
    text = reply["reply_text"]
    fluency = reply["metadata"]["public_fluency"]
    assert "vài giây" in text, reply
    assert "5.6" in text and "à?" in text, reply
    assert fluency["phase"] == "STAGE-9P", fluency
    assert fluency["changed"] is True, fluency
    assert "typo" in fluency["actions"], fluency
    print("  PASSED")


def _test_status_help_firewall_stage_surface():
    print("[9P Smoke] Test 5: status/help/firewall/stage surface exists...")
    from nana.commands.help import print_command_help
    from nana.core.status import print_stage_status
    from nana.runtime.public_fluency_polish import public_fluency_preview_lines, public_fluency_status_lines
    from nana.runtime.public_stage_identity import get_public_stage_identity_guard

    status = "\n".join(public_fluency_status_lines())
    preview = "\n".join(public_fluency_preview_lines("Nana đứng hình bạn giây."))
    assert "STAGE-9P" in status, status
    assert "memory_write=False" in status, status
    assert "api_call=False" in status, status
    assert "Public Vietnamese Fluency Preview" in preview, preview
    assert "vài giây" in preview, preview

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_stage_status()
    stage = buf.getvalue()
    assert "Public fluency polish:" in stage, stage

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_command_help()
    help_output = buf.getvalue()
    assert "/public-fluency-status" in help_output, help_output

    guard = get_public_stage_identity_guard()
    assert guard.classify_public_input("/public-fluency-status") == "backstage_command"
    assert guard.classify_public_input("/public-fluency-preview Nana đứng hình bạn giây.") == "backstage_command"
    print("  PASSED")


def run_all() -> int:
    print("=" * 60)
    print("STAGE-9P Public Vietnamese Fluency Polish — Smoke Tests")
    print("=" * 60)
    tests = [
        _test_typo_and_punctuation_polish,
        _test_role_wording_and_meta_frame_polish,
        _test_label_and_voice_tags_are_removed,
        _test_bridge_metadata_and_reply_are_polished,
        _test_status_help_firewall_stage_surface,
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
