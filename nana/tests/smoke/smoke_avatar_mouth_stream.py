"""Read/write-free checks for the semantic latest-mouth sample contract."""

import sys
import json
from urllib.request import urlopen
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nana.runtime.avatar_mouth_stream import AvatarMouthStream, STALE_AFTER_MS


def test_bounds_and_silence():
    stream = AvatarMouthStream()
    first = stream.publish(open_value=2, energy=-1, viseme="unknown")
    assert first["open"] == 1.0
    assert first["energy"] == 0.0
    assert first["viseme"] == "aa"
    assert first["speaking"] is True
    silent = stream.silence()
    assert silent["open"] == 0.0
    assert silent["viseme"] == "sil"
    assert silent["speaking"] is False


def test_latest_sample_and_cursor():
    stream = AvatarMouthStream()
    sample = stream.publish(open_value=.2, energy=.15, viseme="oh")
    response = stream.snapshot(0)
    assert response["ok"] is True
    assert response["changed"] is True
    assert response["cursor"] == sample["sequence"]
    assert response["sample"]["viseme"] == "oh"
    assert response["stale_after_ms"] == STALE_AFTER_MS
    assert stream.snapshot(response["cursor"])["changed"] is False


def test_gateway_endpoint():
    from nana.runtime.avatar_intent_gateway import AvatarIntentGateway, RecordingTransport

    gateway = AvatarIntentGateway(enabled=True, host="127.0.0.1", port=0, transport=RecordingTransport())
    assert gateway.start()
    try:
        gateway.mouth_stream.publish(open_value=.3, energy=.2, viseme="ih")
        with urlopen(f"http://127.0.0.1:{gateway.bound_port}/v1/avatar/mouth", timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert payload["protocol"] == "nana.avatar.mouth.v1"
        assert payload["sample"]["viseme"] == "ih"
    finally:
        gateway.stop()


def test_playback_envelope_retains_dynamics():
    stream = AvatarMouthStream()
    values = [stream.publish_pcm_level(x)["open"] for x in (0, .002, .02, .08, .15, .3)]
    assert values[0] == values[1] == 0
    assert 0 < values[2] < values[3] < values[4] < values[5] < 1
    assert values[3] * .4 < .3
    for value in (float("nan"), float("inf"), -1, None):
        assert stream.publish_pcm_level(value)["open"] == 0


def test_pcm_observer_does_not_change_legacy_mouth():
    from nana.voice.lipsync import LipsyncManager
    manager = LipsyncManager()
    levels, legacy = [], []
    manager.set_pcm_level_callback(levels.append)
    manager.set_mouth_update_callback(legacy.append)
    manager._set_mouth(.9, pcm_level=.08)
    assert manager.mouth == .9 and levels == [.08] and legacy == [.9]
    manager._set_mouth(0)
    assert levels[-1] == 0 and legacy[-1] == 0
    def broken(_):
        raise RuntimeError("optional observer failed")
    manager.set_pcm_level_callback(broken)
    manager._set_mouth(.5, pcm_level=.1)
    assert manager.mouth == .5 and manager._pcm_level_callback is None


if __name__ == "__main__":
    for test in (test_bounds_and_silence, test_latest_sample_and_cursor, test_gateway_endpoint,
                 test_playback_envelope_retains_dynamics, test_pcm_observer_does_not_change_legacy_mouth):
        test()
        print("PASS", test.__name__)
