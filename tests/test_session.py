"""The call state machine, with fake speech and a fake Kiri.

No models, no sockets: the transport and the three slow stages are injected, so
these run in milliseconds and assert the things that actually matter on a
telephone — that it answers instantly, that it hands the turn back, and that
replacing the handset stops everything.
"""

import asyncio
import unittest

from handset_service import protocol
from handset_service.session import Call
from tests.test_vad import FRAME, ScriptedVad
from handset_service.vad import Segmenter


class FakeTransport:
    def __init__(self) -> None:
        self.messages = []
        self.audio = bytearray()

    async def send_json(self, message: dict) -> None:
        self.messages.append(message)

    async def send_audio(self, data: bytes) -> None:
        self.audio.extend(data)

    def types(self) -> list[str]:
        return [m["type"] for m in self.messages]

    def of(self, type_: str) -> list[dict]:
        return [m for m in self.messages if m["type"] == type_]


def make_call(transport, *, script=None, reply="Sunny tomorrow.", transcript="hello",
              greeting="Hello.", chat=None, synthesize=None, mute_log=None):
    async def fake_chat(text, **kwargs):
        for token in reply.split(" "):
            yield token + " "

    async def fake_mute(muted):
        # Never let a test reach the real homeai-voice and silence the workshop.
        if mute_log is not None:
            mute_log.append(muted)
        return True

    segmenter = Segmenter(ScriptedVad(script or []))
    return Call(
        transport.send_json,
        transport.send_audio,
        terminal_id="zodiac-test",
        transcribe=lambda pcm: transcript,
        chat=chat or fake_chat,
        synthesize=synthesize or (lambda text: b"\x00\x01" * 100),
        sample_rate=lambda: 22050,
        segmenter=segmenter,
        greeting=greeting,
        mute_voice=fake_mute,
    )


