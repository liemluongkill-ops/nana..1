"""Task 8 one-turn source capture and private pipeline regressions."""

from __future__ import annotations

import asyncio
import copy
from dataclasses import FrozenInstanceError
import math
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from nana.runtime import awareness_memory, context as runtime_context, context_runtime
from nana.runtime import context_adapters, live_awareness
from nana.runtime.context_contracts import (
    ContextContractError,
    Freshness,
    Lane,
    Route,
    SourceSnapshot,
    UnresolvedContextRequest,
)


class _PrivateAuthority:
    def authorize(self, _request):
        return context_runtime.TrustedIngress(Lane.PRIVATE_OWNER, None)


@pytest.fixture(autouse=True)
def _offline_expression_capture(monkeypatch):
    def capture(*, request):
        captured_at = request.captured_wall_time
        return SourceSnapshot(
            source="expression_private",
            revision="expression-private-offline-fixture",
            observed_at=captured_at,
            captured_at=captured_at,
            freshness=Freshness.FRESH,
            payload={
                "mood": {
                    "tone": "steady",
                    "energy": 0.5,
                    "focus": 0.5,
                    "tension": 0.18,
                },
                "affect": {
                    "warmth": 0.6,
                    "playfulness": 0.48,
                    "assertiveness": 0.3,
                    "intimacy": 0.86,
                },
                "persona": {"mode": "chill", "clamp": "open"},
            },
        )

    monkeypatch.setattr(
        context_runtime,
        "capture_private_expression_sources",
        capture,
    )
    monkeypatch.setattr(
        context_runtime,
        "capture_private_checkpoint_source",
        lambda *, request: SourceSnapshot(
            source="private_checkpoint",
            revision="private-checkpoint-offline-fixture",
            observed_at=None,
            captured_at=request.captured_wall_time,
            freshness=Freshness.UNKNOWN,
            payload={
                "available": False,
                "session_id": "",
                "summary": "",
                "anchors": [],
                "pending_turns": [],
            },
        ),
    )
    monkeypatch.setattr(
        context_runtime,
        "capture_private_memory_source",
        lambda *, request: SourceSnapshot(
            source="private_memory",
            revision="private-memory-offline-fixture",
            observed_at=request.captured_wall_time,
            captured_at=request.captured_wall_time,
            freshness=Freshness.FRESH,
            payload={"long_term": []},
        ),
    )


def _private_request(*, wall_time=1_000.0, monotonic_time=500.0, temporal=False):
    raw = UnresolvedContextRequest(
        request_id="task8-private-request",
        correlation_id="task8-private-correlation",
        route=Route.INTERACTIVE,
        model="fake-private-model",
        current_input="What is happening now?",
        viewer_name=None,
        stream_mode=False,
        public_platform=None,
        caller_metadata={},
        bridge_system=False,
        story_mode=False,
        casual_mode=False,
        temporal_intent=temporal,
        grounding_intent=False,
    )
    return context_runtime.LaneResolver(
        _PrivateAuthority(),
        wall_clock=lambda: wall_time,
        monotonic_clock=lambda: monotonic_time,
    ).resolve(raw)


