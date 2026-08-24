"""Adapter onto homeai-voice's proven speech helpers.

The spec says to prefer adapters around existing HomeAI code over rewriting it,
and `homeai-voice/speak.py` already solves the hard part of speaking a token
stream: where to cut it. Its boundary rule survives decimals ("27.9" is not a
sentence end) and allows one early clause break so the first phrase reaches the
earpiece sooner — both worth reusing verbatim rather than re-deriving.

Only *pure functions* are imported. Playback stays here, because homeai-voice
plays to the workshop speakers and the handset's audio has to go down a socket.

Set HOMEAI_VOICE_DIR if the checkout isn't the sibling directory.
"""

import sys

from . import config

_voice_dir = str(config.HOMEAI_VOICE_DIR)
if _voice_dir not in sys.path:
    sys.path.insert(0, _voice_dir)

try:
    # `_first_break` is the boundary detector behind speak.sentence_chunks; we
    # need it directly because our token stream is async (sentence_chunks takes
    # a sync iterable).
    from speak import _first_break, strip_markdown  # noqa: F401
except ImportError as exc:  # pragma: no cover - configuration error
    raise ImportError(
        f"Could not import homeai-voice's speak.py from {_voice_dir}. "
        "Set HOMEAI_VOICE_DIR to your homeai-voice checkout."
    ) from exc
