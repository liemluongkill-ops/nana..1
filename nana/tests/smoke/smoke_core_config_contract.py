"""Deterministic smoke for CORE-CONFIG-CONTRACT-1.

No Nana runtime service or external backend is started. The app fail-fast path
uses local sentinels that fail the test if construction is attempted.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager, redirect_stdout
from dataclasses import FrozenInstanceError
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import types
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


_PROTECTED_DATA_ROOT = (ROOT / "nana" / "data").resolve()
_PROTECTED_LOG_ROOT = (ROOT / "nana" / "runtime_logs").resolve()
_ALLOW_INTERNAL_CHILD = False


def _guarded_path(value):
    if isinstance(value, int):
        return None
    try:
        return Path(value).resolve()
    except (OSError, TypeError, ValueError):
        return None


def _startup_audit_guard(event, args):
    if event == "open" and args:
        path = _guarded_path(args[0])
        if path is not None:
            folded = str(path).casefold()
            if (
                path.name.casefold() == ".env"
                or path.name.casefold() == "token.txt"
                or folded.startswith(str(_PROTECTED_DATA_ROOT).casefold())
                or folded.startswith(str(_PROTECTED_LOG_ROOT).casefold())
                or "\\.factory\\settings.json" in folded
            ):
                raise PermissionError(f"protected startup smoke path: {path.name}")
    if event == "socket.connect":
        caller = sys._getframe(1)
        address = args[1] if len(args) > 1 else None
        if (
            caller.f_code.co_name in {"socketpair", "_fallback_socketpair"}
            and Path(caller.f_code.co_filename).name == "socket.py"
            and isinstance(address, tuple)
            and address
            and address[0] in {"127.0.0.1", "::1"}
        ):
            return
    if event in {"socket.connect", "socket.getaddrinfo"}:
        raise PermissionError("network disabled in startup config smoke")
    if event in {"subprocess.Popen", "os.system", "os.posix_spawn"}:
        if not _ALLOW_INTERNAL_CHILD:
            raise PermissionError("process creation disabled in startup config smoke")


sys.addaudithook(_startup_audit_guard)

# The smoke process owns these synthetic values. It never reads or restores a
# host credential and exits after the checks.
os.environ.update(
    {
        "OPENAI_API_KEY": "test-openai-secret",
        "ELEVEN_API_KEY": "test-eleven-secret",
        "ELEVEN_VOICE_ID": "test-voice-id",
        "NANA_PRESENCE_SESSION_TOKEN": "",
        "NANA_AVATAR_GATEWAY_TOKEN": "",
        "NANA_CONTEXT_PRIVATE_MODE": "legacy",
        "NANA_CONTEXT_PUBLIC_GPT_MODE": "legacy",
        "NANA_CONTEXT_CUM2_MODE": "legacy",
        "NANA_CONTEXT_AUTONOMY_MODE": "legacy",
        "NANA_CONTEXT_BUDGET_POLICY_REVISION": "",
    }
)


_original_path_exists = Path.exists


def _env_blind_exists(path):
    if path.name.casefold() == ".env":
        return False
    return _original_path_exists(path)


Path.exists = _env_blind_exists
try:
    nana_package = types.ModuleType("nana")
    nana_package.__path__ = [str(ROOT / "nana")]
    runtime_package = types.ModuleType("nana.runtime")
    runtime_package.__path__ = [str(ROOT / "nana" / "runtime")]
    sys.modules["nana"] = nana_package
    sys.modules["nana.runtime"] = runtime_package
    from nana.runtime.startup_config_contract import (
        build_startup_config_contract,
        build_startup_config_snapshot,
        format_startup_config_line,
    )
finally:
    Path.exists = _original_path_exists



def _config(**overrides):
    values = {
        "NANA_CHAT_PROVIDER": "llmgate",
        "LLMGATE_MAIN_MODEL": "gpt-5.6-terra",
        "LLMGATE_MAIN_REASONING_EFFORT": "none",
        "OPENAI_MODEL": "gpt-4o",
        "OPENAI_API_KEY": "test-openai-secret",
        "NANA_OPENAI_FALLBACK_ENABLED": False,
        "VOICE_STREAMING_ENABLED": True,
        "VOICE_STREAMING_PILOT_ENABLED": True,
        "VOICE_STREAMING_KILL_SWITCH": False,
        "VOICE_STREAMING_DIRECT_ONLY": True,
        "PRIVATE_VOICE_OVERLAP_ENABLED": True,
        "PRIVATE_VOICE_OVERLAP_MIN_CHARS": 45,
        "PRIVATE_VOICE_OVERLAP_MAX_CHARS": 140,
        "PRIVATE_VOICE_OVERLAP_COALESCE_MS": 150,
        "PRIVATE_VOICE_OVERLAP_TAIL_TIMEOUT_S": 35,
        "PRIVATE_VOICE_OVERLAP_PCM_ENABLED": True,
        "PRIVATE_VOICE_OVERLAP_PCM_OUTPUT_FORMAT": "pcm_24000",
        "PRIVATE_VOICE_OVERLAP_PCM_START_BUFFER_MS": 300,
        "PRIVATE_VOICE_OVERLAP_PCM_NETWORK_CHUNK_BYTES": 4096,
        "PRIVATE_VOICE_OVERLAP_PCM_TIMEOUT_S": 45,
        "VOICE_HTTP_KEEPALIVE_ENABLED": True,
        "VOICE_HTTP_POOL_MAXSIZE": 8,
        "PRIVATE_VOICE_TTD_ENABLED": False,
        "PRIVATE_VOICE_TTD_MODEL": "eleven_v3",
        "PRIVATE_VOICE_TTD_INPUT_MODE": "incremental",
        "PRIVATE_VOICE_TTD_OUTPUT_FORMAT": "pcm_24000",
        "PRIVATE_VOICE_TTD_MIN_CHARS": 40,
        "PRIVATE_VOICE_TTD_MIN_WORDS": 8,
        "PRIVATE_VOICE_TTD_CHUNK_TARGET_CHARS": 120,
        "PRIVATE_VOICE_TTD_CHUNK_MAX_CHARS": 240,
        "PRIVATE_VOICE_TTD_START_BUFFER_MS": 300,
        "PRIVATE_VOICE_TTD_TIMEOUT_S": 45,
        "PRIVATE_VOICE_TTD_CAPTURE_ENABLED": False,
        "DEBUG_NO_TTS": False,
        "VOICE_TEST_MODE": False,
        "ELEVEN_OUTPUT_FORMAT": "mp3_44100_128",
        "ELEVEN_API_KEY": "test-eleven-secret",
        "VOICE_ID": "test-voice-secret",
        "VTS_STARTUP_ENABLED": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _contract(*, env=None, **config_overrides):
    return build_startup_config_contract(
        env={} if env is None else env,
        config_source=_config(**config_overrides),
    )


@contextmanager
def _app_lazy_import_fixtures():
    modules = {
        "nana.autonomy.llm_banter": types.SimpleNamespace(
            get_banter=lambda *_a, **_k: None,
            init_banter=lambda *_a, **_k: None,
        ),
        "nana.autonomy.web_context": types.SimpleNamespace(
            init_scraper=lambda *_a, **_k: None,
        ),
        "nana.cli.handle_text": types.SimpleNamespace(
            handle_text=lambda *_a, **_k: None,
        ),
        "nana.integrations.vts": types.SimpleNamespace(
            ensure_vts_ready=lambda *_a, **_k: None,
            vts_mouth_loop=lambda *_a, **_k: None,
        ),
        "nana.runtime.presence_session_server": types.SimpleNamespace(
            PresenceCaptureOutcome=object,
            PresenceDisplayError=RuntimeError,
            PresenceSessionServer=object,
            configure_presence_session_status=lambda *_a, **_k: None,
            set_active_presence_session_server=lambda *_a, **_k: None,
        ),
        "nana.runtime.presence_audio_quality": types.SimpleNamespace(
            evaluate_presence_audio=lambda *_a, **_k: None,
        ),
        "nana.runtime.pulse": types.SimpleNamespace(
            nana_pulse=lambda *_a, **_k: None,
        ),
    }
    with mock.patch.dict(sys.modules, modules):
        yield


def test_current_and_default_contract_are_valid() -> None:
    current = build_startup_config_contract(env={}, config_source=_config())
    default = _contract()

    assert current.is_valid, current.errors
    assert current.status == "valid", current.warnings
    assert default.is_valid and default.status == "valid"
    assert default.snapshot.external_bridge_poll_seconds == 0.35
    assert default.snapshot.autonomy_stop_join_timeout_seconds == 65.0
    assert default.snapshot.autonomy_poller_join_timeout_seconds == 2.0
    assert default.snapshot.autonomy_auto_output_enabled is False
    assert default.snapshot.presence_session_enabled is False
    assert default.snapshot.presence_session_host == "0.0.0.0"
    assert default.snapshot.presence_session_port == 8765
    assert default.snapshot.presence_session_token_present is False
    assert default.snapshot.presence_session_heartbeat_seconds == 5.0
    assert default.snapshot.presence_session_timeout_seconds == 15.0
    assert "presence_session=off:0.0.0.0:8765" in format_startup_config_line(default)
    print("  current/default: valid")


def test_private_web_defaults_on_and_explicit_off_remains_available() -> None:
    default = _contract()
    assert default.is_valid
    assert default.snapshot.private_web_chat_enabled is True
    assert default.snapshot.private_web_chat_host == '127.0.0.1'
    assert default.snapshot.private_web_chat_port == 8767
    for value in ('0', 'false', 'off'):
        disabled = _contract(env={'NANA_PRIVATE_WEB_CHAT_ENABLED': value})
        assert disabled.is_valid
        assert disabled.snapshot.private_web_chat_enabled is False
    invalid = _contract(env={'NANA_PRIVATE_WEB_CHAT_ENABLED': 'maybe'})
    assert not invalid.is_valid
    exposed = _contract(env={'NANA_PRIVATE_WEB_CHAT_HOST': '0.0.0.0'})
    assert not exposed.is_valid
    print('  private web: default ON, explicit OFF, invalid/remote binding rejected')


def test_context_modes_default_legacy_and_fail_closed() -> None:
    from nana import config as runtime_config

    default = _contract(env={})
    snapshot = default.snapshot
    assert default.is_valid, default.errors
    assert snapshot.context_private_mode == "legacy"
    assert snapshot.context_public_gpt_mode == "legacy"
    assert snapshot.context_cum2_mode == "legacy"
    assert snapshot.context_autonomy_mode == "legacy"
    assert snapshot.context_budget_policy_revision == ""
    assert snapshot.private_web_chat_enabled is True
    assert runtime_config.NANA_CONTEXT_PRIVATE_MODE == "legacy"
    assert runtime_config.NANA_CONTEXT_PUBLIC_GPT_MODE == "legacy"
    assert runtime_config.NANA_CONTEXT_CUM2_MODE == "legacy"
    assert runtime_config.NANA_CONTEXT_AUTONOMY_MODE == "legacy"
    assert runtime_config.NANA_CONTEXT_BUDGET_POLICY_REVISION == ""

    variables = (
        "NANA_CONTEXT_PRIVATE_MODE",
        "NANA_CONTEXT_PUBLIC_GPT_MODE",
        "NANA_CONTEXT_CUM2_MODE",
        "NANA_CONTEXT_AUTONOMY_MODE",
    )
    for name in variables:
        for accepted in ("legacy", "shadow"):
            contract = _contract(env={name: accepted})
            assert contract.is_valid, (name, accepted, contract.errors)
        invalid = _contract(env={name: "unexpected"})
        assert not invalid.is_valid
        assert any(name in error for error in invalid.errors), invalid.errors

        for revision in ("", "fabricated-approved-looking-revision"):
            canonical = _contract(
                env={
                    name: "canonical",
                    "NANA_CONTEXT_BUDGET_POLICY_REVISION": revision,
                }
            )
            assert not canonical.is_valid
            assert "budget_policy_unapproved" in canonical.errors

    rendered = format_startup_config_line(default)
    assert "context=private:legacy/public:legacy/cum2:legacy/autonomy:legacy" in rendered
    print("  context modes: default legacy, invalid/canonical fail closed")


def test_snapshot_is_immutable() -> None:
    snapshot = _contract().snapshot
    try:
        snapshot.provider = "changed"
    except FrozenInstanceError:
        pass
    else:
        raise AssertionError("startup snapshot accepted mutation")

    serialized = snapshot.to_dict()
    serialized["provider"] = "changed"
    assert snapshot.provider == "llmgate"
    assert isinstance(snapshot.parse_errors, tuple)
    print("  immutable: frozen dataclass and detached serialization")


def test_snapshot_and_summary_do_not_expose_secrets() -> None:
    openai_secret = "openai-super-secret-value"
    eleven_secret = "eleven-super-secret-value"
    voice_secret = "voice-id-super-secret-value"
    presence_secret = "presence-session-super-secret-value"
    contract = build_startup_config_contract(
        env={"NANA_PRESENCE_SESSION_TOKEN": presence_secret},
        config_source=_config(
            OPENAI_API_KEY=openai_secret,
            ELEVEN_API_KEY=eleven_secret,
            VOICE_ID=voice_secret,
        ),
    )
    serialized = "\n".join(
        (
            repr(contract),
            repr(contract.snapshot),
            json.dumps(contract.snapshot.to_dict(), sort_keys=True),
            format_startup_config_line(contract),
        )
    )
    assert openai_secret not in serialized
    assert eleven_secret not in serialized
    assert voice_secret not in serialized
    assert presence_secret not in serialized
    assert contract.snapshot.openai_key_present is True
    assert contract.snapshot.eleven_key_present is True
    assert contract.snapshot.voice_id_present is True
    assert contract.snapshot.presence_session_token_present is True
    print("  secrets: readiness booleans only")


def test_unsupported_provider_is_fatal() -> None:
    contract = _contract(NANA_CHAT_PROVIDER="mystery-gateway")
    assert contract.status == "invalid" and not contract.is_valid
    assert any("unsupported provider" in error for error in contract.errors)
    print("  provider: unsupported value rejected")


def test_llmgate_requires_main_model() -> None:
    contract = _contract(LLMGATE_MAIN_MODEL="")
    assert contract.status == "invalid" and not contract.is_valid
    assert any("NANA_LLMGATE_MAIN_MODEL" in error for error in contract.errors)
    print("  llmgate: empty main model rejected")


def test_main_model_is_resolved_by_provider() -> None:
    llmgate = _contract(
        LLMGATE_MAIN_MODEL="llmgate-main",
        OPENAI_MODEL="openai-main",
    )
    openai = _contract(
        NANA_CHAT_PROVIDER="openai",
        LLMGATE_MAIN_MODEL="llmgate-main",
        OPENAI_MODEL="openai-main",
    )
    official = _contract(
        NANA_CHAT_PROVIDER="official_openai",
        LLMGATE_MAIN_MODEL="llmgate-main",
        OPENAI_MODEL="official-main",
    )
    missing = _contract(
        NANA_CHAT_PROVIDER="openai",
        OPENAI_MODEL="",
    )

    assert llmgate.snapshot.main_model == "llmgate-main"
    assert openai.snapshot.main_model == "openai-main"
    assert official.snapshot.main_model == "official-main"
    assert not missing.is_valid
    assert any("OPENAI_MODEL" in error for error in missing.errors)
    print("  model: resolved from active provider and required")


def test_openai_key_readiness_is_fatal_and_sanitized() -> None:
    for value in ("", "OPENAI_KEY_" + "CUA_BAN"):
        contract = _contract(OPENAI_API_KEY=value)
        assert not contract.is_valid and contract.status == "invalid"
        assert contract.snapshot.openai_key_present is False
        assert any("OPENAI_API_KEY" in error for error in contract.errors)
        assert value not in repr(contract.snapshot) if value else True
    print("  missing provider credential rejected safely")


def test_private_fast_lane_is_explicit_and_fail_closed() -> None:
    enabled = _contract(
        env={
            "NANA_LLM_FAST_PRIVATE_ENABLED": "1",
            "NANA_LLM_FAST_PRIVATE_MODEL": "gemini-3-flash",
        }
    )
    missing = _contract(
        env={
            "NANA_LLM_FAST_PRIVATE_ENABLED": "1",
            "NANA_LLM_FAST_PRIVATE_MODEL": "   ",
        }
    )
    wrong_provider = _contract(
        env={
            "NANA_LLM_FAST_PRIVATE_ENABLED": "1",
            "NANA_LLM_FAST_PRIVATE_MODEL": "gemini-3-flash",
        },
        NANA_CHAT_PROVIDER="openai",
    )
    same_model = _contract(
        env={
            "NANA_LLM_FAST_PRIVATE_ENABLED": "1",
            "NANA_LLM_FAST_PRIVATE_MODEL": "gpt-5.6-terra",
        }
    )

    assert enabled.is_valid and enabled.status == "valid", enabled
    assert enabled.snapshot.llm_fast_private_enabled is True
    assert enabled.snapshot.llm_fast_private_model == "gemini-3-flash"
    assert enabled.snapshot.llm_fast_private_max_input_chars == 220
    assert enabled.snapshot.llm_fast_private_max_tokens == 96
    assert "llm_fast=on:gemini-3-flash" in format_startup_config_line(enabled)
    assert not missing.is_valid
    assert any("NANA_LLM_FAST_PRIVATE_MODEL" in item for item in missing.errors)
    assert not wrong_provider.is_valid
    assert any("requires llmgate" in item for item in wrong_provider.errors)
    assert same_model.is_valid and same_model.status == "degraded"
    assert any("matches main model" in item for item in same_model.warnings)
    print("  private fast lane: explicit opt-in, validated, and separated from main")


def test_private_voice_overlap_is_explicit_and_bounded() -> None:
    default = _contract(env={})
    disabled = _contract(
        env={"NANA_PRIVATE_VOICE_OVERLAP_ENABLED": "0"}
    )
    enabled = _contract(
        env={
            "NANA_PRIVATE_VOICE_OVERLAP_ENABLED": "1",
            "NANA_PRIVATE_VOICE_OVERLAP_MIN_CHARS": "45",
            "NANA_PRIVATE_VOICE_OVERLAP_MAX_CHARS": "140",
            "NANA_PRIVATE_VOICE_OVERLAP_COALESCE_MS": "150",
            "NANA_PRIVATE_VOICE_OVERLAP_TAIL_TIMEOUT_S": "35",
        }
    )
    invalid = _contract(
        env={
            "NANA_PRIVATE_VOICE_OVERLAP_ENABLED": "1",
            "NANA_PRIVATE_VOICE_OVERLAP_MIN_CHARS": "180",
            "NANA_PRIVATE_VOICE_OVERLAP_MAX_CHARS": "100",
        }
    )

    assert default.snapshot.private_voice_overlap_enabled is True
    assert "voice_overlap=on" in format_startup_config_line(default)
    assert disabled.is_valid, disabled.errors
    assert disabled.snapshot.private_voice_overlap_enabled is False
    assert "voice_overlap=off" in format_startup_config_line(disabled)
    assert enabled.is_valid, enabled.errors
    assert enabled.snapshot.private_voice_overlap_enabled is True
    assert enabled.snapshot.private_voice_overlap_min_chars == 45
    assert enabled.snapshot.private_voice_overlap_max_chars == 140
    assert enabled.snapshot.private_voice_overlap_coalesce_ms == 150
    assert enabled.snapshot.private_voice_overlap_tail_timeout_seconds == 35
    assert "voice_overlap=on" in format_startup_config_line(enabled)
    assert not invalid.is_valid
    assert any("must not exceed" in error for error in invalid.errors)
    print("  private voice overlap: explicit opt-in with bounded split/timing")


def test_private_voice_overlap_pcm_is_on_and_fail_closed() -> None:
    default = _contract(env={})
    disabled = _contract(
        env={"NANA_PRIVATE_VOICE_OVERLAP_PCM_ENABLED": "0"}
    )
    enabled = _contract(
        env={
            "NANA_PRIVATE_VOICE_OVERLAP_PCM_ENABLED": "1",
            "NANA_PRIVATE_VOICE_OVERLAP_PCM_OUTPUT_FORMAT": "pcm_24000",
            "NANA_PRIVATE_VOICE_OVERLAP_PCM_START_BUFFER_MS": "300",
            "NANA_PRIVATE_VOICE_OVERLAP_PCM_NETWORK_CHUNK_BYTES": "4096",
            "NANA_PRIVATE_VOICE_OVERLAP_PCM_TIMEOUT_S": "45",
        }
    )
    invalid_format = _contract(
        env={
            "NANA_PRIVATE_VOICE_OVERLAP_PCM_ENABLED": "1",
            "NANA_PRIVATE_VOICE_OVERLAP_PCM_OUTPUT_FORMAT": "pcm_44100",
        }
    )
    inert = _contract(
        env={
            "NANA_PRIVATE_VOICE_OVERLAP_ENABLED": "0",
            "NANA_PRIVATE_VOICE_OVERLAP_PCM_ENABLED": "1",
        }
    )

    assert default.snapshot.private_voice_overlap_pcm_enabled is True
    assert "overlap_pcm=on:pcm_24000" in format_startup_config_line(default)
    assert default.snapshot.voice_http_keepalive_enabled is True
    assert default.snapshot.voice_http_pool_maxsize == 8
    assert "voice_http=keepalive:8" in format_startup_config_line(default)
    assert disabled.is_valid
    assert disabled.snapshot.private_voice_overlap_pcm_enabled is False
    assert "overlap_pcm=off:pcm_24000" in format_startup_config_line(disabled)
    assert enabled.is_valid and enabled.status == "valid", enabled
    assert enabled.snapshot.private_voice_overlap_pcm_enabled is True
    assert enabled.snapshot.private_voice_overlap_pcm_output_format == "pcm_24000"
    assert enabled.snapshot.private_voice_overlap_pcm_start_buffer_ms == 300
    assert enabled.snapshot.private_voice_overlap_pcm_network_chunk_bytes == 4096
    assert enabled.snapshot.private_voice_overlap_pcm_timeout_seconds == 45
    assert "overlap_pcm=on:pcm_24000" in format_startup_config_line(enabled)
    assert not invalid_format.is_valid
    assert any(
        "NANA_PRIVATE_VOICE_OVERLAP_PCM_OUTPUT_FORMAT" in value
        for value in invalid_format.errors
    )
    assert inert.is_valid and inert.status == "degraded"
    assert any("overlap PCM is inert" in value for value in inert.warnings)
    print("  HTTP overlap PCM: ON default, pooled, PCM-only, and fail-closed")


def test_private_voice_ttd_is_off_by_default_and_validated() -> None:
    default = _contract(env={})
    enabled = _contract(
        env={
            "NANA_PRIVATE_VOICE_TTD_ENABLED": "1",
            "NANA_PRIVATE_VOICE_TTD_MODEL": "eleven_v3",
            "NANA_PRIVATE_VOICE_TTD_INPUT_MODE": "incremental",
            "NANA_PRIVATE_VOICE_TTD_OUTPUT_FORMAT": "pcm_24000",
            "NANA_PRIVATE_VOICE_TTD_MIN_CHARS": "40",
            "NANA_PRIVATE_VOICE_TTD_MIN_WORDS": "8",
            "NANA_PRIVATE_VOICE_TTD_CHUNK_TARGET_CHARS": "120",
            "NANA_PRIVATE_VOICE_TTD_CHUNK_MAX_CHARS": "240",
            "NANA_PRIVATE_VOICE_TTD_START_BUFFER_MS": "300",
            "NANA_PRIVATE_VOICE_TTD_TIMEOUT_S": "45",
        }
    )
    invalid_model = _contract(
        env={
            "NANA_PRIVATE_VOICE_TTD_ENABLED": "1",
            "NANA_PRIVATE_VOICE_TTD_MODEL": "eleven_flash_v2_5",
        }
    )
    invalid_format = _contract(
        env={
            "NANA_PRIVATE_VOICE_TTD_ENABLED": "1",
            "NANA_PRIVATE_VOICE_TTD_OUTPUT_FORMAT": "pcm_44100",
        }
    )
    invalid_mode = _contract(
        env={
            "NANA_PRIVATE_VOICE_TTD_ENABLED": "1",
            "NANA_PRIVATE_VOICE_TTD_INPUT_MODE": "mystery",
        }
    )
    invalid_bounds = _contract(
        env={
            "NANA_PRIVATE_VOICE_TTD_ENABLED": "1",
            "NANA_PRIVATE_VOICE_TTD_MIN_CHARS": "140",
            "NANA_PRIVATE_VOICE_TTD_CHUNK_MAX_CHARS": "100",
        }
    )

    assert default.snapshot.private_voice_ttd_enabled is False
    assert "voice_ttd=off:eleven_v3/pcm_24000" in format_startup_config_line(default)
    assert enabled.is_valid, enabled.errors
    assert enabled.snapshot.private_voice_ttd_enabled is True
    assert enabled.snapshot.private_voice_ttd_model == "eleven_v3"
    assert enabled.snapshot.private_voice_ttd_input_mode == "incremental"
    assert enabled.snapshot.private_voice_ttd_output_format == "pcm_24000"
    assert enabled.snapshot.private_voice_ttd_min_chars == 40
    assert enabled.snapshot.private_voice_ttd_min_words == 8
    assert enabled.snapshot.private_voice_ttd_chunk_target_chars == 120
    assert enabled.snapshot.private_voice_ttd_chunk_max_chars == 240
    assert enabled.snapshot.private_voice_ttd_start_buffer_ms == 300
    assert enabled.snapshot.private_voice_ttd_timeout_seconds == 45
    assert "voice_ttd=on:eleven_v3/pcm_24000" in format_startup_config_line(enabled)
    assert not invalid_model.is_valid
    assert any("NANA_PRIVATE_VOICE_TTD_MODEL" in value for value in invalid_model.errors)
    assert not invalid_format.is_valid
    assert any("NANA_PRIVATE_VOICE_TTD_OUTPUT_FORMAT" in value for value in invalid_format.errors)
    assert not invalid_mode.is_valid
    assert any("NANA_PRIVATE_VOICE_TTD_INPUT_MODE" in value for value in invalid_mode.errors)
    assert not invalid_bounds.is_valid
    assert any("TTD_MIN_CHARS" in value for value in invalid_bounds.errors)
    print("  private voice TTD: OFF pilot, v3/PCM-only, and bounded")


def test_autonomy_disabled_literals_match_runtime() -> None:
    from nana.autonomy import llm_banter

    previous = os.environ.get("NANA_AUTONOMY_LLM_DISABLED")
    try:
        for literal in ("0", "false", "no", "off", "1", "true", "yes", "on"):
            os.environ["NANA_AUTONOMY_LLM_DISABLED"] = literal
            runtime_enabled = llm_banter._env_enabled()
            contract = _contract(
                env={"NANA_AUTONOMY_LLM_DISABLED": literal}
            )
            assert contract.is_valid, (literal, contract.errors)
            assert contract.snapshot.autonomy_enabled is runtime_enabled, literal
    finally:
        if previous is None:
            os.environ.pop("NANA_AUTONOMY_LLM_DISABLED", None)
        else:
            os.environ["NANA_AUTONOMY_LLM_DISABLED"] = previous
    print("  autonomy disabled: 0/false/no/off/1/true/yes/on parity")


def test_enabled_autonomy_requires_model() -> None:
    enabled = _contract(
        env={
            "NANA_AUTONOMY_LLM_DISABLED": "0",
            "NANA_AUTONOMY_LLM_MODEL": "   ",
        }
    )
    disabled = _contract(
        env={
            "NANA_AUTONOMY_LLM_DISABLED": "1",
            "NANA_AUTONOMY_LLM_MODEL": "   ",
        }
    )
    assert not enabled.is_valid
    assert any("NANA_AUTONOMY_LLM_MODEL" in error for error in enabled.errors)
    assert disabled.is_valid
    print("  autonomy model: required only while enabled")


def test_autonomy_auto_output_defaults_off_and_accepts_explicit_override() -> None:
    default = _contract(env={})
    enabled = _contract(env={"NANA_AUTONOMY_AUTO_OUTPUT_ENABLED": "1"})

    assert default.snapshot.autonomy_auto_output_enabled is False
    assert "auto_output=off" in format_startup_config_line(default)
    assert enabled.snapshot.autonomy_auto_output_enabled is True
    assert "auto_output=on" in format_startup_config_line(enabled)
    print("  autonomy output: silent by default with explicit opt-in")


def test_legacy_cabled_presence_env_is_inert() -> None:
    contract = _contract(
        env={
            "NANA_PRESENCE_V1_ENABLED": "1",
            "NANA_PRESENCE_SERIAL_PORT": "COM17",
            "NANA_PRESENCE_SERIAL_BAUD": "921600",
            "NANA_PRESENCE_CAMERA_ENABLED": "1",
            "NANA_PRESENCE_CAMERA_INTERVAL_SECONDS": "0.1",
        }
    )
    rendered = format_startup_config_line(contract)

    assert contract.is_valid, contract.errors
    assert "presence_v1" not in rendered
    assert "presence_camera" not in rendered
    assert "COM17" not in rendered
    assert "921600" not in rendered
    print("  retired cable env: ignored by the runtime contract")


def test_presence_session_requires_auth_and_bounded_liveness() -> None:
    enabled = _contract(
        env={
            "NANA_PRESENCE_SESSION_ENABLED": "1",
            "NANA_PRESENCE_SESSION_HOST": "127.0.0.1",
            "NANA_PRESENCE_SESSION_PORT": "8765",
            "NANA_PRESENCE_SESSION_TOKEN": "private-test-token",
            "NANA_PRESENCE_SESSION_HEARTBEAT_SECONDS": "5",
            "NANA_PRESENCE_SESSION_TIMEOUT_SECONDS": "15",
        }
    )
    missing_token = _contract(
        env={"NANA_PRESENCE_SESSION_ENABLED": "1"}
    )
    bad_port = _contract(
        env={
            "NANA_PRESENCE_SESSION_ENABLED": "1",
            "NANA_PRESENCE_SESSION_TOKEN": "private-test-token",
            "NANA_PRESENCE_SESSION_PORT": "70000",
        }
    )
    bad_timeout = _contract(
        env={
            "NANA_PRESENCE_SESSION_ENABLED": "1",
            "NANA_PRESENCE_SESSION_TOKEN": "private-test-token",
            "NANA_PRESENCE_SESSION_HEARTBEAT_SECONDS": "5",
            "NANA_PRESENCE_SESSION_TIMEOUT_SECONDS": "5",
        }
    )

    assert enabled.is_valid, enabled.errors
    assert enabled.snapshot.presence_session_enabled is True
    assert enabled.snapshot.presence_session_token_present is True
    assert "presence_session=on:127.0.0.1:8765" in format_startup_config_line(enabled)
    assert not missing_token.is_valid
    assert any("NANA_PRESENCE_SESSION_TOKEN" in error for error in missing_token.errors)
    assert not bad_port.is_valid
    assert any("at most 65535" in error for error in bad_port.errors)
    assert not bad_timeout.is_valid
    assert any("must exceed heartbeat" in error for error in bad_timeout.errors)
    print("  presence session: authenticated and bounded liveness contract")


def test_presence_contract_exposes_session_only() -> None:
    fields = set(_contract(env={}).snapshot.to_dict())
    retired_fields = {
        "presence_v1_enabled",
        "presence_serial_port",
        "presence_serial_baud",
        "presence_camera_enabled",
        "presence_camera_config_path",
        "presence_camera_interval_seconds",
        "presence_camera_model_path",
    }

    assert "presence_session_enabled" in fields
    assert "presence_session_host" in fields
    assert "presence_session_port" in fields
    assert fields.isdisjoint(retired_fields)
    print("  presence contract: Wi-Fi session is the only runtime transport")


def test_invalid_bool_and_numbers_are_validation_errors() -> None:
    contract = _contract(
        env={
            "NANA_AUTONOMY_AUTO_OUTPUT_ENABLED": "perhaps",
            "NANA_VOICE_STREAM_CALLBACK_OUTPUT_ENABLED": "sometimes",
            "NANA_EXTERNAL_BRIDGE_ENABLED": "perhaps",
            "NANA_EXTERNAL_BRIDGE_POLL_SECONDS": "fast",
            "NANA_PRESENCE_SESSION_ENABLED": "perhaps",
            "NANA_PRESENCE_SESSION_PORT": "many",
            "NANA_PRESENCE_SESSION_HEARTBEAT_SECONDS": "often",
            "NANA_PRESENCE_SESSION_TIMEOUT_SECONDS": "later",
            "NANA_AUTONOMY_STOP_JOIN_TIMEOUT_S": "0",
            "NANA_AUTONOMY_POLLER_JOIN_TIMEOUT_S": "nan",
        }
    )
    joined = "\n".join(contract.errors)
    assert contract.status == "invalid" and not contract.is_valid
    for name in (
        "NANA_AUTONOMY_AUTO_OUTPUT_ENABLED",
        "NANA_VOICE_STREAM_CALLBACK_OUTPUT_ENABLED",
        "NANA_EXTERNAL_BRIDGE_ENABLED",
        "NANA_EXTERNAL_BRIDGE_POLL_SECONDS",
        "NANA_PRESENCE_SESSION_ENABLED",
        "NANA_PRESENCE_SESSION_PORT",
        "NANA_PRESENCE_SESSION_HEARTBEAT_SECONDS",
        "NANA_PRESENCE_SESSION_TIMEOUT_SECONDS",
        "NANA_AUTONOMY_STOP_JOIN_TIMEOUT_S",
        "NANA_AUTONOMY_POLLER_JOIN_TIMEOUT_S",
    ):
        assert name in joined, (name, contract.errors)
    print("  strict env: invalid bool/number captured without exception")


def test_contradictory_voice_flags_are_degraded_not_fatal() -> None:
    contract = _contract(
        VOICE_STREAMING_ENABLED=False,
        VOICE_STREAMING_PILOT_ENABLED=True,
        VOICE_STREAMING_DIRECT_ONLY=True,
        VOICE_STREAMING_KILL_SWITCH=True,
    )
    joined = "\n".join(contract.warnings)
    assert contract.is_valid and contract.status == "degraded"
    assert contract.errors == ()
    assert "pilot" in joined
    assert "direct-only" in joined
    assert "callback output is inert" in joined
    assert "kill switch" in joined
    print(f"  feature flags: degraded with warnings={len(contract.warnings)}")


def test_app_prints_each_degraded_warning() -> None:
    from nana.cli import app

    degraded = _contract(
        VOICE_STREAMING_ENABLED=False,
        VOICE_STREAMING_PILOT_ENABLED=True,
        VOICE_STREAMING_DIRECT_ONLY=True,
        VOICE_STREAMING_KILL_SWITCH=True,
    )

    class StopAfterWarnings(Exception):
        pass

    async def harmless_shutdown(*_args, **_kwargs):
        return True

    original_build = app.build_startup_config_contract
    original_voice = app._create_voice_engine
    original_shutdown = app._shutdown_runtime
    original_clear = app.vision_previewer.clear_preview_cache
    try:
        app.build_startup_config_contract = lambda: degraded
        app._create_voice_engine = lambda: (_ for _ in ()).throw(StopAfterWarnings())
        app._shutdown_runtime = harmless_shutdown
        app.vision_previewer.clear_preview_cache = lambda: 0
        output = io.StringIO()
        with _app_lazy_import_fixtures(), redirect_stdout(output):
            asyncio.run(app.main())
    finally:
        app.build_startup_config_contract = original_build
        app._create_voice_engine = original_voice
        app._shutdown_runtime = original_shutdown
        app.vision_previewer.clear_preview_cache = original_clear

    rendered = output.getvalue()
    assert "status=degraded" in rendered
    for warning in degraded.warnings:
        assert f"Startup config warning: {warning}" in rendered
    print("  app: every degraded warning printed")


def test_app_fails_before_runtime_construction() -> None:
    from nana.cli import app

    invalid = _contract(NANA_CHAT_PROVIDER="unsupported")
    calls: list[str] = []

    def forbidden(name):
        def _call(*_args, **_kwargs):
            calls.append(name)
            raise AssertionError(f"runtime construction attempted: {name}")

        return _call

    original_build = app.build_startup_config_contract
    original_voice = app._create_voice_engine
    original_vts = app._get_vts_runtime
    original_autonomy_start = app.AUTONOMY_LOOP.start
    original_bridge = app._start_external_bridge_worker
    try:
        app.build_startup_config_contract = lambda: invalid
        app._create_voice_engine = forbidden("voice")
        app._get_vts_runtime = forbidden("vts")
        app.AUTONOMY_LOOP.start = forbidden("autonomy")
        app._start_external_bridge_worker = forbidden("bridge")
        output = io.StringIO()
        with redirect_stdout(output):
            asyncio.run(app.main())
    finally:
        app.build_startup_config_contract = original_build
        app._create_voice_engine = original_voice
        app._get_vts_runtime = original_vts
        app.AUTONOMY_LOOP.start = original_autonomy_start
        app._start_external_bridge_worker = original_bridge

    rendered = output.getvalue()
    assert calls == []
    assert "status=invalid" in rendered
    assert "Startup config error:" in rendered
    print("  app: invalid contract fails before voice/VTS/autonomy/bridge")


def test_invalid_bridge_poll_does_not_break_app_import() -> None:
    env = {
        name: os.environ[name]
        for name in (
            "SystemRoot",
            "WINDIR",
            "PATH",
            "TEMP",
            "TMP",
            "COMSPEC",
            "PATHEXT",
        )
        if name in os.environ
    }
    env.update(
        {
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": str(ROOT) + os.pathsep + env.get("PYTHONPATH", ""),
            "USERPROFILE": str(ROOT),
            "NANA_EXTERNAL_BRIDGE_POLL_SECONDS": "not-a-number",
            "OPENAI_API_KEY": "test-openai-secret",
            "ELEVEN_API_KEY": "test-eleven-secret",
            "ELEVEN_VOICE_ID": "test-voice-id",
            "NANA_CONTEXT_PRIVATE_MODE": "legacy",
            "NANA_CONTEXT_PUBLIC_GPT_MODE": "legacy",
            "NANA_CONTEXT_CUM2_MODE": "legacy",
            "NANA_CONTEXT_AUTONOMY_MODE": "legacy",
            "NANA_CONTEXT_BUDGET_POLICY_REVISION": "",
        }
    )
    code = r"""
