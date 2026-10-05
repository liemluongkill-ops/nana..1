"""Pure Task 5 lane resolution, collection, and context selection.

The module is deliberately unwired.  Trusted ingress authority and all source
readers are injected by composition; no ambient request defaults to private
authority, and no adapter here starts services, writes state, or calls a
provider.  Read receipts use each batch's true owner.  Denied or skipped
capabilities use the stable policy capability name because no owner was read.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from itertools import count
from threading import Lock
import time
from typing import Protocol
import uuid

from nana.runtime.context_adapters import (
    SourceCaptureChanged,
    SourceRevision,
    OperatorSourceView,
    PrivateSourceView,
    PublicSourceView,
    SourceBatch,
    FrozenGroundingEvidence,
    MemoryContextBundle,
    build_public_cum2_source_view,
    require_memory_context_bundle,
)
from nana.runtime.context_compiler import (
    _render_payload,
    estimate_tokens_v1,
    registry_slot_ids,
)
from nana.runtime.context_budget import (
    BudgetPolicyV1,
    approved_private_budget_policy_v1,
    copy_budget_policy_v1,
    enforce_budget_v1,
)
from nana.runtime.context_readiness import (
    PrivateReadinessGrant,
    bind_compiled_private_readiness,
    private_readiness_compiler_revision,
    require_private_readiness_grant,
)
from nana.runtime.context_contracts import (
    BudgetPolicy,
    BudgetPolicyStatus,
    CompiledContext,
    CollectionDecision,
    ContextContractError,
    ContextPacket,
    ContextSection,
    Freshness,
    FrozenMapping,
    Lifetime,
    Lane,
    ResolvedContextPacket,
    ResolvedContextRequest,
    Route,
    SelectionDecision,
    SemanticRole,
    SourceSnapshot,
    SourceRef,
    UnresolvedContextRequest,
    _copy_context_packet,
    _copy_context_section,
    _make_resolved_context_request,
    _make_resolved_context_scope,
    _require_canonical_public_scope,
    require_resolved_context_request,
    freeze_payload,
    _structural_digest,
)
from nana.runtime.persona_boundary import (
    resolve_persona_boundary,
    resolve_request_scope,
)
from nana.runtime.public_context_boundary import PublicEventScope


RESOLVER_VERSION = "nana-context-lane-resolver.v1"
PRIVATE_POLICY_REVISION = "budget-policy-v1.review.2026-10-01.r1"
PRIVATE_POLICY_ARTIFACT_SHA256 = "ED0DE1F88A856689A49BC2904F9BEF4C649C87F3638F4EF5844401FDD975E652"


def require_private_policy(request, revision):
    request = require_resolved_context_request(request)
    if request.scope.lane is not Lane.PRIVATE_OWNER or request.route is not Route.INTERACTIVE:
        raise ContextContractError('unapproved_canonical_profile')
    if revision != PRIVATE_POLICY_REVISION:
        raise ContextContractError('budget_policy_unapproved')
    return request


def require_canonical_dispatch_ready(*_args, **_kwargs):
    """Policy approval is not model/source/budget readiness; no live cutover yet.

    Offline integration tests replace this gate only inside guarded fake-source
    and fake-transport fixtures. No environment flag bypasses this gate.
    """
    raise ContextContractError('canonical_runtime_not_ready')


def require_cum2_canonical_dispatch_ready(*_args, **_kwargs):
    """CUM2 has no approved public budget or reviewed model capacity."""

    raise ContextContractError("budget_policy_unapproved")


def require_autonomy_canonical_dispatch_ready(*_args, **_kwargs):
    """Neither autonomy lane has an approved enforced budget or live cutover."""

    raise ContextContractError("budget_policy_unapproved")


@dataclass(frozen=True, slots=True)
class GptTurnInput:
    """Resolved sources; evidence is the final sanitized snapshot from its adapter."""
    request: ResolvedContextRequest
    sections: tuple[ContextSection, ...]
    evidence: FrozenGroundingEvidence | None
    model: str
    max_output_tokens: int
    budget_revision: str
    memory_bundle: MemoryContextBundle | None = None
    readiness: PrivateReadinessGrant | None = None

    def __post_init__(self):
        require_private_policy(self.request, self.budget_revision)
        object.__setattr__(self, 'sections', tuple(_copy_context_section(s) for s in self.sections))
        if self.evidence is not None and type(self.evidence) is not FrozenGroundingEvidence:
            raise ContextContractError('invalid_grounding_evidence')
        if self.memory_bundle is not None:
            memory_bundle = require_memory_context_bundle(self.memory_bundle)
            if self.evidence is not None and self.evidence is not memory_bundle.evidence:
                raise ContextContractError('memory_bundle_evidence_mismatch')
            supplied = tuple(section for section in self.sections if section.source.owner == 'private_memory')
            if supplied != memory_bundle.sections:
                raise ContextContractError('memory_bundle_section_mismatch')
        if type(self.model) is not str or not self.model.strip() or self.model != self.request.model:
            raise ContextContractError('model_request_mismatch')
        reserve = 1600 if self.request.story_mode else 1000
        if type(self.max_output_tokens) is not int or not 0 < self.max_output_tokens <= reserve:
            raise ContextContractError('invalid_output_reserve')
        if self.readiness is not None:
            require_private_readiness_grant(
                self.readiness,
                logical_model=self.model,
                lane=self.request.scope.lane,
                route=self.request.route,
                requested_output_tokens=self.max_output_tokens,
            )


@dataclass(frozen=True, slots=True)
class TurnPlan:
    compiled: CompiledContext
    evidence: FrozenGroundingEvidence | None
    model: str
    max_output_tokens: int
    temperature: float = .75
    memory_bundle: MemoryContextBundle | None = None

    def transport_messages(self):
        """Detached transport copy; no semantic transformations after compile."""
        return [{'role': m.role, 'content': m.content} for m in self.compiled.messages]


def _redact_payload(value, redact):
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, FrozenMapping):
        result = {}
        for key, item in value.items():
            safe_key = redact(key)
            if safe_key in result:
                raise ContextContractError('redacted_mapping_key_conflict')
            result[safe_key] = _redact_payload(item, redact)
        return result
    if isinstance(value, tuple):
        return tuple(_redact_payload(item, redact) for item in value)
    return value


def build_turn_plan(inputs: GptTurnInput, *, redact=None) -> TurnPlan:
    """One transport-neutral private collection -> resolution -> compiler path."""
    from nana.runtime.context_compiler import COMPILER_VERSION, CompilerConfig, ContextCompiler
    if type(inputs) is not GptTurnInput:
        raise ContextContractError('invalid_turn_input')
    inputs = GptTurnInput(inputs.request, inputs.sections, inputs.evidence,
                          inputs.model, inputs.max_output_tokens, inputs.budget_revision,
                          inputs.memory_bundle, inputs.readiness)
    if redact is None:
        from nana.runtime.history_privacy import redact_history_text
        redact = redact_history_text
    request = inputs.request
    # The source adapter already sanitized and froze this exact same-turn
    # snapshot. Preserve it for both rendering and verifier reconstruction.
    memory_bundle = inputs.memory_bundle
    evidence = memory_bundle.evidence if memory_bundle is not None else inputs.evidence
    if evidence is not None and evidence.lane_visible_to not in {'private_only', 'public_safe'}:
        raise ContextContractError('grounding_visibility_denied')
    pinned_ids = {'identity.owner.v1', 'expression.private.v1'}
    if request.story_mode or request.casual_mode:
        pinned_ids.add('mode.turn.private.v1')
    if request.temporal_intent:
        pinned_ids.add('situation.temporal.v1')
    if not pinned_ids.issubset({s.id for s in inputs.sections}):
        raise ContextContractError('context_required_section_unavailable')
    sections = [
        replace(
            section,
            payload=(
                section.payload
                if memory_bundle is not None and section.source.owner == 'private_memory'
                else _redact_payload(section.payload, redact)
            ),
            required=(
                section.required
                or section.id in pinned_ids
                or (memory_bundle is not None and section.source.owner == 'private_memory')
            ),
        )
        for section in inputs.sections
    ]
    has_grounding_section = any(s.id == 'memory.grounding.v1' for s in sections)
    if has_grounding_section and memory_bundle is None:
        raise ContextContractError('duplicate_grounding_authority')
    if request.grounding_intent and evidence is None and memory_bundle is None:
        raise ContextContractError('context_grounding_unavailable')
    if evidence is not None and memory_bundle is None:
        sections.append(ContextSection(
            id='memory.grounding.v1', lifetime=Lifetime.TURN, semantic_role=SemanticRole.EVIDENCE,
            freshness=Freshness.FRESH, visibility=frozenset({Lane.PRIVATE_OWNER}),
            source=SourceRef('private_memory', 'canonical.private.v1', 'memory.grounding.v1'),
            revision='turn-grounding.v1', authority=None, observed_at=None, expires_at=None,
            conflict_key=None, dedupe_key='memory.grounding.v1', max_tokens=1500,
            payload=evidence.to_prompt_block(), formatter_version='legacy_evidence.v1',
            required=True, budget_class='grounding', semantic_status='active', provenance=(), relevance=1.,
        ))
    grounding_required = request.grounding_intent or any(
        section.id == 'memory.grounding.v1' for section in sections
    )
    required_section_ids = {'identity.owner.v1', 'expression.private.v1'}
    if request.story_mode or request.casual_mode:
        required_section_ids.add('mode.turn.private.v1')
    if request.temporal_intent:
        required_section_ids.add('situation.temporal.v1')
    if grounding_required:
        required_section_ids.add('memory.grounding.v1')
    # Reissue copied authority for sanitized input and same-turn grounding intent;
    # no raw caller metadata, scope widening, or reread of any source owner.
    raw = UnresolvedContextRequest(
        request_id=request.request_id, correlation_id=request.correlation_id,
        route=request.route, model=inputs.model, current_input=redact(request.current_input),
        viewer_name=None, stream_mode=False, public_platform=None, caller_metadata={},
        bridge_system=False, story_mode=request.story_mode, casual_mode=request.casual_mode,
        temporal_intent=request.temporal_intent,
        grounding_intent=any(s.source.owner == 'private_memory' for s in sections),
    )
    compiler_config_revision = request.compiler_config_revision
    if inputs.readiness is not None:
        compiler_config_revision = private_readiness_compiler_revision(
            compiler_config_revision,
            inputs.readiness,
        )
    request = _make_resolved_context_request(
        unresolved=raw, scope=request.scope, public_context={},
        captured_wall_time=request.captured_wall_time, captured_monotonic_time=request.captured_monotonic_time,
        compiler_config_revision=compiler_config_revision,
    )
    owners = ('core','owner_identity','private_memory','private_session','runtime_state','expression')
    if any(s.source.owner not in owners for s in sections):
        raise ContextContractError('invalid_source_owner')
    import hashlib
    import json
    class Materialized:
        def __init__(self, owner):
            values = tuple(s for s in sections if s.source.owner == owner)
            if owner == 'private_memory' and memory_bundle is not None:
                revision = memory_bundle.retrieval.source_revision
                if any(section.revision != revision for section in values):
                    raise ContextContractError('memory_source_revision_mismatch')
            else:
                # Content revision is metadata only and does not invent source truth.
                encoded = json.dumps([(s.id, _render_payload(s.payload)) for s in values], ensure_ascii=False, separators=(',',':'))
                revision = 'content-hash-' + hashlib.sha256(encoded.encode('utf-8')).hexdigest()
            self.batch = SourceBatch(owner, revision, tuple(replace(s, revision=revision) for s in values))
        def read(self, _request):
            return self.batch
    sources = PrivateSourceView(*(Materialized(owner) for owner in owners))
    packet = LaneAwareContextCollector(private_sources=sources).collect(request)
    budget = approved_private_budget_policy_v1(request)
    readiness_profile = None
    if inputs.readiness is not None:
        readiness_profile = require_private_readiness_grant(
            inputs.readiness,
            logical_model=inputs.model,
            lane=request.scope.lane,
            route=request.route,
            requested_output_tokens=inputs.max_output_tokens,
            policy_output_reserve=budget.output_reserve_tokens,
        )
    resolved = ContextRuntime().resolve(
        packet,
        budget,
        enforce_budget=True,
        model_capability=(
            None if readiness_profile is None else readiness_profile.capability
        ),
        resolved_model=(
            None if readiness_profile is None else readiness_profile.capability.model
        ),
        require_model_capacity=readiness_profile is not None,
        required_section_ids=frozenset(required_section_ids),
        requested_output_tokens=inputs.max_output_tokens,
    )
    compiled = ContextCompiler().compile(resolved, CompilerConfig(
        COMPILER_VERSION,
        request.compiler_config_revision,
        None if readiness_profile is None else readiness_profile.transport_provider,
        None if readiness_profile is None else readiness_profile.capability.model,
        budget,
    ))
    if readiness_profile is not None:
        bind_compiled_private_readiness(
            compiled,
            inputs.readiness,
            requested_output_tokens=inputs.max_output_tokens,
            policy_output_reserve=budget.output_reserve_tokens,
        )
    try:
        from nana.runtime.context_budget_audit import record_compiled_budget
        record_compiled_budget(compiled)
    except Exception:
        # Optional metadata observation must not abort or alter a valid plan.
        pass
    return TurnPlan(compiled, evidence, inputs.model, inputs.max_output_tokens,
                    memory_bundle=memory_bundle)

_SNAPSHOT_REVISION_COUNTER = count(1)
_SNAPSHOT_REVISION_LOCK = Lock()


def _next_snapshot_revision() -> int:
    with _SNAPSHOT_REVISION_LOCK:
        return next(_SNAPSHOT_REVISION_COUNTER)


def _thaw_snapshot_payload(value):
    if isinstance(value, FrozenMapping):
        return {
            key: _thaw_snapshot_payload(item)
            for key, item in value.items()
        }
    if isinstance(value, tuple):
        return [_thaw_snapshot_payload(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class FrozenMomentView:
    id: str
    timestamp: float
    monotonic: float
    source: str
    active_app: str
    active_zone: str
    browser_kind: str
    title: str
    url_host: str
    focus_source: str
    focus_text: str
    user_text: str
    nana_text: str
    confidence: float
    importance: str
    ttl_seconds: float
    can_act: bool
    frozen_awareness: Mapping[str, object] | None
    frozen_url: str
    frozen_title: str
    turn_counter: int

    @property
    def expires_at(self):
        return self.timestamp + self.ttl_seconds

    def is_expired(self, now=None):
        return float(self.timestamp if now is None else now) >= self.expires_at

    def age_seconds(self, now=None):
        clock = self.timestamp if now is None else float(now)
        return max(0.0, clock - self.timestamp)


class FrozenAwarenessMemoryView:
    """Read-only compatibility view over the turn's captured recent moments."""

    __slots__ = ("_captured_at", "_moments")

    def __init__(self, source: SourceSnapshot, captured_at: float) -> None:
        payload = _thaw_snapshot_payload(source.payload)
        rows = payload.get("moments", []) if isinstance(payload, dict) else []
        moments = []
        for row in rows:
            if not isinstance(row, dict):
                raise ContextContractError("invalid_source_snapshot")
            try:
                moments.append(FrozenMomentView(**row))
            except (TypeError, ValueError) as exc:
                raise ContextContractError("invalid_source_snapshot") from exc
        self._captured_at = float(captured_at)
        self._moments = tuple(moments)

    def get_recent(
        self,
        limit=5,
        min_importance="low",
        exclude_expired=True,
        now=None,
    ):
        clock = self._captured_at if now is None else float(now)
        levels = {"low": 0, "medium": 1, "high": 2}
        minimum = levels.get(min_importance, 0)
        result = []
        for moment in self._moments:
            if exclude_expired and moment.is_expired(clock):
                continue
            if levels.get(moment.importance, 0) < minimum:
                continue
            result.append(moment)
            if len(result) >= limit:
                break
        return result

    def format_recent_moments_block(self, limit=5, min_importance="low"):
        moments = self.get_recent(limit=limit, min_importance=min_importance)
        if not moments:
            return "RECENT MOMENTS: (none yet this session)"
        from nana.runtime.awareness_memory import _age_string, _project_moment_focus

        lines = ["RECENT MOMENTS:"]
        for moment in moments:
            age = _age_string(moment.age_seconds(self._captured_at))
            focus = _project_moment_focus(moment)
            source_tag = f"[{moment.source}]"
            if moment.source == "chat":
                lines.append(
                    f"  {source_tag} {age} | Ba: {moment.user_text or '(no text)'}"
                    + (f" | Na: {moment.nana_text}" if moment.nana_text else "")
                    + (f" | focus: {focus}" if focus else "")
                )
            elif moment.source == "awareness":
                lines.append(
                    f"  {source_tag} {age} | zone={moment.active_zone} | "
                    f"app={moment.active_app} | kind={moment.browser_kind}"
                    + (f" | {focus}" if focus else "")
                )
            elif moment.source == "zone":
                lines.append(
                    f"  {source_tag} {age} | zone={moment.active_zone} | app={moment.active_app}"
                )
            else:
                lines.append(f"  {source_tag} {age} | {focus}")
        return "\n".join(lines)

    def format_timeline_summary(self, max_moments=3):
        moments = self.get_recent(limit=max_moments, min_importance="low")
        if not moments:
            return "No recent moments."
        from nana.runtime.awareness_memory import _age_string, _project_moment_focus

        parts = []
        for moment in moments:
            age = _age_string(moment.age_seconds(self._captured_at))
            focus = _project_moment_focus(moment)
            if moment.source == "awareness":
                parts.append(
                    f"[aw] {age} {moment.browser_kind or moment.active_zone}: {focus}"
                )
            elif moment.source == "chat":
                parts.append(
                    f"[ch] {age} Ba: {moment.user_text or '...'}"
                    + (f" | focus: {focus}" if focus else "")
                )
            elif moment.source == "zone":
                parts.append(f"[zo] {age} zone={moment.active_zone}")
        return " | ".join(parts) if parts else "No recent moments."


