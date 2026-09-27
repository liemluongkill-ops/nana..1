"""Offline face preset contract; no model, provider, playback or live gateway."""
from pathlib import Path
import json
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from nana.runtime.avatar_intent_gateway import (
    AvatarIntent, AvatarIntentArbiter, AvatarIntentError, FACE_PRESET_ACTIONS,
)


class FacePresetContract(unittest.TestCase):
    def test_catalog_and_gateway_agree(self):
        catalog = (
            Path(__file__).resolve().parents[3]
            / "components"
            / "avatar-unity-scripts"
            / "Assets"
            / "Nana"
            / "Expressions"
            / "seven_faces_v1"
            / "seven_faces.json"
        )
        data = json.loads(catalog.read_text(encoding='utf-8'))
        self.assertEqual({p['action'] for p in data['presets']}, set(FACE_PRESET_ACTIONS))
        for preset in data['presets']:
            intent = AvatarIntent.from_payload({'action': preset['action']})
            self.assertEqual(intent.kind, 'expression')
            self.assertEqual(intent.mask, 'face')
            self.assertEqual(intent.interrupt_policy, 'replace')
            self.assertAlmostEqual(intent.duration_s, preset['duration'])
            self.assertAlmostEqual(preset['duration'] - preset['enter'] - preset['exit'], 1)
            self.assertLessEqual(preset['exit'], 1)
            for channel in preset['channels']:
                name = channel['shape']
                self.assertFalse(name.startswith(('eye_close', 'eye_look_', 'vrc.v_')))
                self.assertNotIn(name, ('mouth_a1','mouth_i1','mouth_o1','mouth_0'))

    def test_finite_duration(self):
        for action in FACE_PRESET_ACTIONS:
            normal=AvatarIntent.from_payload({'action':action})
            for duration in (0, 60):
                intent=AvatarIntent.from_payload({'action':action,'duration_s':duration})
                self.assertEqual(intent.duration_s,normal.duration_s)
            for duration in (float('nan'),float('inf'),-1):
                with self.assertRaises(AvatarIntentError):
                    AvatarIntent.from_payload({'action':action,'duration_s':duration})

    def test_expression_does_not_cancel_wave(self):
        arbiter=AvatarIntentArbiter()
        wave=AvatarIntent.from_payload({'action':'wave','intent_id':'wave-1'})
        face=AvatarIntent.from_payload({'action':'shy_crying','intent_id':'face-1'})
        next_face=AvatarIntent.from_payload({'action':'heart_happy','intent_id':'face-2'})
        self.assertEqual(arbiter.submit(wave).status,'accepted')
        self.assertEqual(arbiter.submit(face).status,'accepted')
        self.assertEqual(arbiter.submit(next_face).status,'accepted')
        self.assertEqual(arbiter.receipt(face.intent_id).status,'cancelled')
        self.assertEqual(arbiter.receipt(wave.intent_id).status,'accepted')
        settle=AvatarIntent.from_payload({'action':'settle','intent_id':'settle-1'})
        arbiter.submit(settle)
        self.assertEqual(arbiter.receipt(wave.intent_id).status,'cancelled')
        self.assertEqual(arbiter.receipt(next_face.intent_id).status,'cancelled')

    def test_raw_controls_still_rejected(self):
        for extra in ({'bone_path':'Head'},{'weights':{'mouth_a1':100}},{'mask':'head'},{'action':'custom-face'}):
            with self.assertRaises(AvatarIntentError):
                AvatarIntent.from_payload({'action':'heart_happy',**extra})


if __name__ == '__main__':
    unittest.main(verbosity=2)