import asyncio
from pathlib import Path
import sys
import types

root = (Path.cwd() / 'nana').resolve()
data_root = (root / 'data').resolve()
log_root = (root / 'runtime_logs').resolve()

def protected(value):
    if isinstance(value, int):
        return False
    try:
        path = Path(value).resolve()
    except (OSError, TypeError, ValueError):
        return False
    folded = str(path).casefold()
    return (
        path.name.casefold() in {'.env', 'token.txt'}
        or folded.startswith(str(data_root).casefold())
        or folded.startswith(str(log_root).casefold())
        or '\\.factory\\settings.json' in folded
    )

def audit(event, args):
    if event == 'open' and args and protected(args[0]):
        raise PermissionError('protected child path')
    if event == 'socket.connect':
        caller = sys._getframe(1)
        address = args[1] if len(args) > 1 else None
        if (caller.f_code.co_name in {'socketpair', '_fallback_socketpair'}
                and Path(caller.f_code.co_filename).name == 'socket.py'
                and isinstance(address, tuple) and address
                and address[0] in {'127.0.0.1', '::1'}):
            return
    if event in {'socket.connect', 'socket.getaddrinfo'}:
        raise PermissionError('network disabled')
    if event in {'subprocess.Popen', 'os.system', 'os.posix_spawn'}:
        raise PermissionError('nested process disabled')

