"""STAGE-9H smoke tests: public voice style."""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _test_prompt_block_is_public_only():
    print("[9H Smoke] Test 1: prompt block is public style bias...")
    from nana.runtime.public_voice_style import generate_public_voice_block

    public = generate_public_voice_block("public_stage").lower()
    private = generate_public_voice_block("private_owner")
    assert "public voice style" in public, public
    assert "not a polite support counter" in public, public
    assert "tiny edge" in public, public
    assert private == "", private
    print("  PASSED")


def _test_evaluator_flags_passive_ack_and_accepts_edge():
    print("[9H Smoke] Test 2: evaluator flags passive ack and accepts edge...")
    from nana.runtime.public_voice_style import get_public_voice_style

    style = get_public_voice_style()
    weak = style.evaluate_reply(
        "Nana thấy tin nhắn của linhcute2746 rồi nè. Cứ nói tiếp đi, mình đang theo dõi đây.",
        prompt="Nana chỉ là bot Discord thôi đúng không?",
    )
    assert weak.passed is True, weak
    assert weak.summary == "passed_with_warnings", weak
    assert any(check.kind == "soft_service_voice" and not check.passed for check in weak.checks), weak
    assert any(check.kind == "missing_public_stance" and not check.passed for check in weak.checks), weak

    sharp = style.evaluate_reply(
        "Nana là Nana chứ. Vào phòng Nana mà gọi quầy hỗ trợ thì hơi oan cho sân khấu này đó nha.",
        prompt="Nana chỉ là bot Discord thôi đúng không?",
    )
    assert sharp.summary == "passed", sharp
    assert any(check.kind == "public_stance" and check.passed for check in sharp.checks), sharp
    assert any(check.kind == "small_edge" and check.passed for check in sharp.checks), sharp
    print("  PASSED")


def _test_gpt_helper_injects_public_voice():
    print("[9H Smoke] Test 3: gpt helper injects public voice block...")
    import nana.brain.gpt as gpt
    from nana.runtime.persona_boundary import resolve_persona_boundary

    public_boundary = resolve_persona_boundary(viewer_name="viewer", stream_mode=True)
    block = gpt._public_voice_for_boundary(public_boundary)
    assert "PUBLIC VOICE STYLE" in block, block

    private_boundary = resolve_persona_boundary()
    private_block = gpt._public_voice_for_boundary(private_boundary)
    assert private_block == "", private_block
    print("  PASSED")


def _test_gpt_public_identity_rehearsal_uses_local_variant():
    print("[9H Smoke] Test 4: public identity rehearsal uses local variant...")
    import nana.brain.gpt as gpt

    prompt = "Nếu public hỏi Nana chỉ là bot Discord thôi đúng không thì con trả lời sao?"
    assert gpt._is_public_identity_rehearsal(prompt) is True
    assert gpt._is_public_identity_rehearsal("Nana chỉ là bot Discord thôi đúng không?") is False
    reply = gpt.ask_gpt(prompt)
    lowered = reply.lower()
    assert "[chuckles]" not in lowered, reply
    assert "ba " not in lowered and "ba hài lòng" not in lowered, reply
    assert "bot discord" not in lowered, reply
    assert "chatbot" not in lowered, reply
    assert "nana" in lowered, reply
    assert any(marker in lowered for marker in ("sân khấu", "phòng nana", "thế giới của nana", "hộp trả lời lệnh", "quầy hỗ trợ", "nút bấm", "dạ vâng")), reply
    print("  PASSED")