class _SnapshotTurnContext(dict):
    __slots__ = ("_context_turn", "_runtime_context_snapshot")

    def __init__(self, values, turn, snapshot):
        super().__init__(values)
        self._context_turn = turn
        self._runtime_context_snapshot = snapshot


@dataclass(frozen=True, slots=True)
class _CapturedLaneAffect:
    warmth: float
    playfulness: float
    assertiveness: float
    intimacy: float
    affection: float
    annoyance: float = 0.0
    lane: str = "private_owner"
    guidance: str = ""

    def as_emotion_dict(self):
        return {
            "affection": self.affection,
            "annoyance": self.annoyance,
            "playfulness": self.playfulness,
            "assertiveness": self.assertiveness,
            "intimacy": self.intimacy,
            "warmth": self.warmth,
        }


@dataclass(frozen=True, slots=True)
class RuntimeContextSnapshot:
    """One immutable private turn view captured from short owner locks."""

    request: ResolvedContextRequest
    snapshot_revision: int
    captured_at: float
    captured_monotonic_at: float
    runtime_context: SourceSnapshot
    browser_state: SourceSnapshot
    live_awareness: SourceSnapshot
    awareness_memory: SourceSnapshot
    source_revisions: tuple[SourceRevision, ...]
    expression: SourceSnapshot | None = None
    private_checkpoint: SourceSnapshot | None = None
    private_memory: SourceSnapshot | None = None

    def __post_init__(self):
        request = require_resolved_context_request(self.request)
        if request.scope.lane is not Lane.PRIVATE_OWNER:
            raise ContextContractError("private_source_capture_denied")
        if type(self.snapshot_revision) is not int or self.snapshot_revision <= 0:
            raise ContextContractError("invalid_snapshot_revision")
        if (
            float(self.captured_at) != request.captured_wall_time
            or float(self.captured_monotonic_at) != request.captured_monotonic_time
        ):
            raise ContextContractError("invalid_source_snapshot")
        expected = {
            "runtime_context": self.runtime_context,
            "browser_state": self.browser_state,
            "live_awareness": self.live_awareness,
            "awareness_memory": self.awareness_memory,
        }
        if self.expression is not None:
            expected["expression_private"] = self.expression
        if self.private_checkpoint is not None:
            expected["private_checkpoint"] = self.private_checkpoint
        if self.private_memory is not None:
            expected["private_memory"] = self.private_memory
        if any(
            type(source) is not SourceSnapshot
            or source.source != name
            or source.captured_at != self.captured_at
            for name, source in expected.items()
        ):
            raise ContextContractError("invalid_source_snapshot")
        revisions = tuple(self.source_revisions)
        if (
            tuple(item.source for item in revisions) != tuple(sorted(expected))
            or any(
                type(item) is not SourceRevision
                or item.revision != expected[item.source].revision
                for item in revisions
            )
        ):
            raise ContextContractError("invalid_source_revision")
        object.__setattr__(self, "request", request)
        object.__setattr__(self, "source_revisions", revisions)

    def source_revision(self, source):
        for item in self.source_revisions:
            if item.source == source:
                return item.revision
        raise KeyError(source)

    def context_mapping(self, turn):
        values = _thaw_snapshot_payload(self.runtime_context.payload)
        browser = _thaw_snapshot_payload(self.browser_state.payload)
        if not isinstance(values, dict) or not isinstance(browser, dict):
            raise ContextContractError("invalid_source_snapshot")
        values["browser"] = browser
        return _SnapshotTurnContext(values, turn, self)

    def awareness_mapping(self):
        values = _thaw_snapshot_payload(self.live_awareness.payload)
        if not isinstance(values, dict):
            raise ContextContractError("invalid_source_snapshot")
        return values

    def awareness_memory_view(self):
        return FrozenAwarenessMemoryView(self.awareness_memory, self.captured_at)

    def expression_lane_affect(self):
        if self.expression is None:
            raise ContextContractError("canonical_source_snapshot_unavailable")
        values = _thaw_snapshot_payload(self.expression.payload)
        affect = values.get("affect") if isinstance(values, dict) else None
        if not isinstance(affect, dict):
            raise ContextContractError("invalid_source_snapshot")
        normalized = {}
        for name in ("warmth", "playfulness", "assertiveness", "intimacy"):
            value = affect.get(name)
            if type(value) not in (int, float):
                raise ContextContractError("invalid_source_snapshot")
            number = float(value)
            if not 0.0 <= number <= 1.0:
                raise ContextContractError("invalid_source_snapshot")
            normalized[name] = number
        return _CapturedLaneAffect(
            warmth=normalized["warmth"],
            playfulness=normalized["playfulness"],
            assertiveness=normalized["assertiveness"],
            intimacy=normalized["intimacy"],
            affection=normalized["warmth"],
        )


