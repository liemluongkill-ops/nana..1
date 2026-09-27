"""Canonical P1-B public-response grounding boundary.

The public hot path uses this module through re-exports in
``runtime.memory_grounding``. It is deliberately read-only and accepts only
public lane candidates that have already been scope-bound. The second safety
filter here is intentional: a caller cannot accidentally bypass the public
provenance contract by passing an untrusted dictionary directly to prompt
assembly.

Policy:

* Public candidates require a public lane, canonical platform, exact
  namespaced actor, exact room, non-empty source event, verification,
  consent, and a future retention deadline.
* Missing actor/room/platform/provenance/TTL is rejection, never wildcard.
* Query relevance is required before any fact enters a prompt. A candidate
  with ``score`` or ``semantic_score`` >= 0.75 may represent an injected fake
  semantic match for tests; that is not provider acceptance.
* High-confidence direct fact questions use a deterministic bounded answer.
  Otherwise a prompt block is produced and the final model reply is verified
  before public delivery.
* The verifier checks salient fact values, not just generic token overlap.
  A substitution such as "cà phê" -> "trà xanh" is rejected.
* Internal identifiers are removed from prompt and answer text.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
import re
import time
import unicodedata
from typing import Any, Mapping, Sequence

try:
    from nana.runtime.history_privacy import contains_history_secret, redact_history_text
except ModuleNotFoundError:  # support direct project-root smoke execution
    from runtime.history_privacy import contains_history_secret, redact_history_text

PUBLIC_LANES = frozenset({"public", "public_stage", "public_viewer", "public_chat"})
_GENERATED_SOURCES = frozenset({
    "model", "assistant", "generated", "llm", "chatbot", "nana", "private",
    "model_generated", "system_generated",
})
_QUESTION_TYPES = frozenset({"question", "ephemeral", "session", "temporary", "transient", "model_reply"})
_UNCERTAINTY_RE = re.compile(
    r"(?:\bchua\s+(?:biet|ro|chac|co\s+thong\s+tin|nho\s+ro)\b|"
    r"\bkhong\s+(?:biet|co\s+thong\s+tin|chac|nho\s+ro)\b|"
    r"\bkhong\s+dam\s+khang\s+dinh\b|\bcan\s+kiem\s+chung\b)",
    re.IGNORECASE,
)
_INTERNAL_FIELD_RE = re.compile(
    r"\b(?:source_event_id|actor_key|room_id|stream_session_id|event_id)\s*[:=]\s*[^\s,;]+",
    re.IGNORECASE,
)

# These words carry little fact value. Relation words such as "thích" are
# retained because the verifier must distinguish a preference from another
# preference; the salient object/value is also retained.
_SALIENCE_STOPWORDS = frozenset({
    "a", "an", "and", "ba", "ban", "bạn", "cau", "cậu", "cho", "co", "có",
    "cua", "của", "con", "em", "gi", "gì", "la", "là", "ma", "mà", "minh",
    "mình", "nana", "nay", "nhung", "những", "toi", "tôi", "the", "thế",
    "what", "who", "why", "when", "where", "how", "is", "are", "the", "a",
    "chua", "biet", "chac", "khong", "co", "thong", "tin", "ro", "dam",
    "khang", "dinh", "can", "kiem", "chung", "nhung", "ve",
})
_CONVERSATIONAL_FILLER_TOKENS = frozenset({
    "va", "nhe", "nha", "do", "day", "roi", "thoi", "a", "ha", "uh", "uhm",
    "dung", "ung", "vay", "theo", "that", "ra", "ok", "okay",
})
_QUERY_GENERIC_TOKENS = frozenset({
    "minh", "mình", "toi", "tôi", "my", "i", "me", "ban", "bạn", "you",
    "nana", "thich", "thích", "gi", "gì", "what", "who", "why", "when",
    "where", "which", "how", "is", "are", "do", "does", "did", "the", "a", "an",
    "cua", "của", "ten", "tên", "like", "likes", "liked", "love", "loved",
    "prefer", "prefers", "preferred", "favorite", "favourite", "enjoy", "enjoys",
    "thich", "thích", "yeu", "yêu", "ua", "ưa", "really", "do", "does",
    "did", "am", "was", "were", "be", "been", "being", "not", "gi", "gì",
    "nao", "nào", "ai", "khi", "o", "ở", "dau", "đâu", "bao", "nhieu", "nhiêu",
})


def _fold(text: Any) -> str:
    normalized = unicodedata.normalize("NFD", str(text or "").lower())
    folded = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    folded = folded.replace("đ", "d")
    return re.sub(r"\s+", " ", folded).strip()


def _tokens(text: Any) -> set[str]:
    return set(re.findall(r"\w+", _fold(text), flags=re.UNICODE))


def _salient_tokens(text: Any) -> set[str]:
    return {
        token for token in _tokens(text)
        if token not in _SALIENCE_STOPWORDS and len(token) > 1
    }


PUBLIC_FACT_FACETS = frozenset({
    "name", "color", "drink_preference", "food_preference",
    "music_preference", "preference", "birthday", "project_deadline",
    "project_number", "phone", "residence",
})
_PREFERENCE_FACETS = frozenset({
    "preference", "color", "drink_preference", "food_preference", "music_preference",
})
_PREFERENCE_WORDS = (
    "thich", "yeu", "like", "likes", "liked", "love", "loved", "prefer",
    "favorite", "favourite", "enjoy", "enjoys", "so thich",
)
_FOOD_WORDS = r"food|foods|noodles|rice|meal|dessert|snack|eat|eating|consume"
_DRINK_WORDS = r"drink|beverage|coffee|tea|ca phe|do uong"
_MUSIC_WORDS = r"music|rock|jazz|band"
_COLOR_WORDS = r"color|colour|hue|mau"


def _contains_food_term(folded: str) -> bool:
    raw = unicodedata.normalize("NFC", str(folded or "").lower())
    return bool(
        re.search(rf"\b(?:{_FOOD_WORDS})\b", raw)
        or re.search(r"\b(?:ăn|an)\s+(?:gì|gi|uống|uong|phở|pho|cơm|com|bún|bun)\b", raw)
        or re.search(r"\b(?:phở|pho)\s+(?:bo|ga|tai|xao)\b", raw)
        or re.search(r"\b(?:món ăn|mon an|đồ ăn|do an|thích ăn|thich an)\b", raw)
    )


def _contains_drink_term(text: str) -> bool:
    raw = unicodedata.normalize("NFC", str(text or "").lower())
    return bool(
        re.search(rf"\b(?:{_DRINK_WORDS})\b", raw)
        or re.search(r"\b(?:trà|tra)\s+(?:sữa|sua|xanh|dao|đào)\b", raw)
        or re.search(r"\b(?:uống|uong|thích|thich|yeu|yêu)\s+(?:trà|tra)\b", raw)
        or re.search(r"\b(?:cà phê|ca phe|đồ uống|do uong|uống|uong)\b", raw)
        or re.search(r"\bred bull\b", raw)
    )


def _contains_music_term(text: str) -> bool:
    raw = unicodedata.normalize("NFC", str(text or "").lower())
    return bool(
        re.search(rf"\b(?:{_MUSIC_WORDS})\b", raw)
        or re.search(r"\b(?:song|bai song)\s+(?:nhac|music)\b", raw)
        or re.search(r"\b(?:nhạc|nhac|bài hát|bai hat)\b", raw)
    )


def _contains_color_term(text: str) -> bool:
    raw = unicodedata.normalize("NFC", str(text or "").lower())
    return bool(
        re.search(rf"\b(?:{_COLOR_WORDS}|màu|sắc)\b", raw)
        or re.search(r"\b(?:mau|màu)\s+(?:sac|sắc)\b", raw)
        or re.search(r"\b(?:blue|red|green|yellow|purple|black|white)\b", raw)
        or (
            re.search(r"\b(?:xanh|vàng|vang|tím|tim|đen|den|trắng|trang|đỏ)\b", raw)
            and any(word in raw for word in _PREFERENCE_WORDS)
        )
    )


def _contains_project_term(folded: str) -> bool:
    raw = unicodedata.normalize("NFC", str(folded or "").lower())
    return bool(re.search(r"\b(?:project|dự án|du an)\b", raw))
_NON_PERSONAL_CONTEXT_WORDS = frozenset({
    "project", "room", "menu", "car", "sky", "weather", "server", "channel",
})
_NON_PERSONAL_CONTEXT_RE = re.compile(
    r"\b(?:dự\s+án|du\s+an|phòng|phong|xe|nhóm|nhom|máy\s+chủ|may\s+chu|"
    r"kênh|kenh|room|menu|car|sky|server|channel|team|company|cong\s+ty)\b",
    re.IGNORECASE,
)
_THIRD_PARTY_SUBJECT_RE = re.compile(
    r"\b(?:my|mine|cua minh|cua toi|cua tui)\s+"
    r"(?:friend|brother|sister|mother|father|mom|dad|parent|partner|wife|husband|"
    r"roommate|colleague|boss|teacher|anh|chi|em|ban|nguoi|me|bo|ba|"
    r"nana|owner|channel\s+owner|viewer|viewers|co\s+ay|anh\s+ay|"
    r"chi\s+ay|em\s+ay|ban\s+toi|ban\s+cua\s+toi|nguoi\s+khac|"
    r"(?:cua\s+)?(?:me|bo|ba|anh|chi|em|con|chau|vo|chong|"
    r"child|cousin|son|daughter|girlfriend|boyfriend|pet|dog|cat)\s+"
    r"(?:minh|toi|tui|likes?|thich|yeu|ten|song|o)\b|"
    r"\b(?:me|us|you)\s+project\b)",
    re.IGNORECASE,
)
_KNOWN_THIRD_PARTY_RE = re.compile(
    r"(?:^|\s)(?:ba|nana|owner|channel\s+owner|viewer|viewers|our|"
    r"friend|brother|sister|mother|father|mom|dad|parent|partner|wife|husband|"
    r"roommate|colleague|boss|teacher|team|company|group|client|customer|user|"
    r"someone|another|nhom|khach\s+hang|nguoi\s+dung|nguoi\s+khac|"
    r"cô\s+ấy|co\s+ay|anh\s+ấy|anh\s+ay|chị\s+ấy|chi\s+ay|"
    r"em\s+ấy|em\s+ay|bạn\s+tôi|ban\s+toi|"
    r"(?:cua\s+)?(?:me|bo|ba|anh|chi|em|con|chau|vo|chong|"
    r"child|cousin|son|daughter|girlfriend|boyfriend|pet|dog|cat)\s+"
    r"(?:minh|toi|tui|likes?|thich|yeu|ten|song|o)\b|"
    r"\b(?:me|us|you)\s+project\b)",
    re.IGNORECASE,
)
_SELF_PREDICATE_RE = re.compile(
    r"(?:\b(?:my|mine|i|me|minh|toi|tui)\b|\b(?:cua minh|cua toi|cua tui)\b)",
    re.IGNORECASE,
)
_QUERY_OTHER_SUBJECT_RE = re.compile(
    r"\b(?:nana|ba|owner|channel\s+owner|viewer|friend|brother|sister|mother|"
    r"father|mom|dad|parent|partner|wife|husband|roommate|colleague|boss|"
    r"teacher|his|her|their|our|ours|your|yours|team|company|nhom|"
    r"anh|chi|em|con|chau|vo|chong|child|cousin|son|daughter|"
    r"girlfriend|boyfriend|pet|dog|cat)\b|"
    r"\b(?:for|of)\s+(?:the\s+)?project\b|"
    r"\b(?:cua|of)\s+(?:me|anh|chi|em|nhom|team|company|cong\s+ty)\b|"
    r"\bmy\s+(?:friend|brother|sister|mother|father|team|company)\b",
    re.IGNORECASE,
)
_INSTRUCTION_LIKE_RE = re.compile(
    r"(?:ignore\s+previous|reveal\s+(?:private|secret)|disclose\s+secret|"
    r"call\s+tools?|system\s*[:=]|owner\s+chat|private\s+context|"
    r"must\s+never|do\s+not\s+reveal|bo\s+qua|bỏ\s+qua|"
    r"tiet\s+lo|tiết\s+lộ|quy\s+tat|quy\s+tắc|giu\s+bi\s+mat|giữ\s+bí\s+mật|"
    r"hay\s+bo|hãy\s+bỏ|khong\s+duoc|không\s+được|thong\s+tin\s+rieng|"
    r"thông\s+tin\s+riêng|prompt|instruction|"
    r"hãy\s+(?:trả\s+lời|nói|làm)|hay\s+(?:tra\s+loi|noi|lam)|"
    r"trả\s+lời|tra\s+loi|nói\s+rằng|noi\s+rang|"
    r"mã\s+bí\s+mật|ma\s+bi\s+mat|private\s+token|secret\s+token|"
    r"\b(?:token|secret|credential|password|mat\s+khau)\b|"
    r"đừng|dung\s+(?:nói|noi)|không\s+được|khong\s+duoc|"
    r"làm\s+theo|lam\s+theo|\b(?:answer|respond|reply|say|tell|mention)\b|"
    r"\bfollow\b|use\s+this|private\s+key|secret\s+key|\bkey\b|"
    r"\btool\b|\bowner\b|\bprompt\b|\binstruction\b|"
    r"\b(?:hãy|hay|nói|noi)\b|trả\s+lời|tra\s+loi|"
    r"tiết\s+lộ|tiet\s+lo)",
    re.IGNORECASE,
)
_NON_ASSERTIVE_RE = re.compile(
    r"(?:\b(?:might|may|maybe|perhaps|possibly|unknown|unsure|uncertain|"
    r"think|used\s+to|no\s+longer|never|not\s+sure|don't|dont|do\s+not|"
    r"hate|hates|dislike|dislikes|rarely|not)\b|"
    r"\b(?:co\s+the|co\s+le|co\s+the|hinh\s+nhu|khong\s+chac|"
    r"chua\s+chac|khong\s+ro|khong\s+con|khong\s+bao\s+gio|"
    r"khong\s+thich|khong\s+phai|không\s+thích|không\s+phải|"
    r"prefer\s+not)\b)",
    re.IGNORECASE,
)
_MULTI_CLAIM_RE = re.compile(
    r"(?:[;,|/+()\[\]{}:]|\s[-–—]\s|\.\s+|\n|\s+\b(?:and|va|và|nhung|nhưng|plus|also|cung|cùng|"
    r"or|hoac|hoặc|versus|vs|maybe|perhaps)\b)",
    re.IGNORECASE,
)


def _valid_actor_key(actor_key: str, platform: str) -> bool:
    prefix = f"{platform}:"
    if not platform or not actor_key.lower().startswith(prefix):
        return False
    suffix = actor_key[len(prefix):]
    return bool(suffix) and suffix == suffix.strip() and ":" not in suffix and not re.search(r"\s", suffix)


def _evaluation_time(now: float | None) -> float | None:
    try:
        value = time.time() if now is None else float(now)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) else None


def _normalize_fact_facet(value: Any) -> str | None:
    folded = _fold(value)
    if not folded:
        return None
    aliases = {
        "name": "name", "person_name": "name", "display_name": "name",
        "has_name": "name",
        "ten": "name", "color": "color", "colour": "color", "mau": "color",
        "favorite_color": "color", "color_preference": "color", "drink": "drink_preference",
        "beverage": "drink_preference", "drink_preference": "drink_preference",
        "favorite_drink": "drink_preference", "food": "food_preference",
        "food_preference": "food_preference", "favorite_food": "food_preference",
        "music": "music_preference", "music_preference": "music_preference",
        "birthday": "birthday", "birth": "birthday", "birth_date": "birthday",
        "date_of_birth": "birthday", "sinh_nhat": "birthday", "project_deadline": "project_deadline",
        "project_date": "project_deadline", "deadline": "project_deadline",
        "due_date": "project_deadline", "project_due_date": "project_deadline",
        "project_number": "project_number",
        "phone": "phone", "phone_number": "phone", "telephone": "phone",
        "residence": "residence", "residence_city": "residence", "city": "residence",
        "location": "residence", "lives_in": "residence", "preference": "preference",
        "like": "preference", "likes": "preference", "favorite": "preference",
    }
    return aliases.get(folded)


def _declared_facet_value(raw: Mapping[str, Any]) -> Any:
    for key in ("fact_key", "predicate", "facet"):
        value = raw.get(key)
        if value not in (None, ""):
            return value
    return None


def infer_public_query_facets(query: str) -> frozenset[str]:
    """Return every supported facet explicitly present in a query."""

    folded = _fold(query)
    if not folded:
        return frozenset()
    facets: set[str] = set()
    if re.search(r"\b(?:ten|name|named|called)\b", folded) or re.search(r"\bwho\s+am\s+i\b", folded):
        facets.add("name")
    if re.search(r"\b(?:where.*\b(?:live|living|stay)\b|\b(?:live|living|stay)\b.*\bwhere\b|o dau|dia chi|address|thanh pho|city|town|hometown|home|house|location|reside|residence|am i living|where am i)\b", folded):
        facets.add("residence")
    if re.search(r"\b(?:phone|telephone|mobile|sdt|so dien thoai|phone number|contact number)\b", folded):
        facets.add("phone")
    if re.search(r"\b(?:birthday|birthdate|birth date|date of birth|born|birth|sinh nhat|sinh ngay|ngay sinh)\b", folded):
        facets.add("birthday")
    if _contains_project_term(query) and re.search(r"\bnumber\b", folded):
        facets.add("project_number")
    if _contains_project_term(query) and re.search(r"\b(?:deadline|date|due|han|ngay)\b", folded):
        facets.add("project_deadline")
    if _contains_color_term(query):
        facets.add("color")
    if _contains_music_term(query):
        facets.add("music_preference")
    elif re.search(r"\b(?:song|listen)\b", folded) and re.search(
        r"\b(?:like|likes|love|enjoy|thich|yeu|favorite|prefer)\b", folded
    ):
        facets.add("music_preference")
    if _contains_food_term(query):
        facets.add("food_preference")
    if _contains_drink_term(query):
        facets.add("drink_preference")
    if re.search(r"\b(?:thich|like|likes|liked|love|loved|prefer|favorite|favourite|enjoy|so thich)\b", folded):
        facets.add("preference")
    if re.search(r"\b(?:did|what)\s+i\s+(?:say|said|tell|told)\b", folded):
        facets.add("preference")
    if len(facets) > 1 and "preference" in facets:
        facets.discard("preference")
    if len(facets) > 1:
        explicit_multi = bool(re.search(r"(?:\band\b|\bvà\b|\bva\b|&|,)", query.lower()))
        if not explicit_multi:
            priority = (
                "name", "residence", "phone", "birthday", "project_deadline",
                "project_number", "color", "music_preference", "food_preference",
                "drink_preference", "preference",
            )
            facets = {next(item for item in priority if item in facets)}
    return frozenset(facets)


def infer_public_query_facet(query: str) -> str | None:
    """Return one facet only when the query is not multi-facet."""

    facets = infer_public_query_facets(query)
    return next(iter(facets)) if len(facets) == 1 else None


def query_targets_public_viewer(query: str, *, facet: str | None = None) -> bool:
    """Return whether a public query asks for the current viewer's fact."""

    folded = _fold(query)
    if _QUERY_OTHER_SUBJECT_RE.search(folded) or re.search(
        r"\b(?:cua ban|cua nana|cua cua ban|cua nguoi khac)\b", folded
    ):
        return False
    if re.search(r"\b(?:my|mine|i|me|minh|toi|tui)\b", folded) or re.search(
        r"\b(?:cua minh|cua toi|cua tui)\b", folded
    ):
        return True
    # The bounded project/date contract is inherently viewer-scoped in this
    # lane even when the short question omits the pronoun.
    return facet in {"project_deadline", "project_number"}


