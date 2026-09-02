"""The brain: homeai-server's /chat, streamed token by token.

The handset is a channel adapter, exactly like homeai-discord-bot and
homeai-voice — it does not talk to Ollama itself. Everything that makes Kiri
herself (persona, the vault's facts and notes, `<<note:>>` / `<<remind:>>` /
`<<run:>>` tool markers, the journal) lives behind /chat, so the telephone gets
all of it for free and stays consistent with every other channel.

`source="handset"` tells the server this reply will be read aloud down a
telephone line, so it can pick the short spoken-style prompt.

Four fields travel with every request, and they answer four different
questions — worth keeping apart, because they vary independently:

    user      who is asking          provenance; a telephone can't tell, so
                                     it's configured rather than observed
    source    how they're asking     the modality, which shapes the reply
    terminal  which thing they used  "zodiac-01", for the log trail
    place     where "here" is        the room, so an action lands in the
                                     right one

Only the last matters to actions rather than answers: "play something" has to
resolve to a speaker, and the only honest answer to "which one?" is "the one in
the room the request came from". The resolving happens server-side — this
service names a room and never a device.
"""

import httpx

from . import config


async def stream_reply(message: str, *, user: str = None, source: str = None,
                       terminal: str = None, place: str = None):
    """Yield Kiri's reply as the server streams it."""
    user = user or config.HANDSET_USER
    source = source or config.HANDSET_SOURCE
    place = place or config.HANDSET_PLACE
    async with httpx.AsyncClient(timeout=config.CHAT_TIMEOUT_S) as client:
        async with client.stream(
            "POST",
            config.HOMEAI_CHAT_URL,
            json={"message": message, "user": user, "source": source,
                  "terminal": terminal, "place": place},
        ) as response:
            response.raise_for_status()
            async for chunk in response.aiter_text():
                if chunk:
                    yield chunk
