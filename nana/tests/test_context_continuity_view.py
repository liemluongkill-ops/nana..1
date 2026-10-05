"""Task 10 typed continuity capture, projection, and canonical-seam contracts."""

from __future__ import annotations

import asyncio
import builtins
import copy
from dataclasses import FrozenInstanceError, replace
import importlib
from pathlib import Path
import re
import sys
import threading
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))
if str(ROOT / "tests" / "smoke") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests" / "smoke"))

from nana.runtime import context_adapters, context_runtime, session_checkpoint
from nana.runtime.context_contracts import (
    ContextContractError,
    ContextSection,
    Freshness,
    FrozenMapping,
    Lane,
    Lifetime,
    Route,
    SemanticRole,
    SourceRef,
    SourceSnapshot,
    UnresolvedContextRequest,
)
from nana.runtime.public_context_boundary import PublicEventScope
from nana.runtime.public_delivery_state import PublicDeliveryRecord
from nana.runtime.public_identity import CanonicalPublicIdentity
from nana.runtime.social_session import SocialSessionCache


NOW = 10_000.0


@pytest.fixture
def _isolated_nana_modules():
    before = {
        name: module
        for name, module in sys.modules.items()
        if name == "nana" or name.startswith("nana.")
    }
    yield
    for name in tuple(sys.modules):
        if (name == "nana" or name.startswith("nana.")) and name not in before:
            sys.modules.pop(name, None)
    sys.modules.update(before)


