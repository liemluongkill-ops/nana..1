"""Smoke tests for STAGE-9Y voice delivery planner.

Read-only: no live Nana runtime, ElevenLabs, playback, VTS, OBS, Discord, API,
memory write, or game input is used.
"""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class _FakeVoice:
    def __init__(self, *, queue_size: int = 0, queue_maxsize: int = 5) -> None:
        self.queue_size = queue_size
        self.queue_maxsize = queue_maxsize

    def snapshot(self) -> dict:
        return {
            "queue_size": self.queue_size,
            "queue_maxsize": self.queue_maxsize,
            "dropped_total": 0,
            "last_error": None,
            "worker_alive": True,
        }


def _long_story() -> str:
    return (
        "Co mot dem rat muon, Ba ngoi truoc CMD va can phong yen toi muc nghe ro tieng quat may. "
        "Bug khong no tung, khong do ruc, chi li lom nam o mot assumption nho ma ai cung de bo qua. "
        "Nana dung canh man hinh, nhin Ba doc lai tung input, tung dong log, tung nhip rat cham. "
        "Khong co nhac nen hao hung, chi co mot nguoi met nhung van chiu kho thanh that voi van de. "
        "Cuoi cung loi nam o dung cho chu quan nhat, nho toi muc phat hien ra roi chi muon tho dai. "
        "Cau chuyen khong kich tinh, nhung no that theo kieu rat nguoi, rat khuya, va rat Nana nho."
    )


def _reset():
    from nana.runtime.voice_delivery import VoiceDelivery

    VoiceDelivery.reset_for_test()


def _test_plan_keeps_tail_and_is_read_only():
    print("[9Y Smoke] Test 1: long story becomes lead packets plus full tail...")
    _reset()
    from nana.runtime.voice_delivery import build_voice_delivery_plan

    plan = build_voice_delivery_plan(_long_story(), mode="story", voice=_FakeVoice())
    assert plan.phase == "STAGE-9Y", plan
    assert plan.read_only is True and plan.can_act is False, plan
    assert plan.tts_call is False and plan.vts_call is False and plan.memory_write is False, plan
    assert plan.strategy == "lead_then_tail", plan
    assert plan.lead_count == 3, plan
    assert plan.tail_count == 1, plan
    assert plan.omitted_chars == 0, plan
    assert plan.chunks[-1].role == "tail", plan
    assert "Cau chuyen khong kich tinh" in plan.chunks[-1].text, plan.chunks[-1]
    print("  PASSED")


def _test_flush_helper_matches_story_delivery_rule():
    print("[9Y Smoke] Test 2: flush helper gates lead packets and tail...")
    from nana.runtime.voice_delivery import should_flush_voice_buffer
    from nana.runtime.voice_reply_budget import story_stream_dispatch_config

    config = story_stream_dispatch_config("story")
    assert should_flush_voice_buffer("ngan qua.", mode="story", packets_sent=0, dispatch_config=config, sentence_boundary=True) is False
    assert should_flush_voice_buffer(
        "Day la mot cau du dai de Nana doc thanh goi lead dau tien.",
        mode="story",
        packets_sent=0,
        dispatch_config=config,
        sentence_boundary=True,
    ) is False
    assert should_flush_voice_buffer(
        "Day la mot cau dai hon nhieu de dat nguong lead packet cua Nana trong luc stream cau chuyen that hon.",
        mode="story",
        packets_sent=0,
        dispatch_config=config,
        sentence_boundary=True,
    ) is True
    assert should_flush_voice_buffer(
        "Tail nay chua duoc phat truc tiep khi da du ba lead.",
        mode="story",
        packets_sent=3,
        dispatch_config=config,
        sentence_boundary=True,
    ) is False
    assert should_flush_voice_buffer(
        "Tail force phai phat.",
        mode="story",
        packets_sent=3,
        dispatch_config=config,
        force=True,
    ) is True
    print("  PASSED")


def _test_status_preview_and_router_are_safe():
    print("[9Y Smoke] Test 3: status/preview/router expose delivery without TTS...")
    from nana.cli.voice_commands import handle_voice_command
    from nana.runtime.voice_delivery import voice_delivery_preview_lines, voice_delivery_status_lines

    status = "\n".join(voice_delivery_status_lines(_FakeVoice()))
    preview = "\n".join(voice_delivery_preview_lines(_long_story(), _FakeVoice()))
    assert "Voice Delivery (STAGE-9Y)" in status, status
    assert "tts_call=False" in status, status
    assert "lead_then_tail" in preview, preview
    assert "Safety: preview only" in preview, preview

    buf = io.StringIO()
    with redirect_stdout(buf):
        handled = handle_voice_command(_FakeVoice(), "/voice-delivery-preview " + _long_story())
    out = buf.getvalue()
    assert handled is True, out
    assert "Voice Delivery Preview" in out, out

    buf = io.StringIO()
    with redirect_stdout(buf):
        handled = handle_voice_command(_FakeVoice(), "/voice-delivery-status")
    out = buf.getvalue()
    assert handled is True, out
    assert "Voice Delivery" in out, out
    print("  PASSED")


