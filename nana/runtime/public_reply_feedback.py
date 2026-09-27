"""STAGE-9R: session feedback from public reply evaluation.

This layer turns the latest STAGE-9Q diagnostic flags into a compact content
directive for the next public reply. It does not rewrite text, block delivery,
call an LLM, write memory, or grant actions.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
import threading
import time
import unicodedata
from typing import Any


PHASE = "STAGE-9R"


@dataclass(frozen=True)
class PublicReplyFeedbackDirective:
    mode: str
    trigger: str
    current_topic: str
    instruction: str
    avoid: tuple[str, ...] = field(default_factory=tuple)
    confidence: float = 0.0
    last_grade: str = "none"
    last_issue_kinds: tuple[str, ...] = field(default_factory=tuple)
    phase: str = PHASE
    read_only: bool = True
    can_act: bool = False
    memory_write: bool = False
    api_call: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clean(value: Any, limit: int = 900) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    return text[:limit]


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFD", str(value or "").lower())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = text.replace("đ", "d")
    return re.sub(r"\s+", " ", text).strip()


def _has_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _topic_for(text: str) -> str:
    folded = _fold(text)
    if _has_any(folded, (r"\bphong\s+(?:nay\s+)?(?:im|vang|yen|lang)\b", r"\bim\s+qua\b")):
        return "quiet_room"
    if _has_any(folded, (r"\bchuyen\s+ngao\b", r"\bke\s+chuyen\b", r"\bchuyen\s+gi\s+vui\b")):
        return "story"
    if _has_any(folded, (r"\btro\s+ly\b", r"\bphuc\s+vu\b", r"\bquay\s+ho\s+tro\b")):
        return "service_boundary"
    if _has_any(folded, (r"\bbot\b", r"\bdiscord\b", r"\bcong\s+cu\b", r"\bcai\s+hop\b")):
        return "identity_boundary"
    if _has_any(folded, (r"\bgpt\b", r"\bllm\b", r"\bmodel\b", r"\bgemini\b", r"\bclaude\b", r"\bgrok\b")):
        return "model_talk"
    return "open_public_chat"


class PublicReplyFeedback:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._initialized_at = time.time()
        self._last_directive: PublicReplyFeedbackDirective | None = None
        self._stats = {
            "built": 0,
            "active": 0,
            "none": 0,
            "menu_loop": 0,
            "gpt_hoa": 0,
            "service": 0,
            "story": 0,
            "fluency": 0,
            "harsh": 0,
        }

    def build(
        self,
        *,
        text: Any = "",
        last_eval: dict[str, Any] | None = None,
    ) -> PublicReplyFeedbackDirective:
        prompt = _clean(text)
        topic = _topic_for(prompt)
        eval_data = self._resolve_last_eval(last_eval)
        grade = str(eval_data.get("grade") or "none")
        issues = tuple(str(item) for item in (eval_data.get("issue_kinds") or ()))
        issue_set = set(issues)

        directive = self._directive_for(topic=topic, grade=grade, issue_set=issue_set, issues=issues)
        self._record(directive)
        return directive

    def _resolve_last_eval(self, last_eval: dict[str, Any] | None) -> dict[str, Any]:
        if isinstance(last_eval, dict) and last_eval:
            return last_eval
        try:
            from nana.runtime.public_reply_evaluator import get_public_reply_evaluator

            snapshot = get_public_reply_evaluator().snapshot()
            return dict(snapshot.get("last_result") or {})
        except Exception:
            return {}

    def _directive_for(
        self,
        *,
        topic: str,
        grade: str,
        issue_set: set[str],
        issues: tuple[str, ...],
    ) -> PublicReplyFeedbackDirective:
        if not issue_set or grade in {"none", "clean"}:
            return PublicReplyFeedbackDirective(
                mode="none",
                trigger="no_recent_eval_issue",
                current_topic=topic,
                instruction="No public reply evaluator feedback is needed for this turn.",
                avoid=("internal_label_leak",),
                confidence=0.0,
                last_grade=grade,
                last_issue_kinds=issues,
            )

        if "menu_loop" in issue_set and topic == "quiet_room":
            return PublicReplyFeedbackDirective(
                mode="avoid_menu_loop",
                trigger="last_reply_menu_loop",
                current_topic=topic,
                instruction="Do not reuse the game/music/story choice menu; switch to one fresh room image or ask for one specific detail.",
                avoid=("same_choice_menu", "same_silence_metaphor", "begging_for_chat"),
                confidence=0.78,
                last_grade=grade,
                last_issue_kinds=issues,
            )

        if "gpt_hoa" in issue_set and topic in {"model_talk", "open_public_chat"}:
            return PublicReplyFeedbackDirective(
                mode="de_gpt_reply",
                trigger="last_reply_gpt_hoa",
                current_topic=topic,
                instruction="Answer with one concrete opinion or metaphor; avoid benchmark/explainer phrasing and corporate summary tails.",
                avoid=("benchmark_lecture", "depends_on_context", "summary_tail"),
                confidence=0.76,
                last_grade=grade,
                last_issue_kinds=issues,
            )

        if issue_set & {"service_tone", "missing_service_boundary"} and topic in {"service_boundary", "identity_boundary"}:
            return PublicReplyFeedbackDirective(
                mode="restore_boundary_voice",
                trigger="last_reply_service_or_boundary_issue",
                current_topic=topic,
                instruction="Put Nana's stance first, then redirect playfully; do not sound like a support counter.",
                avoid=("support_counter_voice", "passive_ack", "over_apology"),
                confidence=0.80,
                last_grade=grade,
                last_issue_kinds=issues,
            )

        if "missed_story_prompt" in issue_set and topic == "story":
            return PublicReplyFeedbackDirective(
                mode="restore_story_shape",
                trigger="last_story_prompt_missed",
                current_topic=topic,
                instruction="Tell a tiny scene with setup, twist, and punchline; do not answer with a bare invitation.",
                avoid=("generic_prompt_back", "one_line_non_story"),
                confidence=0.78,
                last_grade=grade,
                last_issue_kinds=issues,
            )

        if issue_set & {"awkward_vietnamese", "meta_leak"}:
            return PublicReplyFeedbackDirective(
                mode="surface_cleanup_attention",
                trigger="last_reply_surface_issue",
                current_topic=topic,
                instruction="Keep the next reply plain Vietnamese; avoid meta labels, bracket voice tags, and awkward machine phrasing.",
                avoid=("meta_frame", "voice_tag", "awkward_vietnamese"),
                confidence=0.74,
                last_grade=grade,
                last_issue_kinds=issues,
            )

        if "too_harsh" in issue_set:
            return PublicReplyFeedbackDirective(
                mode="soften_edge",
                trigger="last_reply_too_harsh",
                current_topic=topic,
                instruction="Keep the backbone, but soften the sting; playful teasing should not become a shutdown.",
                avoid=("harsh_shutdown", "mean_tease"),
                confidence=0.82,
                last_grade=grade,
                last_issue_kinds=issues,
            )

        return PublicReplyFeedbackDirective(
            mode="watch_last_issue",
            trigger="recent_eval_issue",
            current_topic=topic,
            instruction="Keep the current answer specific and avoid repeating the last public-reply issue.",
            avoid=tuple(sorted(issue_set)) or ("internal_label_leak",),
            confidence=0.54,
            last_grade=grade,
            last_issue_kinds=issues,
        )

    def _record(self, directive: PublicReplyFeedbackDirective) -> None:
        with self._lock:
            self._last_directive = directive
            self._stats["built"] += 1
            if directive.mode == "none":
                self._stats["none"] += 1
            else:
                self._stats["active"] += 1
            if directive.mode == "avoid_menu_loop":
                self._stats["menu_loop"] += 1
            elif directive.mode == "de_gpt_reply":
                self._stats["gpt_hoa"] += 1
            elif directive.mode == "restore_boundary_voice":
                self._stats["service"] += 1
            elif directive.mode == "restore_story_shape":
                self._stats["story"] += 1
            elif directive.mode == "surface_cleanup_attention":
                self._stats["fluency"] += 1
            elif directive.mode == "soften_edge":
                self._stats["harsh"] += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            stats = dict(self._stats)
            last = self._last_directive
        return {
            "phase": PHASE,
            "mode": "session-eval-feedback",
            "read_only": True,
            "can_act": False,
            "memory_write": False,
            "api_call": False,
            "initialized_at": self._initialized_at,
            "stats": stats,
            "last_directive": last.to_dict() if last else None,
        }


_PUBLIC_REPLY_FEEDBACK = PublicReplyFeedback()


def get_public_reply_feedback() -> PublicReplyFeedback:
    return _PUBLIC_REPLY_FEEDBACK


def build_public_reply_feedback_directive(
    *,
    text: Any = "",
    last_eval: dict[str, Any] | None = None,
) -> PublicReplyFeedbackDirective:
    return get_public_reply_feedback().build(text=text, last_eval=last_eval)


def format_public_reply_feedback_hint(directive: PublicReplyFeedbackDirective) -> str:
    avoid = ", ".join(directive.avoid) if directive.avoid else "none"
    issues = ", ".join(directive.last_issue_kinds) if directive.last_issue_kinds else "none"
    return "\n".join(
        [
            f"Public reply feedback ({PHASE}):",
            f"- mode={directive.mode}",
            f"- trigger={directive.trigger}",
            f"- current_topic={directive.current_topic}",
            f"- last_grade={directive.last_grade}",
            f"- last_issues={issues}",
            f"- instruction={directive.instruction}",
            f"- avoid={avoid}",
            "- This is next-turn style feedback only; do not mention evaluator labels to viewers.",
        ]
    )


def public_reply_feedback_status_lines() -> list[str]:
    snap = get_public_reply_feedback().snapshot()
    stats = dict(snap.get("stats") or {})
    last = dict(snap.get("last_directive") or {})
    return [
        f"Public Reply Feedback ({PHASE})",
        "  Mode: session-eval-feedback | read_only=True | can_act=False | memory_write=False | api_call=False",
        (
            "  Last: "
            f"mode={last.get('mode', 'none')} | trigger={last.get('trigger', 'none')} | "
            f"topic={last.get('current_topic', 'none')} | confidence={last.get('confidence', 0.0)}"
        ),
        (
            "  Stats: "
            f"built={stats.get('built', 0)} | active={stats.get('active', 0)} | "
            f"menu={stats.get('menu_loop', 0)} | gpt_hoa={stats.get('gpt_hoa', 0)} | "
            f"service={stats.get('service', 0)} | story={stats.get('story', 0)} | fluency={stats.get('fluency', 0)}"
        ),
        "  Commands: /public-reply-feedback-status | /public-reply-feedback-preview <text>",
        "  Safety: content bias only | no LLM | no memory write | no TTS/VTS/OBS/Discord/game input",
    ]


def public_reply_feedback_preview_lines(payload: str) -> list[str]:
    text = _clean(payload)
    if not text:
        return ["  Usage: /public-reply-feedback-preview <text>"]
    directive = build_public_reply_feedback_directive(text=text)
    lines = [
        f"Public Reply Feedback Preview ({PHASE})",
        f"  Input: {text}",
        f"  Mode: {directive.mode} | trigger={directive.trigger} | topic={directive.current_topic}",
        f"  Last: grade={directive.last_grade} | issues={', '.join(directive.last_issue_kinds) or 'none'}",
        f"  Instruction: {directive.instruction}",
        f"  Avoid: {', '.join(directive.avoid) or 'none'}",
        "  Safety: preview only | content bias only | no write | no output action",
    ]
    return lines
