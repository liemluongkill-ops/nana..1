"""Task 8 canonical CurrentSituation projection regressions."""

from __future__ import annotations

import asyncio
import copy
import importlib
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from nana.runtime import awareness_memory, browser_state, context as runtime_context
from nana.runtime import context_adapters, context_runtime, live_awareness
from nana.runtime.context_compiler import _render_payload
from nana.runtime.context_contracts import (
    Freshness,
    Lane,
    Route,
    SourceSnapshot,
    UnresolvedContextRequest,
)


class _PrivateAuthority:
    def authorize(self, _request):
        return context_runtime.TrustedIngress(Lane.PRIVATE_OWNER, None)


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


def _request(*, captured_at=2_000.0, temporal=False):
    raw = UnresolvedContextRequest(
        "current-situation-request",
        "current-situation-correlation",
        Route.INTERACTIVE,
        "fake-private-model",
        "What is on this page?",
        None,
        False,
        None,
        {},
        False,
        False,
        False,
        temporal,
        False,
    )
    return context_runtime.LaneResolver(
        _PrivateAuthority(),
        wall_clock=lambda: captured_at,
        monotonic_clock=lambda: 900.0,
    ).resolve(raw)


def _snapshot(monkeypatch, *, captured_at=2_000.0, temporal=False, browser_age=20.0):
    state = copy.deepcopy(runtime_context.context_state)
    state.update(
        {
            "active_app": "chrome",
            "active_title": "Current Window",
            "active_zone": "chill",
            "idle_state": "active",
            "idle_seconds": 3.5,
            "in_flow": False,
            "poll_rate": 123.0,
            "poll_history": ["POLL-HISTORY-SENTINEL"],
            "transport_diagnostics": "TRANSPORT-SENTINEL",
        }
    )
    state["browser"].update(
        {
            "available": True,
            "browser": "chrome",
            "kind": "docs",
            "title": "Canonical Fixture Title",
            "url": "https://example.test/current?fixture=1",
            "reason": "fixture",
            "page_heading": "Fixture Page Heading",
            "meta_description": "Fixture meta description",
            "selected_text": "Canonical selected focus",
            "social_post_text": "Weaker social focus",
            "social_vibe": "quiet",
            "social_comments": ["COMMENT-SENTINEL"],
            "local_summary": "Canonical derived summary",
            "dom_debug": "DOM-DEBUG-SENTINEL",
            "local_helper_debug": "HELPER-DEBUG-SENTINEL",
            "site_signals": {"raw": "SITE-SIGNAL-SENTINEL"},
            "tab_list": ["TAB-LIST-SENTINEL"],
            "last_update": captured_at - browser_age,
            "age_seconds": 888.0,
            "fresh": False,
        }
    )
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
    monkeypatch.setattr(awareness_memory, "_awareness_memory", awareness_memory.AwarenessMemory())
    request = _request(captured_at=captured_at, temporal=temporal)
    return request, context_runtime.capture_turn_snapshot(request)


def _section(monkeypatch, **kwargs):
    request, snapshot = _snapshot(monkeypatch, **kwargs)
    section = context_adapters.build_current_situation_section(snapshot, request)
    assert section is not None
    return request, snapshot, section


def test_current_situation_uses_distinct_registry_and_projection_identifiers(monkeypatch):
    _request_value, _snapshot_value, section = _section(monkeypatch)
    assert section.id == "situation.current.v1"
    assert section.source.projection == "current_situation.v1"
    assert section.source.owner == "runtime_state"
    assert section.source.adapter == "current_situation.adapter.v1"


def test_one_projection_dedupes_fields_and_excludes_debug_telemetry(monkeypatch):
    _request_value, _snapshot_value, section = _section(monkeypatch)
    rendered = _render_payload(section.payload)
    assert "social" not in section.payload

    for expected in (
        "Canonical Fixture Title",
        "https://example.test/current?fixture=1",
        "Canonical selected focus",
        "Canonical derived summary",
    ):
        assert rendered.count(expected) == 1

    for forbidden in (
        "DOM-DEBUG-SENTINEL",
        "HELPER-DEBUG-SENTINEL",
        "SITE-SIGNAL-SENTINEL",
        "COMMENT-SENTINEL",
        "POLL-HISTORY-SENTINEL",
        "TAB-LIST-SENTINEL",
        "TRANSPORT-SENTINEL",
        "dom_debug",
        "local_helper_debug",
        "site_signals",
        "age_seconds",
    ):
        assert forbidden not in rendered


