"""Speech to text — faster-whisper on the GPU, same setup homeai-voice uses.

The model is loaded once at startup (not on the first call) so lifting the
handset never pays for it. Transcription itself is blocking C++, so callers
run it via `asyncio.to_thread`.
"""

from . import _netfix  # noqa: F401  (patch DNS before faster_whisper can try a download)
from . import _cudalibs  # noqa: F401  (dlopen bundled CUDA libs before ctranslate2 loads)

import io
import logging
import wave

from . import config

_model = None


def load() -> None:
    """Load the Whisper model. Safe to call more than once."""
    global _model
    if _model is not None:
        return
    from faster_whisper import WhisperModel

    logging.info(
        "loading whisper | size=%s | device=%s | compute=%s",
        config.WHISPER_MODEL, config.WHISPER_DEVICE, config.WHISPER_COMPUTE or "(default)",
        extra={"stage": "STT", "terminal": "-"},
    )
    kwargs = {"compute_type": config.WHISPER_COMPUTE} if config.WHISPER_COMPUTE else {}
    _model = WhisperModel(config.WHISPER_MODEL, device=config.WHISPER_DEVICE, **kwargs)
    logging.info("whisper ready", extra={"stage": "STT", "terminal": "-"})


def _wav_bytes(pcm: bytes) -> io.BytesIO:
    """Wrap raw PCM in a WAV container in memory (no temp files on the hot path)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(config.CAPTURE_RATE)
        wav.writeframes(pcm)
    buf.seek(0)
    return buf


def transcribe_pcm(pcm: bytes) -> str:
    """Transcribe raw 16 kHz mono int16 PCM. Empty in -> empty out."""
    if not pcm:
        return ""
    load()
    segments, _info = _model.transcribe(_wav_bytes(pcm), language="en", beam_size=1)
    return " ".join(segment.text.strip() for segment in segments).strip()
