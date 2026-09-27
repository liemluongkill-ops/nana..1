"""Sanitized startup configuration snapshot and validation contract.

This module deliberately covers only configuration that decides which Nana
runtime services start. It does not replace ``nana.config`` and never exposes
secret values.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import os
from typing import Any, Mapping

from nana import config as nana_config


_ALLOWED_PROVIDERS = {"llmgate", "openai", "official_openai"}
_ALLOWED_REASONING_EFFORTS = {
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
}
_TRUE_LITERALS = {"1", "true", "yes", "on"}
_FALSE_LITERALS = {"0", "false", "no", "off"}
_AUTONOMY_DISABLED_LITERALS = {"1", "true", "yes", "on", "off"}
_AUTONOMY_ENABLED_LITERALS = {"0", "false", "no"}
_SECRET_PLACEHOLDERS = {
    "",
    "ELEVENLABS_KEY_CUA_BAN",
    "OPENAI_KEY_CUA_BAN",
    "YOUR_API_KEY",
    "YOUR_ELEVEN_API_KEY",
}


@dataclass(frozen=True)
class StartupConfigSnapshot:
    provider: str
    main_model: str
    reasoning_effort: str
    openai_fallback_enabled: bool
    llm_fast_private_enabled: bool
    llm_fast_private_model: str
    llm_fast_private_max_input_chars: int
    llm_fast_private_max_tokens: int
    autonomy_enabled: bool
    autonomy_model: str
    autonomy_auto_output_enabled: bool
    voice_streaming_enabled: bool
    voice_streaming_pilot_enabled: bool
    voice_streaming_kill_switch: bool
    voice_streaming_direct_only: bool
    voice_stream_callback_output_enabled: bool
    private_voice_overlap_enabled: bool
    private_voice_overlap_min_chars: int
    private_voice_overlap_max_chars: int
    private_voice_overlap_coalesce_ms: int
    private_voice_overlap_tail_timeout_seconds: int
    private_voice_overlap_pcm_enabled: bool
    private_voice_overlap_pcm_output_format: str
    private_voice_overlap_pcm_start_buffer_ms: int
    private_voice_overlap_pcm_network_chunk_bytes: int
    private_voice_overlap_pcm_timeout_seconds: int
    voice_http_keepalive_enabled: bool
    voice_http_pool_maxsize: int
    private_voice_ttd_enabled: bool
    private_voice_ttd_model: str
    private_voice_ttd_input_mode: str
    private_voice_ttd_output_format: str
    private_voice_ttd_min_chars: int
    private_voice_ttd_min_words: int
    private_voice_ttd_chunk_target_chars: int
    private_voice_ttd_chunk_max_chars: int
    private_voice_ttd_start_buffer_ms: int
    private_voice_ttd_timeout_seconds: int
    private_voice_ttd_capture_enabled: bool
    voice_debug_no_tts: bool
    voice_test_mode: bool
    voice_output_format: str
    openai_key_present: bool
    eleven_key_present: bool
    voice_id_present: bool
    vts_startup_enabled: bool
    external_bridge_enabled: bool
    external_bridge_poll_seconds: float
    presence_session_enabled: bool
    presence_session_host: str
    presence_session_port: int
    presence_session_token_present: bool
    presence_session_heartbeat_seconds: float
    presence_session_timeout_seconds: float
    autonomy_stop_join_timeout_seconds: float
    autonomy_poller_join_timeout_seconds: float
    parse_errors: tuple[str, ...] = ()
    avatar_gateway_enabled: bool = False
    avatar_gateway_host: str = "127.0.0.1"
    avatar_gateway_port: int = 8766
    avatar_gateway_transport: str = "recording"
    avatar_gateway_pending_limit: int = 1
    avatar_gateway_timeout_seconds: float = 2.0
    avatar_gateway_auto_events_enabled: bool = False
    avatar_gateway_token_present: bool = False
    warudo_ws_url_present: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Return a serialization-safe view containing no secret material."""
        return asdict(self)


@dataclass(frozen=True)
class StartupConfigContract:
    snapshot: StartupConfigSnapshot
    errors: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def is_valid(self) -> bool:
        return not self.errors

    @property
    def status(self) -> str:
        if self.errors:
            return "invalid"
        if self.warnings:
            return "degraded"
        return "valid"