def test_runtime_unknown_and_inactive_browser_focus_remain_qualified(monkeypatch):
    request, _initial = _snapshot(monkeypatch)
    state = runtime_context.context_state
    state["active_app"] = "code"
    state["active_zone"] = "war_zone"
    captured = context_runtime.capture_turn_snapshot(request)
    awareness = captured.awareness_mapping()
    section = context_adapters.build_current_situation_section(captured, request)

    assert captured.runtime_context.freshness.value == "unknown"
    assert awareness["browser_fresh"] is True
    assert awareness["browser_effective"] is False
    assert awareness["is_confirmed_active"] is False
    assert section.payload["active"]["source"] == "runtime_context"
    assert section.payload["active"]["freshness"] == "unknown"
    assert section.payload["active"]["status"] == "latest_known"
    assert section.payload["focus"]["confirmed_active"] is False


@pytest.mark.parametrize(
    ("values", "missing_path", "repeated_prefix"),
    [
        (
            {
                "selected_text": "",
                "social_post_text": "",
                "local_summary": "S" * 300,
                "page_heading": "",
                "title": "Different title",
            },
            ("derived_summary",),
            "S" * 217,
        ),
        (
            {
                "selected_text": "",
                "social_post_text": "",
                "local_summary": "",
                "page_heading": "",
                "title": "T" * 250,
            },
            ("browser", "title"),
            "T" * 197,
        ),
        (
            {
                "selected_text": "Same semantic title",
                "social_post_text": "",
                "local_summary": "",
                "page_heading": "",
                "title": "  Same   semantic title  ",
            },
            ("browser", "title"),
            "Same semantic title",
        ),
    ],
)
def test_current_situation_dedupes_semantics_before_independent_caps(
    monkeypatch,
    values,
    missing_path,
    repeated_prefix,
):
    request, _initial = _snapshot(monkeypatch)
    runtime_context.context_state["browser"].update(values)
    captured = context_runtime.capture_turn_snapshot(request)
    section = context_adapters.build_current_situation_section(captured, request)
    value = section.payload
    for key in missing_path[:-1]:
        value = value[key]
    assert missing_path[-1] not in value
    assert _render_payload(section.payload).count(repeated_prefix) == 1


@pytest.mark.parametrize(
    ("values", "expected_source", "expected_text"),
    [
        ({"selected_text": "selected", "social_post_text": "social"}, "selected_text", "selected"),
        ({"selected_text": "", "social_post_text": "social"}, "social_post", "social"),
        ({"selected_text": "", "social_post_text": "", "local_summary": "summary"}, "local_summary", "summary"),
        ({"selected_text": "", "social_post_text": "", "local_summary": "", "page_heading": "heading"}, "page_heading", "heading"),
        ({"selected_text": "", "social_post_text": "", "local_summary": "", "page_heading": "", "title": "title focus"}, "title", "title focus"),
    ],
)
def test_awareness_owned_focus_precedence(monkeypatch, values, expected_source, expected_text):
    state = copy.deepcopy(runtime_context.context_state)
    state.update(
        {
            "active_app": "chrome",
            "active_zone": "chill",
            "idle_state": "active",
            "in_flow": False,
        }
    )
    state["browser"].update(
        {
            "available": True,
            "browser": "chrome",
            "kind": "docs",
            "url": "https://example.test/focus",
            "title": "fallback title",
            "page_heading": "fallback heading",
            "local_summary": "fallback summary",
            "social_post_text": "fallback social",
            "selected_text": "fallback selected",
            "last_update": 1_990.0,
        }
    )
    state["browser"].update(values)
    monkeypatch.setattr(runtime_context, "context_state", state)
    monkeypatch.setattr(live_awareness, "_sticky_state", {**live_awareness._sticky_state, "locked": False})
    monkeypatch.setattr(awareness_memory, "_awareness_memory", awareness_memory.AwarenessMemory())
    request = _request()
    captured = context_runtime.capture_turn_snapshot(request)
    section = context_adapters.build_current_situation_section(captured, request)
    assert section.payload["focus"]["source"] == expected_source
    assert section.payload["focus"]["text"] == expected_text
    if expected_source == "title":
        assert _render_payload(section.payload).count(expected_text) == 1


@pytest.mark.parametrize(
    ("age", "expected"),
    [(45.0, "fresh"), (45.001, "warm"), (180.0, "warm"), (180.001, "stale"), (540.0, "stale"), (540.001, "expired")],
)
def test_browser_freshness_uses_design_boundaries(age, expected):
    assert browser_state.classify_browser_freshness(2_000.0 - age, 2_000.0).value == expected


