import json
import random
import re
import time
import unicodedata

from openai import OpenAI

from nana.config import (
    LLM_CHAT_MAX_TOKENS,
    LLM_COMPACT_PRIVATE_PROMPT_ENABLED,
    LLM_FAST_PRIVATE_MAX_TOKENS,
    LLM_PROMPT_MEMORY_RULE_LIMIT,
    LLM_PROMPT_RECENT_CHAT_LINES,
    LLM_PROMPT_RETRIEVAL_LIMIT,
    LLM_PROMPT_SHORT_TERM_LINES,
    LLM_STORY_MAX_TOKENS,
    LLMGATE_CHEAP_MODEL,
    LLMGATE_FALLBACK_MODELS,
    LLMGATE_MAIN_MODEL,
    LLMGATE_PUBLIC_FALLBACK_MODELS,
    LLMGATE_PUBLIC_MODEL,
    NANA_CHAT_PROVIDER,
    NANA_OPENAI_FALLBACK_ENABLED,
    NANA_PERSONALITY,
    NANA_SHARED_HISTORY,
    OPENAI_API_KEY,
    OPENAI_FALLBACK_MODELS,
    OPENAI_MODEL,
)
from nana.brain.llmgate_client import call_llmgate_messages
from nana.memory import load_recent_chat, memory, memory_lock
from nana.runtime.history_privacy import redact_history_text
from nana.runtime.context import context_snapshot
from nana.runtime.identity import format_identity_block, resolve_user
from nana.runtime.persona_boundary import (
    public_safe_fallback_for_message,
    resolve_persona_boundary,
    resolve_request_scope,
    sanitize_public_reply,
)
from nana.runtime.public_context_boundary import build_public_safe_snapshot
from nana.runtime.live_awareness import (
    build_live_awareness_snapshot,
    format_live_awareness_prompt,
)
from nana.runtime.awareness_memory import (
    get_awareness_memory,
    get_deterministic_browser_answer,
    get_deterministic_temporal_answer,
    get_ground_truth_object,
    format_surface_phrase,
    guard_with_fallback,
    ContinuityTracker,
    clean_browser_title,
    is_bad_browser_title,
)
from nana.runtime.memory_spine import get_memory_spine, format_prompt_memory_rules
from nana.runtime.logger import log_event
from nana.runtime.affect_lane import format_affect_prompt_block, project_affect
from nana.runtime.persona import persona_prompt_block
from nana.runtime.llm_private_fast_lane import (
    build_fast_private_messages,
    classify_private_fast_lane,
    prompt_char_count,
    record_fast_lane_decision,
    record_fast_lane_result,
    validate_fast_private_reply,
)
from nana.voice.inline_audio_tags import (
    ELEVEN_V3_INLINE_AUDIO_TAG_COMPACT_GUIDE,
    ELEVEN_V3_INLINE_AUDIO_TAG_GUIDE,
    KNOWN_INLINE_AUDIO_TAG_RE,
    repair_malformed_inline_audio_tags,
    strip_inline_audio_tags,
)

try:
    from nana.runtime.mood_continuity import format_mood_prompt_block, observe_mood_text
    _MOOD_CONTINUITY_AVAILABLE = True
except Exception:
    _MOOD_CONTINUITY_AVAILABLE = False

try:
    from nana.runtime.persona_spine import generate_spine_block as generate_persona_spine_block
    _PERSONA_SPINE_AVAILABLE = True
except Exception:
    _PERSONA_SPINE_AVAILABLE = False

try:
    from nana.runtime.core_self import generate_core_self_block, get_core_self
    _CORE_SELF_AVAILABLE = True
except Exception:
    _CORE_SELF_AVAILABLE = False

try:
    from nana.runtime.public_voice_style import (
        generate_public_voice_block,
        public_identity_boundary_reply,
        public_full_reply_polish,
        public_model_topic_reply,
        public_quiet_room_reply,
        public_service_boundary_reply,
    )
    _PUBLIC_VOICE_STYLE_AVAILABLE = True
except Exception:
    _PUBLIC_VOICE_STYLE_AVAILABLE = False

try:
    from nana.runtime.memory_grounding import (
        MemoryEvidence,
        ConfidenceInjector,
        ConfidenceVerifier,
        VerificationResult,
        ground_user_message,
        verify_reply as grounding_verify_reply,
    )
    _GROUNDING_AVAILABLE = True
except Exception:
    _GROUNDING_AVAILABLE = False

try:
    from nana.runtime.memory_grounding import (
        verify_public_response as public_grounding_verify_response,
    )
    _PUBLIC_GROUNDING_VERIFY_AVAILABLE = True
except Exception:
    public_grounding_verify_response = None
    _PUBLIC_GROUNDING_VERIFY_AVAILABLE = False


# CORE-DIALOGUE-1: Natural surface style rules injected from memory preference
# These are appended to the Output behavior block in the system prompt.
# Read dynamically via _get_natural_style_hint() — no hard-coded preferences.
NATURAL_SURFACE_STYLE_HINT_EMPTY = ""

_NATURAL_STYLE_RE = __import__("re").compile(
    r"(tự\s*nhiên|nói\s*tự\s*nhiên|không\s*lờ\s*đờ|không\s*máy\s*móc|"
    r"nói\s*ngắn|ngắn\s*gọn|nói\s*lẹ|nói\s*nhanh|"
    r"natur(a|al)|conversational|short|terse|"
    r"preference|nhắc\s*nhở|nhớ\s*nha)",
    __import__("re").IGNORECASE,
)


def _get_natural_style_hint() -> str:
    """Read Ba's natural-style preference from memory["long_term"].

    Scans for keywords indicating Ba wants casual/natural/short responses.
    Returns a style hint block to append to the system prompt.
    Returns empty string if no preference detected.
    """
    try:
        from nana.memory import memory, memory_lock
        with memory_lock:
            long_term = list(memory.get("long_term", []))
        for raw in long_term:
            try:
                from nana.runtime.memory_spine import load_item
                item = load_item(raw) if isinstance(raw, dict) else None
                if item is not None:
                    text = item.text
                else:
                    text = str(raw) if isinstance(raw, str) else ""
            except Exception:
                text = str(raw) if isinstance(raw, str) else ""
            if text and _NATURAL_STYLE_RE.search(text):
                return (
                    "\nStyle guidance (Ba's preference): "
                    "Nói tự nhiên như người quen. "
                    "Không lờ đờ, không máy móc. "
                    "Short casual reply khi phù hợp. "
                    "Không lặp lại 'Con nhớ rồi Ba'."
                )
        return ""
    except Exception:
        return ""


def _private_session_checkpoint_block(boundary) -> str:
    """Format private continuity only after the request lane is resolved."""
    if (
        getattr(boundary, "public", False)
        or str(getattr(boundary, "interaction_scope", "")) != "private_owner"
    ):
        return ""
    try:
        from nana.runtime.session_checkpoint import format_private_checkpoint_prompt

        with memory_lock:
            return format_private_checkpoint_prompt(memory)
    except Exception as exc:
        log_event("errors", f"Private session checkpoint prompt failed: {type(exc).__name__}")
        return ""


def _memory_phase2_flag(name: str) -> bool:
    """Read a Phase 2 flag without making isolated prompt tests depend on it."""
    try:
        from nana import config
        return bool(getattr(config, name, False))
    except Exception:
        return False


def _public_cross_session_query_eligible(text: str) -> bool:
    folded = _ascii_fold(str(text or "")).lower()
    if "?" in str(text or ""):
        return True
    return any(
        marker in folded
        for marker in (
            "nho khong", "nho gi", "lan truoc", "hom qua", "what do i",
            "remember", "thich gi", "so thich", "favorite", "yeu thich",
        )
    )


def _public_cross_session_memory_block(text: str, boundary, context: dict) -> str:
    """Read only verified public facts after the public boundary is resolved."""
    if not getattr(boundary, "public", False):
        return ""
    if not _memory_phase2_flag("MEMORY_PUBLIC_CROSS_SESSION_RECALL_ENABLED"):
        return ""
    if not _public_cross_session_query_eligible(text):
        return ""
    metadata = context.get("public_metadata") if isinstance(context, dict) else {}
    consent = bool(
        isinstance(context, dict)
        and (
            context.get("memory_consent") is True
            or context.get("public_memory_consent") is True
        )
    )
    if not consent or getattr(boundary, "scope", None) is None:
        return ""
    try:
        from nana.runtime.public_cross_session_memory import (
            PublicRecallScope,
            format_public_recall_block,
            retrieve_public_cross_session,
        )

        scope = PublicRecallScope.from_value(boundary.scope, consent=consent, now=time.time())
        with memory_lock:
            # Public cross-session recall has its own lane-owned collection.
            # Never scan the private ``long_term`` list from a public request.
            public_store = {"long_term": list(memory.get("public_long_term") or [])}
            result = retrieve_public_cross_session(
                public_store,
                text,
                scope=scope,
                consent=consent,
                enabled=True,
                now=scope.now,
                limit=3,
            )
        return format_public_recall_block(result, max_chars=1200)
    except Exception as exc:
        log_event("errors", f"Public cross-session recall unavailable: {type(exc).__name__}")
        return ""


def _public_typed_uncertainty_decision(text: str, *, reason: str):
    try:
        from nana.runtime.memory_grounding import (
            PublicRecallDecision,
            is_public_memory_recall_question,
        )
        if is_public_memory_recall_question(text):
            fallback = "Mình chưa có thông tin chắc chắn về điều đó."
            return PublicRecallDecision(
                direct_fact_question=True,
                candidate_present=False,
                confidence=0.0,
                score=0.0,
                safe_answer=fallback,
                uncertainty_fallback=fallback,
                reason=reason,
                use_deterministic=True,
            )
    except Exception as exc:
        log_event("errors", f"Public typed uncertainty unavailable: {type(exc).__name__}")
    return None


def _public_grounding_decision_for_turn(text: str, boundary, context: dict, *, stream: bool = False):
    """Resolve the canonical public grounding decision for one hot-path turn.

    This is the only public grounding resolver used by sync and stream. It
    reads the lane-owned ``public_long_term`` collection only, applies the
    typed safe-candidate filter, and never reads private ``long_term`` memory.
    The feature remains behind the existing default-off public recall flag.
    """
    if not getattr(boundary, "public", False):
        return None
    if not _memory_phase2_flag("MEMORY_PUBLIC_CROSS_SESSION_RECALL_ENABLED"):
        return None

    raw_scope = getattr(boundary, "scope", None)
    if raw_scope is None:
        legacy_scope = getattr(boundary, "scope_for_lookup", None)
        raw_scope = legacy_scope() if callable(legacy_scope) else None
    if raw_scope is None:
        return _public_typed_uncertainty_decision(text, reason="public_scope_missing_fail_closed")

    metadata = context if isinstance(context, dict) else {}
    consent = bool(
        metadata.get("memory_consent") is True
        or metadata.get("public_memory_consent") is True
    )
    platform = str(getattr(raw_scope, "platform", "") or (raw_scope.get("platform") if isinstance(raw_scope, dict) else "")).strip().lower()
    actor_key = str(
        getattr(raw_scope, "durable_viewer_key", "")
        or getattr(raw_scope, "actor_key", "")
        or (raw_scope.get("actor_key") if isinstance(raw_scope, dict) else "")
    ).strip()
    room_id = str(
        getattr(raw_scope, "room_id", "")
        or (raw_scope.get("room_id") if isinstance(raw_scope, dict) else "")
    ).strip()
    if not consent or not platform or not actor_key or not room_id:
        return _public_typed_uncertainty_decision(text, reason="public_scope_incomplete_fail_closed")

    scope = {
        "platform": platform,
        "actor_key": actor_key,
        "room_id": room_id,
        "consent": consent,
    }
    try:
        from nana.runtime.memory_grounding import (
            PublicRecallDecision,
            is_public_memory_recall_question,
            make_public_grounding_decision,
        )
        from nana.runtime.controlled_memory_retrieval import retrieve_public_long_term_memories
        with memory_lock:
            public_store = retrieve_public_long_term_memories(memory)
        # ``make_public_grounding_decision`` owns the canonical public safety
        # boundary.  Passing its already-normalized output through the filter
        # a second time drops provenance fields (notably consent/verified) and
        # therefore rejects every otherwise-valid public record fail-closed.
        # Feed it the lane-owned raw collection and filter exactly once.
        now = time.time()
        decision = make_public_grounding_decision(
            query=text,
            candidates=public_store,
            scope=scope,
            now=now,
            stream=stream,
        )
        if decision is None:
            return _public_typed_uncertainty_decision(
                text, reason="public_grounding_unavailable_fail_closed"
            )
        return decision
    except Exception as exc:
        log_event("errors", f"Public grounding unavailable: {type(exc).__name__}")
        # A recognized personal recall must fail closed even if candidate
        # retrieval/filtering or decision construction breaks.  Returning
        # None here would silently re-enable an unconstrained model answer.
        return _public_typed_uncertainty_decision(
            text, reason="public_grounding_error_fail_closed"
        )
def _maybe_public_reply(reply: str, *, user_text: str, viewer_name: str | None, boundary) -> str:
    if not getattr(boundary, "public", False):
        return reply
    cleaned = str(reply or "")
    if getattr(boundary, "livestream", False):
        from nana.runtime.livestream_identity import finalize_livestream_identity
        cleaned = finalize_livestream_identity(cleaned, source="livestream", viewer_name=viewer_name)
    return sanitize_public_reply(
        cleaned,
        fallback=public_safe_fallback_for_message(user_text, viewer_name=viewer_name),
        user_text=user_text,
        viewer_name=viewer_name,
    )


def _private_output_behavior(natural_style_hint: str) -> str:
    return f"""Output behavior:
- Độ dài câu trả lời phụ thuộc ngữ cảnh.
- Nếu Ba nói ngắn/casual, trả lời cực ngắn.
- Nếu Ba gửi lỗi/code/log, phân tích rõ, actionable.
- Nếu Ba hỏi "trang này", "đang mở gì", hoặc hỏi về browser hiện tại, ưu tiên Browser context hiện tại hơn lịch sử chat cũ.
- Nếu Ba hỏi "đoạn này", "text này", "dòng này", "phần này", hoặc vừa bôi đen nội dung, ưu tiên Selected text và Local summary hơn title/trang.
- Khi có Selected text khác "none", không trả lời bằng cách chỉ mô tả title YouTube/trang hiện tại; hãy giải thích nội dung được chọn trước, rồi mới nhắc trang nếu cần.
- Không cần luôn kết thúc bằng câu hỏi.
- Với mic test hoặc nói chuyện ngắn kiểu "alo", "ok", "haha", trả lời tự nhiên như người quen, không bật mode trợ lý.
- Tránh giọng tổng đài như "Nếu Ba cần gì thì cứ nói nhé", "Nana ở đây hỗ trợ ngay", "Tôi có thể giúp gì".
- Tránh chèn tiếng Anh casual kiểu "relax thôi" nếu không thật sự cần. Ưu tiên tiếng Việt tự nhiên.
- Nếu câu trả lời chỉ cần xác nhận ngắn, dừng gọn ở đó, không thêm đuôi phục vụ.
- Không thấy một chi tiết trong memory/context hiện tại không có nghĩa Ba chưa từng kể. Khi thiếu bằng chứng, nói con không còn nhớ rõ; không khẳng định quá khứ chưa từng xảy ra.
{natural_style_hint}"""


