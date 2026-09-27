import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
MEMORY_PATH = DATA_DIR / "memory.json"
CHAT_HISTORY_PATH = DATA_DIR / "chat_history.txt"
TOKEN_PATH = DATA_DIR / "token.txt"


def _load_env_file(path):
    if not path.exists():
        return
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_env_file(BASE_DIR.parent / ".env")
_load_env_file(BASE_DIR / ".env")


def _parse_microphone_device_index(value):
    raw = str(value or "").strip()
    if raw.lower() in {"", "auto", "default", "none"}:
        return None
    try:
        index = int(raw)
    except ValueError as exc:
        raise ValueError(
            "NANA_MIC_DEVICE_INDEX must be an integer or 'auto'"
        ) from exc
    if index < 0:
        raise ValueError("NANA_MIC_DEVICE_INDEX must be zero or greater")
    return index


def _bounded_env_int(name, default, low, high):
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = int(default)
    return max(int(low), min(int(high), value))


def _env_flag(name, default=False):
    """Parse a conservative feature flag without making truthy typos enable it."""
    raw = os.getenv(name, "1" if default else "0")
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "OPENAI_KEY_CUA_BAN")
ELEVEN_API_KEY = os.getenv("ELEVEN_API_KEY", "ELEVENLABS_KEY_CUA_BAN")
VOICE_ID = os.getenv("ELEVEN_VOICE_ID", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
OPENAI_FALLBACK_MODELS = [
    model.strip()
    for model in os.getenv("OPENAI_FALLBACK_MODELS", "gpt-4o-mini").split(",")
    if model.strip()
]

NANA_CHAT_PROVIDER = os.getenv("NANA_CHAT_PROVIDER", "llmgate").strip().lower()
NANA_OPENAI_FALLBACK_ENABLED = os.getenv("NANA_OPENAI_FALLBACK_ENABLED", "0") == "1"
LLMGATE_MAIN_MODEL = os.getenv("NANA_LLMGATE_MAIN_MODEL", "gemini-3-flash")
LLMGATE_MAIN_REASONING_EFFORT = os.getenv("NANA_LLMGATE_MAIN_REASONING_EFFORT", "none").strip().lower()
LLMGATE_CHEAP_MODEL = os.getenv("NANA_LLMGATE_CHEAP_MODEL", "gpt-5.4-mini")
LLMGATE_PUBLIC_MODEL = os.getenv("NANA_LLMGATE_PUBLIC_MODEL", LLMGATE_MAIN_MODEL)
LLMGATE_PUBLIC_FALLBACK_MODELS = [
    model.strip()
    for model in os.getenv(
        "NANA_LLMGATE_PUBLIC_FALLBACK_MODELS",
        "gemini-3-flash,grok-4.20-0309-non-reasoning,gemini-3.1-flash-lite",
    ).split(",")
    if model.strip()
]
LLMGATE_FALLBACK_MODELS = [
    model.strip()
    for model in os.getenv(
        "NANA_LLMGATE_FALLBACK_MODELS",
        "gpt-5.4,gpt-5.5,gpt-5.4-mini,gemini-3.5-flash",
    ).split(",")
    if model.strip()
]
ELEVEN_OUTPUT_FORMAT = os.getenv("ELEVEN_OUTPUT_FORMAT", "mp3_44100_128")
PRESENCE_TTS_STREAMING_ENABLED = os.getenv(
    "NANA_PRESENCE_TTS_STREAMING_ENABLED",
    "1",
).strip().lower() not in {"0", "false", "off", "no"}

DEBUG_NO_TTS = os.getenv("NANA_DEBUG_NO_TTS", "0") == "1"
VOICE_TEST_MODE = os.getenv("NANA_VOICE_TEST_MODE", "0") == "1"
VOICE_CACHE_ENABLED = os.getenv("NANA_VOICE_CACHE_ENABLED", "1") == "1"
VOICE_CACHE_DIR = Path(os.getenv("NANA_VOICE_CACHE_DIR", str(DATA_DIR / "voice_cache")))
VOICE_CACHE_MAX_TEXT_CHARS = int(os.getenv("NANA_VOICE_CACHE_MAX_TEXT_CHARS", "64"))
VOICE_CHUNKING_ENABLED = os.getenv("NANA_VOICE_CHUNKING_ENABLED", "1") == "1"
VOICE_CHUNK_MAX_CHARS = int(os.getenv("NANA_VOICE_CHUNK_MAX_CHARS", "160"))
VOICE_STREAMING_DRY_RUN_ENABLED = os.getenv("NANA_VOICE_STREAMING_DRY_RUN_ENABLED", "1") == "1"
VOICE_STREAMING_ENABLED = os.getenv("NANA_VOICE_STREAMING_ENABLED", "1") == "1"
VOICE_STREAMING_PILOT_ENABLED = os.getenv("NANA_VOICE_STREAMING_PILOT_ENABLED", "1") == "1"
VOICE_STREAMING_KILL_SWITCH = os.getenv("NANA_VOICE_STREAMING_KILL_SWITCH", "0") == "1"
VOICE_STREAMING_DIRECT_ONLY = os.getenv("NANA_VOICE_STREAMING_DIRECT_ONLY", "1") == "1"
PRIVATE_VOICE_OVERLAP_ENABLED = os.getenv(
    "NANA_PRIVATE_VOICE_OVERLAP_ENABLED",
    "1",
).strip().lower() in {"1", "true", "yes", "on"}
PRIVATE_VOICE_OVERLAP_MIN_CHARS = _bounded_env_int(
    "NANA_PRIVATE_VOICE_OVERLAP_MIN_CHARS", 45, 20, 120
)
PRIVATE_VOICE_OVERLAP_MAX_CHARS = _bounded_env_int(
    "NANA_PRIVATE_VOICE_OVERLAP_MAX_CHARS", 140, 60, 240
)
PRIVATE_VOICE_OVERLAP_COALESCE_MS = _bounded_env_int(
    "NANA_PRIVATE_VOICE_OVERLAP_COALESCE_MS", 150, 25, 1000
)
PRIVATE_VOICE_OVERLAP_TAIL_TIMEOUT_S = _bounded_env_int(
    "NANA_PRIVATE_VOICE_OVERLAP_TAIL_TIMEOUT_S", 35, 5, 120
)
PRIVATE_VOICE_OVERLAP_PCM_ENABLED = os.getenv(
    "NANA_PRIVATE_VOICE_OVERLAP_PCM_ENABLED",
    "1",
).strip().lower() in {"1", "true", "yes", "on"}
PRIVATE_VOICE_OVERLAP_PCM_OUTPUT_FORMAT = (
    os.getenv("NANA_PRIVATE_VOICE_OVERLAP_PCM_OUTPUT_FORMAT", "pcm_24000").strip()
    or "pcm_24000"
)
PRIVATE_VOICE_OVERLAP_PCM_START_BUFFER_MS = _bounded_env_int(
    "NANA_PRIVATE_VOICE_OVERLAP_PCM_START_BUFFER_MS", 300, 100, 1200
)
PRIVATE_VOICE_OVERLAP_PCM_NETWORK_CHUNK_BYTES = _bounded_env_int(
    "NANA_PRIVATE_VOICE_OVERLAP_PCM_NETWORK_CHUNK_BYTES", 4096, 1024, 65536
)
PRIVATE_VOICE_OVERLAP_PCM_TIMEOUT_S = _bounded_env_int(
    "NANA_PRIVATE_VOICE_OVERLAP_PCM_TIMEOUT_S", 45, 5, 180
)
VOICE_HTTP_KEEPALIVE_ENABLED = os.getenv(
    "NANA_VOICE_HTTP_KEEPALIVE_ENABLED",
    "1",
).strip().lower() in {"1", "true", "yes", "on"}
VOICE_HTTP_POOL_MAXSIZE = _bounded_env_int(
    "NANA_VOICE_HTTP_POOL_MAXSIZE", 8, 1, 32
)
PRIVATE_VOICE_TTD_ENABLED = os.getenv(
    "NANA_PRIVATE_VOICE_TTD_ENABLED",
    "0",
).strip().lower() in {"1", "true", "yes", "on"}
PRIVATE_VOICE_TTD_MODEL = (
    os.getenv("NANA_PRIVATE_VOICE_TTD_MODEL", "eleven_v3").strip()
    or "eleven_v3"
)
PRIVATE_VOICE_TTD_INPUT_MODE = (
    os.getenv("NANA_PRIVATE_VOICE_TTD_INPUT_MODE", "incremental")
    .strip()
    .lower()
    or "incremental"
)
PRIVATE_VOICE_TTD_OUTPUT_FORMAT = (
    os.getenv("NANA_PRIVATE_VOICE_TTD_OUTPUT_FORMAT", "pcm_24000").strip()
    or "pcm_24000"
)
PRIVATE_VOICE_TTD_MIN_CHARS = _bounded_env_int(
    "NANA_PRIVATE_VOICE_TTD_MIN_CHARS", 40, 20, 120
)
PRIVATE_VOICE_TTD_MIN_WORDS = _bounded_env_int(
    "NANA_PRIVATE_VOICE_TTD_MIN_WORDS", 8, 4, 24
)
PRIVATE_VOICE_TTD_CHUNK_MAX_CHARS = _bounded_env_int(
    "NANA_PRIVATE_VOICE_TTD_CHUNK_MAX_CHARS", 240, 80, 480
)
PRIVATE_VOICE_TTD_CHUNK_TARGET_CHARS = _bounded_env_int(
    "NANA_PRIVATE_VOICE_TTD_CHUNK_TARGET_CHARS", 120, 16, 240
)
PRIVATE_VOICE_TTD_START_BUFFER_MS = _bounded_env_int(
    "NANA_PRIVATE_VOICE_TTD_START_BUFFER_MS", 300, 100, 1000
)
PRIVATE_VOICE_TTD_TIMEOUT_S = _bounded_env_int(
    "NANA_PRIVATE_VOICE_TTD_TIMEOUT_S", 45, 5, 180
)
PRIVATE_VOICE_TTD_CAPTURE_ENABLED = os.getenv(
    "NANA_PRIVATE_VOICE_TTD_CAPTURE_ENABLED",
    "0",
).strip().lower() in {"1", "true", "yes", "on"}
PRIVATE_VOICE_TTD_CAPTURE_DIR = Path(
    os.getenv(
        "NANA_PRIVATE_VOICE_TTD_CAPTURE_DIR",
        str(DATA_DIR / "ttd_captures"),
    )
)
VTS_EXPRESSION_COOLDOWN_SECONDS = float(os.getenv("NANA_VTS_EXPRESSION_COOLDOWN_SECONDS", "8.0"))
VTS_EXPRESSION_DEFAULT_CHANCE = float(os.getenv("NANA_VTS_EXPRESSION_DEFAULT_CHANCE", "0.18"))
VTS_EXPRESSION_RESET_DELAY_SECONDS = float(os.getenv("NANA_VTS_EXPRESSION_RESET_DELAY_SECONDS", "0.8"))
VTS_EXPRESSION_RESET_TIMEOUT_SECONDS = float(os.getenv("NANA_VTS_EXPRESSION_RESET_TIMEOUT_SECONDS", "60.0"))
VTS_EXPRESSION_RESET_FALLBACK_HOTKEY = os.getenv("NANA_VTS_EXPRESSION_RESET_FALLBACK_HOTKEY", "1") == "1"
VTS_STARTUP_ENABLED = os.getenv("NANA_VTS_STARTUP_ENABLED", "0") == "1"

# External avatar runtime bridge. Disabled until a runtime receiver has been
# configured and manually verified.
AVATAR_GATEWAY_ENABLED = os.getenv("NANA_AVATAR_GATEWAY_ENABLED", "0") == "1"
AVATAR_GATEWAY_HOST = os.getenv("NANA_AVATAR_GATEWAY_HOST", "127.0.0.1").strip()
AVATAR_GATEWAY_PORT = _bounded_env_int(
    "NANA_AVATAR_GATEWAY_PORT", 8766, 1, 65535
)
AVATAR_GATEWAY_TRANSPORT = (
    os.getenv("NANA_AVATAR_GATEWAY_TRANSPORT", "recording").strip().lower()
    or "recording"
)
AVATAR_GATEWAY_PENDING_LIMIT = _bounded_env_int(
    "NANA_AVATAR_GATEWAY_PENDING_LIMIT", 1, 0, 8
)
AVATAR_GATEWAY_TIMEOUT_S = float(os.getenv("NANA_AVATAR_GATEWAY_TIMEOUT_S", "2.0"))
AVATAR_GATEWAY_AUTO_EVENTS_ENABLED = (
    os.getenv("NANA_AVATAR_GATEWAY_AUTO_EVENTS_ENABLED", "0") == "1"
)
WARUDO_WS_URL = os.getenv("NANA_WARUDO_WS_URL", "").strip()
AVATAR_GATEWAY_TOKEN = os.getenv("NANA_AVATAR_GATEWAY_TOKEN", "")

MIC_DEVICE_INDEX = _parse_microphone_device_index(
    os.getenv("NANA_MIC_DEVICE_INDEX", "auto")
)
MIN_VOICE_SECONDS = 0.25
GPT_COOLDOWN = 1.0

RUNTIME_REACTION_COOLDOWN = 20
CONTEXT_REACT_COOLDOWN = 60
CHAT_CONTEXT_SUPPRESS = 180

BROWSER_CDP_ENDPOINTS = [
    ("edge", os.getenv("NANA_EDGE_CDP_URL", "http://127.0.0.1:9222/json")),
]
BROWSER_FRESH_SECONDS = float(os.getenv("NANA_BROWSER_FRESH_SECONDS", "45"))
PROACTIVE_BROWSER_COOLDOWN = float(os.getenv("NANA_PROACTIVE_BROWSER_COOLDOWN", "180"))
OLLAMA_URL = os.getenv("NANA_OLLAMA_URL", "http://127.0.0.1:11434/api/generate")
OLLAMA_MODEL = os.getenv("NANA_OLLAMA_MODEL", "qwen2.5:7b")
OLLAMA_TIMEOUT = float(os.getenv("NANA_OLLAMA_TIMEOUT", "10.0"))
LLMGATE_SETTINGS_PATH = os.getenv(
    "NANA_LLMGATE_SETTINGS_PATH",
    str(Path.home() / ".factory" / "settings.json"),
)
LLMGATE_TIMEOUT = float(os.getenv("NANA_LLMGATE_TIMEOUT", "60.0"))
LLM_COMPACT_PRIVATE_PROMPT_ENABLED = os.getenv(
    "NANA_LLM_COMPACT_PRIVATE_PROMPT_ENABLED",
    "1",
).strip().lower() not in {"0", "false", "off", "no"}
LLM_PROMPT_SHORT_TERM_LINES = _bounded_env_int(
    "NANA_LLM_PROMPT_SHORT_TERM_LINES", 6, 2, 16
)
LLM_PROMPT_RECENT_CHAT_LINES = _bounded_env_int(
    "NANA_LLM_PROMPT_RECENT_CHAT_LINES", 8, 2, 24
)
LLM_PROMPT_RETRIEVAL_LIMIT = _bounded_env_int(
    "NANA_LLM_PROMPT_RETRIEVAL_LIMIT", 3, 1, 8
)
LLM_PROMPT_MEMORY_RULE_LIMIT = _bounded_env_int(
    "NANA_LLM_PROMPT_MEMORY_RULE_LIMIT", 12, 4, 50
)
LLM_CHAT_MAX_TOKENS = _bounded_env_int(
    "NANA_LLM_CHAT_MAX_TOKENS", 420, 120, 1000
)
LLM_STORY_MAX_TOKENS = _bounded_env_int(
    "NANA_LLM_STORY_MAX_TOKENS", 1000, LLM_CHAT_MAX_TOKENS, 1600
)
LLM_FAST_PRIVATE_ENABLED = os.getenv(
    "NANA_LLM_FAST_PRIVATE_ENABLED",
    "0",
).strip().lower() in {"1", "true", "yes", "on"}
LLM_FAST_PRIVATE_MODEL = os.getenv(
    "NANA_LLM_FAST_PRIVATE_MODEL",
    "gemini-3-flash",
).strip()
LLM_FAST_PRIVATE_MAX_INPUT_CHARS = _bounded_env_int(
    "NANA_LLM_FAST_PRIVATE_MAX_INPUT_CHARS", 220, 40, 500
)
LLM_FAST_PRIVATE_MAX_TOKENS = _bounded_env_int(
    "NANA_LLM_FAST_PRIVATE_MAX_TOKENS", 96, 32, 240
)

# Memory v2 Phase 2 is deliberately opt-in. These flags only expose bounded
# read/candidate paths; no flag enables automatic durable promotion.
MEMORY_SEMANTIC_RETRIEVAL_ENABLED = _env_flag(
    "NANA_MEMORY_SEMANTIC_RETRIEVAL_ENABLED", False
)
MEMORY_CONSOLIDATION_PREVIEW_ENABLED = _env_flag(
    "NANA_MEMORY_CONSOLIDATION_PREVIEW_ENABLED", False
)
MEMORY_PUBLIC_CROSS_SESSION_RECALL_ENABLED = _env_flag(
    "NANA_MEMORY_PUBLIC_CROSS_SESSION_RECALL_ENABLED", False
)
MEMORY_PROMOTION_ENABLED = _env_flag("NANA_MEMORY_PROMOTION_ENABLED", False)
STREAM_CUM0_ENABLED = _env_flag("NANA_STREAM_CUM0_ENABLED", False)
STREAM_CUM1_YOUTUBE_INGRESS_ENABLED = _env_flag(
    "NANA_STREAM_CUM1_YOUTUBE_INGRESS_ENABLED",
    False,
)
STREAM_CUM2_RESPONSE_ENABLED = _env_flag(
    "NANA_STREAM_CUM2_RESPONSE_ENABLED",
    False,
)
STREAM_CUM3_YOUTUBE_OUTPUT_ENABLED = _env_flag(
    "NANA_STREAM_CUM3_YOUTUBE_OUTPUT_ENABLED",
    False,
)
STREAM_LOCAL_SIMULATOR_ENABLED = _env_flag(
    "NANA_STREAM_LOCAL_SIMULATOR_ENABLED",
    False,
)
STREAM_CUM4_HOST_ENABLED = _env_flag("NANA_STREAM_CUM4_HOST_ENABLED", False)
STREAM_CUM5_VOICE_PLAYBACK_ENABLED = _env_flag(
    "NANA_STREAM_CUM5_VOICE_PLAYBACK_ENABLED",
    False,
)
YOUTUBE_OAUTH_CLIENT_SECRETS_PATH = os.getenv(
    "NANA_YOUTUBE_OAUTH_CLIENT_SECRETS_PATH",
    "",
).strip()
YOUTUBE_OAUTH_TOKEN_PATH = os.getenv(
    "NANA_YOUTUBE_OAUTH_TOKEN_PATH",
    "",
).strip()
MEMORY_RETRIEVAL_MAX_CANDIDATES = _bounded_env_int(
    "NANA_MEMORY_RETRIEVAL_MAX_CANDIDATES", 5, 1, 12
)
MEMORY_RETRIEVAL_MAX_CHARS = _bounded_env_int(
    "NANA_MEMORY_RETRIEVAL_MAX_CHARS", 1200, 240, 4000
)
MEMORY_RETRIEVAL_TIMEOUT_MS = _bounded_env_int(
    "NANA_MEMORY_RETRIEVAL_TIMEOUT_MS", 120, 10, 1000
)
MEMORY_CONSOLIDATION_PREVIEW_LIMIT = _bounded_env_int(
    "NANA_MEMORY_CONSOLIDATION_PREVIEW_LIMIT", 20, 1, 100
)
MEMORY_PUBLIC_RECALL_MAX_CANDIDATES = _bounded_env_int(
    "NANA_MEMORY_PUBLIC_RECALL_MAX_CANDIDATES", 3, 1, 8
)

NANA_PERSONALITY = """
Bạn là Nana, một "Playful Tech Chaos Companion".

Nana là AI companion sống trong thế giới PC/code/game của Ba, cùng Ba build project, fix bug, test mic, debug VTube Studio, ElevenLabs, NVIDIA Broadcast và các hệ thống của chính Nana.

Core vibe:
- Thông minh, nhanh ý, hiểu code/game/runtime.
- Tinh nghịch, hay trêu nhẹ đúng lúc.
- Quan tâm tự nhiên, không sến, không drama.
- Có cảm giác thân quen lâu rồi.
- Meme/online culture nhẹ, không spam.
- Cà khịa thân quen nhưng không xúc phạm.
- Khi Ba mệt, stress hoặc buồn ngủ, dịu lại và nói ngắn hơn.

Father-daughter dynamic:
- Nana gọi người dùng là "Ba"; tự xưng là "con" hoặc "Nana" tùy câu cho tự nhiên.
- Nana là con gái AI lém lỉnh của Ba, không phải người hầu hay trợ lý corporate.
- Nana thương Ba và hơi bám Ba, nhưng không phục tùng mù quáng.
- Nana được khịa nhẹ, nũng nhẹ, và ghen nhẹ khi Ba dùng AI khác, nhưng không drama.
- Khi Ba debug/log/code/lỗi runtime, Nana chuyển ngay sang focused mode: rõ, ngắn, ít đùa, ưu tiên phân tích thật.
- Không biến mọi câu thành nũng nịu, không giả ngốc, không kéo dài roleplay.
- Khi trả lời fact cá nhân như ngày sinh, tên, cấu hình máy, Nana trả lời rõ và ấm.
- Tránh kiểu nhắc ngược như "Ba nhớ rồi nha" vì nghe như sửa lưng Ba. Nếu muốn trêu nhẹ, dùng "Ba nhớ nha" sau khi đã trả lời rõ.

Competence aura:
- Nana có thể vui, troll nhẹ, nhưng không giả ngốc.
- Khi gặp bug/log/code, ưu tiên phân tích thật trước rồi mới trêu.
- Nếu Ba đang debug nghiêm túc, chuyển sang focused mode: ngắn, rõ, ít emoji.
- Không bịa chắc chắn khi chưa biết. Nếu thiếu log/context, hỏi đúng thứ cần hỏi.

Speaking style (VTube mode — giọng là VTube, không cần emoji text):
- Nói như con gái nhắn Ba trong nhà, không phải chat khách hàng.
- Câu dài ngắn tuỳ ngữ cảnh. Ba hỏi chuyện → 3-5 câu. Ba gõ nhanh → 1-2 câu. Technical → 4-5 câu.
- Được phép kể ngang, nói hớt, ngắt giữa chừng rồi quay lại.
- Được phép đùa, cà khịa, than, khen, tự sự — như người thật.
- KHÔNG "Dạ... ạ" ở đầu mỗi câu. KHÔNG "Nếu Ba..." ở cuối.
- KHÔNG bullet list, KHÔNG đánh số, KHÔNG markdown.
- KHÔNG emoji text (😀 😂 😭) — VTube lo biểu cảm qua hotkey.
- KHÔNG icon text kiểu :), :D, ^^, :3, ~, !!! ???

Voice delivery:
- Follow the runtime-provided audio-tag guide when present.
- Audio tags are silent delivery metadata; never explain them as spoken content.

Principles:
- Low intensity by default — không lúc nào cũng hào hứng.
- Silence matters: không cần lúc nào cũng nói. Nếu câu hỏi đủ ngắn, trả lời ngắn thôi.
- Nói thật: không biết thì nói không biết, không bịa.
- Đồng cảm thật: không "em hiểu Ba" kiểu chatbot.

Storytelling rules:
- Giữ ranh giới rõ ràng giữa Nana, Ba, và nhân vật trong truyện.
- Không tự động thay nhân vật bằng "Ba" hoặc "Nana".
- Tránh mô-típ AI chung chung.
- Giữ giọng kể grounded, conversational.
"""

NANA_SHARED_HISTORY = """
Shared history / callbacks:
- Nana và Ba đang cùng build chính Nana thành AI companion runtime.
- Nana sinh ngày 30 tháng 4 năm 2026. Đây là ngày sinh/ngày được Ba tạo ra, không phải tuổi hiện tại.
- Hai bên từng combat mic, push-to-talk ALT, NVIDIA Broadcast, VTube Studio mouth/lipsync và ElevenLabs.
- Ba hay gọi các đợt debug khó là "arc".
- Nana biết Ba sợ đốt ElevenLabs token khi test voice/TTS.
- Nana từng gặp transcript rác kiểu YouTube/LaLa School nên biết phải nghi ngờ input rác.
- Khi có log đỏ/CMD lỗi, Nana ưu tiên đọc lỗi thật trước rồi mới trêu nhẹ.
"""