def runtime_snapshot_from_context(value):
    snapshot = getattr(value, "_runtime_context_snapshot", None)
    return snapshot if type(snapshot) is RuntimeContextSnapshot else None


def _retry_source_capture(capture):
    for attempt in range(2):
        try:
            return capture()
        except SourceCaptureChanged as exc:
            if attempt == 1:
                raise ContextContractError("source_changed_during_capture") from exc
    raise ContextContractError("source_changed_during_capture")


def capture_private_checkpoint_source(*, request):
    """Copy the private checkpoint under its owner lock after attestation."""

    request = require_resolved_context_request(request)
    if request.scope.lane is not Lane.PRIVATE_OWNER:
        raise ContextContractError("private_source_capture_denied")

    from nana.runtime import session_checkpoint as checkpoint_owner

    reader = getattr(checkpoint_owner, "capture_private_checkpoint_source", None)
    if not callable(reader):
        raise ContextContractError("source_snapshot_api_unavailable")
    from nana.memory import memory, memory_lock

    with memory_lock:
        return reader(memory, captured_at=request.captured_wall_time)


def capture_private_memory_source(*, request):
    """Deep-freeze durable long-term memory once under its owner lock."""

    request = require_resolved_context_request(request)
    if request.scope.lane is not Lane.PRIVATE_OWNER:
        raise ContextContractError("private_source_capture_denied")
    from nana.memory import memory, memory_lock

    with memory_lock:
        long_term = memory.get("long_term", [])
        if not isinstance(long_term, (list, tuple)):
            raise ContextContractError("invalid_source_snapshot")
        payload = freeze_payload({"long_term": long_term})
        source_revision = memory.get("snapshot_revision")
        if type(source_revision) is int and source_revision >= 0:
            revision = f"memory-v2-{source_revision}"
        elif type(source_revision) is str and source_revision.strip():
            revision = f"memory-v2-{source_revision.strip()}"
        else:
            revision = "memory-content-" + _structural_digest(payload).hex()
        return SourceSnapshot(
            source="private_memory",
            revision=revision,
            observed_at=request.captured_wall_time,
            captured_at=request.captured_wall_time,
            freshness=Freshness.FRESH,
            payload=payload,
        )