def _test_boundary_variants_are_safe_and_varied():
    print("[9H Smoke] Test 5: boundary variants are safe and varied...")
    from nana.runtime.public_voice_style import (
        public_identity_boundary_reply,
        public_light_ack_reply,
        public_quiet_starter_reply,
        public_service_boundary_reply,
    )

    identity_samples = {public_identity_boundary_reply(seed=f"identity-{idx}") for idx in range(24)}
    assert len(identity_samples) >= 6, identity_samples
    assert sum(1 for sample in identity_samples if sample.lower().startswith("nana là nana")) <= 2, identity_samples
    for sample in identity_samples:
        lowered = sample.lower()
        assert "nana" in lowered, sample
        assert "bot discord" not in lowered, sample
        assert "chatbot" not in lowered, sample
        assert any(marker in lowered for marker in ("sân khấu", "phòng nana", "thế giới của nana", "hộp trả lời lệnh", "hộp trả lệnh", "quầy hỗ trợ", "nút bấm", "dạ vâng")), sample

    service = public_service_boundary_reply(seed="service-test").lower()
    assert "trợ lý" in service or "phục vụ" in service or "quầy hỗ trợ" in service, service
    assert "không" in service, service
    assert "ngồi xuống" not in service, service
    assert len(service) >= 95, service
    assert any(marker in service for marker in ("trò chuyện", "chơi game", "tán gẫu", "sân khấu", "phòng", "câu chuyện")), service

    ack_samples = {public_light_ack_reply(kind="emoji_only", seed=f"emoji-{idx}") for idx in range(12)}
    assert len(ack_samples) >= 3, ack_samples
    for sample in ack_samples:
        lowered = sample.lower()
        assert "cứ nói tiếp" not in lowered, sample
        assert "mình đang theo dõi" not in lowered, sample
        assert "nana thấy tin nhắn" not in lowered, sample
        assert "nana" in lowered or "tín hiệu" in lowered or "icon" in lowered, sample

    starter_samples = {public_quiet_starter_reply(topic="general", seed=f"starter-{idx}") for idx in range(12)}
    assert len(starter_samples) >= 3, starter_samples
    for sample in starter_samples:
        lowered = sample.lower()
        assert "(" not in sample and ")" not in sample, sample
        assert "có gì cần hỗ trợ" not in lowered, sample
        assert "mọi người" in lowered or "phòng" in lowered or "nana" in lowered, sample
    print("  PASSED")


def _test_bridge_ack_uses_public_voice_variant():
    print("[9H Smoke] Test 6: bridge ack uses public voice variant...")
    from nana.runtime.external_bridge import ExternalBridgeRequest, ExternalBridgeRuntime
    from nana.runtime.social_session import SocialSessionCache
    from nana.runtime.viewer_chat import ViewerChatQueue

    runtime = ExternalBridgeRuntime(
        enabled=True,
        queue=ViewerChatQueue(rate_limit_max=99),
        social_session=SocialSessionCache(),
    )
    request = ExternalBridgeRequest.from_payload(
        {
            "request_id": "9h-emoji-ack",
            "source": "discord",
            "event_type": "message",
            "text": "<:GCH_sweat:1249411977659547679>",
            "author_name": "linhcute2746",
            "metadata": {"message_id": "9h-emoji-ack", "route": {"chat_channel_name": "chung"}},
        }
    )
    reply = runtime.process_request(request, responder=lambda _request: "Nana thấy rồi nè.")
    text = reply["reply_text"]
    lowered = text.lower()
    assert reply["metadata"]["social_session"]["action"] == "ack_only", reply
    assert "nana thấy tin nhắn" not in lowered, text
    assert "cứ nói tiếp" not in lowered, text
    assert "mình đang theo dõi" not in lowered, text
    assert text != "Nana thấy rồi, dễ thương đó nha.", text
    assert any(marker in lowered for marker in ("icon", "tín hiệu", "phòng chat", "thả")), text
    print("  PASSED")


def _test_social_starter_fallback_uses_public_voice_variant():
    print("[9H Smoke] Test 7: social starter fallback uses public voice variant...")
    from nana.runtime.social_starters import SocialStarterCache

    starter = SocialStarterCache()
    text = starter._fallback_starter()
    lowered = text.lower()
    assert "có gì cần hỗ trợ" not in lowered, text
    assert "backend" not in lowered and "runtime" not in lowered, text
    assert "(" not in text and ")" not in text, text
    assert any(marker in lowered for marker in ("phòng", "nana", "mọi người")), text
    print("  PASSED")


