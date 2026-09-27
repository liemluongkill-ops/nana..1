"""Latest-sample semantic mouth stream for the local avatar runtime.

This channel deliberately does not carry PCM, bone paths, or frame commands.
Voice playback publishes normalized energy/viseme intent; the avatar runtime
owns smoothing, limits, and the stale-sample fail-closed behavior.
"""

from __future__ import annotations

import math
import threading
import time
from typing import Any


PROTOCOL_NAME = "nana.avatar.mouth.v1"
STALE_AFTER_MS = 180


class AvatarMouthStream:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sequence = 0
        self._sample: dict[str, Any] = {
            "open": 0.0,
            "energy": 0.0,
            "viseme": "sil",
            "speaking": False,
            "timestamp_ms": int(time.time() * 1000),
            "sequence": 0,
        }

    def publish(self, *, open_value: float = 0.0, energy: float = 0.0,
                viseme: str = "aa", speaking: bool | None = None) -> dict[str, Any]:
        def bounded(value: Any) -> float:
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                return 0.0
            return max(0.0, min(1.0, parsed)) if math.isfinite(parsed) else 0.0

        normalized_open = bounded(open_value)
        normalized_energy = bounded(energy)
        active = bool(speaking) if speaking is not None else normalized_open > 0.001
        if not active or normalized_open <= 0.001:
            normalized_open = 0.0
            normalized_energy = 0.0
            normalized_viseme = "sil"
        else:
            normalized_viseme = str(viseme or "aa").strip().lower()
            if normalized_viseme not in {"aa", "ih", "ee", "oh", "ou", "nn", "sil"}:
                normalized_viseme = "aa"

        with self._lock:
            self._sequence += 1
            self._sample = {
                "open": normalized_open,
                "energy": normalized_energy,
                "viseme": normalized_viseme,
                "speaking": active,
                "timestamp_ms": int(time.time() * 1000),
                "sequence": self._sequence,
            }
            return dict(self._sample)

    def publish_energy(self, value: float) -> dict[str, Any]:
        normalized = max(0.0, min(1.0, float(value or 0.0)))
        return self.publish(open_value=normalized, energy=normalized, viseme="aa")

    def publish_pcm_level(self, value: float) -> dict[str, Any]:
        """Convert mean absolute playback amplitude to a soft-bounded speech envelope.

        No PCM bytes are retained. The 2D MouthOpen gain is not reused because
        it clips normal speech to 1.0 and flattens the 3D articulation.
        """
        try:
            level = float(value)
        except (ValueError, TypeError):
            level = 0.0
        if not math.isfinite(level):
            level = 0.0
        envelope = math.tanh(6.0 * max(0.0, min(1.0, level) - 0.003))
        # This is an already normalized envelope, not a second gain input.
        return self.publish(open_value=envelope, energy=0.0, viseme="aa")

    def silence(self) -> dict[str, Any]:
        return self.publish(open_value=0.0, energy=0.0, viseme="sil", speaking=False)

    def snapshot(self, after: int = 0) -> dict[str, Any]:
        try:
            cursor = max(0, int(after))
        except (TypeError, ValueError):
            cursor = 0
        with self._lock:
            sample = dict(self._sample)
            sequence = self._sequence
        return {
            "ok": True,
            "protocol": PROTOCOL_NAME,
            "cursor": sequence,
            "changed": sequence > cursor,
            "stale_after_ms": STALE_AFTER_MS,
            "sample": sample,
        }


_STREAM = AvatarMouthStream()


def get_avatar_mouth_stream() -> AvatarMouthStream:
    return _STREAM


__all__ = ["AvatarMouthStream", "PROTOCOL_NAME", "STALE_AFTER_MS", "get_avatar_mouth_stream"]
