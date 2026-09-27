"""Semantic event delivery, queue ownership and acknowledgements. No LLM/TTS calls."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from nana.runtime.avatar_intent_gateway import AvatarIntentGateway, RecordingTransport, AvatarIntent


def wait_for(predicate):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    assert predicate(), "dispatch timed out"


def test_events():
    for name in "curious happy nod listen think surprised shy playful wave shy_smile settle".split():
        intent = AvatarIntent.from_payload({"action": name})
        assert intent.duration_s > 0
        assert intent.to_wire()["intent"]["action"] == name


def test_queue_and_ack():
    gateway = AvatarIntentGateway(enabled=True, port=0, transport=RecordingTransport())
    assert gateway.start()
    try:
        first = gateway.submit_action("nod", duration_s=0)
        pending = gateway.submit_action("nod")
        assert pending.receipt.status == "queued"
        wait_for(lambda: len(gateway.commands_since()["commands"]) == 1)
        batch = gateway.commands_since()
        assert batch["commands"][0]["intent"]["intent_id"] == first.intent.intent_id
        assert gateway.commands_since(batch["cursor"])["commands"] == []
        assert gateway.acknowledge_payload({"intent_id": first.intent.intent_id, "status": "started"}).ok
        assert gateway.acknowledge_payload({"intent_id": first.intent.intent_id, "status": "finished"}).ok
        assert gateway.acknowledge_payload({"intent_id": first.intent.intent_id, "status": "finished"}).reason == "already_terminal"
        assert all(item["intent"]["intent_id"] != first.intent.intent_id for item in gateway.commands_since()["commands"])
        wait_for(lambda: len(gateway.commands_since(batch["cursor"])["commands"]) == 1)
        assert gateway.commands_since(batch["cursor"])["commands"][0]["intent"]["intent_id"] == pending.intent.intent_id
        # Facial expression can coexist with a head gesture.
        assert gateway.submit_action("happy").receipt.status == "accepted"
        third = gateway.submit_action("nod")
        assert third.receipt.status == "queued"
        settle = gateway.submit_action("settle")
        assert settle.ok
        assert gateway.arbiter.receipt(third.intent.intent_id).status == "cancelled"
        assert gateway.arbiter.receipt(pending.intent.intent_id).status == "cancelled"
    finally:
        gateway.stop()


def test_cursor_pagination():
    gateway = AvatarIntentGateway(enabled=True, port=0, transport=RecordingTransport())
    assert gateway.start()
    try:
        gateway.submit_action("nod")
        gateway.submit_action("happy")
        wait_for(lambda: gateway.commands_since()["cursor"] == 2)
        first = gateway.commands_since(limit=1)
        second = gateway.commands_since(first["cursor"], limit=1)
        assert len(first["commands"]) == len(second["commands"]) == 1
        assert first["cursor"] < second["cursor"]
    finally:
        gateway.stop()


def test_session_identity():
    first = AvatarIntentGateway(enabled=False, transport=RecordingTransport())
    second = AvatarIntentGateway(enabled=False, transport=RecordingTransport())
    assert first.commands_since()["session_id"] == first.commands_since()["session_id"]
    assert first.commands_since()["session_id"] != second.commands_since()["session_id"]


if __name__ == "__main__":
    for test in (test_events, test_queue_and_ack, test_cursor_pagination, test_session_identity):
        test()
        print("PASS", test.__name__)
