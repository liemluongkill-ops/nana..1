"""Static and runtime side-effect firewall for STREAM V1 CUM 0."""

from __future__ import annotations

import ast
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


NANA_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = NANA_ROOT / "runtime" / "stream_cum0_contract.py"

FORBIDDEN_IMPORTS = {
    "socket",
    "subprocess",
    "requests",
    "httpx",
    "urllib",
    "websockets",
    "openai",
    "grpc",
    "elevenlabs",
    "nana.runtime.youtube_chat_transport",
    "nana.runtime.youtube_chat_probe",
    "nana.runtime.external_bridge",
    "nana.runtime.stream_event_timeline",
    "nana.runtime.stream_ready_status",
    "nana.runtime.stream_public_output",
    "nana.runtime.stream_result_subtitle_pipeline",
    "nana.runtime.memory_spine",
    "nana.runtime.public_delivery_state",
    "nana.brain.gpt",
    "nana.brain.llmgate_client",
    "nana.runtime.voice_delivery",
    "nana.runtime.avatar_live_hook",
    "nana.runtime.avatar_vts_dispatch",
    "nana.runtime.public_avatar_reaction",
    "nana.voice.engine",
    "nana.integrations.vts",
}

FORBIDDEN_CALLS = {
    "open",
    "write_text",
    "write_bytes",
    "system",
    "stream_ready_snapshot",
    "record_stream_event",
    "transition_delivery",
    "get_stream_state",
    "start_external_bridge_worker",
    "ask_gpt",
    "ask_gpt_stream",
    "speak",
    "play_audio",
    "send_public",
    "write_subtitle",
    "write_public_subtitle_file",
    "clear_public_subtitle_file",
    "build_result_subtitle_pipeline_write",
    "trigger_expression",
    "connect_vts",
    "get_voice_delivery",
    "get_avatar_live_hook",
}


def _module_name(node: ast.AST) -> str:
    if isinstance(node, ast.Import):
        return ""
    if isinstance(node, ast.ImportFrom):
        return node.module or ""
    return ""


def test_contract_source_has_no_forbidden_direct_imports_or_calls() -> None:
    tree = ast.parse(CONTRACT_PATH.read_text(encoding="utf-8"), str(CONTRACT_PATH))
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imports.update(
        _module_name(node)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    )
    assert not (imports & FORBIDDEN_IMPORTS), imports & FORBIDDEN_IMPORTS

    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not (called & FORBIDDEN_CALLS), called & FORBIDDEN_CALLS

    attributes = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert not (attributes & FORBIDDEN_CALLS), attributes & FORBIDDEN_CALLS


def test_contract_does_not_reference_forbidden_owner_symbols() -> None:
    source = CONTRACT_PATH.read_text(encoding="utf-8")
    for marker in (
        "nana.runtime.public_delivery_state",
        "nana.runtime.memory_spine",
        "stream_ready_snapshot",
        "record_stream_event",
        "start_external_bridge_worker",
        "transition_delivery",
    ):
        assert marker not in source