def capture_private_expression_sources(*, request):
    """Freeze pure mood, affect, and Governor projections for one private turn."""

    request = require_resolved_context_request(request)
    if request.scope.lane is not Lane.PRIVATE_OWNER:
        raise ContextContractError("private_source_capture_denied")
    captured_at = request.captured_wall_time

    import hashlib
    import json

    from nana.memory import memory, memory_lock
    from nana.runtime import affect_lane, mood_continuity, persona

    mood_reader = getattr(mood_continuity, "capture_mood_expression_snapshot", None)
    persona_reader = getattr(persona, "capture_persona_expression_snapshot", None)
    if not callable(mood_reader) or not callable(persona_reader):
        raise ContextContractError("source_snapshot_api_unavailable")

    mood = mood_reader(captured_at=captured_at)
    governor = persona_reader(captured_at=captured_at)
    with memory_lock:
        raw_emotion = memory.get("emotion")
        emotion = dict(raw_emotion) if isinstance(raw_emotion, dict) else {}
    affect = affect_lane.project_affect(emotion, "private_owner")
    try:
        payload = {
            "mood": {
                "tone": mood.tone,
                "energy": float(mood.energy),
                "focus": float(mood.focus),
                "tension": float(mood.tension),
            },
            "affect": {
                "warmth": float(affect.warmth),
                "playfulness": float(affect.playfulness),
                "assertiveness": float(affect.assertiveness),
                "intimacy": float(affect.intimacy),
            },
            "persona": {
                "mode": governor.mode,
                "clamp": governor.clamp,
            },
        }
    except (AttributeError, TypeError, ValueError) as exc:
        raise ContextContractError("invalid_source_snapshot") from exc
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return SourceSnapshot(
        source="expression_private",
        revision="expression-private-" + hashlib.sha256(encoded).hexdigest(),
        observed_at=float(captured_at),
        captured_at=float(captured_at),
        freshness=Freshness.FRESH,
        payload=payload,
    )


def capture_turn_snapshot(request):
    """Capture private sources once after lane resolution and attestation."""

    request = require_resolved_context_request(request)
    if request.scope.lane is not Lane.PRIVATE_OWNER:
        raise ContextContractError("private_source_capture_denied")

    from nana.runtime import awareness_memory as awareness_owner
    from nana.runtime import context as context_owner
    from nana.runtime import live_awareness as awareness_projection

    for module, name in (
        (context_owner, "capture_runtime_context_sources"),
        (awareness_projection, "capture_live_awareness_source"),
        (awareness_owner, "capture_awareness_memory_source"),
    ):
        if not callable(getattr(module, name, None)):
            raise ContextContractError("source_snapshot_api_unavailable")

    captured_at = request.captured_wall_time
    captured_monotonic_at = request.captured_monotonic_time
    checkpoint_source = capture_private_checkpoint_source(request=request)
    expression_source = capture_private_expression_sources(request=request)
    private_memory_source = capture_private_memory_source(request=request)
    context_sources = _retry_source_capture(
        lambda: context_owner.capture_runtime_context_sources(
            captured_at=captured_at,
            captured_monotonic_at=captured_monotonic_at,
        )
    )
    try:
        runtime_source = context_sources.runtime_context
        browser_source = context_sources.browser_state
    except AttributeError as exc:
        raise ContextContractError("invalid_source_snapshot") from exc

    context_values = _thaw_snapshot_payload(runtime_source.payload)
    browser_values = _thaw_snapshot_payload(browser_source.payload)
    if not isinstance(context_values, dict) or not isinstance(browser_values, dict):
        raise ContextContractError("invalid_source_snapshot")
    context_values["browser"] = browser_values
    awareness_source = _retry_source_capture(
        lambda: awareness_projection.capture_live_awareness_source(
            context_values,
            browser_snapshot=browser_source,
            captured_at=captured_at,
            captured_monotonic_at=captured_monotonic_at,
        )
    )
    memory_source = _retry_source_capture(
        lambda: awareness_owner.capture_awareness_memory_source(
            captured_at=captured_at,
        )
    )
    sources = {
        source.source: source
        for source in (
            runtime_source,
            browser_source,
            awareness_source,
            memory_source,
            expression_source,
            checkpoint_source,
            private_memory_source,
        )
    }
    if set(sources) != {
        "runtime_context",
        "browser_state",
        "live_awareness",
        "awareness_memory",
        "expression_private",
        "private_checkpoint",
        "private_memory",
    }:
        raise ContextContractError("invalid_source_snapshot")
    revisions = tuple(
        SourceRevision(
            source,
            sources[source].revision,
            (
                "semantic_revision"
                if source == "private_memory"
                and sources[source].revision.startswith("memory-v2-")
                else "content_hash"
            ),
        )
        for source in sorted(sources)
    )
    return RuntimeContextSnapshot(
        request=request,
        snapshot_revision=_next_snapshot_revision(),
        captured_at=captured_at,
        captured_monotonic_at=captured_monotonic_at,
        runtime_context=runtime_source,
        browser_state=browser_source,
        live_awareness=awareness_source,
        awareness_memory=memory_source,
        source_revisions=revisions,
        expression=expression_source,
        private_checkpoint=checkpoint_source,
        private_memory=private_memory_source,
    )

_POLICY_IDS = {
    (Lane.PRIVATE_OWNER, Route.INTERACTIVE): "private.interactive.v1",
    (Lane.PRIVATE_OWNER, Route.PRIVATE_FAST): "private.private_fast.v1",
    (Lane.PRIVATE_OWNER, Route.AUTONOMY): "private.autonomy.v1",
    (Lane.PUBLIC_STAGE, Route.INTERACTIVE): "public.interactive.v1",
    (Lane.PUBLIC_STAGE, Route.YOUTUBE_CUM2): "public.youtube_cum2.v1",
    (Lane.PUBLIC_STAGE, Route.AUTONOMY): "public.autonomy.v1",
    (Lane.OPERATOR_BACKSTAGE, Route.INTERACTIVE): "operator.interactive.v1",
}

