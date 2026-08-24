"""Make the pip-installed NVIDIA CUDA libraries loadable in-process.

faster-whisper's CTranslate2 backend dlopens ``libcublas.so.12`` and
``libcudnn.so.9`` by name. When those come from pip wheels
(``nvidia-cublas-cu12`` / ``nvidia-cudnn-cu12``) they live inside the venv at
``site-packages/nvidia/*/lib`` — a directory the dynamic loader doesn't search
by default. ``LD_LIBRARY_PATH`` only helps if set *before* the process starts,
which is awkward for a service, so instead we eagerly ``dlopen`` every bundled
.so with ``RTLD_GLOBAL`` here.

Import this before ``faster_whisper``. It's a no-op if the wheels aren't
installed, so CPU-only setups are unaffected. (Same shim as
homeai-voice/_cudalibs.py, resolved against *this* repo's venv.)
"""

import ctypes
import logging
from pathlib import Path

_NVIDIA_ROOT = Path(__file__).resolve().parent.parent / ".venv"


def _lib_dirs() -> list[Path]:
    # site-packages/nvidia/<pkg>/lib under whichever pythonX.Y this venv is.
    roots = list(_NVIDIA_ROOT.glob("lib/python*/site-packages/nvidia"))
    return [d for root in roots for d in root.glob("*/lib") if d.is_dir()]


def preload() -> bool:
    """dlopen every bundled CUDA .so with RTLD_GLOBAL. Returns True if any loaded."""
    sofiles = [so for d in _lib_dirs() for so in d.glob("*.so*")]
    if not sofiles:
        return False
    # Some libs depend on others; retry across passes until no more progress.
    pending = set(sofiles)
    loaded = False
    while pending:
        made_progress = False
        for so in list(pending):
            try:
                ctypes.CDLL(str(so), mode=ctypes.RTLD_GLOBAL)
                pending.discard(so)
                made_progress = True
                loaded = True
            except OSError:
                continue
        if not made_progress:
            break  # remaining ones have unmet deps we can't resolve; leave them
    if loaded:
        logging.debug("preloaded bundled CUDA libraries from %s", _lib_dirs())
    return loaded


preload()
