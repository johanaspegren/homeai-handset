"""The Pi client's config — the file that keeps machine specifics out of code."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "zodiac-client"))

from zodiac_client.config import DEFAULTS, frame_bytes, load  # noqa: E402


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.pop("HOMEAI_WS_URL", None)

    def tearDown(self):
        if self._env is not None:
            os.environ["HOMEAI_WS_URL"] = self._env
        else:
            os.environ.pop("HOMEAI_WS_URL", None)

    def _write(self, text: str) -> str:
        path = Path(tempfile.mkdtemp()) / "config.yaml"
        path.write_text(text)
        return str(path)

    def test_shipped_config_loads(self):
        path = Path(__file__).resolve().parent.parent / "zodiac-client" / "config.yaml"
        settings = load(path)
        self.assertTrue(settings["homeai"]["websocket_url"].startswith("ws://"))
        self.assertIn(settings["hook"]["source"], ("stdin", "gpio", "always"))

    def test_partial_config_keeps_the_defaults_around_it(self):
        """A second Zodiac should only need to say what's different about it."""
        path = self._write("audio:\n  capture_device: plughw:CARD=Other,DEV=0\n")
        settings = load(path)
        self.assertEqual(settings["audio"]["capture_device"], "plughw:CARD=Other,DEV=0")
        self.assertEqual(settings["audio"]["capture_rate"], DEFAULTS["audio"]["capture_rate"])
        self.assertEqual(settings["terminal"]["id"], DEFAULTS["terminal"]["id"])

    def test_missing_config_falls_back_to_defaults(self):
        self.assertEqual(load("/nonexistent/config.yaml")["hook"]["source"], "stdin")

    def test_env_overrides_the_url(self):
        os.environ["HOMEAI_WS_URL"] = "ws://elsewhere:9000/zodiac"
        self.assertEqual(load("/nonexistent/config.yaml")["homeai"]["websocket_url"],
                         "ws://elsewhere:9000/zodiac")

    def test_frame_bytes_is_whole_int16_samples(self):
        self.assertEqual(frame_bytes({"capture_rate": 16000, "frame_ms": 40}), 1280)


if __name__ == "__main__":
    unittest.main()