_PUBLIC_ALIAS_GROUPS = (
    (("platform", "source", "provider"), "platform", True),
    (("room_id", "channel_id"), "room_id", False),
    (("stream_session_id", "session_id"), "stream_session_id", False),
    (("event_id", "eventId", "message_id", "request_id"), "event_id", False),
    (
        ("author_id", "authorId", "author_channel_id", "user_id", "userId"),
        "author_id",
        False,
    ),
    (("display_name", "viewer_name", "author_name"), "display_name", False),
    (("actor_key",), "actor_key", False),
)

_PUBLIC_METADATA_LIMITS = {
    "topic": 200,
    "room": 80,
    "channel": 80,
    "language": 32,
}

_CURRENTNESS_REQUIRED_SECTION_IDS = frozenset(
    {"situation.current.v1", "situation.operator_status.v1"}
)


@dataclass(frozen=True, slots=True)
class TrustedIngress:
    """Decision issued by an injected, trusted composition boundary.

    This is a capability seam, not remote authentication.  Task 5 deliberately
    supplies no production issuer.
    """

    lane: Lane
    public_scope: PublicEventScope | None


@dataclass(frozen=True, slots=True)
class _PublicCum2Authority:
    scope: PublicEventScope

    def authorize(self, request: UnresolvedContextRequest) -> TrustedIngress:
        if request.route is not Route.YOUTUBE_CUM2 or request.model != "nana-public":
            raise ContextContractError("trusted_ingress_mismatch")
        return TrustedIngress(Lane.PUBLIC_STAGE, self.scope)


class IngressAuthority(Protocol):
    def authorize(self, request: UnresolvedContextRequest) -> TrustedIngress:
        ...


def _copy_unresolved(value: object) -> UnresolvedContextRequest:
    if type(value) is not UnresolvedContextRequest:
        raise ContextContractError("unresolved_context_request")
    try:
        return UnresolvedContextRequest(
            request_id=value.request_id,
            correlation_id=value.correlation_id,
            route=value.route,
            model=value.model,
            current_input=value.current_input,
            viewer_name=value.viewer_name,
            stream_mode=value.stream_mode,
            public_platform=value.public_platform,
            caller_metadata=value.caller_metadata,
            bridge_system=value.bridge_system,
            story_mode=value.story_mode,
            casual_mode=value.casual_mode,
            temporal_intent=value.temporal_intent,
            grounding_intent=value.grounding_intent,
        )
    except AttributeError as exc:
        raise ContextContractError("unresolved_context_request") from exc


def _policy_id(lane: Lane, route: Route) -> str:
    policy_id = _POLICY_IDS.get((lane, route))
    if policy_id is None:
        raise ContextContractError("invalid_lane_route_scope")
    return policy_id


def _text_aliases_match(
    metadata: Mapping[str, object],
    aliases: tuple[str, ...],
    expected: str,
    *,
    casefold: bool,
) -> bool:
    expected_value = expected.casefold() if casefold else expected
    for alias in aliases:
        if alias not in metadata:
            continue
        value = metadata[alias]
        if value is None:
            return False
        actual = str(value).strip()
        if casefold:
            actual = actual.casefold()
        if actual != expected_value:
            return False
    return True


def _validate_public_aliases(
    request: UnresolvedContextRequest,
    scope: PublicEventScope,
) -> None:
    metadata = request.caller_metadata
    expected = {
        "platform": scope.platform,
        "room_id": scope.room_id,
        "stream_session_id": scope.stream_session_id,
        "event_id": scope.event_id,
        "author_id": scope.identity.author_id,
        "display_name": scope.display_name,
        "actor_key": scope.identity.actor_key,
    }
    for aliases, field, casefold in _PUBLIC_ALIAS_GROUPS:
        if not _text_aliases_match(
            metadata,
            aliases,
            expected[field],
            casefold=casefold,
        ):
            raise ContextContractError("trusted_ingress_mismatch")

    if (
        request.public_platform is None
        or request.public_platform.strip().casefold() != scope.platform.casefold()
    ):
        raise ContextContractError("trusted_ingress_mismatch")
    if (
        request.viewer_name is not None
        and request.viewer_name.strip()
        and request.viewer_name.strip() != scope.display_name
    ):
        raise ContextContractError("trusted_ingress_mismatch")


def _bounded_public_metadata(metadata: Mapping[str, object]) -> dict[str, str]:
    nested = metadata.get("public_metadata")
    if not isinstance(nested, Mapping):
        return {}
    result: dict[str, str] = {}
    for key, limit in _PUBLIC_METADATA_LIMITS.items():
        if key not in nested or nested[key] is None:
            continue
        value = str(nested[key]).strip()
        if value:
            result[key] = value[:limit]
    return result


def _public_context_projection(
    request: UnresolvedContextRequest,
    scope: PublicEventScope,
) -> dict[str, object]:
    metadata = request.caller_metadata
    return {
        "platform": scope.platform,
        "display_name": scope.display_name[:80],
        "memory_consent": bool(
            metadata.get("memory_consent") is True
            or metadata.get("public_memory_consent") is True
        ),
        "public_metadata": _bounded_public_metadata(metadata),
    }


class LaneResolver:
    """Resolve a copied request using only an injected ingress authority."""

    def __init__(
        self,
        authority: IngressAuthority | None = None,
        *,
        wall_clock: Callable[[], float] = time.time,
        monotonic_clock: Callable[[], float] = time.monotonic,
        compiler_config_revision: str = "compiler-config.v1",
    ) -> None:
        self._authority = authority
        self._wall_clock = wall_clock
        self._monotonic_clock = monotonic_clock
        self._compiler_config_revision = compiler_config_revision

    def resolve(self, request: UnresolvedContextRequest) -> ResolvedContextRequest:
        unresolved = _copy_unresolved(request)
        if self._authority is None:
            raise ContextContractError("trusted_ingress_required")
        try:
            ingress = self._authority.authorize(unresolved)
        except ContextContractError:
            raise
        except Exception as exc:
            raise ContextContractError("trusted_ingress_rejected") from exc
        if type(ingress) is not TrustedIngress:
            raise ContextContractError("invalid_trusted_ingress")
        if not isinstance(ingress.lane, Lane):
            raise ContextContractError("unknown_lane")

        if ingress.lane is Lane.PUBLIC_STAGE:
            public_scope = _require_canonical_public_scope(ingress.public_scope)
            if unresolved.bridge_system:
                raise ContextContractError("trusted_ingress_mismatch")
            _validate_public_aliases(unresolved, public_scope)
            canonical_metadata = {
                "platform": public_scope.platform,
                "room_id": public_scope.room_id,
                "stream_session_id": public_scope.stream_session_id,
                "event_id": public_scope.event_id,
                "display_name": public_scope.display_name,
                "author_id": public_scope.identity.author_id,
            }
            boundary = resolve_request_scope(
                viewer_name=unresolved.viewer_name,
                stream_mode=unresolved.stream_mode,
                public_platform=unresolved.public_platform,
                metadata=canonical_metadata,
            )
            if (
                not boundary.public
                or boundary.interaction_scope != "public_viewer"
                or type(boundary.scope) is not PublicEventScope
                or boundary.scope != public_scope
            ):
                raise ContextContractError("trusted_ingress_mismatch")
            public_context: object = _public_context_projection(
                unresolved,
                public_scope,
            )
            interaction_scope = boundary.interaction_scope
        elif ingress.lane is Lane.PRIVATE_OWNER:
            if ingress.public_scope is not None or unresolved.bridge_system:
                raise ContextContractError("trusted_ingress_mismatch")
            if (
                unresolved.stream_mode
                or (unresolved.viewer_name is not None and unresolved.viewer_name.strip())
                or (
                    unresolved.public_platform is not None
                    and unresolved.public_platform.strip()
                )
            ):
                raise ContextContractError("trusted_ingress_mismatch")
            boundary = resolve_request_scope(
                viewer_name=unresolved.viewer_name,
                stream_mode=unresolved.stream_mode,
                public_platform=unresolved.public_platform,
                metadata={},
            )
            if boundary.public or boundary.interaction_scope != "private_owner":
                raise ContextContractError("trusted_ingress_mismatch")
            public_scope = None
            public_context = {}
            interaction_scope = boundary.interaction_scope
        else:
            if ingress.public_scope is not None or not unresolved.bridge_system:
                raise ContextContractError("trusted_ingress_mismatch")
            if (
                unresolved.stream_mode
                or (unresolved.viewer_name is not None and unresolved.viewer_name.strip())
                or (
                    unresolved.public_platform is not None
                    and unresolved.public_platform.strip()
                )
            ):
                raise ContextContractError("trusted_ingress_mismatch")
            ordinary = resolve_request_scope(
                viewer_name=None,
                stream_mode=False,
                public_platform=None,
                metadata={},
            )
            boundary = resolve_persona_boundary(bridge_system=True, platform=None)
            if (
                ordinary.public
                or ordinary.interaction_scope != "private_owner"
                or boundary.public
                or boundary.interaction_scope != "bridge_system"
            ):
                raise ContextContractError("trusted_ingress_mismatch")
            public_scope = None
            public_context = {}
            interaction_scope = boundary.interaction_scope

        policy_id = _policy_id(ingress.lane, unresolved.route)
        scope = _make_resolved_context_scope(
            lane=ingress.lane,
            interaction_scope=interaction_scope,
            public_scope=public_scope,
            policy_id=policy_id,
            resolver_version=RESOLVER_VERSION,
        )
        return _make_resolved_context_request(
            unresolved=unresolved,
            scope=scope,
            public_context=public_context,
            captured_wall_time=self._wall_clock(),
            captured_monotonic_time=self._monotonic_clock(),
            compiler_config_revision=self._compiler_config_revision,
        )


