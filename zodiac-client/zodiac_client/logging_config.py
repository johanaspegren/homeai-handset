"""Client logging — same readable shape as the service.

    20:14:09.101 | GPIO  | OFF_HOOK
    20:14:09.104 | NET   | websocket connected
"""

import logging
import sys


class _Formatter(logging.Formatter):
    default_msec_format = "%s.%03d"

    def format(self, record: logging.LogRecord) -> str:
        stage = getattr(record, "stage", record.name.split(".")[-1].upper())
        return f"{self.formatTime(record, self.datefmt)} | {stage:<5} | {record.getMessage()}"


def setup(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_Formatter(datefmt="%H:%M:%S"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