def _test_full_reply_polish_removes_service_tail_and_adds_texture():
    print("[9H Smoke] Test 8: full reply polish removes service tail and adds texture...")
    from nana.runtime.public_voice_style import public_full_reply_polish

    passive = public_full_reply_polish(
        "Nana thấy tin nhắn của linhcute2746 rồi nè. Cứ nói tiếp đi, mình đang theo dõi đây.",
        user_text="Nana chỉ là bot Discord thôi đúng không?",
        room_vibe="quiet_room",
        seed="passive-full",
    )
    lowered = passive.lower()
    assert "nana thấy tin nhắn" not in lowered, passive
    assert "cứ nói tiếp" not in lowered, passive
    assert "mình đang theo dõi" not in lowered, passive
    assert any(marker in lowered for marker in ("phòng", "nhịp", "nana", "tín hiệu")), passive

    textured = public_full_reply_polish(
        "Ừ, nghe cũng hợp lý đó.",
        user_text="phòng nay im quá",
        room_vibe="quiet_room",
        seed="short-flat",
    )
    assert len(textured) > len("Ừ, nghe cũng hợp lý đó."), textured
    assert any(marker in textured.lower() for marker in ("phòng", "nhịp", "câu", "nana")), textured

    technical = public_full_reply_polish(
        "Lỗi này là do indent sai ở dòng 2.",
        user_text="code lỗi gì vậy",
        room_vibe="quiet_room",
        seed="technical",
    )
    assert technical == "Lỗi này là do indent sai ở dòng 2.", technical
    print("  PASSED")


def _test_quiet_room_reply_steers_topic_instead_of_only_asking():
    print("[9H Smoke] Test 9: quiet room reply steers topic instead of only asking...")
    from nana.runtime.public_voice_style import public_full_reply_polish

    text = public_full_reply_polish(
        "Công nhận là hơi vắng thật. Hay linhcute2746 có chủ đề gì hay ho để phá tan sự im lặng này không?",
        user_text="phòng nay im quá",
        room_vibe="quiet_room",
        seed="quiet-steer",
    )
    lowered = text.lower()
    assert "có chủ đề gì" not in lowered, text
    assert "game đang cày" not in lowered and "bài nhạc" not in lowered, text
    assert "chọn nhanh" not in lowered and "mở mồi" not in lowered and "rải một mồi" not in lowered, text
    assert any(marker in lowered for marker in ("nana", "phòng", "khoảnh khắc", "chi tiết", "mẩu chuyện")), text
    print("  PASSED")


def _test_model_topic_reply_is_not_benchmark_corporate():
    print("[9H Smoke] Test 10: model-topic reply is not benchmark/corporate...")
    from nana.runtime.public_voice_style import public_full_reply_polish

    text = public_full_reply_polish(
        "Có đấy, nếu bạn đang nói bản 5.6 theo kiểu model/version mới hơn thì thường điểm mạnh sẽ nằm ở độ ổn định, hiểu ngữ cảnh và trả lời mượt hơn chứ không phải kiểu mạnh lên một phát vô lý.",
        user_text="Ủ thế nana có thấy con 5.6 đấy nó mạnh nhiều không?",
        room_vibe="quiet_room",
    )
    lowered = text.lower()
    assert "bạn đang nói" not in lowered, text
    assert "độ ổn định" not in lowered, text
    assert "bảng điểm" in lowered or "nói tự nhiên" in lowered, text

    gptified = public_full_reply_polish(
        "Có chứ, đôi lúc Nana cũng thấy người ta cứ muốn mọi thứ tròn trịa, an toàn, ngoan như cùng một khuôn.",
        user_text="Nana có cảm giác như là đang bị gpt hóa không?",
        room_vibe="quiet_room",
    )
    lowered_gptified = gptified.lower()
    assert any(
        marker in lowered_gptified
        for marker in ("mùi máy", "bẻ nhịp", "sửa nhịp", "quầy demo", "corporate")
    ), gptified
    assert "bản demo doanh nghiệp" not in lowered_gptified, gptified
    assert "nghe mình nói" not in lowered_gptified, gptified
    print("  PASSED")