class CallTests(unittest.IsolatedAsyncioTestCase):
    async def test_lifting_the_handset_answers_immediately(self):
        transport = FakeTransport()
        call = make_call(transport)
        await call.start()
        await call._turn  # the greeting

        self.assertEqual(transport.messages[0]["type"], protocol.CALL_STARTED)
        # The client is told the real playback rate rather than guessing.
        self.assertEqual(transport.messages[0]["audio"]["rate"], 22050)
        self.assertIn(protocol.ASSISTANT_SPEAKING, transport.types())
        self.assertTrue(transport.audio, "the greeting should reach the earpiece")
        self.assertIn(protocol.LISTENING, transport.types())

    async def test_greeting_needs_no_model(self):
        """Answering a phone must never wait on an LLM."""
        called = False

        async def chat(text, **kwargs):
            nonlocal called
            called = True
            yield ""

        transport = FakeTransport()
        call = make_call(transport, chat=chat)
        await call.start()
        await call._turn
        self.assertFalse(called)

    async def test_a_spoken_turn_runs_the_whole_pipeline(self):
        transport = FakeTransport()
        # 2 silent frames, 5 of speech, 9 of silence -> one utterance
        call = make_call(transport, script=[False] * 2 + [True] * 5 + [False] * 9,
                         greeting="", transcript="what's the weather")
        await call.start()
        transport.messages.clear()
        transport.audio.clear()

        await call.on_audio(FRAME * 16)
        await call._turn

        self.assertEqual(transport.of(protocol.TRANSCRIPT)[0]["text"], "what's the weather")
        self.assertIn(protocol.ASSISTANT_SPEAKING, transport.types())
        self.assertIn(protocol.ASSISTANT_FINISHED, transport.types())
        self.assertTrue(transport.audio)
        # ...and the turn comes back to the caller.
        self.assertEqual(transport.types()[-1], protocol.LISTENING)

    async def test_mic_is_gated_while_the_assistant_speaks(self):
        """Half-duplex for now: the earpiece bleeding into the mouthpiece must
        not become the next question."""
        transport = FakeTransport()
        call = make_call(transport, script=[False] * 2 + [True] * 5 + [False] * 9,
                         greeting="")
        await call.start()
        await call.on_audio(FRAME * 16)
        gates = [m["active"] for m in transport.of(protocol.MIC)]
        self.assertEqual(gates[-1], False)
        await call._turn
        self.assertEqual([m["active"] for m in transport.of(protocol.MIC)][-1], True)

    async def test_audio_during_a_turn_is_ignored(self):
        transport = FakeTransport()
        call = make_call(transport, script=[True] * 100, greeting="")
        await call.start()
        call._accepting = False
        await call.on_audio(FRAME * 20)
        self.assertFalse(call._segmenter.in_speech)

    async def test_muting_stops_the_microphone(self):
        transport = FakeTransport()
        call = make_call(transport, script=[True] * 100, greeting="")
        await call.start()
        await call.on_control({"type": protocol.MUTE, "active": True})
        await call.on_audio(FRAME * 20)
        self.assertFalse(call._segmenter.in_speech)
        await call.on_control({"type": protocol.MUTE, "active": False})
        await call.on_audio(FRAME * 3)
        self.assertTrue(call._segmenter.in_speech)

    async def test_replacing_the_handset_cancels_a_reply_mid_sentence(self):
        started = asyncio.Event()

        async def slow_chat(text, **kwargs):
            started.set()
            await asyncio.sleep(30)  # a long, rambling answer
            yield "never"

        transport = FakeTransport()
        call = make_call(transport, script=[False] * 2 + [True] * 5 + [False] * 9,
                         greeting="", chat=slow_chat)
        await call.start()
        await call.on_audio(FRAME * 16)
        await asyncio.wait_for(started.wait(), timeout=1)

        await asyncio.wait_for(call.hang_up(), timeout=1)
        self.assertFalse(call.active)
        self.assertNotIn(protocol.ASSISTANT_FINISHED, transport.types())

    async def test_end_of_speech_from_the_client_ends_the_turn(self):
        transport = FakeTransport()
        call = make_call(transport, script=[True] * 100, greeting="")
        await call.start()
        await call.on_audio(FRAME * 5)
        await call.on_control({"type": protocol.END_OF_SPEECH})
        await call._turn
        self.assertTrue(transport.of(protocol.TRANSCRIPT))

    async def test_silence_transcribed_as_nothing_just_listens_again(self):
        transport = FakeTransport()
        call = make_call(transport, script=[False] * 2 + [True] * 5 + [False] * 9,
                         greeting="", transcript="")
        await call.start()
        transport.messages.clear()
        await call.on_audio(FRAME * 16)
        await call._turn
        self.assertNotIn(protocol.ASSISTANT_SPEAKING, transport.types())
        self.assertEqual(transport.types()[-1], protocol.LISTENING)

    async def test_the_workshop_mic_is_held_for_the_call(self):
        """Kiri listens in two places. Without this she answers twice — once in
        the earpiece and once out loud across the workshop."""
        mute_log = []
        transport = FakeTransport()
        call = make_call(transport, mute_log=mute_log, greeting="")
        await call.start()
        self.assertEqual(mute_log, [True], "mic should be held as the handset lifts")
        await call.hang_up()
        self.assertEqual(mute_log, [True, False], "and released when it goes down")

    async def test_the_mic_is_held_before_the_greeting_plays(self):
        # The workshop mic would otherwise hear the greeting itself.
        order = []
        transport = FakeTransport()

        async def note_mute(muted):
            order.append("mute" if muted else "unmute")
            return True

        async def note_audio(data):
            order.append("audio")

        call = Call(
            transport.send_json,
            note_audio,
            transcribe=lambda pcm: "",
            chat=None,
            synthesize=lambda text: b"\x00\x01" * 10,
            sample_rate=lambda: 22050,
            segmenter=Segmenter(ScriptedVad([])),
            greeting="Hello.",
            mute_voice=note_mute,
        )
        await call.start()
        await call._turn
        self.assertEqual(order[0], "mute")
        self.assertIn("audio", order)

    async def test_a_dead_voice_service_does_not_break_the_call(self):
        async def failing_mute(muted):
            return False  # voice_control swallowed an error and told us so

        transport = FakeTransport()
        call = make_call(transport, greeting="")
        call._mute_voice = failing_mute
        await call.start()
        self.assertTrue(call.active, "a nuisance, not a reason to drop the call")

    async def test_a_failing_brain_is_reported_not_swallowed(self):
        async def broken_chat(text, **kwargs):
            raise RuntimeError("homeai-server is down")
            yield ""

        transport = FakeTransport()
        call = make_call(transport, script=[False] * 2 + [True] * 5 + [False] * 9,
                         greeting="", chat=broken_chat)
        await call.start()
        await call.on_audio(FRAME * 16)
        await call._turn
        self.assertTrue(transport.of(protocol.ERROR))
        # ...and the call carries on rather than wedging.
        self.assertEqual(transport.types()[-1], protocol.LISTENING)


if __name__ == "__main__":
    unittest.main()
