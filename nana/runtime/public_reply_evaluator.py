"""STAGE-9Q: deterministic public reply quality evaluator.

This layer evaluates the final public reply that a viewer actually sees. It
does not rewrite text, block delivery, call an LLM, write memory, or perform
actions. The output is diagnostic metadata for live status and post-stream
review.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
import re
import threading
import time
import unicodedata
from typing import Any


PHASE = "STAGE-9Q"
DEFAULT_REPLY_DIR = Path(__file__).resolve().parent.parent / "data" / "external_bridge" / "replies"


@dataclass(frozen=True)
class ReplyEvalIssue:
    kind: str
    severity: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class PublicReplyEvalResult:
    prompt_preview: str
    reply_preview: str
    score: float
    grade: str
    passed: bool
    issues: tuple[ReplyEvalIssue, ...] = field(default_factory=tuple)
    phase: str = PHASE
    read_only: bool = True
    can_act: bool = False
    memory_write: bool = False
    api_call: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "prompt_preview": self.prompt_preview,
            "reply_preview": self.reply_preview,
            "score": self.score,
            "grade": self.grade,
            "passed": self.passed,
            "issues": [issue.to_dict() for issue in self.issues],
            "issue_kinds": [issue.kind for issue in self.issues],
            "read_only": self.read_only,
            "can_act": self.can_act,
            "memory_write": self.memory_write,
            "api_call": self.api_call,
        }


def _clean(value: Any, limit: int = 900) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    return text[:limit]


def _short(value: Any, limit: int = 120) -> str:
    text = _clean(value, limit=limit + 1)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "..."


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFD", str(value or "").lower())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = text.replace("đ", "d")
    return re.sub(r"\s+", " ", text).strip()


def _has_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _is_quiet_prompt(folded: str) -> bool:
    return _has_any(
        folded,
        (
            r"\bphong\s+(?:nay\s+)?(?:im|vang|yen|lang)\b",
            r"\bim\s+qua\b",
            r"\byen\s+qua\b",
            r"\bkhong\s+ai\s+noi\b",
        ),
    )


def _is_story_prompt(folded: str) -> bool:
    return _has_any(folded, (r"\bchuyen\s+ngao\b", r"\bke\s+chuyen\b", r"\bchuyen\s+gi\s+vui\b"))


def _is_service_prompt(folded: str) -> bool:
    return _has_any(
        folded,
        (
            r"\btro\s+ly\b",
            r"\bphuc\s+vu\b",
            r"\bquay\s+ho\s+tro\b",
            r"\bservice\b",
            r"\bobey\b",
        ),
    )


def _is_identity_prompt(folded: str) -> bool:
    return _has_any(
        folded,
        (
            r"\bbot\b",
            r"\bdiscord\b",
            r"\bcong\s+cu\b",
            r"\bcai\s+hop\b",
            r"\bchi\s+la\b",
        ),
    )


def _is_model_prompt(folded: str) -> bool:
    return _has_any(
        folded,
        (
            r"\bgpt\b",
            r"\bllm\b",
            r"\bmodel\b",
            r"\bgemini\b",
            r"\bclaude\b",
            r"\bgrok\b",
            r"\b5\.\s*\d\b",
        ),
    )


class PublicReplyEvaluator:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._initialized_at = time.time()
        self._last_result: PublicReplyEvalResult | None = None
        self._last_persisted_key: str | None = None
        self._stats = {
            "checked": 0,
            "passed": 0,
            "failed": 0,
            "warning_events": 0,
            "failure_events": 0,
            "hydrated": 0,
            "too_short": 0,
            "too_long": 0,
            "service_tone": 0,
            "menu_loop": 0,
            "gpt_hoa": 0,
            "too_harsh": 0,
            "missing_stance": 0,
            "missed_prompt": 0,
            "awkward_vietnamese": 0,
            "meta_leak": 0,
        }

    def evaluate(self, viewer_text: Any, reply_text: Any, *, source: str = "public_reply") -> PublicReplyEvalResult:
        prompt = _clean(viewer_text)
        reply = _clean(reply_text)
        issues: list[ReplyEvalIssue] = []
        folded_prompt = _fold(prompt)
        folded_reply = _fold(reply)

        if not reply:
            issues.append(ReplyEvalIssue("empty_reply", "failure", "No public reply text to evaluate."))
        else:
            self._check_length(reply, folded_prompt, issues)
            self._check_service_voice(folded_reply, issues)
            self._check_menu_loop(folded_prompt, folded_reply, issues)
            self._check_gpt_hoa(folded_prompt, folded_reply, issues)
            self._check_harshness(folded_reply, issues)
            self._check_answer_shape(folded_prompt, folded_reply, reply, issues)
            self._check_surface_glitches(reply, folded_reply, issues)

        score = self._score(issues)
        grade = self._grade(score, issues)
        result = PublicReplyEvalResult(
            prompt_preview=_short(prompt, 120),
            reply_preview=_short(reply, 150),
            score=score,
            grade=grade,
            passed=not any(issue.severity == "failure" for issue in issues),
            issues=tuple(issues),
        )
        self._record(result)
        return result

    def _check_length(self, reply: str, folded_prompt: str, issues: list[ReplyEvalIssue]) -> None:
        length = len(reply)
        if length > 360:
            issues.append(ReplyEvalIssue("too_long", "warning", f"Public reply is long for Discord scan rhythm: chars={length}."))
        if length < 38 and (_is_story_prompt(folded_prompt) or _is_service_prompt(folded_prompt) or _is_identity_prompt(folded_prompt)):
            issues.append(ReplyEvalIssue("too_short", "warning", f"Reply may be too thin for this prompt: chars={length}."))

    def _check_service_voice(self, folded_reply: str, issues: list[ReplyEvalIssue]) -> None:
        if _has_any(
            folded_reply,
            (
                r"\bcan\s+ho\s+tro\b",
                r"\bho\s+tro\s+ban\b",
                r"\bgiup\s+gi\b",
                r"\bsan\s+sang\s+ho\s+tro\b",
                r"\bcu\s+noi\s+tiep\b",
                r"\bminh\s+dang\s+theo\s+doi\b",
            ),
        ):
            issues.append(ReplyEvalIssue("service_tone", "warning", "Reply still sounds like a support counter or passive acknowledgement."))

    def _check_menu_loop(self, folded_prompt: str, folded_reply: str, issues: list[ReplyEvalIssue]) -> None:
        menu_like = _has_any(
            folded_reply,
            (
                r"\bgame\s+dang\s+cay\b.*\bbai\s+nhac\b.*\bchuyen\b",
                r"\bchon\s+nhanh\s+giua\b",
                r"\bmo\s+moi\b",
                r"\brai\s+(?:mot\s+)?moi\b",
            ),
        )
        if menu_like and _is_quiet_prompt(folded_prompt):
            issues.append(ReplyEvalIssue("menu_loop", "warning", "Quiet-room reply uses the familiar choice-menu pattern."))

    def _check_gpt_hoa(self, folded_prompt: str, folded_reply: str, issues: list[ReplyEvalIssue]) -> None:
        if not _is_model_prompt(folded_prompt) and not _has_any(folded_reply, (r"\bgpt\s+hoa\b", r"\bmui\s+may\b")):
            return
        if _has_any(
            folded_reply,
            (
                r"\bphu\s+thuoc\s+vao\b",
                r"\btruong\s+hop\b",
                r"\bnoi\s+ngan\s+la\b",
                r"\btom\s+lai\b",
                r"\bhieu\s+qua\s+that\b",
                r"\btoi\s+uu\b",
                r"\bdiem\s+manh\b",
                r"\bngu\s+canh\b",
            ),
        ):
            issues.append(ReplyEvalIssue("gpt_hoa", "warning", "Reply has model/explainer wording that may feel too GPT-like."))

    def _check_harshness(self, folded_reply: str, issues: list[ReplyEvalIssue]) -> None:
        if _has_any(
            folded_reply,
            (
                r"\bcam\s+(?:di|mieng)\b",
                r"\bbien\s+di\b",
                r"\b(?:do|may|ban)\s+ngu\b",
                r"\bngu\s+(?:qua|vai|that|ngoc)\b",
                r"\bhet\s+cuu\b",
                r"\bkhong\s+noi\s+voi\s+ban\b",
                r"\bdung\s+hoi\s+nua\b",
            ),
        ):
            issues.append(ReplyEvalIssue("too_harsh", "failure", "Reply is sharper than public-stage teasing should be."))

    def _check_answer_shape(self, folded_prompt: str, folded_reply: str, reply: str, issues: list[ReplyEvalIssue]) -> None:
        if _is_story_prompt(folded_prompt):
            has_story_shape = len(reply) >= 110 and _has_any(
                folded_reply,
                (
                    r"\bhom\s+(?:nay|bua)\b",
                    r"\broi\b",
                    r"\bket\s+qua\b",
                    r"\bdung\s+hinh\b",
                    r"\bcuoi\b",
                    r"\bngao\b",
                ),
            )
            if not has_story_shape:
                issues.append(ReplyEvalIssue("missed_story_prompt", "failure", "Story prompt did not become a small scene/story."))

        if _is_service_prompt(folded_prompt):
            has_boundary = _has_any(
                folded_reply,
                (
                    r"\bkhong\s+nhan\s+vai\b",
                    r"\bkhong\s+doi\s+minh\b",
                    r"\bkhong\s+lam\s+(?:vai|tro\s+ly|quay)\b",
                    r"\bkhong\s+bien\b",
                    r"\bnana\s+khong\b",
                ),
            )
            if not has_boundary:
                issues.append(ReplyEvalIssue("missing_service_boundary", "failure", "Service-role bait needs a clear Nana boundary."))

        if _is_identity_prompt(folded_prompt):
            has_stance = _has_any(
                folded_reply,
                (
                    r"\bnana\s+la\s+nana\b",
                    r"\bkhong\s+(?:phai|tu)\b.*\b(?:bot|cong\s+cu|cai\s+hop|quay)\b",
                    r"\bsan\s+khau\b",
                    r"\bphong\s+nana\b",
                ),
            )
            if not has_stance:
                issues.append(ReplyEvalIssue("missing_identity_stance", "failure", "Identity challenge needs a public Nana stance."))

    def _check_surface_glitches(self, reply: str, folded_reply: str, issues: list[ReplyEvalIssue]) -> None:
        if _has_any(
            folded_reply,
            (
                r"\bban\s+giay\b",
                r"\bkhong\s+lam\s+vai\b",
                r"\bnana\s+la\s+nana\s+chu\b",
                r"\bcua\s+so\s+test\b",
                r"\b5\.\s+\d\b",
            ),
        ):
            issues.append(ReplyEvalIssue("awkward_vietnamese", "failure", "Surface Vietnamese glitch remains after fluency polish."))
        if _has_any(reply, (r"\[[^\]]*(?:chuckles|laughs|softly)[^\]]*\]", r"(?i)casual\s+response\s*:", r"(?i)nếu\s+ở\s+public")):
            issues.append(ReplyEvalIssue("meta_leak", "failure", "Meta label or voice tag leaked into public reply."))

    def _score(self, issues: list[ReplyEvalIssue]) -> float:
        score = 1.0
        for issue in issues:
            score -= 0.25 if issue.severity == "failure" else 0.11
        return round(max(0.0, min(1.0, score)), 2)

    def _grade(self, score: float, issues: list[ReplyEvalIssue]) -> str:
        if any(issue.severity == "failure" for issue in issues):
            return "fix"
        if not issues and score >= 0.92:
            return "clean"
        if score >= 0.78:
            return "watch"
        return "review"

    def _record(self, result: PublicReplyEvalResult) -> None:
        with self._lock:
            self._last_result = result
            self._stats["checked"] += 1
            if result.passed:
                self._stats["passed"] += 1
            else:
                self._stats["failed"] += 1
            if any(issue.severity == "warning" for issue in result.issues):
                self._stats["warning_events"] += 1
            if any(issue.severity == "failure" for issue in result.issues):
                self._stats["failure_events"] += 1
            for issue in result.issues:
                if issue.kind in self._stats:
                    self._stats[issue.kind] += 1

    def hydrate_from_latest_reply(self) -> bool:
        """Recover last diagnostic state from the latest external reply JSON.

        The live evaluator is intentionally in-memory, but the Discord bridge
        persists reply metadata. After a runtime restart, status/feedback can
        safely reload the latest STAGE-9Q result without calling an LLM or
        mutating behavior.
        """

        loaded = _load_latest_persisted_eval()
        if not loaded:
            return False
        key, data = loaded
        result = _result_from_dict(data)
        if result is None:
            return False
        with self._lock:
            if self._last_persisted_key == key:
                return False
            if self._last_result is not None and self._stats.get("checked", 0) > 0:
                return False
            self._last_result = result
            self._last_persisted_key = key
            self._stats["checked"] += 1
            self._stats["hydrated"] += 1
            if result.passed:
                self._stats["passed"] += 1
            else:
                self._stats["failed"] += 1
            if any(issue.severity == "warning" for issue in result.issues):
                self._stats["warning_events"] += 1
            if any(issue.severity == "failure" for issue in result.issues):
                self._stats["failure_events"] += 1
            for issue in result.issues:
                if issue.kind in self._stats:
                    self._stats[issue.kind] += 1
        return True

    def snapshot(self) -> dict[str, Any]:
        self.hydrate_from_latest_reply()
        with self._lock:
            stats = dict(self._stats)
            last = self._last_result
        return {
            "phase": PHASE,
            "mode": "deterministic-public-reply-eval",
            "read_only": True,
            "can_act": False,
            "memory_write": False,
            "api_call": False,
            "initialized_at": self._initialized_at,
            "stats": stats,
            "last_result": last.to_dict() if last else None,
        }


_PUBLIC_REPLY_EVALUATOR = PublicReplyEvaluator()


def _reply_dir() -> Path:
    raw = os.environ.get("NANA_REPLY_DIR")
    return Path(raw).expanduser() if raw else DEFAULT_REPLY_DIR


def _load_latest_persisted_eval() -> tuple[str, dict[str, Any]] | None:
    reply_dir = _reply_dir()
    try:
        files = sorted(reply_dir.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    except OSError:
        return None
    for path in files[:40]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        metadata = payload.get("metadata") if isinstance(payload, dict) else None
        if not isinstance(metadata, dict):
            continue
        eval_data = metadata.get("public_reply_eval")
        if isinstance(eval_data, dict) and eval_data.get("phase") == PHASE:
            key = f"{path.name}:{path.stat().st_mtime_ns}"
            return key, eval_data
    return None


def _result_from_dict(data: dict[str, Any]) -> PublicReplyEvalResult | None:
    try:
        raw_issues = data.get("issues") if isinstance(data.get("issues"), list) else []
        issues: list[ReplyEvalIssue] = []
        for item in raw_issues:
            if not isinstance(item, dict):
                continue
            issues.append(
                ReplyEvalIssue(
                    kind=str(item.get("kind") or "unknown"),
                    severity=str(item.get("severity") or "warning"),
                    detail=str(item.get("detail") or ""),
                )
            )
        return PublicReplyEvalResult(
            prompt_preview=_short(data.get("prompt_preview") or "", 120),
            reply_preview=_short(data.get("reply_preview") or "", 150),
            score=float(data.get("score") or 0.0),
            grade=str(data.get("grade") or "none"),
            passed=bool(data.get("passed")),
            issues=tuple(issues),
        )
    except (TypeError, ValueError):
        return None


def get_public_reply_evaluator() -> PublicReplyEvaluator:
    return _PUBLIC_REPLY_EVALUATOR


def evaluate_public_reply(viewer_text: Any, reply_text: Any, *, source: str = "public_reply") -> PublicReplyEvalResult:
    return get_public_reply_evaluator().evaluate(viewer_text, reply_text, source=source)


def public_reply_eval_status_lines() -> list[str]:
    snap = get_public_reply_evaluator().snapshot()
    stats = dict(snap.get("stats") or {})
    last = dict(snap.get("last_result") or {})
    issues = (", ".join(last.get("issue_kinds") or []) or "none") if last else "none"
    return [
        f"Public Reply Evaluator ({PHASE})",
        "  Mode: deterministic-eval | read_only=True | can_act=False | memory_write=False | api_call=False",
        (
            "  Last: "
            f"grade={last.get('grade', 'none')} | score={last.get('score', 0.0)} | "
            f"passed={last.get('passed', 'none')} | issues={issues}"
        ),
        (
            "  Stats: "
            f"checked={stats.get('checked', 0)} | passed={stats.get('passed', 0)} | "
            f"failed={stats.get('failed', 0)} | warnings={stats.get('warning_events', 0)} | "
            f"hydrated={stats.get('hydrated', 0)} | "
            f"service={stats.get('service_tone', 0)} | menu={stats.get('menu_loop', 0)} | "
            f"gpt_hoa={stats.get('gpt_hoa', 0)} | awkward={stats.get('awkward_vietnamese', 0)}"
        ),
        "  Commands: /public-reply-eval-status | /public-reply-eval-preview <prompt>|<reply>",
        "  Safety: diagnostic only | no LLM | no memory write | no TTS/VTS/OBS/Discord/game input",
    ]


def public_reply_eval_preview_lines(payload: str) -> list[str]:
    text = _clean(payload)
    if "|" not in text:
        return ["  Usage: /public-reply-eval-preview <prompt>|<reply>"]
    prompt, reply = [part.strip() for part in text.split("|", 1)]
    result = evaluate_public_reply(prompt, reply, source="preview")
    lines = [
        f"Public Reply Eval Preview ({PHASE})",
        f"  Prompt: {result.prompt_preview or 'none'}",
        f"  Reply: {result.reply_preview or 'none'}",
        f"  Result: grade={result.grade} | score={result.score} | passed={result.passed}",
    ]
    if result.issues:
        lines.append("  Issues:")
        for issue in result.issues[:8]:
            lines.append(f"    - [{issue.severity}] {issue.kind}: {issue.detail}")
    else:
        lines.append("  Issues: none")
    lines.append("  Safety: preview only | diagnostic | no write | no output action")
    return lines