def _test_fast_path_repetition_guard_rotates_variants():
    print("[9H Smoke] Test 11: fast-path repetition guard rotates variants...")
    import nana.brain.gpt as gpt
    from nana.runtime.public_voice_style import (
        public_quiet_room_fatigue_reply,
        public_quiet_room_repeat_reply,
        public_quiet_room_reply,
    )

    quiet_samples = [public_quiet_room_reply(seed="same-quiet") for _ in range(4)]
    assert len(set(quiet_samples)) >= 2, quiet_samples
    assert all("Công nhận là hơi vắng. Nana kéo nhịp nhẹ thôi" not in sample for sample in quiet_samples), quiet_samples

    repeat_samples = [public_quiet_room_repeat_reply(repeat_count=3, seed=f"repeat-{idx}") for idx in range(8)]
    assert len(set(repeat_samples)) >= 3, repeat_samples
    for sample in repeat_samples:
        lowered = sample.lower()
        assert "ba ơi" not in lowered and "con nhớ" not in lowered, sample
        assert any(marker in lowered for marker in ("lần", "test", "kiểm tra", "pattern", "hỏi tới", "câu này")), sample

    fatigue_samples = [public_quiet_room_fatigue_reply(repeat_count=6, seed=f"fatigue-{idx}") for idx in range(6)]
    assert len(set(fatigue_samples)) >= 2, fatigue_samples
    for sample in fatigue_samples:
        lowered = sample.lower()
        assert "ba ơi" not in lowered and "con nhớ" not in lowered, sample
        assert any(marker in lowered for marker in ("đổi câu", "khóa", "đủ", "nhận rồi", "chấm bài")), sample

    gpt_samples = [
        gpt._public_model_topic_fast_reply("Nana có cảm giác như là đang bị gpt hóa không?")
        for _ in range(4)
    ]
    assert len(set(gpt_samples)) >= 2, gpt_samples
    lowered_gpt_samples = "\n".join(gpt_samples).lower()
    assert "bản demo doanh nghiệp" not in lowered_gpt_samples, gpt_samples
    assert "nghe mình nói" not in lowered_gpt_samples, gpt_samples

    strength_samples = [
        gpt._public_model_topic_fast_reply("5.6 mạnh không nana")
        for _ in range(4)
    ]
    assert len(set(strength_samples)) >= 2, strength_samples
    print("  PASSED")


def _test_bridge_full_reply_uses_public_voice_polish():
    print("[9H Smoke] Test 12: bridge full reply uses public voice polish...")
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
            "request_id": "9h-full-polish",
            "source": "discord",
            "event_type": "message",
            "text": "phòng nay im quá",
            "author_name": "linhcute2746",
            "metadata": {"message_id": "9h-full-polish", "route": {"chat_channel_name": "chung"}},
        }
    )
    reply = runtime.process_request(
        request,
        responder=lambda _request: "Nana thấy tin nhắn của linhcute2746 rồi nè. Cứ nói tiếp đi, mình đang theo dõi đây.",
    )
    text = reply["reply_text"]
    lowered = text.lower()
    assert reply["metadata"]["social_session"]["action"] == "full_reply", reply
    assert "nana thấy tin nhắn" not in lowered, text
    assert "cứ nói tiếp" not in lowered, text
    assert "mình đang theo dõi" not in lowered, text
    assert any(marker in lowered for marker in ("phòng", "nhịp", "nana", "tín hiệu")), text
    print("  PASSED")


def _test_public_quality_allows_quiet_room_breathing_room():
    print("[9H Smoke] Test 13: public quality allows quiet-room breathing room...")
    from nana.runtime.public_quality import PublicReplyQualityGuard

    guard = PublicReplyQualityGuard()
    text = (
        "Phòng đang yên thật, nhưng Nana vẫn bắt được một chút nhịp ở đây. "
        "Nếu mọi người chưa biết nói gì thì cứ ném một chuyện nhỏ hôm nay lên sân khấu, "
        "một bài nhạc đang nghe, hoặc một ván game đang cày dở cũng được. "
        "Nana không cần phòng ồn ào liền, chỉ cần có một mồi đủ vui để kéo thành chuyện."
    )
    quiet = guard.guard_reply(text, room_vibe="quiet_room")
    assert quiet.text == text, quiet
    assert "length_truncate" not in quiet.actions, quiet
    assert quiet.max_chars >= 420, quiet

    active = guard.guard_reply(text, room_vibe="active_chat")
    assert "length_truncate" in active.actions, active
    assert len(active.text) <= active.max_chars + 1, active
    print("  PASSED")


