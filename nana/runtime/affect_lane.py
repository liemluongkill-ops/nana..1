"""Lane-aware affect projection for Nana.

CORE-AFFECT-LANE-1 keeps Nana as one character while preventing private-owner
emotion from leaking unchanged into public stage replies.

This module is pure/code-only:
- no LLM call
- no disk writes
- no TTS/VTS/OBS
- no game input
"""

from __future__ import annotations

from dataclasses import dataclass

PHASE = "CORE-AFFECT-LANE-1"


@dataclass(frozen=True)
class LaneAffect:
    affection: float
    annoyance: float
    playfulness: float
    assertiveness: float
    intimacy: float
    warmth: float
    lane: str
    guidance: str

    def as_emotion_dict(self) -> dict:
        return {
            "affection": self.affection,
            "annoyance": self.annoyance,
            "playfulness": self.playfulness,
            "assertiveness": self.assertiveness,
            "intimacy": self.intimacy,
            "warmth": self.warmth,
        }


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        n = 0.0
    return max(low, min(high, n))


def normalize_lane(lane: str | None) -> str:
    raw = str(lane or "").strip()
    aliases = {
        "private": "private_owner",
        "private_owner": "private_owner",
        "public": "public_stage",
        "public_viewer": "public_stage",
        "public_stage": "public_stage",
        "operator": "operator_backstage",
        "operator_backstage": "operator_backstage",
        "bridge_system": "operator_backstage",
    }
    return aliases.get(raw, "private_owner")


def project_affect(emotion: dict | None, lane: str | None) -> LaneAffect:
    """Project global emotion into the active lane.

    Private keeps Nana's warmth with Ba. Public lowers intimacy and turns that
    energy into playful/assertive stage presence. Operator is clear and low-drama.
    """

    lane_key = normalize_lane(lane)
    raw = dict(emotion or {})
    affection = _clamp(raw.get("affection", 0.5))
    annoyance = _clamp(raw.get("annoyance", 0.0))
    playfulness = _clamp(raw.get("playfulness", 0.5))

    if lane_key == "public_stage":
        public_warmth = _clamp(min(0.58, max(0.32, affection * 0.55 + 0.12)))
        public_play = _clamp(max(playfulness, 0.48) + 0.10)
        assertiveness = _clamp(0.46 + annoyance * 0.25 + max(0.0, public_play - 0.55) * 0.20)
        return LaneAffect(
            affection=public_warmth,
            annoyance=_clamp(min(0.45, annoyance)),
            playfulness=public_play,
            assertiveness=assertiveness,
            intimacy=0.22,
            warmth=public_warmth,
            lane=lane_key,
            guidance=(
                "Public affect: warm but not intimate; playful/assertive stage voice. "
                "Do not sound romantic, service-like, or privately attached to a viewer."
            ),
        )

    if lane_key == "operator_backstage":
        return LaneAffect(
            affection=_clamp(min(0.35, affection * 0.35)),
            annoyance=_clamp(min(0.35, annoyance)),
            playfulness=_clamp(min(0.35, playfulness * 0.45)),
            assertiveness=0.62,
            intimacy=0.05,
            warmth=0.25,
            lane=lane_key,
            guidance="Operator affect: clear, calm, low-drama; technical without turning into a service bot.",
        )

    return LaneAffect(
        affection=affection,
        annoyance=annoyance,
        playfulness=playfulness,
        assertiveness=_clamp(0.30 + annoyance * 0.20),
        intimacy=0.86,
        warmth=_clamp(max(0.60, affection)),
        lane="private_owner",
        guidance="Private affect: warm and familiar with Ba; still avoid inventing memories or over-agreeing.",
    )


def format_affect_prompt_block(affect: LaneAffect) -> str:
    return (
        "Lane affect projection:\n"
        f"- lane={affect.lane}\n"
        f"- warmth={affect.warmth:.2f}\n"
        f"- intimacy={affect.intimacy:.2f}\n"
        f"- assertiveness={affect.assertiveness:.2f}\n"
        f"- guidance={affect.guidance}"
    )


def status_lines() -> list[str]:
    public = project_affect({"affection": 0.90, "annoyance": 0.05, "playfulness": 0.50}, "public_stage")
    private = project_affect({"affection": 0.90, "annoyance": 0.05, "playfulness": 0.50}, "private_owner")
    operator = project_affect({"affection": 0.90, "annoyance": 0.05, "playfulness": 0.50}, "operator_backstage")
    return [
        "🎚️ Lane Affect Status",
        f"  Phase: {PHASE} | read_only=True | can_act=False",
        (
            "  Private: "
            f"affection={private.affection:.2f} | intimacy={private.intimacy:.2f} | "
            f"playfulness={private.playfulness:.2f} | assertiveness={private.assertiveness:.2f}"
        ),
        (
            "  Public: "
            f"affection={public.affection:.2f} | intimacy={public.intimacy:.2f} | "
            f"playfulness={public.playfulness:.2f} | assertiveness={public.assertiveness:.2f}"
        ),
        (
            "  Operator: "
            f"affection={operator.affection:.2f} | intimacy={operator.intimacy:.2f} | "
            f"playfulness={operator.playfulness:.2f} | assertiveness={operator.assertiveness:.2f}"
        ),
        "  Rule: public stage converts private warmth into playful/assertive presence, not intimacy.",
        "  Safety: no LLM judge | no disk write | no live action | no TTS/VTS/OBS/game input",
    ]
