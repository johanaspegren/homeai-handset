"""Hands free: the workshop's mic and speakers, on demand.

The telephone is the default way to talk to Kiri, so homeai-voice no longer
starts with the stack. That isn't just tidiness — a *muted* voice service still
runs VAD on every frame and still puts every utterance in the room through
Whisper. Muting stops her answering; it doesn't stop the work. Not running the
service is what actually gives the GPU back (~1 GB of VRAM, plus the
transcription of conversations nobody asked it to hear).

Hands free turns it on again: press the button, the workshop mic and speakers
come up and you can talk to the room instead of holding the handset. Press it
again and they go away.

Starting the service is not instant — it loads Whisper onto the GPU, so expect
several seconds before the room is listening. It is a mode you switch, not a
key you hold.
"""

import asyncio
import logging
import os

from . import config

log = logging.getLogger("handset")


def voice_running() -> bool:
    """Is homeai-voice up? Read the same pidfile homeai.sh writes."""
    try:
        pid = int(config.VOICE_PIDFILE.read_text().strip())
    except (OSError, ValueError):
        return False
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


async def _homeai_sh(*args: str) -> bool:
    """Run ~/dev/homeai.sh, which owns every service's lifecycle."""
    if not config.HOMEAI_SH.exists():
        log.warning("homeai.sh not found at %s — cannot switch hands free",
                    config.HOMEAI_SH, extra={"stage": "HANDS", "terminal": "-"})
        return False
    try:
        # Inherit our stdout/stderr rather than capturing through a pipe. The
        # service it starts is long-lived and would hold the pipe's write end
        # open, so waiting for EOF (communicate()) hangs until the timeout even
        # though homeai.sh itself returned in three seconds. Inheriting also
        # puts homeai.sh's own output straight into this service's log.
        proc = await asyncio.create_subprocess_exec(
            str(config.HOMEAI_SH), *args,
            start_new_session=True,  # don't die with us; homeai.sh owns it now
        )
        await asyncio.wait_for(proc.wait(), timeout=config.HANDSFREE_TIMEOUT_S)
        log.info("homeai.sh %s | exit %s", " ".join(args), proc.returncode,
                 extra={"stage": "HANDS", "terminal": "-"})
        return proc.returncode == 0
    except asyncio.TimeoutError:
        log.warning("homeai.sh %s timed out", " ".join(args),
                    extra={"stage": "HANDS", "terminal": "-"})
        return False
    except Exception:
        logging.exception("homeai.sh %s failed", " ".join(args))
        return False


async def set_hands_free(on: bool) -> bool:
    """Switch the workshop mic and speakers on or off. Returns the state after."""
    if on == voice_running():
        return on
    await _homeai_sh("start" if on else "stop", "voice")
    return voice_running()


async def toggle() -> bool:
    """What the phone's hands-free button does. Returns the new state."""
    return await set_hands_free(not voice_running())