sys.addaudithook(audit)
original_exists = Path.exists
Path.exists = lambda path: False if path.name.casefold() == '.env' else original_exists(path)
nana_package = types.ModuleType('nana')
nana_package.__path__ = [str(root)]
runtime_package = types.ModuleType('nana.runtime')
runtime_package.__path__ = [str(root / 'runtime')]
sys.modules['nana'] = nana_package
sys.modules['nana.runtime'] = runtime_package
from nana.cli import app
Path.exists = original_exists

calls = []
def forbidden(name):
    def call(*args, **kwargs):
        calls.append(name)
        raise AssertionError(name)
    return call

app._create_voice_engine = forbidden('voice')
app._get_vts_runtime = forbidden('vts')
app.AUTONOMY_LOOP.start = forbidden('autonomy')
app._start_external_bridge_worker = forbidden('bridge')
asyncio.run(app.main())
assert calls == [], calls
print('invalid-poll-fail-fast')
"""
    global _ALLOW_INTERNAL_CHILD
    _ALLOW_INTERNAL_CHILD = True
    try:
        result = subprocess.run(
            [sys.executable, "-B", "-c", code],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    finally:
        _ALLOW_INTERNAL_CHILD = False
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "invalid-poll-fail-fast" in result.stdout
    assert "NANA_EXTERNAL_BRIDGE_POLL_SECONDS" in result.stdout
    print("  import: invalid bridge poll reaches contract, not import crash")


def main() -> None:
    tests = [
        test_current_and_default_contract_are_valid,
        test_private_web_defaults_on_and_explicit_off_remains_available,
        test_context_modes_default_legacy_and_fail_closed,
        test_snapshot_is_immutable,
        test_snapshot_and_summary_do_not_expose_secrets,
        test_unsupported_provider_is_fatal,
        test_llmgate_requires_main_model,
        test_main_model_is_resolved_by_provider,
        test_openai_key_readiness_is_fatal_and_sanitized,
        test_private_fast_lane_is_explicit_and_fail_closed,
        test_private_voice_overlap_is_explicit_and_bounded,
        test_private_voice_overlap_pcm_is_on_and_fail_closed,
        test_private_voice_ttd_is_off_by_default_and_validated,
        test_autonomy_disabled_literals_match_runtime,
        test_enabled_autonomy_requires_model,
        test_autonomy_auto_output_defaults_off_and_accepts_explicit_override,
        test_legacy_cabled_presence_env_is_inert,
        test_presence_session_requires_auth_and_bounded_liveness,
        test_presence_contract_exposes_session_only,
        test_invalid_bool_and_numbers_are_validation_errors,
        test_contradictory_voice_flags_are_degraded_not_fatal,
        test_app_prints_each_degraded_warning,
        test_app_fails_before_runtime_construction,
        test_invalid_bridge_poll_does_not_break_app_import,
    ]
    for index, test in enumerate(tests, 1):
        print(f"[{index}/{len(tests)}] {test.__name__}")
        test()
    print(f"smoke_core_config_contract: PASS ({len(tests)}/{len(tests)})")


if __name__ == "__main__":
    main()