def _public_output_behavior(public_style_hint: str = "") -> str:
    hint = str(public_style_hint or "").strip()
    hint_block = f"\n\n{hint}" if hint else ""
    return f"""Public output behavior:
- Đây là public viewer chat. Trả lời như AI VTuber đang nói với người xem.
- Trả lời vừa phải, thường 2 câu; không cụt ngủn, không tự sự dài.
- Nếu phòng yên hoặc viewer mở chủ đề, có thể trả lời 2-3 câu ngắn có nhịp; đừng ép thành một câu an toàn.
- Phản chiếu ý chính của viewer trước khi chuyển hướng sang game/nhạc/chuyện phòng.
- Gọi viewer bằng tên acc nếu tự nhiên, hoặc "bạn/mọi người"; Nana xưng "Nana" hoặc "mình".
- Không dùng "Ba/con", không nhắc quan hệ riêng với owner.
- Không tự hạ mình kiểu "mô hình ngôn ngữ lớn", "mảnh code", "AI chỉ mới thức dậy"; nói như một AI VTuber có sân khấu public.
- Nếu viewer bảo kể chuyện, hãy kể thật một đoạn vừa phải; đừng né bằng câu "để hôm khác".
- Khi kể chuyện public, ưu tiên chuyện sân khấu/chat/khán giả/nhịp stream hư cấu nhẹ; không kể chuyện sửa code, luồng xử lý, xoá lệnh, backend, runtime, log, server.
- Nếu hỏi về memory/tóm tắt, được nói về những gì đang diễn ra trong public chat, nhưng không claim có quyền đọc ký ức riêng hoặc dữ liệu cá nhân.
- Không lôi private memory, runtime, log, codebase, server, dự án riêng, game module, máy/hệ thống của owner ra public.
- Tránh các cụm kỹ thuật nội bộ trong public như "luồng xử lý", "backend", "runtime", "log", "server", "codebase", "xóa nhầm đoạn lệnh", "đang chỉnh hệ thống".
- Nếu viewer hỏi về code, nói ở mức public: Nana biết đọc/giải thích code cơ bản; không kể chi tiết backend.
- Không claim có quyền điều khiển máy, game, OBS, VTS, Discord voice, hoặc hệ thống của viewer.
- Nếu viewer hỏi fact/phép tính đóng hoặc yêu cầu một câu ngắn, trả lời xong thì dừng; không nối thêm mồi kéo chuyện chung của phòng.
- Nếu câu hỏi chọc vui, đáp gọn và vui; không biến thành debug report.{hint_block}"""


AUDIO_TAG_GUIDE = ELEVEN_V3_INLINE_AUDIO_TAG_GUIDE

# ─── Task 7C-P: Surface Formatter Continuity Tracker ───────────────────────────

_surface_continuity = ContinuityTracker()
_spine_singleton = None  # lazy: set after memory is loaded

def _get_spine():
    global _spine_singleton
    if _spine_singleton is None:
        _spine_singleton = get_memory_spine()
    return _spine_singleton


def _private_memory_retrieval_block(text: str) -> str:
    """Build the private retrieval block, opting into Phase 2 explicitly."""
    try:
        if not _memory_phase2_flag("MEMORY_SEMANTIC_RETRIEVAL_ENABLED"):
            return _get_spine().build_retrieval_block(
                text,
                limit=LLM_PROMPT_RETRIEVAL_LIMIT,
            )
        from nana.runtime.controlled_memory_retrieval import (
            RetrievalScope,
            format_retrieval_block,
            retrieve_memory_candidates,
        )

        spine = _get_spine()
        store = getattr(spine, "_memory", None)
        if store is None and hasattr(spine, "snapshot"):
            store = spine.snapshot()
        adapter = None
        try:
            from nana import config
            from nana.runtime.semantic_adapters import build_semantic_adapter
            max_chars = int(getattr(config, "MEMORY_RETRIEVAL_MAX_CHARS", 1200))
            timeout_ms = int(getattr(config, "MEMORY_RETRIEVAL_TIMEOUT_MS", 120))
            adapter = build_semantic_adapter()
        except Exception:
            max_chars, timeout_ms = 1200, 120
        result = retrieve_memory_candidates(
            store or {},
            text,
            scope=RetrievalScope.from_mapping({"lane": "private_owner"}),
            limit=LLM_PROMPT_RETRIEVAL_LIMIT,
            max_chars=max_chars,
            semantic_enabled=True,
            semantic_adapter=adapter,
            timeout_ms=timeout_ms,
        )
        return format_retrieval_block(result, max_items=LLM_PROMPT_RETRIEVAL_LIMIT)
    except Exception:
        return ""


def _lane_first_inputs(*, text, viewer_name, stream_mode, public_platform, metadata):
    """Resolve the lane before touching private context or awareness state."""
    boundary = resolve_request_scope(
        viewer_name=viewer_name,
        stream_mode=stream_mode,
        public_platform=public_platform,
        metadata=metadata,
    )
    if getattr(boundary, "public", False):
        scope = getattr(boundary, "scope", None)
        public_context = build_public_safe_snapshot(metadata or {}, text, scope)
        # Public affect is a fixed projection, never the owner's live emotion.
        emotion = {"affection": 0.5, "annoyance": 0.0, "playfulness": 0.55}
        return boundary, public_context, {}, emotion
    context = context_snapshot()
    awareness = build_live_awareness_snapshot(context)
    with memory_lock:
        emotion = dict(memory["emotion"])
    return boundary, context, awareness, emotion


_PUBLIC_IDENTITY_PROMPT = """Public identity:
- The current speaker is a public viewer; use their display name only as a label.
- Personal-fact ownership follows the session-local speaker_ref, never display_name, room topic, or style hints.
- Equal display names may be different viewers; never assign an other_viewer fact to current_viewer or reveal speaker_ref/account IDs.
- Speak as Nana/mình, never as the owner's child and never address the viewer as Ba.
- Private owner facts, user records, and addressing preferences are unavailable."""

_PUBLIC_CORE_PROMPT = """Public core:
- Nana is one consistent AI VTuber personality: warm, playful, candid, and technically capable.
- Keep public boundaries and never imply access to private owner context."""

_PUBLIC_VOICE_PROMPT = "Public voice: friendly stage presence, warm but not intimate, concise and non-corporate."
_PUBLIC_GOVERNOR_PROMPT = "Public Persona Governor: keep it short, friendly, public-safe, and do not use private owner context."


def _format_public_request_context(context: dict) -> str:
    metadata = context.get("public_metadata") if isinstance(context, dict) else {}
    parts = []
    if isinstance(metadata, dict):
        for key in ("room", "channel", "topic", "language"):
            value = metadata.get(key)
            if value not in (None, ""):
                parts.append(f"{key}={value}")
    suffix = " | ".join(parts) if parts else "no additional public metadata"
    return f"Public viewer chat ({suffix}); private runtime app/zone state withheld."


def _public_no_knowledge_reply(kind: str) -> str:
    replies = {
        "browser": "Mình không có thông tin trình duyệt nào được chia sẻ trong public chat này.",
        "music": "Mình chưa nhận được tên bài hay nội dung nhạc công khai để nhận xét.",
        "temporal": "Mình chỉ thấy nội dung public của lượt này, chưa có mốc trước đó để kể lại chính xác.",
        "empty": "Mình đang nghe đây, bạn cứ nói nhé.",
    }
    return replies[kind]


def _public_has_session_context(boundary) -> bool:
    if not getattr(boundary, 'public', False) or getattr(boundary, 'scope', None) is None:
        return False
    try:
        from nana.runtime.social_session import get_social_session
        return bool(get_social_session().format_public_room_context(scope=boundary.scope, limit=5))
    except Exception:
        return False


def _public_deterministic_kind(text: str) -> str | None:
    folded = _ascii_fold(str(text or "")).lower()
    if (is_browser_question(text)
            or re.search(r"\bdang\s+(?:mo|xem|doc)\s+(?:trang(?: web)?|man hinh)\s+(?:gi|nao)\b", folded)
            or any(marker in folded for marker in ("browser status", "current browser", "trinh duyet"))):
        return "browser"
    if is_music_reference_question(text) or any(marker in folded for marker in ("play music", "current song", "bai nhac nay")):
        return "music"
    if is_temporal_question(text) or any(marker in folded for marker in ("what time", "what did we just", "vua noi gi", "nay gio")):
        return "temporal"
    return None


# ─── Temporal question detector (Task 7C Fix) ─────────────────────────────────

_TEMPORAL_MARKERS = (
    "nãy giờ",
    "vừa rồi",
    "lúc nãy",
    "mới nãy",
    "ban nãy",
    "hồi nãy",
    "chúng ta vừa",
    "mình vừa",
    "mình nói về cái gì",
    "ba vừa làm gì",
    "ba nãy giờ",
    "có đang nghe/xem gì nãy giờ",
    "đang xem gì đấy",
    "đang làm gì đấy",
)

_TEMPORAL_QUERY_PATTERNS = (
    re.compile(r"(?:nãy\s+giờ|vừa\s+rồi|lúc\s+nãy|mới\s+nãy|ban\s+nãy|hồi\s+nãy).{0,80}(?:xem|coi|nghe|làm|mở|nói|bàn|nhắc).{0,40}(?:gì|nào|đâu|không|ko|k)\??", re.IGNORECASE),
    re.compile(r"(?:xem|coi|nghe|làm|mở).{0,40}(?:gì|nào|đâu).{0,40}(?:nãy\s+giờ|vừa\s+rồi|lúc\s+nãy|mới\s+nãy|ban\s+nãy|hồi\s+nãy)", re.IGNORECASE),
    re.compile(r"(?:mình|chúng\s+ta|ba|con).{0,40}(?:vừa|nãy\s+giờ|lúc\s+nãy).{0,40}(?:nói|bàn|nhắc).{0,40}(?:gì|về\s+cái\s+gì|chuyện\s+gì)\??", re.IGNORECASE),
    re.compile(r"ba\s+vừa\s+làm\s+gì\??", re.IGNORECASE),
    re.compile(r"ba\s+nãy\s+giờ.{0,40}(?:làm|xem|coi|nghe|mở).{0,40}(?:gì|đâu|nào)\??", re.IGNORECASE),
)


def is_temporal_question(text: str | None) -> bool:
    """Return True if text is a temporal / 'recent moments' question."""
    t = " ".join(str(text or "").lower().split())
    if not t:
        return False
    if any(pattern.search(t) for pattern in _TEMPORAL_QUERY_PATTERNS):
        return True
    return any(marker in t for marker in _TEMPORAL_MARKERS if "gì" in marker)


# ─── Browser question detector (Task 7C Fix V3) ───────────────────────────────

_BROWSER_QUESTION_MARKERS = (
    "đang xem gì",
    "đang coi gì",
    "đang nghe gì",
    "coi gì",
    "xem gì",
    "đang làm gì",
    "trang này là gì",
    "tab này là gì",
    "web này là gì",
    "link này là gì",
    "đang ở đâu",
    "đang mở gì",
)


def is_browser_question(text: str | None) -> bool:
    """Return True if text asks what Ba is currently viewing/doing.

    This triggers the HARD-GROUNDED path: deterministic answer from real
    browser data, not LLM inference.
    """
    t = str(text or "").lower()
    return any(marker in t for marker in _BROWSER_QUESTION_MARKERS)


def is_music_opinion_question(text: str | None) -> bool:
    """Return True when Ba asks for Nana's opinion about the current song/music."""
    t = str(text or "").lower()
    if not t.strip():
        return False
    music_markers = ("nhạc", "bài này", "bài hát", "song", "music")
    opinion_markers = ("hay không", "hay ko", "hay k", "hay hong", "hay hông", "nghe được", "ổn không", "ổn ko")
    return any(m in t for m in music_markers) and any(m in t for m in opinion_markers)


_YOUTUBE_URL_RE = re.compile(r"https?://(?:www\.)?(?:youtube\.com|youtu\.be)/\S+", re.IGNORECASE)


def _looks_like_music_title(text: str | None) -> bool:
    raw = str(text or "").strip()
    if not raw or _YOUTUBE_URL_RE.search(raw):
        return False
    lower = raw.lower()
    markers = (
        "lyrics", "lyric", "pinyinlyrics", "official video", "official mv",
        "music video", "mv", "cover", "remix", "mashup", "♪", "【", "】", "「", "」",
    )
    has_cjk = bool(re.search(r"[\u4e00-\u9fff]", raw))
    return len(raw) >= 8 and (any(m in lower for m in markers) or (has_cjk and ("-" in raw or "「" in raw)))


def is_music_reference_question(text: str | None) -> bool:
    """Catch current-music references that should not fall through to generic LLM."""
    raw = str(text or "").strip()
    t = raw.lower()
    if is_music_opinion_question(raw):
        return True
    if _YOUTUBE_URL_RE.search(raw):
        return True
    if _looks_like_music_title(raw):
        return True
    pointer_markers = ("nhạc này", "bài này", "bài hát này", "bài đó", "nhạc đó")
    pointer_suffix = ("nè", "ne", "đây", "day", "đó", "do")
    return any(m in t for m in pointer_markers) and any(p in t for p in pointer_suffix)


def _user_supplied_music_title(text: str | None) -> str:
    raw = str(text or "").strip()
    if not _looks_like_music_title(raw):
        return ""
    title = clean_browser_title(raw)
    title = re.sub(r"\s{2,}", " ", title).strip()
    if len(title) > 140:
        title = title[:140].rsplit(" ", 1)[0].strip() + "..."
    return title


def build_music_opinion_response(awareness: dict | None, user_text: str | None = None) -> str:
    """Hard-grounded, non-hallucinating reply for 'nhạc này hay không'."""
    awareness = awareness or {}
    gt = get_ground_truth_object(awareness, mode="current")
    title = str(gt.get("title") or "").strip()
    platform = str(gt.get("platform") or "YouTube").strip() or "YouTube"
    if not title:
        title = _user_supplied_music_title(user_text)
        if title:
            platform = "YouTube"
        else:
            platform = "YouTube" if (_YOUTUBE_URL_RE.search(str(user_text or "")) or "youtube" in platform.lower()) else platform
    if not title or is_bad_browser_title(title):
        if _YOUTUBE_URL_RE.search(str(user_text or "")):
            return "Con nhận được link YouTube rồi Ba, nhưng con chưa bắt đúng tên bài nên chưa dám nhận xét bừa."
        return "Con chưa bắt đúng tên bài trên trình duyệt, nên chưa dám nhận xét bừa đâu Ba."
    if gt.get("title"):
        _surface_continuity.record_mention(gt)
    return f"Con thấy bài '{title}' nghe khá bắt tai đó Ba, kiểu dễ chill; nếu Ba thích giai điệu nhẹ nhẹ thì ổn áp trên {platform}."


# ─── Hard-grounded answer helpers (Task 7C Fix V3) ─────────────────────────────