def test_contract_runtime_audit_allows_only_in_memory_ledger_mutation() -> None:
    # Imports happen before the irreversible audit hook so ordinary Python
    # import bookkeeping is not mistaken for a contract side effect.
    child = textwrap.dedent(
        f"""
        import os, sys
        sys.path.insert(0, {str(NANA_ROOT.parent)!r})
        os.environ['NANA_STREAM_CUM0_ENABLED'] = '1'
        from nana.runtime.public_context_boundary import PublicEventScope
        from nana.runtime.public_identity import CanonicalPublicIdentity
        from nana.runtime.stream_state import StreamPolicy, StreamState, ErrorType, InteractionTone, AvatarEnergy, ViewerExpectation
        from nana.runtime.social_session import PublicTurn
        import nana.runtime.public_delivery_state as delivery_owner
        import nana.runtime.social_session as social_owner
        from nana.runtime.stream_cum0_contract import StreamContractLedger, build_stream_envelope

        tripwire_calls = []
        def tripwire(*args, **kwargs):
            tripwire_calls.append((args, kwargs))
            raise AssertionError('forbidden owner operation')
        delivery_owner.transition_delivery = tripwire
        delivery_owner.start_delivery_attempt = tripwire
        social_owner.transition_delivery = tripwire

        class Provenance:
            source = 'youtube_live_chat'
            adapter_id = 'fake-youtube-v1'
            provider = 'youtube'
            def verify(self, *, scope, provider_event_type, provider_stream_state, offline_at):
                return True

        class PolicySource:
            def get_policy(self):
                return StreamPolicy(
                    state=StreamState.LIVE_ACTIVE, reason='audit',
                    can_proactive=False, can_auto_send=False, can_use_private_memory=False,
                    proactive_budget=0, interaction_tone=InteractionTone.QUIET,
                    avatar_energy=AvatarEnergy.LOW, viewer_expectation=ViewerExpectation.MUTED,
                    error_type=ErrorType.NONE, can_reply=True, can_speak=False, can_avatar=False,
                )

        def audit(event, args):
            if event in {{
                'open', 'os.open', 'os.rename', 'os.replace', 'os.remove',
                'socket.connect', 'socket.__new__', 'subprocess.Popen',
                'os.system', 'os.spawn', 'os.posix_spawn', 'ctypes.dlopen',
            }}:
                raise AssertionError('contract side effect: ' + event)
        sys.addaudithook(audit)

        identity = CanonicalPublicIdentity('youtube', 'actor-1', 'youtube:actor-1')
        scope = PublicEventScope('youtube', 'room-1', 'session-1', 'provider-event-1', 'Viewer', identity)
        built = build_stream_envelope(scope, Provenance(), 'Xin chào Nana', 'text_message_event', 'live', None, 'ingress-1', 1, 1700000099.0, 1700000090.0, 1700000100.0)
        assert built.status.value == 'validated'
        ledger = StreamContractLedger()
        admitted = ledger.authorize_and_accept(built.envelope, 'Xin chào Nana', PolicySource(), 'session-1', 1700000100.0)
        assert admitted.status == 'accepted'
        assert admitted.public_turn is not None
        assert admitted.public_turn.delivery_state == 'generated'
        assert admitted.public_turn.attempt_id == 'ingress-1'
        assert tripwire_calls == []
        assert ledger.purge(1700001000.001) == 1
        assert ledger.snapshot()['entries'] == 0
        for forbidden_module in (
            'nana.runtime.youtube_chat_transport',
            'nana.runtime.youtube_chat_probe',
            'nana.runtime.external_bridge',
            'nana.runtime.stream_event_timeline',
            'nana.runtime.stream_ready_status',
            'nana.runtime.stream_public_output',
            'nana.runtime.stream_result_subtitle_pipeline',
            'nana.runtime.memory_spine',
            'nana.runtime.voice_delivery',
            'nana.runtime.avatar_live_hook',
            'nana.runtime.avatar_vts_dispatch',
            'nana.runtime.public_avatar_reaction',
            'nana.brain.gpt',
            'nana.voice.engine',
            'nana.integrations.vts',
            'openai',
            'grpc',
            'elevenlabs',
        ):
            assert forbidden_module not in sys.modules, forbidden_module
        print('PASS audit')
        """
    )
    env = dict(os.environ)
    env["NANA_STREAM_CUM0_ENABLED"] = "1"
    result = subprocess.run(
        [sys.executable, "-c", child],
        cwd=str(NANA_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS audit" in result.stdout


def test_contract_has_no_memory_or_delivery_owner_globals() -> None:
    import importlib

    module = importlib.import_module("nana.runtime.stream_cum0_contract")
    assert not hasattr(module, "MemoryRecord")
    assert not hasattr(module, "PublicDeliveryRecord")
    assert not hasattr(module, "transition_delivery")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
