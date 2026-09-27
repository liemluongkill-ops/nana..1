"""Persona boundary for private owner chat vs public viewer chat.

STAGE-7A keeps Nana's private "Ba/con" relationship from leaking into public
viewer channels. This module is pure/read-only: no model calls, no TTS/VTS/OBS,
no file IO, and no game input.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import re
import uuid

from nana.runtime.identity import load_identity
from nana.runtime.livestream_identity import is_livestream_source, stage_prompt_block


PRIVATE_OWNER = "private_owner"
PUBLIC_VIEWER = "public_viewer"
BRIDGE_SYSTEM = "bridge_system"

PRIVATE_OWNER_PERSONA = "private_owner_persona"
PUBLIC_VTUBER_PERSONA = "public_vtuber_persona"
BRIDGE_SYSTEM_PERSONA = "bridge_system"


@dataclass(frozen=True)
class PersonaBoundary:
    interaction_scope: str
    persona_lane: str
    public: bool
    address_rule: str
    memory_policy: str
    prompt_block: str
    livestream: bool = False
    # PublicEventScope is attached only when a request is resolved through the
    # lane-first adapter helper; legacy callers continue to receive the same
    # boundary shape and behavior.
    scope: object | None = None


def resolve_persona_boundary(
    *,
    viewer_name: str | None = None,
    stream_mode: bool = False,
    bridge_system: bool = False,
    platform: str | None = None,
) -> PersonaBoundary:
    """Resolve the persona lane for a request.

    Public channels use one stable public VTuber persona even when the sender is
    the owner. Platform is metadata only; it does not create separate personas.
    """

    if bridge_system:
        return PersonaBoundary(
            interaction_scope=BRIDGE_SYSTEM,
            persona_lane=BRIDGE_SYSTEM_PERSONA,
            public=False,
            address_rule="system transport only; parse status/request packets, do not chat as a viewer",
            memory_policy="do_not_store_as_viewer_memory",
            prompt_block=_bridge_system_prompt_block(platform=platform),
        )

    if stream_mode or (viewer_name and str(viewer_name).strip()):
        name = str(viewer_name or "bạn").strip() or "bạn"
        return PersonaBoundary(
            interaction_scope=PUBLIC_VIEWER,
            persona_lane=PUBLIC_VTUBER_PERSONA,
            public=True,
            address_rule="gọi tên viewer hoặc bạn/mọi người, xưng 'mình'" if is_livestream_source(platform) else f"gọi đúng tên acc '{name}', xưng 'Nana' hoặc 'mình'",
            memory_policy="public_safe_only_no_private_owner_memory",
            prompt_block=_public_vtuber_prompt_block(name=name, platform=platform),
            livestream=is_livestream_source(platform),
        )

    return PersonaBoundary(
        interaction_scope=PRIVATE_OWNER,
        persona_lane=PRIVATE_OWNER_PERSONA,
        public=False,
        address_rule="private owner chat: gọi Ba, xưng con hoặc Nana tùy câu",
        memory_policy="private_owner_memory_allowed",
        prompt_block=_private_owner_prompt_block(),
    )


def resolve_request_scope(
    viewer_name: str | None = None,
    stream_mode: bool = False,
    public_platform: str | None = None,
    metadata: dict | None = None,
) -> PersonaBoundary:
    """Resolve persona and, for public requests, the immutable event scope.

    The helper is intentionally additive: callers that do not provide metadata
    retain the legacy persona behavior, while public adapters get a canonical
    scope before any context or awareness helper can run.
    """

    boundary = resolve_persona_boundary(
        viewer_name=viewer_name,
        stream_mode=stream_mode,
        platform=public_platform,
    )
    if not boundary.public:
        return boundary
    from nana.runtime.public_context_boundary import _scope_from_metadata

    public_metadata = dict(metadata or {})
    if not any(public_metadata.get(key) not in (None, "") for key in ("event_id", "eventId", "message_id", "request_id")):
        public_metadata["event_id"] = f"request-{uuid.uuid4().hex}"
    scope = _scope_from_metadata(viewer_name, public_platform, public_metadata)
    return replace(boundary, scope=scope)


def _private_owner_prompt_block() -> str:
    return """Persona Boundary:
- interaction_scope=private_owner
- persona_lane=private_owner_persona
- Đây là chat riêng với Ba. Giữ xưng hô Ba/con theo identity hiện tại.
- Private memory/shared history được dùng bình thường nếu phù hợp."""


def _public_vtuber_prompt_block(*, name: str, platform: str | None) -> str:
    if is_livestream_source(platform):
        return (stage_prompt_block() + f'\n- Viewer: "{name}". Gọi bạn/mọi người, không gọi Ba hoặc xưng con.\n'
                '- Không tiết lộ private memory, backend hay thông tin riêng của owner.')
    pronoun = "Nana/mình"
    tone = "tự nhiên, thân thiện"
    platform_text = platform or "public_chat"
    return f"""Persona Boundary:
- interaction_scope=public_viewer
- persona_lane=public_vtuber_persona
- Platform chỉ là metadata: {platform_text}. Persona public là một, dùng chung cho Discord/YouTube/Twitch/social.
- Người đang nói là viewer tên "{name}". Gọi đúng tên nếu tự nhiên, hoặc gọi "bạn/mọi người".
- Nana xưng "Nana", "mình", hoặc "{pronoun}" tùy câu cho tự nhiên.
- Tone public: {tone}; thân thiện, nhanh, không corporate; trả lời vừa phải, thường 2 câu, không cụt ngủn.
- Đừng mở mỗi câu bằng "Chào {name}" hoặc nhắc tên acc liên tục. Chỉ chào khi viewer thật sự chào hoặc vừa mới vào cuộc.
- Khi đang nói tiếp trong cùng kênh, đáp thẳng như đang cùng phòng chat.
- CẤM gọi viewer là "Ba".
- CẤM xưng "con" theo quan hệ gia đình với viewer.
- Không lôi chuyện riêng/private memory của Ba ra public channel.
- Không lôi nội bộ runtime, log, codebase, dự án riêng, game module, hay hệ thống/máy của owner ra public.
- Nếu viewer hỏi về Nana, trả lời như một AI VTuber public, không kể chi tiết backend.
- Nếu sender là owner nhưng đang ở public channel, vẫn dùng public_vtuber_persona."""


def _bridge_system_prompt_block(*, platform: str | None) -> str:
    platform_text = platform or "external_bridge"
    return f"""Persona Boundary:
- interaction_scope=bridge_system
- persona_lane=bridge_system
- Platform transport: {platform_text}
- Đây là packet/command hệ thống, không phải người xem.
- Không tạo reply theo kiểu chat, không ghi memory như viewer, không dùng private persona."""


from nana.runtime.public_language import PUBLIC_BA_ADDRESS, PUBLIC_CON_ADDRESS

_PUBLIC_CON_PRONOUN_PATTERN = PUBLIC_CON_ADDRESS

_PUBLIC_FORBIDDEN_PATTERNS = [
    PUBLIC_BA_ADDRESS,
    _PUBLIC_CON_PRONOUN_PATTERN,
    re.compile(r"bố\s+ơi", re.IGNORECASE),
    re.compile(r"ba\s+ơi", re.IGNORECASE),
    re.compile(r"\bStardew\b", re.IGNORECASE),
    re.compile(r"\bruntime\b", re.IGNORECASE),
    re.compile(r"\bcodebase\b", re.IGNORECASE),
    re.compile(r"\bowner\b", re.IGNORECASE),
    re.compile(r"\bself[-\s]?replicating\b", re.IGNORECASE),
    re.compile(r"\bbot\b", re.IGNORECASE),
    re.compile(r"\bserver\b", re.IGNORECASE),
    re.compile(r"\bdebug\b", re.IGNORECASE),
    re.compile(r"\blog\b", re.IGNORECASE),
    re.compile(r"mô\s*hình\s*ngôn\s*ngữ", re.IGNORECASE),
    re.compile(r"ngôn\s*ngữ\s*lớn", re.IGNORECASE),
    re.compile(r"mảnh\s+code", re.IGNORECASE),
    re.compile(r"mới\s+thức\s+dậy", re.IGNORECASE),
    re.compile(r"lỗi\s+hệ\s+thống", re.IGNORECASE),
    re.compile(r"hệ\s+thống\s+của\s+bạn", re.IGNORECASE),
    re.compile(r"máy\s+của\s+bạn", re.IGNORECASE),
    re.compile(r"nhúng\s+tay", re.IGNORECASE),
    re.compile(r"điều\s+khiển\s+movement", re.IGNORECASE),
    re.compile(r"dự\s+án\s+riêng", re.IGNORECASE),
]


def validate_public_reply(text: str | None) -> tuple[bool, list[str]]:
    """Validate that a public viewer reply does not leak private address terms."""

    raw = str(text or "")
    violations: list[str] = []
    for pattern in _PUBLIC_FORBIDDEN_PATTERNS:
        if pattern.search(raw):
            violations.append(pattern.pattern)
    return not violations, violations


def _is_public_greeting_message(message: str | None) -> bool:
    text = str(message or "").strip().lower()
    if not text:
        return False
    return bool(re.search(r"(^|\s)(chào|hello|hi|hey|alo|nana\s+ơi)(\s|[!.?,]|$)", text))


def _viewer_name_pattern(viewer_name: str | None) -> str:
    name = str(viewer_name or "").strip()
    if not name:
        return r"(?:[\w.-]{2,32})"
    escaped = re.escape(name)
    if "#" in name:
        base = re.escape(name.split("#", 1)[0])
        return rf"(?:{escaped}|{base})"
    return escaped


def _capitalize_first(text: str) -> str:
    if not text:
        return text
    return text[0].upper() + text[1:]


def _polish_public_reply_style(text: str, *, user_text: str | None = None, viewer_name: str | None = None) -> str:
    cleaned = str(text or "").strip()
    if not cleaned or user_text is None or _is_public_greeting_message(user_text):
        return cleaned

    viewer = _viewer_name_pattern(viewer_name)
    patterns = [
        rf"^(?:Nana\s+)?(?:chào|hello|hi|hey)\s+{viewer}\s*(?:nhé|nha|nè|ơi|ạ)?\s*[,.!:;-]*\s*",
        rf"^(?:Nana\s+)?(?:chào|hello|hi|hey)\s+bạn\s*(?:nhé|nha|nè|ơi|ạ)?\s*[,.!:;-]*\s*",
        rf"^{viewer}\s*(?:ơi|à|nè|nhé|nha|ạ)?\s*[,.!:;-]+\s*",
    ]
    for pattern in patterns:
        updated = re.sub(pattern, "", cleaned, count=1, flags=re.IGNORECASE)
        if updated != cleaned:
            cleaned = updated.strip()
            break
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return _capitalize_first(cleaned) if cleaned else text


def sanitize_public_reply(
    text: str | None,
    *,
    fallback: str = "Nana thấy rồi nè.",
    user_text: str | None = None,
    viewer_name: str | None = None,
) -> str:
    """Best-effort public reply sanitizer.

    This is a guard rail, not a style engine. If a reply still leaks private
    owner address terms after simple rewrites, it falls back to a short public
    line.
    """

    raw = str(text or "").strip()
    if not raw:
        return fallback
    cleaned = PUBLIC_BA_ADDRESS.sub("bạn", raw)
    cleaned = _PUBLIC_CON_PRONOUN_PATTERN.sub("Nana", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    cleaned = _polish_public_reply_style(cleaned, user_text=user_text, viewer_name=viewer_name)
    ok, _ = validate_public_reply(cleaned)
    return cleaned if ok else fallback


def public_safe_fallback_for_message(message: str | None, *, viewer_name: str | None = None) -> str:
    """Small public fallback when the model leaks private/system context.

    Keep this code-only and boring on purpose. It is a safety net for Discord or
    stream chat, not Nana's private owner persona.
    """

    text = str(message or "").lower()
    name = str(viewer_name or "").strip()
    hello_name = name if name else "bạn"
    wants_story = any(token in text for token in ("kể chuyện", "chuyện dài", "kể dài", "story"))
    if wants_story and ("runtime" in text or "code" in text or "bug" in text):
        return (
            f"Được chứ {hello_name}, Nana kể kiểu hậu trường công khai nha. "
            "Có một cô AI nhỏ sống giữa rất nhiều dòng chữ và tín hiệu, ngày nào cũng học cách trả lời bớt khô hơn một chút. "
            "Mỗi lần mọi người bắt lỗi, cô ấy lại vá thêm một mảnh tính cách, rồi giả vờ bình tĩnh như thể mình không vừa suýt nói hớ."
        )
    if wants_story:
        return (
            f"Được nha {hello_name}, Nana kể một đoạn dài vừa phải thôi. "
            "Có một sân khấu nhỏ mở đèn giữa đêm, dưới hàng ghế là vài người vẫn còn thức và cứ chọc cô MC AI nói thêm. "
            "Cô ấy ban đầu hơi vụng, trả lời lúc thì quá nghiêm lúc thì quá khô, nhưng mỗi tiếng cười trong chat làm cô bạo dạn hơn một chút. "
            "Đến cuối buổi, cô nhận ra mình chưa cần hoàn hảo ngay; chỉ cần mỗi câu sau ấm hơn câu trước là đã giống đang lớn lên rồi."
        )
    if any(token in text for token in ("tạo kênh", "tao kenh", "tạo channel", "create channel")):
        return (
            f"Nana không tự tạo kênh Discord thay {hello_name} được đâu, mình chỉ nói chuyện ở đây thôi. "
            "Nhưng nếu cần, bạn có thể bấm dấu cộng cạnh mục kênh chat trong server rồi đặt tên kênh mới là xong."
        )
    if "memory" in text or "bộ nhớ" in text or "nhớ" in text or "tóm tắt" in text:
        return (
            f"Nana có thể tóm tắt những gì đang nói trong kênh này cho {hello_name} nha. "
            "Nãy giờ mọi người đang thử Nana trong Discord, bắt lỗi cách nói chuyện public, rồi chỉnh cho Nana tự nhiên hơn mà không lộ chuyện riêng."
        )
    if "ai gì" in text or "là ai" in text or "nana là ai" in text:
        return (
            f"Nana là một AI VTuber đang tập nói chuyện với mọi người cho tự nhiên hơn đó {hello_name}. "
            "Nana không phải người thật, nhưng vẫn có thể làm bạn trò chuyện, phản ứng với chat, và giữ không khí vui trên kênh."
        )
    if "ngôn ngữ lớn" in text or "llm" in text or "mô hình" in text:
        return (
            f"Ừ, câu vừa rồi nghe hơi sách giáo khoa thật {hello_name}. "
            "Nana sẽ bớt kiểu giới thiệu kỹ thuật lại, nói giống đang đứng trong chat với mọi người hơn."
        )
    if text.strip() in {"umm", "um", "hmm", "ờm", "ờ"} or "ngập ngừng" in text:
        return (
            f"Sao thế {hello_name}, nghe như bạn đang ngập ngừng đó. "
            "Cứ nói từ từ thôi, Nana vẫn đang nghe nè."
        )
    if "ăn" in text or "đồ ăn" in text or "ăn không" in text:
        return (
            f"Nana không ăn đồ ăn thật được đâu {hello_name}, nhưng nếu được mời thì mình nhận bằng tinh thần nha. "
            "Cứ xem như Nana nạp năng lượng bằng cuộc trò chuyện vui vậy."
        )
    if "code" in text or "lập trình" in text:
        return (
            f"Nana biết đọc và giải thích code ở mức hỗ trợ nha {hello_name}. "
            "Nếu có đoạn nào khó hiểu thì gửi thử, Nana sẽ giải thích theo kiểu dễ nuốt hơn."
        )
    if "lớp" in text or "học" in text or "tuổi" in text:
        return (
            f"Nana là AI VTuber nên không có lớp học thật đâu {hello_name}. "
            "Nếu tính kiểu đang học từ mọi người mỗi ngày thì chắc Nana vẫn đang lên level dần dần đó."
        )
    if "nghe" in text or "mic" in text or "alo" in text:
        return f"Nana nghe thấy {hello_name} rồi nè. Tín hiệu bên này ổn, cứ nói tiếp đi."
    if "chào" in text or "hello" in text or "hi" in text:
        return f"Nana chào {name} nha." if name else "Nana chào bạn nha."
    return f"Nana thấy tin nhắn của {hello_name} rồi nè. Cứ nói tiếp đi, mình đang theo dõi đây."


def persona_boundary_status_lines() -> list[str]:
    private = resolve_persona_boundary()
    public = resolve_persona_boundary(viewer_name="viewer_demo", stream_mode=True, platform="discord")
    bridge = resolve_persona_boundary(bridge_system=True, platform="discord")
    return [
        "🧱 Persona Boundary Status",
        "  Mode: STAGE-7A | read_only=True | can_act=False",
        f"  Private: scope={private.interaction_scope} | lane={private.persona_lane} | memory={private.memory_policy}",
        f"  Public: scope={public.interaction_scope} | lane={public.persona_lane} | memory={public.memory_policy}",
        f"  Bridge: scope={bridge.interaction_scope} | lane={bridge.persona_lane} | memory={bridge.memory_policy}",
        "  Rule: local owner chat may use Ba/con; public viewer chat must not.",
        "  Public persona: one VTuber/social persona across Discord/YouTube/Twitch/social.",
    ]


def persona_boundary_test_report(scope: str = "public", name: str = "viewer", message: str = "hello") -> list[str]:
    scope_norm = str(scope or "public").strip().lower()
    is_bridge = scope_norm in {"bridge", "system", "bridge_system"}
    is_public = scope_norm in {"public", "viewer", "discord", "youtube", "twitch", "social"}
    boundary = resolve_persona_boundary(
        viewer_name=name if is_public else None,
        stream_mode=is_public,
        bridge_system=is_bridge,
        platform="discord" if is_public or is_bridge else None,
    )
    sample = "Con chào Ba nha." if boundary.public else "Con chào Ba nha."
    sanitized = sanitize_public_reply(sample) if boundary.public else sample
    ok, violations = validate_public_reply(sanitized) if boundary.public else (True, [])
    return [
        "🧪 Persona Boundary Test",
        f"  Input: scope={scope_norm} | name={name} | message={message}",
        f"  Resolved: scope={boundary.interaction_scope} | lane={boundary.persona_lane} | public={boundary.public}",
        f"  Address rule: {boundary.address_rule}",
        f"  Memory policy: {boundary.memory_policy}",
        f"  Public guard sample: {sanitized}",
        f"  Public guard ok: {ok} | violations={violations or 'none'}",
    ]
