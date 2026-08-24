"""Chunking the reply stream — the thing that decides how soon speech starts."""

import unittest

from handset_service.tts import speakable_chunks


async def tokens(text, size=3):
    """Dribble text out the way a model streams it."""
    for i in range(0, len(text), size):
        yield text[i : i + size]


async def collect(text):
    return [chunk async for chunk in speakable_chunks(tokens(text))]


class SpeakableChunkTests(unittest.IsolatedAsyncioTestCase):
    async def test_sentences_come_out_one_at_a_time(self):
        chunks = await collect("Tomorrow should be sunny. Around twenty three degrees. ")
        self.assertEqual(
            chunks, ["Tomorrow should be sunny.", "Around twenty three degrees."])

    async def test_first_phrase_may_break_at_a_clause(self):
        """Speech starts sooner if the first chunk can end at a comma — that
        early break is worth a few hundred milliseconds on every reply."""
        chunks = await collect("Yes, it's on the dock, charging away happily.")
        self.assertEqual(chunks[0], "Yes, it's on the dock,")
        self.assertEqual(chunks[1], "charging away happily.")

    async def test_decimals_are_not_sentence_ends(self):
        chunks = await collect("The battery is at 27.9 volts. Fine.")
        self.assertEqual(chunks[0], "The battery is at 27.9 volts.")

    async def test_trailing_text_without_punctuation_is_still_spoken(self):
        self.assertEqual(await collect("no full stop here"), ["no full stop here"])

    async def test_empty_stream_yields_nothing(self):
        self.assertEqual(await collect(""), [])


if __name__ == "__main__":
    unittest.main()
