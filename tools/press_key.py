#!/usr/bin/env python3
"""Press a button on the Zodiac without the Zodiac.

The keypad is seven bare conductors on GPIO, so with the phone anywhere but on
the bench there is no way to press anything — and the interesting case is a
press with the handset in the cradle, which is exactly the case a call-shaped
test tool can't reach. This connects, says hello as the phone does, sends the
key and watches what the service says back.

    python3 tools/press_key.py 5          # "put some music on", in the workshop
    python3 tools/press_key.py '*'        # hands free: open the room up
    python3 tools/press_key.py 5 6        # several, half a second apart

Nothing is spoken back — with the handset down there is no earpiece to speak
into, which is the point. Watch the service log for what the button did:

    tail -f ~/dev/logs/handset.log
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import websockets

from handset_service import config, protocol


async def press(url: str, keys: list[str], terminal: str, place: str,
                gap: float, listen: float) -> None:
    async with websockets.connect(url, max_size=None) as ws:
        await ws.send(json.dumps(protocol.message(
            protocol.HELLO, terminal_id=terminal, place=place)))
        print(f"☎  connected as {terminal} in the {place}")
        for i, key in enumerate(keys):
            if i:
                await asyncio.sleep(gap)
            await ws.send(json.dumps(protocol.message(protocol.KEY, key=key)))
            print(f"⌨  pressed {key}")
        # The service answers a keypress by doing something, not by replying, so
        # there is usually nothing to receive. Wait anyway: an error frame is
        # worth seeing, and so is the silence when it all worked.
        try:
            async with asyncio.timeout(listen):
                async for message in ws:
                    if isinstance(message, bytes):
                        print(f"🔊 {len(message)} bytes of audio (nobody is listening)")
                    else:
                        print(f"←  {message}")
        except (TimeoutError, asyncio.TimeoutError):
            pass
    print("✔  done — see what it did:  tail -n 20 ~/dev/logs/handset.log")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("keys", nargs="+", help="the buttons to press, in order")
    parser.add_argument("--url", default=f"ws://localhost:{config.PORT}/zodiac")
    parser.add_argument("--terminal", default="zodiac-01")
    parser.add_argument("--place", default=config.HANDSET_PLACE,
                        help="the room the phone is pretending to stand in")
    parser.add_argument("--gap", type=float, default=0.5,
                        help="seconds between presses")
    parser.add_argument("--listen", type=float, default=3.0,
                        help="seconds to wait for anything coming back")
    args = parser.parse_args()

    bound = ", ".join(f"{k}={v!r}" for k, v in config.KEY_PHRASES.items())
    print(f"bound keys: {bound or '(none)'} | {config.HANDSFREE_KEY}=hands free\n")
    asyncio.run(press(args.url, args.keys, args.terminal, args.place,
                      args.gap, args.listen))


if __name__ == "__main__":
    main()