def _seed_private_sources(monkeypatch, *, wall_time=1_000.0):
    state = copy.deepcopy(runtime_context.context_state)
    state.update(
        {
            "active_app": "chrome",
            "active_title": "Fixture Window",
            "active_zone": "chill",
            "idle_seconds": 7.25,
            "idle_state": "active",
            "in_flow": True,
            "poll_rate": 99.0,
        }
    )
    state["browser"].update(
        {
            "available": True,
            "browser": "chrome",
            "url": "https://example.test/article",
            "title": "Fixture Article",
            "kind": "docs",
            "reason": "fixture",
            "page_heading": "Fixture Heading",
            "meta_description": "Fixture Meta",
            "selected_text": "Selected fixture text",
            "dom_debug": {"secret": ["DOM-SENTINEL"]},
            "site_signals": {"nested": {"value": "SITE-SENTINEL"}},
            "social_post_text": "Social fixture post",
            "social_comments": ["COMMENT-SENTINEL"],
            "social_vibe": "calm",
            "local_summary": "Derived fixture summary",
            "local_helper_debug": {"trace": "HELPER-SENTINEL"},
            "last_update": wall_time - 20.0,
            "age_seconds": 999.0,
            "fresh": False,
        }
    )
    state["proactive"]["presence_debug"]["debug"] = "TRANSPORT-SENTINEL"
    monkeypatch.setattr(runtime_context, "context_state", state)
    monkeypatch.setattr(
        live_awareness,
        "_sticky_state",
        {
            "locked": False,
            "locked_at": 0.0,
            "locked_title": "",
            "locked_url": "",
            "locked_focus_text": "",
            "lock_reason": "",
            "last_snap": {},
            "unlock_at": 0.0,
            "auto_unlock_seconds": 30.0,
        },
    )
    memory = awareness_memory.AwarenessMemory()
    memory.add_moment(
        awareness_memory.Moment(
            id="aw1",
            timestamp=wall_time - 30.0,
            monotonic=470.0,
            source=awareness_memory.SRC_AWARENESS,
            active_app="chrome",
            active_zone="chill",
            browser_kind="docs",
            title="Earlier fixture article",
            focus_source="selected_text",
            focus_text="Earlier selected text",
            confidence=0.9,
            frozen_awareness={
                "browser_available": True,
                "browser_kind": "docs",
                "browser_title": "Earlier fixture article",
                "browser_url": "https://example.test/earlier",
                "focus_source": "selected_text",
                "focus_text": "Earlier selected text",
            },
            frozen_url="https://example.test/earlier",
            frozen_title="Earlier fixture article",
        )
    )
    monkeypatch.setattr(awareness_memory, "_awareness_memory", memory)
    return state, memory


def test_capture_recursively_freezes_nested_owner_state(monkeypatch):
    state, _memory = _seed_private_sources(monkeypatch)
    request = _private_request()

    snapshot = context_runtime.capture_turn_snapshot(request)

    assert isinstance(snapshot, context_runtime.RuntimeContextSnapshot)
    assert snapshot.request is request
    assert snapshot.browser_state.payload["site_signals"]["nested"]["value"] == "SITE-SENTINEL"

    state["browser"]["site_signals"]["nested"]["value"] = "late mutation"
    state["browser"]["dom_debug"]["secret"].append("late mutation")
    assert snapshot.browser_state.payload["site_signals"]["nested"]["value"] == "SITE-SENTINEL"
    assert tuple(snapshot.browser_state.payload["dom_debug"]["secret"]) == ("DOM-SENTINEL",)

    with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
        snapshot.browser_state.payload["site_signals"]["nested"]["value"] = "mutated"


def test_content_revisions_ignore_capture_age_and_debug_but_track_semantics(monkeypatch):
    state, _memory = _seed_private_sources(monkeypatch)
    request = _private_request()

    first = context_runtime.capture_turn_snapshot(request)
    state["poll_rate"] = 1.0
    state["browser"]["age_seconds"] = 123_456.0
    state["browser"]["fresh"] = not state["browser"]["fresh"]
    state["browser"]["dom_debug"] = {"changed": True}
    state["browser"]["local_helper_debug"] = {"changed": True}
    state["browser"]["site_signals"] = {"changed": True}
    second = context_runtime.capture_turn_snapshot(request)

    assert first.source_revision("runtime_context") == second.source_revision("runtime_context")
    assert first.source_revision("browser_state") == second.source_revision("browser_state")
    assert first.source_revision("live_awareness") == second.source_revision("live_awareness")
    assert all(
        revision.revision_kind in {"content_hash", "semantic_revision"}
        for revision in first.source_revisions
    )
    assert next(
        revision.revision_kind
        for revision in first.source_revisions
        if revision.source == "expression_private"
    ) == "content_hash"

    state["browser"]["title"] = "Semantically new article"
    third = context_runtime.capture_turn_snapshot(request)
    assert third.source_revision("browser_state") != second.source_revision("browser_state")
    assert third.source_revision("live_awareness") != second.source_revision("live_awareness")


def test_content_revisions_do_not_change_when_only_capture_clock_advances(monkeypatch):
    _state, _memory = _seed_private_sources(monkeypatch)
    first = context_runtime.capture_turn_snapshot(
        _private_request(wall_time=1_000.0, monotonic_time=500.0)
    )
    second = context_runtime.capture_turn_snapshot(
        _private_request(wall_time=1_100.0, monotonic_time=600.0)
    )

    for source in ("runtime_context", "browser_state", "live_awareness", "awareness_memory"):
        assert first.source_revision(source) == second.source_revision(source)