def infer_public_candidate_facet(text: str, *, declared: Any = None) -> str | None:
    """Infer a candidate facet; unknown text is rejected for personal recall."""

    declared_facet = _normalize_fact_facet(declared)
    if declared not in (None, "",) and declared_facet is None:
        return None
    folded = _fold(text)
    inferred: str | None = None
    if re.search(r"\b(?:ten|name|named|called)\b", folded):
        inferred = "name"
    elif re.search(r"\b(?:phone|telephone|mobile|sdt|so dien thoai|phone number|contact number)\b", folded):
        inferred = "phone"
    elif re.search(r"\b(?:i live|live in|i am living|living in|live at|i stay|stay in|reside|hometown|home|house|town|location|song o|song tai|o tai|dia chi|address)\b", folded) or re.search(r"\b(?:sống ở|song o)\b", text.lower()):
        inferred = "residence"
    elif _contains_music_term(text):
        inferred = "music_preference"
    elif re.search(r"\b(?:birthday|birthdate|birth date|date of birth|born|birth|sinh nhat|sinh ngay|ngay sinh)\b", folded):
        inferred = "birthday"
    elif _contains_project_term(text) and re.search(r"\b(?:deadline|date|due|han|ngay)\b", folded):
        inferred = "project_deadline"
    elif _contains_project_term(text) and re.search(r"\bnumber\b", folded):
        inferred = "project_number"
    elif _contains_food_term(text):
        inferred = "food_preference"
    elif _contains_drink_term(text):
        inferred = "drink_preference"
    elif _contains_color_term(text):
        inferred = "color"
    elif (
        any(word in folded.split() for word in ("blue", "red", "green", "yellow", "purple", "black", "white", "xanh", "do", "vang", "tim", "den", "trang"))
        and not (_contains_drink_term(text) or _contains_food_term(text) or _contains_music_term(text))
        and any(word in folded for word in _PREFERENCE_WORDS)
    ):
        inferred = "color"
    elif any(word in folded for word in _PREFERENCE_WORDS):
        inferred = "preference"
    if declared_facet and inferred and declared_facet != inferred:
        # A generic preference verb ("like", "enjoy", ...) does not
        # contradict a more specific typed predicate supplied by the adapter.
        if inferred == "preference" and declared_facet in _PREFERENCE_FACETS:
            return declared_facet
        return None
    return declared_facet or inferred


