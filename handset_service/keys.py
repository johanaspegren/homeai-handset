"""What a button on the Zodiac does.

The keypad is part of the *telephone*, not part of a conversation. A press with
the handset in the cradle is still a press — and the two things the buttons are
for both happen with the handset down: putting music on as you walk past, and
opening the room up into what is effectively a conference call so Kiri is
talking to the workshop rather than into an earpiece. So this is deliberately
not a method on `Call`: it works whether or not one exists, and a call is
something it *decorates* when there is one.

Two kinds of binding, and the split is the whole design:

  actions   things this service does itself, because they are about the
            telephone rather than about Kiri. Hands free is the only one:
            it starts and stops homeai-voice.

  phrases   words put to Kiri through /chat exactly as if they had been
            spoken down the line. This is why "put some music on" reaches
            Spotify while this file has never heard of Spotify — the phrase
            goes to the brain, the brain emits `<<run: play …>>`, and the
            server resolves the room to a speaker. A button is a stored
            sentence, not a command, so it inherits the persona, the vault
            and every tool Kiri already has. Add a button by adding a
            sentence to HANDSET_KEYS; no code changes, and nothing about the
            music lives on the phone.

The cost of that choice is honest: a press goes through a language model, so a
button is a second or so rather than instant, and Kiri has to choose the right
marker. The alternative — this service calling the Spotify tool itself — would
be quicker and would make the handset a second brain, which is the one thing
the architecture is built to prevent.

Where the reply goes depends on whether anyone is holding the handset. Off-hook
it is spoken into the earpiece, because someone is listening. On-hook it is
logged and no more: the point of the press was the action, and there is no ear
to speak into. The music starts either way.
"""

import logging

from . import config, handsfree, llm
from .logging_config import CallLog

log = logging.getLogger("handset")


async def press(key: str, *, call=None, terminal: str = "zodiac",
                place: str = None, chat=None, toggle=None) -> str | None:
    """Act on one key. Returns Kiri's reply when there was one, else None.

    `call` is the call in progress, if any — passed rather than looked up so
    that a press on a phone in its cradle is the ordinary case and not a
    special one.
    """
    key = str(key)
    trail = call.log if call is not None else CallLog(terminal)
    trail.event("KEY", key)

    if config.HANDSFREE_KEY and key == config.HANDSFREE_KEY:
        await _hands_free(call, trail, toggle=toggle)
        return None

    phrase = config.KEY_PHRASES.get(key)
    if phrase is None:
        trail.event("KEY", f"{key} — nothing bound")
        return None
    return await _say_to_kiri(phrase, call, trail, terminal=terminal,
                              place=place, chat=chat)


async def _hands_free(call, trail, *, toggle=None) -> None:
    """The conference-call button.

    In a call this belongs to the Call, which can say "one moment" into the
    earpiece while homeai-voice loads Whisper — several seconds of silence on a
    telephone reads as a dead line. With the handset down there is nobody to
    tell, so it just happens.
    """
    if call is not None:
        await call.toggle_hands_free()
        return
    try:
        on = await (toggle or handsfree.toggle)()
        trail.event("HANDS", f"hands free {'on' if on else 'off'}")
    except Exception:
        logging.exception("hands-free toggle failed")


async def _say_to_kiri(phrase, call, trail, *, terminal, place, chat=None) -> str:
    trail.event("KEY", f'saying | "{phrase}"')
    if call is not None:
        # Someone is on the line: this is their turn, spoken for them, and the
        # answer comes back in the earpiece like any other.
        await call.say(phrase)
        return None
    ask = chat or llm.stream_reply
    reply = []
    try:
        async for token in ask(phrase, terminal=terminal, place=place):
            reply.append(token)
    except Exception:
        logging.exception("key phrase failed")
        trail.event("KEY", f'"{phrase}" — the brain did not answer')
        return None
    text = "".join(reply).strip()
    # Nowhere to speak it: the action was the point. Logged so a press that
    # quietly did nothing can be told from one that worked.
    trail.event("KIRI", text or "(no reply)")
    return text