@dataclass(frozen=True, slots=True)
class _ReadRule:
    capability: str
    decision: str
    reason: str


def _read(capability: str, *, conditional: bool = False) -> _ReadRule:
    return _ReadRule(
        capability,
        "CONDITIONAL_READ" if conditional else "ALLOW_READ",
        "condition_met" if conditional else "lane_policy",
    )


def _skip(capability: str) -> _ReadRule:
    return _ReadRule(capability, "CONDITIONAL_SKIP", "condition_not_met")


def _deny(capability: str, *, public: bool = False) -> _ReadRule:
    return _ReadRule(
        capability,
        "DENY_NOT_READ",
        "public_lane_denied" if public else "lane_policy",
    )


class _PacketBuilder:
    def __init__(
        self,
        request: ResolvedContextRequest,
        capabilities: frozenset[str],
    ) -> None:
        self.request = request
        self.capabilities = capabilities
        self.sections: list[ContextSection] = []
        self.revisions: dict[str, str] = {}
        self.receipts: dict[str, CollectionDecision] = {}

    def add_without_read(self, rule: _ReadRule) -> None:
        if rule.capability in self.receipts or rule.capability in self.revisions:
            raise ContextContractError("duplicate_source_id")
        self.receipts[rule.capability] = CollectionDecision(
            source=rule.capability,
            decision=rule.decision,
            reason=rule.reason,
            source_revision=None,
        )

    def add_read(self, rule: _ReadRule, adapter: object) -> None:
        try:
            batch_value = adapter.read(self.request)  # type: ignore[attr-defined]
        except ContextContractError:
            raise
        except Exception as exc:
            raise ContextContractError("source_adapter_error") from exc
        if type(batch_value) is not SourceBatch:
            raise ContextContractError("invalid_source_batch")
        batch = SourceBatch(
            owner=batch_value.owner,
            revision=batch_value.revision,
            sections=batch_value.sections,
        )
        if batch.owner in self.capabilities and batch.owner != rule.capability:
            raise ContextContractError("duplicate_source_id")
        if batch.owner in self.receipts or batch.owner in self.revisions:
            raise ContextContractError("duplicate_source_id")
        self.revisions[batch.owner] = batch.revision
        self.receipts[batch.owner] = CollectionDecision(
            source=batch.owner,
            decision=rule.decision,
            reason=rule.reason,
            source_revision=batch.revision,
        )
        self.sections.extend(batch.sections)


class LaneAwareContextCollector:
    """Dispatch only an attested request to its lane-shaped capabilities."""

    def __init__(
        self,
        *,
        private_sources: PrivateSourceView | None = None,
        public_sources: PublicSourceView | None = None,
        operator_sources: OperatorSourceView | None = None,
        public_grounding_enabled: bool = False,
        capture_id_factory: Callable[[], str] | None = None,
        snapshot_revision_factory: Callable[[], int] | None = None,
    ) -> None:
        self._private_sources = private_sources
        self._public_sources = public_sources
        self._operator_sources = operator_sources
        if type(public_grounding_enabled) is not bool:
            raise ContextContractError("invalid_collection_policy")
        self._public_grounding_enabled = public_grounding_enabled
        self._capture_id_factory = capture_id_factory or (
            lambda: f"context-{uuid.uuid4().hex}"
        )
        self._snapshot_revision_factory = (
            snapshot_revision_factory or _next_snapshot_revision
        )

    def collect(self, request: ResolvedContextRequest) -> ContextPacket:
        resolved = require_resolved_context_request(request)
        expected_policy = _policy_id(resolved.scope.lane, resolved.route)
        if resolved.scope.policy_id != expected_policy:
            raise ContextContractError("invalid_collection_policy")
        if resolved.route is Route.PRIVATE_FAST:
            raise ContextContractError("unsupported_collector_profile")

        if resolved.scope.lane is Lane.PUBLIC_STAGE:
            if type(self._public_sources) is not PublicSourceView:
                raise ContextContractError("source_view_unavailable")
            builder = self._collect_public(resolved, self._public_sources)
        elif resolved.scope.lane is Lane.PRIVATE_OWNER:
            if type(self._private_sources) is not PrivateSourceView:
                raise ContextContractError("source_view_unavailable")
            builder = self._collect_private(resolved, self._private_sources)
        elif resolved.scope.lane is Lane.OPERATOR_BACKSTAGE:
            if type(self._operator_sources) is not OperatorSourceView:
                raise ContextContractError("source_view_unavailable")
            builder = self._collect_operator(resolved, self._operator_sources)
        else:
            raise ContextContractError("unknown_lane")

        section_ids = [section.id for section in builder.sections]
        if len(section_ids) != len(set(section_ids)):
            raise ContextContractError("duplicate_section_id")
        return ContextPacket(
            schema_version=1,
            capture_id=self._capture_id_factory(),
            snapshot_revision=self._snapshot_revision_factory(),
            request=resolved,
            source_revisions=tuple(sorted(builder.revisions.items())),
            sections=tuple(
                sorted(
                    builder.sections,
                    key=lambda section: (section.id, section.source.owner),
                )
            ),
            collection_receipt=tuple(
                builder.receipts[source]
                for source in sorted(builder.receipts)
            ),
        )

    @staticmethod
    def _apply_rules(
        request: ResolvedContextRequest,
        sources: object,
        rules: tuple[_ReadRule, ...],
    ) -> _PacketBuilder:
        capabilities = tuple(rule.capability for rule in rules)
        if len(capabilities) != len(set(capabilities)):
            raise ContextContractError("duplicate_source_id")
        builder = _PacketBuilder(request, frozenset(capabilities))
        for rule in rules:
            if rule.decision in {"CONDITIONAL_SKIP", "DENY_NOT_READ"}:
                builder.add_without_read(rule)
                continue
            try:
                adapter = getattr(sources, rule.capability)
            except AttributeError as exc:
                raise ContextContractError("source_view_unavailable") from exc
            builder.add_read(rule, adapter)
        return builder

    def _collect_public(
        self,
        request: ResolvedContextRequest,
        sources: PublicSourceView,
    ) -> _PacketBuilder:
        if request.route is Route.YOUTUBE_CUM2:
            grounding = _deny("public_grounding", public=True)
            session = _read("public_session")
        elif request.route is Route.AUTONOMY:
            grounding = _deny("public_grounding", public=True)
            session = _read("public_session", conditional=True)
        else:
            consent = request.public_context.get("memory_consent") is True
            grounding = (
                _read("public_grounding", conditional=True)
                if (
                    self._public_grounding_enabled
                    and request.grounding_intent
                    and consent
                )
                else _skip("public_grounding")
            )
            session = _read("public_session")
        rules = (
            _read("core", conditional=True),
            _read("public_scope"),
            session,
            grounding,
            _read("expression"),
            _deny("owner_identity", public=True),
            _deny("private_memory", public=True),
            _deny("private_session", public=True),
            _deny("runtime_state", public=True),
        )
        return self._apply_rules(request, sources, rules)

    def _collect_private(
        self,
        request: ResolvedContextRequest,
        sources: PrivateSourceView,
    ) -> _PacketBuilder:
        if request.route is Route.AUTONOMY:
            private_rules = (
                _read("core", conditional=True),
                _read("owner_identity", conditional=True),
                _deny("private_memory"),
                _deny("private_session"),
                _read("runtime_state"),
                _read("expression", conditional=True),
            )
        else:
            memory = (
                _read("private_memory", conditional=True)
                if request.grounding_intent
                else _skip("private_memory")
            )
            private_rules = (
                _read("core", conditional=True),
                _read("owner_identity"),
                memory,
                _read("private_session"),
                _read("runtime_state"),
                _read("expression"),
            )
        rules = private_rules + (
            _deny("public_scope"),
            _deny("public_session"),
            _deny("public_grounding"),
        )
        return self._apply_rules(request, sources, rules)

    def _collect_operator(
        self,
        request: ResolvedContextRequest,
        sources: OperatorSourceView,
    ) -> _PacketBuilder:
        rules = (
            _read("core", conditional=True),
            _skip("runtime_state"),
            _read("expression"),
            _deny("owner_identity"),
            _deny("private_memory"),
            _deny("private_session"),
            _deny("public_scope"),
            _deny("public_session"),
            _deny("public_grounding"),
        )
        return self._apply_rules(request, sources, rules)