def _install_real_private_evidence_helper(gpt, grounding):
    """Load the product helper against the fake-only dependency graph."""

    spec = importlib.util.spec_from_file_location(
        "task10_real_gpt_evidence",
        ROOT / "brain" / "gpt.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ground_user_message = grounding.ground_user_message
    module.memory = gpt.memory
    module.memory_lock = gpt.memory_lock
    module._GROUNDING_AVAILABLE = True
    gpt._private_memory_evidence_for_turn = module._private_memory_evidence_for_turn
    return module


class _Authority:
    def __init__(self, lane, public_scope=None):
        self.lane = lane
        self.public_scope = public_scope

    def authorize(self, _request):
        return context_runtime.TrustedIngress(self.lane, self.public_scope)


def _private_request(text="carry on", *, temporal=False, grounding=False):
    raw = UnresolvedContextRequest(
        "task10-private-request",
        "task10-private-correlation",
        Route.INTERACTIVE,
        "fake-private-model",
        text,
        None,
        False,
        None,
        {},
        False,
        False,
        False,
        temporal,
        grounding,
    )
    return context_runtime.LaneResolver(
        _Authority(Lane.PRIVATE_OWNER),
        wall_clock=lambda: NOW,
        monotonic_clock=lambda: NOW / 2,
    ).resolve(raw)


def _operator_request(text="diagnose"):
    raw = UnresolvedContextRequest(
        "task10-operator-request",
        "task10-operator-correlation",
        Route.INTERACTIVE,
        "fake-operator-model",
        text,
        None,
        False,
        None,
        {},
        True,
        False,
        False,
        False,
        False,
    )
    return context_runtime.LaneResolver(
        _Authority(Lane.OPERATOR_BACKSTAGE),
        wall_clock=lambda: NOW,
        monotonic_clock=lambda: NOW / 2,
    ).resolve(raw)


def _public_scope(event_id, *, actor="youtube:viewer-1", room="room-1", session="stream-1"):
    author_id = actor.split(":", 1)[-1]
    identity = CanonicalPublicIdentity("youtube", author_id, actor)
    return PublicEventScope(
        "youtube",
        room,
        session,
        event_id,
        f"Viewer {author_id}",
        identity,
    )


def _public_request(event_id="public-current", *, text="hello room", scope=None):
    scope = scope or _public_scope(event_id)
    raw = UnresolvedContextRequest(
        f"task10-public-request-{event_id}",
        f"task10-public-correlation-{event_id}",
        Route.INTERACTIVE,
        "fake-public-model",
        text,
        scope.display_name,
        True,
        "youtube",
        {
            "platform": scope.platform,
            "room_id": scope.room_id,
            "stream_session_id": scope.stream_session_id,
            "event_id": scope.event_id,
            "display_name": scope.display_name,
            "author_id": scope.identity.author_id,
        },
        False,
        False,
        False,
        False,
        False,
    )
    return context_runtime.LaneResolver(
        _Authority(Lane.PUBLIC_STAGE, scope),
        wall_clock=lambda: NOW,
        monotonic_clock=lambda: NOW / 2,
    ).resolve(raw)


def _thaw(value):
    if isinstance(value, FrozenMapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _source(name, payload, *, observed=NOW - 1, fresh=Freshness.FRESH):
    return SourceSnapshot(
        source=name,
        revision=f"{name}-fixture-r1",
        observed_at=observed,
        captured_at=NOW,
        freshness=fresh,
        payload=payload,
    )


def _checkpoint_payload(*, summary="Keep the migration focused.", anchors=None, turns=None):
    return {
        "available": True,
        "session_id": "private-session-task10",
        "summary": summary,
        "anchors": list(
            anchors
            if anchors is not None
            else (
                {
                    "event_id": "anchor-decision",
                    "turn_index": 1,
                    "kind": "decision",
                    "text": "Decision: retain the typed boundary.",
                    "created_at": NOW - 40,
                },
                {
                    "event_id": "anchor-loop",
                    "turn_index": 2,
                    "kind": "open_loop",
                    "text": "Need to verify the final parity run?",
                    "created_at": NOW - 30,
                },
            )
        ),
        "pending_turns": list(
            turns
            if turns is not None
            else (
                {
                    "event_id": "turn-prior",
                    "turn_index": 3,
                    "user_text": "The prior completed question",
                    "nana_text": "The prior completed answer",
                    "created_at": NOW - 20,
                },
            )
        ),
    }


def _moment(index, *, source="awareness", text=None):
    return {
        "id": f"moment-{index}",
        "timestamp": NOW - index,
        "monotonic": NOW / 2 - index,
        "source": source,
        "active_app": "chrome",
        "active_zone": "focus",
        "browser_kind": "docs",
        "title": f"Moment title {index}",
        "url_host": "example.test",
        "focus_source": "selected_text",
        "focus_text": text or f"Moment focus {index}",
        "user_text": "chat user" if source == "chat" else "",
        "nana_text": "chat nana" if source == "chat" else "",
        "confidence": 0.9,
        "importance": "medium",
        "ttl_seconds": 600.0,
        "can_act": False,
        "frozen_awareness": None,
        "frozen_url": "",
        "frozen_title": f"Moment title {index}",
        "turn_counter": index,
    }


def _snapshot(request, *, checkpoint=None, moments=None, include_checkpoint=True):
    runtime = _source(
        "runtime_context",
        {
            "active_app": "chrome",
            "active_zone": "focus",
            "idle_state": "active",
            "in_flow": True,
            "time": {},
        },
        observed=None,
        fresh=Freshness.UNKNOWN,
    )
    browser = _source(
        "browser_state",
        {"available": False},
        observed=None,
        fresh=Freshness.UNKNOWN,
    )
    awareness = _source(
        "live_awareness",
        {
            "browser_available": False,
            "focus_source": "none",
            "focus_text": "",
            "focus_confidence": 0.0,
        },
        observed=None,
        fresh=Freshness.UNKNOWN,
    )
    memory = _source(
        "awareness_memory",
        {"enabled": True, "moments": list(moments or ())},
    )
    expression = _source(
        "expression_private",
        {
            "mood": {"tone": "steady", "energy": 0.5, "focus": 0.7, "tension": 0.1},
            "affect": {"warmth": 0.6, "playfulness": 0.4, "assertiveness": 0.3, "intimacy": 0.8},
            "persona": {"mode": "focus", "clamp": "strict"},
        },
    )
    checkpoint_source = (
        _source("private_checkpoint", checkpoint or _checkpoint_payload())
        if include_checkpoint
        else None
    )
    sources = [runtime, browser, awareness, memory, expression]
    if checkpoint_source is not None:
        sources.append(checkpoint_source)
    revisions = tuple(
        context_adapters.SourceRevision(item.source, item.revision, "content_hash")
        for item in sorted(sources, key=lambda item: item.source)
    )
    return context_runtime.RuntimeContextSnapshot(
        request=request,
        snapshot_revision=1,
        captured_at=NOW,
        captured_monotonic_at=NOW / 2,
        runtime_context=runtime,
        browser_state=browser,
        live_awareness=awareness,
        awareness_memory=memory,
        source_revisions=revisions,
        expression=expression,
        private_checkpoint=checkpoint_source,
    )


def _private_scope(*, session_id="private-session-task10", owner="private_session"):
    return context_adapters.ContinuityScope(
        lane=Lane.PRIVATE_OWNER,
        visibility=frozenset({Lane.PRIVATE_OWNER}),
        owner=owner,
        session_id=session_id,
    )


def _old_turn(text="An explicitly selected older turn", *, event_id="old-event-1"):
    return context_adapters.RelevantOldTurnRecord(
        scope=_private_scope(),
        text=text,
        reply_text="Older grounded reply",
        event_id=event_id,
        turn_id="old-turn-1",
        provenance_record_id="old-record-1",
        occurred_at=NOW - 500,
    )


@pytest.mark.parametrize(
    ("question", "expected_authority", "expected_order", "expected_sections"),
    (
        (
            "Please continue that thought",
            "ordinary",
            ("recent_turns", "summary", "anchors"),
            ("continuity.summary.v1", "continuity.recent_turns.v1"),
        ),
        (
            "What are we doing and which open loop is next?",
            "active_work",
            ("open_loops", "summary", "anchors"),
            ("continuity.summary.v1", "continuity.open_loops.v1"),
        ),
        (
            "What did we say in that older conversation last week?",
            "old_dialogue_recall",
            ("relevant_old_turns", "memory"),
            ("continuity.recent_turns.v1",),
        ),
        (
            "What is on the current browser right now?",
            "current_browser",
            ("current_situation",),
            (),
        ),
        (
            "Nãy giờ có chuyện gì?",
            "recent_temporal",
            ("recent_moments", "recent_turns"),
            ("continuity.recent_turns.v1", "situation.temporal.v1"),
        ),
        (
            "What is my durable personal preference?",
            "durable_fact",
            ("memory",),
            (),
        ),
    ),
)
def test_question_authority_selects_only_its_owned_records(
    question, expected_authority, expected_order, expected_sections
):
    request = _private_request(
        question,
        temporal=expected_authority == "recent_temporal",
        grounding=expected_authority in {"old_dialogue_recall", "durable_fact"},
    )
    snapshot = _snapshot(request, moments=[_moment(1), _moment(2)])

    view = context_adapters.build_continuity_view(
        request,
        snapshot=snapshot,
        relevant_old_turns=(_old_turn(),),
    )
    sections = context_adapters.build_continuity_sections(view, request)

    assert view.authority.value == expected_authority
    assert view.authority_order == expected_order
    assert tuple(section.id for section in sections) == expected_sections
    assert all(question not in record.text for record in view.records)
    if expected_authority == "current_browser":
        assert not view.records
    if expected_authority == "durable_fact":
        assert not view.records


def test_classifier_precedence_requires_explicit_old_dialogue_language():
    cases = (
        ("Show the current browser from our older conversation", "current_browser"),
        ("In the older conversation, what was my personal preference?", "old_dialogue_recall"),
        ("What is my personal preference for this active work?", "durable_fact"),
        ("What are we doing nãy giờ?", "active_work"),
        ("Summarize market changes last week", "ordinary"),
        ("Which tab is open?", "current_browser"),
        ("Ba đang mở tab nào?", "current_browser"),
        ("What is my birthday?", "durable_fact"),
        ("Where do I live?", "durable_fact"),
        ("What allergies do I have?", "durable_fact"),
        ("Sinh nhật của Ba là ngày nào?", "durable_fact"),
        ("Ba dị ứng với gì?", "durable_fact"),
    )
    assert [
        context_adapters.classify_continuity_authority(text).value
        for text, _expected in cases
    ] == [expected for _text, expected in cases]


@pytest.mark.parametrize(
    ("text", "expected"),
    (
        ("Which tab is open?", "current_browser"),
        ("Ba đang mở tab nào?", "current_browser"),
        ("What is my birthday?", "durable_fact"),
        ("Where do I live?", "durable_fact"),
        ("What allergies do I have?", "durable_fact"),
    ),
)
def test_common_exclusive_authorities_never_receive_ordinary_continuity(text, expected):
    request = _private_request(text, grounding=expected == "durable_fact")
    view = context_adapters.build_continuity_view(
        request,
        snapshot=_snapshot(request),
    )

    assert view.authority.value == expected
    assert view.records == ()


def test_private_caps_select_newest_whole_records_and_keep_source_bounds():
    anchors = [
        {
            "event_id": f"loop-{index}",
            "turn_index": index + 1,
            "kind": "open_loop",
            "text": f"Open loop {index} " + ("L" * 400),
            "created_at": NOW - 100 + index,
        }
        for index in range(6)
    ] + [
        {
            "event_id": f"anchor-{index}",
            "turn_index": index + 20,
            "kind": "episode",
            "text": f"Anchor {index} " + ("A" * 400),
            "created_at": NOW - 50 + index,
        }
        for index in range(8)
    ]
    turns = [
        {
            "event_id": f"turn-{index}",
            "turn_index": index + 40,
            "user_text": f"user-{index}-" + ("U" * 400),
            "nana_text": f"nana-{index}-" + ("N" * 300),
            "created_at": NOW - 20 + index,
        }
        for index in range(8)
    ]
    request = _private_request("continue")
    view = context_adapters.build_continuity_view(
        request,
        snapshot=_snapshot(
            request,
            checkpoint=_checkpoint_payload(
                summary="S" * 1_500,
                anchors=anchors,
                turns=turns,
            ),
        ),
    )

    assert len(view.summary.text) == 1_200
    assert [record.event_id for record in view.anchors] == [
        "anchor-2",
        "anchor-3",
        "anchor-4",
        "anchor-5",
        "anchor-6",
        "anchor-7",
    ]
    assert all(len(record.text) == 280 for record in view.anchors)
    assert [record.event_id for record in view.recent_turns] == [
        "turn-2",
        "turn-3",
        "turn-4",
        "turn-5",
        "turn-6",
        "turn-7",
    ]
    assert all(len(record.text) == 280 for record in view.recent_turns)
    assert all(len(record.reply_text) == 200 for record in view.recent_turns)

    active_request = _private_request("continue our active work")
    active = context_adapters.build_continuity_view(
        active_request,
        snapshot=_snapshot(
            active_request,
            checkpoint=_checkpoint_payload(summary="S" * 1_500, anchors=anchors, turns=turns),
        ),
    )
    assert [record.event_id for record in active.open_loops] == [
        "loop-1",
        "loop-2",
        "loop-3",
        "loop-4",
        "loop-5",
    ]
    assert all(len(record.text) == 240 for record in active.open_loops)


def test_old_turn_and_recent_moment_caps_exclude_chat_before_selection():
    request = _private_request("What happened just now?", temporal=True)
    moments = [_moment(99, source="chat")] + [
        _moment(index, text=("F" * 300 if index == 1 else None))
        for index in range(1, 6)
    ]
    old = tuple(
        context_adapters.RelevantOldTurnRecord(
            scope=_private_scope(),
            text=f"old-{index}-" + ("O" * 700),
            reply_text="R" * 100,
            event_id=f"old-{index}",
            occurred_at=NOW - 1_000 - index,
        )
        for index in range(4)
    )
    temporal = context_adapters.build_continuity_view(
        request,
        snapshot=_snapshot(request, moments=moments),
        relevant_old_turns=old,
    )
    assert [record.event_id for record in temporal.recent_moments] == [
        "moment-1",
        "moment-2",
        "moment-3",
    ]
    assert all(record.moment_source != "chat" for record in temporal.recent_moments)
    assert len(temporal.recent_moments[0].focus_text) == 220

    recall_request = _private_request("Recall the older conversation", grounding=True)
    recall = context_adapters.build_continuity_view(
        recall_request,
        snapshot=_snapshot(recall_request),
        relevant_old_turns=old,
    )
    assert [record.event_id for record in recall.relevant_old_turns] == [
        "old-0",
        "old-1",
        "old-2",
    ]
    assert all(
        len(record.text) + len(record.reply_text) <= 600
        for record in recall.relevant_old_turns
    )


def test_stable_identity_dedupes_conflicts_and_preserves_distinct_ids():
    scope = _private_scope()
    recent = context_adapters.CompletedTurnRecord(
        scope=scope,
        text="same user text",
        reply_text="same Nana text",
        event_id="shared-event",
        turn_id="turn-7",
        occurred_at=NOW - 5,
        text_completion="complete",
        voice_delivery="not_attested",
    )
    old = context_adapters.RelevantOldTurnRecord(
        scope=scope,
        text="same user text",
        reply_text="same Nana text",
        event_id="shared-event",
        provenance_record_id="old-7",
        occurred_at=NOW - 50,
    )
    assert context_adapters.dedupe_continuity_records((old, recent)) == (recent,)

    conflicting = replace(old, text="conflicting text")
    with pytest.raises(ContextContractError, match="continuity_identity_conflict"):
        context_adapters.dedupe_continuity_records((recent, conflicting))

    distinct = replace(old, event_id="different-event")
    kept = context_adapters.dedupe_continuity_records((recent, distinct))
    assert kept == (recent, distinct)


def test_legacy_digest_is_session_and_full_scope_local():
    base = context_adapters.RelevantOldTurnRecord(
        scope=_private_scope(),
        text="equal legacy text",
        event_id=None,
        turn_id=None,
        provenance_record_id=None,
        occurred_at=NOW - 5,
    )
    same = replace(base, occurred_at=NOW - 50)
    assert context_adapters.dedupe_continuity_records((base, same)) == (base,)

    public_scope = context_adapters.ContinuityScope(
        lane=Lane.PUBLIC_STAGE,
        visibility=frozenset({Lane.PUBLIC_STAGE}),
        owner="public_session",
        session_id="stream-1",
        platform="youtube",
        room_id="room-1",
        stream_session_id="stream-1",
        actor_key="youtube:viewer-1",
    )
    variants = (
        replace(base, scope=_private_scope(session_id="private-session-other")),
        replace(base, scope=_private_scope(owner="other-provenance")),
        context_adapters.RelevantOldTurnRecord(
            scope=public_scope,
            text=base.text,
            occurred_at=base.occurred_at,
        ),
        context_adapters.RelevantOldTurnRecord(
            scope=replace(public_scope, room_id="room-2"),
            text=base.text,
            occurred_at=base.occurred_at,
        ),
        context_adapters.RelevantOldTurnRecord(
            scope=replace(public_scope, actor_key="youtube:viewer-2"),
            text=base.text,
            occurred_at=base.occurred_at,
        ),
    )
    result = context_adapters.dedupe_continuity_records((base, *variants))
    assert result == (base, *variants)


def test_authority_selection_precedes_cross_type_dedupe_and_unrelated_conflicts():
    shared_text = "Finish the scoped continuity review"
    shared = _checkpoint_payload(
        summary="",
        anchors=[
            {
                "event_id": "shared-active-event",
                "turn_index": 1,
                "kind": "open_loop",
                "text": shared_text,
                "created_at": NOW - 2,
            }
        ],
        turns=[
            {
                "event_id": "shared-active-event",
                "turn_index": 1,
                "user_text": shared_text,
                "nana_text": "Earlier generated reply",
                "created_at": NOW - 2,
            }
        ],
    )
    active_request = _private_request("What are we doing and which open loop is next?")
    active = context_adapters.build_continuity_view(
        active_request,
        snapshot=_snapshot(active_request, checkpoint=shared),
    )
    assert [record.event_id for record in active.open_loops] == ["shared-active-event"]

    conflicting = _checkpoint_payload(
        summary="",
        anchors=[
            {
                "event_id": "unrelated-conflict",
                "turn_index": 1,
                "kind": "decision",
                "text": "anchor truth",
                "created_at": NOW - 2,
            }
        ],
        turns=[
            {
                "event_id": "unrelated-conflict",
                "turn_index": 1,
                "user_text": "different turn truth",
                "nana_text": "generated reply",
                "created_at": NOW - 2,
            }
        ],
    )
    for text, expected in (
        ("Which tab is open?", "current_browser"),
        ("What is my birthday?", "durable_fact"),
    ):
        request = _private_request(text, grounding=expected == "durable_fact")
        view = context_adapters.build_continuity_view(
            request,
            snapshot=_snapshot(request, checkpoint=conflicting),
        )
        assert view.authority.value == expected
        assert view.records == ()


@pytest.mark.parametrize(
    "metadata_kind",
    ("completion", "delivery", "anchor_kind", "speaker", "moment_source"),
)
def test_same_id_same_type_conflicting_truth_metadata_fails_closed(metadata_kind):
    scope = _private_scope()
    if metadata_kind in {"completion", "delivery"}:
        left = context_adapters.CompletedTurnRecord(
            scope=scope,
            text="same text",
            reply_text="same reply",
            event_id="metadata-event",
            occurred_at=NOW - 1,
            text_completion="complete",
            voice_delivery="not_attested",
        )
        right = replace(
            left,
            text_completion=("partial" if metadata_kind == "completion" else left.text_completion),
            voice_delivery=("delivered" if metadata_kind == "delivery" else left.voice_delivery),
        )
    elif metadata_kind == "anchor_kind":
        left = context_adapters.CheckpointAnchorRecord(
            scope=scope,
            text="same text",
            event_id="metadata-event",
            anchor_kind="decision",
        )
        right = replace(left, anchor_kind="episode")
    elif metadata_kind == "speaker":
        public_scope = context_adapters.ContinuityScope(
            lane=Lane.PUBLIC_STAGE,
            visibility=frozenset({Lane.PUBLIC_STAGE}),
            owner="public_session",
            session_id="stream-1",
            platform="youtube",
            room_id="room-1",
            stream_session_id="stream-1",
            actor_key="youtube:viewer-1",
        )
        left = context_adapters.PublicTurnRecord(
            scope=public_scope,
            text="same text",
            event_id="metadata-event",
            speaker="Viewer One",
            reply_delivery="not_delivered",
        )
        right = replace(left, speaker="Viewer Renamed")
    else:
        left = context_adapters.RecentMomentRecord(
            scope=context_adapters.ContinuityScope(
                lane=Lane.PRIVATE_OWNER,
                visibility=frozenset({Lane.PRIVATE_OWNER}),
                owner="runtime_state",
                session_id="private-session-task10",
            ),
            text="same text",
            event_id="metadata-event",
            moment_source="awareness",
            focus_text="same text",
        )
        right = replace(left, moment_source="zone")

    with pytest.raises(ContextContractError, match="continuity_identity_conflict"):
        context_adapters.dedupe_continuity_records((left, right))


def test_checkpoint_capture_is_pure_immutable_and_revision_stable_without_invention():
    store = {}
    for index in range(2):
        result = session_checkpoint.record_private_turn(
            store,
            user_text=f"Meaningful user turn {index}",
            nana_text=f"Meaningful Nana reply {index}",
            event_id=f"private-event-{index}",
            now=NOW - 20 + index,
        )
        assert result["status"] == "recorded"
    before = copy.deepcopy(store)

    first = session_checkpoint.capture_private_checkpoint_source(store, captured_at=NOW)
    second = session_checkpoint.capture_private_checkpoint_source(store, captured_at=NOW + 100)

    assert store == before
    assert first.revision == second.revision
    assert first.payload["summary"] == ""
    assert "topic" not in first.payload
    assert first.payload["pending_turns"][0]["event_id"] == "private-event-0"
    store["session_checkpoint"]["pending_turns"][0]["user_text"] = "late mutation"
    assert first.payload["pending_turns"][0]["user_text"] == "Meaningful user turn 0"
    with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
        first.payload["pending_turns"][0]["user_text"] = "mutated"


@pytest.mark.parametrize("lane", [Lane.PUBLIC_STAGE, Lane.OPERATOR_BACKSTAGE])
def test_real_private_capture_denies_nonprivate_before_private_import(monkeypatch, lane):
    request = _public_request() if lane is Lane.PUBLIC_STAGE else _operator_request()
    private_imports = []
    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "nana.memory" or "session_checkpoint" in name or (
            name == "nana.runtime" and "session_checkpoint" in fromlist
        ):
            private_imports.append((name, tuple(fromlist)))
            raise AssertionError("nonprivate request attempted private continuity lookup")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    with pytest.raises(ContextContractError, match="private_source_capture_denied"):
        context_runtime.capture_turn_snapshot(request)
    assert private_imports == []


def test_real_private_capture_requires_and_seals_checkpoint_source(monkeypatch):
    from nana.runtime import awareness_memory, context as runtime_owner, live_awareness

    memory_module = importlib.import_module("nana.memory")
    store = {}
    session_checkpoint.record_private_turn(
        store,
        user_text="Prior checkpoint user text",
        nana_text="Prior checkpoint Nana text",
        event_id="checkpoint-event",
        now=NOW - 10,
    )
    monkeypatch.setattr(memory_module, "memory", store)
    monkeypatch.setattr(memory_module, "memory_lock", threading.RLock())

    runtime_source = _source(
        "runtime_context",
        {"active_app": "fixture", "active_zone": "focus", "idle_state": "active", "in_flow": True, "time": {}},
        observed=None,
        fresh=Freshness.UNKNOWN,
    )
    browser_source = _source("browser_state", {"available": False}, observed=None, fresh=Freshness.UNKNOWN)
    monkeypatch.setattr(
        runtime_owner,
        "capture_runtime_context_sources",
        lambda **_kwargs: SimpleNamespace(runtime_context=runtime_source, browser_state=browser_source),
    )
    monkeypatch.setattr(
        live_awareness,
        "capture_live_awareness_source",
        lambda *_args, **_kwargs: _source(
            "live_awareness",
            {"browser_available": False, "focus_source": "none", "focus_text": "", "focus_confidence": 0.0},
            observed=None,
            fresh=Freshness.UNKNOWN,
        ),
    )
    monkeypatch.setattr(
        awareness_memory,
        "capture_awareness_memory_source",
        lambda **_kwargs: _source("awareness_memory", {"enabled": True, "moments": []}),
    )
    monkeypatch.setattr(
        context_runtime,
        "capture_private_expression_sources",
        lambda **_kwargs: _source(
            "expression_private",
            {
                "mood": {"tone": "steady", "energy": 0.5, "focus": 0.5, "tension": 0.1},
                "affect": {"warmth": 0.6, "playfulness": 0.4, "assertiveness": 0.3, "intimacy": 0.8},
                "persona": {"mode": "chill", "clamp": "open"},
            },
        ),
    )

    request = _private_request()
    captured = context_runtime.capture_turn_snapshot(request)
    assert captured.private_checkpoint.source == "private_checkpoint"
    assert captured.private_checkpoint.payload["pending_turns"][0]["event_id"] == "checkpoint-event"
    assert captured.source_revision("private_checkpoint") == captured.private_checkpoint.revision


def _delivery(session, scope, reply, *, delivered):
    generated = PublicDeliveryRecord(
        scope.event_id,
        f"output-{scope.event_id}",
        "generated",
        f"attempt-{scope.event_id}",
        0,
        reply,
        scope,
        NOW,
    )
    session.record_reply_context(scope=scope, delivery_record=generated, now=NOW, monotonic_now=NOW / 2)
    if not delivered:
        return
    for revision, state in enumerate(("published", "playback_started", "delivered"), start=1):
        session.record_reply_context(
            scope=scope,
            delivery_record=replace(generated, state=state, revision=revision, updated_at=NOW + revision),
            now=NOW,
            monotonic_now=NOW / 2,
        )


def test_public_capture_excludes_current_before_cap_and_keeps_delivery_truth():
    session = SocialSessionCache(clock=lambda: NOW)
    prior_scopes = [_public_scope(f"prior-{index}") for index in range(6)]
    for index, scope in enumerate(prior_scopes):
        text = "identical current text" if index == 5 else f"prior public text {index}"
        session.record_public_turn(scope=scope, text=text, topic="fixture")
        _delivery(session, scope, f"reply {index}", delivered=index % 2 == 0)
    current_scope = _public_scope("current-event")
    session.record_public_turn(scope=current_scope, text="identical current text", topic="fixture")
    _delivery(session, current_scope, "current reply must be excluded", delivered=True)
    request = _public_request("current-event", text="identical current text", scope=current_scope)

    partition = session._sessions[("youtube", "room-1", "stream-1")]
    before_turns = tuple(turn.to_dict() for turn in partition._recent_turns)
    before_delivery = dict(partition._delivery)
    source = session.capture_continuity_source(request)
    view = context_adapters.build_continuity_view(request, public_source=source)
    sections = context_adapters.build_continuity_sections(view, request)

    assert tuple(turn.to_dict() for turn in partition._recent_turns) == before_turns
    assert partition._delivery == before_delivery
    assert [record.event_id for record in view.public_turns] == [
        "prior-1",
        "prior-2",
        "prior-3",
        "prior-4",
        "prior-5",
    ]
    assert view.public_turns[-1].text == "identical current text"
    assert all(record.event_id != "current-event" for record in view.public_turns)
    assert [record.reply_text for record in view.public_turns] == [
        "",
        "reply 2",
        "",
        "reply 4",
        "",
    ]
    assert all(len(record.text) <= 96 and len(record.reply_text) <= 72 for record in view.public_turns)
    assert all(re.fullmatch(r"[0-9a-f]{64}", row["content_hash"]) for row in _thaw(source.payload)["turns"])
    assert tuple(section.id for section in sections) == ("continuity.public_room.v1",)
    rendered = repr(_thaw(sections[0].payload))
    assert "current reply must be excluded" not in rendered
    assert "reply 1" not in rendered and "reply 3" not in rendered


@pytest.mark.parametrize("request_factory", [_private_request, _operator_request])
def test_public_capture_validates_lane_before_partition_lookup(monkeypatch, request_factory):
    session = SocialSessionCache(clock=lambda: NOW)
    calls = []

    def tripwire(*_args, **_kwargs):
        calls.append("partition")
        raise AssertionError("partition lookup occurred before lane validation")

    monkeypatch.setattr(session, "_peek_partition", tripwire, raising=False)
    with pytest.raises(ContextContractError, match="public_source_capture_denied"):
        session.capture_continuity_source(request_factory())
    assert calls == []


def test_public_capture_does_not_create_empty_partition_and_operator_is_absent():
    session = SocialSessionCache(clock=lambda: NOW)
    public_request = _public_request()
    source = session.capture_continuity_source(public_request)
    assert session._sessions == {}
    assert _thaw(source.payload)["turns"] == []

    operator_request = _operator_request()
    operator = context_adapters.build_continuity_view(
        operator_request,
        snapshot=object(),
        public_source=object(),
        relevant_old_turns=(_old_turn(),),
    )
    assert operator.records == ()
    assert context_adapters.build_continuity_sections(operator, operator_request) == ()


def test_private_identical_prior_text_is_truth_once_and_delivery_metadata_is_exact():
    text = "identical prior and current input"
    request = _private_request(text)
    checkpoint = _checkpoint_payload(
        summary="",
        anchors=[],
        turns=[
            {
                "event_id": "prior-identical-event",
                "turn_index": 1,
                "user_text": text,
                "nana_text": "completed text response",
                "created_at": NOW - 1,
            }
        ],
    )
    view = context_adapters.build_continuity_view(
        request,
        snapshot=_snapshot(request, checkpoint=checkpoint),
    )
    sections = context_adapters.build_continuity_sections(view, request)
    payload = _thaw(sections[0].payload)

    assert len(view.recent_turns) == 1
    assert view.recent_turns[0].text == text
    assert view.recent_turns[0].text_completion == "complete"
    assert view.recent_turns[0].voice_delivery == "not_attested"
    assert repr(payload).count(text) == 1
    assert "prior-identical-event" not in repr(payload)


def test_canonical_sync_stream_and_empty_fallback_share_snapshot_without_legacy_reads(
    monkeypatch,
    _isolated_nana_modules,
):
    from smoke_context_runtime_integration import _install_private_dependencies, _run_stream

    gpt, captures, reads = _install_private_dependencies(private_mode="canonical")
    sys.modules.pop("nana.runtime.memory_grounding", None)
    grounding = importlib.import_module("nana.runtime.memory_grounding")
    contracts = importlib.import_module("nana.runtime.context_contracts")
    adapters = importlib.import_module("nana.runtime.context_adapters")
    runtime = importlib.import_module("nana.runtime.context_runtime")
    shadow = importlib.import_module("nana.runtime.context_shadow")
    sys.modules["nana.config"].NANA_CONTEXT_BUDGET_POLICY_REVISION = runtime.PRIVATE_POLICY_REVISION
    monkeypatch.setattr(runtime, "require_canonical_dispatch_ready", lambda *_a, **_k: None)

    text = "identical canonical input"
    boundary, turn = shadow.resolve_context_turn(
        text=text,
        viewer_name=None,
        stream_mode=False,
        public_platform=None,
        metadata=None,
        private_model="fake",
        public_model="fake-public",
        story_mode=False,
        casual_mode=False,
        temporal_intent=False,
        grounding_intent=False,
    )
    request = turn.resolved_request

    def source(name, payload, *, observed=None, fresh=contracts.Freshness.UNKNOWN):
        return contracts.SourceSnapshot(
            source=name,
            revision=f"{name}-canonical-fixture-r1",
            observed_at=observed,
            captured_at=request.captured_wall_time,
            freshness=fresh,
            payload=payload,
        )

    runtime_source = source(
        "runtime_context",
        {"active_app": "fixture", "active_zone": "focus", "idle_state": "active", "in_flow": True, "time": {}},
    )
    browser_source = source("browser_state", {"available": False})
    awareness_source = source(
        "live_awareness",
        {"browser_available": False, "focus_source": "none", "focus_text": "", "focus_confidence": 0.0},
    )
    memory_source = source("awareness_memory", {"enabled": True, "moments": []})
    expression_source = source(
        "expression_private",
        {
            "mood": {"tone": "steady", "energy": 0.5, "focus": 0.6, "tension": 0.1},
            "affect": {"warmth": 0.6, "playfulness": 0.4, "assertiveness": 0.3, "intimacy": 0.8},
            "persona": {"mode": "focus", "clamp": "strict"},
        },
        observed=request.captured_wall_time,
        fresh=contracts.Freshness.FRESH,
    )
    checkpoint_source = source(
        "private_checkpoint",
        {
            "available": True,
            "session_id": "canonical-session",
            "summary": "",
            "anchors": [],
            "pending_turns": [
                {
                    "event_id": "canonical-prior-event",
                    "turn_index": 1,
                    "user_text": text,
                    "nana_text": "prior complete reply",
                    "created_at": request.captured_wall_time - 1,
                }
            ],
        },
        observed=request.captured_wall_time - 1,
        fresh=contracts.Freshness.FRESH,
    )
    private_memory_source = source(
        "private_memory",
        {"long_term": []},
        observed=request.captured_wall_time,
        fresh=contracts.Freshness.FRESH,
    )
    sources = (runtime_source, browser_source, awareness_source, memory_source, expression_source, checkpoint_source, private_memory_source)
    snapshot = runtime.RuntimeContextSnapshot(
        request=request,
        snapshot_revision=1,
        captured_at=request.captured_wall_time,
        captured_monotonic_at=request.captured_monotonic_time,
        runtime_context=runtime_source,
        browser_state=browser_source,
        live_awareness=awareness_source,
        awareness_memory=memory_source,
        source_revisions=tuple(
            adapters.SourceRevision(item.source, item.revision, "content_hash")
            for item in sorted(sources, key=lambda item: item.source)
        ),
        expression=expression_source,
        private_checkpoint=checkpoint_source,
        private_memory=private_memory_source,
    )

    _install_real_private_evidence_helper(gpt, grounding)
    checkpoint_reads = []
    checkpoint_owner = importlib.import_module("nana.runtime.session_checkpoint")

    def checkpoint_tripwire(*_args, **_kwargs):
        checkpoint_reads.append("checkpoint")
        return []

    monkeypatch.setattr(
        checkpoint_owner,
        "private_checkpoint_evidence_candidates",
        checkpoint_tripwire,
    )

    forbidden = {"checkpoint": 0, "recent_chat": 0, "awareness_memory": 0, "temporal": 0, "short_term": 0}

    def reject(name):
        def call(*_args, **_kwargs):
            forbidden[name] += 1
            raise AssertionError(f"canonical continuity read legacy {name}")

        return call

    gpt._private_session_checkpoint_block = reject("checkpoint")
    gpt.load_recent_chat = reject("recent_chat")
    gpt.get_awareness_memory = reject("awareness_memory")
    gpt.build_temporal_prompt_block = reject("temporal")

    class GuardedMemory(dict):
        def __getitem__(self, key):
            if key == "short_term":
                forbidden["short_term"] += 1
                raise AssertionError("canonical continuity read short_term")
            return super().__getitem__(key)

    guarded_memory = GuardedMemory(gpt.memory)
    gpt.memory = guarded_memory
    sys.modules["nana.memory"].memory = guarded_memory

    plans = []
    snapshots_seen = []
    real_plan = runtime.build_turn_plan
    real_view = adapters.build_continuity_view

    def observe_plan(inputs, **kwargs):
        plan = real_plan(inputs, **kwargs)
        plans.append(plan)
        return plan

    def observe_view(resolved_request, **kwargs):
        snapshots_seen.append(kwargs.get("snapshot"))
        return real_view(resolved_request, **kwargs)

    monkeypatch.setattr(runtime, "build_turn_plan", observe_plan)
    monkeypatch.setattr(adapters, "build_continuity_view", observe_view)
    prepared = (boundary, turn)
    kwargs = {"_prepared_private_turn": prepared, "_turn_snapshot": snapshot}

    assert gpt.ask_gpt(text, **kwargs)
    assert _run_stream(gpt, text, **kwargs)

    def empty_stream(compiled, **_provider_kwargs):
        captures["stream"].append(
            [
                {"role": message.role, "content": message.content}
                for message in compiled.messages
            ]
        )
        if False:
            yield "unreachable"

    sys.modules["nana.brain.llmgate_client"].stream_llmgate_compiled = empty_stream
    assert _run_stream(gpt, text, **kwargs) == ""
    assert gpt.ask_gpt(text, **kwargs)

    assert len(plans) == 4
    assert snapshots_seen == [snapshot, snapshot, snapshot, snapshot]
    assert all(plan.compiled.messages == plans[0].compiled.messages for plan in plans[1:])
    assert all(plan.compiled.full_context_hash == plans[0].compiled.full_context_hash for plan in plans[1:])
    system_text = plans[0].compiled.messages[0].content
    assert system_text.count(text) == 1
    assert plans[0].compiled.messages[-1].content == text
    assert sum(message.content.count(text) for message in plans[0].compiled.messages) == 2
    assert forbidden == {"checkpoint": 0, "recent_chat": 0, "awareness_memory": 0, "temporal": 0, "short_term": 0}
    assert checkpoint_reads == []
    assert reads["checkpoint"] == 0 and reads["history"] == 0
    assert len(captures["sync"]) == 2 and len(captures["stream"]) == 2


def test_canonical_temporal_sync_stream_and_empty_fallback_use_typed_view(
    monkeypatch,
    _isolated_nana_modules,
):
    from smoke_context_runtime_integration import _install_private_dependencies, _run_stream

    gpt, captures, _reads = _install_private_dependencies(private_mode="canonical")
    contracts = importlib.import_module("nana.runtime.context_contracts")
    adapters = importlib.import_module("nana.runtime.context_adapters")
    runtime = importlib.import_module("nana.runtime.context_runtime")
    shadow = importlib.import_module("nana.runtime.context_shadow")
    sys.modules["nana.config"].NANA_CONTEXT_BUDGET_POLICY_REVISION = runtime.PRIVATE_POLICY_REVISION
    monkeypatch.setattr(runtime, "require_canonical_dispatch_ready", lambda *_a, **_k: None)

    text = "Ba nãy giờ làm gì"
    boundary, turn = shadow.resolve_context_turn(
        text=text,
        viewer_name=None,
        stream_mode=False,
        public_platform=None,
        metadata=None,
        private_model="fake",
        public_model="fake-public",
        story_mode=False,
        casual_mode=False,
        temporal_intent=True,
        grounding_intent=False,
    )
    request = turn.resolved_request
    captured_at = request.captured_wall_time

    def source(name, payload, *, observed=None, fresh=contracts.Freshness.UNKNOWN):
        return contracts.SourceSnapshot(
            source=name,
            revision=f"{name}-temporal-fixture-r1",
            observed_at=observed,
            captured_at=captured_at,
            freshness=fresh,
            payload=payload,
        )

    def moment(event_id, source_name, focus, age):
        return {
            "id": event_id,
            "timestamp": captured_at - age,
            "monotonic": request.captured_monotonic_time - age,
            "source": source_name,
            "active_app": "chrome",
            "active_zone": "focus",
            "browser_kind": "docs",
            "title": focus,
            "url_host": "example.test",
            "focus_source": "selected_text",
            "focus_text": focus,
            "user_text": focus if source_name == "chat" else "",
            "nana_text": "chat reply" if source_name == "chat" else "",
            "confidence": 0.9,
            "importance": "medium",
            "ttl_seconds": 600.0,
            "can_act": False,
            "frozen_awareness": None,
            "frozen_url": "",
            "frozen_title": focus,
            "turn_counter": 1,
        }

    runtime_source = source(
        "runtime_context",
        {"active_app": "chrome", "active_zone": "focus", "idle_state": "active", "in_flow": True, "time": {}},
    )
    browser_source = source("browser_state", {"available": False})
    awareness_source = source(
        "live_awareness",
        {"browser_available": False, "focus_source": "none", "focus_text": "", "focus_confidence": 0.0},
    )
    memory_source = source(
        "awareness_memory",
        {
            "enabled": True,
            "moments": [
                moment("chat-moment", "chat", "CHAT_MOMENT_SENTINEL", 1),
                moment("awareness-moment", "awareness", "NON_DIALOGUE_MOMENT_SENTINEL", 2),
            ],
        },
        observed=captured_at - 1,
        fresh=contracts.Freshness.FRESH,
    )
    expression_source = source(
        "expression_private",
        {
            "mood": {"tone": "steady", "energy": 0.5, "focus": 0.6, "tension": 0.1},
            "affect": {"warmth": 0.6, "playfulness": 0.4, "assertiveness": 0.3, "intimacy": 0.8},
            "persona": {"mode": "focus", "clamp": "strict"},
        },
        observed=captured_at,
        fresh=contracts.Freshness.FRESH,
    )
    checkpoint_source = source(
        "private_checkpoint",
        {
            "available": True,
            "session_id": "canonical-temporal-session",
            "summary": "",
            "anchors": [],
            "pending_turns": [
                {
                    "event_id": "completed-temporal-event",
                    "turn_index": 1,
                    "user_text": "COMPLETED_TURN_SENTINEL",
                    "nana_text": "completed reply",
                    "created_at": captured_at - 3,
                }
            ],
        },
        observed=captured_at - 3,
        fresh=contracts.Freshness.FRESH,
    )
    private_memory_source = source(
        "private_memory",
        {"long_term": []},
        observed=captured_at,
        fresh=contracts.Freshness.FRESH,
    )
    sources = (
        runtime_source,
        browser_source,
        awareness_source,
        memory_source,
        expression_source,
        checkpoint_source,
        private_memory_source,
    )
    snapshot = runtime.RuntimeContextSnapshot(
        request=request,
        snapshot_revision=1,
        captured_at=captured_at,
        captured_monotonic_at=request.captured_monotonic_time,
        runtime_context=runtime_source,
        browser_state=browser_source,
        live_awareness=awareness_source,
        awareness_memory=memory_source,
        source_revisions=tuple(
            adapters.SourceRevision(item.source, item.revision, "content_hash")
            for item in sorted(sources, key=lambda item: item.source)
        ),
        expression=expression_source,
        private_checkpoint=checkpoint_source,
        private_memory=private_memory_source,
    )

    gpt._private_memory_evidence_for_turn = lambda *_a, **_k: None
    legacy_temporal_calls = []

    def legacy_temporal(*_args, **_kwargs):
        legacy_temporal_calls.append("legacy")
        return "LEGACY_TEMPORAL_SENTINEL"

    gpt.get_deterministic_temporal_answer = legacy_temporal
    plans = []
    snapshots_seen = []
    real_plan = runtime.build_turn_plan
    real_view = adapters.build_continuity_view

    def observe_plan(inputs, **kwargs):
        plan = real_plan(inputs, **kwargs)
        plans.append(plan)
        return plan

    def observe_view(resolved_request, **kwargs):
        snapshots_seen.append(kwargs.get("snapshot"))
        return real_view(resolved_request, **kwargs)

    monkeypatch.setattr(runtime, "build_turn_plan", observe_plan)
    monkeypatch.setattr(adapters, "build_continuity_view", observe_view)
    prepared = (boundary, turn)
    kwargs = {"_prepared_private_turn": prepared, "_turn_snapshot": snapshot}

    assert gpt.ask_gpt(text, **kwargs)
    assert _run_stream(gpt, text, **kwargs)

    def empty_stream(compiled, **_provider_kwargs):
        captures["stream"].append(
            [
                {"role": message.role, "content": message.content}
                for message in compiled.messages
            ]
        )
        if False:
            yield "unreachable"

    sys.modules["nana.brain.llmgate_client"].stream_llmgate_compiled = empty_stream
    assert _run_stream(gpt, text, **kwargs) == ""
    assert gpt.ask_gpt(text, **kwargs)

    assert legacy_temporal_calls == []
    assert len(plans) == 4
    assert snapshots_seen == [snapshot, snapshot, snapshot, snapshot]
    assert all(plan.compiled.messages == plans[0].compiled.messages for plan in plans[1:])
    system_text = plans[0].compiled.messages[0].content
    assert "NON_DIALOGUE_MOMENT_SENTINEL" in system_text
    assert "COMPLETED_TURN_SENTINEL" in system_text
    assert "CHAT_MOMENT_SENTINEL" not in system_text
    assert "LEGACY_TEMPORAL_SENTINEL" not in system_text
    assert len(captures["sync"]) == 2 and len(captures["stream"]) == 2


def test_canonical_evidence_keeps_durable_retrieval_without_checkpoint_fallback(
    monkeypatch,
    _isolated_nana_modules,
):
    from smoke_context_runtime_integration import _install_private_dependencies

    gpt, _captures, _reads = _install_private_dependencies(private_mode="canonical")
    sys.modules.pop("nana.runtime.memory_grounding", None)
    grounding = importlib.import_module("nana.runtime.memory_grounding")
    runtime = importlib.import_module("nana.runtime.context_runtime")
    shadow = importlib.import_module("nana.runtime.context_shadow")
    sys.modules["nana.config"].NANA_CONTEXT_BUDGET_POLICY_REVISION = runtime.PRIVATE_POLICY_REVISION
    monkeypatch.setattr(runtime, "require_canonical_dispatch_ready", lambda *_a, **_k: None)
    gpt.ground_user_message = grounding.ground_user_message
    _install_real_private_evidence_helper(gpt, grounding)

    text = "Nana có nhớ sinh nhật của Ba không?"
    boundary, _turn = shadow.resolve_context_turn(
        text=text,
        viewer_name=None,
        stream_mode=False,
        public_platform=None,
        metadata=None,
        private_model="fake",
        public_model="fake-public",
        story_mode=False,
        casual_mode=False,
        temporal_intent=False,
        grounding_intent=True,
    )
    checkpoint_reads = []
    checkpoint_owner = importlib.import_module("nana.runtime.session_checkpoint")

    def checkpoint_tripwire(*_args, **_kwargs):
        checkpoint_reads.append("checkpoint")
        return []

    monkeypatch.setattr(
        checkpoint_owner,
        "private_checkpoint_evidence_candidates",
        checkpoint_tripwire,
    )
    durable_candidate = SimpleNamespace(
        text="Sinh nhật của Ba là ngày 2 tháng 1",
        source="memory_v2",
        evidence="durable_fact",
        source_event_id="durable-birthday-event",
        created_at=NOW - 1,
    )
    spine = SimpleNamespace(
        retrieve=lambda _query, **_kwargs: [durable_candidate],
    )
    sys.modules["nana.runtime.memory_spine"].get_memory_spine = lambda: spine

    evidence = gpt._private_memory_evidence_for_turn(
        text,
        boundary,
        None,
        "",
        False,
        include_checkpoint=False,
    )

    assert checkpoint_reads == []
    assert evidence is not None
    assert evidence.status in {"found", "partial"}
    assert any("2 tháng 1" in snippet for snippet in evidence.snippets)