@pytest.mark.parametrize("observed_at", [None, float("nan"), float("inf"), 2_003.0])
def test_unknown_browser_timestamp_does_not_use_materialization_time(monkeypatch, observed_at):
    state = copy.deepcopy(runtime_context.context_state)
    state.update({"active_app": "chrome", "active_zone": "chill"})
    state["browser"].update(
        {
            "available": True,
            "title": "Unknown-time title",
            "url": "https://example.test/unknown",
            "last_update": observed_at,
        }
    )
    monkeypatch.setattr(runtime_context, "context_state", state)
    monkeypatch.setattr(live_awareness, "_sticky_state", {**live_awareness._sticky_state, "locked": False})
    monkeypatch.setattr(awareness_memory, "_awareness_memory", awareness_memory.AwarenessMemory())
    request = _request()
    captured = context_runtime.capture_turn_snapshot(request)
    assert captured.browser_state.observed_at is None
    assert context_adapters.build_current_situation_section(captured, request) is None


def test_field_caps_use_design_or_lower_source_cap(monkeypatch):
    request, snapshot = _snapshot(monkeypatch)
    state = runtime_context.context_state
    state["browser"]["title"] = "T" * 260
    state["browser"]["url"] = "https://example.test/" + "u" * 260
    state["browser"]["selected_text"] = "F" * 500
    state["browser"]["local_summary"] = "S" * 500
    snapshot = context_runtime.capture_turn_snapshot(request)
    section = context_adapters.build_current_situation_section(snapshot, request)
    assert len(section.payload["browser"]["title"]) <= 200
    assert len(section.payload["browser"]["url"]) <= 220
    assert len(section.payload["focus"]["text"]) <= 360
    assert len(section.payload["derived_summary"]) <= 220


def test_ordinary_turn_has_no_capture_clock_but_temporal_turn_has_one(monkeypatch):
    _ordinary_request, _ordinary_snapshot, ordinary = _section(monkeypatch, temporal=False)
    ordinary_rendered = _render_payload(ordinary.payload)
    assert "captured_at" not in ordinary_rendered
    assert "age_seconds" not in ordinary_rendered

    _temporal_request, _temporal_snapshot, temporal = _section(monkeypatch, temporal=True)
    temporal_rendered = _render_payload(temporal.payload)
    assert temporal_rendered.count('"captured_at":2000.000000') == 1
    assert "age_seconds" not in temporal_rendered


def test_current_situation_revision_ignores_unrelated_recent_moments(monkeypatch):
    request, first_snapshot = _snapshot(monkeypatch)
    first = context_adapters.build_current_situation_section(first_snapshot, request)
    memory = awareness_memory.get_awareness_memory()
    memory.add_moment(
        awareness_memory.Moment(
            id="unrelated",
            timestamp=1_990.0,
            monotonic=890.0,
            source=awareness_memory.SRC_SYSTEM,
            focus_text="unrelated temporal event",
        )
    )
    second_snapshot = context_runtime.capture_turn_snapshot(request)
    second = context_adapters.build_current_situation_section(second_snapshot, request)

    assert first.revision == second.revision
    assert first.dedupe_key == second.dedupe_key