def test_awareness_memory_revision_excludes_age_and_debug_but_tracks_meaning():
    memory = awareness_memory.AwarenessMemory()
    frozen = {
        "browser_available": True,
        "browser_kind": "docs",
        "browser_title": "Semantic title",
        "browser_url": "https://example.test/semantic",
        "focus_source": "selected_text",
        "focus_text": "Semantic focus",
        "browser_age_seconds": 1.0,
        "idle_seconds": 2.0,
        "time": {"time": "00:00:01"},
        "browser_site_signals": {"debug": "first"},
        "router": {"debug": "first"},
        "operator_debug": "first",
    }
    moment = awareness_memory.Moment(
        id="semantic-event",
        timestamp=990.0,
        monotonic=490.0,
        source=awareness_memory.SRC_AWARENESS,
        browser_kind="docs",
        title="Semantic title",
        focus_source="selected_text",
        focus_text="Semantic focus",
        frozen_awareness=frozen,
        frozen_url="https://example.test/semantic",
        frozen_title="Semantic title",
    )
    memory.add_moment(moment)
    first = memory.capture_source_snapshot(captured_at=1_000.0)

    frozen["browser_age_seconds"] = 99.0
    frozen["idle_seconds"] = 77.0
    frozen["time"] = {"time": "00:00:59"}
    frozen["browser_site_signals"] = {"debug": "second"}
    frozen["router"] = {"debug": "second"}
    frozen["operator_debug"] = "second"
    second = memory.capture_source_snapshot(captured_at=1_000.0)
    assert second.revision == first.revision

    frozen["focus_text"] = "Semantically changed focus"
    third = memory.capture_source_snapshot(captured_at=1_000.0)
    assert third.revision != second.revision


def test_source_instability_retries_once_then_fails_closed(monkeypatch):
    _seed_private_sources(monkeypatch)
    request = _private_request()
    capture_owner = runtime_context.capture_runtime_context_sources
    changed = context_adapters.SourceCaptureChanged
    calls = []

    def changes_once(**kwargs):
        calls.append("capture")
        if len(calls) == 1:
            raise changed("runtime_context")
        return capture_owner(**kwargs)

    monkeypatch.setattr(runtime_context, "capture_runtime_context_sources", changes_once)
    context_runtime.capture_turn_snapshot(request)
    assert calls == ["capture", "capture"]

    calls.clear()

    def always_changes(**_kwargs):
        calls.append("capture")
        raise changed("runtime_context")

    monkeypatch.setattr(runtime_context, "capture_runtime_context_sources", always_changes)
    with pytest.raises(ContextContractError) as exc_info:
        context_runtime.capture_turn_snapshot(request)
    assert exc_info.value.code == "source_changed_during_capture"
    assert calls == ["capture", "capture"]


def test_sticky_focus_capture_preserves_source_and_never_auto_unlocks(monkeypatch):
    state, _memory = _seed_private_sources(monkeypatch)
    live_awareness.lock_focus(
        {
            "browser_title": "Locked title",
            "browser_url": "https://example.test/locked",
            "focus_source": "local_summary",
            "focus_text": "Locked local summary",
        },
        reason="user_question",
    )
    state["browser"]["title"] = "Drifted title"
    state["browser"]["url"] = "https://example.test/drifted"
    state["browser"]["selected_text"] = "Drifted selected text"

    captured = context_runtime.capture_turn_snapshot(_private_request())
    awareness = captured.awareness_mapping()
    assert awareness["focus_source"] == "local_summary"
    assert awareness["focus_text"] == "Locked local summary"
    assert live_awareness._sticky_state["locked"] is True

    live_awareness._sticky_state["unlock_at"] = 499.0
    expired = context_runtime.capture_turn_snapshot(_private_request())
    assert expired.awareness_mapping()["is_sticky_locked"] is False
    assert live_awareness._sticky_state["locked"] is True