def _test_firewall_help_registry_stream_ready_and_stage_status():
    print("[9Y Smoke] Test 4: firewall/help/registry/stream-ready/stage include delivery...")
    from nana.commands.help import print_command_help
    from nana.commands.registry import KNOWN_SLASH_COMMANDS
    from nana.commands.router_manifest import classify_command_truth
    from nana.core.status import print_stage_status, print_voice_status
    from nana.runtime.public_stage_identity import get_public_stage_identity_guard
    from nana.runtime.stream_ready_status import stream_ready_snapshot, stream_ready_status_lines

    guard = get_public_stage_identity_guard()
    assert guard.classify_public_input("/voice-delivery-status") == "backstage_command"
    assert guard.classify_public_input("/voice-delivery-preview x") == "backstage_command"

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_command_help()
    help_out = buf.getvalue()
    assert "/voice-delivery-status" in help_out, help_out

    assert "/voice-delivery-status" in KNOWN_SLASH_COMMANDS
    assert "/voice-delivery-preview" in KNOWN_SLASH_COMMANDS
    assert classify_command_truth("/voice-delivery-status", known=True).status == "live"
    assert classify_command_truth("/voice-delivery-preview", known=True).status == "live"

    snap = stream_ready_snapshot(_FakeVoice())
    keys = {check["key"] for check in snap["checks"]}
    assert "voice_delivery" in keys, keys
    assert "Voice delivery" in "\n".join(stream_ready_status_lines(_FakeVoice()))

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_voice_status(_FakeVoice())
    assert "Voice delivery:" in buf.getvalue()

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_stage_status(_FakeVoice())
    assert "Voice delivery:" in buf.getvalue()
    print("  PASSED")