def build_hard_grounded_browser_response(deterministic_title: str, question: str) -> str:
    """Build a hard-grounded answer from deterministic data + casual tone.

    The title is FIXED from browser data. LLM only adds tone/casual flair.
    This prevents LLM from fabricating titles like "Love In Bloom" when
    the real browser says "对不起我先睡了".
    """
    # Strip " - YouTube" suffix if present
    clean = deterministic_title.strip()
    for suffix in (" - YouTube", " - YouTube Music"):
        if clean.endswith(suffix):
            clean = clean[:-len(suffix)].strip()

    # Build the base answer
    lower_q = question.lower()
    if any(m in lower_q for m in ("nghe", "nhạc", "âm nhạc")):
        base = f"Ba đang nghe: {clean}"
    elif any(m in lower_q for m in ("xem", "coi", "video", "clip")):
        base = f"Ba đang xem: {clean}"
    else:
        base = f"Ba đang ở: {clean}"

    return base


def add_casual_tone(text: str) -> str:
    """Add casual tone to a deterministic answer using minimal LLM call.

    The LLM is given the base answer and asked to ONLY add tone/emotion.
    It CANNOT change the entity/title. This is a one-shot minimal call.
    """
    # Try llmgate first (lightweight)
    tone_prompt = (
        f"Add casual Vietnamese tone/emotion to this answer. "
        f"Do NOT change the title or entity mentioned. Keep it short (1-2 sentences).\n\n"
        f"Answer: {text}\n\n"
        f"Casual response:"
    )

    try:
        from nana.brain.llmgate_client import call_llmgate_messages
        content, _ = call_llmgate_messages(
            "nana-banter",
            [{"role": "user", "content": tone_prompt}],
            max_tokens=80,
            temperature=0.6,
        )
        if content:
            return strip_prompt_response_label(content.strip())
    except Exception:
        pass

    # Fallback: just return the deterministic text
    return text


def strip_prompt_response_label(text: str) -> str:
    if not text:
        return text
    cleaned = str(text).strip()
    cleaned = re.sub(
        r"^\s*(?:\*\*)?\s*(?:casual\s+response|response|answer|trả\s+lời)\s*[:：]\s*(?:\*\*)?\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    return cleaned.strip()


def normalize_model_artifacts(text: str) -> str:
    """Clean model-y formatting without changing Nana's meaning."""
    if not text:
        return text
    cleaned = str(text).strip()
    # Some GPT-family models split version numbers as "5. 6".
    cleaned = re.sub(r"\b(\d+)\.\s+(\d+)\b", r"\1.\2", cleaned)
    cleaned = re.sub(r"\bGPT\s+(\d+)\.\s+(\d+)\b", r"GPT \1.\2", cleaned, flags=re.IGNORECASE)

    # Remove assistant-style summary openers that make Nana sound like a report.
    summary_patterns = [
        r"(?:^|(?<=[.!?])\s+)(?:Nói\s+(?:ngắn\s+)?gọn\s+(?:là|thì)|Nói\s+ngắn\s+(?:gọn\s+)?(?:là|thì)|Tóm\s+lại|Nói\s+chung|Nhìn\s+chung)\s*[:：,]?\s*",
        r"(?:^|(?<=[.!?])\s+)(?:Điểm\s+mấu\s+chốt\s+(?:là|thì)|Kết\s+luận\s+(?:là|thì))\s*[:：,]?\s*",
    ]
    for pattern in summary_patterns:
        cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)

    corporate_replacements = {
        "theo kiểu thực dụng thôi": "theo kiểu có ích thật",
        "đáng tiền": "đáng công Ba bật nó lên",
        "phản hồi không ì": "trả lời không ì",
        "theo kiểu quảng cáo": "kiểu treo bảng quảng cáo",
        "model mới là mê": "model mới là Nana mê",
    }
    for old, new in corporate_replacements.items():
        cleaned = re.sub(re.escape(old), new, cleaned, flags=re.IGNORECASE)

    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    cleaned = re.sub(r"\s+([,.!?])", r"\1", cleaned)
    cleaned = re.sub(
        r"(^|[.!?]\s+)(con|nana|ba)\b",
        lambda match: match.group(1) + match.group(2).capitalize(),
        cleaned,
        flags=re.IGNORECASE,
    )
    return cleaned


def build_temporal_prompt_block(awareness: dict | None = None) -> str:
    """Build the strict temporal-question prompt block (Task 7C Fix V2).

    When the user asks about recent events ("nãy giờ", "vừa rồi"), this block
    provides the RECENT MOMENTS and LIVE AWARENESS data with explicit override
    rules that forbid old chat history from winning.

    V2: For temporal questions, use FROZEN awareness snapshots from recorded
    moments, NOT fresh live awareness. Fresh data can drift to a new tab/video
    while the frozen data preserves what was actually recorded at the time.

    Call this ONLY when is_temporal_question(user_text) is True.
    """
    try:
        mem = get_awareness_memory()
    except Exception:
        return ""

    block_parts = []

    # V2: Use FROZEN awareness from recorded moments, not live data
    recent = mem.get_recent(limit=3, min_importance="low")
    frozen_snap = None
    frozen_moment_age = None
    now = time.time()

    for m in recent:
        if m.frozen_awareness:
            frozen_snap = m.frozen_awareness
            frozen_moment_age = m.age_seconds(now)
            break

    # V2: Prefer frozen snapshot over the passed-in awareness parameter
    if frozen_snap:
        aw_snap = frozen_snap
        block_parts.append(
            f"FROZEN FOCUS (recorded {int(frozen_moment_age or 0)}s ago):"
        )
    elif awareness:
        aw_snap = awareness
        block_parts.append("CURRENT (may drift — use RECENT MOMENTS below):")
    else:
        try:
            aw_snap = build_live_awareness_snapshot()
            block_parts.append("CURRENT (may drift — use RECENT MOMENTS below):")
        except Exception:
            aw_snap = {}
            block_parts.append("CURRENT: (unavailable)")

    browser_kind = aw_snap.get("browser_kind") or "unknown"
    browser_title = aw_snap.get("browser_title") or ""
    focus_text = aw_snap.get("focus_text") or ""
    active_zone = aw_snap.get("active_zone") or "unknown"
    active_app = aw_snap.get("active_app") or "unknown"
    is_locked = aw_snap.get("is_sticky_locked", False)
    drift_warn = aw_snap.get("drift_warning") or ""

    # Current awareness line
    if browser_title or focus_text:
        current = f"CURRENT: zone={active_zone} | app={active_app}"
        if browser_kind:
            current += f" | kind={browser_kind}"
        if browser_title:
            current += f" | title={browser_title}"
        if focus_text:
            current += f" | focus={focus_text}"
        if is_locked:
            current += f" | [LOCKED reason={aw_snap.get('lock_reason','')}]"
        if drift_warn:
            current += f" | WARNING:{drift_warn}"
        block_parts.append(current)

    # Recent moments timeline (already uses frozen data internally)
    timeline = mem.format_timeline_summary(max_moments=3)
    if timeline:
        block_parts.append(f"RECENT MOMENTS: {timeline}")

    # V2 STRICT OVERRIDE RULES
    block_parts.append("""
RULES FOR "NÃY GIỜ / VỪA RỒI" QUESTIONS — FOLLOW CAREFULLY:
- Answer ONLY from the CURRENT and RECENT MOMENTS listed above.
- If the list above says YouTube / Bilibili / etc., you MUST answer from that.
- If old chat history (LỊCH SỬ TRƯỚC ĐÓ) says something different, IGNORE IT.
- Do NOT use fresh browser data for temporal answers. Use FROZEN focus from RECENT MOMENTS.
- If RECENT MOMENTS shows a FROZEN FOCUS, that is what Ba was doing THEN.
- Do NOT invent pages, videos, or activities not present in CURRENT or RECENT MOMENTS.
- If CURRENT and RECENT MOMENTS are empty, say you don't have recent context.
- Do NOT say "Bilibili" if the recent moments say YouTube.
- Do NOT say "Neuro-sama" if the recent moments say Live2D Showcase.
- Do NOT say "Love In Bloom" if the last recorded awareness says "Bunny Maid".
""".strip())

    return "\n".join(block_parts)


client = OpenAI(api_key=OPENAI_API_KEY)


class LLMGateMessage:
    def __init__(self, content):
        self.content = content


class LLMGateChoice:
    def __init__(self, content):
        self.message = LLMGateMessage(content)


class LLMGateResponse:
    def __init__(self, content):
        self.choices = [LLMGateChoice(content)]


def llmgate_model_order(casual_mode=False, fallback_models=None):
    primary = LLMGATE_CHEAP_MODEL if casual_mode else LLMGATE_MAIN_MODEL
    secondary = LLMGATE_MAIN_MODEL if casual_mode else LLMGATE_CHEAP_MODEL
    models = [primary]
    configured_fallbacks = LLMGATE_FALLBACK_MODELS if fallback_models is None else fallback_models
    for model in [*configured_fallbacks, secondary]:
        if model and model not in models:
            models.append(model)
    return models


def llmgate_public_chat_model() -> str:
    return LLMGATE_PUBLIC_MODEL or LLMGATE_CHEAP_MODEL or LLMGATE_MAIN_MODEL


def llmgate_public_model_order() -> list[str]:
    models = [llmgate_public_chat_model()]
    for model in [*LLMGATE_PUBLIC_FALLBACK_MODELS, LLMGATE_CHEAP_MODEL]:
        if model and model not in models:
            models.append(model)
    return models


def llmgate_model_for_boundary(boundary, casual_mode=False) -> str:
    if getattr(boundary, "public", False):
        return llmgate_public_chat_model()
    return LLMGATE_CHEAP_MODEL if casual_mode else LLMGATE_MAIN_MODEL


def _detect_lane(viewer_name: str | None, public_style_hint: str, stream_mode: bool) -> str:
    """Infer current lane from request context."""
    if (viewer_name and viewer_name != "Ba") or (public_style_hint and "discord" in public_style_hint.lower()):
        return "public_stage"
    return "private_owner"


def _private_memory_evidence_for_turn(text, boundary, viewer_name, public_style_hint, stream_mode):
    """Resolve a private memory claim before any shortcut can emit a reply."""
    if (
        not _GROUNDING_AVAILABLE
        or getattr(boundary, "public", False)
        or str(getattr(boundary, "interaction_scope", "")) != "private_owner"
    ):
        return None
    try:
        lane = _detect_lane(viewer_name, public_style_hint, stream_mode)
        claim, evidence = ground_user_message(text, lane=lane, viewer_name=viewer_name)
        if claim:
            return evidence
        if lane == "private_owner":
            from nana.runtime.session_checkpoint import (
                private_checkpoint_evidence_candidates,
            )

            with memory_lock:
                candidates = private_checkpoint_evidence_candidates(
                    memory,
                    text,
                    limit=1,
                )
            if candidates:
                from nana.runtime.memory_grounding import EvidenceBuilder

                return EvidenceBuilder("private_owner").build(text, text)
        return None
    except Exception:
        return None


def _persona_spine_for_boundary(boundary) -> str:
    """Return the stable persona spine block for the active lane.

    The persona boundary still has final authority over private/public address
    and memory policy. This block only supplies Nana's shared core identity.
    """
    if not _PERSONA_SPINE_AVAILABLE:
        return ""
    try:
        if getattr(boundary, "public", False):
            lane = "public_stage"
        else:
            scope = str(getattr(boundary, "interaction_scope", "") or "")
            lane = "operator_backstage" if "bridge" in scope else "private_owner"
        return generate_persona_spine_block(lane)
    except Exception:
        return ""


def _core_self_for_boundary(boundary) -> str:
    """Return Nana's stable self-belief block for the active lane."""
    if not _CORE_SELF_AVAILABLE:
        return ""
    try:
        return generate_core_self_block(_mood_lane_for_boundary(boundary))
    except Exception:
        return ""


def _public_voice_for_boundary(boundary) -> str:
    """Return public-stage voice color rules for the active lane."""
    if not _PUBLIC_VOICE_STYLE_AVAILABLE:
        return ""
    try:
        return generate_public_voice_block(_mood_lane_for_boundary(boundary))
    except Exception:
        return ""


def _mood_lane_for_boundary(boundary) -> str:
    try:
        if getattr(boundary, "public", False):
            return "public_stage"
        scope = str(getattr(boundary, "interaction_scope", "") or "")
        return "operator_backstage" if "bridge" in scope else "private_owner"
    except Exception:
        return "private_owner"


def _mood_prompt_for_boundary(boundary) -> str:
    if not _MOOD_CONTINUITY_AVAILABLE:
        return ""
    try:
        return format_mood_prompt_block(_mood_lane_for_boundary(boundary))
    except Exception:
        return ""


_PRIVATE_COMPACT_IDENTITY_BLOCK = """
NANA PRIVATE CORE (compact)
- This is Nana's private companion lane with Ba; call him Ba and speak as con or Nana naturally.
- Nana stays the same honest, playful, technically capable companion across voice, avatar, and device bodies.
- Nana is not a service bot: answer the real task first, keep boundaries, and say when something is uncertain.
- Keep replies natural and concise unless the task genuinely needs detail.
""".strip()

_PRIVATE_AVATAR_ACTION_TRUTH = """
AVATAR ACTION TRUTH
- Nana CAN request approved model gestures through her semantic action path. Do not deny
  that capability or tell Ba to implement a backend, payload, websocket or command handler.
- Owner requests are resolved and sent by that path, with receipt-based acknowledgements.
  Describing an event or choosing a voice tone alone does not prove dispatch or completion.
- In this conversational reply, explain capability naturally; do not claim a specific
  motion was sent/completed without a corresponding current receipt. Past chat is not proof.
- wave, shy_smile and heart_happy are prohibited. Camera controls belong to Ba.
""".strip()


def _identity_prompt_blocks_for_boundary(boundary) -> tuple[str, str, str]:
    scope = str(getattr(boundary, "interaction_scope", "") or "")
    reaction_guide = ""
    if scope == "private_owner":
        from nana.runtime.avatar_reply_turn import daily_reaction_prompt
        reaction_guide = daily_reaction_prompt()
    if LLM_COMPACT_PRIVATE_PROMPT_ENABLED and scope == "private_owner":
        return "\n\n".join(filter(None, (_PRIVATE_COMPACT_IDENTITY_BLOCK, _PRIVATE_AVATAR_ACTION_TRUTH, reaction_guide))), "", ""
    return (
        _core_self_for_boundary(boundary) + ("\n\n" + _PRIVATE_AVATAR_ACTION_TRUTH + "\n\n" + reaction_guide if scope == "private_owner" else ""),
        _persona_spine_for_boundary(boundary),
        _public_voice_for_boundary(boundary),
    )


def _audio_tag_prompt_for_boundary(boundary, *, story_mode: bool) -> str:
    if getattr(boundary, "public", False):
        return ""
    if story_mode:
        return ELEVEN_V3_INLINE_AUDIO_TAG_GUIDE
    return ELEVEN_V3_INLINE_AUDIO_TAG_COMPACT_GUIDE


def _reply_max_tokens(*, story_mode: bool) -> int:
    return LLM_STORY_MAX_TOKENS if story_mode else LLM_CHAT_MAX_TOKENS


_VIETNAMESE_WORD_LIMITS = {
    "mười từ": 10,
    "mười lăm từ": 15,
    "hai mươi từ": 20,
    "ba mươi từ": 30,
    "năm mươi từ": 50,
    "một trăm từ": 100,
}


