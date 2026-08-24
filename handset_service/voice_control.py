"""Hold the workshop's open mic while someone is on the phone.

Kiri listens in two places at once: the always-on mic in the workshop and now
the telephone. Without this, lifting the handset gets you two of her — the
workshop mic hears the conversation and answers it as well.

homeai-server already has the channel for this: the dashboard's mute button
POSTs to /voice/mute, and homeai-voice drains the queued command on its
heartbeat. The handset presses the same button — muted while the handset is
up, unmuted when it goes down.

Set HANDSET_MUTES_VOICE=0 to leave the workshop mic listening through a call,
which turns the pair into a hands-free setup: talk to the phone, or talk to the
room, whichever is nearer.
"""

import logging

import httpx

from . import config, handsfree


async def set_voice_muted(muted: bool) -> bool:
    """Ask homeai-voice to go quiet (or come back). False if it didn't take.

    Never raises: the workshop mic answering twice is a nuisance, not a reason
    to drop a telephone call.
    """
    if not config.MUTE_VOICE_DURING_CALL:
        return False
    if not handsfree.voice_running():
        # Normal state now: the workshop mic isn't running at all, so there's
        # nothing to hold. Queueing the command anyway would leave it sitting on
        # the server for whenever hands free is switched on — which would then
        # start up already muted.
        return False
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.post(config.VOICE_MUTE_URL, json={"muted": muted})
            response.raise_for_status()
        return True
    except Exception as exc:
        logging.warning(
            "could not %s the workshop mic (%s) — she may answer twice",
            "mute" if muted else "unmute", exc,
            extra={"stage": "VOICE", "terminal": "-"},
        )
        return False
