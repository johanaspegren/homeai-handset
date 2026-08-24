"""Hands free — switching the workshop's mic and speakers on and off."""

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from handset_service import config, handsfree, voice_control


class VoiceRunningTests(unittest.TestCase):
    def _pidfile(self, contents: str) -> Path:
        path = Path(tempfile.mkdtemp()) / "voice.pid"
        path.write_text(contents)
        return path

    def test_no_pidfile_means_not_running(self):
        with patch.object(config, "VOICE_PIDFILE", Path("/nonexistent/voice.pid")):
            self.assertFalse(handsfree.voice_running())

    def test_a_stale_pidfile_means_not_running(self):
        # homeai.sh leaves the pidfile behind if the process died; a pid that
        # no longer exists must not read as "the room is listening".
        with patch.object(config, "VOICE_PIDFILE", self._pidfile("999999")):
            self.assertFalse(handsfree.voice_running())

    def test_a_garbage_pidfile_means_not_running(self):
        with patch.object(config, "VOICE_PIDFILE", self._pidfile("not a pid")):
            self.assertFalse(handsfree.voice_running())

    def test_our_own_pid_reads_as_running(self):
        import os

        with patch.object(config, "VOICE_PIDFILE", self._pidfile(str(os.getpid()))):
            self.assertTrue(handsfree.voice_running())


class SetHandsFreeTests(unittest.IsolatedAsyncioTestCase):
    async def test_switching_on_when_already_on_does_nothing(self):
        with patch.object(handsfree, "voice_running", return_value=True):
            with patch.object(handsfree, "_homeai_sh") as run:
                self.assertTrue(await handsfree.set_hands_free(True))
                run.assert_not_called()

    async def test_switching_on_starts_the_voice_service(self):
        calls = []

        async def fake_run(*args):
            calls.append(args)
            return True

        states = iter([False, True])  # not running, then running
        with patch.object(handsfree, "voice_running", side_effect=lambda: next(states)):
            with patch.object(handsfree, "_homeai_sh", fake_run):
                self.assertTrue(await handsfree.set_hands_free(True))
        self.assertEqual(calls, [("start", "voice")])

    async def test_switching_off_stops_it(self):
        calls = []

        async def fake_run(*args):
            calls.append(args)
            return True

        states = iter([True, False])
        with patch.object(handsfree, "voice_running", side_effect=lambda: next(states)):
            with patch.object(handsfree, "_homeai_sh", fake_run):
                self.assertFalse(await handsfree.set_hands_free(False))
        self.assertEqual(calls, [("stop", "voice")])

    async def test_a_start_that_does_not_take_reports_honestly(self):
        """If homeai.sh can't bring it up, say so rather than claiming success."""
        async def fake_run(*args):
            return True  # exit 0 but the service still isn't there

        with patch.object(handsfree, "voice_running", return_value=False):
            with patch.object(handsfree, "_homeai_sh", fake_run):
                self.assertFalse(await handsfree.set_hands_free(True))


class MuteSkippingTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_mute_is_queued_when_the_room_is_not_listening(self):
        """Otherwise the command sits on the server until hands free is
        switched on — which would then come up already muted."""
        with patch.object(handsfree, "voice_running", return_value=False):
            with patch.object(voice_control, "httpx") as fake_httpx:
                self.assertFalse(await voice_control.set_voice_muted(True))
                fake_httpx.AsyncClient.assert_not_called()


if __name__ == "__main__":
    unittest.main()
