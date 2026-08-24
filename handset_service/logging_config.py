"""Human-readable, latency-legible logging.

The spec asks for lines you can read down a terminal and see where the time
went, e.g.

    20:14:15.742 | AUDIO | zodiac-01 | speech ended | 3.24s
    20:14:16.301 | STT   | zodiac-01 | transcript | "What's the weather?"

so every event is `HH:MM:SS.mmm | STAGE | terminal | detail`.
"""

import logging
import sys
import time


class _Formatter(logging.Formatter):
    default_msec_format = "%s.%03d"

    def format(self, record: logging.LogRecord) -> str:
        stage = getattr(record, "stage", record.name.split(".")[-1].upper())
        terminal = getattr(record, "terminal", "-")
        return f"{self.formatTime(record, self.datefmt)} | {stage:<5} | {terminal} | {record.getMessage()}"


def setup(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_Formatter(datefmt="%H:%M:%S"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # uvicorn's own access log would fight this format; keep its errors only.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


class CallLog:
    """A logger bound to one call, with a stopwatch for latency lines."""

    def __init__(self, terminal: str) -> None:
        self.terminal = terminal
        self._log = logging.getLogger("handset")
        self._marks: dict[str, float] = {}

    def event(self, stage: str, message: str, level: int = logging.INFO) -> None:
        self._log.log(level, message, extra={"stage": stage, "terminal": self.terminal})

    def mark(self, name: str) -> float:
        """Record a timestamp to measure later stages against."""
        now = time.perf_counter()
        self._marks[name] = now
        return now

    def since(self, name: str) -> float | None:
        start = self._marks.get(name)
        return None if start is None else time.perf_counter() - start

    def timed(self, stage: str, message: str, mark: str = "speech_end") -> None:
        """Log an event with the elapsed time since a mark — the latency trail."""
        elapsed = self.since(mark)
        suffix = "" if elapsed is None else f" | +{elapsed:.3f}s"
        self.event(stage, message + suffix)
