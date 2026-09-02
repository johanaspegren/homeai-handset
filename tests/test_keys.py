"""What the Zodiac's buttons do — with and without a handset on the line.

The case that matters most here is the one that has no call at all. A button is
part of the telephone rather than part of a conversation, and the two things the
buttons exist for (music on, room opened up) both happen with the handset in the
cradle. So the no-call path is the ordinary path, and it is tested as such.
"""

import unittest
from unittest import mock

from handset_service import config, keys, protocol
from tests.test_session import FakeTransport, make_call


class FakeChat:
    """Kiri, without a server. Records what the button actually said."""

    def __init__(self, reply="Playing something."):
        self.reply = reply
        self.said = []
        self.kwargs = []

    def __call__(self, phrase, **kwargs):
        self.said.append(phrase)
        self.kwargs.append(kwargs)

        async def tokens():
            yield self.reply

        return tokens()


class KeysInTheCradleTests(unittest.IsolatedAsyncioTestCase):
    """No call: the press still has to do its job."""

    async def test_a_bound_key_puts_its_phrase_to_kiri(self):
        chat = FakeChat()
        with mock.patch.dict(config.KEY_PHRASES, {"5": "put some music on"}, clear=True):
            reply = await keys.press("5", place="workshop", terminal="zodiac-01", chat=chat)
        self.assertEqual(chat.said, ["put some music on"])
        self.assertEqual(reply, "Playing something.")

    async def test_the_room_travels_with_the_press(self):
        """A button pressed in the workshop is a request from the workshop —
        otherwise the music starts wherever Spotify last woke up."""
        chat = FakeChat()
        with mock.patch.dict(config.KEY_PHRASES, {"5": "put some music on"}, clear=True):
            await keys.press("5", place="workshop", terminal="zodiac-01", chat=chat)
        self.assertEqual(chat.kwargs[0], {"terminal": "zodiac-01", "place": "workshop"})

    async def test_an_unbound_key_does_nothing_quietly(self):
        chat = FakeChat()
        with mock.patch.dict(config.KEY_PHRASES, {"5": "music"}, clear=True):
            self.assertIsNone(await keys.press("7", chat=chat))
        self.assertEqual(chat.said, [])

    async def test_the_star_key_opens_the_room_up(self):
        toggled = []

        async def toggle():
            toggled.append(True)
            return True

        await keys.press(config.HANDSFREE_KEY, toggle=toggle)
        self.assertEqual(len(toggled), 1)

    async def test_a_dead_brain_does_not_take_the_phone_down_with_it(self):
        def broken(phrase, **kwargs):
            async def tokens():
                raise RuntimeError("homeai-server is down")
                yield ""
            return tokens()

        with mock.patch.dict(config.KEY_PHRASES, {"5": "music"}, clear=True):
            self.assertIsNone(await keys.press("5", chat=broken))


class KeysDuringACallTests(unittest.IsolatedAsyncioTestCase):
    """A call is something a press decorates, not a precondition for it."""

    async def test_a_press_becomes_the_callers_turn(self):
        transport = FakeTransport()
        chat = FakeChat("Right you are.")
        call = make_call(transport, greeting="", chat=lambda text, **kw: chat(text))
        await call.start()
        with mock.patch.dict(config.KEY_PHRASES, {"5": "put some music on"}, clear=True):
            await call.on_control(protocol.message(protocol.KEY, key="5"))
            await call._turn
        # It went through the ordinary turn: the caller sees a transcript of
        # what the button said, and hears the answer.
        said = transport.of(protocol.TRANSCRIPT)
        self.assertEqual(said[-1]["text"], "put some music on")
        self.assertEqual(chat.said, ["put some music on"])
        self.assertTrue(transport.of(protocol.ASSISTANT_FINISHED))

    async def test_a_press_never_starts_a_second_turn_at_once(self):
        """Two answers down one earpiece is nobody's idea of a phone call."""
        transport = FakeTransport()
        call = make_call(transport, greeting="", chat=lambda text, **kw: FakeChat()(text))
        await call.start()
        with mock.patch.dict(config.KEY_PHRASES, {"5": "music", "6": "next"}, clear=True):
            await call.say("music")
            first = call._turn
            await call.say("next")
            self.assertIs(call._turn, first)
            await first

    async def test_a_press_before_the_call_is_up_is_ignored(self):
        transport = FakeTransport()
        call = make_call(transport, greeting="")
        await call.say("music")   # never started
        self.assertIsNone(call._turn)


if __name__ == "__main__":
    unittest.main()