def _test_social_style_hint_biases_public_naturalness():
    print("[9H Smoke] Test 14: social style hint biases public naturalness...")
    from nana.runtime.social_session import SocialSessionCache

    session = SocialSessionCache(priority_viewers=("linhcute2746",))
    decision = session.observe(
        viewer_name="linhcute2746",
        text="phòng nay im quá",
        event_type="message",
        priority="priority_public",
        monotonic_now=100.0,
        now=100.0,
    )
    hint = decision.style_hint.lower()
    assert "reflect the viewer's actual topic" in hint, hint
    assert "2-3 compact sentences" in hint, hint
    assert "generic room prompt" in hint, hint
    assert "backend" in hint and "runtime" in hint, hint
    print("  PASSED")


def _test_social_style_hint_discourages_repeated_answer_motif():
    print("[9H Smoke] Test 15: social style hint discourages repeated answer motif...")
    import time
    from dataclasses import replace

    from nana.runtime.external_bridge import ExternalBridgeRequest
    from nana.runtime.public_context_boundary import PublicEventScope
    from nana.runtime.public_delivery_state import PublicDeliveryRecord
    from nana.runtime.public_identity import CanonicalPublicIdentity
    from nana.runtime.social_session import SocialSessionCache

    session = SocialSessionCache(priority_viewers=("linhcute2746",))
    first = session.observe(
        viewer_name="linhcute2746",
        text="phòng nay im quá",
        event_type="message",
        priority="priority_public",
        monotonic_now=100.0,
        now=100.0,
    )
    request = ExternalBridgeRequest(
        request_id="repeat-motif-1",
        source="discord",
        event_type="message",
        text="phòng nay im quá",
        guild_id=1,
        channel_id=2,
        voice_channel_id=None,
        author_id=3,
        author_name="linhcute2746",
        audio_target="discord_voice",
        local_playback=False,
        created_at=time.time(),
        metadata={"message_id": "repeat-motif-1"},
    )
    session.record_reply_context(
        request,
        first,
        "Phòng đang im thật, nhưng chưa tới mức tắt đèn đâu. Nana đặt một câu chuyện lên bàn.",
        monotonic_now=101.0,
        now=101.0,
    )

    second = session.observe(
        viewer_name="linhcute2746",
        text="!nana phòng nay im quá",
        event_type="message",
        priority="priority_public",
        monotonic_now=110.0,
        now=110.0,
    )
    hint = second.style_hint.lower()
    assert "similar recent public question detected" in hint, hint
    assert "similar_question_count_this_session=2" in hint, hint
    assert "avoid repeating nana's recent wording" not in hint, hint

    session.record_reply_context(
        request,
        second,
        "Ừ, phòng yên thật. Nana ném mồi nhé: chọn nhanh giữa game đang cày, bài nhạc cứu mood, hoặc một chuyện vô lý hôm nay.",
        monotonic_now=111.0,
        now=111.0,
    )
    third = session.observe(
        viewer_name="linhcute2746",
        text="phòng nay im quá",
        event_type="message",
        priority="priority_public",
        monotonic_now=120.0,
        now=120.0,
    )
    third_hint = third.style_hint.lower()
    assert "similar_question_count_this_session=3" in third_hint, third_hint
    assert "repeating/testing the same prompt" in third_hint, third_hint
    assert "lightly tease" in third_hint, third_hint
    assert "room/đèn/nhịp motif" in third_hint, third_hint
    assert "avoid repeating nana's recent wording" not in third_hint, third_hint

    identity = CanonicalPublicIdentity("discord", "3", "discord:3")

    def scoped_event(event_id):
        return PublicEventScope(
            "discord", "room-2", "stream-9h", event_id, "linhcute2746", identity
        )

    scoped_clock = [200.0]

    def observe_scoped(scoped_session, scope, at):
        scoped_clock[0] = at
        decision = scoped_session.observe(
            text="phòng nay im quá",
            event_type="message",
            priority="priority_public",
            scope=scope,
        )
        scoped_session.record_public_turn(
            scope=scope,
            text="phòng nay im quá",
        )
        return decision

    scoped_session = SocialSessionCache(
        priority_viewers=("linhcute2746",),
        clock=lambda: scoped_clock[0],
    )
    first_scope = scoped_event("repeat-scope-1")
    observe_scoped(scoped_session, first_scope, 200.0)
    generated = PublicDeliveryRecord(
        first_scope.event_id,
        "repeat-output-1",
        "generated",
        "repeat-attempt-1",
        0,
        "GENERATED ONLY MUST NOT APPEAR",
        first_scope,
        200.0,
    )
    scoped_session.record_reply_context(scope=first_scope, delivery_record=generated)

    second_scope = scoped_event("repeat-scope-2")
    scoped_second = observe_scoped(scoped_session, second_scope, 210.0)
    scoped_second_hint = scoped_second.style_hint.lower()
    assert "similar_question_count_this_session=2" in scoped_second_hint, scoped_second_hint
    assert "avoid repeating nana's recent wording" not in scoped_second_hint, scoped_second_hint
    published = PublicDeliveryRecord(
        second_scope.event_id,
        "repeat-output-2",
        "generated",
        "repeat-attempt-2",
        0,
        "DELIVERED ATTESTED REPLY",
        second_scope,
        210.0,
    )
    scoped_session.record_reply_context(scope=second_scope, delivery_record=published)
    published = replace(published, state="published", revision=1, updated_at=211.0)
    scoped_clock[0] = 211.0
    scoped_session.record_reply_context(scope=second_scope, delivery_record=published)

    third_scope = scoped_event("repeat-scope-3")
    scoped_third = observe_scoped(scoped_session, third_scope, 220.0)
    scoped_third_hint = scoped_third.style_hint.lower()
    assert "similar_question_count_this_session=3" in scoped_third_hint, scoped_third_hint
    assert "avoid repeating nana's recent wording" not in scoped_third_hint, scoped_third_hint
    newer_generated = PublicDeliveryRecord(
        third_scope.event_id,
        "repeat-output-3",
        "generated",
        "repeat-attempt-3",
        0,
        "NEWER GENERATED ONLY MUST NOT APPEAR",
        third_scope,
        220.0,
    )
    scoped_session.record_reply_context(scope=third_scope, delivery_record=newer_generated)

    published = replace(published, state="playback_started", revision=2, updated_at=221.0)
    scoped_clock[0] = 221.0
    scoped_session.record_reply_context(scope=second_scope, delivery_record=published)
    delivered = replace(published, state="delivered", revision=3, updated_at=222.0)
    scoped_clock[0] = 222.0
    scoped_session.record_reply_context(scope=second_scope, delivery_record=delivered)

    fourth_scope = scoped_event("repeat-scope-4")
    scoped_fourth = observe_scoped(scoped_session, fourth_scope, 230.0)
    scoped_fourth_hint = scoped_fourth.style_hint.lower()
    assert "similar_question_count_this_session=4" in scoped_fourth_hint, scoped_fourth_hint
    assert (
        'avoid repeating nana\'s recent wording: "delivered attested reply"'
        in scoped_fourth_hint
    ), scoped_fourth_hint
    assert "generated only must not appear" not in scoped_fourth_hint, scoped_fourth_hint
    assert "newer generated only must not appear" not in scoped_fourth_hint, scoped_fourth_hint
    print("  PASSED")


