"""Pure reducer tests. Namespace packages avoid importing the live Nana facade."""
from __future__ import annotations

import importlib.util
from dataclasses import replace
from pathlib import Path
import sys
import types
import unittest

ROOT = Path(__file__).resolve().parents[2]


def _load_contracts():
    saved = {k: v for k, v in sys.modules.items() if k == "nana" or k.startswith("nana.")}
    for k in list(saved):
        del sys.modules[k]
    try:
        for name, folder in (("nana", ROOT), ("nana.runtime", ROOT / "runtime")):
            package = types.ModuleType(name)
            package.__path__ = [str(folder)]
            sys.modules[name] = package
        from nana.runtime.public_identity import CanonicalPublicIdentity
        from nana.runtime.public_context_boundary import PublicEventScope
        from nana.runtime.public_delivery_state import PublicDeliveryRecord, transition_delivery, start_delivery_attempt
        return CanonicalPublicIdentity, PublicEventScope, PublicDeliveryRecord, transition_delivery, start_delivery_attempt
    finally:
        for k in list(sys.modules):
            if k == "nana" or k.startswith("nana."):
                del sys.modules[k]
        sys.modules.update(saved)


Identity, Scope, Record, transition, retry = _load_contracts()


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.scope = Scope("youtube", "room", "session", "event", "Display",
                           Identity("youtube", "actor", "youtube:actor"))
        self.record = Record("event", "output-1", "generated", "attempt-1", 1,
                             "A public reply", self.scope, 100.0)

    def receipt(self, state, revision, **changes):
        return {"event_id": "event", "output_id": "output-1", "attempt_id": "attempt-1",
                "platform": "youtube", "room_id": "room", "stream_session_id": "session",
                "state": state, "revision": revision, "timestamp": 100.0 + revision, **changes}

    def advance(self, record, state, revision):
        return transition(record, state, revision, record.attempt_id, self.receipt(state, revision))

    def test_happy_path_requires_each_receipt(self):
        current = self.record
        for rev, state in enumerate(("published", "playback_started", "delivered"), 2):
            self.assertIs(transition(current, state, rev, current.attempt_id, {}), current)
            current = self.advance(current, state, rev)
            self.assertEqual(current.state, state)
        self.assertEqual(self.record.state, "generated")

    def test_cannot_skip_playback_or_reopen_terminal(self):
        published = self.advance(self.record, "published", 2)
        self.assertIs(self.advance(published, "delivered", 3), published)
        done = self.advance(self.advance(published, "playback_started", 3), "delivered", 4)
        for target in ("generated", "published", "playback_started", "interrupted", "delivered"):
            self.assertIs(self.advance(done, target, 5), done)

    def test_failure_from_each_nonterminal_is_terminal(self):
        states = [self.record]
        states.append(self.advance(states[-1], "published", 2))
        states.append(self.advance(states[-1], "playback_started", 3))
        for state in states:
            stopped = self.advance(state, "interrupted", state.revision + 1)
            self.assertEqual(stopped.state, "interrupted")
            self.assertIs(self.advance(stopped, "delivered", stopped.revision + 1), stopped)

    def test_valid_but_stale_and_duplicate_updates_are_noops(self):
        published = self.advance(self.record, "published", 8)
        self.assertIs(self.advance(published, "playback_started", 7), published)
        self.assertIs(self.advance(published, "published", 8), published)
        self.assertEqual(self.advance(published, "playback_started", 9).state, "playback_started")

    def test_wrong_scope_output_attempt_or_state_cannot_confirm(self):
        for field in ("event_id", "output_id", "attempt_id", "platform", "room_id", "stream_session_id", "state"):
            r = {**self.receipt("published", 2), field: "wrong"}
            self.assertIs(transition(self.record, "published", 2, "attempt-1", r), self.record)

    def test_new_attempt_does_not_change_old_record(self):
        stopped = self.advance(self.record, "interrupted", 2)
        self.assertIs(transition(stopped, "generated", 3, "attempt-2", {}), stopped)
        fresh = retry(stopped, attempt_id="attempt-2", output_id="output-2", revision=3, timestamp=105.)
        self.assertNotEqual(fresh.key, stopped.key)
        self.assertEqual(fresh.state, "generated")
        self.assertEqual(stopped.state, "interrupted")
        with self.assertRaises(ValueError):
            retry(stopped, attempt_id="attempt-1", output_id="output-2", revision=3, timestamp=105.)

    def test_invalid_revision_time_and_unknown_states(self):
        for revision in (-1, True, 1.5):
            self.assertIs(transition(self.record, "published", revision, "attempt-1", {}), self.record)
        for timestamp in (99., float("nan"), float("inf")):
            self.assertIs(transition(self.record, "published", 2, "attempt-1",
                                     self.receipt("published", 2, timestamp=timestamp)), self.record)
        self.assertIs(self.advance(self.record, "made_up", 2), self.record)

    def test_voice_only_uses_audio_receipts_without_publication(self):
        voice = replace(self.record, delivery_mode="voice_only")
        started = transition(voice, "playback_started", 2, voice.attempt_id,
                             self.receipt("playback_started", 2, delivery_mode="voice_only"))
        done = transition(started, "delivered", 3, voice.attempt_id,
                          self.receipt("delivered", 3, delivery_mode="voice_only"))
        self.assertEqual((started.state, done.state), ("playback_started", "delivered"))
        self.assertEqual(done.delivery_mode, "voice_only")
        for state in ("published", "delivered"):
            self.assertIs(transition(voice, state, 2, voice.attempt_id,
                                    self.receipt(state, 2, delivery_mode="voice_only")), voice)

    def test_delivery_mode_cannot_be_omitted_switched_or_unknown(self):
        voice = replace(self.record, delivery_mode="voice_only")
        for changes in ({}, {"delivery_mode": "published_then_voice"}, {"delivery_mode": "other"}):
            self.assertIs(transition(voice, "playback_started", 2, voice.attempt_id,
                                    self.receipt("playback_started", 2, **changes)), voice)
        self.assertIs(transition(self.record, "published", 2, self.record.attempt_id,
                                self.receipt("published", 2, delivery_mode="voice_only")), self.record)
        with self.assertRaises(ValueError):
            replace(self.record, delivery_mode="unknown")
        with self.assertRaises(ValueError):
            replace(voice, state="published")

    def test_voice_interruption_stale_receipt_and_retry_keep_mode(self):
        voice = replace(self.record, delivery_mode="voice_only")
        stopped = transition(voice, "interrupted", 2, voice.attempt_id,
                             self.receipt("interrupted", 2, delivery_mode="voice_only"))
        self.assertEqual(stopped.state, "interrupted")
        self.assertIs(transition(stopped, "playback_started", 3, voice.attempt_id,
                                self.receipt("playback_started", 3, delivery_mode="voice_only")), stopped)
        self.assertIs(transition(voice, "playback_started", 1, voice.attempt_id,
                                self.receipt("playback_started", 1, delivery_mode="voice_only")), voice)
        fresh = retry(stopped, attempt_id="attempt-2", output_id="output-2", revision=3, timestamp=105.)
        self.assertEqual(fresh.delivery_mode, "voice_only")
        self.assertNotEqual(fresh.key, stopped.key)


