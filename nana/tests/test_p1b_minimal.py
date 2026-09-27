"""Compatibility smoke for the canonical P1-B public grounding API."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))


def _fact(now=2_000_000.0):
    return {
        "id": "pub1",
        "text": "minh thích cà phê",
        "lane": "public",
        "platform": "test",
        "actor_key": "test:minh",
        "room_id": "room1",
        "source_event_id": "evt-001",
        "source": "public_verified",
        "type": "project_fact",
        "confidence": 0.95,
        "verified": True,
        "consent": True,
        "expires_at": now + 1000,
    }


def _scope():
    return {"platform": "test", "actor_key": "test:minh", "room_id": "room1", "consent": True}


def test_import_public_grounding_helpers():
    from nana.runtime import (
        PublicGroundingDecision,
        filter_public_candidates,
        make_public_grounding_decision,
        format_public_grounding_prompt,
        verify_public_grounding_reply,
    )
    assert PublicGroundingDecision is not None
    assert callable(filter_public_candidates)
    assert callable(make_public_grounding_decision)
    assert callable(format_public_grounding_prompt)
    assert callable(verify_public_grounding_reply)


def test_import_memory_grounding():
    from nana.runtime import memory_grounding
    assert hasattr(memory_grounding, "filter_public_candidates")
    assert hasattr(memory_grounding, "make_public_grounding_decision")


def test_gpt_imports_grounding():
    from nana.brain import gpt
    assert callable(gpt._public_grounding_decision_for_turn)


def test_basic_filter_public_candidates():
    from nana.runtime.memory_grounding import filter_public_candidates
    assert len(filter_public_candidates([_fact()], _scope(), now=2_000_000.0)) == 1


def test_basic_make_decision():
    from nana.runtime.memory_grounding import make_public_grounding_decision
    decision = make_public_grounding_decision(
        "minh thích gì?", [_fact()], _scope(), now=2_000_000.0
    )
    assert decision is not None
    assert decision.should_ground
    assert "cà phê" in decision.safe_answer


def test_private_grounding_survives_missing_public_helper():
    script = """
import builtins
import sys
real_import = builtins.__import__
for name in list(sys.modules):
    if name.startswith('nana.runtime.memory_grounding') or name.startswith('nana.runtime.public_grounding_helpers'):
        sys.modules.pop(name, None)
def blocked_import(name, *args, **kwargs):
    if name == 'nana.runtime.public_grounding_helpers':
        raise ModuleNotFoundError(name)
    return real_import(name, *args, **kwargs)
builtins.__import__ = blocked_import
from nana.runtime import memory_grounding
assert hasattr(memory_grounding, 'MemoryEvidence')
assert memory_grounding._PUBLIC_GROUNDING_AVAILABLE is False
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(ROOT.parent),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_public_hotpath_fails_closed_when_public_helper_is_unavailable():
    script = "\n".join([
        "import asyncio, builtins, sys",
        "from types import SimpleNamespace",
        "real_import = builtins.__import__",
        "for name in list(sys.modules):",
        "    if name.startswith('nana.runtime.memory_grounding') or name.startswith('nana.runtime.public_grounding_helpers'):",
        "        sys.modules.pop(name, None)",
        "def blocked_import(name, *args, **kwargs):",
        "    if name == 'nana.runtime.public_grounding_helpers': raise ModuleNotFoundError(name)",
        "    return real_import(name, *args, **kwargs)",
        "builtins.__import__ = blocked_import",
        "from nana import config",
        "from nana.brain import gpt",
        "config.MEMORY_PUBLIC_CROSS_SESSION_RECALL_ENABLED = True",
        "boundary = SimpleNamespace(public=True, livestream=False, prompt_block='', scope={'platform':'test','actor_key':'test:minh','room_id':'room1','consent':True})",
        "gpt._lane_first_inputs = lambda **kwargs: (boundary, {'memory_consent': True}, {}, {'affection': .5, 'annoyance': 0., 'playfulness': .5})",
        "gpt._private_memory_evidence_for_turn = lambda *args, **kwargs: None",
        "gpt._public_deterministic_kind = lambda *args: None",
        "gpt._public_has_session_context = lambda *args: False",
        "gpt.create_chat_completion_with_fallback = lambda **kwargs: (_ for _ in ()).throw(AssertionError('sync model call'))",
        "assert 'chưa' in gpt.ask_gpt('what is my birthday?', viewer_name='viewer', public_platform='test', metadata={}).lower()",
        "from nana.brain import llmgate_client",
        "llmgate_client.stream_llmgate_messages = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('stream model call'))",
        "async def run_stream(): return ''.join([part async for part in gpt.ask_gpt_stream('what is my birthday?', viewer_name='viewer', public_platform='test', metadata={})])",
        "assert 'chưa' in asyncio.run(run_stream()).lower()",
    ])
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(ROOT.parent),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
