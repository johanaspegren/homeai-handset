"""Text to speech — Piper, synthesised straight to PCM for the earpiece.

homeai-voice writes WAVs and plays them on the workshop speakers; here the
audio has to go down a WebSocket instead, so we take Piper's raw int16 chunks
and hand them back as bytes. Everything else (the voice model, markdown
stripping, where to cut sentences) is shared with homeai-voice.
"""

import logging

from . import config
from .voice_reuse import _first_break, strip_markdown

_voice = None


def load():
    """Load the Piper voice. Safe to call more than once."""
    global _voice
    if _voice is None:
        from piper import PiperVoice

        model_path = config.PIPER_VOICE_DIR / f"{config.PIPER_VOICE}.onnx"
        if not model_path.exists():
            raise FileNotFoundError(
                f"Voice model not found at {model_path}. Point PIPER_VOICE_DIR at "
                "homeai-voice/voices, or download it with: python -m piper.download_voices "
                f"{config.PIPER_VOICE} --download-dir {config.PIPER_VOICE_DIR}"
            )
        logging.info("loading voice | model=%s", model_path,
                     extra={"stage": "TTS", "terminal": "-"})
        _voice = PiperVoice.load(model_path)
    return _voice


def sample_rate() -> int:
    """The rate Piper actually produces — announced to the client at call start
    so the earpiece never plays back at the wrong speed."""
    voice = load()
    return int(voice.config.sample_rate)


def synthesize(text: str) -> bytes:
    """Render one phrase to raw 16-bit mono PCM. Blocking — call in a thread."""
    text = strip_markdown(text).strip()
    if not text:
        return b""
    voice = load()
    return b"".join(chunk.audio_int16_bytes for chunk in voice.synthesize(text))


async def speakable_chunks(tokens, first_min: int = 16, max_len: int = 220):
    """Regroup an *async* stream of text tokens into complete, speakable phrases.

    The async counterpart of homeai-voice's `speak.sentence_chunks`, sharing its
    boundary rule. Yields each phrase the moment its boundary arrives, so the
    first sentence can be synthesised while the model is still writing the rest.
    """
    buf = ""
    first = True
    async for token in tokens:
        buf += token
        while True:
            idx = _first_break(buf, first, first_min, max_len)
            if not idx:
                break
            chunk, buf = buf[:idx].strip(), buf[idx:].lstrip()
            if chunk:
                first = False
                yield chunk
    tail = buf.strip()
    if tail:
        yield tail