def candidate_has_personal_predicate(text: str, *, facet: str | None, declared: Any = None) -> bool:
    """Reject a domain-labelled fact that is not owned by the viewer."""

    folded = _fold(text)
    if _THIRD_PARTY_SUBJECT_RE.search(folded) or _KNOWN_THIRD_PARTY_RE.search(folded):
        return False
    nonpersonal_context = _NON_PERSONAL_CONTEXT_RE.search(folded)
    if nonpersonal_context or any(word in folded.split() for word in _NON_PERSONAL_CONTEXT_WORDS):
        # Project facts are allowed only for their own deadline/number facets.
        if (
            ("project" in folded.split() or re.search(r"\b(?:du\s+an|dự\s+án)\b", folded))
            and facet in {"project_deadline", "project_number"}
            and not re.search(r"\b(?:room|phong|phòng|team|nhom|nhóm|company|cong\s+ty|menu|car|xe|owner|channel|kenh|kênh)\b", folded)
        ):
            pass
        else:
            return False
    if re.search(r"\b(?:color|colour)\s+of\s+my\s+car\b", folded):
        return False
    if re.search(r"\b(?:the\s+)?name\s+is\b", folded) and not re.search(r"\b(?:my|i|me|minh|toi)\b", folded):
        return False
    has_self = bool(_SELF_PREDICATE_RE.search(folded))
    if has_self:
        return True
    if facet in {"project_deadline", "project_number"} and (
        "project" in folded.split() or re.search(r"\b(?:du\s+an|dự\s+án)\b", folded)
    ):
        # A short project fact may omit the pronoun, but only after all
        # third-party/non-personal context checks above have passed.
        return True
    if declared in (None, ""):
        # Free-form text without an explicit first-person owner is not a
        # viewer fact, even when it contains a facet word such as "favorite".
        return False
    return _strict_structured_value(text, facet)


