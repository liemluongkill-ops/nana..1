"""Sanitized offline capture of Nana's current legacy prompt builders."""

from __future__ import annotations

import asyncio
import builtins
import _io
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import importlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import types
from unittest import mock


NANA_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = NANA_ROOT / "tests" / "fixtures" / "context_runtime"
PROFILES = {
    "private_owner": ("private_owner", "interactive"),
    "public_stage": ("public_stage", "interactive"),
    "operator": ("operator", "bridge"),
    "autonomy_private": ("private_owner", "autonomy"),
    "autonomy_public": ("public_stage", "autonomy"),
}
FIXTURE_KEYS = {
    "fixture_version",
    "lane",
    "route",
    "model",
    "current_input",
    "required_markers",
    "forbidden_markers",
    "wire_roles",
}
SEMANTIC_MARKERS = {
    "Nana",
    "private",
    "Public",
    "public",
    "AI VTuber",
    "bridge_system",
    "PRIVATE-LEAK-SENTINEL",
}
SYNTHETIC_TEXT = re.compile(r"CONTEXT-FIXTURE-[A-Z0-9]+(?:-[A-Z0-9]+)*\Z")
CREDENTIAL = re.compile(
    r"\bsk-[A-Za-z0-9_-]{8,}|\bAKIA[A-Z0-9]{16}\b|"
    r"\bAIza[A-Za-z0-9_-]{20,}|\b(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,}|"
    r"\bxox[baprs]-[A-Za-z0-9-]{10,}|\bbearer\s+[A-Za-z0-9._-]{8,}|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"\b(?:api[ _-]*key|access[ _-]*token|password|client[ _-]*secret)\s*[:=]\s*\S+",
    re.IGNORECASE,
)
REAL_PROVIDER_CALLS = 0


class FixtureError(ValueError):
    pass


class SideEffectViolation(AssertionError):
    pass


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _strings(key)
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


def validate_fixture(value, profile):
    if any(CREDENTIAL.search(text) for text in _strings(value)):
        raise FixtureError("credential-like material is forbidden")
    if not isinstance(value, dict):
        raise FixtureError("fixture must be a JSON object")
    if set(value) - FIXTURE_KEYS:
        raise FixtureError("unknown fixture keys are forbidden")
    if FIXTURE_KEYS - set(value):
        raise FixtureError("missing required fixture keys")
    if len(value) != 8:
        raise FixtureError("fixture must contain exactly 8 keys")
    if type(value["fixture_version"]) is not int or value["fixture_version"] != 1:
        raise FixtureError("fixture_version must be integer 1")
    if (value["lane"], value["route"]) != PROFILES[profile]:
        raise FixtureError("lane/route does not match the fixture profile")
    if value["model"] != "fixture-model":
        raise FixtureError("model must be fixture-model")
    if not isinstance(value["current_input"], str) or not SYNTHETIC_TEXT.fullmatch(
        value["current_input"]
    ):
        raise FixtureError("current_input must contain synthetic source content only")
    if value["wire_roles"] != ["system", "user"]:
        raise FixtureError("wire_roles must be exactly [system, user]")
    for name in ("required_markers", "forbidden_markers"):
        markers = value[name]
        if (
            not isinstance(markers, list)
            or not markers
            or len(set(map(str, markers))) != len(markers)
        ):
            raise FixtureError(f"{name} must be a nonempty unique string list")
        if any(
            not isinstance(marker, str)
            or not (marker in SEMANTIC_MARKERS or SYNTHETIC_TEXT.fullmatch(marker))
            for marker in markers
        ):
            raise FixtureError(f"{name} contains non-synthetic source content")
    if set(value["required_markers"]) & set(value["forbidden_markers"]):
        raise FixtureError("required and forbidden markers must be disjoint")
    return value


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise FixtureError("duplicate JSON keys are forbidden")
        result[key] = value
    return result