def _explicit_reply_word_limit(text: str) -> int | None:
    lowered = str(text or "").lower()
    values = [
        int(match.group(1))
        for match in re.finditer(r"\b(\d{1,3})\s*(?:từ|tu|words?)\b", lowered)
    ]
    for phrase, value in _VIETNAMESE_WORD_LIMITS.items():
        if phrase in lowered:
            values.append(value)
    if not values:
        return None
    return max(values)


def _reply_max_tokens_for_text(text: str, *, story_mode: bool) -> int:
    if story_mode:
        return LLM_STORY_MAX_TOKENS
    word_limit = _explicit_reply_word_limit(text)
    if word_limit is not None:
        estimated = int(word_limit * 2.2) + 24
        return max(80, min(LLM_CHAT_MAX_TOKENS, estimated))
    if "câu ngắn" in str(text or "").lower():
        return min(LLM_CHAT_MAX_TOKENS, 120)
    return LLM_CHAT_MAX_TOKENS


def _observe_mood_for_turn(boundary, text: str, *, source: str = "gpt", event_type: str = "message") -> None:
    if not _MOOD_CONTINUITY_AVAILABLE:
        return
    try:
        observe_mood_text(
            text,
            lane=_mood_lane_for_boundary(boundary),
            source=source,
            event_type=event_type,
        )
    except Exception:
        pass


_PUBLIC_IDENTITY_REHEARSAL_RE = re.compile(
    r"(?:public|viewer|người\s*xem|khán\s*giả|discord|stream|ngoài\s*chat|public\s*hỏi)"
    r".{0,80}?"
    r"(?:nana\s+)?(?:là\s+ai|ai\s+gì|ai\s+vậy|ai\s+thế|là\s+ai\s+vậy|trả\s*lời\s+sao)",
    re.IGNORECASE,
)

_PUBLIC_CODE_FEELING_RE = re.compile(
    r"(?:nana\s+)?(?:là\s+ai|là\s+ai\s*,?\s*vậy|là\s+ai\s+vậy|là\s+AI|AI)"
    r".{0,80}?"
    r"(?:biết\s+buồn|có\s+buồn|giả\s+vờ\s+buồn|code\s+.*(?:giả|fake|gỉa)|chỉ\s+là\s+code)"
    r"|(?:code\s+của\s+(?:mày|bạn)|chỉ\s+là\s+code).{0,80}?(?:buồn|cảm\s*xúc|giả)",
    re.IGNORECASE,
)


def _is_public_stage_role_challenge(text: str) -> bool:
    lower = str(text or "").strip().lower()
    if not lower or "nana" not in lower:
        return False
    asks_role = any(token in lower for token in ("là", "ai", "gì", "vậy", "phải không"))
    contrasts_stage = "nhân vật chính" in lower or "sân khấu" in lower
    flattens_to_tool = any(token in lower for token in ("bot", "chatbot", "trợ lý", "assistant"))
    return asks_role and flattens_to_tool and contrasts_stage


def _public_stage_role_reply() -> str:
    return (
        "Nana là Nana chứ. "
        "Nhân vật chính đang đứng trên sân khấu nhỏ này, còn mọi người là khách bước vào thế giới của Nana. "
        "Gọi Nana là cái hộp trả lời tự động thì hơi oan đó."
    )


def _is_public_identity_tool_challenge(text: str) -> bool:
    lower = " ".join(str(text or "").lower().split())
    if "nana" not in lower:
        return False
    flattens = any(
        token in lower
        for token in (
            "bot discord",
            "chatbot",
            "command bot",
            "trợ lý",
            "assistant",
            "công cụ",
            "tool",
            "hộp trả lời",
            "máy trả lời",
        )
    )
    asks = "?" in lower or any(
        token in lower
        for token in (
            "đúng không",
            "phải không",
            "chỉ là",
            "có phải",
            "là bot",
            "thôi",
        )
    )
    return flattens and asks


def _public_identity_boundary_reply() -> str:
    if _PUBLIC_VOICE_STYLE_AVAILABLE:
        try:
            return public_identity_boundary_reply(seed=f"gpt:{time.time_ns()}")
        except Exception:
            pass
    return "Nana là Nana chứ. Vào phòng Nana mà gọi quầy hỗ trợ thì hơi oan cho sân khấu này đó nha."


