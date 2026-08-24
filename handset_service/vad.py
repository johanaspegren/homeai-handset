"""Turn taking: split a live microphone stream into complete utterances.

homeai-voice does this with `always.segment_speech()`, which *pulls* frames
from a generator. A WebSocket pushes, so this is the same algorithm inverted:
feed it bytes as they arrive and it hands back a finished utterance whenever
the speaker stops talking.

Doing this on the server keeps the Pi client dumb — no numpy, no ONNX runtime,
nothing that would have to be rebuilt for the ARMv6 Pi Zero.
"""

import logging
from collections import deque

from . import config

_FRAME_MS = config.FRAME_SAMPLES / config.CAPTURE_RATE * 1000


class Vad:
    """silero-VAD, as bundled with openWakeWord (same model homeai-voice uses)."""

    def __init__(self) -> None:
        from openwakeword.vad import VAD

        self._vad = VAD()

    def speech_prob(self, frame: bytes) -> float:
        import numpy as np

        return float(self._vad.predict(np.frombuffer(frame, dtype=np.int16)))

    def reset(self) -> None:
        self._vad.reset_states()


class Segmenter:
    """Accumulates PCM and emits one utterance per speech burst.

    Feed it whatever the socket delivers — frames are re-sliced to the 80 ms
    the VAD model expects, so the client is free to send any chunk size.
    """

    def __init__(
        self,
        vad,
        *,
        threshold: float = config.VAD_THRESHOLD,
        hangover_ms: int = config.VAD_HANGOVER_MS,
        preroll_ms: int = config.VAD_PREROLL_MS,
        min_speech_ms: int = config.VAD_MIN_SPEECH_MS,
        max_utterance_ms: int = config.VAD_MAX_UTTERANCE_MS,
        on_speech_start=None,
    ) -> None:
        self._vad = vad
        self.threshold = threshold
        self.hangover_ms = hangover_ms
        self.min_speech_ms = min_speech_ms
        self.max_utterance_ms = max_utterance_ms
        self.on_speech_start = on_speech_start
        self._preroll = deque(maxlen=max(1, int(preroll_ms / _FRAME_MS)))
        self._buf = bytearray()
        self.reset()

    def reset(self) -> None:
        """Forget anything half-heard. Called after each reply so the assistant
        never transcribes the tail of its own turn."""
        self._preroll.clear()
        self._buf.clear()
        self._captured = bytearray()
        self._in_speech = False
        self._speech_ms = 0.0
        self._silence_ms = 0.0
        self._total_ms = 0.0

    @property
    def in_speech(self) -> bool:
        return self._in_speech

    def push(self, data: bytes) -> list[bytes]:
        """Consume audio; return any utterances that completed (usually none)."""
        self._buf.extend(data)
        done = []
        while len(self._buf) >= config.FRAME_BYTES:
            frame = bytes(self._buf[: config.FRAME_BYTES])
            del self._buf[: config.FRAME_BYTES]
            utterance = self._frame(frame)
            if utterance:
                done.append(utterance)
        return done

    def _frame(self, frame: bytes) -> bytes | None:
        try:
            speech = self._vad.speech_prob(frame) >= self.threshold
        except Exception:
            logging.exception("VAD failed on a frame — treating it as silence")
            speech = False

        if not self._in_speech:
            if not speech:
                self._preroll.append(frame)
                return None
            # Speech onset: keep the preroll so the first syllable survives the
            # VAD's own reaction time. The onset frame itself is added below.
            self._in_speech = True
            self._captured = bytearray(b"".join(self._preroll))
            self._preroll.clear()
            self._speech_ms = 0.0
            self._silence_ms = 0.0
            self._total_ms = 0.0
            if self.on_speech_start:
                self.on_speech_start()

        self._captured += frame
        self._total_ms += _FRAME_MS
        if speech:
            self._speech_ms += _FRAME_MS
            self._silence_ms = 0.0
        else:
            self._silence_ms += _FRAME_MS

        # End on the hangover whatever was heard: a cough or a door bang would
        # otherwise leave us "in speech", swallowing silence until the hard cap.
        # flush() decides whether what we captured was long enough to be speech.
        if self._silence_ms >= self.hangover_ms or self._total_ms >= self.max_utterance_ms:
            return self.flush()
        return None

    def flush(self) -> bytes | None:
        """End the current utterance now (client `end_of_speech`, or hangover).

        Returns the captured PCM, or None if it was too short to be speech."""
        if not self._in_speech:
            return None
        pcm = bytes(self._captured)
        too_short = self._speech_ms < self.min_speech_ms
        self._in_speech = False
        self._captured = bytearray()
        self._preroll.clear()
        return None if too_short else pcm

    @property
    def speech_seconds(self) -> float:
        return self._total_ms / 1000.0
