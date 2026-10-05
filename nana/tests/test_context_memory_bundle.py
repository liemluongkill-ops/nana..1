"""Task 11 one-shot private memory bundle contracts (offline only)."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import importlib
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from nana.runtime import context_adapters, context_runtime
from nana.runtime.context_compiler import _render_payload
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
from nana.runtime.public_identity import CanonicalPublicIdentity


NOW = 20_000.0


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


class _Authority:
    def __init__(self, lane, public_scope=None):
        self.lane = lane
        self.public_scope = public_scope

    def authorize(self, _request):
        return context_runtime.TrustedIngress(self.lane, self.public_scope)


def _request(text="Nana co nho GPU cua Ba khong?"):
    raw = UnresolvedContextRequest(
        "task11-request",
        "task11-correlation",
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
        False,
        True,
    )
    return context_runtime.LaneResolver(
        _Authority(Lane.PRIVATE_OWNER),
        wall_clock=lambda: NOW,
        monotonic_clock=lambda: NOW / 2,
    ).resolve(raw)


def _public_request():
    identity = CanonicalPublicIdentity("youtube", "viewer-1", "youtube:viewer-1")
    scope = PublicEventScope(
        "youtube", "room-1", "stream-1", "event-1", "Alice", identity
    )
    raw = UnresolvedContextRequest(
        "task11-public-request",
        "task11-public-correlation",
        Route.INTERACTIVE,
        "fake-public-model",
        "hello",
        "Alice",
        True,
        "youtube",
        {
            "platform": "youtube",
            "room_id": "room-1",
            "stream_session_id": "stream-1",
            "event_id": "event-1",
            "display_name": "Alice",
            "author_id": "viewer-1",
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


def _api():
    names = (
        "MemoryContextBundle",
        "MemoryRetrievalRecord",
        "MemoryRetrievalResult",
        "build_memory_context_bundle",
        "require_memory_context_bundle",
    )
    missing = [name for name in names if not hasattr(context_adapters, name)]
    assert not missing, "Task 11 memory bundle API missing: " + ", ".join(missing)
    assert hasattr(context_runtime, "capture_private_memory_source"), (
        "Task 11 private memory snapshot API missing"
    )
    return SimpleNamespace(**{name: getattr(context_adapters, name) for name in names})


def _source(records, revision="memory-v2-7"):
    return SourceSnapshot(
        source="private_memory",
        revision=revision,
        observed_at=NOW,
        captured_at=NOW,
        freshness=Freshness.FRESH,
        payload={"long_term": list(records)},
    )


def _record(api, record_id="record-1", **overrides):
    values = {
        "record_id": record_id,
        "source_event_id": f"event-{record_id}",
        "source": "user",
        "memory_type": "project_fact",
        "semantic_role": SemanticRole.FACT,
        "text": "Ba uses an RTX 4070",
        "visibility": frozenset({Lane.PRIVATE_OWNER}),
        "confidence": 0.95,
        "relevance": 0.95,
        "observed_at": NOW - 10,
        "expires_at": None,
        "subject": "Ba",
        "subject_role": "owner",
        "conflict_key": "hardware.gpu.current",
        "canonical_value": "RTX 4070",
        "authority": "verified_current_state",
        "semantic_status": "active",
        "temporal_selected": False,
    }
    values.update(overrides)
    return api.MemoryRetrievalRecord(**values)


def _result(api, records, revision="memory-v2-7"):
    return api.MemoryRetrievalResult(
        source_revision=revision,
        status="ok",
        records=tuple(records),
        semantic_used=False,
        fallback_used=False,
        rejected={},
        query_fingerprint="fixture-query",
    )


def _thaw(value):
    if isinstance(value, FrozenMapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def test_public_request_fails_before_private_memory_import_or_retrieval(monkeypatch):
    _api()
    touched = []
    real_import = __import__

    def guarded_import(name, *args, **kwargs):
        if name == "nana.memory":
            touched.append(name)
            raise AssertionError("public path imported private memory")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", guarded_import)
    with pytest.raises(ContextContractError, match="private_source_capture_denied"):
        context_runtime.capture_private_memory_source(request=_public_request())
    assert touched == []


def test_fresh_public_capture_rejects_before_first_private_memory_import():
    script = r'''
import builtins
from pathlib import Path
import sys
import types

root = Path.cwd() / "nana"
nana_package = types.ModuleType("nana")
nana_package.__path__ = [str(root)]
runtime_package = types.ModuleType("nana.runtime")
runtime_package.__path__ = [str(root / "runtime")]
nana_package.runtime = runtime_package
sys.modules["nana"] = nana_package
sys.modules["nana.runtime"] = runtime_package

touched = []
real_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name == "nana.memory":
        touched.append(name)
        raise AssertionError("private memory imported")
    return real_import(name, *args, **kwargs)
builtins.__import__ = guarded_import

from nana.runtime import context_runtime
from nana.runtime.context_contracts import ContextContractError, Lane, Route, UnresolvedContextRequest
from nana.runtime.public_context_boundary import PublicEventScope
from nana.runtime.public_identity import CanonicalPublicIdentity

identity = CanonicalPublicIdentity("youtube", "viewer-1", "youtube:viewer-1")
scope = PublicEventScope("youtube", "room-1", "stream-1", "event-1", "Alice", identity)
class Authority:
    def authorize(self, _request):
        return context_runtime.TrustedIngress(Lane.PUBLIC_STAGE, scope)
raw = UnresolvedContextRequest(
    "fresh-public", "fresh-correlation", Route.INTERACTIVE, "fake", "hello",
    "Alice", True, "youtube",
    {"platform": "youtube", "room_id": "room-1", "stream_session_id": "stream-1",
     "event_id": "event-1", "display_name": "Alice", "author_id": "viewer-1"},
    False, False, False, False, False,
)
request = context_runtime.LaneResolver(Authority()).resolve(raw)
try:
    context_runtime.capture_private_memory_source(request=request)
except ContextContractError as exc:
    assert exc.code == "private_source_capture_denied", exc.code
else:
    raise AssertionError("public request was accepted")
assert touched == [], touched
print("PUBLIC_PREIMPORT_TRIPWIRE_PASS")
'''
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script],
        cwd=ROOT.parent,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "PUBLIC_PREIMPORT_TRIPWIRE_PASS"


def test_capture_freezes_long_term_under_lock_and_uses_owned_revision(monkeypatch):
    api = _api()
    memory_module = importlib.import_module("nana.memory")

    store = {
        "snapshot_revision": 17,
        "long_term": [
            {
                "id": "record-1",
                "text": "Ba uses an RTX 4070",
                "source_event_id": "event-1",
            }
        ],
    }
    lock = threading.Lock()
    monkeypatch.setattr(memory_module, "memory", store)
    monkeypatch.setattr(memory_module, "memory_lock", lock)
    snapshot = context_runtime.capture_private_memory_source(request=_request())
    assert snapshot.revision == "memory-v2-17"
    store["long_term"][0]["text"] = "MUTATED AFTER CAPTURE"
    assert "MUTATED AFTER CAPTURE" not in repr(snapshot.payload)

    class CountingRetriever:
        count = 0

        def __call__(self, captured, request):
            self.count += 1
            assert captured is snapshot
            assert not lock.locked(), "retrieval ran while memory_lock was held"
            return _result(api, (_record(api),), snapshot.revision)

    retriever = CountingRetriever()
    bundle = api.build_memory_context_bundle(
        _request(), snapshot, retriever=retriever, redact=lambda value: value
    )
    assert retriever.count == 1
    assert bundle.retrieval.source_revision == snapshot.revision


def test_content_revision_is_deterministic_when_store_has_no_revision(monkeypatch):
    _api()
    memory_module = importlib.import_module("nana.memory")

    lock = threading.Lock()
    first = {
        "long_term": [
            {"id": "r1", "text": "same", "source_event_id": "e1", "scope": {"b": 2, "a": 1}}
        ]
    }
    second = {
        "long_term": [
            {"scope": {"a": 1, "b": 2}, "source_event_id": "e1", "text": "same", "id": "r1"}
        ]
    }
    monkeypatch.setattr(memory_module, "memory_lock", lock)
    monkeypatch.setattr(memory_module, "memory", first)
    one = context_runtime.capture_private_memory_source(request=_request())
    monkeypatch.setattr(memory_module, "memory", second)
    two = context_runtime.capture_private_memory_source(request=_request())
    assert one.revision == two.revision
    assert one.revision.startswith("memory-content-")


def test_one_result_feeds_prompt_grounding_provenance_and_verifier_context():
    api = _api()
    preference = _record(
        api,
        "preference-1",
        memory_type="preference",
        semantic_role=SemanticRole.INSTRUCTION,
        text="Ba prefers concise replies",
        subject=None,
        subject_role=None,
        conflict_key=None,
        canonical_value=None,
        authority=None,
    )
    fact = _record(api)

    class Retriever:
        count = 0

        def __call__(self, _snapshot, _request):
            self.count += 1
            return _result(api, (preference, fact))

    retriever = Retriever()
    bundle = api.build_memory_context_bundle(
        _request(), _source(()), retriever=retriever, redact=lambda value: value
    )
    assert retriever.count == 1
    assert tuple(section.id for section in bundle.sections) == (
        "preference.rules.v1",
        "memory.retrieval.v1",
        "memory.grounding.v1",
    )
    assert bundle.evidence is not None
    assert bundle.evidence.source_event_ids == ("event-record-1",)
    assert bundle.verifier_context["source_event_ids"] == ("event-record-1",)
    assert {
        provenance.record_id
        for section in bundle.sections
        for provenance in section.provenance
    } == {"preference-1", "record-1"}


def test_grounding_intent_direct_fact_query_always_uses_bundle_verifier_evidence():
    api = _api()
    request = _request("What GPU does Ba use?")
    record = _record(api)
    bundle = api.build_memory_context_bundle(
        request,
        _source(()),
        retriever=lambda *_: _result(api, (record,)),
        redact=lambda value: value,
    )
    assert request.grounding_intent is True
    assert bundle.evidence is not None
    assert bundle.evidence.source_event_ids == (record.source_event_id,)
    assert "memory.grounding.v1" in {section.id for section in bundle.sections}
    assert bundle.verifier_context["evidence_status"] == bundle.evidence.status


def test_bundle_and_retrieval_are_recursively_immutable_and_reject_mutable_payloads():
    api = _api()
    record = _record(api)
    result = _result(api, (record,))
    bundle = api.build_memory_context_bundle(
        _request(), _source(()), retriever=lambda *_: result, redact=lambda value: value
    )
    with pytest.raises(FrozenInstanceError):
        record.text = "changed"
    with pytest.raises(FrozenInstanceError):
        result.records = ()
    with pytest.raises(TypeError):
        result.rejected["late"] = 1
    with pytest.raises(TypeError):
        bundle.verifier_context["late"] = "value"
    with pytest.raises(ContextContractError, match="mutable_bundle_payload"):
        api.MemoryContextBundle(result, None, (), {"mutable": []})


def test_contract_rejects_forced_mutation_in_every_nested_frozen_value():
    api = _api()
    from nana.runtime.context_contracts import freeze_payload

    mutated_record = _record(api)
    object.__setattr__(mutated_record, "visibility", {Lane.PRIVATE_OWNER})
    with pytest.raises(ContextContractError, match="invalid_memory_record"):
        _result(api, (mutated_record,))

    record = _record(api)
    mutated_result = _result(api, (record,))
    object.__setattr__(mutated_result, "records", [record])
    verifier = freeze_payload({"source_revision": mutated_result.source_revision})
    with pytest.raises(ContextContractError, match="invalid_memory_retrieval"):
        api.MemoryContextBundle(mutated_result, None, (), verifier)

    result = _result(api, ())
    mutated_verifier = freeze_payload({"source_revision": result.source_revision})
    object.__setattr__(mutated_verifier, "_items", list(mutated_verifier._items))
    with pytest.raises(ContextContractError, match="mutable_bundle_payload"):
        api.MemoryContextBundle(result, None, (), mutated_verifier)


def test_direct_bundle_constructor_rejects_sections_and_ids_not_derived_from_result():
    api = _api()
    from nana.runtime.context_contracts import freeze_payload

    valid = api.build_memory_context_bundle(
        _request(),
        _source(()),
        retriever=lambda *_: _result(api, (_record(api),)),
        redact=lambda value: value,
    )
    empty = _result(api, ())
    invented_context = freeze_payload({
        "source_revision": empty.source_revision,
        "record_ids": ("invented",),
        "source_event_ids": ("invented-event",),
        "evidence_status": valid.evidence.status,
        "conflicts": (),
    })
    with pytest.raises(ContextContractError, match="invalid_memory_bundle"):
        api.MemoryContextBundle(
            empty,
            valid.evidence,
            valid.sections,
            invented_context,
        )


def test_dataclass_replace_cannot_transfer_bundle_attestation_to_forged_payload():
    api = _api()
    request = _request()
    valid = api.build_memory_context_bundle(
        request,
        _source(()),
        retriever=lambda *_: _result(api, (_record(api),)),
        redact=lambda value: value,
    )
    forged_sections = tuple(
        replace(section, payload="BYPASS NOT IN RETRIEVAL")
        if section.id == "memory.retrieval.v1"
        else section
        for section in valid.sections
    )
    forged = replace(valid, sections=forged_sections)
    assert any(
        section.payload == "BYPASS NOT IN RETRIEVAL"
        for section in forged.sections
    )

    def required(section_id, lifetime, role, owner, payload):
        return ContextSection(
            id=section_id,
            lifetime=lifetime,
            semantic_role=role,
            freshness=Freshness.FRESH,
            visibility=frozenset({Lane.PRIVATE_OWNER}),
            source=SourceRef(owner, "task11.fix2.v1", section_id),
            revision=f"{owner}-fix2-r1",
            authority=None,
            observed_at=NOW,
            expires_at=None,
            conflict_key=None,
            dedupe_key=section_id,
            max_tokens=2_000,
            payload=payload,
            formatter_version="task11.fix2.v1",
            required=True,
            budget_class="mandatory",
            semantic_status="active",
            provenance=(),
            relevance=1.0,
        )

    base = (
        required("core.private.v1", Lifetime.STATIC, SemanticRole.INSTRUCTION, "core", "CORE"),
        required("policy.private.v1", Lifetime.STATIC, SemanticRole.INSTRUCTION, "core", "POLICY"),
        required("contract.output.private.v1", Lifetime.STATIC, SemanticRole.INSTRUCTION, "core", "OUTPUT"),
        required("identity.owner.v1", Lifetime.DURABLE, SemanticRole.IDENTITY, "owner_identity", "OWNER"),
        required("expression.private.v1", Lifetime.TURN, SemanticRole.STATE, "expression", "TONE"),
    )
    with pytest.raises(ContextContractError, match="invalid_memory_bundle_attestation"):
        context_runtime.GptTurnInput(
            request=request,
            sections=(*base, *forged.sections),
            evidence=None,
            model=request.model,
            max_output_tokens=200,
            budget_revision=context_runtime.PRIVATE_POLICY_REVISION,
            memory_bundle=forged,
        )


@pytest.mark.parametrize("mutation", ("records_list", "visibility_set"))
def test_post_issuance_nested_type_mutation_invalidates_attestation(mutation):
    api = _api()
    bundle = api.build_memory_context_bundle(
        _request(),
        _source(()),
        retriever=lambda *_: _result(api, (_record(api),)),
        redact=lambda value: value,
    )
    if mutation == "records_list":
        object.__setattr__(bundle.retrieval, "records", list(bundle.retrieval.records))
    else:
        object.__setattr__(
            bundle.retrieval.records[0],
            "visibility",
            set(bundle.retrieval.records[0].visibility),
        )
    with pytest.raises(ContextContractError, match="invalid_memory_bundle_attestation"):
        api.require_memory_context_bundle(bundle)


@pytest.mark.parametrize(
    "field",
    ("subject", "subject_role", "conflict_key", "canonical_value"),
)
def test_direct_typed_record_rejects_overbound_truth_metadata(field):
    api = _api()
    with pytest.raises(ContextContractError, match="invalid_memory_record"):
        _record(api, **{field: "x" * 701})


@pytest.mark.parametrize(
    ("field", "limit"),
    (
        ("subject", 200),
        ("subject_role", 80),
        ("conflict_key", 200),
        ("canonical_value", 700),
    ),
)
def test_direct_truth_metadata_requires_canonical_trimmed_text(field, limit):
    api = _api()
    canonical = "x" * limit
    record = _record(api, **{field: canonical})
    assert getattr(record, field) == canonical
    for noncanonical in (" " + canonical, canonical + " "):
        with pytest.raises(ContextContractError, match="invalid_memory_record"):
            _record(api, **{field: noncanonical})


def test_default_retrieval_rejections_cannot_reenter_any_bundle_section():
    api = _api()
    accepted = {
        "id": "accepted",
        "type": "preference",
        "text": "Ba prefers concise GPU replies",
        "source": "user",
        "source_event_id": "event-accepted",
        "confidence": 0.95,
        "created_at": NOW - 10,
        "lane": "private_owner",
    }
    rejected = [
        {
            **accepted,
            "id": "low-confidence",
            "source_event_id": "event-low",
            "text": "REJECTED-LOW-CONFIDENCE prefers verbose GPU replies",
            "confidence": 0.1,
        },
        {
            **accepted,
            "id": "secret",
            "source_event_id": "event-secret",
            "text": "token=REJECTED-SECRET GPU style",
        },
    ]
    bundle = api.build_memory_context_bundle(
        _request("What GPU reply style does Ba prefer?"),
        _source([accepted, *rejected]),
        redact=lambda value: value,
    )
    rendered = "\n".join(_render_payload(section.payload) for section in bundle.sections)
    assert "Ba prefers concise GPU replies" in rendered
    assert "REJECTED-LOW-CONFIDENCE" not in rendered
    assert "REJECTED-SECRET" not in rendered
    assert "REJECTED" not in repr(bundle.verifier_context)


@pytest.mark.parametrize(
    ("secret_field", "secret_value"),
    (
        ("subject", "token=TOP-SECRET"),
        ("id", "token=SECRET-RECORD"),
        ("source_event_id", "token=SECRET-EVENT"),
    ),
)
def test_secret_bearing_conflict_metadata_fails_default_admission(
    secret_field, secret_value
):
    api = _api()
    record = {
        "id": "secret-metadata",
        "type": "project_fact",
        "text": "Ba GPU typed fact",
        "source": "user",
        "source_event_id": "event-secret-metadata",
        "confidence": 0.95,
        "created_at": NOW - 10,
        "lane": "private_owner",
        "semantic_role": "fact",
        "subject": "Ba",
        "subject_role": "owner",
        "conflict_key": "hardware.gpu.current",
        "canonical_value": "RTX 4070",
        "authority": "verified_current_state",
        "semantic_status": "active",
        secret_field: secret_value,
    }
    bundle = api.build_memory_context_bundle(
        _request("What GPU typed fact does Ba use?"),
        _source([record]),
        redact=lambda value: value,
    )
    assert bundle.retrieval.records == ()
    assert bundle.retrieval.rejected["secret"] == 1
    assert "SECRET" not in repr(bundle.sections)
    assert "SECRET" not in repr(bundle.verifier_context)


def test_every_model_facing_memory_string_is_sanitized_exactly_once():
    api = _api()
    selected = _record(
        api,
        "selected",
        text="RAW-TEXT",
        memory_type="project_fact",
        semantic_status="RAW-STATUS",
        subject=None,
        subject_role=None,
        conflict_key=None,
        canonical_value=None,
        authority=None,
    )
    preference = _record(
        api,
        "preference",
        text="safe preference",
        memory_type="RAW-TYPE",
        semantic_role=SemanticRole.INSTRUCTION,
        subject=None,
        subject_role=None,
        conflict_key=None,
        canonical_value=None,
        authority=None,
    )
    first = _record(
        api,
        "conflict-a",
        text="safe conflict a",
        subject="RAW-SUBJECT",
        subject_role="RAW-ROLE",
        conflict_key="RAW-KEY",
        canonical_value="A",
    )
    second = _record(
        api,
        "conflict-b",
        text="safe conflict b",
        subject="RAW-SUBJECT",
        subject_role="RAW-ROLE",
        conflict_key="raw-key",
        canonical_value="B",
    )
    calls = []

    def redact(value):
        calls.append(value)
        if value == "raw-key":
            return "SAFE-KEY"
        return value.replace("RAW-", "SAFE-")

    bundle = api.build_memory_context_bundle(
        _request("What typed value is current?"),
        _source(()),
        retriever=lambda *_: _result(api, (selected, preference, first, second)),
        redact=redact,
    )
    rendered = "\n".join(_render_payload(section.payload) for section in bundle.sections)
    for raw in ("RAW-TEXT", "RAW-TYPE", "RAW-STATUS", "RAW-SUBJECT", "RAW-ROLE", "raw-key"):
        assert raw not in rendered
        assert raw not in repr(bundle.verifier_context)
    for safe in ("SAFE-TEXT", "SAFE-TYPE", "SAFE-STATUS", "SAFE-SUBJECT", "SAFE-ROLE", "SAFE-KEY"):
        assert safe in rendered
    assert calls.count("RAW-TEXT") == 1
    assert calls.count("RAW-TYPE") == 1
    assert calls.count("RAW-STATUS") == 1
    assert calls.count("RAW-SUBJECT") == 1
    assert calls.count("RAW-ROLE") == 1
    assert calls.count("raw-key") == 1


def test_view_caps_three_complete_records_and_1200_rendered_characters():
    api = _api()
    records = tuple(
        _record(
            api,
            f"record-{index}",
            text=f"complete-{index}-" + (str(index) * 330),
            subject=f"subject-{index}",
            conflict_key=f"fact.{index}",
            canonical_value=f"value-{index}",
        )
        for index in range(4)
    )
    bundle = api.build_memory_context_bundle(
        _request("Nana co nho complete khong?"),
        _source(()),
        retriever=lambda *_: _result(api, records),
        redact=lambda value: value,
    )
    memory_sections = bundle.sections
    payload = _thaw(next(section.payload for section in memory_sections if section.id == "memory.retrieval.v1"))
    selected_texts = [item["text"] for item in payload["records"]]
    assert len(selected_texts) <= 3
    assert all(text in {record.text for record in records} for text in selected_texts)
    assert sum(len(_render_payload(section.payload)) for section in memory_sections) <= 1200


def test_bundle_wide_1200_bound_drops_whole_record_and_does_not_duplicate_snippet():
    api = _api()
    text = "COMPLETE-LONG-RECORD-" + ("x" * 760)
    record = _record(api, text=text)
    bundle = api.build_memory_context_bundle(
        _request("What GPU does Ba use?"),
        _source(()),
        retriever=lambda *_: _result(api, (record,)),
        redact=lambda value: value,
    )
    rendered = "\n".join(_render_payload(section.payload) for section in bundle.sections)
    assert sum(len(_render_payload(section.payload)) for section in bundle.sections) <= 1200
    assert rendered.count(text) in {0, 1}
    assert all(
        text == item.text
        for item in bundle.retrieval.records
        if item.record_id == record.record_id
    )


def test_uncertainty_only_projection_sheds_complete_outcomes_under_1200_chars():
    api = _api()
    records = []
    for index in range(3):
        common = {
            "subject": f"subject-{index}-" + ("s" * 175),
            "subject_role": "role-" + ("r" * 65),
            "conflict_key": f"domain-{index}-" + ("k" * 175),
            "authority": "verified_current_state",
        }
        records.extend((
            _record(api, f"domain-{index}-a", canonical_value="A", **common),
            _record(api, f"domain-{index}-b", canonical_value="B", **common),
        ))
    bundle = api.build_memory_context_bundle(
        _request("Which typed state is current?"),
        _source(()),
        retriever=lambda *_: _result(api, tuple(records)),
        redact=lambda value: value,
    )
    rendered_sections = tuple(_render_payload(section.payload) for section in bundle.sections)
    assert sum(map(len, rendered_sections)) <= 1200
    retrieval = next(
        (section for section in bundle.sections if section.id == "memory.retrieval.v1"),
        None,
    )
    if retrieval is not None:
        payload = _thaw(retrieval.payload)
        assert len(payload.get("uncertainties", ())) < 3
        assert all(item["status"] == "conflict_unresolved" for item in payload["uncertainties"])
    assert "canonical_value" not in "\n".join(rendered_sections)


def test_grounding_section_references_retrieval_without_repeating_or_cutting_text():
    api = _api()
    text = "COMPLETE-GROUNDING-RECORD-" + ("z" * 180)
    record = _record(api, text=text)
    bundle = api.build_memory_context_bundle(
        _request("What GPU does Ba use?"),
        _source(()),
        retriever=lambda *_: _result(api, (record,)),
        redact=lambda value: value,
    )
    rendered = "\n".join(_render_payload(section.payload) for section in bundle.sections)
    assert rendered.count(text) == 1
    assert bundle.evidence is not None
    assert bundle.evidence.snippets == (text,)
    grounding = next(
        _render_payload(section.payload)
        for section in bundle.sections
        if section.id == "memory.grounding.v1"
    )
    assert "snippets:" not in grounding
    assert "source_event_ids:" not in grounding


def test_stable_id_duplicates_dedupe_but_conflicting_duplicates_fail_closed():
    api = _api()
    original = _record(api)
    result = _result(api, (original, original))
    assert result.records == (original,)
    conflicting = _record(api, text="Ba uses a different GPU", canonical_value="RTX 4090")
    with pytest.raises(ContextContractError, match="conflicting_memory_record_id"):
        _result(api, (original, conflicting))


def test_default_retriever_omits_conflicting_raw_stable_id_duplicates():
    api = _api()
    common = {
        "id": "duplicate-id",
        "type": "project_fact",
        "source": "user",
        "source_event_id": "duplicate-event",
        "confidence": 0.95,
        "created_at": NOW - 10,
        "lane": "private_owner",
        "semantic_role": "fact",
        "subject": "Ba",
        "subject_role": "owner",
        "conflict_key": "hardware.gpu.current",
        "authority": "verified_current_state",
        "semantic_status": "active",
    }
    bundle = api.build_memory_context_bundle(
        _request("Nana co nho GPU cua Ba khong?"),
        _source([
            {**common, "text": "Ba GPU uses RTX 4070", "canonical_value": "RTX 4070"},
            {**common, "text": "Ba GPU uses RTX 4090", "canonical_value": "RTX 4090"},
        ]),
        redact=lambda value: value,
    )
    rendered = "\n".join(_render_payload(section.payload) for section in bundle.sections)
    assert "RTX 4070" not in rendered
    assert "RTX 4090" not in rendered
    assert bundle.retrieval.rejected["conflicting_duplicate"] == 2


def test_shared_retrieval_default_keeps_legacy_truncation_and_duplicate_semantics():
    from nana.runtime.controlled_memory_retrieval import (
        RetrievalScope,
        retrieve_memory_candidates,
    )

    common = {
        "id": "legacy-id",
        "type": "project_fact",
        "source": "user",
        "confidence": 0.95,
        "created_at": NOW - 10,
        "lane": "private_owner",
    }
    long_text = "coffee preference " + ("x" * 90)
    truncated = retrieve_memory_candidates(
        {"long_term": [{**common, "text": long_text, "source_event_id": "legacy-event"}]},
        "coffee",
        scope=RetrievalScope("private_owner"),
        max_chars=20,
        semantic_enabled=False,
        now=NOW,
    )
    assert len(truncated.candidates) == 1
    assert truncated.candidates[0].text == long_text[:20]

    duplicate = retrieve_memory_candidates(
        {
            "long_term": [
                {**common, "text": "coffee first", "source_event_id": "event-first"},
                {**common, "text": "coffee second", "source_event_id": "event-second"},
            ]
        },
        "coffee",
        scope=RetrievalScope("private_owner"),
        semantic_enabled=False,
        now=NOW,
    )
    assert len(duplicate.candidates) == 1
    assert duplicate.candidates[0].text == "coffee second"
    assert duplicate.candidates[0].source_event_id == "event-second"
    assert "conflicting_duplicate" not in duplicate.rejected

    optional_metadata = {
        **common,
        "text": "coffee optional metadata",
        "source_event_id": "event-optional",
        "canonical_value": "v" * 701,
    }
    legacy_optional = retrieve_memory_candidates(
        {"long_term": [optional_metadata]},
        "coffee",
        scope=RetrievalScope("private_owner"),
        semantic_enabled=False,
        now=NOW,
    )
    strict_optional = retrieve_memory_candidates(
        {"long_term": [optional_metadata]},
        "coffee",
        scope=RetrievalScope("private_owner"),
        semantic_enabled=False,
        canonical_strict=True,
        now=NOW,
    )
    assert len(legacy_optional.candidates) == 1
    assert strict_optional.candidates == ()
    assert strict_optional.rejected["truth_metadata"] == 1


def test_truth_metadata_is_never_truncated_into_false_duplicate_values():
    api = _api()
    prefix = "v" * 700
    common = {
        "type": "project_fact",
        "source": "user",
        "confidence": 0.95,
        "created_at": NOW - 10,
        "lane": "private_owner",
        "semantic_role": "fact",
        "subject": "Ba",
        "subject_role": "owner",
        "conflict_key": "hardware.gpu.current",
        "authority": "verified_current_state",
        "semantic_status": "active",
    }
    source = _source([
        {
            **common,
            "id": "long-a",
            "source_event_id": "long-event-a",
            "text": "Ba GPU fact A",
            "canonical_value": prefix + "A",
        },
        {
            **common,
            "id": "long-b",
            "source_event_id": "long-event-b",
            "text": "Ba GPU fact B",
            "canonical_value": prefix + "B",
        },
    ])
    bundle = api.build_memory_context_bundle(
        _request("What GPU fact does Ba use?"), source, redact=lambda value: value
    )
    assert bundle.retrieval.records == ()
    assert bundle.retrieval.rejected["truth_metadata"] == 2


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("subject", "s" * 201),
        ("subject_role", "r" * 81),
        ("conflict_key", "k" * 201),
        ("canonical_value", "v" * 701),
    ),
)
def test_overbound_truth_metadata_is_rejected_losslessly(field, value):
    api = _api()
    record = {
        "id": f"overbound-{field}",
        "type": "project_fact",
        "text": "Ba GPU typed fact",
        "source": "user",
        "source_event_id": f"event-{field}",
        "confidence": 0.95,
        "created_at": NOW - 10,
        "lane": "private_owner",
        "semantic_role": "fact",
        "subject": "Ba",
        "subject_role": "owner",
        "conflict_key": "hardware.gpu.current",
        "canonical_value": "RTX 4070",
        "authority": "verified_current_state",
        "semantic_status": "active",
        field: value,
    }
    bundle = api.build_memory_context_bundle(
        _request("What GPU typed fact does Ba use?"),
        _source([record]),
        redact=lambda item: item,
    )
    assert bundle.retrieval.records == ()
    assert bundle.retrieval.rejected["truth_metadata"] == 1


def test_unresolved_domain_forces_bounded_uncertainty_in_prompt_and_verifier():
    api = _api()
    first = _record(
        api,
        "first",
        text="Ba uses RTX 4070",
        canonical_value="RTX 4070",
        authority="verified_current_state",
    )
    second = _record(
        api,
        "second",
        text="Ba uses RTX 4090",
        canonical_value="RTX 4090",
        authority="verified_current_state",
    )
    request = _request("Which GPU is current?")
    bundle = api.build_memory_context_bundle(
        request,
        _source(()),
        retriever=lambda *_: _result(api, (second, first)),
        redact=lambda value: value,
    )
    rendered = "\n".join(_render_payload(section.payload) for section in bundle.sections)
    assert "RTX 4070" not in rendered
    assert "RTX 4090" not in rendered
    assert "conflict_unresolved" in rendered
    assert bundle.evidence is not None
    assert bundle.evidence.status == "partial"
    from nana.runtime.memory_grounding import verify_memory_context_bundle
    verification = verify_memory_context_bundle(
        bundle,
        "Ba uses RTX 4070.",
        user_text=request.current_input,
    )
    assert verification.passed is False
    assert verification.fail_reason == "conflict_unresolved"
    assert verification.suggested_fallback


def test_bundle_rejects_visibility_revision_and_section_identity_mismatches():
    api = _api()
    hidden = _record(api, visibility=frozenset({Lane.PUBLIC_STAGE}))
    with pytest.raises(ContextContractError, match="memory_visibility_mismatch"):
        api.build_memory_context_bundle(
            _request(), _source(()), retriever=lambda *_: _result(api, (hidden,))
        )
    with pytest.raises(ContextContractError, match="memory_source_revision_mismatch"):
        api.build_memory_context_bundle(
            _request(), _source((), revision="memory-v2-7"),
            retriever=lambda *_: _result(api, (), revision="memory-v2-8"),
        )

    valid = api.build_memory_context_bundle(
        _request(), _source(()), retriever=lambda *_: _result(api, (_record(api),))
    )
    with pytest.raises(ContextContractError, match="duplicate_section_id"):
        api.MemoryContextBundle(
            valid.retrieval,
            valid.evidence,
            (valid.sections[0], valid.sections[0]),
            valid.verifier_context,
        )
    with pytest.raises(ContextContractError, match="memory_bundle_evidence_mismatch"):
        api.MemoryContextBundle(
            valid.retrieval,
            valid.evidence,
            tuple(section for section in valid.sections if section.id != "memory.grounding.v1"),
            valid.verifier_context,
        )
    from nana.runtime.context_contracts import freeze_payload
    with pytest.raises(ContextContractError, match="memory_source_revision_mismatch"):
        api.MemoryContextBundle(
            valid.retrieval,
            valid.evidence,
            valid.sections,
            freeze_payload({"source_revision": "memory-v2-other"}),
        )


def _canonical_snapshot(contracts, runtime, adapters, request):
    def source(name, payload, *, observed=None, fresh=False):
        return contracts.SourceSnapshot(
            source=name,
            revision=f"{name}-fixture-r1",
            observed_at=observed,
            captured_at=request.captured_wall_time,
            freshness=(contracts.Freshness.FRESH if fresh else contracts.Freshness.UNKNOWN),
            payload=payload,
        )

    runtime_source = source(
        "runtime_context",
        {"active_app": "editor", "active_zone": "focus", "idle_state": "active", "in_flow": True, "time": {}},
    )
    browser_source = source("browser_state", {"available": False})
    awareness_source = source(
        "live_awareness",
        {"browser_available": False, "focus_source": "none", "focus_text": "", "focus_confidence": 0.0},
    )
    awareness_memory = source("awareness_memory", {"enabled": True, "moments": []})
    expression = source(
        "expression_private",
        {
            "mood": {"tone": "steady", "energy": 0.5, "focus": 0.7, "tension": 0.1},
            "affect": {"warmth": 0.6, "playfulness": 0.4, "assertiveness": 0.3, "intimacy": 0.8},
            "persona": {"mode": "focus", "clamp": "strict"},
        },
        observed=request.captured_wall_time,
        fresh=True,
    )
    checkpoint = source(
        "private_checkpoint",
        {"available": False, "session_id": "", "summary": "", "anchors": [], "pending_turns": []},
    )
    private_memory = source(
        "private_memory",
        {"long_term": []},
        observed=request.captured_wall_time,
        fresh=True,
    )
    sources = (
        runtime_source,
        browser_source,
        awareness_source,
        awareness_memory,
        expression,
        checkpoint,
        private_memory,
    )
    return runtime.RuntimeContextSnapshot(
        request=request,
        snapshot_revision=1,
        captured_at=request.captured_wall_time,
        captured_monotonic_at=request.captured_monotonic_time,
        runtime_context=runtime_source,
        browser_state=browser_source,
        live_awareness=awareness_source,
        awareness_memory=awareness_memory,
        source_revisions=tuple(
            adapters.SourceRevision(item.source, item.revision, "content_hash")
            for item in sorted(sources, key=lambda item: item.source)
        ),
        expression=expression,
        private_checkpoint=checkpoint,
        private_memory=private_memory,
    )


def test_canonical_sync_stream_reuse_each_exact_bundle_and_never_call_raw_bypasses(
    monkeypatch, _isolated_nana_modules
):
    smoke = ROOT / "tests" / "smoke"
    if str(smoke) not in sys.path:
        sys.path.insert(0, str(smoke))
    from smoke_context_runtime_integration import _install_private_dependencies, _run_stream

    gpt, captures, _reads = _install_private_dependencies(private_mode="legacy")
    sys.modules.pop("nana.runtime.memory_grounding", None)
    grounding = importlib.import_module("nana.runtime.memory_grounding")
    contracts = importlib.import_module("nana.runtime.context_contracts")
    runtime = importlib.import_module("nana.runtime.context_runtime")
    adapters = importlib.import_module("nana.runtime.context_adapters")
    config = sys.modules["nana.config"]
    config.NANA_CONTEXT_PRIVATE_MODE = "canonical"
    config.NANA_CONTEXT_BUDGET_POLICY_REVISION = runtime.PRIVATE_POLICY_REVISION
    monkeypatch.setattr(runtime, "require_canonical_dispatch_ready", lambda *_a, **_k: None)

    def forbidden(name):
        def fail(*_args, **_kwargs):
            raise AssertionError(f"canonical memory bypass called: {name}")
        return fail

    gpt._private_memory_evidence_for_turn = forbidden("parallel evidence")
    gpt._private_memory_retrieval_block = forbidden("raw retrieval block")
    gpt.format_prompt_memory_rules = forbidden("raw durable rules")
    gpt._get_natural_style_hint = forbidden("raw style scan")
    gpt._canonical_current_snapshot = (
        lambda _context, _awareness, request: _canonical_snapshot(
            contracts, runtime, adapters, request
        )
    )

    retrievals = []

    def retrieve(snapshot, _request):
        retrievals.append(snapshot)
        record = adapters.MemoryRetrievalRecord(
            record_id="gpu-record",
            source_event_id="gpu-event",
            source="user",
            memory_type="project_fact",
            semantic_role=contracts.SemanticRole.FACT,
            text="Ba uses an RTX 4070",
            visibility=frozenset({contracts.Lane.PRIVATE_OWNER}),
            confidence=0.95,
            relevance=0.95,
            observed_at=_request.captured_wall_time - 1,
            expires_at=None,
            subject="Ba",
            subject_role="owner",
            conflict_key="hardware.gpu.current",
            canonical_value="RTX 4070",
            authority="verified_current_state",
            semantic_status="active",
            temporal_selected=False,
        )
        return adapters.MemoryRetrievalResult(
            snapshot.revision, "ok", (record,), False, False, {}, "gpu-query"
        )

    monkeypatch.setattr(adapters, "retrieve_context_memory", retrieve)
    redacted_record_texts = []
    original_redact = gpt.redact_history_text

    def redact_once(value):
        if value == "Ba uses an RTX 4070":
            redacted_record_texts.append(value)
        return original_redact(value)

    gpt.redact_history_text = redact_once
    plans = []
    real_build = runtime.build_turn_plan

    def observe(inputs, **kwargs):
        plan = real_build(inputs, **kwargs)
        plans.append(plan)
        return plan

    monkeypatch.setattr(runtime, "build_turn_plan", observe)
    verified = []
    real_verify = grounding.verify_memory_context_bundle

    def verify(bundle, reply, *, user_text=""):
        verified.append(bundle)
        return real_verify(bundle, reply, user_text=user_text)

    monkeypatch.setattr(grounding, "verify_memory_context_bundle", verify)
    query = "Nana co nho GPU cua Ba khong?"
    assert gpt.ask_gpt(query)
    assert _run_stream(gpt, query)
    assert len(retrievals) == len(plans) == len(verified) == 2
    assert len(redacted_record_texts) == 2
    assert all(verified[index] is plans[index].memory_bundle for index in range(2))
    assert captures["sync"][0] == captures["stream"][0]
    assert plans[0].compiled.full_context_hash == plans[1].compiled.full_context_hash
    assert all(
        plan.compiled.messages[0].content.count("Ba uses an RTX 4070") >= 1
        for plan in plans
    )


def test_legacy_rollback_never_builds_task11_bundle_and_keeps_raw_paths(
    monkeypatch, _isolated_nana_modules
):
    smoke = ROOT / "tests" / "smoke"
    if str(smoke) not in sys.path:
        sys.path.insert(0, str(smoke))
    from smoke_context_runtime_integration import _install_private_dependencies, _run_stream

    gpt, _captures, _reads = _install_private_dependencies(private_mode="legacy")
    adapters = importlib.import_module("nana.runtime.context_adapters")
    monkeypatch.setattr(
        adapters,
        "build_memory_context_bundle",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("legacy built Task 11 bundle")),
    )
    calls = {"retrieval": 0, "rules": 0, "style": 0}
    gpt._private_memory_evidence_for_turn = lambda *_a, **_k: None
    gpt._private_memory_retrieval_block = lambda _text: calls.__setitem__("retrieval", calls["retrieval"] + 1) or "LEGACY RETRIEVAL"
    gpt.format_prompt_memory_rules = lambda *_a, **_k: calls.__setitem__("rules", calls["rules"] + 1) or "LEGACY RULES"
    gpt._get_natural_style_hint = lambda: calls.__setitem__("style", calls["style"] + 1) or "LEGACY STYLE"
    assert gpt.ask_gpt("A full legacy question", story_mode=True)
    assert _run_stream(gpt, "A full legacy question", story_mode=True)
    assert calls == {"retrieval": 2, "rules": 2, "style": 2}
