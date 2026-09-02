"""The call state machine, with fake speech and a fake Kiri.

No models, no sockets: the transport and the three slow stages are injected, so
these run in milliseconds and assert the things that actually matter on a
telephone — that it answers instantly, that it hands the turn back, and that
replacing the handset stops everything.
"""

import asyncio
import time
import unittest
from unittest import mock

from handset_service import config, llm, protocol, session
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
              greeting="Hello.", greeting_delay_s=0, chat=None, synthesize=None,
              mute_log=None):
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
        greeting_delay_s=greeting_delay_s,
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

    async def test_the_greeting_waits_for_the_handset_to_reach_your_ear(self):
        """Answering instantly means she's mid-word before the earpiece
        arrives. The pause is before the greeting, not before answering."""
        transport = FakeTransport()
        call = make_call(transport, greeting_delay_s=0.05)

        started = time.perf_counter()
        await call.start()
        # call_started goes out at once — only the speech waits behind the pause.
        self.assertEqual(transport.messages[0]["type"], protocol.CALL_STARTED)
        await call._turn
        elapsed = time.perf_counter() - started

        self.assertGreaterEqual(elapsed, 0.04, "the greeting did not wait")
        self.assertIn(protocol.ASSISTANT_SPEAKING, transport.types())
        self.assertTrue(transport.audio)

    async def test_replacing_the_handset_during_the_pause_says_nothing(self):
        """Lift and put down again: she should not talk to an empty room, nor
        hand the turn back on a call that has ended."""
        transport = FakeTransport()
        call = make_call(transport, greeting_delay_s=5)
        await call.start()
        # Let the greeting task actually start and reach the pause. Without
        # this the task is cancelled before its first line ever runs, which
        # skips the cleanup path this test exists to cover.
        await asyncio.sleep(0)
        self.assertFalse(call._turn.done())

        await call.hang_up()

        self.assertNotIn(protocol.ASSISTANT_SPEAKING, transport.types())
        self.assertFalse(transport.audio, "nothing should have been spoken")
        self.assertNotIn(protocol.LISTENING, transport.types())
        self.assertFalse(call._accepting, "a dead call must not reopen the mic")

    async def test_the_greeting_varies_between_calls(self):
        """A handful of lines, and never the same one twice running — plain
        random choice repeats often enough to sound like a fault."""
        pool = ["One.", "Two.", "Three.", "Four."]
        with mock.patch.object(config, "GREETINGS", pool):
            session._last_greeting = None
            picked = [session.pick_greeting() for _ in range(40)]

        # Every configured line gets used — alternating between two of them
        # would satisfy "it varies" while quietly wasting the other two.
        self.assertEqual(set(picked), set(pool), "some greetings were never used")
        for before, after in zip(picked, picked[1:]):
            self.assertNotEqual(before, after, "greeted twice the same way running")

    async def test_a_single_configured_greeting_still_works(self):
        """HANDSET_GREETING with no '|' is one line, exactly as before."""
        with mock.patch.object(config, "GREETINGS", ["HomeAI. Hello Johan."]):
            session._last_greeting = None
            self.assertEqual(session.pick_greeting(), "HomeAI. Hello Johan.")
            self.assertEqual(session.pick_greeting(), "HomeAI. Hello Johan.")

    async def test_no_greeting_configured_answers_silently(self):
        with mock.patch.object(config, "GREETINGS", []):
            session._last_greeting = None
            self.assertEqual(session.pick_greeting(), "")

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


class PlaceTests(unittest.IsolatedAsyncioTestCase):
    """The telephone says where it is, and stops there.

    The Pi announces a room; this service passes it on without ever learning
    what is in it. These tests pin the seam: the field survives the trip to
    /chat, and nothing on this side resolves it to a speaker."""

    def test_identity_is_bound_to_the_real_chat_callable(self):
        transport = FakeTransport()
        call = Call(transport.send_json, transport.send_audio,
                    terminal_id="zodiac-01", place="workshop", greeting="")
        self.assertEqual(call._chat.keywords,
                         {"terminal": "zodiac-01", "place": "workshop"})

    def test_a_client_that_names_no_room_falls_back_to_config(self):
        transport = FakeTransport()
        call = Call(transport.send_json, transport.send_audio,
                    terminal_id="zodiac-01", greeting="")
        self.assertEqual(call.place, config.HANDSET_PLACE)

    async def test_the_room_reaches_the_post_body(self):
        posted = {}

        class FakeResponse:
            def raise_for_status(self): pass
            async def aiter_text(self):
                yield "hello"

        class FakeStream:
            def __init__(self, **kwargs): posted.update(kwargs)
            async def __aenter__(self): return FakeResponse()
            async def __aexit__(self, *a): return False

        class FakeClient:
            def __init__(self, **kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            def stream(self, method, url, **kwargs): return FakeStream(**kwargs)

        with mock.patch("handset_service.llm.httpx.AsyncClient", FakeClient):
            async for _ in llm.stream_reply("hello", terminal="zodiac-01",
                                            place="workshop"):
                pass
        body = posted["json"]
        self.assertEqual(body["terminal"], "zodiac-01")
        self.assertEqual(body["place"], "workshop")
        self.assertEqual(body["source"], "handset")

    def test_nothing_here_knows_what_is_in_a_room(self):
        """The whole point of the split: a speaker's name must never appear in
        service *code*. Prose may discuss Spotify — keys.py explains at length
        why it doesn't call it — so this reads the parsed syntax tree with
        docstrings removed, and comments never survive parsing at all. If a
        device name shows up here, the adapter has started owning hardware it
        cannot see."""
        import ast
        import pathlib
        service = pathlib.Path(session.__file__).parent
        for module in sorted(service.glob("*.py")):
            tree = ast.parse(module.read_text())
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.ClassDef,
                                     ast.FunctionDef, ast.AsyncFunctionDef)):
                    if (node.body and isinstance(node.body[0], ast.Expr)
                            and isinstance(node.body[0].value, ast.Constant)
                            and isinstance(node.body[0].value.value, str)):
                        node.body.pop(0)
                        if not node.body:
                            node.body.append(ast.Pass())
            code = ast.unparse(ast.fix_missing_locations(tree)).lower()
            self.assertNotIn("spotify", code, module.name)


if __name__ == "__main__":
    unittest.main()
