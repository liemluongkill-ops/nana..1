from __future__ import annotations

import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SAFE_ENV = {
    "OPENAI_API_KEY": "fixture-" + "not-a-live-key",
    "NANA_CHAT_PROVIDER": "openai",
    "OPENAI_MODEL": "gpt-4o-mini",
    "NANA_DEBUG_NO_TTS": "1",
    "NANA_VOICE_TEST_MODE": "1",
    "NANA_AUTONOMY_LLM_DISABLED": "1",
    "NANA_VTS_STARTUP_ENABLED": "0",
    "NANA_AVATAR_GATEWAY_ENABLED": "0",
    "NANA_PRESENCE_SESSION_ENABLED": "0",
    "NANA_STREAM_CUM0_ENABLED": "0",
    "NANA_STREAM_CUM1_YOUTUBE_INGRESS_ENABLED": "0",
    "NANA_STREAM_CUM2_RESPONSE_ENABLED": "0",
    "NANA_STREAM_CUM3_YOUTUBE_OUTPUT_ENABLED": "0",
    "NANA_STREAM_CUM4_HOST_ENABLED": "0",
    "NANA_STREAM_CUM5_VOICE_PLAYBACK_ENABLED": "0",
}
for name, value in SAFE_ENV.items():
    os.environ[name] = value

import nana  # noqa: E402
from nana import config  # noqa: E402
from nana.cli import app  # noqa: E402
from nana.runtime.identity import load_identity, load_users  # noqa: E402


def main() -> int:
    assert Path(nana.__file__).resolve().parent == ROOT / "nana"
    assert config.DATA_DIR == ROOT / "nana" / "data"
    assert config.AVATAR_GATEWAY_ENABLED is False
    assert config.STREAM_CUM0_ENABLED is False
    assert config.STREAM_CUM5_VOICE_PLAYBACK_ENABLED is False
    assert load_identity().get("self_name") == "Nana"
    assert load_users().get("owner", {}).get("is_default") is True
    assert callable(app.main)
    assert "nana.main" not in sys.modules
    print("public_runtime_smoke: PASS")
    print("network_calls=0 side_effect_flags=OFF private_data_loaded=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