def test_real_private_canonical_plan_serializes_only_current_situation(
    monkeypatch,
    _isolated_nana_modules,
):
    smoke_root = ROOT / "tests" / "smoke"
    if str(smoke_root) not in sys.path:
        sys.path.insert(0, str(smoke_root))
    from smoke_context_runtime_integration import _install_private_dependencies

    gpt, captures, _reads = _install_private_dependencies(private_mode="legacy")
    contracts = importlib.import_module("nana.runtime.context_contracts")
    runtime = importlib.import_module("nana.runtime.context_runtime")
    adapters = importlib.import_module("nana.runtime.context_adapters")
    shadow = importlib.import_module("nana.runtime.context_shadow")
    config = sys.modules["nana.config"]
    config.NANA_CONTEXT_PRIVATE_MODE = "canonical"
    config.NANA_CONTEXT_BUDGET_POLICY_REVISION = runtime.PRIVATE_POLICY_REVISION
    monkeypatch.setattr(runtime, "require_canonical_dispatch_ready", lambda: None)

    text = "Explain the synthetic fixture in detail"
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
    captured_at = request.captured_wall_time

    def source(name, freshness, observed_at, payload):
        return contracts.SourceSnapshot(
            source=name,
            revision=f"{name}-revision",
            observed_at=observed_at,
            captured_at=captured_at,
            freshness=freshness,
            payload=payload,
        )

    runtime_source = source(
        "runtime_context",
        contracts.Freshness.UNKNOWN,
        None,
        {
            "active_app": "chrome",
            "active_title": "Fixture Window",
            "active_zone": "chill",
            "idle_state": "active",
            "in_flow": False,
            "time": {},
            "poll_history": ["POLL-PRIVATE-SENTINEL"],
        },
    )
    browser_source = source(
        "browser_state",
        contracts.Freshness.FRESH,
        captured_at,
        {
            "available": True,
            "browser": "chrome",
            "kind": "docs",
            "title": "Compiled Current Title",
            "url": "https://example.test/compiled",
            "selected_text": "Compiled selected focus",
            "local_summary": "Compiled summary",
            "dom_debug": "DOM-PRIVATE-SENTINEL",
            "local_helper_debug": "HELPER-PRIVATE-SENTINEL",
            "site_signals": {"raw": "SIGNAL-PRIVATE-SENTINEL"},
            "age_seconds": 0.0,
            "fresh": True,
        },
    )
    awareness_source = source(
        "live_awareness",
        contracts.Freshness.FRESH,
        captured_at,
        {
            "active_app": "chrome",
            "active_zone": "chill",
            "idle_state": "active",
            "in_flow": False,
            "browser_available": True,
            "browser_kind": "docs",
            "browser_title": "Compiled Current Title",
            "browser_url": "https://example.test/compiled",
            "browser_local_summary": "Compiled summary",
            "browser_social_vibe": "",
            "focus_source": "selected_text",
            "focus_text": "Compiled selected focus",
            "focus_confidence": 1.0,
            "is_confirmed_active": True,
            "browser_site_signals": {"raw": "SIGNAL-PRIVATE-SENTINEL"},
        },
    )
    memory_source = source(
        "awareness_memory",
        contracts.Freshness.UNKNOWN,
        None,
        {"enabled": True, "moments": []},
    )
    expression_source = source(
        "expression_private",
        contracts.Freshness.FRESH,
        captured_at,
        {
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
    checkpoint_source = source(
        "private_checkpoint",
        contracts.Freshness.UNKNOWN,
        None,
        {
            "available": False,
            "session_id": "",
            "summary": "",
            "anchors": [],
            "pending_turns": [],
        },
    )
    private_memory_source = source(
        "private_memory",
        contracts.Freshness.FRESH,
        captured_at,
        {"long_term": []},
    )
    sources = (
        runtime_source,
        browser_source,
        awareness_source,
        memory_source,
        checkpoint_source,
        expression_source,
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
        private_checkpoint=checkpoint_source,
        expression=expression_source,
        private_memory=private_memory_source,
        source_revisions=tuple(
            adapters.SourceRevision(
                item.source,
                item.revision,
                "content_hash",
            )
            for item in sorted(sources, key=lambda item: item.source)
        ),
    )
    gpt._private_memory_evidence_for_turn = lambda *_args, **_kwargs: None

    with pytest.raises(contracts.ContextContractError, match="invalid_prepared_private_turn"):
        gpt._lane_first_inputs(
            text=text,
            viewer_name="Public Viewer",
            stream_mode=True,
            public_platform="youtube",
            metadata={"author_id": "public-viewer"},
            temporal_intent=False,
            grounding_intent=False,
            _prepared_private_turn=(boundary, turn),
            _turn_snapshot=snapshot,
        )

    assert gpt.ask_gpt(
        text,
        _prepared_private_turn=(boundary, turn),
        _turn_snapshot=snapshot,
    )
    streamed = asyncio.run(
        _collect_stream(
            gpt.ask_gpt_stream(
                text,
                _prepared_private_turn=(boundary, turn),
                _turn_snapshot=snapshot,
            )
        )
    )
    assert streamed
    assert captures["sync"][0] == captures["stream"][0]
    system = captures["sync"][0][0]["content"]
    assert system.count("Compiled Current Title") == 1
    assert system.count("Compiled selected focus") == 1
    assert system.count("Compiled summary") == 1
    for forbidden in (
        "DOM-PRIVATE-SENTINEL",
        "HELPER-PRIVATE-SENTINEL",
        "SIGNAL-PRIVATE-SENTINEL",
        "POLL-PRIVATE-SENTINEL",
        "age_seconds",
    ):
        assert forbidden not in system


async def _collect_stream(source):
    return "".join([chunk async for chunk in source])