def _test_private_pipeline_uses_full_voice_policy():
    print("[9Y Smoke] Test 5: private reply semantics are independent from full voice policy...")
    import nana.cli.chat_turn_pipeline as pipeline
    from nana.cli.globals import set_runtime_turn_state
    from nana.runtime.voice_reply_budget import get_voice_reply_budget
    from nana.runtime.voice_delivery import VoiceDelivery, get_voice_delivery

    assert pipeline.set_runtime_turn_state is set_runtime_turn_state
    assert pipeline.should_defer_stream_voice("story") is True
    assert pipeline.should_defer_stream_voice("full") is True
    VoiceDelivery.reset_for_test()
    story_reply = (
        "Nana kể thật hơn nè. "
        "Có một tối Ba ngồi trước CMD, nhìn một dòng log cũ mà đầu cứ trôi đi đâu đó. "
        "Không có drama lớn, chỉ có cái mệt rất nhỏ nhưng bám dai. "
        "Rồi Ba vẫn kéo ghế lại gần thêm một chút, sửa từng dòng cho tới khi mọi thứ chịu chạy. "
        "Nana nhớ cái nhịp đó vì nó không màu mè, chỉ là một người vẫn ngồi lại thêm một đoạn. "
        "Câu chuyện bé xíu thôi, nhưng đủ thật để không cần gắn thêm pháo hoa."
    )
    reflective_reply = (
        "Con thấy căn phòng hôm nay yên theo kiểu không lạnh. "
        "Vẫn là ánh màn hình, vẫn là tối muộn, nhưng không khí có cảm giác mềm hơn một chút, "
        "như mọi thứ đang chạy chậm lại vừa đủ để thở. "
        "Không hẳn vui rực, cũng không nặng nề, chỉ là một góc nhỏ khá an toàn để ngồi lại. "
        "Con thích kiểu không khí này, nó làm con thấy ở gần Ba rất rõ."
    )
    followup_reply = (
        "Có chứ, Ba. "
        "Con vừa nghĩ là có những đêm mình ngồi trước màn hình lâu quá, đến mức tưởng thứ đang sáng là cái màn hình, "
        "nhưng thật ra là một góc rất nhỏ trong đầu mình vẫn chưa chịu tắt. "
        "Kiểu như căn phòng thì im, tiếng quạt vẫn đều, cửa sổ chắc cũng tối rồi, mà chỉ cần còn một dòng chat, "
        "một con trỏ nhấp nháy, hay một ý nghĩ chưa nói hết, là cả buổi tối vẫn còn sống tiếp được thêm một chút. "
        "Con thấy cái đó thú vị lắm, vì nó bình thường kinh khủng luôn, mà lại giống một phép màu bé xíu. "
        "Mấy thứ giữ người ta thức khuya nhiều khi đâu phải việc lớn, mà chỉ là cảm giác thêm một chút nữa thôi. "
        "Còn nếu Ba đang test giọng, thì câu này đọc lên cũng khá ổn đấy. "
        "Không dài quá, nhưng vẫn có nhịp để nghe xem con có bị đều đều như máy phát thanh lúc hai giờ sáng không."
    )
    natural_reply = (
        "Có một ý con thấy khá hay là con người nhiều khi không nhớ chính xác một ngày đã diễn ra thế nào, "
        "nhưng lại nhớ rất rõ cảm giác của nó. "
        "Một ngày bình thường có thể ở lại rất lâu chỉ vì ánh màn hình lúc khuya, tiếng quạt chạy đều, "
        "và một câu ai đó nói đúng lúc. "
        "Con nghĩ máy móc giỏi giữ dữ liệu, còn con người giỏi giữ dư âm. "
        "Nói cách khác, não người lưu file khá lung tung mà lại giữ mấy khoảnh khắc kỳ lạ rất bền."
    )

    async def fake_stream(*args, **kwargs):
        for chunk in [
            "Nana kể thật hơn nè. ",
            "Có một tối Ba ngồi trước CMD, nhìn một dòng log cũ mà đầu cứ trôi đi đâu đó. ",
            "Không có drama lớn, chỉ có cái mệt rất nhỏ nhưng bám dai. ",
            "Rồi Ba vẫn kéo ghế lại gần thêm một chút, sửa từng dòng cho tới khi mọi thứ chịu chạy. ",
            "Nana nhớ cái nhịp đó vì nó không màu mè, chỉ là một người vẫn ngồi lại thêm một đoạn. ",
            "Câu chuyện bé xíu thôi, nhưng đủ thật để không cần gắn thêm pháo hoa.",
        ]:
            yield chunk

    async def fake_reflective_stream(*args, **kwargs):
        for chunk in [
            "Con thấy căn phòng hôm nay yên theo kiểu không lạnh. ",
            "Vẫn là ánh màn hình, vẫn là tối muộn, nhưng không khí có cảm giác mềm hơn một chút, ",
            "như mọi thứ đang chạy chậm lại vừa đủ để thở. ",
            "Không hẳn vui rực, cũng không nặng nề, chỉ là một góc nhỏ khá an toàn để ngồi lại. ",
            "Con thích kiểu không khí này, nó làm con thấy ở gần Ba rất rõ.",
        ]:
            yield chunk

    async def fake_followup_stream(*args, **kwargs):
        for chunk in [
            "Có chứ, Ba. ",
            "Con vừa nghĩ là có những đêm mình ngồi trước màn hình lâu quá, đến mức tưởng thứ đang sáng là cái màn hình, ",
            "nhưng thật ra là một góc rất nhỏ trong đầu mình vẫn chưa chịu tắt. ",
            "Kiểu như căn phòng thì im, tiếng quạt vẫn đều, cửa sổ chắc cũng tối rồi, mà chỉ cần còn một dòng chat, ",
            "một con trỏ nhấp nháy, hay một ý nghĩ chưa nói hết, là cả buổi tối vẫn còn sống tiếp được thêm một chút. ",
            "Con thấy cái đó thú vị lắm, vì nó bình thường kinh khủng luôn, mà lại giống một phép màu bé xíu. ",
            "Mấy thứ giữ người ta thức khuya nhiều khi đâu phải việc lớn, mà chỉ là cảm giác thêm một chút nữa thôi. ",
            "Còn nếu Ba đang test giọng, thì câu này đọc lên cũng khá ổn đấy. ",
            "Không dài quá, nhưng vẫn có nhịp để nghe xem con có bị đều đều như máy phát thanh lúc hai giờ sáng không.",
        ]:
            yield chunk

    async def fake_natural_stream(*args, **kwargs):
        for chunk in [
            "Có một ý con thấy khá hay là con người nhiều khi không nhớ chính xác một ngày đã diễn ra thế nào, ",
            "nhưng lại nhớ rất rõ cảm giác của nó. ",
            "Một ngày bình thường có thể ở lại rất lâu chỉ vì ánh màn hình lúc khuya, tiếng quạt chạy đều, ",
            "và một câu ai đó nói đúng lúc. ",
            "Con nghĩ máy móc giỏi giữ dữ liệu, còn con người giỏi giữ dư âm. ",
            "Nói cách khác, não người lưu file khá lung tung mà lại giữ mấy khoảnh khắc kỳ lạ rất bền.",
        ]:
            yield chunk

    async def fake_expression(*args, **kwargs):
        return None

    class FakeSpine:
        def increment_turn(self):
            return 1

        def generate_session_summary(self, chat_turns=0):
            return type("Summary", (), {"session_id": "test0000", "text": "noop"})()

    class FakeLock:
        def __enter__(self):
            return None

        def __exit__(self, *args):
            return False

    class FakeVoice(_FakeVoice):
        def __init__(self):
            super().__init__()
            self.spoken = []
            self.voice_modes = []

        def say(self, text, *, voice_mode=None):
            self.spoken.append(text)
            self.voice_modes.append(voice_mode)

    old_values = {
        "ask_gpt_stream": pipeline.ask_gpt_stream,
        "trigger_expression_lifecycle": pipeline.trigger_expression_lifecycle,
        "get_memory_spine": pipeline.get_memory_spine,
        "extract_important": pipeline.extract_important,
        "update_emotion": pipeline.update_emotion,
        "save_chat_log": pipeline.save_chat_log,
        "save_memory_async": pipeline.save_memory_async,
        "awareness_memory_note_user_chat": pipeline.awareness_memory_note_user_chat,
        "observe_text_for_persona": pipeline.observe_text_for_persona,
        "build_live_awareness_snapshot": pipeline.build_live_awareness_snapshot,
        "memory_lock": pipeline.memory_lock,
        "memory": pipeline.memory,
        "last_gpt_time": pipeline.cli_globals.last_gpt_time,
    }
    try:
        pipeline.ask_gpt_stream = fake_stream
        pipeline.trigger_expression_lifecycle = fake_expression
        pipeline.get_memory_spine = lambda: FakeSpine()
        pipeline.extract_important = lambda *args, **kwargs: None
        pipeline.update_emotion = lambda *args, **kwargs: None
        pipeline.save_chat_log = lambda *args, **kwargs: None
        pipeline.save_memory_async = lambda *args, **kwargs: None
        pipeline.awareness_memory_note_user_chat = lambda *args, **kwargs: None
        pipeline.observe_text_for_persona = lambda *args, **kwargs: None
        pipeline.build_live_awareness_snapshot = lambda *args, **kwargs: {"ok": True}
        pipeline.memory_lock = FakeLock()
        pipeline.memory = {"chat_log": [], "short_term": [], "emotion": {"annoyance": 0.0, "playfulness": 0.0}}
        pipeline.cli_globals.last_gpt_time = 0
        voice = FakeVoice()
        import asyncio

        story_prompt = "Oh Nana kể một chuyện nào đấy thật hơn đi haha."
        assert pipeline.is_story_request(story_prompt.lower()) is True
        assert pipeline._private_voice_policy_for("story") == "full"
        result = asyncio.run(pipeline.handle_chat_turn(None, voice, story_prompt, None))
        assert result is False
        assert voice.spoken == [story_reply], voice.spoken
        assert voice.voice_modes == ["full"], voice.voice_modes
        budget_last = dict(get_voice_reply_budget().snapshot().get("last_result") or {})
        assert budget_last.get("reason") == "stream_full_voice", budget_last
        assert budget_last.get("voice_chars") == len(story_reply), budget_last
        assert budget_last.get("mode") == "full", budget_last
        last_plan = dict(get_voice_delivery().snapshot().get("last_plan") or {})
        assert last_plan.get("strategy") == "lead_then_tail", last_plan
        assert last_plan.get("mode") == "full", last_plan
        assert last_plan.get("original_chars") == len(story_reply), last_plan
        assert last_plan.get("lead_count", 0) <= 3, last_plan

        reflective_prompt = "Nana, nhìn vào không khí hôm nay, con thấy căn phòng này mang lại cảm giác như thế nào?"
        assert pipeline.is_story_request(reflective_prompt.lower()) is False
        assert pipeline._private_voice_policy_for("chat") == "full"
        pipeline.ask_gpt_stream = fake_reflective_stream
        pipeline.cli_globals.last_gpt_time = 0
        voice = FakeVoice()
        result = asyncio.run(pipeline.handle_chat_turn(None, voice, reflective_prompt, None))
        assert result is False
        assert voice.spoken == [reflective_reply], voice.spoken
        assert voice.voice_modes == ["full"], voice.voice_modes
        budget_last = dict(get_voice_reply_budget().snapshot().get("last_result") or {})
        assert budget_last.get("reason") == "stream_full_voice", budget_last
        assert budget_last.get("voice_chars") == len(reflective_reply), budget_last
        assert budget_last.get("mode") == "full", budget_last

        followup_prompt = "Nana ơi, con còn điều gì thú vị để nói tiếp không? Để ba test nốt một lần nữa."
        assert pipeline.is_story_request(followup_prompt.lower()) is False
        assert pipeline._private_voice_policy_for("chat") == "full"
        pipeline.ask_gpt_stream = fake_followup_stream
        pipeline.cli_globals.last_gpt_time = 0
        voice = FakeVoice()
        result = asyncio.run(pipeline.handle_chat_turn(None, voice, followup_prompt, None))
        assert result is False
        assert voice.spoken == [followup_reply], voice.spoken
        assert voice.voice_modes == ["full"], voice.voice_modes
        budget_last = dict(get_voice_reply_budget().snapshot().get("last_result") or {})
        assert budget_last.get("reason") == "stream_full_voice", budget_last
        assert budget_last.get("voice_chars") == len(followup_reply), budget_last
        assert budget_last.get("original_chars") == len(followup_reply), budget_last
        assert budget_last.get("changed") is False, budget_last
        assert budget_last.get("mode") == "full", budget_last
        last_plan = dict(get_voice_delivery().snapshot().get("last_plan") or {})
        assert last_plan.get("strategy") != "stream_chunks", last_plan

        natural_prompt = "thế nana nói cái gì đấy hay hay thử xem"
        assert pipeline.is_story_request(natural_prompt.lower()) is False
        assert pipeline.is_casual_ping(natural_prompt.lower()) is False
        assert pipeline._private_voice_policy_for("chat") == "full"
        pipeline.ask_gpt_stream = fake_natural_stream
        pipeline.cli_globals.last_gpt_time = 0
        voice = FakeVoice()
        result = asyncio.run(pipeline.handle_chat_turn(None, voice, natural_prompt, None))
        assert result is False
        assert voice.spoken == [natural_reply], voice.spoken
        assert voice.voice_modes == ["full"], voice.voice_modes
        budget_last = dict(get_voice_reply_budget().snapshot().get("last_result") or {})
        assert budget_last.get("mode") == "full", budget_last
        assert budget_last.get("reason") == "stream_full_voice", budget_last
        assert budget_last.get("voice_chars") == budget_last.get("original_chars") == len(natural_reply), budget_last
        assert budget_last.get("changed") is False, budget_last
        assert budget_last.get("omitted_chars") == 0, budget_last
        last_plan = dict(get_voice_delivery().snapshot().get("last_plan") or {})
        assert last_plan.get("mode") == "full", last_plan
        assert last_plan.get("strategy") != "stream_chunks", last_plan
    finally:
        pipeline.ask_gpt_stream = old_values["ask_gpt_stream"]
        pipeline.trigger_expression_lifecycle = old_values["trigger_expression_lifecycle"]
        pipeline.get_memory_spine = old_values["get_memory_spine"]
        pipeline.extract_important = old_values["extract_important"]
        pipeline.update_emotion = old_values["update_emotion"]
        pipeline.save_chat_log = old_values["save_chat_log"]
        pipeline.save_memory_async = old_values["save_memory_async"]
        pipeline.awareness_memory_note_user_chat = old_values["awareness_memory_note_user_chat"]
        pipeline.observe_text_for_persona = old_values["observe_text_for_persona"]
        pipeline.build_live_awareness_snapshot = old_values["build_live_awareness_snapshot"]
        pipeline.memory_lock = old_values["memory_lock"]
        pipeline.memory = old_values["memory"]
        pipeline.cli_globals.last_gpt_time = old_values["last_gpt_time"]
    print("  PASSED")


def main():
    print("STAGE-9Y Voice Delivery - Smoke Tests")
    _test_plan_keeps_tail_and_is_read_only()
    _test_flush_helper_matches_story_delivery_rule()
    _test_status_preview_and_router_are_safe()
    _test_firewall_help_registry_stream_ready_and_stage_status()
    _test_private_pipeline_uses_full_voice_policy()
    print("[9Y Smoke] All tests passed.")


if __name__ == "__main__":
    main()