def test_public_request_is_denied_before_any_private_source_read(monkeypatch):
    from nana.runtime.public_context_boundary import PublicEventScope
    from nana.runtime.public_identity import CanonicalPublicIdentity

    identity = CanonicalPublicIdentity("youtube", "viewer-1", "youtube:viewer-1")
    scope = PublicEventScope(
        "youtube", "room-1", "stream-1", "event-1", "Viewer One", identity
    )

    class PublicAuthority:
        def authorize(self, _request):
            return context_runtime.TrustedIngress(Lane.PUBLIC_STAGE, scope)

    raw = UnresolvedContextRequest(
        "task8-public-request",
        "task8-public-correlation",
        Route.INTERACTIVE,
        "fake-public-model",
        "hello public",
        "Viewer One",
        True,
        "youtube",
        {
            "platform": "youtube",
            "room_id": "room-1",
            "stream_session_id": "stream-1",
            "event_id": "event-1",
            "display_name": "Viewer One",
            "author_id": "viewer-1",
        },
        False,
        False,
        False,
        False,
        False,
    )
    request = context_runtime.LaneResolver(PublicAuthority()).resolve(raw)
    calls = []

    def private_tripwire(**_kwargs):
        calls.append("private")
        raise AssertionError("private source read")

    monkeypatch.setattr(runtime_context, "capture_runtime_context_sources", private_tripwire)
    with pytest.raises(ContextContractError) as exc_info:
        context_runtime.capture_turn_snapshot(request)
    assert exc_info.value.code == "private_source_capture_denied"
    assert calls == []


@pytest.mark.parametrize("empty_stream", [False, True])
def test_pipeline_reuses_one_snapshot_for_stream_fallback_and_history(monkeypatch, empty_stream):
    smoke_root = ROOT / "tests" / "smoke"
    if str(smoke_root) not in sys.path:
        sys.path.insert(0, str(smoke_root))
    from smoke_history_voice_consistency import Voice, pipeline_fixture

    with pipeline_fixture() as (pipeline, _memory, _tags, notes):
        awareness = {"focus_text": "one captured focus"}
        snapshot = SimpleNamespace(awareness_mapping=lambda: awareness)
        prepared = object()
        capture_calls = []
        stream_seen = []
        sync_seen = []

        def capture_once(**_kwargs):
            capture_calls.append("capture")
            return prepared, snapshot

        async def stream(*_args, **kwargs):
            stream_seen.append(
                (kwargs.get("_prepared_private_turn"), kwargs.get("_turn_snapshot"))
            )
            if not empty_stream:
                yield "one reply"

        def sync(*_args, **kwargs):
            sync_seen.append(
                (kwargs.get("_prepared_private_turn"), kwargs.get("_turn_snapshot"))
            )
            return "fallback reply"

        monkeypatch.setattr(pipeline, "_capture_private_model_snapshot", capture_once)
        monkeypatch.setattr(pipeline, "ask_gpt_stream", stream)
        monkeypatch.setattr(pipeline, "ask_gpt", sync)

        asyncio.run(pipeline.handle_chat_turn(None, Voice(), "ordinary model turn", None))

        assert capture_calls == ["capture"]
        assert stream_seen == [(prepared, snapshot)]
        assert sync_seen == ([(prepared, snapshot)] if empty_stream else [])
        assert notes and notes[-1]["awareness"] is awareness


def test_empty_stream_real_finalizer_uses_captured_awareness_without_live_recapture(monkeypatch):
    smoke_root = ROOT / "tests" / "smoke"
    if str(smoke_root) not in sys.path:
        sys.path.insert(0, str(smoke_root))
    from smoke_history_voice_consistency import Voice, pipeline_fixture

    captured = {
        "browser_available": True,
        "browser_fresh": True,
        "browser_kind": "docs",
        "browser_title": "Captured title",
        "focus_source": "selected_text",
        "focus_text": "CAPTURED-A",
    }
    late = {
        **captured,
        "browser_title": "Late title",
        "focus_text": "LATE-B",
    }
    real_repair = live_awareness.repair_awareness_reply
    late_reads = []

    def late_capture():
        late_reads.append("read")
        return late

    monkeypatch.setitem(
        real_repair.__globals__,
        "build_live_awareness_snapshot",
        late_capture,
    )

    with pipeline_fixture() as (pipeline, _memory, _tags, notes):
        core_surface = sys.modules["nana.core.chat_surface"]
        monkeypatch.setattr(core_surface, "repair_awareness_reply", real_repair)
        monkeypatch.setattr(pipeline, "finalize_live_reply", core_surface.finalize_live_reply)
        snapshot = SimpleNamespace(awareness_mapping=lambda: captured)
        monkeypatch.setattr(
            pipeline,
            "_capture_private_model_snapshot",
            lambda **_kwargs: (object(), snapshot),
        )

        async def empty_stream(*_args, **_kwargs):
            if False:
                yield "unreachable"

        monkeypatch.setattr(pipeline, "ask_gpt_stream", empty_stream)
        monkeypatch.setattr(
            pipeline,
            "ask_gpt",
            lambda *_args, **_kwargs: "Con khong the xem.",
        )
        voice = Voice()
        asyncio.run(
            pipeline.handle_chat_turn(
                None,
                voice,
                "Nana đang thấy gì?",
                None,
            )
        )

        assert late_reads == []
        assert notes[-1]["awareness"] is captured
        assert "CAPTURED-A" in voice.normal[-1]
        assert "LATE-B" not in voice.normal[-1]


