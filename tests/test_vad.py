"""Turn taking — does a burst of speech become exactly one utterance?"""

import unittest

from handset_service import config
from handset_service.vad import Segmenter

FRAME = b"\x01\x00" * config.FRAME_SAMPLES  # one 80 ms frame of (fake) audio


class ScriptedVad:
    """A VAD that says 'speech' for the frames you tell it to."""

    def __init__(self, script) -> None:
        self.script = list(script)
        self.calls = 0

    def speech_prob(self, frame: bytes) -> float:
        speech = self.script[self.calls] if self.calls < len(self.script) else False
        self.calls += 1
        return 1.0 if speech else 0.0

    def reset(self) -> None:
        pass


def segmenter(script, **kwargs):
    return Segmenter(ScriptedVad(script), **kwargs)


class SegmenterTests(unittest.TestCase):
    def test_silence_alone_produces_nothing(self):
        seg = segmenter([False] * 20)
        self.assertEqual(seg.push(FRAME * 20), [])

    def test_speech_then_silence_is_one_utterance(self):
        # 2 silent, 5 speech, 9 silent (720 ms > the 700 ms hangover)
        seg = segmenter([False] * 2 + [True] * 5 + [False] * 9)
        utterances = seg.push(FRAME * 16)
        self.assertEqual(len(utterances), 1)
        # Everything from the preroll onward is kept, so the first syllable
        # survives the VAD's own reaction time.
        self.assertEqual(len(utterances[0]), 16 * config.FRAME_BYTES)
        self.assertFalse(seg.in_speech)

    def test_short_blip_is_discarded(self):
        # One 80 ms frame is below VAD_MIN_SPEECH_MS: a door bang, not a turn.
        seg = segmenter([False] + [True] + [False] * 10)
        self.assertEqual(seg.push(FRAME * 12), [])
        self.assertFalse(seg.in_speech, "a discarded blip must not leave us mid-utterance")

    def test_frames_are_resliced_from_arbitrary_chunks(self):
        seg = segmenter([False] * 2 + [True] * 5 + [False] * 9)
        stream = FRAME * 16
        out = []
        for i in range(0, len(stream), 999):  # deliberately not a frame multiple
            out += seg.push(stream[i : i + 999])
        self.assertEqual(len(out), 1)

    def test_long_utterance_is_capped(self):
        seg = segmenter([True] * 200, max_utterance_ms=400)  # 5 frames
        utterances = seg.push(FRAME * 20)
        # Someone who never stops talking still gets transcribed in pieces
        # rather than filling memory until the hangover that never comes.
        self.assertGreaterEqual(len(utterances), 1)
        self.assertLessEqual(len(utterances[0]), 6 * config.FRAME_BYTES)

    def test_reset_forgets_a_half_heard_utterance(self):
        seg = segmenter([True] * 10)
        seg.push(FRAME * 3)
        self.assertTrue(seg.in_speech)
        seg.reset()
        self.assertFalse(seg.in_speech)
        self.assertIsNone(seg.flush())

    def test_flush_ends_the_turn_early(self):
        seg = segmenter([True] * 10)
        seg.push(FRAME * 5)
        pcm = seg.flush()
        self.assertIsNotNone(pcm)
        self.assertFalse(seg.in_speech)


if __name__ == "__main__":
    unittest.main()
