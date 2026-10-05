"""Provider/audio-free contract for Nana's public ElevenLabs model choice."""
from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from nana import config
from nana.voice import engine as engine_module


def main() -> None:
    engine = engine_module.VoiceEngine.__new__(engine_module.VoiceEngine)

    assert config.ELEVEN_PUBLIC_TTS_MODEL == "eleven_v3"
    assert engine._pick_tts_model("Nana public test") == "eleven_v3"

    v3_key = engine._voice_cache_key("Nana public test", "mp3_44100_128")
    expected_v3_key = engine_module.hashlib.sha256(
        "|".join((
            str(config.VOICE_ID),
            "eleven_v3",
            "mp3_44100_128",
            "nana public test",
        )).encode("utf-8")
    ).hexdigest()[:24]
    assert v3_key == expected_v3_key

    original_model = engine_module.ELEVEN_PUBLIC_TTS_MODEL
    try:
        engine_module.ELEVEN_PUBLIC_TTS_MODEL = "eleven_v4"
        assert engine._voice_cache_key("Nana public test", "mp3_44100_128") != v3_key
    finally:
        engine_module.ELEVEN_PUBLIC_TTS_MODEL = original_model

    assert engine_module.PRIVATE_VOICE_TTD_MODEL == "eleven_v3"
    print("smoke_public_tts_model: PASS (public=eleven_v3, private_ttd=eleven_v3)")


if __name__ == "__main__":
    main()
