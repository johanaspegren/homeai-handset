"""The brain: homeai-server's /chat, streamed token by token.

The handset is a channel adapter, exactly like homeai-discord-bot and
homeai-voice — it does not talk to Ollama itself. Everything that makes Kiri
herself (persona, the vault's facts and notes, `<<note:>>` / `<<remind:>>` /
`<<run:>>` tool markers, the journal) lives behind /chat, so the telephone gets
all of it for free and stays consistent with every other channel.

`source="handset"` tells the server this reply will be read aloud down a
telephone line, so it can pick the short spoken-style prompt.
"""

import httpx

from . import config


async def stream_reply(message: str, *, user: str = None, source: str = None):
    """Yield Kiri's reply as the server streams it."""
    user = user or config.HANDSET_USER
    source = source or config.HANDSET_SOURCE
    async with httpx.AsyncClient(timeout=config.CHAT_TIMEOUT_S) as client:
        async with client.stream(
            "POST",
            config.HOMEAI_CHAT_URL,
            json={"message": message, "user": user, "source": source},
        ) as response:
            response.raise_for_status()
            async for chunk in response.aiter_text():
                if chunk:
                    yield chunk