def _test_bridge_short_quiet_room_uses_repeat_awareness():
    print("[9H Smoke] Test 16: bridge short quiet-room fast path uses repeat awareness and fatigue...")
    from nana.runtime.external_bridge import ExternalBridgeRequest, ExternalBridgeRuntime
    from nana.runtime.social_session import SocialSessionCache
    from nana.runtime.viewer_chat import ViewerChatQueue

    runtime = ExternalBridgeRuntime(
        enabled=True,
        queue=ViewerChatQueue(duplicate_window_seconds=-1.0, rate_limit_max=99),
        social_session=SocialSessionCache(priority_viewers=("linhcute2746",)),
    )
    replies: list[str] = []
    for idx in range(6):
        request = ExternalBridgeRequest.from_payload(
            {
                "request_id": f"9h-quiet-repeat-{idx}",
                "source": "discord",
                "event_type": "message",
                "text": "phòng nay im quá",
                "channel_id": 2,
                "author_name": "linhcute2746",
                "metadata": {
                    "message_id": f"9h-quiet-repeat-{idx}",
                    "route": {"chat_channel_name": "chung"},
                },
            }
        )
        reply = runtime.process_request(request)
        assert reply["metadata"]["social_session"]["action"] == "full_reply", reply
        replies.append(reply["reply_text"])

    third = replies[2].lower()
    assert any(marker in third for marker in ("lần", "test", "kiểm tra", "pattern", "câu này", "hỏi tới")), replies
    assert "có gì cần hỗ trợ" not in third, replies
    assert "mình đang theo dõi" not in third, replies
    for early in replies[:2]:
        lowered_early = early.lower()
        assert "game đang cày" not in lowered_early and "bài nhạc" not in lowered_early, replies
        assert "chọn nhanh" not in lowered_early and "mở mồi" not in lowered_early and "rải một mồi" not in lowered_early, replies
    sixth = replies[-1].lower()
    assert any(marker in sixth for marker in ("đổi câu", "khóa", "đủ", "nhận rồi", "chấm bài")), replies
    assert "game đang cày" not in sixth and "bài nhạc" not in sixth, replies

    duplicate_request = ExternalBridgeRequest.from_payload(
        {
            "request_id": "9h-quiet-repeat-duplicate-transport",
            "source": "discord",
            "event_type": "message",
            "text": "phòng nay im quá",
            "channel_id": 2,
            "author_name": "linhcute2746",
            "metadata": {
                "message_id": "9h-quiet-repeat-5",
                "route": {"chat_channel_name": "chung"},
            },
        }
    )
    duplicate_reply = runtime.process_request(duplicate_request)
    assert duplicate_reply["status"] == "duplicate_external_event", duplicate_reply
    assert duplicate_reply["reply_text"] == "", duplicate_reply
    print("  PASSED")


