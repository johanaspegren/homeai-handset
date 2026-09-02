"""The Pi's lifecycle: one socket, a handset that comes and goes on top of it.

The socket now outlives the handset so the buttons work with the phone in its
cradle. That is a real loosening, and it makes one property worth pinning down
hard rather than trusting: a live socket must never mean a live microphone.
Between "down" and "lifted" there is no `arecord` process, and these tests fail
if that ever stops being true.

Everything slow or physical is a stand-in: no ALSA, no GPIO, no network.
"""

import asyncio
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "zodiac-client"))

from zodiac_client import protocol  # noqa: E402
from zodiac_client.app import ZodiacClient  # noqa: E402
from zodiac_client.keypad import KEY_DOWN, KEY_UP, KeyEvent  # noqa: E402


class FakeMic:
    """Counts its own lifetime — the thing the privacy guarantee is about."""

    def __init__(self):
        self.running = False
        self.starts = 0

    async def start(self):
        self.running = True
        self.starts += 1

    async def stop(self):
        self.running = False

    async def frames(self):
        while self.running:
            await asyncio.sleep(0.01)
            yield b"\x00\x00"


class FakeEarpiece:
    rate = 22050

    async def start(self, rate=None):
        pass

    async def play(self, data):
        pass

    async def stop(self):
        pass


class FakeSocket:
    def __init__(self):
        self.sent = []
        self._incoming = asyncio.Queue()

    async def send(self, data):
        self.sent.append(json.loads(data) if isinstance(data, str) else data)

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self._incoming.get()

    def frames(self, type_):
        return [m for m in self.sent if isinstance(m, dict) and m["type"] == type_]

    def types(self):
        return [m["type"] for m in self.sent if isinstance(m, dict)]


class FakeKeypad:
    def __init__(self, events):
        self._events = events
        self.closed = False

    async def events(self):
        for event in self._events:
            yield event
        while True:                      # stay open like the real one
            await asyncio.sleep(3600)

    async def close(self):
        self.closed = True


SETTINGS = {
    "terminal": {"id": "zodiac-01", "place": "workshop"},
    "homeai": {"websocket_url": "ws://test/zodiac",
               "reconnect_delay_s": 0.01, "reconnect_max_delay_s": 0.01},
    "audio": {"capture_device": "null", "playback_device": "null",
              "capture_rate": 16000, "playback_rate": 22050, "frame_ms": 40,
              "buffer_us": 1000, "period_us": 1000},
    "hook": {"source": "always"},
    "keypad": {"enabled": False},
    "logging": {"level": "CRITICAL"},
}


def make_client():
    client = ZodiacClient(SETTINGS)
    client.mic = FakeMic()
    client.earpiece = FakeEarpiece()
    return client


class HookAndMicrophoneTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_microphone_does_not_exist_until_the_handset_is_lifted(self):
        client = make_client()
        ws = FakeSocket()
        client._ws = ws

        self.assertFalse(client.mic.running, "connected, but nobody has lifted it")
        await client._lift()
        self.assertTrue(client.mic.running)
        await client._replace()
        self.assertFalse(client.mic.running, "handset down: no recording process")

    async def test_the_hook_is_two_frames_not_two_connections(self):
        client = make_client()
        ws = FakeSocket()
        client._ws = ws

        await client._lift()
        await client._replace()
        await client._lift()
        # Same socket throughout — two calls, one connection.
        self.assertEqual(ws.types().count(protocol.OFF_HOOK), 2)
        self.assertEqual(ws.types().count(protocol.ON_HOOK), 1)

    async def test_hanging_up_twice_does_not_send_a_second_goodbye(self):
        client = make_client()
        ws = FakeSocket()
        client._ws = ws
        await client._lift()
        await client._replace()
        await client._replace()
        self.assertEqual(ws.types().count(protocol.ON_HOOK), 1)

    async def test_a_lift_while_offline_still_opens_the_call_on_reconnect(self):
        """The handset is up and the network is not. When it comes back, the
        caller should find themselves in a call rather than holding a dead
        line."""
        client = make_client()
        await client._lift()             # no socket yet
        self.assertFalse(client.mic.running)

        ws = FakeSocket()
        client._ws = ws
        await client._open_call(ws)      # what _stay_connected does on connect
        self.assertTrue(client.mic.running)
        self.assertEqual(len(ws.frames(protocol.OFF_HOOK)), 1)
        await client._stop_mic()


class KeypadTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_press_in_the_cradle_still_reaches_the_service(self):
        client = make_client()
        ws = FakeSocket()
        client._ws = ws
        keypad = FakeKeypad([KeyEvent(KEY_DOWN, "5"), KeyEvent(KEY_UP, "5")])

        watcher = asyncio.create_task(client._watch_keypad(keypad))
        await asyncio.sleep(0.05)
        watcher.cancel()

        self.assertFalse(client.mic.running, "on-hook: still no microphone")
        self.assertEqual(ws.frames(protocol.KEY), [{"type": protocol.KEY, "key": "5"}])

    async def test_only_the_press_is_sent_not_the_release(self):
        client = make_client()
        ws = FakeSocket()
        client._ws = ws
        keypad = FakeKeypad([KeyEvent(KEY_DOWN, "5"), KeyEvent(KEY_UP, "5"),
                             KeyEvent(KEY_DOWN, "6"), KeyEvent(KEY_UP, "6")])

        watcher = asyncio.create_task(client._watch_keypad(keypad))
        await asyncio.sleep(0.05)
        watcher.cancel()

        self.assertEqual([m["key"] for m in ws.frames(protocol.KEY)], ["5", "6"])

    async def test_a_press_while_offline_is_dropped_not_queued(self):
        """Music arriving five minutes late, once the network is back and
        nobody is standing there, is worse than nothing happening."""
        client = make_client()
        client._ws = None
        keypad = FakeKeypad([KeyEvent(KEY_DOWN, "5")])

        watcher = asyncio.create_task(client._watch_keypad(keypad))
        await asyncio.sleep(0.05)
        watcher.cancel()

        ws = FakeSocket()
        client._ws = ws
        await asyncio.sleep(0.02)
        self.assertEqual(ws.frames(protocol.KEY), [])


if __name__ == "__main__":
    unittest.main()