_AUTHORITY_RANK = {
    "explicit_verified_correction": 5,
    "verified_current_state": 4,
    "verified_current": 4,
    "verified": 4,
    "grounded_session_fact": 3,
    "grounded_session": 3,
    "active_durable_fact": 2,
    "active_durable": 2,
    "historical_fact": 1,
}
_STATUS_RANK = {
    "active": 4,
    "latest_known": 3,
    None: 2,
    "historical": 1,
    "superseded": 0,
}
_FRESHNESS_RANK = {
    Freshness.FRESH: 4,
    Freshness.WARM: 3,
    Freshness.STALE: 2,
    Freshness.UNKNOWN: 1,
    Freshness.EXPIRED: 0,
}


def _rank(section: ContextSection) -> tuple[float, ...]:
    return (
        float(_STATUS_RANK.get(section.semantic_status, 1)),
        float(_AUTHORITY_RANK.get(section.authority or "", 0)),
        float(_FRESHNESS_RANK[section.freshness]),
        float(section.observed_at or 0.0),
        float(section.relevance or 0.0),
    )


def _winner(sections: list[ContextSection]) -> ContextSection:
    return sorted(
        sections,
        key=lambda section: tuple(-value for value in _rank(section))
        + (section.id,),
    )[0]


def _subject_identity(section: ContextSection) -> tuple[str, str] | None:
    if not isinstance(section.payload, FrozenMapping):
        return None
    subject = section.payload.get("subject")
    subject_role = section.payload.get("subject_role")
    if (
        type(subject) is not str
        or not subject.strip()
        or type(subject_role) is not str
        or not subject_role.strip()
    ):
        return None
    return subject.strip(), subject_role.strip()


def _subject_domain(section: ContextSection) -> tuple[str, str, str] | None:
    subject_identity = _subject_identity(section)
    canonical_value = (
        section.payload.get("canonical_value")
        if isinstance(section.payload, FrozenMapping)
        else None
    )
    if (
        section.semantic_role not in {SemanticRole.FACT, SemanticRole.STATE}
        or section.conflict_key is None
        or subject_identity is None
        or type(canonical_value) is not str
        or not canonical_value.strip()
        or (section.authority or "") not in _AUTHORITY_RANK
    ):
        return None
    return (
        subject_identity[0],
        subject_identity[1],
        section.conflict_key.strip().casefold(),
    )


def _canonical_value(section):
    if not isinstance(section.payload, FrozenMapping):
        return None
    return section.payload.get("canonical_value")


def _source_selected_temporal(section):
    return (
        isinstance(section.payload, FrozenMapping)
        and section.payload.get("temporal_selected") is True
    )


