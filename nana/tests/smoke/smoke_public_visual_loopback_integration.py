"""Local Core-to-NanaApp public visual contract smoke.

This check briefly binds the dedicated loopback route and invokes the real
NanaApp consumer through Node. Unity is a recording fake. No provider, TTS,
audio device, OBS, YouTube, browser, credential, or production data is used.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import urllib.request


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nana.runtime.avatar_intent_gateway import PublicVisualSignalServer
from nana.runtime.stream_public_visual_signals import (
    PublicVisualPlaybackIdentity,
    PublicVisualSignalStore,
)


NODE_CONSUMER = r"""
const { createObsSignalConsumer } = await import('__PUBLIC_OBS_SIGNALS_URI__');
const calls = [];
const fetchImpl = (url, options) => fetch(url, {
  ...options,
  headers: { ...options.headers, Origin: 'http://127.0.0.1:5174' },
});
const consumer = createObsSignalConsumer({
  fetchImpl,
  sendUnity(objectName, method, value) {
    calls.push({ objectName, method, payload: JSON.parse(value) });
    return true;
  },
});
const accepted = await consumer.pollOnce();
const snapshot = consumer.snapshot();
consumer.stop();
console.log(JSON.stringify({ accepted, snapshot, calls }));
"""


def main() -> None:
    store = PublicVisualSignalStore("youtube-voice-integration")
    identity = PublicVisualPlaybackIdentity(
        "youtube-voice-integration",
        "cum5-integration-playback",
        "attempt-integration",
        "event-integration",
    )
    server = PublicVisualSignalServer(store)
    assert store.begin_playback(identity, expression_action="happy")
    assert store.publish_frame(
        identity,
        sequence=1,
        open_value=0.42,
        energy=0.12,
        viseme="aa",
        speaking=True,
    )
    assert server.start(), server.last_error
    try:
        request = urllib.request.Request(
            "http://127.0.0.1:8766/v1/avatar/public-signals?after=0",
            headers={"Origin": "http://127.0.0.1:5174"},
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            envelope = json.loads(response.read().decode("utf-8"))
            cors = response.headers.get("Access-Control-Allow-Origin")
        assert cors == "http://127.0.0.1:5174", cors
        assert envelope["current"]["playback_id"] == identity.playback_id, envelope

        completed = subprocess.run(
            ["node", "--input-type=module", "-e", NODE_CONSUMER.replace("__PUBLIC_OBS_SIGNALS_URI__", (ROOT / "components/nana-app/src/obsSignals.js").as_uri())],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        result = json.loads(completed.stdout.strip().splitlines()[-1])
        methods = [call["method"] for call in result["calls"]]
        assert result["accepted"] is True, result
        assert result["snapshot"]["connected"] is True, result
        assert "SetMouthVisemeJson" in methods, result
        assert "ReceiveAvatarIntentJson" in methods, result
        assert result["calls"][-1]["method"] == "SetMouthVisemeJson", result
        assert result["calls"][-1]["payload"]["speaking"] is False, result
        print(
            json.dumps(
                {
                    "status": "pass",
                    "protocol": envelope["protocol"],
                    "cors": cors,
                    "playback_id": envelope["current"]["playback_id"],
                    "unity_methods": methods,
                    "consumer_connected_before_stop": result["snapshot"]["connected"],
                },
                separators=(",", ":"),
            )
        )
    finally:
        store.shutdown()
        server.stop()


if __name__ == "__main__":
    main()
