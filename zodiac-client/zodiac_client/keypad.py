"""The Zodiac's keypad — a passive switch matrix read by scanning.

The original phone electronics are gone, so the keypad is seven bare
conductors on GPIO with nothing between them and the Pi: no diodes, no series
resistors, no driver chip. A pressed key is simply 25-300 ohms between two of
those seven pins.

That "nothing between them" is the whole design constraint. If two GPIOs were
ever driven as outputs at the same time and a key shorted them together, one
pin would be sourcing current straight into the other with no resistance to
limit it, and the Pi's pad is what gives way. So the scan holds to one rule,
enforced here rather than trusted to the caller:

    at any instant, exactly ONE matrix pin is an output, it is LOW,
    and every other matrix pin is an input with its pull-up on.

Then the worst a pressed key can do is connect a pull-up to ground, which is
what a pull-up is for. Nothing is ever driven HIGH — a key bridging a HIGH
output to a LOW output is the failure this file exists to make impossible.
`tests/test_keypad.py` fails if either half of that rule is broken.

Reading the matrix is otherwise unremarkable: drive one line low, and any other
line that follows it down is on the far side of a closed contact. Which two
pins that is identifies the key, via a mapping in config.yaml — the phone has
had thirty-odd years to have its keypad wired however the factory felt, so the
pairs are measured with `tools/keypad_mapper.py` and configured, never guessed.

Like the hook, this is hardware input and nothing more. Events are logged; the
protocol to the service is unchanged.
"""

import asyncio
import logging
import sys
import threading
import time
from dataclasses import dataclass

log = logging.getLogger("zodiac.keypad")

KEY_DOWN = "KEY_DOWN"
KEY_UP = "KEY_UP"


@dataclass(frozen=True)
class KeyEvent:
    type: str   # KEY_DOWN or KEY_UP
    key: str    # the label from config.yaml's mapping, e.g. "1"


def parse_mapping(mapping: dict, pins: list[int]) -> dict[frozenset, str]:
    """config.yaml's `{"1": [22, 26]}` into `{frozenset({22, 26}): "1"}`.

    Pairs are unordered because the contact is: which of the two pins happens
    to be the one driven low during a given sweep is an accident of scan order,
    not a property of the key. [22, 26] and [26, 22] are the same button.
    """
    out: dict[frozenset, str] = {}
    for label, pair in (mapping or {}).items():
        pair = [int(p) for p in pair]
        if len(set(pair)) != 2:
            log.warning("keypad mapping %r is not a pair of distinct pins — ignored", label)
            continue
        unknown = [p for p in pair if p not in pins]
        if unknown:
            log.warning("keypad mapping %r uses pins not in keypad.pins: %s — ignored",
                        label, unknown)
            continue
        out[frozenset(pair)] = str(label)
    return out


class Matrix:
    """One sweep of the keypad, returning the contacts currently closed."""

    def closed_pairs(self) -> set[frozenset]:  # pragma: no cover - interface
        raise NotImplementedError

    def close(self) -> None:
        pass


class LgpioMatrix(Matrix):
    """The real keypad, through lgpio's persistent chip handle.

    gpiozero is the wrong tool here and deliberately isn't used: changing a
    line's direction through it means closing one device object and building
    another, seventy times a second, seven lines at a time. lgpio re-claims a
    line on a handle we already hold — an ioctl, no Python object per scan.

    Sharing the chip with the hook's gpiozero Button is fine: separate handles
    on gpiochip0 are allowed as long as they don't claim the same lines, and
    GPIO17 is not in the matrix.
    """

    def __init__(self, pins: list[int], *, chip: int = 0, settle_us: int = 50) -> None:
        import lgpio

        self._lgpio = lgpio
        self.pins = list(pins)
        self._settle_s = settle_us / 1_000_000
        self._handle = lgpio.gpiochip_open(chip)
        for pin in self.pins:
            self._as_input(pin)
        log.info("keypad on GPIO %s | chip %d", self.pins, chip)

    def _as_input(self, pin: int) -> None:
        self._lgpio.gpio_claim_input(self._handle, pin, self._lgpio.SET_PULL_UP)

    def closed_pairs(self) -> set[frozenset]:
        closed: set[frozenset] = set()
        for driver in self.pins:
            # A line can only change direction by being released first, so the
            # window where `driver` is an output starts here and is closed in
            # the finally below — no early return or exception may skip it.
            self._lgpio.gpio_free(self._handle, driver)
            try:
                self._lgpio.gpio_claim_output(self._handle, driver, 0)   # LOW. Only ever LOW.
                if self._settle_s:
                    time.sleep(self._settle_s)
                for sensed in self.pins:
                    if sensed == driver:
                        continue
                    if self._lgpio.gpio_read(self._handle, sensed) == 0:
                        closed.add(frozenset((driver, sensed)))
            finally:
                self._lgpio.gpio_free(self._handle, driver)
                self._as_input(driver)
        return closed

    def close(self) -> None:
        for pin in self.pins:
            try:
                self._lgpio.gpio_free(self._handle, pin)
            except Exception:  # already free, or the chip went away
                pass
        self._lgpio.gpiochip_close(self._handle)


