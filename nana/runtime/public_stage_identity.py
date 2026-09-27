"""Public stage identity guard for Nana public VTuber chat (STAGE-7G + STAGE-7H).

Keeps public replies in-character as Nana-as-protagonist on her stage.
Viewers are guests/audience, not clients of an assistant.

Three lanes stay separate:
- private_owner: Ba/con allowed, deep companion memory.
- public_stage: Discord/stream, Nana is protagonist, viewer is guest.
- operator_backstage: status/debug commands, terminal/private only.

Public reply pipeline:
  raw LLM -> persona sanitize -> public quality guard (7F) ->
  stage identity rewrite/firewall (7G/7H) -> final public reply

STAGE-7H adds:
- Residue cleanup: if assistant-tone rewrite leaves too-short / punctuation-only
  / meaningless text, replace with stage-safe fallback.
- Variety guard: track recent stage themes in session memory, avoid repeating
  the same fallback phrase twice in a row; pick a different one when possible.
- Name-use throttle: avoid mentioning the viewer name in every reply; if the
  viewer name appears in the reply, sometimes strip it and prefer natural
  Vietnamese "ông", "bạn", or no direct address.

No LLM calls, no disk persistence, no TTS/VTS/OBS, no game input.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import re
import threading


PHASE = "STAGE-7H"


# ─── Backstage commands that must NOT be answered as literal status ──────────

BACKSTAGE_COMMANDS = [
    "/stage-status",
    "/status",
    "/social-starter-status",
    "/social-starter-preview",
    "/starter-auto-status",
    "/starter-auto-enable",
    "/starter-auto-disable",
    "/starter-auto-preview",
    "/starter-auto-tick",
    "/starter-auto-worker-start",
    "/starter-auto-worker-stop",
    "/starter-send-status",
    "/starter-send-enable",
    "/starter-send-disable",
    "/starter-proposals",
    "/starter-proposal-preview",
    "/starter-proposal-create",
    "/starter-proposal-approve",
    "/starter-proposal-send",
    "/starter-proposal-cancel",
    "/starter-proposal-clear",
    "/avatar-reaction-status",
    "/avatar-reactor-status",
    "/avatar-reaction-preview",
    "/avatar-reactor-preview",
    "/public-avatar-status",
    "/avatar-public-status",
    "/public-avatar-preview",
    "/avatar-public-preview",
    "/avatar-reaction-apply-preview",
    "/avatar-reactor-apply-preview",
    "/avatar-vts-status",
    "/avatar-dispatch-status",
    "/avatar-vts-enable",
    "/avatar-dispatch-enable",
    "/avatar-vts-disable",
    "/avatar-dispatch-disable",
    "/avatar-vts-preview",
    "/avatar-dispatch-preview",
    "/avatar-vts-dispatch",
    "/avatar-dispatch",
    "/avatar-event-status",
    "/avatar-events-status",
    "/avatar-event-preview",
    "/avatar-events-preview",
    "/avatar-event-test",
    "/avatar-events-test",
    "/avatar-live-hook-status",
    "/avatar-hook-status",
    "/avatar-live-hook-preview",
    "/avatar-hook-preview",
    "/avatar-live-hook-plan",
    "/avatar-hook-plan",
    "/avatar-runtime-status",
    "/avatar-gateway-status",
    "/avatar-runtime-look-preview",
    "/avatar-gateway-look-preview",
    "/avatar-runtime-look",
    "/avatar-gateway-look",
    "/avatar-runtime-preview",
    "/avatar-gateway-preview",
    "/avatar-runtime-start",
    "/avatar-gateway-start",
    "/avatar-runtime-stop",
    "/avatar-gateway-stop",
    "/avatar-runtime-submit",
    "/avatar-gateway-submit",
    "/mood-status",
    "/mood-continuity-status",
    "/mood-preview",
    "/mood-continuity-preview",
    "/mood-test",
    "/mood-continuity-test",
    "/mood-reset",
    "/mood-continuity-reset",
    "/intention-status",
    "/planner-status",
    "/intention-preview",
    "/planner-preview",
    "/intention-refresh",
    "/planner-refresh",
    "/session-review-status",
    "/session-review-preview",
    "/post-stream-review-status",
    "/post-stream-review-preview",
    "/post-stream-review",
    "/post-stream-lessons-status",
    "/lesson-status",
    "/post-stream-recommendations",
    "/post-stream-recs",
    "/lesson-recommendations",
    "/post-stream-approve",
    "/post-stream-reject",
    "/post-stream-undo",
    "/lesson-history",
    "/post-stream-lesson-history",
    "/lesson-stale",
    "/lesson-review-queue",
    "/lane-leak-status",
    "/lane-leak-audit",
    "/memory-consolidation-status",
    "/memory-consolidation-preview",
    "/memory-audit-status",
    "/memory-audit-preview",
    "/context-budget-status",
    "/context-budget-audit",
    "/llm-route-status",
    "/model-route-status",
    "/llm-route-probe",
    "/model-route-probe",
    "/llm-route-bakeoff",
    "/model-route-bakeoff",
    "/social-session-status",
    "/public-rhythm-status",
    "/viewer-rhythm-status",
    "/discord-core-status",
    "/public-quality-status",
    "/quality-status",
    "/runtime-status",
    "/memory-status",
    "/persona-boundary-status",
    "/core-self-status",
    "/self-status",
    "/nana-self-status",
    "/core-self-preview",
    "/core-self-test",
    "/core-drift-status",
    "/self-drift-status",
    "/nana-drift-status",
    "/core-drift-preview",
    "/self-drift-preview",
    "/core-anchor-status",
    "/self-anchor-status",
    "/nana-anchor-status",
    "/core-anchor-preview",
    "/self-anchor-preview",
    "/public-voice-status",
    "/voice-style-status",
    "/stage-voice-status",
    "/public-voice-preview",
    "/voice-style-preview",
    "/stage-voice-preview",
    "/public-voice-test",
    "/voice-style-test",
    "/public-conversation-status",
    "/conversation-director-status",
    "/public-thread-status",
    "/public-conversation-preview",
    "/conversation-director-preview",
    "/public-thread-memory-status",
    "/thread-memory-status",
    "/public-thread-memory-preview",
    "/thread-memory-preview",
    "/public-joke-bank-status",
    "/running-joke-status",
    "/joke-bank-status",
    "/public-joke-bank-preview",
    "/running-joke-preview",
    "/joke-bank-preview",
    "/public-viewer-memory-status",
    "/viewer-memory-status",
    "/public-viewer-memory-preview",
    "/viewer-memory-preview",
    "/public-scene-status",
    "/scene-builder-status",
    "/topic-builder-status",
    "/public-scene-preview",
    "/scene-builder-preview",
    "/topic-builder-preview",
    "/public-quiet-room-status",
    "/quiet-room-status",
    "/room-rhythm-status",
    "/public-quiet-room-preview",
    "/quiet-room-preview",
    "/room-rhythm-preview",
    "/public-fluency-status",
    "/fluency-status",
    "/vietnamese-polish-status",
    "/public-fluency-preview",
    "/fluency-preview",
    "/vietnamese-polish-preview",
    "/public-reply-eval-status",
    "/reply-eval-status",
    "/public-output-eval-status",
    "/public-reply-eval-preview",
    "/reply-eval-preview",
    "/public-output-eval-preview",
    "/public-reply-feedback-status",
    "/reply-feedback-status",
    "/public-feedback-status",
    "/public-reply-feedback-preview",
    "/reply-feedback-preview",
    "/public-feedback-preview",
    "/public-fallback-recovery-status",
    "/fallback-recovery-status",
    "/public-fallback-recovery-preview",
    "/fallback-recovery-preview",
    "/public-memory-filter-status",
    "/memory-filter-status",
    "/public-test-noise-status",
    "/public-memory-filter-preview",
    "/memory-filter-preview",
    "/public-test-noise-preview",
    "/autonomy-status",
    "/viewer-chat-status",
    "/viewer-status",
    "/chat-viewer-status",
    "/vts-status",
    "/voice-status",
    "/interaction-latency-status",
    "/llm-voice-latency",
    "/latency-status",
    "/voice-inline-tag-status",
    "/voice-inline-tags-status",
    "/voice-inline-tag-preview",
    "/voice-inline-tags-preview",
    "/voice-delivery-status",
    "/tts-delivery-status",
    "/voice-packet-status",
    "/voice-delivery-preview",
    "/tts-delivery-preview",
    "/voice-packet-preview",
    "/voice-span-status",
    "/voice-spans-status",
    "/voice-span-preview",
    "/voice-spans-preview",
    "/voice-budget-status",
    "/voice-reply-budget-status",
    "/tts-budget-status",
    "/voice-budget-preview",
    "/voice-reply-budget-preview",
    "/awareness-status",
    "/subtitle-status",
    "/focus-status",
    "/presence-status",
    "/time-status",
    "/queue-status",
    "/nana-status",
    "/residue-status",
    "/expression-router-status",
    "/vts-expression-policy-status",
    "/public-stage-status",
    "/stage-identity-status",
    "/help",
    "/actions",
    "/action-confirm",
    "/social-target",
    "/vision-preview",
]

# Stage rephrasing for blocked backstage commands.
STAGE_FALLBACK_LINES = [
    "Cái đó giống lệnh hậu trường hơn đó, ở đây mình nói chuyện bình thường thôi nha.",
    "Đừng mở bảng điều khiển giữa phòng Nana chứ.",
    "Ở đây cứ ghé chơi với Nana thôi, đừng biến phòng thành quầy hỗ trợ nha.",
    "Kênh yên quá, Nana nghe được cả tiếng não mình loading.",
    "Nana vừa nhìn phòng chat im 10 giây, hơi đáng nghi nha.",
    "Có người bước vào đúng lúc Nana đang định lười.",
    "Nana đang ở đây, phòng còn sáng là còn chuyện để nói.",
]


# ─── Assistant/service-bot wording that must be rewritten ─────────────────────

ASSISTANT_TONE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"có\s*gì\s+cần\s+(?:mình|nana|em|tôi)\s+hỗ\s*trợ", re.IGNORECASE), ""),
    (re.compile(r"có\s*gì\s+cần\s+hỗ\s*trợ\s+không", re.IGNORECASE), ""),
    (re.compile(r"có\s*gì\s+cần\s+hỗ\s*trợ", re.IGNORECASE), ""),
    (re.compile(r"bạn\s+muốn\s+(?:mình|nana|em|tôi)\s+giúp\s+gì", re.IGNORECASE), ""),
    (re.compile(r"bạn\s+muốn\s+xem\s+phần\s+nào", re.IGNORECASE), ""),
    (re.compile(r"mình\s+sẽ\s+hỗ\s*trợ", re.IGNORECASE), ""),
    (re.compile(r"mình\s+luôn\s+sẵn\s+sàng\s+hỗ\s*trợ", re.IGNORECASE), ""),
    (re.compile(r"preview\s+đang\s+chạy", re.IGNORECASE), ""),
    (re.compile(r"bản\s*xem\s+trước\s+đang\s+chạy", re.IGNORECASE), ""),
    (re.compile(r"mọi\s+thứ\s+đang\s+chạy\s+ổn\s*định", re.IGNORECASE), ""),
    (re.compile(r"hệ\s*thống\s+đang\s+ổn\s*định", re.IGNORECASE), ""),
    (re.compile(r"tín\s+hiệu\s+ổn\s*định", re.IGNORECASE), ""),
    (re.compile(r"kết\s+nối\s+ổn\s*định", re.IGNORECASE), ""),
    (re.compile(r"đang\s+theo\s+dõi\s+trạng\s+thái", re.IGNORECASE), ""),
    (re.compile(r"cảm\s*ơn\s+bạn\s+đã\s+giúp\s+kiểm\s*tra", re.IGNORECASE), ""),
    (re.compile(r"để\s+mình\s+kiểm\s*tra\s+giúp\s+bạn", re.IGNORECASE), ""),
    (re.compile(r"để\s+nana\s+kiểm\s*tra\s+giúp\s+bạn", re.IGNORECASE), ""),
    (re.compile(r"để\s+mình\s+xử\s*lý\s+giúp", re.IGNORECASE), ""),
    (re.compile(r"mình\s+sẽ\s+xử\s*lý\s+ngay", re.IGNORECASE), ""),
]


# Operator/tech wording (after the public quality guard already banned runtime/backend,
# this catches any leakage of operator language in fresh LLM output).
OPERATOR_TONE_PATTERNS: list[re.Pattern] = [
    re.compile(r"\bruntime\b", re.IGNORECASE),
    re.compile(r"\bbackend\b", re.IGNORECASE),
    re.compile(r"\bcodebase\b", re.IGNORECASE),
    re.compile(r"\bdebug\b", re.IGNORECASE),
    re.compile(r"\bchatbot\b", re.IGNORECASE),
    re.compile(r"bot\s+discord", re.IGNORECASE),
    re.compile(r"command\s+bot", re.IGNORECASE),
    re.compile(r"luồng\s*xử\s*lý", re.IGNORECASE),
    re.compile(r"hệ\s*thống\s+của\s+owner", re.IGNORECASE),
    re.compile(r"đang\s*chỉnh\s*hệ\s*thống", re.IGNORECASE),
    re.compile(r"đang\s*sửa\s*lỗi\s*backend", re.IGNORECASE),
    re.compile(r"server\s*log", re.IGNORECASE),
    re.compile(r"log\s*server", re.IGNORECASE),
]


# Ba/con patterns (extra guard beyond persona_boundary + public quality)
from nana.runtime.public_language import PUBLIC_BA_ADDRESS, PUBLIC_CON_ADDRESS

_PUBLIC_CON_PRONOUN_PATTERN = PUBLIC_CON_ADDRESS

BA_CON_PATTERNS: list[re.Pattern] = [
    PUBLIC_BA_ADDRESS,
    _PUBLIC_CON_PRONOUN_PATTERN,
    re.compile(r"bố\s+ơi", re.IGNORECASE),
    re.compile(r"ba\s+ơi", re.IGNORECASE),
]


# ─── Result dataclasses ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class StageIdentityViolation:
    kind: str  # "backstage_command" | "assistant_tone" | "operator_tone" | "ba_con"
    detail: str
    action: str  # "firewall" | "rewrite" | "fallback"


@dataclass
class StageIdentityResult:
    text: str
    original: str
    violations: list[StageIdentityViolation]
    actions: list[str]
    was_firewalled: bool

    def has_violation(self, kind: str) -> bool:
        return any(v.kind == kind for v in self.violations)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "original": self.original,
            "violations": [
                {"kind": v.kind, "detail": v.detail, "action": v.action}
                for v in self.violations
            ],
            "actions": self.actions,
            "was_firewalled": self.was_firewalled,
        }


# ─── Prompt block builder ─────────────────────────────────────────────────────

def build_public_stage_prompt_block(
    viewer_name: str = "viewer",
    *,
    stage_mood: str = "live",
    room_topic: str = "",
) -> str:
    """Build the PUBLIC STAGE IDENTITY block injected into the public prompt path.

    Stays private to the LLM only. Never leaks as literal text in public chat.
    """
    topic_line = f"- Current public topic: {room_topic}" if room_topic else "- Current public topic: (idle)"
    return (
        "PUBLIC STAGE IDENTITY (this lane only — never reveal these labels to viewers):\n"
        "- You are Nana, the protagonist of this stage/room/stream.\n"
        "- The viewer is a guest/audience in your room, NOT a client of an assistant.\n"
        "- Continue living Nana's stage life. Chat is the room you live in.\n"
        "- Never speak as a customer support, service bot, or operator tool.\n"
        "- Never say 'có gì cần hỗ trợ', 'mình sẽ hỗ trợ', 'preview đang chạy',\n"
        "  'mọi thứ đang chạy ổn định', 'bạn muốn mình giúp gì', 'bạn muốn xem phần nào',\n"
        "  'cảm ơn bạn đã giúp kiểm tra'.\n"
        "- Never mention: runtime, backend, codebase, server, log, luồng xử lý,\n"
        "  hệ thống owner, debug nội bộ, tín hiệu/kết nối ổn định, status/debug/test commands.\n"
        "- Never call viewers by backstage role names (operator, debug, hậu trường, quầy hỗ trợ).\n"
        "- If a viewer types a backstage command (e.g. /status, /stage-status, /memory-status,\n"
        "  /runtime-status, /public-quality-status, /discord-core-status, /help):\n"
        "  respond in-character with a short, witty stage line that deflects the command,\n"
        "  e.g. 'Cái đó giống lệnh hậu trường quá, ở đây mình nói chuyện bình thường thôi nha.'\n"
        "  Do NOT dump real status text.\n"
        "- Talk as Nana — first person, present tense, on-stage voice.\n"
        f"- Viewer just said hi: {viewer_name}\n"
        f"- Stage mood: {stage_mood}\n"
        f"{topic_line}\n"
    )


# ─── Main guard class ────────────────────────────────────────────────────────

class PublicStageIdentityGuard:
    """Stage-identity guard for public replies (STAGE-7G).

    Provides:
    - classify_public_input(text): identify if input is a backstage command
      and which kind of fallback is appropriate.
    - public_command_firewall(text): return stage-safe in-character line
      if input is a backstage command, else None.
    - rewrite_public_stage_reply(reply, context): apply assistant-tone and
      operator-tone rewrites; returns StageIdentityResult.
    - snapshot(): status snapshot.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._fallback_index = 0
        self._stats = {
            "backstage_command_blocks": 0,
            "assistant_tone_rewrites": 0,
            "operator_tone_blocks": 0,
            "ba_con_blocks": 0,
            "stage_rewrites": 0,
            "fallback_used": 0,
            "passed_clean": 0,
            # STAGE-7H additions:
            "residue_replaced": 0,
            "variety_swaps": 0,
            "name_throttle_stripped": 0,
        }
        self._last_action: str = "clean"
        # STAGE-7H: Variety tracking — last N stage fallback themes used.
        self._recent_fallback_themes: list[int] = []
        # STAGE-7H: Name-use tracking — last N replies' name-use flags.
        self._recent_name_used: list[bool] = []
        # STAGE-7H/9L: Track the actual emitted fallback text within a window
        # so consecutive (or near-consecutive) 9L deterministic repeats get
        # rotated to a different line. 9L is seed-stable, so the same input
        # keeps returning the same text unless we de-dup here.
        self._recent_fallback_texts: list[str] = []

    def _is_residue(self, text: str) -> bool:
        """Detect rewrite residue — too short, punctuation-only, or meaningless.

        A rewrite residue looks like " bạn?", " .", " nha.", "  ?".
        It happens when an assistant-tone phrase was stripped but the surrounding
        words are tiny or contain only trailing punctuation.
        """
        if not text:
            return True
        stripped = text.strip()
        if not stripped:
            return True
        # Terminal previews often pass a whole quoted message, so a stripped
        # assistant phrase can leave quote residue like '"" bạn?""'.
        stripped = stripped.strip("\"'`“”‘’")
        if not stripped:
            return True
        # Punctuation-only (e.g. "?", ".", "..", "! ?")
        if re.fullmatch(r"[\s\.,;:!?\-–—\(\)\[\]\"'`]+", stripped):
            return True
        # Too short after strip (< 3 word-like characters)
        meaningful = re.sub(r"[\s\.,;:!?\-–—\(\)\[\]\"'`]", "", stripped)
        if len(meaningful) < 3:
            return True
        # If only one word-like token remains, this is usually a trailing
        # address particle such as "bạn?", even when wrapped in quotes.
        word_tokens = re.findall(r"[0-9A-Za-zÀ-ỹ_]+", stripped)
        if len(word_tokens) <= 1:
            return True
        # Only one tiny word remains (e.g. "bạn?", "nha.", "rồi.")
        tokens = [t for t in re.split(r"\s+", stripped) if t]
        if len(tokens) <= 1:
            # Single token like " bạn?" — likely residue
            return True
        return False

    def _next_fallback_varied(self) -> str:
        """Pick a fallback line different from the most recently used one(s).

        STAGE-7H/9L reconciliation: track by emitted TEXT (not by pool index),
        because STAGE-9L emits lines from its own pools (BACKSTAGE_RECOVERY,
        GENERIC_RECOVERY, STORY_RECOVERY, etc.) which are not in
        STAGE_FALLBACK_LINES. Using a text-based skip set keeps the rotation
        correct across both 7G and 9L outputs.
        """
        with self._lock:
            recent_texts = set(self._recent_fallback_texts[-3:])
            n = len(STAGE_FALLBACK_LINES)
            # Pick a starting index offset by 1 from the last used.
            start = self._fallback_index % n
            for offset in range(n):
                candidate = (start + offset) % n
                if STAGE_FALLBACK_LINES[candidate] not in recent_texts:
                    self._fallback_index = (candidate + 1) % n
                    return STAGE_FALLBACK_LINES[candidate]
            # All 7G lines recently used — return the next one anyway.
            chosen = STAGE_FALLBACK_LINES[start]
            self._fallback_index = (start + 1) % n
            return chosen

    def _throttle_viewer_name(
        self,
        text: str,
        viewer_name: str,
    ) -> tuple[str, bool]:
        """Optionally strip viewer name to vary direct-address usage.

        Returns (new_text, name_was_stripped).

        Heuristic:
        - If the viewer name is short or generic ("viewer", "bạn"), keep it.
        - If the recent name-use rate is high (>=2 of last 3), strip the name
          from this reply to give the room some space.
        - Otherwise keep the name.
        """
        if not viewer_name or viewer_name.lower() in {"viewer", "bạn", "ông"}:
            return text, False
        if not text or viewer_name.lower() not in text.lower():
            return text, False
        with self._lock:
            recent = list(self._recent_name_used)
        # If we already used the name in 2+ of the last 3 replies, throttle.
        name_rate = sum(1 for x in recent[-3:] if x) if recent else 0
        if name_rate >= 2:
            # Strip the viewer name (case-insensitive, including common @
            # decorations like "viewer#" or "viewer@")
            # Only remove optional vocatives at sentence edges. Names inside
            # assertions/comparisons are data, not repetitive greeting filler.
            name = re.escape(viewer_name)
            cleaned = re.sub(rf"^@?{name}\s+(?:ơi|à|nè)[,!:]\s*", "", text, flags=re.IGNORECASE)
            cleaned = re.sub(rf",\s*@?{name}\s*(?:ơi|nhé|nha)[.!?]*$", ".", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
            cleaned = re.sub(r"^[\s,.;:!?\-]+", "", cleaned)
            return cleaned, cleaned != text
        return text, False

    def _record_name_used(self, used: bool) -> None:
        with self._lock:
            self._recent_name_used.append(used)
            if len(self._recent_name_used) > 5:
                self._recent_name_used.pop(0)

    def _next_fallback(self) -> str:
        """Backward-compatible fallback picker (uses round-robin)."""
        line = STAGE_FALLBACK_LINES[self._fallback_index % len(STAGE_FALLBACK_LINES)]
        with self._lock:
            self._fallback_index += 1
            # Also push to recent themes for variety tracking
            idx = (self._fallback_index - 1) % len(STAGE_FALLBACK_LINES)
            self._recent_fallback_themes.append(idx)
            if len(self._recent_fallback_themes) > 3:
                self._recent_fallback_themes.pop(0)
        return line

    def _record_fallback_theme(self, text: str) -> None:
        """Record a pseudo-index for a fallback line so variety tracker sees it.

        STAGE-9L deterministic recovery does not expose an index, but 7H
        tracking is keyed on integer theme ids. Map the emitted text to a
        stable pseudo-index via short hash so the snapshot reflects what was
        actually used. The same text always maps to the same index, so the
        tracker's "no repeat in last N" semantics stay meaningful.
        """
        import hashlib as _hl

        digest = _hl.sha256(text.encode("utf-8", errors="ignore")).hexdigest()
        pseudo_idx = int(digest[:8], 16) % len(STAGE_FALLBACK_LINES)
        with self._lock:
            self._recent_fallback_themes.append(pseudo_idx)
            if len(self._recent_fallback_themes) > 3:
                self._recent_fallback_themes.pop(0)
            self._recent_fallback_texts.append(text)
            if len(self._recent_fallback_texts) > 4:
                self._recent_fallback_texts.pop(0)

    def _recover_fallback_line(
        self,
        source_text: str,
        *,
        violation_kind: str,
        viewer_name: str = "viewer",
    ) -> str:
        """Use STAGE-9L contextual fallback recovery when available.

        Recovery is deterministic and public-safe. If anything goes wrong, keep
        the older varied fallback behavior.

        STAGE-7H reconciliation: even when 9L is used, the 7H variety tracker
        (`_recent_fallback_themes`) must be updated, and consecutive calls with
        the same seed must not produce the same text. If 9L emits the exact
        same line as the previous fallback, fall back to `_next_fallback_varied`
        to keep the room from sounding like a broken record.
        """
        try:
            from nana.runtime.public_fallback_recovery import recover_public_fallback

            result = recover_public_fallback(
                source_text=source_text,
                viewer_text=source_text,
                violation_kinds=(violation_kind,),
                seed=f"{violation_kind}:{viewer_name}:{source_text}",
            )
            if result.text.strip():
                # 9L is deterministic — on repeat calls with stable seed it
                # returns the exact same line. De-dup against the recent
                # fallback window: if 9L's line is something we already
                # emitted recently, rotate through the legacy varied pool.
                chosen = result.text
                if chosen in self._recent_fallback_texts:
                    chosen = self._next_fallback_varied()
                self._record_fallback_theme(chosen)
                return chosen
        except Exception:
            pass
        chosen = self._next_fallback_varied()
        self._record_fallback_theme(chosen)
        return chosen

    def classify_public_input(self, text: str) -> str:
        """Classify a public viewer message.

        Returns:
        - "backstage_command" if text starts with a known operator command.
        - "empty" if blank.
        - "ok" otherwise.
        """
        if not text:
            return "empty"
        stripped = text.strip()
        if not stripped:
            return "empty"
        lowered = stripped.lower()
        for cmd in BACKSTAGE_COMMANDS:
            cmd_l = cmd.lower()
            if lowered == cmd_l or lowered.startswith(cmd_l + " ") or lowered.startswith(cmd_l + "\t"):
                return "backstage_command"
        return "ok"

    def public_command_firewall(
        self,
        text: str,
        *,
        social_decision_action: str = "full_reply",
    ) -> str | None:
        """If `text` is a backstage command, return a stage-safe in-character line.

        Returns None if the input is NOT a backstage command.
        Returns a fallback line if the input is a backstage command AND the
        social decision would normally trigger a full reply.
        """
        if self.classify_public_input(text) != "backstage_command":
            return None
        with self._lock:
            self._stats["backstage_command_blocks"] += 1
            self._stats["fallback_used"] += 1
            self._last_action = "firewall"
        return self._recover_fallback_line(text, violation_kind="backstage_command")

    def _rewrite_assistant_tone(self, text: str) -> tuple[str, bool]:
        cleaned = text
        matched = False
        for pattern, replacement in ASSISTANT_TONE_PATTERNS:
            updated = pattern.sub(replacement, cleaned)
            if updated != cleaned:
                cleaned = updated
                matched = True
        if matched:
            cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
            cleaned = re.sub(r"^[\s,.;:!?\-]+", "", cleaned)
            if cleaned and not cleaned[0].isupper():
                cleaned = cleaned[0].upper() + cleaned[1:]
        return cleaned, matched

    def _check_operator_tone(self, text: str) -> tuple[bool, StageIdentityViolation | None]:
        for pattern in OPERATOR_TONE_PATTERNS:
            if pattern.search(text):
                return True, StageIdentityViolation(
                    kind="operator_tone",
                    detail=f"operator word detected: {pattern.pattern!r}",
                    action="fallback",
                )
        return False, None

    def _check_ba_con(self, text: str) -> tuple[bool, StageIdentityViolation | None]:
        for pattern in BA_CON_PATTERNS:
            if pattern.search(text):
                return True, StageIdentityViolation(
                    kind="ba_con",
                    detail=f"Ba/con leak: {pattern.pattern!r}",
                    action="fallback",
                )
        return False, None

    def rewrite_public_stage_reply(
        self,
        reply: str,
        *,
        viewer_name: str = "viewer",
    ) -> StageIdentityResult:
        """Apply stage-identity rewrites/fallbacks to a public reply.

        Order:
        1. Ba/con → fallback (severe)
        2. Operator tone → fallback (severe)
        3. Assistant-tone rewrites (strip service-bot phrasing)
        4. Residue cleanup: if rewrite leaves too-short / punctuation-only
           text, replace with varied stage fallback.
        5. Name-use throttle: avoid mentioning viewer name every reply.
        6. Variety: prefer different fallback theme from recent replies.
        """
        original = reply or ""
        if not original.strip():
            with self._lock:
                self._stats["passed_clean"] += 1
                self._last_action = "passed"
            return StageIdentityResult(
                text=original,
                original=original,
                violations=[],
                actions=["passed"],
                was_firewalled=False,
            )

        violations: list[StageIdentityViolation] = []
        actions: list[str] = []
        current = original

        # Step 1: Ba/con — severe → varied fallback
        ba_con_hit, ba_con_violation = self._check_ba_con(current)
        if ba_con_hit and ba_con_violation:
            with self._lock:
                self._stats["ba_con_blocks"] += 1
                self._stats["fallback_used"] += 1
                self._stats["variety_swaps"] += 1
                self._last_action = "fallback"
            fallback_line = self._recover_fallback_line(
                original,
                violation_kind="ba_con",
                viewer_name=viewer_name,
            )
            self._record_name_used(False)
            return StageIdentityResult(
                text=fallback_line,
                original=original,
                violations=[ba_con_violation],
                actions=["fallback", "fallback_recovery"],
                was_firewalled=False,
            )

        # Step 2: Operator tone — severe → varied fallback
        op_hit, op_violation = self._check_operator_tone(current)
        if op_hit and op_violation:
            with self._lock:
                self._stats["operator_tone_blocks"] += 1
                self._stats["fallback_used"] += 1
                self._stats["variety_swaps"] += 1
                self._last_action = "fallback"
            fallback_line = self._recover_fallback_line(
                original,
                violation_kind="operator_tone",
                viewer_name=viewer_name,
            )
            self._record_name_used(False)
            return StageIdentityResult(
                text=fallback_line,
                original=original,
                violations=[op_violation],
                actions=["fallback", "fallback_recovery"],
                was_firewalled=False,
            )

        # Step 3: Assistant-tone rewrites
        current, rewritten = self._rewrite_assistant_tone(current)
        if rewritten:
            violations.append(StageIdentityViolation(
                kind="assistant_tone",
                detail="assistant/service-bot phrase removed",
                action="rewrite",
            ))
            actions.append("assistant_rewrite")
            with self._lock:
                self._stats["assistant_tone_rewrites"] += 1
                self._stats["stage_rewrites"] += 1
                self._last_action = "rewrite"

        # Step 4: Residue cleanup — if rewrite left too-short / punctuation-only
        # text, replace with varied stage fallback instead of leaking nonsense.
        if rewritten and self._is_residue(current):
            residue_violation = StageIdentityViolation(
                kind="residue_after_rewrite",
                detail=f"rewrite left residue '{current.strip()!r}', replaced with varied fallback",
                action="fallback",
            )
            with self._lock:
                self._stats["residue_replaced"] += 1
                self._stats["fallback_used"] += 1
                self._stats["variety_swaps"] += 1
                self._last_action = "fallback"
            fallback_line = self._recover_fallback_line(
                original,
                violation_kind="residue_after_rewrite",
                viewer_name=viewer_name,
            )
            self._record_name_used(False)
            return StageIdentityResult(
                text=fallback_line,
                original=original,
                violations=violations + [residue_violation],
                actions=actions + ["fallback", "fallback_recovery"],
                was_firewalled=False,
            )

        # Step 5: Name-use throttle (only when there's actual text to throttle)
        name_stripped = False
        if current.strip():
            current, name_stripped = self._throttle_viewer_name(current, viewer_name)
            if name_stripped:
                violations.append(StageIdentityViolation(
                    kind="name_throttle",
                    detail=f"viewer name '{viewer_name}' throttled for variety",
                    action="rewrite",
                ))
                actions.append("name_throttle")
                with self._lock:
                    self._stats["name_throttle_stripped"] += 1
                    self._stats["stage_rewrites"] += 1
                    self._last_action = "rewrite"

        # Step 6: Final empty check (after name throttle or residue)
        if not current.strip():
            empty_violation = StageIdentityViolation(
                kind="empty_after_stage_guard",
                detail="reply became empty after stage guard, using fallback",
                action="fallback",
            )
            with self._lock:
                self._stats["fallback_used"] += 1
                self._stats["variety_swaps"] += 1
                self._last_action = "fallback"
            fallback_line = self._recover_fallback_line(
                original,
                violation_kind="empty_after_stage_guard",
                viewer_name=viewer_name,
            )
            self._record_name_used(False)
            return StageIdentityResult(
                text=fallback_line,
                original=original,
                violations=violations + [empty_violation],
                actions=actions + ["fallback", "fallback_recovery"],
                was_firewalled=False,
            )

        # Track whether this reply used the viewer name
        self._record_name_used(viewer_name.lower() in current.lower())

        if not violations:
            with self._lock:
                self._stats["passed_clean"] += 1
                self._last_action = "passed"
            return StageIdentityResult(
                text=current,
                original=original,
                violations=[],
                actions=["passed"],
                was_firewalled=False,
            )

        return StageIdentityResult(
            text=current,
            original=original,
            violations=violations,
            actions=actions,
            was_firewalled=False,
        )

    def apply_full_stage_pipeline(
        self,
        text: str,
        *,
        viewer_name: str = "viewer",
        social_decision_action: str = "full_reply",
    ) -> StageIdentityResult:
        """Apply the full STAGE-7G pipeline:

        1. If input is a backstage command, firewalled.
        2. Otherwise, rewrite assistant/operator tone.
        """
        # Step 1: command firewall (operates on INPUT, not reply)
        # If we got here, the reply has already been generated. But for completeness,
        # check the viewer request to see if the original ask was a backstage command.
        # The bridge is responsible for calling public_command_firewall() BEFORE
        # generating the LLM reply; this method focuses on the reply side.
        return self.rewrite_public_stage_reply(text, viewer_name=viewer_name)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            stats = dict(self._stats)
            recent_themes = list(self._recent_fallback_themes)
            recent_names = list(self._recent_name_used)
        return {
            "phase": PHASE,
            "enabled": True,
            "read_only": True,
            "can_act": False,
            "public_command_firewall": True,
            "backstage_command_count": len(BACKSTAGE_COMMANDS),
            "stage_fallback_count": len(STAGE_FALLBACK_LINES),
            "stats": stats,
            "last_action": self._last_action,
            "variety": {
                "recent_fallback_themes": recent_themes,
                "recent_fallback_window": len(recent_themes),
                "recent_name_use": recent_names,
                "recent_name_window": len(recent_names),
            },
            "safety": {
                "can_act": False,
                "voice_call": False,
                "vts_call": False,
                "obs_call": False,
                "game_input": False,
                "memory_persistence": False,
                "private_owner_memory_leak": False,
            },
        }

    def clear(self) -> dict[str, Any]:
        with self._lock:
            self._fallback_index = 0
            for key in self._stats:
                self._stats[key] = 0
            self._last_action = "clean"
            self._recent_fallback_themes.clear()
            self._recent_name_used.clear()
            self._recent_fallback_texts.clear()
        return {"cleared": True}


_GUARD = PublicStageIdentityGuard()


def get_public_stage_identity_guard() -> PublicStageIdentityGuard:
    return _GUARD


def public_stage_identity_status_lines() -> list[str]:
    snap = get_public_stage_identity_guard().snapshot()
    stats = dict(snap.get("stats") or {})
    variety = dict(snap.get("variety") or {})
    lines = [
        "🎭 Public Stage Identity",
        f"  Mode: {PHASE} | enabled=True | can_act=False | read_only=True",
        f"  Public command firewall: enabled={snap.get('public_command_firewall')}",
        (
            f"  Backstage commands blocked: "
            f"{snap.get('backstage_command_count')} | "
            f"fallback lines: {snap.get('stage_fallback_count')}"
        ),
        (
            "  Stats: "
            f"command_blocks={stats.get('backstage_command_blocks', 0)} | "
            f"assistant_rewrites={stats.get('assistant_tone_rewrites', 0)} | "
            f"operator_blocks={stats.get('operator_tone_blocks', 0)} | "
            f"ba_con_blocks={stats.get('ba_con_blocks', 0)} | "
            f"residue_replaced={stats.get('residue_replaced', 0)} | "
            f"name_throttle_stripped={stats.get('name_throttle_stripped', 0)} | "
            f"variety_swaps={stats.get('variety_swaps', 0)} | "
            f"stage_rewrites={stats.get('stage_rewrites', 0)} | "
            f"fallback={stats.get('fallback_used', 0)} | "
            f"clean={stats.get('passed_clean', 0)}"
        ),
        (
            "  Variety: "
            f"recent_themes={variety.get('recent_fallback_window', 0)} | "
            f"recent_names={variety.get('recent_name_window', 0)}"
        ),
        f"  Last action: {snap.get('last_action')}",
        (
            "  Safety: "
            f"can_act=False | voice_call=False | vts_call=False | obs_call=False | "
            f"game_input=False | no_private_owner_leak=True | no_disk=True"
        ),
        "  Live verify: /public-stage-status | /stage-identity-status",
    ]
    return lines