class SessionVoiceDeliveryTests(unittest.TestCase):
    def setUp(self):
        from smoke_memory_v2_phase1 import _isolated_nana_imports
        self.isolated = _isolated_nana_imports()
        self.isolated.__enter__()
        self.addCleanup(self.isolated.__exit__, None, None, None)
        from nana.runtime.public_context_boundary import PublicEventScope
        from nana.runtime.public_identity import CanonicalPublicIdentity
        from nana.runtime.public_delivery_state import PublicDeliveryRecord
        from nana.runtime.social_session import SocialSessionCache
        self.scope = PublicEventScope("youtube", "room", "session", "event", "Display",
                                      CanonicalPublicIdentity("youtube", "actor", "youtube:actor"))
        self.cache = SocialSessionCache(clock=lambda: 200.)
        self.cache.record_public_turn(scope=self.scope, text="A public question", attempt_id="ingress-1")
        self.record = PublicDeliveryRecord("event", "output-1", "generated", "voice-attempt-1", 0,
                                           "VOICE-REPLY-947", self.scope, 200., delivery_mode="voice_only")

    def project(self, record):
        self.cache.record_reply_context(scope=self.scope, delivery_record=record)
        return self.cache.format_public_room_context(scope=self.scope)

    def test_only_completed_voice_enters_public_conversation_once(self):
        self.assertNotIn("VOICE-REPLY-947", self.project(self.record))
        started = replace(self.record, state="playback_started", revision=1, updated_at=201.)
        self.assertNotIn("VOICE-REPLY-947", self.project(started))
        done = replace(started, state="delivered", revision=2, updated_at=202.)
        self.assertEqual(self.project(done).count("VOICE-REPLY-947"), 1)
        self.assertEqual(self.project(done).count("VOICE-REPLY-947"), 1)
        self.assertEqual(self.project(started).count("VOICE-REPLY-947"), 1)
        wrong_room = replace(self.scope, room_id="other-room")
        self.assertNotIn("VOICE-REPLY-947", self.cache.format_public_room_context(scope=wrong_room))

    def test_incomplete_or_mode_switched_voice_never_enters_conversation(self):
        for mode in ("missing_start", "interrupted", "switched_mode"):
            with self.subTest(mode=mode):
                record = replace(self.record, output_id=f"output-{mode}", attempt_id=f"attempt-{mode}")
                self.project(record)
                if mode == "interrupted":
                    self.project(replace(record, state="interrupted", revision=1, updated_at=201.))
                elif mode == "switched_mode":
                    self.project(replace(record, state="playback_started", revision=1,
                                         updated_at=201., delivery_mode="published_then_voice"))
                done = replace(record, state="delivered", revision=2, updated_at=202.)
                self.assertNotIn("VOICE-REPLY-947", self.project(done))


if __name__ == "__main__":
    unittest.main()
