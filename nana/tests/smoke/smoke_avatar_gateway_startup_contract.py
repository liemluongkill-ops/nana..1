"""Offline startup-contract checks for the semantic avatar gateway."""

from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _config():
    return SimpleNamespace(
        NANA_CHAT_PROVIDER="llmgate",
        LLMGATE_MAIN_MODEL="gpt-5.6-terra",
        LLMGATE_MAIN_REASONING_EFFORT="none",
        NANA_OPENAI_FALLBACK_ENABLED=False,
        OPENAI_API_KEY="test-openai-key",
        NANA_AUTONOMY_LLM_MODEL="nana-banter",
        AVATAR_GATEWAY_ENABLED=False,
        AVATAR_GATEWAY_HOST="127.0.0.1",
        AVATAR_GATEWAY_PORT=8766,
        AVATAR_GATEWAY_TRANSPORT="recording",
        AVATAR_GATEWAY_PENDING_LIMIT=1,
        AVATAR_GATEWAY_TIMEOUT_S=2.0,
        AVATAR_GATEWAY_AUTO_EVENTS_ENABLED=False,
        AVATAR_GATEWAY_TOKEN="",
        WARUDO_WS_URL="",
    )


def _contract(env=None, **overrides):
    from nana.runtime.startup_config_contract import build_startup_config_contract

    values = _config()
    for key, value in overrides.items():
        setattr(values, key, value)
    return build_startup_config_contract(env={} if env is None else env, config_source=values)


def test_default_gateway_is_off_and_sanitized():
    from nana.runtime.startup_config_contract import format_startup_config_line

    contract = _contract()
    assert contract.is_valid, contract.errors
    assert contract.snapshot.avatar_gateway_enabled is False
    assert contract.snapshot.avatar_gateway_token_present is False
    assert "avatar=off:127.0.0.1:8766/recording" in format_startup_config_line(contract)
    print("  default: gateway off and token-free status")


def test_recording_gateway_can_be_enabled():
    contract = _contract(
        env={
            "NANA_AVATAR_GATEWAY_ENABLED": "1",
            "NANA_AVATAR_GATEWAY_TRANSPORT": "recording",
            "NANA_AVATAR_GATEWAY_PORT": "8767",
        }
    )
    assert contract.is_valid, contract.errors
    assert contract.snapshot.avatar_gateway_enabled is True
    assert contract.snapshot.avatar_gateway_port == 8767
    assert contract.snapshot.avatar_gateway_transport == "recording"
    print("  recording: explicit enable accepted")


def test_warudo_transport_requires_endpoint():
    contract = _contract(
        env={
            "NANA_AVATAR_GATEWAY_ENABLED": "1",
            "NANA_AVATAR_GATEWAY_TRANSPORT": "warudo_ws",
        }
    )
    assert not contract.is_valid, contract.errors
    assert any("NANA_WARUDO_WS_URL" in error for error in contract.errors)
    print("  warudo: missing endpoint fails closed")


def test_gateway_host_is_loopback_only():
    contract = _contract(env={"NANA_AVATAR_GATEWAY_HOST": "0.0.0.0"})
    assert not contract.is_valid, contract.errors
    assert any("loopback-only" in error for error in contract.errors)
    print("  host: non-loopback rejected")


def run_smoke_tests() -> bool:
    tests = [
        test_default_gateway_is_off_and_sanitized,
        test_recording_gateway_can_be_enabled,
        test_warudo_transport_requires_endpoint,
        test_gateway_host_is_loopback_only,
    ]
    passed = 0
    failed = 0
    print("Avatar Gateway Startup Contract — Offline Smoke Tests")
    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as exc:
            print(f"  FAILED: {exc}")
            failed += 1
        except Exception as exc:
            print(f"  ERROR: {type(exc).__name__}: {exc}")
            failed += 1
    print(f"Results: {passed} passed, {failed} failed")
    return failed == 0


if __name__ == "__main__":
    raise SystemExit(0 if run_smoke_tests() else 1)
