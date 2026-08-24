"""Hook switch — and the stand-ins for it until it's wired.

The hook is the phone's on/off button and its privacy guarantee: the microphone
only exists between "lifted" and "replaced". Because the Zodiac's switch isn't
wired to the Pi yet, this is an interface with three implementations that emit
exactly the same events, so nothing downstream changes when the real switch
arrives — only `hook.source` in config.yaml.

    gpio    the real thing (gpiozero Button on `hook.pin`)
    stdin   press Enter to lift, Enter again to replace  (bring-up on a desk)
    always  permanently off-hook                          (soak tests, demos)
"""

import asyncio
import logging
import sys

log = logging.getLogger("zodiac.gpio")


class HookSource:
    """Yields True when the handset is lifted, False when it's replaced."""

    async def events(self):  # pragma: no cover - interface
        raise NotImplementedError
        yield

    async def close(self) -> None:
        pass


class GpioHook(HookSource):
    """The physical switch. Debounced in hardware terms by gpiozero's bounce_time.

    Wiring assumption: the switch closes to ground when the handset is DOWN
    (the classic cradle plunger), so `pressed` == on-hook. Flip `invert` if the
    Zodiac turns out to be the other way round — measure before trusting it.
    """

    def __init__(self, pin: int, *, invert: bool = False, debounce_ms: int = 50,
                 pull_up: bool = True) -> None:
        from gpiozero import Button

        self.invert = invert
        self._queue: asyncio.Queue = asyncio.Queue()
        self._loop = asyncio.get_event_loop()
        self._button = Button(pin, pull_up=pull_up, bounce_time=debounce_ms / 1000)
        self._button.when_pressed = lambda: self._emit(False)
        self._button.when_released = lambda: self._emit(True)
        log.info("hook on GPIO %d | invert=%s | debounce=%dms", pin, invert, debounce_ms)

    def _emit(self, off_hook: bool) -> None:
        # gpiozero calls this from its own thread.
        if self.invert:
            off_hook = not off_hook
        self._loop.call_soon_threadsafe(self._queue.put_nowait, off_hook)

    async def events(self):
        # Report the current position first, so starting up with the handset
        # already lifted opens a call instead of waiting for an edge.
        yield self._button.is_released != self.invert
        while True:
            yield await self._queue.get()

    async def close(self) -> None:
        self._button.close()


class StdinHook(HookSource):
    """Keyboard stand-in: ENTER lifts, ENTER replaces, 'q' quits.

    This is what makes the prototype usable before the cradle switch is wired —
    the call lifecycle is identical, only the trigger differs.
    """

    def __init__(self) -> None:
        self._off_hook = False

    async def events(self):
        loop = asyncio.get_event_loop()
        print("\n☎  ENTER = lift handset / replace handset, q = quit\n", flush=True)
        while True:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if not line or line.strip().lower() == "q":
                if self._off_hook:
                    yield False
                return
            self._off_hook = not self._off_hook
            print("☎  *click*  " + ("lifted" if self._off_hook else "replaced"), flush=True)
            yield self._off_hook


class AlwaysOffHook(HookSource):
    """Permanently off-hook — one call that never ends. For soak tests."""

    async def events(self):
        yield True
        await asyncio.Event().wait()


def make_hook(config: dict) -> HookSource:
    source = str(config.get("source", "stdin")).lower()
    if source == "gpio":
        return GpioHook(
            int(config.get("pin", 17)),
            invert=bool(config.get("invert", False)),
            debounce_ms=int(config.get("debounce_ms", 50)),
            pull_up=bool(config.get("pull_up", True)),
        )
    if source == "always":
        return AlwaysOffHook()
    if source != "stdin":
        log.warning("unknown hook source %r — falling back to stdin", source)
    return StdinHook()
