"""The wire protocol, and the one thing that silently breaks it."""

import unittest
from pathlib import Path

from handset_service import protocol

REPO = Path(__file__).resolve().parent.parent


class ProtocolTests(unittest.TestCase):
    def test_client_and_server_copies_are_identical(self):
        """The Pi client carries its own copy so it needs nothing installed from
        the service. That only works while the two files stay in step."""
        service = (REPO / "handset_service" / "protocol.py").read_text()
        client = (REPO / "zodiac-client" / "zodiac_client" / "protocol.py").read_text()
        self.assertEqual(
            service, client,
            "protocol.py has drifted — copy handset_service/protocol.py over "
            "zodiac-client/zodiac_client/protocol.py",
        )

    def test_message_carries_its_fields(self):
        self.assertEqual(
            protocol.message(protocol.KEY, key="7"),
            {"type": "key", "key": "7"},
        )

    def test_audio_format_matches_alsa_naming(self):
        fmt = protocol.audio_format(22050)
        self.assertEqual(fmt, {"rate": 22050, "channels": 1, "encoding": "s16le"})


if __name__ == "__main__":
    unittest.main()