class Keypad:
    """Scans a Matrix on its own thread and yields debounced key events.

    The state machine is `_step`, kept pure and separate from the thread so the
    tests can drive it with a fake clock in milliseconds instead of pressing
    buttons in real time.
    """

    def __init__(self, matrix: Matrix, mapping: dict[frozenset, str], *,
                 debounce_ms: int = 30, scan_hz: int = 70) -> None:
        self.matrix = matrix
        self.mapping = mapping
        self.debounce_s = debounce_ms / 1000
        self.interval_s = 1 / max(scan_hz, 1)
        self._key: str | None = None          # what we've reported as held
        self._candidate: str | None = None    # what the last sweep saw
        self._changed_at = 0.0
        self._unmapped: set[frozenset] = set()
        self._queue: asyncio.Queue | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # --- the part worth testing --------------------------------------------

    def _reading(self, pairs: set[frozenset]) -> str | None:
        """Which key one sweep saw, or None.

        Someone resting a thumb across two buttons, or a ghost contact, closes
        more than one pair. Rather than report that as a release, keep hold of
        the key already down — a spurious KEY_UP while a finger is still on the
        button is the worse lie. Otherwise take the pairs in a stable order so
        the choice doesn't flap between sweeps.
        """
        keys = sorted({self.mapping[pair] for pair in pairs if pair in self.mapping})
        for pair in pairs:
            if pair not in self.mapping and pair not in self._unmapped:
                # Free measurement: run the client, press the unknown buttons,
                # and read the pairs out of the log straight into config.yaml.
                self._unmapped.add(pair)
                a, b = sorted(pair)
                log.info("unmapped contact %d <-> %d — add it to keypad.mapping", a, b)
        if not keys:
            return None
        if self._key in keys:
            return self._key
        return keys[0]

    def _step(self, pairs: set[frozenset], now: float) -> list[KeyEvent]:
        """One sweep's worth of debouncing.

        A reading has to hold still for debounce_ms before it's believed, which
        swallows contact bounce on both edges. Because events are emitted only
        when the settled reading *differs* from what's already reported, a held
        button produces one KEY_DOWN and then nothing until it's released.
        """
        reading = self._reading(pairs)
        if reading != self._candidate:
            self._candidate = reading
            self._changed_at = now
            return []
        if reading == self._key or now - self._changed_at < self.debounce_s:
            return []
        events = []
        if self._key is not None:
            events.append(KeyEvent(KEY_UP, self._key))
        self._key = reading
        if reading is not None:
            events.append(KeyEvent(KEY_DOWN, reading))
        return events

    # --- scanning ----------------------------------------------------------

    def _scan_forever(self) -> None:
        while not self._stop.is_set():
            try:
                events = self._step(self.matrix.closed_pairs(), time.monotonic())
            except Exception:
                log.exception("keypad scan failed — stopping the keypad")
                return
            for event in events:
                self._emit(event)
            self._stop.wait(self.interval_s)

    def _emit(self, event: KeyEvent) -> None:
        # Called from the scan thread; the queue belongs to the event loop.
        if self._loop and self._queue:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, event)

    async def events(self):
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue()
        self._thread = threading.Thread(target=self._scan_forever,
                                        name="keypad-scan", daemon=True)
        self._thread.start()
        while True:
            yield await self._queue.get()

    async def close(self) -> None:
        self._stop.set()
        if self._thread:
            # The scan sleeps between sweeps; give it one interval to notice.
            await asyncio.to_thread(self._thread.join, self.interval_s * 4)
            self._thread = None
        self.matrix.close()


class StdinKeypad:
    """The keypad, typed instead of pressed — the hook's `stdin` source, for the
    buttons.

    The Zodiac's keypad is seven bare conductors on GPIO, so without the phone
    on the desk there is no way to press anything at all, and the whole path
    from a button to music playing is untestable. This makes it testable
    anywhere: type `5` and ENTER, get KEY_DOWN 5 followed by KEY_UP 5.

    A typed line is instantaneous, so the press and the release arrive together.
    Nothing downstream minds — the service acts on KEY_DOWN — but a hold cannot
    be simulated here, and anything that eventually cares about how long a
    button was held will need the real keypad to test it.
    """

    def __init__(self) -> None:
        self._stop = False

    async def events(self):
        loop = asyncio.get_running_loop()
        log.info("keypad on stdin — type digits and ENTER (e.g. 5, then *)")
        while not self._stop:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if not line:            # stdin closed: no more presses, ever
                return
            for char in line.strip():
                yield KeyEvent(KEY_DOWN, char)
                yield KeyEvent(KEY_UP, char)

    async def close(self) -> None:
        self._stop = True


def make_keypad(config: dict) -> Keypad | StdinKeypad | None:
    """Build the keypad from config, or None if it isn't wired on this Zodiac.

    Returning None rather than a no-op stand-in is deliberate: unlike the hook,
    which the phone cannot work without, a keypad-less Zodiac is a complete
    telephone. Nothing downstream should have to pretend otherwise.

    `source` mirrors the hook's: `gpio` is the real matrix, `stdin` is the
    keyboard standing in for it while the phone is in pieces on a bench.
    """
    if not config or not config.get("enabled"):
        return None
    if config.get("source", "gpio") == "stdin":
        return StdinKeypad()
    pins = [int(p) for p in config.get("pins", [])]
    if len(set(pins)) < 2:
        log.warning("keypad enabled but %d pins configured — disabled", len(pins))
        return None
    mapping = parse_mapping(config.get("mapping", {}), pins)
    if not mapping:
        log.warning("keypad enabled with no usable mapping — "
                    "contacts will be logged as unmapped")
    matrix = LgpioMatrix(pins, chip=int(config.get("chip", 0)),
                         settle_us=int(config.get("settle_us", 50)))
    return Keypad(matrix, mapping,
                  debounce_ms=int(config.get("debounce_ms", 30)),
                  scan_hz=int(config.get("scan_hz", 70)))