def _configured_secret_present(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(text and text.upper() not in _SECRET_PLACEHOLDERS)


def _runtime_autonomy_enabled(
    env: Mapping[str, str],
    errors: list[str],
) -> bool:
    """Match llm_banter._env_enabled without changing its legacy semantics."""
    name = "NANA_AUTONOMY_LLM_DISABLED"
    if name not in env:
        return True
    raw = str(env[name]).strip().lower()
    if raw in _AUTONOMY_DISABLED_LITERALS:
        return False
    if raw in _AUTONOMY_ENABLED_LITERALS:
        return True
    errors.append(f"{name}: invalid boolean literal")
    return True


def _strict_env_bool(
    env: Mapping[str, str],
    name: str,
    default: bool,
    errors: list[str],
) -> bool:
    if name not in env:
        return bool(default)
    raw = str(env[name]).strip().lower()
    if raw in _TRUE_LITERALS:
        return True
    if raw in _FALSE_LITERALS:
        return False
    errors.append(f"{name}: invalid boolean literal")
    return bool(default)


def _strict_env_positive_float(
    env: Mapping[str, str],
    name: str,
    default: float,
    errors: list[str],
) -> float:
    if name not in env:
        return float(default)
    try:
        value = float(str(env[name]).strip())
    except (TypeError, ValueError):
        errors.append(f"{name}: invalid number literal")
        return float(default)
    if not math.isfinite(value) or value <= 0.0:
        errors.append(f"{name}: must be a positive finite number")
    return value


def _strict_env_positive_int(
    env: Mapping[str, str],
    name: str,
    default: int,
    errors: list[str],
) -> int:
    if name not in env:
        return int(default)
    try:
        value = int(str(env[name]).strip())
    except (TypeError, ValueError):
        errors.append(f"{name}: invalid integer literal")
        return int(default)
    if value <= 0:
        errors.append(f"{name}: must be a positive integer")
    return value


def _strict_env_nonnegative_int(
    env: Mapping[str, str],
    name: str,
    default: int,
    errors: list[str],
) -> int:
    if name not in env:
        return int(default)
    try:
        value = int(str(env[name]).strip())
    except (TypeError, ValueError):
        errors.append(f"{name}: invalid integer literal")
        return int(default)
    if value < 0:
        errors.append(f"{name}: must be nonnegative")
    return value


def build_startup_config_snapshot(
    *,
    env: Mapping[str, str] | None = None,
    config_source: Any | None = None,
) -> StartupConfigSnapshot:
    """Build one immutable, sanitized view of startup-critical settings."""
    source_env = os.environ if env is None else env
    config = nana_config if config_source is None else config_source
    parse_errors: list[str] = []

    autonomy_enabled = _runtime_autonomy_enabled(source_env, parse_errors)
    autonomy_auto_output_enabled = _strict_env_bool(
        source_env,
        "NANA_AUTONOMY_AUTO_OUTPUT_ENABLED",
        False,
        parse_errors,
    )
    callback_output = _strict_env_bool(
        source_env,
        "NANA_VOICE_STREAM_CALLBACK_OUTPUT_ENABLED",
        True,
        parse_errors,
    )
    bridge_enabled = _strict_env_bool(
        source_env,
        "NANA_EXTERNAL_BRIDGE_ENABLED",
        True,
        parse_errors,
    )
    bridge_poll = _strict_env_positive_float(
        source_env,
        "NANA_EXTERNAL_BRIDGE_POLL_SECONDS",
        0.35,
        parse_errors,
    )
    avatar_gateway_enabled = _strict_env_bool(
        source_env,
        "NANA_AVATAR_GATEWAY_ENABLED",
        bool(getattr(config, "AVATAR_GATEWAY_ENABLED", False)),
        parse_errors,
    )
    avatar_gateway_host = str(
        source_env.get(
            "NANA_AVATAR_GATEWAY_HOST",
            getattr(config, "AVATAR_GATEWAY_HOST", "127.0.0.1"),
        )
        or ""
    ).strip()
    avatar_gateway_port = _strict_env_positive_int(
        source_env,
        "NANA_AVATAR_GATEWAY_PORT",
        int(getattr(config, "AVATAR_GATEWAY_PORT", 8766)),
        parse_errors,
    )
    avatar_gateway_transport = str(
        source_env.get(
            "NANA_AVATAR_GATEWAY_TRANSPORT",
            getattr(config, "AVATAR_GATEWAY_TRANSPORT", "recording"),
        )
        or ""
    ).strip().lower()
    avatar_gateway_pending_limit = _strict_env_nonnegative_int(
        source_env,
        "NANA_AVATAR_GATEWAY_PENDING_LIMIT",
        int(getattr(config, "AVATAR_GATEWAY_PENDING_LIMIT", 1)),
        parse_errors,
    )
    avatar_gateway_timeout_seconds = _strict_env_positive_float(
        source_env,
        "NANA_AVATAR_GATEWAY_TIMEOUT_S",
        float(getattr(config, "AVATAR_GATEWAY_TIMEOUT_S", 2.0)),
        parse_errors,
    )
    avatar_gateway_auto_events_enabled = _strict_env_bool(
        source_env,
        "NANA_AVATAR_GATEWAY_AUTO_EVENTS_ENABLED",
        bool(getattr(config, "AVATAR_GATEWAY_AUTO_EVENTS_ENABLED", False)),
        parse_errors,
    )
    avatar_gateway_token_present = _configured_secret_present(
        source_env.get(
            "NANA_AVATAR_GATEWAY_TOKEN",
            getattr(config, "AVATAR_GATEWAY_TOKEN", ""),
        )
    )
    warudo_ws_url_present = bool(
        str(
            source_env.get(
                "NANA_WARUDO_WS_URL",
                getattr(config, "WARUDO_WS_URL", ""),
            )
            or ""
        ).strip()
    )
    presence_session_enabled = _strict_env_bool(
        source_env,
        "NANA_PRESENCE_SESSION_ENABLED",
        False,
        parse_errors,
    )
    presence_session_host = str(
        source_env.get("NANA_PRESENCE_SESSION_HOST", "0.0.0.0") or ""
    ).strip()
    presence_session_port = _strict_env_positive_int(
        source_env,
        "NANA_PRESENCE_SESSION_PORT",
        8765,
        parse_errors,
    )
    presence_session_token_present = _configured_secret_present(
        source_env.get("NANA_PRESENCE_SESSION_TOKEN", "")
    )
    presence_session_heartbeat = _strict_env_positive_float(
        source_env,
        "NANA_PRESENCE_SESSION_HEARTBEAT_SECONDS",
        5.0,
        parse_errors,
    )
    presence_session_timeout = _strict_env_positive_float(
        source_env,
        "NANA_PRESENCE_SESSION_TIMEOUT_SECONDS",
        15.0,
        parse_errors,
    )
    autonomy_join = _strict_env_positive_float(
        source_env,
        "NANA_AUTONOMY_STOP_JOIN_TIMEOUT_S",
        65.0,
        parse_errors,
    )
    poller_join = _strict_env_positive_float(
        source_env,
        "NANA_AUTONOMY_POLLER_JOIN_TIMEOUT_S",
        2.0,
        parse_errors,
    )
    llm_fast_private_enabled = _strict_env_bool(
        source_env,
        "NANA_LLM_FAST_PRIVATE_ENABLED",
        bool(getattr(config, "LLM_FAST_PRIVATE_ENABLED", False)),
        parse_errors,
    )
    llm_fast_private_model = str(
        source_env.get(
            "NANA_LLM_FAST_PRIVATE_MODEL",
            getattr(config, "LLM_FAST_PRIVATE_MODEL", "gemini-3-flash"),
        )
        or ""
    ).strip()
    llm_fast_private_max_input_chars = _strict_env_positive_int(
        source_env,
        "NANA_LLM_FAST_PRIVATE_MAX_INPUT_CHARS",
        int(getattr(config, "LLM_FAST_PRIVATE_MAX_INPUT_CHARS", 220)),
        parse_errors,
    )
    llm_fast_private_max_tokens = _strict_env_positive_int(
        source_env,
        "NANA_LLM_FAST_PRIVATE_MAX_TOKENS",
        int(getattr(config, "LLM_FAST_PRIVATE_MAX_TOKENS", 96)),
        parse_errors,
    )
    private_voice_overlap_enabled = _strict_env_bool(
        source_env,
        "NANA_PRIVATE_VOICE_OVERLAP_ENABLED",
        bool(getattr(config, "PRIVATE_VOICE_OVERLAP_ENABLED", False)),
        parse_errors,
    )
    private_voice_overlap_min_chars = _strict_env_positive_int(
        source_env,
        "NANA_PRIVATE_VOICE_OVERLAP_MIN_CHARS",
        int(getattr(config, "PRIVATE_VOICE_OVERLAP_MIN_CHARS", 45)),
        parse_errors,
    )
    private_voice_overlap_max_chars = _strict_env_positive_int(
        source_env,
        "NANA_PRIVATE_VOICE_OVERLAP_MAX_CHARS",
        int(getattr(config, "PRIVATE_VOICE_OVERLAP_MAX_CHARS", 140)),
        parse_errors,
    )
    private_voice_overlap_coalesce_ms = _strict_env_positive_int(
        source_env,
        "NANA_PRIVATE_VOICE_OVERLAP_COALESCE_MS",
        int(getattr(config, "PRIVATE_VOICE_OVERLAP_COALESCE_MS", 150)),
        parse_errors,
    )
    private_voice_overlap_tail_timeout_seconds = _strict_env_positive_int(
        source_env,
        "NANA_PRIVATE_VOICE_OVERLAP_TAIL_TIMEOUT_S",
        int(getattr(config, "PRIVATE_VOICE_OVERLAP_TAIL_TIMEOUT_S", 35)),
        parse_errors,
    )
    private_voice_overlap_pcm_enabled = _strict_env_bool(
        source_env,
        "NANA_PRIVATE_VOICE_OVERLAP_PCM_ENABLED",
        bool(getattr(config, "PRIVATE_VOICE_OVERLAP_PCM_ENABLED", False)),
        parse_errors,
    )
    private_voice_overlap_pcm_output_format = str(
        source_env.get(
            "NANA_PRIVATE_VOICE_OVERLAP_PCM_OUTPUT_FORMAT",
            getattr(
                config,
                "PRIVATE_VOICE_OVERLAP_PCM_OUTPUT_FORMAT",
                "pcm_24000",
            ),
        )
        or ""
    ).strip()
    private_voice_overlap_pcm_start_buffer_ms = _strict_env_positive_int(
        source_env,
        "NANA_PRIVATE_VOICE_OVERLAP_PCM_START_BUFFER_MS",
        int(getattr(config, "PRIVATE_VOICE_OVERLAP_PCM_START_BUFFER_MS", 300)),
        parse_errors,
    )
    private_voice_overlap_pcm_network_chunk_bytes = _strict_env_positive_int(
        source_env,
        "NANA_PRIVATE_VOICE_OVERLAP_PCM_NETWORK_CHUNK_BYTES",
        int(
            getattr(
                config,
                "PRIVATE_VOICE_OVERLAP_PCM_NETWORK_CHUNK_BYTES",
                4096,
            )
        ),
        parse_errors,
    )
    private_voice_overlap_pcm_timeout_seconds = _strict_env_positive_int(
        source_env,
        "NANA_PRIVATE_VOICE_OVERLAP_PCM_TIMEOUT_S",
        int(getattr(config, "PRIVATE_VOICE_OVERLAP_PCM_TIMEOUT_S", 45)),
        parse_errors,
    )
    voice_http_keepalive_enabled = _strict_env_bool(
        source_env,
        "NANA_VOICE_HTTP_KEEPALIVE_ENABLED",
        bool(getattr(config, "VOICE_HTTP_KEEPALIVE_ENABLED", True)),
        parse_errors,
    )
    voice_http_pool_maxsize = _strict_env_positive_int(
        source_env,
        "NANA_VOICE_HTTP_POOL_MAXSIZE",
        int(getattr(config, "VOICE_HTTP_POOL_MAXSIZE", 8)),
        parse_errors,
    )
    private_voice_ttd_enabled = _strict_env_bool(
        source_env,
        "NANA_PRIVATE_VOICE_TTD_ENABLED",
        bool(getattr(config, "PRIVATE_VOICE_TTD_ENABLED", False)),
        parse_errors,
    )
    private_voice_ttd_model = str(
        source_env.get(
            "NANA_PRIVATE_VOICE_TTD_MODEL",
            getattr(config, "PRIVATE_VOICE_TTD_MODEL", "eleven_v3"),
        )
        or ""
    ).strip()
    private_voice_ttd_input_mode = str(
        source_env.get(
            "NANA_PRIVATE_VOICE_TTD_INPUT_MODE",
            getattr(config, "PRIVATE_VOICE_TTD_INPUT_MODE", "incremental"),
        )
        or ""
    ).strip().lower()
    private_voice_ttd_output_format = str(
        source_env.get(
            "NANA_PRIVATE_VOICE_TTD_OUTPUT_FORMAT",
            getattr(config, "PRIVATE_VOICE_TTD_OUTPUT_FORMAT", "pcm_24000"),
        )
        or ""
    ).strip()
    private_voice_ttd_min_chars = _strict_env_positive_int(
        source_env,
        "NANA_PRIVATE_VOICE_TTD_MIN_CHARS",
        int(getattr(config, "PRIVATE_VOICE_TTD_MIN_CHARS", 40)),
        parse_errors,
    )
    private_voice_ttd_min_words = _strict_env_positive_int(
        source_env,
        "NANA_PRIVATE_VOICE_TTD_MIN_WORDS",
        int(getattr(config, "PRIVATE_VOICE_TTD_MIN_WORDS", 8)),
        parse_errors,
    )
    private_voice_ttd_chunk_max_chars = _strict_env_positive_int(
        source_env,
        "NANA_PRIVATE_VOICE_TTD_CHUNK_MAX_CHARS",
        int(getattr(config, "PRIVATE_VOICE_TTD_CHUNK_MAX_CHARS", 240)),
        parse_errors,
    )
    private_voice_ttd_chunk_target_chars = _strict_env_positive_int(
        source_env,
        "NANA_PRIVATE_VOICE_TTD_CHUNK_TARGET_CHARS",
        int(getattr(config, "PRIVATE_VOICE_TTD_CHUNK_TARGET_CHARS", 120)),
        parse_errors,
    )
    private_voice_ttd_start_buffer_ms = _strict_env_positive_int(
        source_env,
        "NANA_PRIVATE_VOICE_TTD_START_BUFFER_MS",
        int(getattr(config, "PRIVATE_VOICE_TTD_START_BUFFER_MS", 300)),
        parse_errors,
    )
    private_voice_ttd_timeout_seconds = _strict_env_positive_int(
        source_env,
        "NANA_PRIVATE_VOICE_TTD_TIMEOUT_S",
        int(getattr(config, "PRIVATE_VOICE_TTD_TIMEOUT_S", 45)),
        parse_errors,
    )
    private_voice_ttd_capture_enabled = _strict_env_bool(
        source_env,
        "NANA_PRIVATE_VOICE_TTD_CAPTURE_ENABLED",
        bool(getattr(config, "PRIVATE_VOICE_TTD_CAPTURE_ENABLED", False)),
        parse_errors,
    )

    autonomy_model = str(
        source_env.get("NANA_AUTONOMY_LLM_MODEL", "nana-banter")
    ).strip()
    provider = str(
        getattr(config, "NANA_CHAT_PROVIDER", "llmgate") or ""
    ).strip().lower()
    if provider == "llmgate":
        main_model = str(
            getattr(config, "LLMGATE_MAIN_MODEL", "") or ""
        ).strip()
    elif provider in {"openai", "official_openai"}:
        main_model = str(getattr(config, "OPENAI_MODEL", "") or "").strip()
    else:
        main_model = ""

    return StartupConfigSnapshot(
        provider=provider,
        main_model=main_model,
        reasoning_effort=str(
            getattr(config, "LLMGATE_MAIN_REASONING_EFFORT", "none") or ""
        ).strip().lower(),
        openai_fallback_enabled=bool(
            getattr(config, "NANA_OPENAI_FALLBACK_ENABLED", False)
        ),
        llm_fast_private_enabled=llm_fast_private_enabled,
        llm_fast_private_model=llm_fast_private_model,
        llm_fast_private_max_input_chars=llm_fast_private_max_input_chars,
        llm_fast_private_max_tokens=llm_fast_private_max_tokens,
        autonomy_enabled=autonomy_enabled,
        autonomy_model=autonomy_model,
        autonomy_auto_output_enabled=autonomy_auto_output_enabled,
        voice_streaming_enabled=bool(
            getattr(config, "VOICE_STREAMING_ENABLED", False)
        ),
        voice_streaming_pilot_enabled=bool(
            getattr(config, "VOICE_STREAMING_PILOT_ENABLED", False)
        ),
        voice_streaming_kill_switch=bool(
            getattr(config, "VOICE_STREAMING_KILL_SWITCH", True)
        ),
        voice_streaming_direct_only=bool(
            getattr(config, "VOICE_STREAMING_DIRECT_ONLY", False)
        ),
        voice_stream_callback_output_enabled=callback_output,
        private_voice_overlap_enabled=private_voice_overlap_enabled,
        private_voice_overlap_min_chars=private_voice_overlap_min_chars,
        private_voice_overlap_max_chars=private_voice_overlap_max_chars,
        private_voice_overlap_coalesce_ms=private_voice_overlap_coalesce_ms,
        private_voice_overlap_tail_timeout_seconds=(
            private_voice_overlap_tail_timeout_seconds
        ),
        private_voice_overlap_pcm_enabled=private_voice_overlap_pcm_enabled,
        private_voice_overlap_pcm_output_format=(
            private_voice_overlap_pcm_output_format
        ),
        private_voice_overlap_pcm_start_buffer_ms=(
            private_voice_overlap_pcm_start_buffer_ms
        ),
        private_voice_overlap_pcm_network_chunk_bytes=(
            private_voice_overlap_pcm_network_chunk_bytes
        ),
        private_voice_overlap_pcm_timeout_seconds=(
            private_voice_overlap_pcm_timeout_seconds
        ),
        voice_http_keepalive_enabled=voice_http_keepalive_enabled,
        voice_http_pool_maxsize=voice_http_pool_maxsize,
        private_voice_ttd_enabled=private_voice_ttd_enabled,
        private_voice_ttd_model=private_voice_ttd_model,
        private_voice_ttd_input_mode=private_voice_ttd_input_mode,
        private_voice_ttd_output_format=private_voice_ttd_output_format,
        private_voice_ttd_min_chars=private_voice_ttd_min_chars,
        private_voice_ttd_min_words=private_voice_ttd_min_words,
        private_voice_ttd_chunk_target_chars=(
            private_voice_ttd_chunk_target_chars
        ),
        private_voice_ttd_chunk_max_chars=private_voice_ttd_chunk_max_chars,
        private_voice_ttd_start_buffer_ms=private_voice_ttd_start_buffer_ms,
        private_voice_ttd_timeout_seconds=private_voice_ttd_timeout_seconds,
        private_voice_ttd_capture_enabled=private_voice_ttd_capture_enabled,
        voice_debug_no_tts=bool(getattr(config, "DEBUG_NO_TTS", False)),
        voice_test_mode=bool(getattr(config, "VOICE_TEST_MODE", False)),
        voice_output_format=str(
            getattr(config, "ELEVEN_OUTPUT_FORMAT", "") or ""
        ).strip(),
        openai_key_present=_configured_secret_present(
            getattr(config, "OPENAI_API_KEY", "")
        ),
        eleven_key_present=_configured_secret_present(
            getattr(config, "ELEVEN_API_KEY", "")
        ),
        voice_id_present=bool(
            str(getattr(config, "VOICE_ID", "") or "").strip()
        ),
        vts_startup_enabled=bool(
            getattr(config, "VTS_STARTUP_ENABLED", False)
        ),
        external_bridge_enabled=bridge_enabled,
        external_bridge_poll_seconds=bridge_poll,
        presence_session_enabled=presence_session_enabled,
        presence_session_host=presence_session_host,
        presence_session_port=presence_session_port,
        presence_session_token_present=presence_session_token_present,
        presence_session_heartbeat_seconds=presence_session_heartbeat,
        presence_session_timeout_seconds=presence_session_timeout,
        autonomy_stop_join_timeout_seconds=autonomy_join,
        autonomy_poller_join_timeout_seconds=poller_join,
        parse_errors=tuple(parse_errors),
        avatar_gateway_enabled=avatar_gateway_enabled,
        avatar_gateway_host=avatar_gateway_host,
        avatar_gateway_port=avatar_gateway_port,
        avatar_gateway_transport=avatar_gateway_transport,
        avatar_gateway_pending_limit=avatar_gateway_pending_limit,
        avatar_gateway_timeout_seconds=avatar_gateway_timeout_seconds,
        avatar_gateway_auto_events_enabled=avatar_gateway_auto_events_enabled,
        avatar_gateway_token_present=avatar_gateway_token_present,
        warudo_ws_url_present=warudo_ws_url_present,
    )


def validate_startup_config(
    snapshot: StartupConfigSnapshot,
) -> StartupConfigContract:
    errors = list(snapshot.parse_errors)
    warnings: list[str] = []

    if snapshot.provider not in _ALLOWED_PROVIDERS:
        errors.append(f"NANA_CHAT_PROVIDER: unsupported provider '{snapshot.provider}'")
    elif not snapshot.main_model:
        model_name = (
            "NANA_LLMGATE_MAIN_MODEL"
            if snapshot.provider == "llmgate"
            else "OPENAI_MODEL"
        )
        errors.append(f"{model_name}: required for {snapshot.provider} provider")
    if not snapshot.openai_key_present:
        errors.append("OPENAI_API_KEY: missing or placeholder")
    if snapshot.autonomy_enabled and not snapshot.autonomy_model:
        errors.append("NANA_AUTONOMY_LLM_MODEL: required while autonomy LLM is enabled")
    if (
        snapshot.provider == "llmgate"
        and snapshot.reasoning_effort not in _ALLOWED_REASONING_EFFORTS
    ):
        warnings.append(
            "NANA_LLMGATE_MAIN_REASONING_EFFORT: unsupported value; provider default will apply"
        )
    if snapshot.llm_fast_private_enabled:
        if snapshot.provider != "llmgate":
            errors.append(
                "NANA_LLM_FAST_PRIVATE_ENABLED: requires llmgate provider"
            )
        if not snapshot.llm_fast_private_model:
            errors.append(
                "NANA_LLM_FAST_PRIVATE_MODEL: required while private fast lane is enabled"
            )
        if snapshot.llm_fast_private_model == snapshot.main_model:
            warnings.append(
                "private fast lane model matches main model; latency A/B separation is disabled"
            )

    positive_fields = (
        ("NANA_EXTERNAL_BRIDGE_POLL_SECONDS", snapshot.external_bridge_poll_seconds),
        (
            "NANA_AUTONOMY_STOP_JOIN_TIMEOUT_S",
            snapshot.autonomy_stop_join_timeout_seconds,
        ),
        (
            "NANA_AUTONOMY_POLLER_JOIN_TIMEOUT_S",
            snapshot.autonomy_poller_join_timeout_seconds,
        ),
        (
            "NANA_PRESENCE_SESSION_HEARTBEAT_SECONDS",
            snapshot.presence_session_heartbeat_seconds,
        ),
        (
            "NANA_PRESENCE_SESSION_TIMEOUT_SECONDS",
            snapshot.presence_session_timeout_seconds,
        ),
        (
            "NANA_AVATAR_GATEWAY_TIMEOUT_S",
            snapshot.avatar_gateway_timeout_seconds,
        ),
    )
    for name, value in positive_fields:
        if not math.isfinite(value) or value <= 0.0:
            message = f"{name}: must be a positive finite number"
            if message not in errors:
                errors.append(message)

    if not snapshot.voice_streaming_enabled:
        if snapshot.voice_streaming_pilot_enabled:
            warnings.append("voice streaming pilot is inert while streaming is disabled")
        if snapshot.voice_streaming_direct_only:
            warnings.append("voice streaming direct-only is inert while streaming is disabled")
        if snapshot.voice_stream_callback_output_enabled:
            warnings.append("voice callback output is inert while streaming is disabled")
    if (
        snapshot.voice_stream_callback_output_enabled
        and snapshot.voice_streaming_kill_switch
    ):
        warnings.append("voice callback output is blocked by the streaming kill switch")
    if (
        snapshot.private_voice_overlap_min_chars
        > snapshot.private_voice_overlap_max_chars
    ):
        errors.append(
            "NANA_PRIVATE_VOICE_OVERLAP_MIN_CHARS: must not exceed max chars"
        )
    if snapshot.private_voice_overlap_enabled:
        if snapshot.voice_debug_no_tts or snapshot.voice_test_mode:
            warnings.append(
                "private voice overlap is inert while voice output is disabled"
            )
        if not snapshot.eleven_key_present or not snapshot.voice_id_present:
            warnings.append(
                "private voice overlap is enabled without ready ElevenLabs credentials"
            )
    if snapshot.private_voice_overlap_pcm_output_format not in {
        "pcm_16000",
        "pcm_22050",
        "pcm_24000",
        "pcm_32000",
        "pcm_48000",
    }:
        errors.append(
            "NANA_PRIVATE_VOICE_OVERLAP_PCM_OUTPUT_FORMAT: must be a "
            "Starter-compatible raw PCM format"
        )
    if snapshot.private_voice_overlap_pcm_enabled:
        if not snapshot.private_voice_overlap_enabled:
            warnings.append(
                "HTTP overlap PCM is inert while private voice overlap is disabled"
            )
        if not snapshot.voice_streaming_enabled:
            warnings.append(
                "HTTP overlap PCM is inert while voice streaming is disabled"
            )
        if snapshot.voice_streaming_kill_switch:
            warnings.append(
                "HTTP overlap PCM is blocked by the voice streaming kill switch"
            )
        if not snapshot.voice_stream_callback_output_enabled:
            warnings.append(
                "HTTP overlap PCM is inert while callback output is disabled"
            )
        if snapshot.voice_debug_no_tts or snapshot.voice_test_mode:
            warnings.append(
                "HTTP overlap PCM is inert while voice output is disabled"
            )
        if not snapshot.eleven_key_present or not snapshot.voice_id_present:
            warnings.append(
                "HTTP overlap PCM is enabled without ready ElevenLabs credentials"
            )
    if snapshot.voice_http_pool_maxsize > 32:
        errors.append("NANA_VOICE_HTTP_POOL_MAXSIZE: must be at most 32")
    if snapshot.private_voice_ttd_min_chars > snapshot.private_voice_ttd_chunk_max_chars:
        errors.append(
            "NANA_PRIVATE_VOICE_TTD_MIN_CHARS: must not exceed chunk max chars"
        )
    if (
        snapshot.private_voice_ttd_chunk_target_chars
        > snapshot.private_voice_ttd_chunk_max_chars
    ):
        errors.append(
            "NANA_PRIVATE_VOICE_TTD_CHUNK_TARGET_CHARS: must not exceed "
            "chunk max chars"
        )
    if snapshot.private_voice_ttd_model not in {
        "eleven_v3",
        "eleven_v3_conversational",
    }:
        errors.append(
            "NANA_PRIVATE_VOICE_TTD_MODEL: must be eleven_v3 or "
            "eleven_v3_conversational"
        )
    if snapshot.private_voice_ttd_input_mode not in {
        "incremental",
        "eof_single",
    }:
        errors.append(
            "NANA_PRIVATE_VOICE_TTD_INPUT_MODE: must be incremental or "
            "eof_single"
        )
    if snapshot.private_voice_ttd_output_format not in {
        "pcm_16000",
        "pcm_22050",
        "pcm_24000",
        "pcm_32000",
        "pcm_48000",
    }:
        errors.append(
            "NANA_PRIVATE_VOICE_TTD_OUTPUT_FORMAT: must be a Starter-compatible "
            "raw PCM format"
        )
    if snapshot.private_voice_ttd_enabled:
        if snapshot.voice_debug_no_tts or snapshot.voice_test_mode:
            warnings.append(
                "private voice TTD is inert while voice output is disabled"
            )
        if not snapshot.voice_streaming_enabled:
            warnings.append(
                "private voice TTD is inert while voice streaming is disabled"
            )
        if snapshot.voice_streaming_kill_switch:
            warnings.append(
                "private voice TTD is blocked by the voice streaming kill switch"
            )
        if not snapshot.voice_stream_callback_output_enabled:
            warnings.append(
                "private voice TTD is inert while callback output is disabled"
            )
        if not snapshot.eleven_key_present or not snapshot.voice_id_present:
            warnings.append(
                "private voice TTD is enabled without ready ElevenLabs credentials"
            )
    if snapshot.presence_session_port > 65535:
        errors.append("NANA_PRESENCE_SESSION_PORT: must be at most 65535")
    if snapshot.presence_session_enabled and not snapshot.presence_session_host:
        errors.append(
            "NANA_PRESENCE_SESSION_HOST: required while Presence session is enabled"
        )
    if (
        snapshot.presence_session_enabled
        and not snapshot.presence_session_token_present
    ):
        errors.append(
            "NANA_PRESENCE_SESSION_TOKEN: required while Presence session is enabled"
        )
    if (
        snapshot.presence_session_timeout_seconds
        <= snapshot.presence_session_heartbeat_seconds
    ):
        errors.append(
            "NANA_PRESENCE_SESSION_TIMEOUT_SECONDS: must exceed heartbeat interval"
        )
    if snapshot.avatar_gateway_host not in {"127.0.0.1", "localhost", "::1"}:
        errors.append("NANA_AVATAR_GATEWAY_HOST: must be loopback-only")
    if snapshot.avatar_gateway_port > 65535:
        errors.append("NANA_AVATAR_GATEWAY_PORT: must be at most 65535")
    if snapshot.avatar_gateway_transport not in {
        "recording",
        "warudo",
        "warudo_ws",
        "websocket",
    }:
        errors.append(
            "NANA_AVATAR_GATEWAY_TRANSPORT: must be recording or warudo_ws"
        )
    if snapshot.avatar_gateway_pending_limit > 8:
        errors.append("NANA_AVATAR_GATEWAY_PENDING_LIMIT: must be at most 8")
    if snapshot.avatar_gateway_enabled and snapshot.avatar_gateway_transport in {
        "warudo",
        "warudo_ws",
        "websocket",
    } and not snapshot.warudo_ws_url_present:
        errors.append(
            "NANA_WARUDO_WS_URL: required when avatar gateway uses warudo_ws"
        )
    if snapshot.avatar_gateway_auto_events_enabled and not snapshot.avatar_gateway_enabled:
        warnings.append(
            "avatar gateway auto events are inert while the gateway is disabled"
        )
    return StartupConfigContract(
        snapshot=snapshot,
        errors=tuple(errors),
        warnings=tuple(warnings),
    )


def build_startup_config_contract(
    *,
    env: Mapping[str, str] | None = None,
    config_source: Any | None = None,
) -> StartupConfigContract:
    return validate_startup_config(
        build_startup_config_snapshot(env=env, config_source=config_source)
    )


def format_startup_config_line(contract: StartupConfigContract) -> str:
    snapshot = contract.snapshot
    voice_mode = "stream" if snapshot.voice_streaming_enabled else "file"
    if snapshot.voice_stream_callback_output_enabled:
        voice_mode += "+callback"
    return (
        "Startup config contract: "
        f"status={contract.status} | provider={snapshot.provider} | "
        f"model={snapshot.main_model or 'none'} | "
        f"llm_fast={'on' if snapshot.llm_fast_private_enabled else 'off'}:"
        f"{snapshot.llm_fast_private_model or 'none'} | "
        f"autonomy={'on' if snapshot.autonomy_enabled else 'off'}:{snapshot.autonomy_model} | "
        f"auto_output={'on' if snapshot.autonomy_auto_output_enabled else 'off'} | "
        f"voice={voice_mode} | "
        f"voice_overlap={'on' if snapshot.private_voice_overlap_enabled else 'off'} | "
        f"overlap_pcm={'on' if snapshot.private_voice_overlap_pcm_enabled else 'off'}:"
        f"{snapshot.private_voice_overlap_pcm_output_format} | "
        f"voice_http={'keepalive' if snapshot.voice_http_keepalive_enabled else 'fresh'}:"
        f"{snapshot.voice_http_pool_maxsize} | "
        f"voice_ttd={'on' if snapshot.private_voice_ttd_enabled else 'off'}:"
        f"{snapshot.private_voice_ttd_model}/{snapshot.private_voice_ttd_output_format}/"
        f"{snapshot.private_voice_ttd_input_mode} | "
        f"ttd_capture={'on' if snapshot.private_voice_ttd_capture_enabled else 'off'} | "
        f"presence_session={'on' if snapshot.presence_session_enabled else 'off'}:"
        f"{snapshot.presence_session_host or 'none'}:{snapshot.presence_session_port} | "
        f"vts={'on' if snapshot.vts_startup_enabled else 'off'} | "
        f"bridge={'on' if snapshot.external_bridge_enabled else 'off'} | "
        f"avatar={'on' if snapshot.avatar_gateway_enabled else 'off'}:"
        f"{snapshot.avatar_gateway_host}:{snapshot.avatar_gateway_port}/"
        f"{snapshot.avatar_gateway_transport} | "
        f"warnings={len(contract.warnings)} | errors={len(contract.errors)}"
    )


__all__ = [
    "StartupConfigContract",
    "StartupConfigSnapshot",
    "build_startup_config_contract",
    "build_startup_config_snapshot",
    "format_startup_config_line",
    "validate_startup_config",
]