def test_frozen_moment_selection_filters_expiry_before_limits():
    memory = awareness_memory.AwarenessMemory()
    memory.add_moment(
        awareness_memory.Moment(
            id="valid",
            timestamp=1_950.0,
            monotonic=450.0,
            source=awareness_memory.SRC_SYSTEM,
            focus_text="VALID-OLDER",
            ttl_seconds=600.0,
        )
    )
    for index, timestamp in enumerate((1_980.0, 1_981.0, 1_982.0), 1):
        memory.add_moment(
            awareness_memory.Moment(
                id=f"expired-{index}",
                timestamp=timestamp,
                monotonic=480.0 + index,
                source=awareness_memory.SRC_SYSTEM,
                focus_text=f"EXPIRED-{index}",
                ttl_seconds=1.0,
            )
        )

    owner_ids = [
        moment.id
        for moment in memory.get_recent(limit=3, now=2_000.0)
    ]
    source = memory.capture_source_snapshot(captured_at=2_000.0)
    view = context_runtime.FrozenAwarenessMemoryView(source, 2_000.0)
    captured_ids = [moment.id for moment in view.get_recent(limit=3)]
    assert owner_ids == captured_ids == ["valid"]

    legacy = awareness_memory.AwarenessMemory()
    for index in range(6):
        legacy.add_moment(
            awareness_memory.Moment(
                id=f"valid-{index}",
                timestamp=1_900.0 + index,
                monotonic=400.0 + index,
                source=awareness_memory.SRC_AWARENESS,
                focus_text=f"VALID-{index}",
                ttl_seconds=600.0,
            )
        )
    legacy_view = context_runtime.FrozenAwarenessMemoryView(
        legacy.capture_source_snapshot(captured_at=2_000.0),
        2_000.0,
    )
    assert len(legacy_view.get_recent(limit=5)) == 5
    assert len(legacy_view.get_recent(limit=3)) == 3
    legacy_block = legacy_view.format_recent_moments_block(limit=5)
    temporal_block = legacy_view.format_timeline_summary(max_moments=3)
    assert sum(f"VALID-{index}" in legacy_block for index in range(6)) == 5
    assert sum(f"VALID-{index}" in temporal_block for index in range(6)) == 3


def test_frozen_recent_and_temporal_formatters_enforce_owner_caps():
    memory = awareness_memory.AwarenessMemory()
    memory.add_moment(
        awareness_memory.Moment(
            id="long-focus",
            timestamp=1_990.0,
            monotonic=490.0,
            source=awareness_memory.SRC_AWARENESS,
            focus_source="selected_text",
            focus_text="short preview",
            frozen_awareness={"focus_text": "F" * 360},
            ttl_seconds=600.0,
        )
    )
    memory.add_moment(
        awareness_memory.Moment(
            id="long-title",
            timestamp=1_991.0,
            monotonic=491.0,
            source=awareness_memory.SRC_AWARENESS,
            frozen_awareness={"focus_text": "", "browser_title": "T" * 260},
            ttl_seconds=600.0,
        )
    )
    view = context_runtime.FrozenAwarenessMemoryView(
        memory.capture_source_snapshot(captured_at=2_000.0),
        2_000.0,
    )

    for rendered in (
        view.format_recent_moments_block(limit=5),
        view.format_timeline_summary(max_moments=3),
    ):
        assert "F" * 217 in rendered
        assert "F" * 218 not in rendered
        assert "T" * 197 in rendered
        assert "T" * 198 not in rendered


def test_invalid_source_clocks_do_not_become_current_at_capture_time(monkeypatch):
    state, _memory = _seed_private_sources(monkeypatch)
    request = _private_request()
    for invalid in (None, math.nan, math.inf, request.captured_wall_time + 3.0):
        state["browser"]["last_update"] = invalid
        captured = context_runtime.capture_turn_snapshot(request)
        assert captured.browser_state.observed_at is None
        assert captured.browser_state.freshness.value == "unknown"
