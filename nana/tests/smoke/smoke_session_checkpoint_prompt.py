"""Checkpoint prompt wiring smoke with fake responders and public tripwires."""
from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys
import time
import types


FIXTURE_PATH = Path(__file__).with_name("smoke_memory_v2_public_prompt.py")
spec = importlib.util.spec_from_file_location("checkpoint_prompt_fixture", FIXTURE_PATH)
fixture = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = fixture
assert spec.loader is not None
spec.loader.exec_module(fixture)

SENTINEL = "TEMP-RABBIT-481: chú thỏ đội mũ cam ăn bánh hình sao."


def _configure_private(gpt):
    boundary = types.SimpleNamespace(
        public=False,
        livestream=False,
        interaction_scope="private_owner",
        prompt_block="PRIVATE BOUNDARY",
    )
    context = {
        "active_zone": "desk",
        "active_app": "chat",
        "idle_state": "active",
        "time": {},
        "browser": {},
    }
    gpt._lane_first_inputs = lambda **_kwargs: (
        boundary,
        context,
        {},
        {"affection": 0.5, "annoyance": 0.0, "playfulness": 0.5},
    )
    gpt._private_memory_evidence_for_turn = lambda *_args, **_kwargs: None
    gpt.classify_private_fast_lane = lambda *_args, **_kwargs: types.SimpleNamespace(
        eligible=False
    )
    gpt.record_fast_lane_decision = lambda *_args, **_kwargs: None
    gpt.resolve_user = lambda **_kwargs: {"name": "Ba"}
    gpt.format_identity_block = lambda *_args, **_kwargs: "PRIVATE IDENTITY"
    gpt._identity_prompt_blocks_for_boundary = lambda _boundary: ("PRIVATE CORE", "", "")
    gpt._mood_prompt_for_boundary = lambda _boundary: ""
    gpt._observe_mood_for_turn = lambda *_args, **_kwargs: None
    gpt.format_live_awareness_prompt = lambda _awareness: "LIVE AWARENESS"
    gpt._get_spine = lambda: types.SimpleNamespace(
        build_retrieval_block=lambda *_args, **_kwargs: "RELEVANT MEMORY: durable-only"
    )
    gpt._get_natural_style_hint = lambda: ""
    gpt.get_awareness_memory = lambda: types.SimpleNamespace(
        format_recent_moments_block=lambda **_kwargs: "RECENT MOMENTS: unrelated",
        get_recent=lambda **_kwargs: [],
        format_timeline_summary=lambda **_kwargs: "",
    )
    gpt.persona_prompt_block = lambda **_kwargs: "PRIVATE GOVERNOR"
    gpt.format_prompt_memory_rules = lambda *_args, **_kwargs: ""
    gpt.load_recent_chat = lambda *_args, **_kwargs: "unrelated disk tail"
    gpt.is_temporal_question = lambda _text: False
    gpt.is_browser_question = lambda _text: False
    gpt.is_music_reference_question = lambda _text: False
    gpt.NANA_SHARED_HISTORY = ""
    gpt.memory = {
        "emotion": {"affection": 0.5, "annoyance": 0.0, "playfulness": 0.5},
        "short_term": ["unrelated short tail"] * 16,
        "long_term": [],
        "session_checkpoint": {"sentinel": SENTINEL},
    }
    return boundary


def test_private_sync_and_stream_receive_checkpoint_after_durable_memory():
    gpt, captured = fixture._install_isolated_dependencies()
    _configure_private(gpt)
    sys.modules["nana.memory"].memory = gpt.memory
    checkpoint = __import__(
        "nana.runtime.session_checkpoint",
        fromlist=["record_private_turn"],
    )
    started_at = time.time()
    checkpoint.record_private_turn(
        gpt.memory,
        user_text=SENTINEL,
        nana_text="Nana heard the story detail.",
        event_id="private-rabbit",
        now=started_at,
    )
    for number in range(2, 19):
        checkpoint.record_private_turn(
            gpt.memory,
            user_text=f"Branch question {number} is open?",
            nana_text=f"Branch answer {number}.",
            event_id=f"private-{number}",
            now=started_at + number,
        )
    calls = []
    real_formatter = checkpoint.format_private_checkpoint_prompt
    checkpoint.format_private_checkpoint_prompt = lambda store: calls.append(store) or real_formatter(
        store,
        now=started_at + 19,
    )

    sync_reply = gpt.ask_gpt("Tiếp tục chi tiết câu chuyện đang dở.")

    async def consume():
        return "".join([
            chunk async for chunk in gpt.ask_gpt_stream("Tiếp tục chi tiết câu chuyện đang dở.")
        ])

    stream_reply = asyncio.run(consume())
    assert sync_reply and stream_reply
    assert len(calls) >= 2, calls
    assert len(captured["sync"]) == 1 and len(captured["stream"]) == 1, captured
    for messages in captured["sync"] + captured["stream"]:
        prompt = messages[0]["content"]
        assert SENTINEL in prompt
        assert "unrelated disk tail" in prompt
        assert prompt.index("RELEVANT MEMORY: durable-only") < prompt.index(SENTINEL)
        assert prompt.index(SENTINEL) < prompt.index("RECENT MOMENTS: unrelated")


def test_public_sync_and_stream_never_call_checkpoint_formatter():
    gpt, captured = fixture._install_isolated_dependencies()
    module = types.ModuleType("nana.runtime.session_checkpoint")

    def forbidden(_store):
        raise AssertionError("public prompt touched private session checkpoint")

    module.format_private_checkpoint_prompt = forbidden
    sys.modules[module.__name__] = module
    metadata = {
        "author_id": "viewer-7",
        "room_id": "room-a",
        "stream_session_id": "stream-1",
        "event_id": "event-1",
    }
    sync = gpt.ask_gpt(
        "normal public question",
        viewer_name="Alex",
        stream_mode=True,
        public_platform="youtube",
        metadata=metadata,
    )

    async def consume():
        return "".join([
            chunk async for chunk in gpt.ask_gpt_stream(
                "normal public question",
                viewer_name="Alex",
                stream_mode=True,
                public_platform="youtube",
                metadata=metadata,
            )
        ])

    stream = asyncio.run(consume())
    assert sync and stream
    assert SENTINEL not in str(captured)


def test_bridge_scope_never_calls_checkpoint_formatter():
    gpt, _captured = fixture._install_isolated_dependencies()
    module = types.ModuleType("nana.runtime.session_checkpoint")
    module.format_private_checkpoint_prompt = lambda _store: (_ for _ in ()).throw(
        AssertionError("bridge touched private checkpoint")
    )
    sys.modules[module.__name__] = module
    bridge = types.SimpleNamespace(public=False, interaction_scope="bridge_system")
    assert gpt._private_session_checkpoint_block(bridge) == ""


def run_all():
    tests = [
        test_private_sync_and_stream_receive_checkpoint_after_durable_memory,
        test_public_sync_and_stream_never_call_checkpoint_formatter,
        test_bridge_scope_never_calls_checkpoint_formatter,
    ]
    failures = 0
    for test in tests:
        try:
            test()
            print(f"PASS {test.__name__}")
        except Exception as exc:
            failures += 1
            print(f"FAIL {test.__name__}: {type(exc).__name__}: {exc}")
    print(f"Session checkpoint prompt: {len(tests) - failures} passed, {failures} failed")
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(run_all())