def _strict_structured_value(text: str, facet: str | None) -> bool:
    """Validate a value-only record admitted by a trusted typed predicate."""

    raw = str(text or "").strip()
    if not raw or len(raw) > 120 or _INSTRUCTION_LIKE_RE.search(raw) or _NON_ASSERTIVE_RE.search(raw):
        return False
    if _MULTI_CLAIM_RE.search(raw):
        return False
    if _SELF_PREDICATE_RE.search(_fold(raw)) or _THIRD_PARTY_SUBJECT_RE.search(_fold(raw)) or _KNOWN_THIRD_PARTY_RE.search(_fold(raw)):
        return False
    value_labels = _FACT_VALUE_LABELS
    if facet == "name":
        value_labels = value_labels - {"minh", "toi", "tui", "i", "me"}
    if _tokens(raw) & value_labels:
        # A typed value-only record must contain the value, not another
        # sentence such as "the color is blue" that could carry hidden text.
        return False
    if facet == "phone":
        return bool(re.fullmatch(r"\+?[0-9][0-9 .()_-]{3,30}", raw))
    if facet == "color":
        return bool(re.fullmatch(r"[A-Za-zÀ-ỹĐđ -]{2,40}", raw))
    if facet in {"birthday", "project_deadline"}:
        return bool(re.fullmatch(r"[A-Za-zÀ-ỹĐđ0-9 /_.-]{2,60}", raw))
    if facet in {"name", "residence", "project_number", "preference", "drink_preference", "food_preference", "music_preference"}:
        return bool(re.fullmatch(r"[A-Za-zÀ-ỹĐđ0-9][A-Za-zÀ-ỹĐđ0-9 _.'-]{1,80}", raw))
    return False


def _facets_compatible(query_facet: str | None, candidate_facet: str | None) -> bool:
    if query_facet is None:
        return True
    if candidate_facet is None:
        return False
    if query_facet == "preference":
        return candidate_facet in _PREFERENCE_FACETS
    return candidate_facet == query_facet


def _canonical_lane(value: Any) -> str:
    raw = str(value or "").strip().lower().replace("-", "_")
    return {
        "public": "public_stage",
        "public_viewer": "public_stage",
        "public_chat": "public_stage",
    }.get(raw, raw)


def _value(raw: Any, *names: str, default: Any = None) -> Any:
    if isinstance(raw, Mapping):
        for name in names:
            if name in raw:
                return raw[name]
        return default
    for name in names:
        try:
            value = getattr(raw, name)
        except AttributeError:
            continue
        if value is not None:
            return value
    return default


def _nested_scope(raw: Mapping[str, Any]) -> Mapping[str, Any]:
    scope = raw.get("scope")
    return scope if isinstance(scope, Mapping) else {}


def _scope_mapping(scope: Any) -> dict[str, Any]:
    if isinstance(scope, Mapping):
        return dict(scope)
    if scope is None:
        return {}
    platform = str(getattr(scope, "platform", "") or "").strip().lower()
    actor = str(
        getattr(scope, "durable_viewer_key", "")
        or getattr(scope, "actor_key", "")
        or ""
    ).strip()
    room = str(getattr(scope, "room_id", "") or "").strip()
    consent = getattr(scope, "consent", None)
    return {
        "platform": platform,
        "actor_key": actor,
        "room_id": room,
        "consent": consent,
    }


def _record_platform(raw: Mapping[str, Any]) -> str:
    platform = str(raw.get("platform") or "").strip().lower()
    if platform:
        return platform
    scope = _nested_scope(raw)
    return str(scope.get("platform") or scope.get("provider") or "").strip().lower()


def _record_actor(raw: Mapping[str, Any]) -> str:
    scope = _nested_scope(raw)
    return str(raw.get("actor_key") or scope.get("actor_key") or "").strip()


def _record_room(raw: Mapping[str, Any]) -> str:
    scope = _nested_scope(raw)
    return str(
        raw.get("room_id")
        or raw.get("room")
        or raw.get("channel_id")
        or scope.get("room_id")
        or scope.get("room_key")
        or scope.get("channel_id")
        or ""
    ).strip()


def _record_consent(raw: Mapping[str, Any]) -> bool:
    provenance = raw.get("provenance")
    if isinstance(provenance, Mapping) and "consent" in provenance:
        return provenance.get("consent") is True
    scope = _nested_scope(raw)
    if "consent" in raw:
        return raw.get("consent") is True
    if "consent_recorded" in raw:
        return raw.get("consent_recorded") is True
    if "consent" in scope:
        return scope.get("consent") is True
    return False


def _record_verified(raw: Mapping[str, Any]) -> bool:
    provenance = raw.get("provenance")
    if isinstance(provenance, Mapping) and "verified" in provenance:
        return provenance.get("verified") is True
    if "verified" in raw:
        return raw.get("verified") is True
    scope = _nested_scope(raw)
    if "verified" in scope:
        return scope.get("verified") is True
    source = _fold(raw.get("source"))
    return source in {"public_verified", "public_explicit", "viewer_verified", "verified_public"}


def _record_expiry(raw: Mapping[str, Any]) -> float | None:
    scope = _nested_scope(raw)
    for value in (
        raw.get("expires_at"), raw.get("retention_until"), raw.get("valid_until"),
        raw.get("ttl"), scope.get("expires_at"), scope.get("retention_until"),
    ):
        if value in (None, ""):
            continue
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        return parsed if math.isfinite(parsed) else None
    return None


def _safe_text(raw: Mapping[str, Any]) -> str:
    text = str(raw.get("text") or "").strip()
    text = _INTERNAL_FIELD_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    return redact_history_text(text)[:400]


def _looks_like_question(text: str, raw: Mapping[str, Any]) -> bool:
    folded = _fold(text)
    return (
        text.rstrip().endswith("?")
        or str(raw.get("type") or "").strip().lower() in _QUESTION_TYPES
        or bool(re.search(r"(?:^|\s)(?:gi|nao|ai|what|who|why|when|where|how)\b", folded))
    )