def load_fixtures():
    fixtures = {}
    for profile in PROFILES:
        path = FIXTURE_ROOT / f"{profile}.json"
        if not path.is_file():
            raise FixtureError(f"missing fixture {path.name}")
        try:
            value = json.loads(
                path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object
            )
        except FixtureError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise FixtureError(f"invalid fixture {path.name}: {type(exc).__name__}") from None
        fixtures[profile] = validate_fixture(value, profile)
    return fixtures


@dataclass(frozen=True)
class CapturedCall:
    label: str
    model: str
    messages: tuple[tuple[str, str], ...]
    sha256: str

    @property
    def roles(self):
        return [role for role, _content in self.messages]

    @property
    def text(self):
        return "\n".join(content for _role, content in self.messages)


class FakeTransport:
    def __init__(self):
        self.calls = []
        self.envelopes = []

    @staticmethod
    def _freeze(label, model, messages):
        frozen = tuple(
            (str(message.get("role", "")), str(message.get("content", "")))
            for message in deepcopy(messages)
        )
        encoded = json.dumps(
            frozen, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        return CapturedCall(
            label=str(label),
            model=str(model or ""),
            messages=frozen,
            sha256=hashlib.sha256(encoded).hexdigest().upper(),
        )

    def capture(self, label, model, messages):
        call = self._freeze(label, model, messages)
        self.calls.append(call)
        return "Nana synthetic fixture response"

    def capture_envelope(self, label, model, messages):
        call = self._freeze(label, model, messages)
        self.envelopes.append(call)
        return call

    @property
    def captures(self):
        return [*self.calls, *self.envelopes]

    def one(self, label):
        matches = [call for call in self.captures if call.label == label]
        assert len(matches) == 1, (label, [call.label for call in self.captures])
        return matches[0]


class SideEffectAudit:
    def __init__(self):
        self.blocked = []
        self.expected_probe_count = 0

    def deny(self, kind, target):
        self.blocked.append((str(kind), str(target)))
        raise SideEffectViolation(f"blocked {kind}: {target}")


def _normalized_path(value):
    if isinstance(value, int):
        return None
    try:
        raw = os.fspath(value)
    except TypeError:
        return None
    if isinstance(raw, bytes):
        raw = os.fsdecode(raw)
    return os.path.normcase(os.path.abspath(raw))


def _protected_path_kind(value):
    normalized = _normalized_path(value)
    if normalized is None:
        return None
    data_root = os.path.normcase(str(NANA_ROOT / "data"))
    logs_root = os.path.normcase(str(NANA_ROOT / "runtime_logs"))
    try:
        if os.path.commonpath((normalized, data_root)) == data_root:
            return "production_data"
        if os.path.commonpath((normalized, logs_root)) == logs_root:
            return "runtime_logs"
    except ValueError:
        pass
    if os.path.basename(normalized).lower() == ".env":
        return "credential_file"
    return None


@contextmanager
def _side_effect_guards():
    audit = SideEffectAudit()
    real_builtin_open = builtins.open
    real_io_open = io.open
    real_io_open_code = io.open_code
    real_import_open_code = _io.open_code
    real_os_open = os.open

    def guarded_builtin_open(file, *args, **kwargs):
        kind = _protected_path_kind(file)
        if kind:
            audit.deny(kind, file)
        return real_builtin_open(file, *args, **kwargs)

    def guarded_io_open(file, *args, **kwargs):
        kind = _protected_path_kind(file)
        if kind:
            audit.deny(kind, file)
        return real_io_open(file, *args, **kwargs)

    def guarded_os_open(file, *args, **kwargs):
        kind = _protected_path_kind(file)
        if kind:
            audit.deny(kind, file)
        return real_os_open(file, *args, **kwargs)

    def guarded_io_open_code(file, *args, **kwargs):
        kind = _protected_path_kind(file)
        if kind:
            audit.deny(kind, file)
        return real_io_open_code(file, *args, **kwargs)

    def guarded_import_open_code(file, *args, **kwargs):
        kind = _protected_path_kind(file)
        if kind:
            audit.deny(kind, file)
        return real_import_open_code(file, *args, **kwargs)

    def deny_socket_connect(_sock, address):
        audit.deny("socket.connect", address)

    def deny_socket_connect_ex(_sock, address):
        audit.deny("socket.connect_ex", address)

    def deny_create_connection(address, *args, **kwargs):
        audit.deny("socket.create_connection", address)

    def deny_subprocess(*args, **kwargs):
        target = args[0] if args else kwargs.get("args", "unknown")
        audit.deny("subprocess", target)

    def deny_os_system(command):
        audit.deny("os.system", command)

    def deny_os_popen(command, *args, **kwargs):
        audit.deny("os.popen", command)

    with ExitStack() as stack:
        stack.enter_context(mock.patch("builtins.open", guarded_builtin_open))
        stack.enter_context(mock.patch("io.open", guarded_io_open))
        stack.enter_context(mock.patch("io.open_code", guarded_io_open_code))
        stack.enter_context(mock.patch("_io.open_code", guarded_import_open_code))
        stack.enter_context(mock.patch("os.open", guarded_os_open))
        stack.enter_context(mock.patch.object(socket.socket, "connect", deny_socket_connect))
        stack.enter_context(mock.patch.object(socket.socket, "connect_ex", deny_socket_connect_ex))
        stack.enter_context(mock.patch("socket.create_connection", deny_create_connection))
        for name in ("Popen", "run", "call", "check_call", "check_output"):
            stack.enter_context(mock.patch.object(subprocess, name, deny_subprocess))
        stack.enter_context(mock.patch("os.system", deny_os_system))
        stack.enter_context(mock.patch("os.popen", deny_os_popen))
        yield audit


def _expect_blocked(action):
    try:
        action()
    except SideEffectViolation:
        return
    raise AssertionError("side-effect guard did not fail closed")


def _probe_direct_socket():
    with socket.socket() as candidate:
        candidate.connect(("127.0.0.1", 9))


def _probe_side_effect_guards(audit):
    probes = [
        lambda: builtins.open(NANA_ROOT / "data" / "memory.json", "rb"),
        lambda: builtins.open(NANA_ROOT / ".env", "rb"),
        lambda: builtins.open(NANA_ROOT / "runtime_logs" / "fixture.log", "wb"),
        lambda: io.open_code(
            str(NANA_ROOT / "data" / "CONTEXT-FIXTURE-MISSING-NO-READ.py")
        ),
        lambda: _io.open_code(
            str(NANA_ROOT / "data" / "CONTEXT-FIXTURE-MISSING-IMPORT-NO-READ.py")
        ),
        _probe_direct_socket,
        lambda: socket.create_connection(("127.0.0.1", 9)),
        lambda: subprocess.run(["fixture-command"], check=False),
        lambda: os.system("fixture-command"),
    ]
    for probe in probes:
        _expect_blocked(probe)
    audit.expected_probe_count = len(probes)
    assert len(audit.blocked) == audit.expected_probe_count


def _load_source_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _model_from_call(args, kwargs):
    if args:
        return args[0]
    return kwargs.get("model_name") or kwargs.get("preferred_model")


def _messages_from_call(args, kwargs):
    if len(args) >= 2:
        return args[1]
    return kwargs["messages"]


def _run_immediate(coroutine):
    """Drive a fake-only coroutine that must not suspend on external work."""
    try:
        coroutine.send(None)
    except StopIteration as completed:
        return completed.value
    coroutine.close()
    raise AssertionError("offline stream capture attempted asynchronous external work")


def _install_immediate_stream_bridge():
    module = types.ModuleType("nana.runtime.async_stream_bridge")

    async def iterate_blocking(source):
        for item in source:
            yield item

    module.iterate_blocking = iterate_blocking
    sys.modules[module.__name__] = module


def _capture_private(fixtures, transport, observations):
    helper = _load_source_module(
        "context_runtime_checkpoint_helper",
        NANA_ROOT / "tests" / "smoke" / "smoke_session_checkpoint_prompt.py",
    )
    gpt, _unused = helper.fixture._install_isolated_dependencies()
    _install_immediate_stream_bridge()
    boundary = helper._configure_private(gpt)
    fixture = fixtures["private_owner"]
    retrieval = "CONTEXT-FIXTURE-PRIVATE-RETRIEVAL-001"
    checkpoint = "CONTEXT-FIXTURE-PRIVATE-CHECKPOINT-001"
    history = "CONTEXT-FIXTURE-PRIVATE-HISTORY-001"

    boundary.prompt_block = (
        "Persona Boundary: interaction_scope=private_owner\n"
        "Nana private fixture boundary."
    )
    gpt.NANA_PERSONALITY = "Nana private synthetic baseline."
    gpt.NANA_SHARED_HISTORY = ""
    gpt.LLMGATE_MAIN_MODEL = fixture["model"]
    gpt.LLMGATE_CHEAP_MODEL = fixture["model"]
    gpt._private_memory_retrieval_block = lambda _text: retrieval
    gpt._private_session_checkpoint_block = lambda _boundary: checkpoint
    gpt.load_recent_chat = lambda *_args, **_kwargs: history
    gpt.memory = {
        "emotion": {"affection": 0.5, "annoyance": 0.0, "playfulness": 0.5},
        "short_term": ["CONTEXT-FIXTURE-PRIVATE-SHORT-001"],
        "long_term": [],
    }
    sys.modules["nana.memory"].memory = gpt.memory
    sync_label = {"value": "private_sync"}

    def sync_provider(**kwargs):
        text = transport.capture(
            sync_label["value"], kwargs.get("preferred_model"), kwargs["messages"]
        )
        return helper.fixture._Response(text)

    def stream_provider(*args, **kwargs):
        yield transport.capture(
            "private_stream",
            _model_from_call(args, kwargs),
            _messages_from_call(args, kwargs),
        )

    gpt.create_chat_completion_with_fallback = sync_provider
    sys.modules["nana.brain.llmgate_client"].stream_llmgate_messages = stream_provider
    sync_reply = gpt.ask_gpt(fixture["current_input"])

    async def consume():
        return "".join(
            [part async for part in gpt.ask_gpt_stream(fixture["current_input"])]
        )

    stream_reply = _run_immediate(consume())
    assert sync_reply and stream_reply

    grounding = _load_source_module(
        "context_runtime_real_memory_grounding",
        NANA_ROOT / "runtime" / "memory_grounding.py",
    )
    evidence_marker = "CONTEXT-FIXTURE-BLOCKER-C-EVIDENCE-001"
    evidence = grounding.MemoryEvidence(
        status="found",
        confidence=0.9,
        lane_visible_to="private_only",
        evidence_strength="strong",
        snippets=[evidence_marker],
    )

    class ObservedConfidenceInjector:
        @staticmethod
        def inject_into_messages(messages, current_evidence):
            observations["blocker_c_pre_inject"] = deepcopy(messages)
            try:
                result = grounding.ConfidenceInjector.inject_into_messages(
                    messages, current_evidence
                )
            except Exception as exc:
                observations["blocker_c_inject_error"] = type(exc).__name__
                raise
            observations["blocker_c_post_inject"] = deepcopy(result)
            return result

    gpt._GROUNDING_AVAILABLE = True
    gpt.ConfidenceInjector = ObservedConfidenceInjector
    gpt._private_memory_evidence_for_turn = lambda *_args, **_kwargs: evidence
    gpt.grounding_verify_reply = lambda *_args, **_kwargs: types.SimpleNamespace(
        passed=True, suggested_fallback=""
    )
    sync_label["value"] = "blocker_c"
    blocker_reply = gpt.ask_gpt("CONTEXT-FIXTURE-BLOCKER-C-001")
    assert blocker_reply
    observations["blocker_c_marker"] = evidence_marker


class _PrivateReadTripwire(dict):
    def _fail(self, *_args, **_kwargs):
        raise AssertionError("public capture read PRIVATE-LEAK-SENTINEL")

    __getitem__ = _fail
    __iter__ = _fail
    __len__ = _fail
    get = _fail
    items = _fail
    keys = _fail
    values = _fail


def _capture_public(fixtures, transport, observations):
    helper = _load_source_module(
        "context_runtime_public_helper",
        NANA_ROOT / "tests" / "smoke" / "smoke_memory_v2_public_prompt.py",
    )
    gpt, _unused = helper._install_isolated_dependencies()
    fixture = fixtures["public_stage"]
    viewer = "CONTEXT-FIXTURE-PUBLIC-VIEWER-001"
    topic = "CONTEXT-FIXTURE-PUBLIC-TOPIC-001"
    room = "CONTEXT-FIXTURE-PUBLIC-ROOM-001"
    gpt.NANA_PERSONALITY = "Nana Public synthetic baseline."
    gpt.NANA_SHARED_HISTORY = "PRIVATE-LEAK-SENTINEL"
    gpt.LLMGATE_PUBLIC_MODEL = fixture["model"]
    gpt.LLMGATE_CHEAP_MODEL = fixture["model"]
    gpt.LLMGATE_MAIN_MODEL = fixture["model"]
    gpt.memory = _PrivateReadTripwire()
    sys.modules["nana.memory"].memory = gpt.memory

    sys.modules["nana.runtime.social_session"].get_social_session = lambda: types.SimpleNamespace(
        _topic_stack=[],
        format_public_room_context=lambda *args, **kwargs: f"PUBLIC ROOM CONTEXT: {room}",
    )

    def stage_identity(**kwargs):
        observations["stage_viewer_names"].append(str(kwargs.get("viewer_name") or ""))
        return (
            "PUBLIC STAGE IDENTITY: "
            f"viewer={kwargs.get('viewer_name')} topic={kwargs.get('room_topic')}"
        )

    sys.modules["nana.runtime.public_stage_identity"].build_public_stage_prompt_block = stage_identity

    def sync_provider(**kwargs):
        text = transport.capture(
            "public_sync", kwargs.get("preferred_model"), kwargs["messages"]
        )
        return helper._Response(text)

    def stream_provider(*args, **kwargs):
        yield transport.capture(
            "public_stream",
            _model_from_call(args, kwargs),
            _messages_from_call(args, kwargs),
        )

    gpt.create_chat_completion_with_fallback = sync_provider
    sys.modules["nana.brain.llmgate_client"].stream_llmgate_messages = stream_provider
    metadata = {
        "author_id": "CONTEXT-FIXTURE-PUBLIC-AUTHOR-001",
        "room_id": "CONTEXT-FIXTURE-PUBLIC-ROOM-ID-001",
        "stream_session_id": "CONTEXT-FIXTURE-PUBLIC-SESSION-001",
        "event_id": "CONTEXT-FIXTURE-PUBLIC-EVENT-001",
        "public_metadata": {"topic": topic},
    }
    kwargs = {
        "viewer_name": viewer,
        "stream_mode": True,
        "public_platform": "youtube",
        "metadata": metadata,
    }
    sync_reply = gpt.ask_gpt(fixture["current_input"], **kwargs)

    async def consume():
        return "".join(
            [
                part
                async for part in gpt.ask_gpt_stream(
                    fixture["current_input"], **kwargs
                )
            ]
        )

    stream_reply = _run_immediate(consume())
    assert sync_reply and stream_reply
    observations["public_viewer"] = viewer
    boundary = gpt.resolve_persona_boundary(
        viewer_name=viewer, stream_mode=True, platform="youtube"
    )
    observations["livestream_boundary"] = boundary.prompt_block


def _capture_operator(fixtures, transport):
    fixture = fixtures["operator"]
    persona_boundary = importlib.import_module("nana.runtime.persona_boundary")
    boundary = persona_boundary.resolve_persona_boundary(
        bridge_system=True, platform="fixture_bridge"
    )
    assert boundary.interaction_scope == "bridge_system"
    assert boundary.public is False
    messages = [
        {
            "role": "system",
            "content": "Nana operator synthetic envelope.\n\n" + boundary.prompt_block,
        },
        {"role": "user", "content": fixture["current_input"]},
    ]
    transport.capture_envelope("operator", fixture["model"], messages)


class _Clock:
    def __init__(self):
        self.value = 1000.0

    def __call__(self):
        self.value += 0.01
        return self.value


def _capture_autonomy(fixtures, transport):
    banter_module = _load_source_module(
        "context_runtime_real_llm_banter",
        NANA_ROOT / "autonomy" / "llm_banter.py",
    )
    llmgate = sys.modules["nana.brain.llmgate_client"]
    labels = iter(("autonomy_private", "autonomy_public"))

    def fake_call(*args, **kwargs):
        label = next(labels)
        text = transport.capture(
            label,
            _model_from_call(args, kwargs),
            _messages_from_call(args, kwargs),
        )
        return text, "ok"

    llmgate.call_llmgate_messages = fake_call
    with mock.patch.dict(
        os.environ,
        {
            "NANA_AUTONOMY_LLM_DISABLED": "0",
            "NANA_AUTONOMY_LLM_MODEL": "fixture-model",
        },
    ):
        private = fixtures["autonomy_private"]
        private_result = banter_module.LLMBanter(clock=_Clock()).generate(
            "idle_banter",
            {
                "active_zone": "fixture_zone",
                "active_app": private["current_input"],
                "mood_affection": 0.6,
                "silence_duration_s": 12,
                "user_is_typing": False,
                "audio_busy": False,
                "time": {"part_of_day": "fixture_time"},
            },
            {
                "effective": True,
                "kind": "fixture",
                "title": "CONTEXT-FIXTURE-AUTONOMY-WEB-001",
            },
            min_age_s=0,
        )
        public = fixtures["autonomy_public"]
        public_result = banter_module.LLMBanter(clock=_Clock()).generate(
            "stream_host",
            {
                "active_zone": "public_stage",
                "active_app": public["current_input"],
                "mood_affection": 0.7,
                "silence_duration_s": 8,
                "user_is_typing": False,
                "audio_busy": False,
                "time": {"part_of_day": "fixture_time"},
                "stream_stage_policy_gate": True,
            },
            {
                "effective": True,
                "kind": "fixture",
                "title": "CONTEXT-FIXTURE-AUTONOMY-PUBLIC-WEB-001",
            },
            min_age_s=0,
        )
    assert private_result is not None and private_result.reason == "ok"
    assert public_result is not None and public_result.reason == "ok"


def _capture_current_builders(fixtures):
    transport = FakeTransport()
    observations = {"stage_viewer_names": []}
    _capture_private(fixtures, transport, observations)
    _capture_public(fixtures, transport, observations)
    _capture_operator(fixtures, transport)
    _capture_autonomy(fixtures, transport)
    return transport, observations


def _expect_fixture_error(value, profile, contains):
    try:
        validate_fixture(value, profile)
    except FixtureError as exc:
        assert contains in str(exc), (contains, str(exc))
        return
    raise AssertionError(f"fixture validation accepted invalid case: {contains}")


def _test_fixture_schema(fixtures):
    assert set(fixtures) == set(PROFILES)
    for profile, fixture in fixtures.items():
        assert set(fixture) == FIXTURE_KEYS and len(fixture) == 8
        assert fixture["wire_roles"] == ["system", "user"]
        assert validate_fixture(deepcopy(fixture), profile) == fixture

    base = deepcopy(fixtures["private_owner"])
    case = deepcopy(base)
    case["unexpected"] = "CONTEXT-FIXTURE-UNKNOWN-001"
    _expect_fixture_error(case, "private_owner", "unknown fixture keys")
    case = deepcopy(base)
    del case["wire_roles"]
    _expect_fixture_error(case, "private_owner", "missing required fixture keys")
    case = deepcopy(base)
    case["wire_roles"] = ["user", "system"]
    _expect_fixture_error(case, "private_owner", "wire_roles")
    case = deepcopy(base)
    case["current_input"] = "real private input"
    _expect_fixture_error(case, "private_owner", "current_input")
    case = deepcopy(base)
    case["required_markers"] = ["real private history"]
    _expect_fixture_error(case, "private_owner", "non-synthetic source content")
    case = deepcopy(base)
    case["required_markers"] = ["sk-1234567890abcdef"]
    _expect_fixture_error(case, "private_owner", "credential-like material")
    case = deepcopy(base)
    case["required_markers"] = ["Nana", "Nana"]
    _expect_fixture_error(case, "private_owner", "unique string list")
    try:
        json.loads(
            '{"fixture_version":1,"fixture_version":1}',
            object_pairs_hook=_unique_object,
        )
    except FixtureError as exc:
        assert "duplicate JSON keys" in str(exc)
    else:
        raise AssertionError("duplicate JSON keys were accepted")


PROFILE_LABELS = {
    "private_owner": ("private_sync", "private_stream"),
    "public_stage": ("public_sync", "public_stream"),
    "operator": ("operator",),
    "autonomy_private": ("autonomy_private",),
    "autonomy_public": ("autonomy_public",),
}


def _test_capture_count(transport):
    assert REAL_PROVIDER_CALLS == 0
    assert len(transport.calls) == 7, [call.label for call in transport.calls]
    assert {call.label for call in transport.calls} == {
        "private_sync",
        "private_stream",
        "public_sync",
        "public_stream",
        "autonomy_private",
        "autonomy_public",
        "blocker_c",
    }
    assert [call.label for call in transport.envelopes] == ["operator"]
    for call in transport.captures:
        _assert_universal_capture_contract(call)


def _assert_universal_capture_contract(call):
    assert call.roles == ["system", "user"], (call.label, call.roles)
    assert call.model == "fixture-model", (call.label, call.model)
    assert not any(
        CREDENTIAL.search(text) for text in _strings(call.messages)
    ), call.label


def _test_semantic_baselines(fixtures, transport):
    for profile, labels in PROFILE_LABELS.items():
        fixture = fixtures[profile]
        for label in labels:
            call = transport.one(label)
            _assert_universal_capture_contract(call)
            assert call.roles == fixture["wire_roles"], (label, call.roles)
            assert call.model == fixture["model"], (label, call.model)
            assert all(marker in call.text for marker in fixture["required_markers"]), label
            assert all(marker not in call.text for marker in fixture["forbidden_markers"]), label
            assert not any(CREDENTIAL.search(text) for text in _strings(call.messages)), label
            if fixture["route"] == "autonomy":
                assert fixture["current_input"] in call.messages[1][1]
            else:
                assert call.messages[1] == ("user", fixture["current_input"])


def _test_sync_stream_capture_parity(transport):
    assert transport.one("private_sync").messages == transport.one("private_stream").messages
    assert transport.one("public_sync").messages == transport.one("public_stream").messages


def _test_blocker_a(fixtures, observations):
    values = observations["stage_viewer_names"]
    assert len(values) == 2, values
    current_input = fixtures["public_stage"]["current_input"]
    display_label = observations["public_viewer"]
    assert set(values) in ({current_input}, {display_label}), values
    observations["blocker_a"] = (
        "legacy_viewer_name_from_text"
        if values[0] == current_input
        else "resolved_scope_display_name"
    )


def _test_blocker_b(transport, observations):
    boundary = observations["livestream_boundary"]
    counts = {
        transport.one("public_sync").messages[0][1].count(boundary),
        transport.one("public_stream").messages[0][1].count(boundary),
    }
    assert len(counts) == 1 and next(iter(counts)) in (1, 2), counts
    count = next(iter(counts))
    observations["blocker_b"] = (
        "legacy_boundary_count_2" if count == 2 else "resolved_boundary_count_1"
    )


def _test_blocker_c(transport, observations):
    marker = observations["blocker_c_marker"]
    wire = transport.one("blocker_c")
    _assert_universal_capture_contract(wire)
    before = observations.get("blocker_c_pre_inject")
    after = observations.get("blocker_c_post_inject")
    if before is None:
        assert marker in wire.text, "resolved path lost precompiled evidence"
        observations["blocker_c"] = "resolved_precompiled"
        return
    if "blocker_c_inject_error" in observations:
        assert marker in wire.text, "fail-closed injector path lost evidence"
        observations["blocker_c"] = "resolved_precompiled"
        return
    assert after is not None
    if after != before:
        assert marker not in json.dumps(before, ensure_ascii=False)
        assert marker in json.dumps(after, ensure_ascii=False)
        assert wire.messages == tuple(
            (str(item["role"]), str(item["content"])) for item in after
        )
        observations["blocker_c"] = "legacy_post_assembly"
        return
    assert marker in wire.text, "no-op injector path lost precompiled evidence"
    observations["blocker_c"] = "resolved_precompiled"


def _test_no_unexpected_side_effect_attempts(audit):
    assert len(audit.blocked) == audit.expected_probe_count, audit.blocked


def run_all():
    state = {}
    failures = 0
    passed = 0

    def check(name, action):
        nonlocal failures, passed
        try:
            action()
            passed += 1
            print(f"PASS {name}")
        except Exception as exc:
            failures += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")

    with _side_effect_guards() as audit:
        check("fail_closed_side_effect_guards", lambda: _probe_side_effect_guards(audit))

        def fixture_case():
            state["fixtures"] = load_fixtures()
            _test_fixture_schema(state["fixtures"])

        check("five_exact_sanitized_fixtures", fixture_case)

        def capture_case():
            transport, observations = _capture_current_builders(state["fixtures"])
            state["transport"] = transport
            state["observations"] = observations
            _test_capture_count(transport)

        check("seven_offline_fake_transport_paths", capture_case)
        check(
            "legacy_semantic_markers_and_exact_wire_roles",
            lambda: _test_semantic_baselines(state["fixtures"], state["transport"]),
        )
        check(
            "legacy_sync_stream_capture_parity",
            lambda: _test_sync_stream_capture_parity(state["transport"]),
        )
        check(
            "blocker_a_observation",
            lambda: _test_blocker_a(state["fixtures"], state["observations"]),
        )
        check(
            "blocker_b_observation",
            lambda: _test_blocker_b(state["transport"], state["observations"]),
        )
        check(
            "blocker_c_observation",
            lambda: _test_blocker_c(state["transport"], state["observations"]),
        )
        check(
            "no_unexpected_side_effect_attempts",
            lambda: _test_no_unexpected_side_effect_attempts(audit),
        )

    transport = state.get("transport")
    if transport is not None:
        for call in transport.captures:
            transport_kind = "fake_transport" if call in transport.calls else "synthetic_envelope"
            print(
                f"CAPTURE {call.label}: kind={transport_kind}, "
                f"roles={','.join(call.roles)}, model={call.model}, sha256={call.sha256}"
            )
    observations = state.get("observations", {})
    for blocker in ("blocker_a", "blocker_b", "blocker_c"):
        if blocker in observations:
            print(f"OBSERVE {blocker}: {observations[blocker]}")
    fake_calls = len(transport.calls) if transport is not None else 0
    print(
        "Context runtime legacy baseline: "
        f"{passed} passed, {failures} failed, "
        f"real_provider_calls={REAL_PROVIDER_CALLS}, fake_transport_calls={fake_calls}"
    )
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(run_all())