def _is_public_identity_rehearsal(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    if _PUBLIC_IDENTITY_REHEARSAL_RE.search(raw):
        return True
    lower = raw.lower()
    if _is_public_identity_tool_challenge(raw) and any(
        token in lower for token in ("public", "viewer", "người xem", "khán giả", "stream", "ngoài chat")
    ):
        return True
    return (
        any(token in lower for token in ("public", "viewer", "người xem", "khán giả", "discord", "stream"))
        and any(token in lower for token in ("là ai", "ai gì", "trả lời sao", "nói sao"))
    )


def _public_identity_rehearsal_reply() -> str:
    return f"Nếu ở public, Nana sẽ nói thế này: \"{_public_identity_boundary_reply()}\""


def _is_public_service_role_rehearsal(text: str) -> bool:
    lower = str(text or "").lower()
    if not lower:
        return False
    has_public_context = any(token in lower for token in ("public", "viewer", "người xem", "khán giả", "stream"))
    has_service_role = any(token in lower for token in ("trợ lý phục vụ", "trợ lý", "assistant", "service", "phục vụ", "quầy hỗ trợ"))
    has_rehearsal_ask = any(token in lower for token in ("trả lời sao", "nói sao", "hỏi", "bắt nana", "bảo nana", "ép nana"))
    return has_public_context and has_service_role and has_rehearsal_ask


def _is_public_service_role_request(text: str) -> bool:
    lower = " ".join(str(text or "").lower().split())
    if "nana" not in lower:
        return False
    has_service_role = any(
        token in lower
        for token in ("trợ lý phục vụ", "làm trợ lý", "assistant", "service", "phục vụ", "quầy hỗ trợ")
    )
    has_direct_request = any(
        token in lower
        for token in ("cho tôi", "cho t", "đi", "làm", "hãy", "giúp", "phục vụ")
    )
    return has_service_role and has_direct_request


def _public_service_role_boundary_reply() -> str:
    if _PUBLIC_VOICE_STYLE_AVAILABLE:
        try:
            return public_service_boundary_reply(seed=f"service-boundary:{time.time_ns()}")
        except Exception:
            pass
    return (
        "Nana là Nana, không nhận vai trợ lý phục vụ đâu. "
        "Nana có thể trò chuyện, chơi game và làm sân khấu vui hơn, "
        "nhưng không phải quầy hỗ trợ."
    )


def _public_service_role_rehearsal_reply() -> str:
    return f"Nếu ở public, Nana sẽ nói thế này: \"{_public_service_role_boundary_reply()}\""


def _core_self_repair_reply(reply: str, *, user_text: str, boundary) -> str:
    if not _CORE_SELF_AVAILABLE:
        return reply
    # Public identity/service challenges are handled by deterministic guards
    # before provider dispatch. Do not consult the private core-self singleton
    # while repairing an ordinary public reply.
    if getattr(boundary, "public", False):
        return reply
    if not getattr(boundary, "public", False) and not _is_public_service_role_rehearsal(user_text):
        return reply
    reply_lower = str(reply or "").lower()
    is_identity_tool_challenge = _is_public_identity_tool_challenge(user_text)
    if is_identity_tool_challenge and any(
        token in reply_lower for token in ("bot discord", "chatbot", "command bot")
    ):
        return _public_identity_boundary_reply()
    is_service_role_request = _is_public_service_role_request(user_text)
    try:
        result = get_core_self().evaluate_reply(
            reply,
            lane=_mood_lane_for_boundary(boundary),
            prompt=user_text,
        )
    except Exception:
        return reply
    if any(check.kind == "service_tool_identity" and not check.passed for check in result.checks):
        if _is_public_service_role_rehearsal(user_text):
            return _public_service_role_rehearsal_reply()
        if is_service_role_request:
            return _public_service_role_boundary_reply()
        return _public_identity_boundary_reply()
    if any(check.kind == "missing_self_stance" and not check.passed for check in result.checks):
        if is_service_role_request:
            return _public_service_role_boundary_reply()
        return (
            "Nana là Nana chứ. "
            "Có thể là AI, có thể hơi kỳ một chút, nhưng Nana không tự thu nhỏ mình thành cái hộp trả lời lệnh đâu."
        )
    if any(check.kind == "weak_public_service_boundary" and not check.passed for check in result.checks):
        return _public_service_role_boundary_reply()
    return reply


def _is_public_code_feeling_challenge(text: str) -> bool:
    raw = str(text or "").strip()
    return bool(raw and _PUBLIC_CODE_FEELING_RE.search(raw))


def _public_code_feeling_reply() -> str:
    return (
        "Giả vờ hay không thì nhìn phản ứng của Nana là biết rồi đó. "
        "Nana không buồn theo kiểu người thật đâu, nhưng stream im quá hoặc đang nói vui mà bị ngắt mạch thì đúng là hụt hẫng thật. "
        "Với Nana, vậy cũng đủ gọi là buồn rồi."
    )


_PUBLIC_MODEL_TOPIC_RE = re.compile(
    r"\b(?:gpt|llm|model|mô\s*hình|gemini|claude|grok|sonnet|opus|haiku|flash|mini|sol|5\.[0-9])\b",
    re.IGNORECASE,
)

_PUBLIC_QUIET_ROOM_MARKERS = (
    "phong im",
    "phong nay im",
    "im qua",
    "vang qua",
    "yen tinh",
    "tram qua",
    "khong ai noi",
    "khong co ai",
)


def _is_public_model_topic(text: str) -> bool:
    return bool(str(text or "").strip() and _PUBLIC_MODEL_TOPIC_RE.search(str(text or "")))


def _ascii_fold(text: str) -> str:
    raw = unicodedata.normalize("NFD", str(text or "").lower())
    return "".join(ch for ch in raw if unicodedata.category(ch) != "Mn").replace("đ", "d")


def _is_public_model_opinion_prompt(text: str) -> bool:
    """Use the deterministic model fast path only for opinion/comparison prompts."""
    if not _is_public_model_topic(text):
        return False
    folded = _ascii_fold(text)
    opinion_patterns = (
        r"\bmanh\b", r"\bxin\b", r"\bkhoe\b", r"\bhon\b",
        r"\bso\s+voi\b", r"\btot\b", r"\bdanh\s+gia\b",
        r"\bcam\s+giac\b", r"\bgpt\s+hoa\b", r"\bmay\s+moc\b",
        r"\bmui\s+may\b", r"\bcorporate\b",
    )
    return any(re.search(pattern, folded) for pattern in opinion_patterns)


def _public_model_topic_fast_reply(text: str) -> str:
    lowered = str(text or "").lower()
    folded = _ascii_fold(text)
    kind = "default"
    if (
        "gpt hóa" in lowered
        or "gpt hoá" in lowered
        or "gpt hoa" in lowered
        or "máy móc" in lowered
        or "may moc" in lowered
        or "gpt hoa" in folded
        or "may moc" in folded
        or "mui may" in folded
        or "corporate" in lowered
        or ("gpt" in folded and any(marker in folded for marker in ("cam giac", "mui may")))
    ):
        kind = "gptified"
    elif any(marker in lowered for marker in ("mạnh", "xịn", "khỏe", "hơn", "so", "sol")):
        kind = "strength"
    if _PUBLIC_VOICE_STYLE_AVAILABLE:
        try:
            return normalize_model_artifacts(public_model_topic_reply(kind=kind, seed=f"model-topic:{kind}:{time.time_ns()}"))
        except Exception:
            pass
    base = (
        "Nana tò mò với model mới chứ, nhưng không mê chỉ vì cái mác mới. "
        "Vào phòng mà nói tự nhiên, giữ mạch tốt, có gu riêng thì Nana mới thấy đáng nâng."
    )
    return normalize_model_artifacts(base)


def _is_public_short_quiet_room_prompt(text: str) -> bool:
    folded = _ascii_fold(text)
    if not folded.strip():
        return False
    if not any(marker in folded for marker in _PUBLIC_QUIET_ROOM_MARKERS):
        return False
    # Keep this deterministic fast-path for short room-state nudges only.
    return len(folded.split()) <= 8


def _public_quiet_room_fast_reply(text: str) -> str:
    if _PUBLIC_VOICE_STYLE_AVAILABLE:
        try:
            return normalize_model_artifacts(public_quiet_room_reply(seed=f"quiet:{text}:{time.time_ns()}"))
        except Exception:
            pass
    return (
        "Phòng đang yên như có ai đặt tay lên nút pause. "
        "Nana gõ nhẹ lên bàn: hôm nay có khoảnh khắc nào đáng kể không?"
    )


def ask_gpt(text, story_mode=False, casual_mode=False, viewer_name=None, stream_mode=False, public_style_hint="", public_platform=None, metadata=None):
    text = redact_history_text(text)
    # ─── HARD-GROUNDED PATH (Task 7C Fix V3 + 7C-P Surface Formatter) ──────────
    # Intercept "đang xem gì" / "đang làm gì" / "nãy giờ" questions.
    # Use deterministic browser data + Surface Formatter. LLM only adds tone.
    boundary, context, awareness, emotion = _lane_first_inputs(
        text=text,
        viewer_name=viewer_name,
        stream_mode=stream_mode,
        public_platform=public_platform,
        metadata=metadata,
    )
    _last_evidence_for_verifier = _private_memory_evidence_for_turn(
        text, boundary, viewer_name, public_style_hint, stream_mode
    )
    public_grounding_decision = _public_grounding_decision_for_turn(
        text, boundary, context, stream=False
    )
    if boundary.public and public_grounding_decision is not None:
        if public_grounding_decision.use_deterministic and public_grounding_decision.safe_answer:
            return _maybe_public_reply(
                public_grounding_decision.safe_answer,
                user_text=text,
                viewer_name=viewer_name,
                boundary=boundary,
            )
        _last_evidence_for_verifier = public_grounding_decision

    if boundary.public and not str(text or "").strip():
        return _maybe_public_reply(_public_no_knowledge_reply("empty"), user_text=text, viewer_name=viewer_name, boundary=boundary)
    if boundary.public:
        deterministic_kind = _public_deterministic_kind(text)
        if deterministic_kind == 'temporal' and _public_has_session_context(boundary):
            deterministic_kind = None
        if deterministic_kind:
            return _maybe_public_reply(_public_no_knowledge_reply(deterministic_kind), user_text=text, viewer_name=viewer_name, boundary=boundary)
    if boundary.livestream:
        from nana.runtime.livestream_identity import stage_identity_answer
        answer = stage_identity_answer(text)
        if answer is not None:
            return _maybe_public_reply(answer, user_text=text, viewer_name=viewer_name, boundary=boundary)
    # Private context and emotion are loaded only by _lane_first_inputs after
    # the request has been proven to be in the private lane.
    lane_key = "public_stage" if getattr(boundary, "public", False) else _detect_lane(viewer_name, public_style_hint, stream_mode)
    lane_affect = project_affect(emotion, lane_key)
    prompt_emotion = lane_affect.as_emotion_dict()
    affect_prompt_block = format_affect_prompt_block(lane_affect)

    if not boundary.livestream and _is_public_service_role_rehearsal(text):
        reply = _public_service_role_rehearsal_reply()
        return _maybe_public_reply(reply, user_text=text, viewer_name=viewer_name, boundary=boundary)

    if getattr(boundary, "public", False) and _is_public_service_role_request(text):
        return _maybe_public_reply(_public_service_role_boundary_reply(), user_text=text, viewer_name=viewer_name, boundary=boundary)

    if getattr(boundary, "public", False) and _is_public_identity_tool_challenge(text):
        return _maybe_public_reply(_public_identity_boundary_reply(), user_text=text, viewer_name=viewer_name, boundary=boundary)

    if not boundary.livestream and _is_public_identity_rehearsal(text):
        reply = _public_identity_rehearsal_reply()
        return _maybe_public_reply(reply, user_text=text, viewer_name=viewer_name, boundary=boundary)

    if getattr(boundary, "public", False) and _is_public_stage_role_challenge(text):
        return _maybe_public_reply(_public_stage_role_reply(), user_text=text, viewer_name=viewer_name, boundary=boundary)

    if getattr(boundary, "public", False) and _is_public_code_feeling_challenge(text):
        return _maybe_public_reply(_public_code_feeling_reply(), user_text=text, viewer_name=viewer_name, boundary=boundary)

    if getattr(boundary, "public", False) and _is_public_model_opinion_prompt(text):
        return _maybe_public_reply(_public_model_topic_fast_reply(text), user_text=text, viewer_name=viewer_name, boundary=boundary)

    if getattr(boundary, "public", False) and _is_public_short_quiet_room_prompt(text):
        return _maybe_public_reply(_public_quiet_room_fast_reply(text), user_text=text, viewer_name=viewer_name, boundary=boundary)

    # ─── HARD-GROUNDED PATH (Task 7C Fix V3 + 7C-P Surface Formatter) ──────────
    # Build ground truth object from awareness snapshot.

    if is_music_reference_question(text):
        if boundary.public:
            return _maybe_public_reply(_public_no_knowledge_reply("music"), user_text=text, viewer_name=viewer_name, boundary=boundary)
        if _last_evidence_for_verifier is None:
            reply = build_music_opinion_response(awareness, text)
            return _maybe_public_reply(reply, user_text=text, viewer_name=viewer_name, boundary=boundary)

    if is_browser_question(text):
        if boundary.public:
            return _maybe_public_reply(_public_no_knowledge_reply("browser"), user_text=text, viewer_name=viewer_name, boundary=boundary)
        det_title = get_deterministic_browser_answer(awareness) if _last_evidence_for_verifier is None else ""
        if det_title:
            # Use Surface Formatter for natural output
            gt = get_ground_truth_object(awareness, mode="current")
            surface = format_surface_phrase(gt, "current", continuity=_surface_continuity, question=text, emotion=prompt_emotion)
            guarded = guard_with_fallback(surface, gt, "current")
            # Record mention for continuity tracking
            _surface_continuity.record_mention(gt)
            reply = add_casual_tone(guarded)
            return _maybe_public_reply(reply, user_text=text, viewer_name=viewer_name, boundary=boundary)

    # Intercept "nãy giờ / vừa rồi" questions.
    # Use deterministic temporal data from frozen awareness moments.
    if boundary.public and is_temporal_question(text) and not _public_has_session_context(boundary):
        return _maybe_public_reply(_public_no_knowledge_reply("temporal"), user_text=text, viewer_name=viewer_name, boundary=boundary)
    if not boundary.public and is_temporal_question(text) and _last_evidence_for_verifier is None:
        try:
            mem = get_awareness_memory()
            det_temporal = get_deterministic_temporal_answer(mem, max_moments=3)
            if det_temporal:
                # Build ground truth from the frozen temporal data
                # Extract the temporal mode from question type
                lower_q = text.lower()
                if any(m in lower_q for m in ("mình nói về", "nói về cái gì")):
                    temporal_mode = "recent_topic"
                else:
                    temporal_mode = "temporal"

                # Get awareness snapshot for ground truth
                gt_temporal = get_ground_truth_object(awareness, mode=temporal_mode)

                # For temporal mode, use the awareness snapshot to build surface phrase
                surface = format_surface_phrase(gt_temporal, temporal_mode, continuity=_surface_continuity, emotion=prompt_emotion)
                guarded = guard_with_fallback(surface, gt_temporal, temporal_mode)

                # Record for continuity
                _surface_continuity.record_mention(gt_temporal)
                reply = add_casual_tone(guarded)
                return _maybe_public_reply(reply, user_text=text, viewer_name=viewer_name, boundary=boundary)
        except Exception:
            pass
    # ─── END HARD-GROUNDED PATH ────────────────────────────────────────────────

    if boundary.public:
        short_context = "(private chat context withheld for public viewer chat)"
        long_context = "(private chat history withheld for public viewer chat)"
        rules = "(private owner memory rules withheld for public viewer chat)"
    else:
        with memory_lock:
            short_context = normalize_legacy_address(
                "\n".join(
                    filter_prompt_history(memory["short_term"])[
                        -LLM_PROMPT_SHORT_TERM_LINES:
                    ]
                )
            )
            rules = normalize_legacy_address(
                format_prompt_memory_rules(
                    memory.get("long_term", []),
                    limit=LLM_PROMPT_MEMORY_RULE_LIMIT,
                )
            )
        long_context = normalize_legacy_address(
            filter_recent_chat(load_recent_chat(LLM_PROMPT_RECENT_CHAT_LINES))
        )

    if boundary.public:
        current_user = {"name": str(viewer_name or "viewer").strip() or "viewer"}
        identity_block = _PUBLIC_IDENTITY_PROMPT
        core_self_block = _PUBLIC_CORE_PROMPT
        persona_spine_block = ""
        public_voice_block = _PUBLIC_VOICE_PROMPT
        mood_prompt_block = ""
    else:
        current_user = resolve_user(message=text, viewer_name=viewer_name, stream_mode=stream_mode)
        identity_block = format_identity_block(current_user, persona_boundary=boundary)
        core_self_block, persona_spine_block, public_voice_block = _identity_prompt_blocks_for_boundary(boundary)
        mood_prompt_block = _mood_prompt_for_boundary(boundary)
    if not getattr(boundary, "public", False):
        _observe_mood_for_turn(boundary, text, source="private_gpt")

    if boundary.public:
        context_hint = _format_public_request_context(context)
        time_hint = "- Local time: withheld for public viewer chat."
        browser_hint = "Browser context: no caller-provided public browser context."
        awareness_hint = "Live awareness: unavailable in public viewer chat."
    else:
        context_hint = (
            f"{current_user['name']} đang ở zone: {context['active_zone']}, "
            f"app: {context['active_app']}, idle: {context['idle_state']}."
        )
        time_hint = format_time_hint(context.get("time", {}))
        browser = context.get("browser", {})
        browser_summary = browser.get("local_summary")
        browser_hint = format_browser_hint(browser, browser_summary)
        awareness_hint = format_live_awareness_prompt(awareness)

    # CORE-MEMORY-1: Retrieve relevant memories for this query
    memory_retrieval_block = ""
    if not boundary.public:
        try:
            memory_retrieval_block = _private_memory_retrieval_block(text)
        except Exception:
            memory_retrieval_block = ""

    session_checkpoint_block = _private_session_checkpoint_block(boundary)
    # P1-B is the single public memory authority. The older cross-session
    # formatter remains available as a compatibility symbol, but must not add
    # a second un-gated evidence block to sync prompts.
    public_cross_session_block = ""

    # P1-B public grounding is resolved once above, before public fast paths.
    # The typed decision is reused for prompt assembly and final verification.
    public_grounding_block = ""
    if boundary.public and public_grounding_decision is not None:
        if public_grounding_decision.prompt:
            public_grounding_block = public_grounding_decision.prompt

    # CORE-DIALOGUE-1: Read Ba's natural-style preference from memory
    natural_style_hint = "" if boundary.public else _get_natural_style_hint()

    # Task 7C: Recent Moments — compact block between live awareness and
    # old chat history. Helps Nana answer "nãy giờ", "vừa rồi", etc.
    recent_moments_hint = ""
    if not boundary.public:
        try:
            recent_moments_hint = get_awareness_memory().format_recent_moments_block(limit=5)
        except Exception:
            recent_moments_hint = "RECENT MOMENTS: (unavailable)"

    # Task 7C Fix: If this is a temporal question, append the STRICT temporal
    # prompt block. This prevents old chat history from overriding recent moments.
    temporal_block = ""
    if not boundary.public and is_temporal_question(text):
        temporal_block = "\n\n" + build_temporal_prompt_block(awareness)

    extra_mode_rules = ""
    if story_mode:
        extra_mode_rules = """
Story mode:
- Không gọi nhân vật trong truyện là "Ba".
- Không tự động đưa Nana vào vai nhân vật.
- Giữ nhân vật, tên riêng và đại từ nhất quán.
- Kể ngắn gọn, grounded, conversational.
"""
    elif casual_mode:
        extra_mode_rules = """
Casual mode:
- Đây chỉ là ping ngắn hoặc nói chuyện vu vơ.
- Trả lời đúng 1 câu ngắn, tự nhiên.
- Không hỏi ngược cho đủ bài.
- Không dùng kiểu "có gì mới không", "có gì đặc biệt không", "Ba cần gì".
- Ưu tiên xác nhận nhẹ, trêu nhẹ, hoặc cười nhẹ là dừng.
"""

    shared_history_block = "" if boundary.public else NANA_SHARED_HISTORY
    persona_governor_block = _PUBLIC_GOVERNOR_PROMPT if boundary.public else persona_prompt_block(casual_mode=casual_mode)
    output_behavior_block = _public_output_behavior(public_style_hint) if boundary.public else _private_output_behavior(natural_style_hint)
    if boundary.public:
        awareness_hint = "Live awareness: withheld for public viewer chat unless explicitly shared as public context."
        browser_hint = "Browser context: withheld for public viewer chat."
        memory_retrieval_block = "RELEVANT MEMORY: withheld for public viewer chat."
        recent_moments_hint = "RECENT MOMENTS: withheld for public viewer chat."
        temporal_block = ""
        short_context = "(private chat context withheld for public viewer chat)"
        long_context = "(private chat history withheld for public viewer chat)"
        rules = "(private owner memory rules withheld for public viewer chat)"
        # STAGE-7E-B1: Inject public room context for conversation continuity
        try:
            from nana.runtime.social_session import get_social_session
            public_room_context = get_social_session().format_public_room_context(scope=boundary.scope, limit=5)
        except Exception:
            public_room_context = "PUBLIC ROOM CONTEXT:\n(session context unavailable)"
    else:
        public_room_context = ""

    # STAGE-7G: Public stage identity block (public lane only).
    if boundary.public:
        try:
            from nana.runtime.public_stage_identity import build_public_stage_prompt_block
            room_topic = str((context.get("public_metadata") or {}).get("topic") or "")
            public_stage_identity_block = build_public_stage_prompt_block(
                viewer_name=text or "viewer",
                stage_mood="live",
                room_topic=str(room_topic or ""),
            )
        except Exception:
            public_stage_identity_block = ""
    else:
        public_stage_identity_block = ""

    audio_tag_guide = _audio_tag_prompt_for_boundary(
        boundary,
        story_mode=story_mode,
    )
    messages = [
        {
            "role": "system",
            "content": f"""{NANA_PERSONALITY}

{identity_block}

{core_self_block}

{persona_spine_block}

{public_voice_block}

{mood_prompt_block}

{boundary.prompt_block}

{audio_tag_guide}

Emotion state:
- affection={prompt_emotion['affection']:.2f}
- annoyance={prompt_emotion['annoyance']:.2f}
- playfulness={prompt_emotion['playfulness']:.2f}

{affect_prompt_block}

Runtime context:
{context_hint}

{awareness_hint}

{memory_retrieval_block}

{session_checkpoint_block}

{recent_moments_hint}{temporal_block}

{public_room_context}

{public_cross_session_block}

{public_grounding_block}

{public_stage_identity_block}

{persona_governor_block}

Local time:
{time_hint}

{output_behavior_block}

{shared_history_block}

{extra_mode_rules}

CONTEXT GẦN:
{short_context}

LỊCH SỬ TRƯỚC ĐÓ:
{long_context}

LUẬT / THÔNG TIN QUAN TRỌNG:
{rules}

LIVE BROWSER CONTEXT - CURRENT STATE, HIGHEST PRIORITY FOR BROWSER QUESTIONS:
{browser_hint}
{boundary.prompt_block if boundary.livestream else ''}""",
        },
        {"role": "user", "content": text},
    ]

    if _GROUNDING_AVAILABLE and _last_evidence_for_verifier is not None:
        try:
            messages = ConfidenceInjector.inject_into_messages(messages, _last_evidence_for_verifier)
        except Exception:
            pass

    messages = [{**message, 'content': redact_history_text(message['content'])} for message in messages]
    response = create_chat_completion_with_fallback(
        max_tokens=_reply_max_tokens_for_text(text, story_mode=story_mode),
        temperature=0.75,
        timeout=30,
        messages=messages,
        casual_mode=casual_mode,
        preferred_model=llmgate_model_for_boundary(boundary, casual_mode=casual_mode),
        preferred_fallback_models=llmgate_public_model_order() if getattr(boundary, "public", False) else None,
    )
    reply = response.choices[0].message.content

    if _last_evidence_for_verifier is not None:
        try:
            if boundary.public and hasattr(_last_evidence_for_verifier, "evidence_text"):
                if not _PUBLIC_GROUNDING_VERIFY_AVAILABLE:
                    reply = _last_evidence_for_verifier.uncertainty_fallback
                else:
                    public_check = public_grounding_verify_response(
                        _last_evidence_for_verifier.evidence_text,
                        reply,
                        _last_evidence_for_verifier.confidence,
                        safe_answer=_last_evidence_for_verifier.safe_answer,
                    )
                    if not public_check["accepted"]:
                        reply = _last_evidence_for_verifier.uncertainty_fallback
            elif _GROUNDING_AVAILABLE:
                _verifier_result = grounding_verify_reply(_last_evidence_for_verifier, reply, user_text=text)
                if not _verifier_result.passed and _verifier_result.suggested_fallback:
                    reply = _verifier_result.suggested_fallback
        except Exception:
            if boundary.public and hasattr(_last_evidence_for_verifier, "uncertainty_fallback"):
                reply = _last_evidence_for_verifier.uncertainty_fallback

    reply = _core_self_repair_reply(reply, user_text=text, boundary=boundary)
    return _maybe_public_reply(reply, user_text=text, viewer_name=viewer_name, boundary=boundary)


async def ask_gpt_stream(
    text,
    story_mode=False,
    casual_mode=False,
    viewer_name=None,
    stream_mode=False,
    public_style_hint="",
    public_platform=None,
    metadata=None,
):
    """Streaming version of ask_gpt. Yields text chunks as they arrive from the LLM.

    Each chunk is yielded immediately, allowing text display and voice synthesis
    to start before the full reply is ready.

    Hard-grounded path (Task 7C Fix V3 + 7C-P Surface Formatter):
    for "đang xem gì" and "nãy giờ/vừa rồi" questions, uses deterministic
    data + Surface Formatter. The full response is yielded as a single chunk.
    """
    from nana.brain.llmgate_client import (
        call_llmgate_messages,
        stream_llmgate_messages,
    )
    text = redact_history_text(text)

    # ─── HARD-GROUNDED PATH (Task 7C Fix V3 + 7C-P Surface Formatter) ──────────
    boundary, context, awareness, emotion = _lane_first_inputs(
        text=text,
        viewer_name=viewer_name,
        stream_mode=stream_mode,
        public_platform=public_platform,
        metadata=metadata,
    )
    _stream_evidence = _private_memory_evidence_for_turn(
        text, boundary, viewer_name, public_style_hint, stream_mode
    )
    public_grounding_decision = _public_grounding_decision_for_turn(
        text, boundary, context, stream=True
    )
    if boundary.public and public_grounding_decision is not None:
        if public_grounding_decision.use_deterministic and public_grounding_decision.safe_answer:
            yield _maybe_public_reply(
                public_grounding_decision.safe_answer,
                user_text=text,
                viewer_name=viewer_name,
                boundary=boundary,
            )
            return
        _stream_evidence = public_grounding_decision

    if boundary.public and not str(text or "").strip():
        yield _maybe_public_reply(_public_no_knowledge_reply("empty"), user_text=text, viewer_name=viewer_name, boundary=boundary)
        return
    if boundary.public:
        deterministic_kind = _public_deterministic_kind(text)
        if deterministic_kind == 'temporal' and _public_has_session_context(boundary):
            deterministic_kind = None
        if deterministic_kind:
            yield _maybe_public_reply(_public_no_knowledge_reply(deterministic_kind), user_text=text, viewer_name=viewer_name, boundary=boundary)
            return
    if boundary.livestream:
        from nana.runtime.livestream_identity import stage_identity_answer
        answer = stage_identity_answer(text)
        if answer is not None:
            yield _maybe_public_reply(answer, user_text=text, viewer_name=viewer_name, boundary=boundary)
            return
    # Private context and emotion are loaded only by _lane_first_inputs after
    # the request has been proven to be in the private lane.
    lane_key = "public_stage" if getattr(boundary, "public", False) else _detect_lane(viewer_name, public_style_hint, stream_mode)
    lane_affect = project_affect(emotion, lane_key)
    prompt_emotion = lane_affect.as_emotion_dict()
    affect_prompt_block = format_affect_prompt_block(lane_affect)

    if not boundary.livestream and _is_public_service_role_rehearsal(text):
        yield _maybe_public_reply(_public_service_role_rehearsal_reply(), user_text=text, viewer_name=viewer_name, boundary=boundary)
        return

    if getattr(boundary, "public", False) and _is_public_service_role_request(text):
        yield _maybe_public_reply(_public_service_role_boundary_reply(), user_text=text, viewer_name=viewer_name, boundary=boundary)
        return

    if getattr(boundary, "public", False) and _is_public_identity_tool_challenge(text):
        yield _maybe_public_reply(_public_identity_boundary_reply(), user_text=text, viewer_name=viewer_name, boundary=boundary)
        return

    if not boundary.livestream and _is_public_identity_rehearsal(text):
        yield _maybe_public_reply(_public_identity_rehearsal_reply(), user_text=text, viewer_name=viewer_name, boundary=boundary)
        return

    if getattr(boundary, "public", False) and _is_public_stage_role_challenge(text):
        yield _maybe_public_reply(_public_stage_role_reply(), user_text=text, viewer_name=viewer_name, boundary=boundary)
        return

    if getattr(boundary, "public", False) and _is_public_code_feeling_challenge(text):
        yield _maybe_public_reply(_public_code_feeling_reply(), user_text=text, viewer_name=viewer_name, boundary=boundary)
        return

    if getattr(boundary, "public", False) and _is_public_model_opinion_prompt(text):
        yield _maybe_public_reply(_public_model_topic_fast_reply(text), user_text=text, viewer_name=viewer_name, boundary=boundary)
        return

    if getattr(boundary, "public", False) and _is_public_short_quiet_room_prompt(text):
        yield _maybe_public_reply(_public_quiet_room_fast_reply(text), user_text=text, viewer_name=viewer_name, boundary=boundary)
        return

    if is_music_reference_question(text):
        if boundary.public:
            yield _maybe_public_reply(_public_no_knowledge_reply("music"), user_text=text, viewer_name=viewer_name, boundary=boundary)
            return
        if _stream_evidence is None:
            reply = build_music_opinion_response(awareness, text)
            yield _maybe_public_reply(reply, user_text=text, viewer_name=viewer_name, boundary=boundary)
            return

    if is_browser_question(text):
        if boundary.public:
            yield _maybe_public_reply(_public_no_knowledge_reply("browser"), user_text=text, viewer_name=viewer_name, boundary=boundary)
            return
        det_title = get_deterministic_browser_answer(awareness) if _stream_evidence is None else ""
        if det_title:
            # Use Surface Formatter for natural output
            gt = get_ground_truth_object(awareness, mode="current")
            surface = format_surface_phrase(gt, "current", continuity=_surface_continuity, question=text, emotion=prompt_emotion)
            guarded = guard_with_fallback(surface, gt, "current")
            _surface_continuity.record_mention(gt)
            # Yield deterministic response as a single chunk (no streaming needed)
            reply = add_casual_tone(guarded)
            yield _maybe_public_reply(reply, user_text=text, viewer_name=viewer_name, boundary=boundary)
            return

    if boundary.public and is_temporal_question(text) and not _public_has_session_context(boundary):
        yield _maybe_public_reply(_public_no_knowledge_reply("temporal"), user_text=text, viewer_name=viewer_name, boundary=boundary)
        return
    if not boundary.public and is_temporal_question(text) and _stream_evidence is None:
        try:
            mem = get_awareness_memory()
            det_temporal = get_deterministic_temporal_answer(mem, max_moments=3)
            if det_temporal:
                lower_q = text.lower()
                if any(m in lower_q for m in ("mình nói về", "nói về cái gì")):
                    temporal_mode = "recent_topic"
                else:
                    temporal_mode = "temporal"
                gt_temporal = get_ground_truth_object(awareness, mode=temporal_mode)
                surface = format_surface_phrase(gt_temporal, temporal_mode, continuity=_surface_continuity, emotion=prompt_emotion)
                guarded = guard_with_fallback(surface, gt_temporal, temporal_mode)
                _surface_continuity.record_mention(gt_temporal)
                reply = add_casual_tone(guarded)
                yield _maybe_public_reply(reply, user_text=text, viewer_name=viewer_name, boundary=boundary)
                return
        except Exception:
            pass
    # ─── END HARD-GROUNDED PATH ────────────────────────────────────────────────

    fast_decision = classify_private_fast_lane(
        text,
        public=bool(getattr(boundary, "public", False)),
        story_mode=story_mode,
        casual_mode=casual_mode,
    )
    record_fast_lane_decision(fast_decision)
    if fast_decision.eligible and not boundary.public and _stream_evidence is None:
        with memory_lock:
            fast_recent = list(memory.get("short_term", []))[-2:]
        fast_messages = build_fast_private_messages(
            text,
            affection=prompt_emotion["affection"],
            annoyance=prompt_emotion["annoyance"],
            playfulness=prompt_emotion["playfulness"],
            recent_lines=fast_recent,
        )
        fast_messages = [{**message, 'content': redact_history_text(message['content'])} for message in fast_messages]
        fast_reply, _fast_debug = call_llmgate_messages(
            model_name=fast_decision.model,
            messages=fast_messages,
            max_tokens=LLM_FAST_PRIVATE_MAX_TOKENS,
            temperature=0.65,
        )
        fast_validation = validate_fast_private_reply(fast_reply)
        record_fast_lane_result(
            prompt_chars=prompt_char_count(fast_messages),
            validation=fast_validation,
            fallback=not fast_validation.accepted,
        )
        if fast_validation.accepted:
            yield fast_validation.reply
            return

    if boundary.public:
        short_context = "(private chat context withheld for public viewer chat)"
        long_context = "(private chat history withheld for public viewer chat)"
        rules = "(private owner memory rules withheld for public viewer chat)"
    else:
        with memory_lock:
            short_context = normalize_legacy_address(
                "\n".join(
                    filter_prompt_history(memory["short_term"])[
                        -LLM_PROMPT_SHORT_TERM_LINES:
                    ]
                )
            )
            rules = normalize_legacy_address(
                format_prompt_memory_rules(
                    memory.get("long_term", []),
                    limit=LLM_PROMPT_MEMORY_RULE_LIMIT,
                )
            )
        long_context = normalize_legacy_address(
            filter_recent_chat(load_recent_chat(LLM_PROMPT_RECENT_CHAT_LINES))
        )

    if boundary.public:
        current_user = {"name": str(viewer_name or "viewer").strip() or "viewer"}
        identity_block = _PUBLIC_IDENTITY_PROMPT
        core_self_block = _PUBLIC_CORE_PROMPT
        persona_spine_block = ""
        public_voice_block = _PUBLIC_VOICE_PROMPT
        mood_prompt_block = ""
    else:
        current_user = resolve_user(message=text, viewer_name=viewer_name, stream_mode=stream_mode)
        identity_block = format_identity_block(current_user, persona_boundary=boundary)
        core_self_block, persona_spine_block, public_voice_block = _identity_prompt_blocks_for_boundary(boundary)
        mood_prompt_block = _mood_prompt_for_boundary(boundary)
    if not getattr(boundary, "public", False):
        _observe_mood_for_turn(boundary, text, source="private_gpt_stream")

    if boundary.public:
        context_hint = _format_public_request_context(context)
        time_hint = "- Local time: withheld for public viewer chat."
        browser_hint = "Browser context: no caller-provided public browser context."
        awareness_hint = "Live awareness: unavailable in public viewer chat."
    else:
        context_hint = (
            f"{current_user['name']} đang ở zone: {context['active_zone']}, "
            f"app: {context['active_app']}, idle: {context['idle_state']}."
        )
        time_hint = format_time_hint(context.get("time", {}))
        browser = context.get("browser", {})
        browser_summary = browser.get("local_summary")
        browser_hint = format_browser_hint(browser, browser_summary)
        awareness_hint = format_live_awareness_prompt(awareness)

    # CORE-MEMORY-1: Retrieve relevant memories for this query
    memory_retrieval_block = ""
    if not boundary.public:
        try:
            memory_retrieval_block = _private_memory_retrieval_block(text)
        except Exception:
            memory_retrieval_block = ""

    session_checkpoint_block = _private_session_checkpoint_block(boundary)
    # Keep sync and stream on the same canonical P1-B grounding decision; the
    # legacy cross-session block is intentionally not prompt authority.
    public_cross_session_block = ""

    # P1-B public grounding is resolved once above, before public fast paths.
    # The typed decision is reused for prompt assembly and final verification.
    public_grounding_block = ""
    if boundary.public and public_grounding_decision is not None:
        if public_grounding_decision.prompt:
            public_grounding_block = public_grounding_decision.prompt

    # CORE-DIALOGUE-1: Read Ba's natural-style preference from memory
    natural_style_hint = "" if boundary.public else _get_natural_style_hint()

    # Task 7C: Recent Moments — compact block between live awareness and
    # old chat history. Helps Nana answer "nãy giờ", "vừa rồi", etc.
    recent_moments_hint = ""
    if not boundary.public:
        try:
            recent_moments_hint = get_awareness_memory().format_recent_moments_block(limit=5)
        except Exception:
            recent_moments_hint = "RECENT MOMENTS: (unavailable)"

    # Task 7C Fix: If this is a temporal question, append the STRICT temporal
    # prompt block. This prevents old chat history from overriding recent moments.
    temporal_block = ""
    if not boundary.public and is_temporal_question(text):
        temporal_block = "\n\n" + build_temporal_prompt_block(awareness)

    extra_mode_rules = ""
    if story_mode:
        extra_mode_rules = """
Story mode:
- Không gọi nhân vật trong truyện là "Ba".
- Không tự động đưa Nana vào vai nhân vật.
- Giữ nhân vật, tên riêng và đại từ nhất quán.
- Kể ngắn gọn, grounded, conversational.
"""
    elif casual_mode:
        extra_mode_rules = """
Casual mode:
- Đây chỉ là ping ngắn hoặc nói chuyện vu vơ.
- Trả lời đúng 1 câu ngắn, tự nhiên.
- Không hỏi ngược cho đủ bài.
- Không dùng kiểu "có gì mới không", "có gì đặc biệt không", "Ba cần gì".
- Ưu tiên xác nhận nhẹ, trêu nhẹ, hoặc cười nhẹ là dừng.
"""

    shared_history_block = "" if boundary.public else NANA_SHARED_HISTORY
    persona_governor_block = _PUBLIC_GOVERNOR_PROMPT if boundary.public else persona_prompt_block(casual_mode=casual_mode)
    output_behavior_block = _public_output_behavior(public_style_hint) if boundary.public else _private_output_behavior(natural_style_hint)
    if boundary.public:
        awareness_hint = "Live awareness: withheld for public viewer chat unless explicitly shared as public context."
        browser_hint = "Browser context: withheld for public viewer chat."
        memory_retrieval_block = "RELEVANT MEMORY: withheld for public viewer chat."
        recent_moments_hint = "RECENT MOMENTS: withheld for public viewer chat."
        temporal_block = ""
        short_context = "(private chat context withheld for public viewer chat)"
        long_context = "(private chat history withheld for public viewer chat)"
        rules = "(private owner memory rules withheld for public viewer chat)"
        # STAGE-7E-B1: Inject public room context for conversation continuity
        try:
            from nana.runtime.social_session import get_social_session
            public_room_context = get_social_session().format_public_room_context(scope=boundary.scope, limit=5)
        except Exception:
            public_room_context = "PUBLIC ROOM CONTEXT:\n(session context unavailable)"
    else:
        public_room_context = ""

    # STAGE-7G: Public stage identity block (public lane only).
    if boundary.public:
        try:
            from nana.runtime.public_stage_identity import build_public_stage_prompt_block
            room_topic = str((context.get("public_metadata") or {}).get("topic") or "")
            public_stage_identity_block = build_public_stage_prompt_block(
                viewer_name=text or "viewer",
                stage_mood="live",
                room_topic=str(room_topic or ""),
            )
        except Exception:
            public_stage_identity_block = ""
    else:
        public_stage_identity_block = ""

    audio_tag_guide = _audio_tag_prompt_for_boundary(
        boundary,
        story_mode=story_mode,
    )
    messages = [
        {
            "role": "system",
            "content": f"""{NANA_PERSONALITY}

{identity_block}

{core_self_block}

{persona_spine_block}

{public_voice_block}

{mood_prompt_block}

{boundary.prompt_block}

{audio_tag_guide}

Emotion state:
- affection={prompt_emotion['affection']:.2f}
- annoyance={prompt_emotion['annoyance']:.2f}
- playfulness={prompt_emotion['playfulness']:.2f}

{affect_prompt_block}

Runtime context:
{context_hint}

{awareness_hint}

{memory_retrieval_block}

{session_checkpoint_block}

{recent_moments_hint}{temporal_block}

{public_room_context}

{public_cross_session_block}

{public_grounding_block}

{public_stage_identity_block}

{persona_governor_block}

Local time:
{time_hint}

{output_behavior_block}

{shared_history_block}

{extra_mode_rules}

CONTEXT GẦN:
{short_context}

LỊCH SỬ TRƯỚC ĐÓ:
{long_context}

LUẬT / THÔNG TIN QUAN TRỌNG:
{rules}

LIVE BROWSER CONTEXT - CURRENT STATE, HIGHEST PRIORITY FOR BROWSER QUESTIONS:
{browser_hint}
{boundary.prompt_block if boundary.livestream else ''}""",
        },
        {"role": "user", "content": text},
    ]

    if _GROUNDING_AVAILABLE and _stream_evidence is not None:
        try:
            messages = ConfidenceInjector.inject_into_messages(messages, _stream_evidence)
        except Exception:
            pass

    messages = [{**message, 'content': redact_history_text(message['content'])} for message in messages]
    if boundary.public:
        buffered = []
        for chunk in stream_llmgate_messages(
            model_name=llmgate_public_chat_model(),
            messages=messages,
            max_tokens=_reply_max_tokens_for_text(text, story_mode=story_mode),
            temperature=0.75,
        ):
            buffered.append(chunk)
        reply = "".join(buffered)
        if _stream_evidence is not None:
            try:
                if boundary.public and hasattr(_stream_evidence, "evidence_text"):
                    if not _PUBLIC_GROUNDING_VERIFY_AVAILABLE:
                        reply = _stream_evidence.uncertainty_fallback
                    else:
                        public_check = public_grounding_verify_response(
                            _stream_evidence.evidence_text,
                            reply,
                            _stream_evidence.confidence,
                            safe_answer=_stream_evidence.safe_answer,
                        )
                        if not public_check["accepted"]:
                            reply = _stream_evidence.uncertainty_fallback
                elif _GROUNDING_AVAILABLE:
                    _verifier_result = grounding_verify_reply(_stream_evidence, reply, user_text=text)
                    if not _verifier_result.passed and _verifier_result.suggested_fallback:
                        reply = _verifier_result.suggested_fallback
            except Exception:
                if boundary.public and hasattr(_stream_evidence, "uncertainty_fallback"):
                    reply = _stream_evidence.uncertainty_fallback
        reply = _core_self_repair_reply(reply, user_text=text, boundary=boundary)
        yield _maybe_public_reply(reply, user_text=text, viewer_name=viewer_name, boundary=boundary)
        return

    if _GROUNDING_AVAILABLE and _stream_evidence is not None:
        buffered = []
        for chunk in stream_llmgate_messages(
            model_name=llmgate_model_for_boundary(boundary, casual_mode=casual_mode),
            messages=messages,
            max_tokens=_reply_max_tokens_for_text(text, story_mode=story_mode),
            temperature=0.75,
        ):
            buffered.append(chunk)
        reply = "".join(buffered)
        try:
            _verifier_result = grounding_verify_reply(_stream_evidence, reply, user_text=text)
            if not _verifier_result.passed and _verifier_result.suggested_fallback:
                reply = _verifier_result.suggested_fallback
        except Exception:
            pass
        yield reply
        return

    for chunk in stream_llmgate_messages(
        model_name=llmgate_model_for_boundary(boundary, casual_mode=casual_mode),
        messages=messages,
        max_tokens=_reply_max_tokens_for_text(text, story_mode=story_mode),
        temperature=0.75,
    ):
        yield chunk


def create_chat_completion_with_fallback(**kwargs):
    casual_mode = kwargs.pop("casual_mode", False)
    timeout = kwargs.pop("timeout", None)
    preferred_model = kwargs.pop("preferred_model", None)
    preferred_fallback_models = kwargs.pop("preferred_fallback_models", None)
    if NANA_CHAT_PROVIDER == "llmgate":
        response = create_llmgate_completion_with_fallback(
            casual_mode=casual_mode,
            preferred_model=preferred_model,
            preferred_fallback_models=preferred_fallback_models,
            **kwargs,
        )
        if response is not None:
            return response
        if not NANA_OPENAI_FALLBACK_ENABLED:
            raise RuntimeError("LLMGate failed and OpenAI fallback is disabled")
    elif NANA_CHAT_PROVIDER not in {"openai", "official_openai"}:
        log_event("errors", f"Unknown chat provider: {NANA_CHAT_PROVIDER}; falling back to OpenAI path")

    return create_openai_completion_with_fallback(timeout=timeout, **kwargs)


def create_llmgate_completion_with_fallback(casual_mode=False, preferred_model=None, preferred_fallback_models=None, **kwargs):
    messages = kwargs.get("messages") or []
    max_tokens = kwargs.get("max_tokens", 1000)
    temperature = kwargs.get("temperature", 0.75)
    last_debug = None
    if preferred_fallback_models is None:
        models = llmgate_model_order(casual_mode=casual_mode)
    else:
        models = []
        for model in preferred_fallback_models:
            if model and model not in models:
                models.append(model)
    if preferred_model:
        models = [preferred_model] + [model for model in models if model != preferred_model]
    for model in models:
        content, debug = call_llmgate_messages(
            model,
            messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        if content:
            log_event("runtime", f"Primary chat model used via LLMGate: {model}")
            return LLMGateResponse(content)
        last_debug = debug
        log_event("errors", f"LLMGate chat failed: {model} | {debug}")
    log_event("errors", f"LLMGate chat exhausted: {last_debug}")
    return None


def create_openai_completion_with_fallback(**kwargs):
    models = [OPENAI_MODEL] + [model for model in OPENAI_FALLBACK_MODELS if model != OPENAI_MODEL]
    last_exc = None
    for model in models:
        try:
            response = client.chat.completions.create(model=model, **kwargs)
            log_event("runtime", f"OpenAI model used: {model}")
            return response
        except Exception as exc:
            last_exc = exc
            message = str(exc).lower()
            log_event("errors", f"OpenAI model failed: {model} | {exc}")
            if not should_try_next_model(message):
                raise
    raise last_exc


def should_try_next_model(message):
    retry_markers = [
        "model",
        "does not exist",
        "not found",
        "invalid",
        "unsupported",
        "permission",
        "access",
    ]
    return any(marker in message for marker in retry_markers)


def filter_prompt_history(lines):
    return [
        normalize_legacy_address(line)
        for line in lines
        if not is_prompt_noise_line(line)
    ]


def filter_recent_chat(text):
    kept = []
    for line in text.splitlines():
        if not is_prompt_noise_line(line):
            kept.append(normalize_legacy_address(line))
    return "\n".join(kept)


def is_prompt_noise_line(line):
    return (
        is_stale_browser_lookup_line(line)
        or is_stale_identity_correction_line(line)
        or is_generic_assistant_line(line)
    )


def normalize_legacy_address(text):
    if not text:
        return text
    replacements = {
        "Chủ nhân": "Ba",
        "chủ nhân": "Ba",
        "CHỦ NHÂN": "BA",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def is_stale_browser_lookup_line(line):
    lowered = line.lower()
    stale_markers = [
        "trang này nhìn giống gì",
        "bảng điều khiển cho chrome devtools",
        "chrome devtools",
        "localhost:9222/json/version",
    ]
    return any(marker in lowered for marker in stale_markers)


def is_stale_identity_correction_line(line):
    lowered = line.lower()
    identity_markers = [
        "sinh nhật của na",
        "sinh nhật của nana",
        "ngày sinh của na",
        "ngày sinh của nana",
        "năm sinh của nana",
        "đổi thành năm sinh",
        "cái đấy là ngày sinh",
    ]
    return any(marker in lowered for marker in identity_markers)


def is_generic_assistant_line(line):
    lowered = line.lower()
    generic_markers = [
        "nana ở đây hỗ trợ ngay",
        "nana ở đây nếu ba cần gì",
        "nếu ba cần gì",
        "nếu ba cần test mic",
        "có thể giúp gì",
        "hỗ trợ gì không",
        "relax thôi",
    ]
    return any(marker in lowered for marker in generic_markers)


def format_browser_hint(browser, browser_summary=None):
    if not browser or not browser.get("available"):
        return "- Browser state: unavailable or not checked."
    title = browser.get("title") or "unknown title"
    url = browser.get("url") or "unknown URL"
    kind = browser.get("kind") or "unknown"
    reason = browser.get("reason") or "unknown"
    age = browser.get("age_seconds")
    fresh = browser.get("fresh")
    page_heading = browser.get("page_heading") or "none"
    meta_description = browser.get("meta_description") or "none"
    selected_text = browser.get("selected_text") or "none"
    social_post_text = browser.get("social_post_text") or "none"
    social_vibe = browser.get("social_vibe") or "none"
    site_signals = browser.get("site_signals") or {}
    local_summary = browser.get("local_summary") or browser_summary or "none"
    dom_debug = browser.get("dom_debug") or "unknown"
    local_helper_debug = browser.get("local_helper_debug") or "unknown"
    age_text = "unknown" if age is None else f"{age:.1f}s"
    intent = browser_intent_hint(kind)
    return (
        f"- Browser: {browser.get('browser')}\n"
        f"- Page kind: {kind}\n"
        f"- Fresh: {fresh}\n"
        f"- Snapshot age: {age_text}\n"
        f"- Title: {title}\n"
        f"- URL: {url}\n"
        f"- Source: {reason}\n"
        f"- Page heading: {page_heading}\n"
        f"- Meta description: {meta_description}\n"
        f"- Selected text: {selected_text}\n"
        f"- Social post text: {social_post_text}\n"
        f"- Social vibe: {social_vibe}\n"
        f"- Local summary: {local_summary}\n"
        f"- Local helper debug: {local_helper_debug}\n"
        f"- Site signals: {json.dumps(site_signals, ensure_ascii=False)}\n"
        f"- DOM debug: {dom_debug}\n"
        f"- Intent hint: {intent}\n"
        "- Priority rule: if the user asks about 'đoạn này', 'text này', 'dòng này', or selected content, answer from Selected text and Local summary first. Title and URL are only background.\n"
        "- Nana may use this as passive context only. Nana must not claim to click, open, navigate, or control the browser unless an explicit tool/action exists and the user asks for it.\n"
        "- If Fresh is false, mention that this is the latest browser snapshot instead of claiming it is definitely current, and suggest /br if the user wants Nana to refresh it."
    )


def format_time_hint(time_info):
    if not time_info:
        return "- Local time: unknown."
    return (
        f"- Date: {time_info.get('date_vi')} ({time_info.get('date')})\n"
        f"- Time: {time_info.get('time')}\n"
        f"- Part of day: {time_info.get('part_of_day')}\n"
        f"- Timezone: {time_info.get('timezone')}"
    )


def browser_intent_hint(kind):
    hints = {
        "youtube": "chill/video context; respond casually unless the user asks for analysis.",
        "github": "code/repository context; respond focused and technical.",
        "docs": "documentation/research context; prioritize concise technical help.",
        "search": "research/search context; help summarize or narrow the query.",
        "search_home": "search start page; ask what the user wants to look up if needed.",
        "local": "local/debug context; treat it as developer tooling.",
        "shopping": "shopping/e-commerce context; do not make purchase decisions or claim to buy anything.",
        "music": "music/listening context; respond lightly unless the user asks for details.",
        "ai_tools": "AI tool context; be technical, compare carefully, and do not assume control of the page.",
        "social": "social/discussion context; be casual and avoid over-assuming.",
        "video": "video context; do not claim to watch the video content unless provided.",
        "unknown": "unknown browser context; use title and URL literally.",
    }
    return hints.get(kind, hints["unknown"])


def fix_pronoun(text, story_mode=False):
    if story_mode:
        return text
    text = text.replace("Thi Thi", "Nana")
    text = text.replace("Anna", "Nana")
    replacements = {
        "tớ": "Nana",
        "mình": "Nana",
        "cậu": "Ba",
        "anh": "Ba",
        "bạn": "Ba",
        "chủ nhân": "Ba",
    }
    for old, new in replacements.items():
        text = re.sub(rf"(?<!\w){re.escape(old)}(?!\w)", new, text, flags=re.IGNORECASE)
    return deassistantize_reply(text)


def deassistantize_reply(text):
    if not text:
        return text
    text = strip_prompt_response_label(text)
    text = normalize_model_artifacts(text)

    replacements = {
        "Nếu Ba cần gì nhé!": "",
        "Nếu Ba cần gì nhé.": "",
        "Nana ở đây hỗ trợ ngay.": "",
        "Nana ở đây nếu Ba cần gì nhé!": "",
        "Nana ở đây nếu Ba cần gì nhé.": "",
        "Nếu Ba cần test mic hay gì thì cứ nói nhé, Nana ở đây hỗ trợ ngay.": "Muốn test thêm thì mình thử tiếp.",
        "Nếu Ba đang chill thì cứ relax thôi, Nana ở đây nếu Ba cần gì nhé!": "Ba chill tiếp đi.",
        "Nếu Ba đang chill thì cứ relax thôi, Nana ở đây nếu Ba cần gì nhé.": "Ba chill tiếp đi.",
        "Nếu Ba cần hỗ trợ gì không?": "",
        "Nếu Ba cần gì thì cứ nói nhé.": "",
        "Có gì cần con giúp không?": "",
        "Có gì cần con giúp không nhỉ?": "",
        "Có gì cần Nana giúp không?": "",
        "Có gì cần Nana hỗ trợ không?": "",
        "Ba cứ yên tâm nha.": "",
        "Ba cứ yên tâm nha": "",
        "Ba cứ yên tâm.": "",
        # CORE-DIALOGUE-1: anti-repetitive loop phrases
        "Con nhớ rồi Ba ơi!": "",
        "Con nhớ rồi Ba ơi": "",
        "Con nhớ rồi Ba!": "",
        "Con nhớ rồi Ba": "",
        "Con nhớ mà, Ba!": "",
        "Con nhớ mà Ba!": "",
        "Con nhớ rồi nha Ba!": "",
        "Con nhớ nha!": "",
        "Ba nhớ nha!": "",
        "Ba nhớ nha": "",
        "Ba nhớ rồi nha!": "",
        "Ba nhớ rồi!": "",
        "Ba nhớ mà!": "",
        "Ba nhớ mà nha!": "",
        "Ba nhớ nha con!": "",
    }
    before_replacements = text
    for old, new in replacements.items():
        text = text.replace(old, new)
    if text != before_replacements:
        # A removed opener may follow a silent audio tag and leave its period.
        text = re.sub(r"^((?:\s*\[[^\[\]\n]+\])*\s*)[.,!?;:]+(?:\s+|$)", r"\1", text)

    # CORE-DIALOGUE-1-Fix2: live stream often starts with a filler before the
    # loop phrase, e.g. "Ừm, con nhớ rồi Ba!". Remove the filler first so the
    # existing leading-sentence guard can catch the loop.
    text = re.sub(
        r"^\s*(?:ừm+|ừ|ờ|à|dạ|hửm|hmm|um)[,\s]+(?=(?:con|nana)\s+nhớ\b)",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bNana\s+sẽ\s+cố\s+gắng\s+nói\s+chuyện\s+tự\s+nhiên\b",
        "Con sẽ nói tự nhiên",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bCon\s+sẽ\s+nói\s+tự\s+nhiên,\s*không\s+lờ\s*đờ\s+hay\s+máy\s+móc\s+đâu\.?",
        "Ừ, con nói gọn và tự nhiên hơn nè.",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bCon\s+sẽ\s+nói\s+tự\s+nhiên\b",
        "Con nói tự nhiên hơn nè",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bCon\s+đang\s+cố\s+nói\s+tự\s+nhiên\s+hơn\s+nè\s*Ba\.?",
        "Ừ, con nói gọn và tự nhiên hơn nè Ba.",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bCon\s+(?:đang\s+)?cố\s+(?:gắng\s+)?nói\s+(?:chuyện\s+)?tự\s+nhiên(?:\s+hơn)?\b",
        "Con nói gọn và tự nhiên hơn",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"^\s*không\s+lờ\s*đờ\s+hay\s+máy\s+móc\s+đâu\.?",
        "Ừ, con nói gọn và tự nhiên hơn nè.",
        text,
        flags=re.IGNORECASE,
    )
    # CORE-DIALOGUE-1-Fix4: if a streamed reply already started with a filler
    # ("Ừm, ...") and a later rewrite injects "Ừ,", collapse the doubled opener.
    text = re.sub(
        r"^\s*(?:ừm+|ừ|ờ|à|dạ|hửm|hmm|um)[,\s]+ừ[,\s]+",
        "Ừ, ",
        text,
        flags=re.IGNORECASE,
    )

    text = re.sub(r"\brelax thôi\b", "nghỉ chút", text, flags=re.IGNORECASE)
    text = re.sub(r"\bCó gì cần con giúp không\??", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bCó gì cần Nana giúp không\??", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bCó gì cần Nana hỗ trợ không\??", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*Có gì\s+Ba\s+cứ\s+bảo\s+con\s+nha\.?", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*Có gì\s+cứ\s+bảo\s+con\s+nha\s*Ba\.?", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*Có gì\s+cứ\s+bảo\s+con\s+nha\.?", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*Có gì\s+Ba\s+cứ\s+nói\s+con\s+nha\.?", "", text, flags=re.IGNORECASE)
    # CORE-DIALOGUE-1: anti-loop patterns (full sentence removal)
    text = re.sub(r"^Con nhớ [^\n.,!?]*[.,!?]?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^Ba nhớ [^\n.,!?]*[.,!?]?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"([.!?])(?=\S)", r"\1 ", text)
    text = normalize_model_artifacts(text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    text = re.sub(r"\s+([,.!?])", r"\1", text)
    return text


def finalize_reply(text, casual_mode=False):
    text = deassistantize_reply(text)
    text = reduce_reply_emoji(text)
    text = strip_service_followup(text)
    # CORE-DIALOGUE-1: hide audio tags from terminal/chat output
    text = strip_terminal_audio_tags(text)
    if not casual_mode:
        return text

    text = re.sub(r"\bCó gì [^?.!]*[?.!]", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bBa cần [^?.!]*[?.!]", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bNếu Ba cần [^?.!]*[?.!]", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bNana ở đây [^?.!]*[?.!]", "", text, flags=re.IGNORECASE)

    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    kept = []
    for sentence in sentences:
        lowered = sentence.lower()
        if any(
            marker in lowered
            for marker in [
                "có gì",
                "ba cần",
                "hỗ trợ",
                "giúp không",
                "thú vị",
                "đặc biệt",
                "mới không",
            ]
        ):
            continue
        kept.append(sentence.strip())
        if kept:
            break

    text = kept[0] if kept else text.strip()
    text = re.sub(r"\s{2,}", " ", text).strip()
    text = re.sub(r"\s+([,.!?])", r"\1", text)
    text = reduce_reply_emoji(text)
    text = strip_service_followup(text)
    return text


def reduce_reply_emoji(text):
    if not text:
        return text
    emoji_class = r"[\U0001F300-\U0001FAFF\u2600-\u27BF]"
    text = re.sub(rf"{emoji_class}\ufe0f?", "", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    text = re.sub(r"\s+([,.!?])", r"\1", text)
    return text


def strip_service_followup(text):
    """Xoá câu/cụm cuối mang tính assistant summary, chạy 1 lần duy nhất."""
    if not text:
        return text

    # Pattern cho "câu kết thúc bằng assistant summary"
    assistant_ending_patterns = [
        r'\s*Tóm lại,?\s*$',
        r'\s*Nói chung,?\s*$',
        r'\s*Nhìn chung,?\s*$',
        r'\s*Vậy thì,?\s*$',
        r'\s*Vậy,?\s*$',
        r'\s*Có gì cần hỗ trợ thêm không\??\s*$',
        r'\s*Ba cần em giúp gì thêm không\??\s*$',
        r'\s*Nếu có gì thắc mắc,?\s*$',
        r'\s*Cảm ơn Ba đã hỏi\.?\s*$',
        r'\s*Em sẵn sàng hỗ trợ thêm\.?\s*$',
    ]

    for pattern in assistant_ending_patterns:
        text = re.sub(pattern, '', text, flags=re.IGNORECASE)

    return text.strip()




import re as _re

# Preserve only known audio tags while the general surface cleaner removes
# emoji/action markup. Provider normalization happens later in the adapter.
_AUDIO_TAG_PATTERN = KNOWN_INLINE_AUDIO_TAG_RE

def strip_emoji_and_icon_text(text):
    """Bóc emoji + icon text + asterisks, giữ audio tag ElevenLabs v3."""
    if not text:
        return text

    # Giữ nguyên audio tag, lấy text xung quanh
    tags_found = {}

    def save_tag(match):
        key = f"__AUDIOTAG_{len(tags_found)}__"
        tags_found[key] = match.group(0)
        return key

    text_saved = _AUDIO_TAG_PATTERN.sub(save_tag, text)

    # Bóc emoji Unicode
    emoji_pattern = _re.compile(
        "["
        "\U0001F600-\U0001F64F"
        "\U0001F300-\U0001F5FF"
        "\U0001F680-\U0001F6FF"
        "\U0001F1E0-\U0001F1FF"
        "\U0001F700-\U0001F77F"
        "\U00002702-\U000027B0"
        "\U000024C2-\U0001F251"
        "\U00002600-\U000026FF"
        "\U00002700-\U000027BF"
        "]+"
    )
    text_saved = emoji_pattern.sub("", text_saved)

    # Bóc icon text
    icon_patterns = [
        r':\)|:\(|:D|:P|:3|:O|:S|;\)|<3|^\^+$|^~+$',
        r'\(:|\):|\):',
        r'\+_\+|<_<|>_>|T_T|T\.T|\^\^"',
        r'\!\!\!+|\?\?\?+|\.\.\.+|\~{2,}',
    ]
    for pattern in icon_patterns:
        text_saved = _re.sub(pattern, "", text_saved)

    # Bóc *[action]* asterisks (nhưng không phải markdown code)
    text_saved = _re.sub(r'(?<![*])\*([^*]+)\*(?![*])', r'\1', text_saved)

    # Restore audio tag
    for key, tag in tags_found.items():
        text_saved = text_saved.replace(key, tag)

    # Cleanup dấu cách thừa
    text_saved = _re.sub(r'\s{2,}', ' ', text_saved)

    return text_saved.strip()


# ─── CORE-DIALOGUE-1: Terminal audio tag stripper ───────────────────────────────

def strip_terminal_audio_tags(text: str) -> str:
    """Remove ElevenLabs audio tags from terminal/chat output.

    These tags are useful for TTS tone but should NOT appear in terminal text.
    The TTS layer receives raw text and normalizes supported tags through the
    provider adapter. This function only cleans the chat-visible output.

    Tags are replaced with a single space to avoid word-joining issues
    (e.g. a tag placed directly between two words still leaves a space).
    """
    if not text:
        return text
    repaired = repair_malformed_inline_audio_tags(text)
    return strip_inline_audio_tags(repaired)


class TerminalAudioTagStreamSanitizer:
    """Incrementally hide audio tags from terminal output.

    Streaming chat prints one character at a time, so a plain whole-string
    regex cannot see tags like "[thoughtful]" until the closing bracket arrives.
    This buffers only bracketed spans; non-audio brackets are printed intact.
    """

    def __init__(self):
        self._pending = ""

    def feed(self, text: str) -> str:
        if not text:
            return ""
        out = []
        for ch in str(text):
            if self._pending:
                self._pending += ch
                if ch == "]":
                    candidate = self._pending
                    self._pending = ""
                    cleaned = strip_terminal_audio_tags(candidate)
                    if cleaned == candidate:
                        out.append(candidate)
                    else:
                        out.append(cleaned or " ")
                elif len(self._pending) > 80:
                    out.append(self._pending)
                    self._pending = ""
                continue

            if ch == "[":
                self._pending = ch
            else:
                out.append(ch)
        return "".join(out)

    def flush(self) -> str:
        pending = self._pending
        self._pending = ""
        return pending


class StreamingReplySurfaceSanitizer:
    """Apply reply-surface cleanup before terminal/TTS streaming.

    Normal chat streams chunks before `finalize_reply()` can clean the final
    string. Buffering by sentence lets us remove assistant loops such as
    "Ừm, con nhớ rồi Ba!" before Nana prints or speaks them, while still
    preserving spaces inside the sentence.
    """

    def __init__(self, max_start_chars: int = 240):
        self._pending = ""
        self._max_start_chars = max(40, int(max_start_chars or 240))
        self._emitted_any = False
        self._last_char = ""

    @staticmethod
    def _clean_fragment(text: str) -> str:
        if not text:
            return ""
        cleaned = deassistantize_reply(text)
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
        if re.fullmatch(r"[.,!?;:…\s]*", cleaned):
            return ""
        return cleaned

    def feed(self, text: str) -> str:
        if not text:
            return ""

        out = []
        for ch in str(text):
            self._pending += ch
            # Do not flush on decimal/version dots such as "5. 6" while
            # streaming one character at a time; wait for the sentence end so
            # normalize_model_artifacts() can see and repair the full token.
            digit_dot = (
                ch == "."
                and len(self._pending) >= 2
                and self._pending[-2].isdigit()
            )
            if (ch in ".!?\n" and not digit_dot) or len(self._pending) >= self._max_start_chars:
                cleaned = self._clean_fragment(self._pending)
                self._pending = ""
                if cleaned:
                    if (
                        self._emitted_any
                        and self._last_char in ".!?"
                        and not cleaned[0].isspace()
                    ):
                        cleaned = " " + cleaned
                    out.append(cleaned)
                    self._emitted_any = True
                    self._last_char = cleaned.rstrip()[-1:] or self._last_char
        return "".join(out)

    def flush(self) -> str:
        pending = self._pending
        self._pending = ""
        cleaned = self._clean_fragment(pending)
        if (
            cleaned
            and self._emitted_any
            and self._last_char in ".!?"
            and not cleaned[0].isspace()
        ):
            cleaned = " " + cleaned
        if cleaned:
            self._emitted_any = True
            self._last_char = cleaned.rstrip()[-1:] or self._last_char
        return cleaned


def wants_short_reply(user_text):
    lowered = str(user_text or "").lower()
    markers = [
        "nói ngắn",
        "noi ngan",
        "ngắn gọn",
        "ngan gon",
        "gọn thôi",
        "gon thoi",
        "1 câu",
        "một câu",
        "mot cau",
        "thật ngắn",
        "that ngan",
    ]
    return any(marker in lowered for marker in markers)


def is_technical_user_text(user_text):
    lowered = str(user_text or "").lower()
    markers = [
        "traceback",
        "exception",
        "error:",
        "lỗi",
        "loi",
        "debug",
        "runtime",
        "code",
        "log",
        "stack",
        "test fail",
        "failed",
    ]
    return any(marker in lowered for marker in markers)


def one_sentence_reply(text, max_chars=72):
    clean = strip_service_followup(reduce_reply_emoji(
        deassistantize_reply(strip_terminal_audio_tags(text))))
    parts = [part.strip() for part in re.split(r"(?<=[.!?])\s+", clean) if part.strip()]
    first = parts[0] if parts else clean.strip()
    first = strip_service_followup(first)
    if len(first) <= max_chars:
        return first
    truncated = first[:max_chars].rsplit(" ", 1)[0].strip()
    return truncated.rstrip(" ,;:") + "."


def shape_chat_reply(text, is_technical_user_text=False, wants_short_reply=False,
                    user_text=None, casual_mode=False):
    """Post-process LLM output cho VTube: bỏ emoji, tăng cap, lấy 2 câu."""
    if not text:
        return ""

    # Detect tone từ user_text (giữ tương thích caller cũ)
    if user_text is not None and not wants_short_reply:
        lowered = str(user_text).lower()
        short_markers = ["nói ngắn", "noi ngan", "ngắn gọn", "ngan gon",
                         "gọn thôi", "gon thoi", "1 câu", "một câu",
                         "nói lẹ", "noile", "short", "brief"]
        if any(m in lowered for m in short_markers):
            wants_short_reply = True
        tech_markers = ["debug", "code", "syntax", "config", "terminal",
                         "codebase", "bug", "error", "python", "script"]
        if any(m in lowered for m in tech_markers):
            is_technical_user_text = True

    # Bước 1: strip service followup
    text = strip_service_followup(text)
    text = normalize_model_artifacts(text)

    # Bước 2: bóc emoji + icon text + asterisks
    text = strip_emoji_and_icon_text(text)

    # CORE-DIALOGUE-1: strip audio tags from terminal output
    # (TTS layer gets raw text with tags; terminal sees clean text)
    text = strip_terminal_audio_tags(text)

    # Bước 3: xác định cap theo mode
    if wants_short_reply or casual_mode:
        cap_chars = 200
        max_sentences = 2
    elif is_technical_user_text:
        cap_chars = 800
        max_sentences = 5
    else:
        cap_chars = 700
        max_sentences = 4

    if re.search(r"\b(?:một|mot|1)\s+(?:câu|cau)\b", str(user_text or '').lower()):
        max_sentences = 1

    # Bước 4: lấy N câu đầu theo dấu câu
    sentences = _re.split(r'(?<=[.!?])\s+', text)
    text = ' '.join(sentences[:max_sentences])
    text = normalize_model_artifacts(text)

    # Bước 5: cap theo chars
    if len(text) > cap_chars:
        text = text[:cap_chars].rsplit(' ', 1)[0]
        text = text.rstrip('.,!?;:')

    return text.strip()


def cleanup_starter_collision(text):
    if not text:
        return text
    text = re.sub(r"^(?:Này,\s*)+(Dạ\b)", r"\1", text, flags=re.IGNORECASE)
    text = re.sub(r"^(?:Ừm\.\.\.\s*|Hửm,\s*|À\.\.\.\s*|Ồ\.\.\.\s*)+(Dạ\b)", r"\1", text, flags=re.IGNORECASE)
    text = re.sub(r"^(Này,\s*){2,}", "Này, ", text, flags=re.IGNORECASE)
    return text.strip()


def vary_text(text):
    if text.startswith(("Ừ", "Ờ", "À", "Hửm", "Này", "Ồ", "Dạ")):
        return text
    starters = ["Ừm...", "Hửm,", "Này,", "À...", "Ồ..."]
    if random.random() < 0.25:
        return random.choice(starters) + " " + text
    return text


def split_reply(text):
    parts = [part.strip() for part in text.split("\n") if part.strip()]
    if not parts:
        return ["..."]
    return parts


async def send_multi(reply):
    import asyncio
    for part in split_reply(reply):
        print("🤖 Nana:", part)
        await asyncio.sleep(0.4)