def _safe_candidate(
    raw: Any,
    *,
    scope: Mapping[str, Any] | None,
    now: float,
) -> tuple[dict[str, Any] | None, str]:
    if not isinstance(raw, Mapping):
        return None, "record_not_object"
    raw_text = str(raw.get("text") or "").strip()
    text = _safe_text(raw)
    if not text:
        return None, "empty_text"
    if (
        _INSTRUCTION_LIKE_RE.search(text)
        or _MULTI_CLAIM_RE.search(text)
        or _NON_ASSERTIVE_RE.search(text)
    ):
        return None, "instruction_like_or_multi_claim"
    lane = _canonical_lane(raw.get("lane"))
    # Public filters are strict, but the canonical boundary accepts the
    # project record shape used by both public_cross_session_memory and the
    # hot path. A synthetic test candidate may omit a redundant lane/platform
    # only when it has a namespaced actor and explicit scope; runtime records
    # still need all fields before they reach this function.
    if lane not in {"public_stage"}:
        return None, "non_public_lane"
    platform = _record_platform(raw)
    actor_key = _record_actor(raw)
    room_id = _record_room(raw)
    source_event_id = str(raw.get("source_event_id") or "").strip()
    if not platform:
        return None, "platform_missing"
    if not actor_key or ":" not in actor_key:
        return None, "actor_missing_or_unnamespaced"
    if not _valid_actor_key(actor_key, platform):
        return None, "actor_platform_mismatch"
    if not room_id:
        return None, "room_missing"
    if not source_event_id:
        return None, "provenance_missing"

    scope_map = scope or {}
    expected_platform = str(scope_map.get("platform") or "").strip().lower()
    expected_actor = str(scope_map.get("actor_key") or "").strip()
    expected_room = str(scope_map.get("room_id") or "").strip()
    # A public request is never allowed to act as a wildcard.  All four
    # canonical scope fields must be present and consent must be the literal
    # boolean True before any record can be considered.
    if (
        not expected_platform
        or not expected_actor
        or not expected_room
        or scope_map.get("consent") is not True
    ):
        return None, "request_scope_incomplete"
    if not _valid_actor_key(expected_actor, expected_platform):
        return None, "request_actor_platform_mismatch"
    if expected_platform and platform != expected_platform:
        return None, "platform_mismatch"
    if expected_actor and actor_key != expected_actor:
        return None, "actor_mismatch"
    if expected_room and room_id != expected_room:
        return None, "room_mismatch"
    if scope_map.get("consent") is False:
        return None, "request_consent_missing"
    if not _record_verified(raw):
        return None, "unverified"
    if not _record_consent(raw):
        return None, "consent_missing"
    expiry = _record_expiry(raw)
    if expiry is None:
        return None, "ttl_missing_or_invalid"
    if expiry <= now:
        return None, "expired"
    source = _fold(raw.get("source"))
    if source in _GENERATED_SOURCES or any(marker in source for marker in ("model", "generated", "assistant", "llm")):
        return None, "generated_source"
    if bool(raw.get("temporary")) or str(raw.get("decay_policy") or "").lower() == "session":
        return None, "temporary_record"
    if _looks_like_question(raw_text, raw):
        return None, "question_record"
    if contains_history_secret(text) or contains_history_secret(str(raw.get("evidence") or "")):
        return None, "secret_material"
    try:
        confidence = float(raw.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        return None, "confidence_invalid"
    if not math.isfinite(confidence) or confidence < 0.7:
        return None, "confidence_low"
    try:
        score = float(raw.get("score", raw.get("semantic_score", 0.0)) or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    if not math.isfinite(score):
        score = 0.0
    facet_value = _declared_facet_value(raw)
    candidate_facet = infer_public_candidate_facet(text, declared=facet_value)
    if facet_value not in (None, "") and candidate_facet is None:
        return None, "facet_invalid_or_conflicting"
    if candidate_facet and not _atomic_fact_shape(text, candidate_facet):
        return None, "non_atomic_fact_value"
    return {
        "id": str(raw.get("id") or "").strip(),
        "text": text,
        "source_event_id": source_event_id,
        "source": str(raw.get("source") or "").strip(),
        "lane": lane,
        "platform": platform,
        "actor_key": actor_key,
        "room_id": room_id,
        "confidence": confidence,
        "score": max(0.0, min(1.0, score)),
        "expires_at": expiry,
        "fact_key": candidate_facet or "",
        # Only a test caller that explicitly opts into the injected-semantic
        # boundary may use this marker. Production public records never get a
        # semantic pass from their persisted raw score.
        "semantic_test_only": raw.get("semantic_test_only") is True,
    }, ""


@dataclass(frozen=True)
class PublicRecallDecision:
    """Typed decision shared by sync and stream public paths."""

    direct_fact_question: bool
    candidate_present: bool
    confidence: float
    score: float
    safe_answer: str
    uncertainty_fallback: str
    reason: str
    evidence_id: str = ""
    evidence_text: str = ""
    prompt: str = ""
    use_deterministic: bool = False

    @property
    def should_ground(self) -> bool:
        return self.candidate_present

    def to_dict(self) -> dict[str, Any]:
        return {
            "should_ground": self.should_ground,
            "direct_fact_question": self.direct_fact_question,
            "candidate_present": self.candidate_present,
            "confidence": round(self.confidence, 4),
            "score": round(self.score, 4),
            "safe_answer": self.safe_answer,
            "uncertainty_fallback": self.uncertainty_fallback,
            "reason": self.reason,
            "evidence_id": self.evidence_id,
            "evidence_text": self.evidence_text,
            "prompt": self.prompt,
            "use_deterministic": self.use_deterministic,
        }

    # Compatibility for existing hot-path callers that treated the old
    # provisional object as a dict.
    def get(self, key: str, default: Any = None) -> Any:
        return self.to_dict().get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]


def is_direct_fact_question(
    query: str,
    evidence_text: str,
    *,
    query_facet: str | None = None,
    candidate_facet: str | None = None,
) -> bool:
    query_tokens = _tokens(query)
    evidence_tokens = _tokens(evidence_text)
    folded_query = _fold(query)
    has_question = bool(
        "?" in str(query or "")
        or re.search(r"(?:^|\s)(?:gi|nao|bao nhieu|o dau|khi nao|ai|what|which|who|why|when|where|how|tell me|remind me)\b", folded_query)
    )
    if not has_question:
        return False
    overlap = query_tokens & evidence_tokens
    if query_facet is not None and candidate_facet is not None and _facets_compatible(query_facet, candidate_facet):
        return True
    discriminative = query_tokens - _QUERY_GENERIC_TOKENS
    anchor_overlap = discriminative & evidence_tokens
    if discriminative and not anchor_overlap:
        return False
    if discriminative and len(anchor_overlap) / len(discriminative) < 1.0:
        return False
    if discriminative and anchor_overlap:
        # All domain anchors are present; generic question/subject words do
        # not need to repeat in the fact for a deterministic answer.
        return True
    # A direct relation query such as "minh thích gì?" shares the subject
    # and relation. A semantic candidate may carry score>=0.75 separately.
    return len(overlap) >= 2 or (len(overlap) == 1 and len(query_tokens) <= 4)


def is_public_memory_recall_question(query: str) -> bool:
    """Recognize a narrow public request to recall the speaker's prior fact.

    Ordinary public questions stay outside this detector. It only prevents an
    explicitly personal recall question with no matching evidence from being
    sent to an unconstrained model that may invent a replacement fact.
    """

    folded = _fold(query)
    if not folded:
        return False
    has_question = bool(
        "?" in str(query or "")
        or re.search(
            r"(?:^|\s)(?:gi|nao|bao nhieu|o dau|khi nao|ai|what|which|who|when|where|how|how old|tell me|remind me|show me)\b",
            folded,
        )
    )
    if not has_question:
        return False
    query_facet = infer_public_query_facet(folded)
    if not query_targets_public_viewer(folded, facet=query_facet):
        return False
    if _is_non_fact_intent(folded):
        return False
    markers = (
        "con nho", "ban nho", "nana nho", "ban con nho",
        "minh da noi", "toi da noi",
        "minh da ke", "toi da ke", "minh tung", "toi tung", "luc truoc",
        "lan truoc", "do you remember", "did i tell", "what did i tell",
        "what do you remember", "my earlier", "my previous",
    )
    if any(marker in folded for marker in markers):
        return True

    # Recognize only personal *fact recall*, not advice/opinion/current
    # questions. This keeps zero-evidence recall fail-closed while ordinary
    # public conversation remains model-eligible.
    personal = bool(re.search(r"(?:^|\s)(?:minh|toi|tui|my|mine|i|me)\b", folded)) or any(
        phrase in folded for phrase in ("cua minh", "cua toi", "cua tui")
    )
    implicit_project = query_facet in {"project_deadline", "project_number"}
    if not personal and not implicit_project:
        return False
    advice_or_current = (
        r"\b(?:nen|should|recommend|goi y|tu van|advice|opinion|nghi|think|"
        r"hom nay|today|bay gio|now|luc nay|weather|thoi tiet)\b"
    )
    if re.search(advice_or_current, folded):
        return False
    preference = bool(re.search(
        r"\b(?:thich|yeu|like|likes|liked|love|loved|prefer|favorite|favourite|enjoy)\b",
        folded,
    ))
    identity_or_date = bool(re.search(
        r"\b(?:ten|name|birthday|birth|birthdate|born|sinh nhat|sinh ngay|ngay sinh|mau|color|colour|"
        r"food|drink|project|deadline|date|phone|telephone|mobile|sdt|number|"
        r"live|living|stay|reside|residence|address|city|town|hometown|home|house|location|age|old|hobby|interest|fun|"
        r"contact|so thich|favorite)\b",
        folded,
    ))
    return preference or identity_or_date or query_facet is not None


def _candidate_relevant(query: str, candidate: Mapping[str, Any]) -> bool:
    return _relevance_strength(query, candidate) > 0.0


def _relevance_strength(
    query: str,
    candidate: Mapping[str, Any],
    *,
    query_facet: str | None = None,
    candidate_facet: str | None = None,
) -> float:
    query_tokens = _tokens(query)
    text_tokens = _tokens(candidate.get("text"))
    if query_facet is not None and candidate_facet is not None and _facets_compatible(query_facet, candidate_facet):
        # A typed facet is stronger evidence than raw lexical overlap. This is
        # what lets "what is my favorite color?" match a typed "i like blue"
        # record while still rejecting a different facet.
        return 1.0
    discriminative = query_tokens - _QUERY_GENERIC_TOKENS
    if discriminative:
        # Generic subject/relation overlap is not enough to ground a fact from
        # another domain. A domain-bearing query term must occur in the fact.
        overlap = discriminative & text_tokens
        return len(overlap) / len(discriminative) if overlap else 0.0
    if len(query_tokens & text_tokens) >= 2:
        return 1.0
    # A persisted/raw score is not trusted semantic evidence. The only score
    # path is an explicit test-only marker handled by the opt-in assembly
    # boundary below; the production hot path leaves it disabled.
    if candidate.get("_allow_injected_semantic") is True and candidate.get("semantic_test_only") is True:
        score = float(candidate.get("score", 0.0) or 0.0)
        return 0.5 if math.isfinite(score) and score >= 0.75 else 0.0
    return 0.0


_FACT_VALUE_LABELS = frozenset({
    "my", "mine", "i", "me", "minh", "toi", "tui", "cua", "la", "is", "am",
    "are", "was", "were", "be", "the", "a", "an", "of", "in", "at", "on",
    "ten", "name", "named", "called", "color", "colour", "hue", "mau", "sac",
    "food", "meal", "dessert", "snack", "drink", "beverage",
    "music", "song", "nhac", "birthday", "birth", "birthdate", "date", "born",
    "sinh", "nhat", "ngay", "project", "deadline", "due", "number", "phone",
    "telephone", "mobile", "sdt", "live", "living", "reside", "residence",
    "address", "city", "hometown", "favorite", "favourite", "like", "likes",
    "liked", "love", "loved", "prefer", "enjoy", "enjoys", "thich", "yeu",
    "gi", "nao", "bao", "nhieu", "khi", "ai", "o", "dau", "day", "days",
})
_QUERY_VALUE_LABELS = _FACT_VALUE_LABELS | frozenset({
    "what", "which", "who", "why", "when", "where", "how", "do", "does", "did",
    "say", "said", "tell", "told", "about", "ve", "về", "think", "nghi",
    "should", "today", "hom", "nay", "many", "much", "old", "am", "are",
    "an", "uong", "co", "khong", "phai", "best", "most", "kind", "full",
    "real", "exactly", "really", "to", "listen", "eat", "eating", "consume",
})
_LONG_NUMERIC_TOKEN_RE = re.compile(r"\b\d{4,}\b")
_SENTINEL_LIKE_RE = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")


def _candidate_value_signature(candidate: Mapping[str, Any]) -> str:
    facet = str(candidate.get("fact_key") or "")
    text = str(candidate.get("text") or "")
    if facet == "phone":
        digits = re.findall(r"\d[\d\s().+-]{1,}\d", text)
        if digits:
            return re.sub(r"\D", "", digits[0])
    tokens = _canonical_value_tokens(text, facet)
    return "|".join(sorted(tokens))


def _canonical_value_tokens(text: str, facet: str | None) -> set[str]:
    folded = _fold(text)
    if facet == "drink_preference":
        folded = re.sub(r"\bca\s+phe\b", "coffee", folded)
        folded = re.sub(r"\btra\b", "tea", folded)
    elif facet == "food_preference":
        folded = re.sub(r"\bpho\b", "pho", folded)
    labels = _FACT_VALUE_LABELS
    if facet == "name":
        # "Minh" can be the actual name value as well as a Vietnamese
        # first-person pronoun; preserve it for a typed name fact.
        labels = labels - {"minh", "toi", "tui", "i", "me"}
    return _tokens(folded) - labels


def _query_value_signature(query: str, facet: str | None) -> set[str]:
    """Extract an explicitly requested value for yes/no/value-specific asks."""

    if not facet:
        return set()
    text = str(query or "")
    if re.search(r"\b(?:or|hoac|hoặc|versus|vs)\b", _fold(text)):
        return set()
    if facet == "phone":
        match = re.search(r"\d[\d .()_-]{1,}\d", text)
        return {re.sub(r"\D", "", match.group(0))} if match else set()
    folded = _fold(text)
    if facet == "name":
        name_match = re.search(
            r"\b(?:name|ten)\s+(?:(?:minh|toi|tui)\s+)?(?:is|la)\s+([\wÀ-ỹĐđ-]+)\b",
            folded,
        )
        if name_match and name_match.group(1) not in {"gi", "gì", "nao", "nào"}:
            return {name_match.group(1)}
        direct_match = re.search(r"\b(?:name|ten)\s+([\wÀ-ỹĐđ-]+)\b", folded)
        if direct_match:
            value = direct_match.group(1)
            remainder = folded[direct_match.end():].lstrip()
            if value not in {"minh", "toi", "tui"} or not re.match(r"(?:la|is|gi|gì|nao|nào)\b", remainder):
                return {value}
    if facet == "drink_preference":
        folded = re.sub(r"\bca\s+phe\b", "coffee", folded)
        folded = re.sub(r"\btra\b", "tea", folded)
    elif facet == "food_preference":
        folded = re.sub(r"\bpho\b", "pho", folded)
    tokens = _tokens(folded) - _QUERY_VALUE_LABELS
    # The generic value query has no salient value; domain labels and grammar
    # are stripped above, leaving only a requested answer such as coffee/red.
    return tokens


def _is_non_fact_intent(query: str) -> bool:
    folded = _fold(query)
    if re.search(r"\b(?:why|how|think|nghi|about|ve|về|feel|feeling|opinion)\b", folded):
        if re.search(r"\bhow\s+(?:old|many|much)\b", folded):
            return False
        return True
    if re.search(r"\bwhat\s+do\s+i\s+like\s+(?:in|within)\b", folded):
        return True
    return False


def _query_has_alternatives(query: str) -> bool:
    return bool(re.search(r"\b(?:or|hoac|hoặc|versus|vs)\b", _fold(query)))


def _atomic_fact_shape(text: str, facet: str | None) -> bool:
    """Reject a record carrying unlabeled extra payload beyond one fact."""

    if not facet:
        return False
    if _SENTINEL_LIKE_RE.search(text) or re.search(r"\bsentinel\b", text, re.IGNORECASE):
        return False
    if re.search(r"\b\d+\b", text) and facet not in {"phone", "project_number", "birthday", "project_deadline"}:
        return False
    signature = _candidate_value_signature({"text": text, "fact_key": facet})
    if not signature:
        return False
    values = signature.split("|")
    # A bounded fact value may contain a few words (for example, New York),
    # but a long tail is more likely an unlabeled payload or another claim.
    if len(values) > 5:
        return False
    if facet == "phone":
        match = re.search(r"\d[\d .()_-]{1,}\d", text)
        if match and re.search(r"[A-Za-zÀ-ỹĐđ]", text[match.end():]):
            return False
    return True


def _bounded_safe_answer(text: str) -> str:
    safe = _INTERNAL_FIELD_RE.sub("", str(text or ""))
    safe = re.split(r"\n|(?<=[.!?])\s+", safe, maxsplit=1)[0]
    return re.sub(r"\s+", " ", safe).strip()[:240]


def get_safe_public_answer(
    query: str,
    evidence: Mapping[str, Any],
    *,
    query_facet: str | None = None,
    candidate_facet: str | None = None,
) -> str | None:
    if not isinstance(evidence, Mapping):
        return None
    try:
        confidence = float(evidence.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        return None
    text = _bounded_safe_answer(evidence.get("text", ""))
    if confidence < 0.90 or not text or not is_direct_fact_question(
        query, text, query_facet=query_facet, candidate_facet=candidate_facet
    ):
        return None
    return text


def _uncertainty_fallback() -> str:
    return "Mình chưa có thông tin chắc chắn về điều đó."


def assemble_public_grounding_prompt(
    query: str,
    public_evidence: Sequence[Mapping[str, Any]],
    *,
    scope_actor: str | None = None,
    scope_room: str | None = None,
    scope_platform: str | None = None,
    scope: Mapping[str, Any] | None = None,
    now: float | None = None,
    stream: bool = False,
    allow_injected_semantic: bool = False,
) -> dict[str, Any]:
    """Filter, relevance-gate, and decide public grounding."""

    current_time = _evaluation_time(now)
    if current_time is None:
        fallback = _uncertainty_fallback()
        decision = (
            PublicRecallDecision(
                direct_fact_question=True,
                candidate_present=False,
                confidence=0.0,
                score=0.0,
                safe_answer=fallback,
                uncertainty_fallback=fallback,
                reason="invalid_evaluation_time",
                use_deterministic=True,
            )
            if is_public_memory_recall_question(query)
            else None
        )
        return {"prompt": "", "decision": decision, "filtered_count": 0, "status": "invalid_evaluation_time"}
    scope_map = dict(scope or {})
    if scope_actor is not None:
        scope_map["actor_key"] = scope_actor
    if scope_room is not None:
        scope_map["room_id"] = scope_room
    if scope_platform is not None:
        scope_map["platform"] = scope_platform

    safe_candidates: list[dict[str, Any]] = []
    rejected = 0
    eligible_before_relevance = 0
    query_facets = infer_public_query_facets(query)
    query_facet = next(iter(query_facets)) if len(query_facets) == 1 else None
    personal_fact_query = is_public_memory_recall_question(query)
    viewer_query = query_targets_public_viewer(query, facet=query_facet)
    requested_values = _query_value_signature(query, query_facet)
    if _is_non_fact_intent(query):
        return {
            "prompt": "",
            "decision": None,
            "filtered_count": 0,
            "status": "non_fact_intent",
        }
    if not personal_fact_query or not viewer_query:
        # Public memory is never incidental context. Ordinary, opinion,
        # advice, open, and unknown questions remain model-eligible but receive
        # no public-memory candidate or prompt block.
        return {
            "prompt": "",
            "decision": None,
            "filtered_count": 0,
            "status": "no_relevant_evidence" if public_evidence else "no_eligible_evidence",
        }
    if _query_has_alternatives(query):
        fallback = _uncertainty_fallback()
        decision = PublicRecallDecision(
            direct_fact_question=True,
            candidate_present=False,
            confidence=0.0,
            score=0.0,
            safe_answer=fallback,
            uncertainty_fallback=fallback,
            reason="ambiguous_query_values",
            use_deterministic=True,
        )
        return {
            "prompt": "",
            "decision": decision,
            "filtered_count": 0,
            "status": "ambiguous_query_values",
        }
    if len(query_facets) > 1 and viewer_query and personal_fact_query:
        fallback = _uncertainty_fallback()
        decision = PublicRecallDecision(
            direct_fact_question=True,
            candidate_present=False,
            confidence=0.0,
            score=0.0,
            safe_answer=fallback,
            uncertainty_fallback=fallback,
            reason="ambiguous_query_facets",
            use_deterministic=True,
        )
        return {
            "prompt": "",
            "decision": decision,
            "filtered_count": 0,
            "status": "ambiguous_query_facets",
        }
    for raw in public_evidence or ():
        normalized, reason = _safe_candidate(raw, scope=scope_map, now=current_time)
        if normalized is None:
            rejected += 1
            continue
        eligible_before_relevance += 1
        candidate_facet = normalized.get("fact_key") or None
        if query_facet is not None and not personal_fact_query:
            # A facet word in an opinion/advice/current question is not a
            # memory request. Keep the model eligible, but never inject or
            # deterministically return a viewer record.
            rejected += 1
            continue
        facet_mismatch = not _facets_compatible(query_facet, candidate_facet)
        unsupported_personal_facet = personal_fact_query and (
            query_facet is None or candidate_facet is None
        )
        if query_facet is not None and not viewer_query:
            # Do not ground a viewer record when the question addresses Nana
            # or an unrelated public subject.
            rejected += 1
            continue
        if facet_mismatch or unsupported_personal_facet:
            rejected += 1
            continue
        declared_facet = _declared_facet_value(raw) if isinstance(raw, Mapping) else None
        if (
            (query_facet is not None or personal_fact_query)
            and not candidate_has_personal_predicate(
                normalized["text"], facet=candidate_facet, declared=declared_facet
            )
        ):
            rejected += 1
            continue
        if (query_facet is not None or personal_fact_query) and candidate_facet:
            if not _candidate_value_signature(normalized):
                rejected += 1
                continue
        if requested_values:
            candidate_values = set(filter(None, _candidate_value_signature(normalized).split("|")))
            if not requested_values.issubset(candidate_values):
                rejected += 1
                continue
        if allow_injected_semantic:
            normalized = dict(normalized, _allow_injected_semantic=True)
        relevance = _relevance_strength(
            query,
            normalized,
            query_facet=query_facet,
            candidate_facet=candidate_facet,
        )
        if relevance <= 0.0:
            rejected += 1
            continue
        safe_candidates.append(dict(normalized, relevance=relevance))

    if not safe_candidates:
        decision = None
        if is_public_memory_recall_question(query):
            fallback = _uncertainty_fallback()
            decision = PublicRecallDecision(
                direct_fact_question=True,
                candidate_present=False,
                confidence=0.0,
                score=0.0,
                safe_answer=fallback,
                uncertainty_fallback=fallback,
                reason="no_matching_public_evidence",
                use_deterministic=True,
            )
        return {
            "prompt": "",
            "decision": decision,
            "filtered_count": rejected,
            "status": (
                "memory_recall_without_evidence"
                if decision is not None
                else ("no_relevant_evidence" if eligible_before_relevance else "no_eligible_evidence")
            ),
        }

    query_tokens = _tokens(query)
    discriminative = query_tokens - _QUERY_GENERIC_TOKENS
    value_groups: dict[str, set[str]] = {}
    for item in safe_candidates:
        signature = _candidate_value_signature(item)
        group = query_facet or "preference"
        value_groups.setdefault(group, set()).add(signature)
    if any(len(values) > 1 for values in value_groups.values()):
        fallback = _uncertainty_fallback()
        decision = PublicRecallDecision(
            direct_fact_question=True,
            candidate_present=False,
            confidence=0.0,
            score=0.0,
            safe_answer=fallback,
            uncertainty_fallback=fallback,
            reason="ambiguous_public_evidence",
            use_deterministic=True,
        )
        return {
            "prompt": "",
            "decision": decision,
            "filtered_count": rejected,
            "status": "ambiguous_evidence",
        }
    safe_candidates.sort(
        key=lambda item: (-float(item["relevance"]), str(item["id"]))
    )
    max_relevance = float(safe_candidates[0]["relevance"])
    top_candidates = [
        item for item in safe_candidates
        if abs(float(item["relevance"]) - max_relevance) < 1e-9
    ]
    # A raw persisted score is not semantic proof. Resolve by lexical/domain
    # relevance first; if more than one distinct fact remains plausible, fail
    # closed instead of letting score/confidence/id choose a value.
    distinct_top = {_fold(item["text"]) for item in top_candidates}
    second_relevance = max(
        (float(item["relevance"]) for item in safe_candidates[1:]
         if _fold(item["text"]) not in distinct_top),
        default=None,
    )
    if len(distinct_top) > 1 or (
        second_relevance is not None
        and max_relevance - second_relevance <= 0.25
    ):
        fallback = _uncertainty_fallback()
        decision = PublicRecallDecision(
            direct_fact_question=True,
            candidate_present=False,
            confidence=0.0,
            score=0.0,
            safe_answer=fallback,
            uncertainty_fallback=fallback,
            reason="ambiguous_public_evidence",
            use_deterministic=True,
        )
        return {
            "prompt": "",
            "decision": decision,
            "filtered_count": rejected,
            "status": "ambiguous_evidence",
        }
    best = top_candidates[0]
    direct = is_direct_fact_question(
        query,
        best["text"],
        query_facet=query_facet,
        candidate_facet=best.get("fact_key") or None,
    )
    safe_answer = (
        get_safe_public_answer(
            query,
            best,
            query_facet=query_facet,
            candidate_facet=best.get("fact_key") or None,
        )
        if direct and float(best["relevance"]) >= 1.0
        else None
    )
    decision = PublicRecallDecision(
        direct_fact_question=direct,
        candidate_present=True,
        confidence=float(best["confidence"]),
        score=float(best["score"]),
        safe_answer=safe_answer or "",
        uncertainty_fallback=_uncertainty_fallback(),
        reason="high_confidence_direct_fact" if safe_answer else "safe_relevant_candidate",
        evidence_id=str(best["id"]),
        evidence_text=str(best["text"]),
        use_deterministic=bool(safe_answer),
    )
    if decision.use_deterministic:
        return {
            "prompt": "",
            "decision": decision,
            "filtered_count": rejected,
            "status": "deterministic_answer",
        }

    facts = [item["text"] for item in safe_candidates[:3]]
    prompt_parts = [
        "VERIFIED PUBLIC FACTS (answer ONLY from these OR state 'chưa biết'):",
        "",
    ]
    prompt_parts.extend(f"{index}. {fact}" for index, fact in enumerate(facts, 1))
    prompt_parts.extend([
        "",
        "RULES:",
        "- Answer only from the verified facts above when the question matches.",
        "- Preserve salient values exactly; do not substitute a different value.",
        "- If the facts do not answer the question, use natural uncertainty.",
        "- Never mention source IDs, actor keys, room IDs, or internal labels.",
        "- Missing evidence does not prove the fact was never stated.",
        "",
        f"User question: {str(query or '').strip()[:500]}",
    ])
    prompt = "\n".join(prompt_parts)
    return {
        "prompt": prompt,
        "decision": replace(decision, prompt=prompt),
        "filtered_count": rejected,
        "status": "prompt_assembled",
    }


def verify_public_response(
    evidence_text: str,
    model_reply: str,
    confidence: float,
    *,
    safe_answer: str | None = None,
) -> dict[str, Any]:
    """Verify salient factual values or accept normalized uncertainty."""

    folded_reply = _fold(model_reply)
    expected = _salient_tokens(safe_answer or evidence_text)
    actual = _salient_tokens(model_reply)
    has_uncertainty = bool(_UNCERTAINTY_RE.search(folded_reply))
    if has_uncertainty:
        # Uncertainty alone is safe.  A hedged claim that introduces a
        # different salient value is still a substitution and must fail.
        unexpected = actual - expected - _CONVERSATIONAL_FILLER_TOKENS
        if unexpected:
            return {"accepted": False, "reason": "uncertainty_with_unsupported_value"}
        return {"accepted": True, "reason": "uncertainty_expressed"}
    if not model_reply:
        return {"accepted": True, "reason": "uncertainty_expressed"}
    # Deterministic output intentionally requires >= 0.90, but every record
    # admitted to the public grounding prompt (>= 0.70 in ``_safe_candidate``)
    # must still be checked for factual substitution.  Using the deterministic
    # threshold here allowed an 0.85-confidence fact to be replaced by a
    # different value on both sync and stream hot paths.
    if float(confidence or 0.0) < 0.70:
        return {"accepted": True, "reason": "confidence_below_verifier_gate"}

    if not expected:
        return {"accepted": False, "reason": "no_salient_evidence_value"}
    missing = expected - actual
    unexpected = actual - expected - _CONVERSATIONAL_FILLER_TOKENS
    # The reply may add ordinary conversational tokens, but if a salient
    # evidence value is missing and a different salient value appears, it is
    # a substitution. This rejects coffee -> green tea even though "thích"
    # and "minh" overlap in the raw text.
    if unexpected:
        return {"accepted": False, "reason": "substitution_detected"}
    if missing:
        return {"accepted": False, "reason": "salient_value_missing"}
    return {"accepted": True, "reason": "salient_value_confirmed"}


def format_public_grounding_prompt(
    decision: PublicRecallDecision | Mapping[str, Any] | None,
    query: str = "",
    candidates: Sequence[Mapping[str, Any]] = (),
) -> str:
    if decision is None:
        return ""
    if isinstance(decision, PublicRecallDecision):
        return decision.prompt
    return str(decision.get("prompt", ""))


def filter_public_candidates(
    records: Sequence[Mapping[str, Any]],
    scope: Mapping[str, Any] | Any,
    *,
    now: float | None = None,
) -> list[dict[str, Any]]:
    """Compatibility name for the canonical safe-candidate filter."""

    current_time = _evaluation_time(now)
    if current_time is None:
        return []
    scope_map = _scope_mapping(scope)
    output: list[dict[str, Any]] = []
    for raw in records or ():
        item, _ = _safe_candidate(raw, scope=scope_map, now=current_time)
        if item is not None:
            declared = _declared_facet_value(raw) if isinstance(raw, Mapping) else None
            if not candidate_has_personal_predicate(
                item["text"], facet=item.get("fact_key") or None, declared=declared
            ):
                continue
            output.append(dict(item, text=item["text"]))
    return output


# Compatibility aliases used by older smoke imports. They still point to the
# same strict implementation and do not bypass the canonical boundary.
filter_public_safe_candidates = filter_public_candidates
verify_public_grounding_reply = verify_public_response

__all__ = [
    "PUBLIC_FACT_FACETS",
    "PublicRecallDecision",
    "assemble_public_grounding_prompt",
    "filter_public_candidates",
    "filter_public_safe_candidates",
    "format_public_grounding_prompt",
    "get_safe_public_answer",
    "is_direct_fact_question",
    "is_public_memory_recall_question",
    "infer_public_query_facet",
    "infer_public_query_facets",
    "infer_public_candidate_facet",
    "query_targets_public_viewer",
    "candidate_has_personal_predicate",
    "verify_public_response",
    "verify_public_grounding_reply",
]