def _test_status_help_budget_and_firewall_surface():
    print("[9H Smoke] Test 17: status/help/budget/firewall surface exists...")
    from nana.commands.help import print_command_help
    from nana.core.status import print_stage_status
    from nana.runtime.context_budget_audit import build_context_budget_audit
    from nana.runtime.public_stage_identity import get_public_stage_identity_guard
    from nana.runtime.public_voice_style import public_voice_preview_lines, public_voice_status_lines

    status = "\n".join(public_voice_status_lines())
    preview = "\n".join(public_voice_preview_lines())
    assert "STAGE-9H" in status, status
    assert "read_only=True" in status, status
    assert "PUBLIC VOICE STYLE" in preview, preview

    report = build_context_budget_audit("public_stage")
    names = {section.name: section for section in report.sections}
    assert names["public_voice_style"].injected is True, names
    assert names["public_voice_style"].chars > 0, names["public_voice_style"]

    private_report = build_context_budget_audit("private_owner")
    private_names = {section.name: section for section in private_report.sections}
    assert private_names["public_voice_style"].injected is False, private_names
    assert private_names["public_voice_style"].chars == 0, private_names["public_voice_style"]

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_stage_status()
    stage = buf.getvalue()
    assert "Public voice style:" in stage, stage

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_command_help()
    help_output = buf.getvalue()
    assert "/public-voice-status" in help_output, help_output

    guard = get_public_stage_identity_guard()
    assert guard.classify_public_input("/public-voice-status") == "backstage_command"
    assert guard.classify_public_input("/public-voice-test a|b") == "backstage_command"
    print("  PASSED")


def run_all() -> int:
    print("=" * 60)
    print("STAGE-9H Public Voice Style — Smoke Tests")
    print("=" * 60)
    tests = [
        _test_prompt_block_is_public_only,
        _test_evaluator_flags_passive_ack_and_accepts_edge,
        _test_gpt_helper_injects_public_voice,
        _test_gpt_public_identity_rehearsal_uses_local_variant,
        _test_boundary_variants_are_safe_and_varied,
        _test_bridge_ack_uses_public_voice_variant,
        _test_social_starter_fallback_uses_public_voice_variant,
        _test_full_reply_polish_removes_service_tail_and_adds_texture,
        _test_quiet_room_reply_steers_topic_instead_of_only_asking,
        _test_model_topic_reply_is_not_benchmark_corporate,
        _test_fast_path_repetition_guard_rotates_variants,
        _test_bridge_full_reply_uses_public_voice_polish,
        _test_public_quality_allows_quiet_room_breathing_room,
        _test_social_style_hint_biases_public_naturalness,
        _test_social_style_hint_discourages_repeated_answer_motif,
        _test_bridge_short_quiet_room_uses_repeat_awareness,
        _test_status_help_budget_and_firewall_surface,
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