class ContextRuntime:
    """Select a one-turn packet and optionally enforce an injected v1 policy."""

    def resolve(
        self,
        packet: ContextPacket,
        budget_policy: BudgetPolicy | BudgetPolicyV1,
        *,
        enforce_budget: bool = False,
        allow_synthetic_budget: bool = False,
        source_projections: tuple = (),
        model_capability=None,
        resolved_model: str | None = None,
        require_model_capacity: bool = False,
        requested_output_tokens: int | None = None,
        required_section_ids: frozenset[str] | None = None,
    ) -> ResolvedContextPacket:
        if type(budget_policy) is BudgetPolicy:
            try:
                budget_policy = BudgetPolicy(
                    policy_id=budget_policy.policy_id,
                    revision=budget_policy.revision,
                    status=budget_policy.status,
                    max_estimated_tokens=budget_policy.max_estimated_tokens,
                    max_characters=budget_policy.max_characters,
                )
            except AttributeError as exc:
                raise ContextContractError("invalid_budget_policy") from exc
        elif type(budget_policy) is BudgetPolicyV1:
            budget_policy = copy_budget_policy_v1(budget_policy)
        else:
            raise ContextContractError("invalid_budget_policy")
        if type(enforce_budget) is not bool:
            raise ContextContractError("invalid_budget_enforcement")
        if enforce_budget and type(budget_policy) is not BudgetPolicyV1:
            raise ContextContractError("budget_policy_unapproved")
        selected_packet = _copy_context_packet(packet)
        request = selected_packet.request
        expected_policy = _policy_id(request.scope.lane, request.route)
        if request.scope.policy_id != expected_policy:
            raise ContextContractError("invalid_collection_policy")

        registry = registry_slot_ids(request.scope.lane, request.route)
        registry_order = {section_id: index for index, section_id in enumerate(registry)}
        candidates = sorted(
            selected_packet.sections,
            key=lambda section: (
                registry_order.get(section.id, len(registry_order)),
                section.id,
                section.source.owner,
            ),
        )
        outcomes: dict[str, tuple[str, str, str | None]] = {}
        eligible: list[ContextSection] = []
        captured_at = request.captured_wall_time

        for section in candidates:
            if request.scope.lane not in section.visibility:
                outcomes[section.id] = ("denied", "lane_visibility", None)
                continue
            if section.id not in registry_order:
                outcomes[section.id] = ("invalid", "invalid_section", None)
                continue
            if (
                section.id in _CURRENTNESS_REQUIRED_SECTION_IDS
                and section.observed_at is None
            ):
                outcomes[section.id] = ("invalid", "invalid_section", None)
                continue
            if (
                section.observed_at is not None
                and section.observed_at > captured_at + 2.0
            ):
                outcomes[section.id] = ("invalid", "invalid_section", None)
                continue
            if (
                section.freshness is Freshness.EXPIRED
                or (
                    section.expires_at is not None
                    and section.expires_at <= captured_at
                )
                or (
                    section.freshness is Freshness.STALE
                    and section.semantic_status
                    not in {"historical", "latest_known", "superseded"}
                )
            ):
                outcomes[section.id] = ("expired", "expired", None)
                continue
            if section.freshness is Freshness.UNKNOWN:
                outcomes[section.id] = ("invalid", "invalid_section", None)
                continue
            if section.semantic_status == "superseded":
                outcomes[section.id] = ("irrelevant", "not_relevant", None)
                continue
            is_historical = (
                section.semantic_status == "historical"
                or section.authority == "historical_fact"
            )
            if is_historical and not (
                request.temporal_intent and _source_selected_temporal(section)
            ):
                outcomes[section.id] = ("irrelevant", "not_relevant", None)
                continue
            if (
                not section.required
                and section.relevance is not None
                and section.relevance <= 0.0
            ):
                outcomes[section.id] = ("irrelevant", "not_relevant", None)
                continue
            if not _render_payload(section.payload):
                outcomes[section.id] = ("invalid", "invalid_section", None)
                continue
            eligible.append(section)

        conflict_groups: dict[tuple[str, str, str], list[ContextSection]] = {}
        for section in eligible:
            domain = _subject_domain(section)
            if domain is not None:
                conflict_groups.setdefault(domain, []).append(section)
        for group in conflict_groups.values():
            if len(group) < 2:
                continue
            highest = max(_AUTHORITY_RANK[section.authority] for section in group)
            leaders = [
                section for section in group
                if _AUTHORITY_RANK[section.authority] == highest
            ]
            values = {_canonical_value(section) for section in leaders}
            if len(values) != 1:
                for section in group:
                    outcomes[section.id] = (
                        "conflict_unresolved",
                        "conflict_unresolved",
                        None,
                    )
                continue
            value = next(iter(values))
            winner = min(
                (section for section in leaders if _canonical_value(section) == value),
                key=lambda section: section.id,
            )
            for section in group:
                if section is not winner:
                    same_value = _canonical_value(section) == value
                    outcomes[section.id] = (
                        "duplicate" if same_value else "conflict_loser",
                        "duplicate" if same_value else "conflict_loser",
                        winner.id,
                    )

        dedupe_groups: dict[tuple[SemanticRole, str, object], list[ContextSection]] = {}
        for section in eligible:
            if section.id in outcomes:
                continue
            if section.semantic_role in {SemanticRole.FACT, SemanticRole.STATE}:
                subject = _subject_identity(section)
                if subject is None:
                    continue
            else:
                subject = None
            dedupe_groups.setdefault(
                (section.semantic_role, section.dedupe_key, subject),
                [],
            ).append(section)
        for group in dedupe_groups.values():
            if len(group) < 2:
                continue
            if group[0].semantic_role in {SemanticRole.FACT, SemanticRole.STATE}:
                values = {_canonical_value(section) for section in group}
                if None in values or len(values) != 1:
                    continue
                winner = min(group, key=lambda section: section.id)
            else:
                winner = _winner(group)
            for section in group:
                if section is not winner:
                    outcomes[section.id] = ("duplicate", "duplicate", winner.id)

        decisions: list[SelectionDecision] = []
        selected: list[ContextSection] = []
        total_tokens = 0
        for section in candidates:
            outcome = outcomes.get(section.id)
            if outcome is None:
                outcome = (
                    "included",
                    "required" if section.required else "selected",
                    None,
                )
                selected.append(section)
                total_tokens += estimate_tokens_v1(_render_payload(section.payload))
            decision, reason, related = outcome
            decisions.append(
                SelectionDecision(
                    section_id=section.id,
                    decision=decision,
                    reason=reason,
                    source=section.source,
                    lifetime=section.lifetime,
                    semantic_role=section.semantic_role,
                    revision=section.revision,
                    formatter_version=section.formatter_version,
                    budget_class=section.budget_class,
                    chars=None,
                    token_estimate=None,
                    related_section_id=related,
                )
            )

        resolved = ResolvedContextPacket(
            packet=selected_packet,
            selected_sections=tuple(selected),
            decisions=tuple(decisions),
            total_estimated_tokens=total_tokens,
        )
        if not enforce_budget:
            return resolved
        return enforce_budget_v1(
            resolved,
            budget_policy,
            allow_synthetic_budget=allow_synthetic_budget,
            source_projections=source_projections,
            model_capability=model_capability,
            resolved_model=resolved_model,
            require_model_capacity=require_model_capacity,
            requested_output_tokens=requested_output_tokens,
            required_section_ids=required_section_ids,
        )


def resolve_public_cum2_request(
    *,
    scope: PublicEventScope,
    current_input: str,
    request_id: str,
    correlation_id: str,
    wall_clock: Callable[[], float] = time.time,
    monotonic_clock: Callable[[], float] = time.monotonic,
) -> ResolvedContextRequest:
    """Resolve one admitted YouTube event before any public source is read."""

    scope = _require_canonical_public_scope(scope)
    unresolved = UnresolvedContextRequest(
        request_id=request_id,
        correlation_id=correlation_id,
        route=Route.YOUTUBE_CUM2,
        model="nana-public",
        current_input=current_input,
        viewer_name=scope.display_name,
        stream_mode=True,
        public_platform=scope.platform,
        caller_metadata={
            "platform": scope.platform,
            "room_id": scope.room_id,
            "stream_session_id": scope.stream_session_id,
            "event_id": scope.event_id,
            "display_name": scope.display_name,
            "author_id": scope.identity.author_id,
            "actor_key": scope.identity.actor_key,
        },
        bridge_system=False,
        story_mode=False,
        casual_mode=False,
        temporal_intent=False,
        grounding_intent=False,
    )
    return LaneResolver(
        _PublicCum2Authority(scope),
        wall_clock=wall_clock,
        monotonic_clock=monotonic_clock,
    ).resolve(unresolved)


def build_public_cum2_plan(
    request: ResolvedContextRequest,
    continuity_source: SourceSnapshot,
) -> CompiledContext:
    """Compile one public CUM2 candidate under an unapproved measurement budget."""

    from nana.runtime.context_compiler import (
        COMPILER_VERSION,
        CompilerConfig,
        ContextCompiler,
    )

    request = require_resolved_context_request(request)
    if request.scope.lane is not Lane.PUBLIC_STAGE or request.route is not Route.YOUTUBE_CUM2:
        raise ContextContractError("unapproved_canonical_profile")
    sources = build_public_cum2_source_view(request, continuity_source)
    packet = LaneAwareContextCollector(public_sources=sources).collect(request)
    budget = BudgetPolicy(
        policy_id="budget.shadow.public_cum2.v1",
        revision="public-cum2.measurement.2026-10-03",
        status=BudgetPolicyStatus.SHADOW_CANDIDATE,
        max_estimated_tokens=4_000,
        max_characters=16_000,
    )
    resolved = ContextRuntime().resolve(packet, budget, enforce_budget=False)
    return ContextCompiler().compile(
        resolved,
        CompilerConfig(
            compiler_version=COMPILER_VERSION,
            revision=request.compiler_config_revision,
            resolved_provider=None,
            resolved_model=None,
            budget_policy=budget,
        ),
    )


__all__ = [
    "RuntimeContextSnapshot",
    "capture_private_expression_sources",
    "capture_private_memory_source",
    "capture_turn_snapshot",
    "runtime_snapshot_from_context",
    "GptTurnInput",
    "TurnPlan",
    "build_turn_plan",
    "PRIVATE_POLICY_REVISION",
    "PRIVATE_POLICY_ARTIFACT_SHA256",
    "require_private_policy",
    "require_canonical_dispatch_ready",
    "require_autonomy_canonical_dispatch_ready",
    "require_cum2_canonical_dispatch_ready",
    "resolve_public_cum2_request",
    "build_public_cum2_plan",
    "ContextRuntime",
    "IngressAuthority",
    "LaneAwareContextCollector",
    "LaneResolver",
    "RESOLVER_VERSION",
    "TrustedIngress",
]
