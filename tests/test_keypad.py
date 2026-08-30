"""The keypad scan — the GPIO safety rule first, the debounce second.

The keypad is seven conductors straight onto GPIO with no diodes and no series
resistors, so the interesting failure here isn't a missed keypress, it's two
pins driven as outputs while a pressed key shorts them together. That can't be
observed from a passing keypress test, so the fake lgpio below refuses to allow
it: a second simultaneous output, or any line driven HIGH, fails the test
rather than the hardware.

lgpio itself is an apt package on the Pi and isn't installed on the homeai box,
which is the same reason tests/test_hook.py fakes gpiozero.
"""

import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "zodiac-client"))


class UnsafeScan(AssertionError):
    """Raised when the scan does something that could damage the Pi."""


class FakeLgpio:
    """Enough of lgpio to model seven wires, and strict about the dangerous parts.

    Contacts are held as unordered pairs; a pin reads low when it is connected,
    through any chain of closed contacts, to a line being driven low. Modelling
    it as connectivity rather than a lookup means a chord or a ghost contact
    behaves the way the copper would.
    """

    SET_PULL_UP = 32

    def __init__(self):
        self.contacts: set[frozenset] = set()
        self.inputs: set[int] = set()
        self.outputs: dict[int, int] = {}
        self.max_simultaneous_outputs = 0
        self.chips_open = 0

    # --- the API keypad.py uses ------------------------------------------

    def gpiochip_open(self, chip):
        self.chips_open += 1
        return 100 + chip

    def gpiochip_close(self, handle):
        self.chips_open -= 1

    def _check_free(self, pin):
        if pin in self.inputs or pin in self.outputs:
            raise UnsafeScan(f"GPIO {pin} claimed twice without being freed")

    def gpio_claim_input(self, handle, pin, flags=0):
        self._check_free(pin)
        if flags != self.SET_PULL_UP:
            raise UnsafeScan(f"GPIO {pin} claimed as input without its pull-up")
        self.inputs.add(pin)

    def gpio_claim_output(self, handle, pin, level):
        self._check_free(pin)
        if level != 0:
            raise UnsafeScan(f"GPIO {pin} driven HIGH — a pressed key could short it")
        self.outputs[pin] = level
        if len(self.outputs) > 1:
            raise UnsafeScan(f"two outputs at once: {sorted(self.outputs)}")
        self.max_simultaneous_outputs = max(self.max_simultaneous_outputs,
                                            len(self.outputs))

    def gpio_free(self, handle, pin):
        self.inputs.discard(pin)
        self.outputs.pop(pin, None)

    def gpio_read(self, handle, pin):
        if pin in self.outputs:
            return self.outputs[pin]
        if pin not in self.inputs:
            raise UnsafeScan(f"GPIO {pin} read while claimed as neither input nor output")
        return 0 if self._grounded(pin) else 1   # pull-up, unless a contact drags it down

    def _grounded(self, pin):
        seen, stack = {pin}, [pin]
        while stack:
            here = stack.pop()
            if self.outputs.get(here) == 0:
                return True
            for contact in self.contacts:
                if here in contact:
                    other = next(iter(contact - {here}))
                    if other not in seen:
                        seen.add(other)
                        stack.append(other)
        return False

    # --- test-side helpers ------------------------------------------------

    def press(self, a, b):
        self.contacts.add(frozenset((a, b)))

    def release(self, a, b):
        self.contacts.discard(frozenset((a, b)))


def _install_fake_lgpio(test):
    fake = FakeLgpio()
    saved = sys.modules.get("lgpio")
    sys.modules["lgpio"] = types.SimpleNamespace(
        SET_PULL_UP=fake.SET_PULL_UP,
        gpiochip_open=fake.gpiochip_open,
        gpiochip_close=fake.gpiochip_close,
        gpio_claim_input=fake.gpio_claim_input,
        gpio_claim_output=fake.gpio_claim_output,
        gpio_free=fake.gpio_free,
        gpio_read=fake.gpio_read,
    )

    def restore():
        if saved is None:
            sys.modules.pop("lgpio", None)
        else:
            sys.modules["lgpio"] = saved

    test.addCleanup(restore)
    return fake


PINS = [27, 22, 5, 6, 13, 19, 26]


class ScanSafetyTests(unittest.TestCase):
    """The rule that protects the pins: one output, low, restored before the next."""

    def setUp(self):
        self.fake = _install_fake_lgpio(self)
        from zodiac_client.keypad import LgpioMatrix

        self.matrix = LgpioMatrix(PINS, settle_us=0)
        self.addCleanup(self.matrix.close)

    def test_a_sweep_never_drives_two_lines_at_once(self):
        self.fake.press(22, 26)
        for _ in range(5):
            self.matrix.closed_pairs()
        self.assertEqual(self.fake.max_simultaneous_outputs, 1)

    def test_every_line_is_an_input_with_its_pull_up_between_sweeps(self):
        self.matrix.closed_pairs()
        self.assertEqual(self.fake.outputs, {})
        self.assertEqual(sorted(self.fake.inputs), sorted(PINS))

    def test_a_line_is_restored_even_if_the_sweep_blows_up(self):
        """A read that raises mid-sweep must not leave a line driving low."""
        boom = RuntimeError("gpio went away")

        def explode(handle, pin):
            raise boom

        sys.modules["lgpio"].gpio_read = explode
        with self.assertRaises(RuntimeError):
            self.matrix.closed_pairs()
        self.assertEqual(self.fake.outputs, {})

    def test_closing_releases_the_chip(self):
        self.matrix.close()
        self.assertEqual(self.fake.chips_open, 0)
        self.addCleanup(setattr, self.matrix, "close", lambda: None)


class ScanReadingTests(unittest.TestCase):
    def setUp(self):
        self.fake = _install_fake_lgpio(self)
        from zodiac_client.keypad import LgpioMatrix

        self.matrix = LgpioMatrix(PINS, settle_us=0)
        self.addCleanup(self.matrix.close)

    def test_nothing_pressed_is_no_contacts(self):
        self.assertEqual(self.matrix.closed_pairs(), set())

    def test_a_press_shows_up_as_its_unordered_pair(self):
        self.fake.press(22, 26)
        self.assertEqual(self.matrix.closed_pairs(), {frozenset({22, 26})})

    def test_the_pair_is_found_whichever_end_is_driven(self):
        """Scan order decides which pin is the driver; the key doesn't care."""
        from zodiac_client.keypad import LgpioMatrix

        self.fake.press(22, 26)
        forwards = self.matrix.closed_pairs()
        # A GPIO line can only be claimed once, so hand the pins over rather
        # than holding two matrices on them at the same time.
        self.matrix.close()
        self.matrix = LgpioMatrix(list(reversed(PINS)), settle_us=0)
        self.assertEqual(forwards, self.matrix.closed_pairs())


class DebounceTests(unittest.TestCase):
    """The state machine, driven with a fake clock instead of a finger."""

    def setUp(self):
        from zodiac_client.keypad import Keypad, Matrix

        self.keypad = Keypad(Matrix(), {frozenset({22, 26}): "1",
                                        frozenset({13, 26}): "2"},
                             debounce_ms=30)
        self.now = 0.0

    def step(self, *pins_pairs, after_ms=0):
        self.now += after_ms / 1000
        pairs = {frozenset(p) for p in pins_pairs}
        return self.keypad._step(pairs, self.now)

    def _press_1(self):
        self.step((22, 26))
        return self.step((22, 26), after_ms=40)

    def test_nothing_pressed_produces_nothing(self):
        self.assertEqual(self.step(), [])
        self.assertEqual(self.step(after_ms=100), [])

    def test_a_press_produces_one_key_down(self):
        from zodiac_client.keypad import KEY_DOWN, KeyEvent

        self.assertEqual(self.step((22, 26)), [])          # not settled yet
        self.assertEqual(self.step((22, 26), after_ms=40),
                         [KeyEvent(KEY_DOWN, "1")])

    def test_holding_repeats_nothing(self):
        self._press_1()
        for _ in range(20):
            self.assertEqual(self.step((22, 26), after_ms=15), [])

    def test_release_produces_one_key_up(self):
        from zodiac_client.keypad import KEY_UP, KeyEvent

        self._press_1()
        self.assertEqual(self.step(), [])                  # not settled yet
        self.assertEqual(self.step(after_ms=40), [KeyEvent(KEY_UP, "1")])
        self.assertEqual(self.step(after_ms=40), [])

    def test_bounce_shorter_than_the_debounce_is_swallowed(self):
        """Contact chatter on the way down mustn't become a burst of events."""
        from zodiac_client.keypad import KEY_DOWN, KeyEvent

        for _ in range(6):
            self.assertEqual(self.step((22, 26), after_ms=5), [])
            self.assertEqual(self.step(after_ms=5), [])
        self.assertEqual(self.step((22, 26)), [])
        self.assertEqual(self.step((22, 26), after_ms=40),
                         [KeyEvent(KEY_DOWN, "1")])

    def test_moving_to_another_key_releases_the_first(self):
        from zodiac_client.keypad import KEY_DOWN, KEY_UP, KeyEvent

        self._press_1()
        self.step((13, 26))
        self.assertEqual(self.step((13, 26), after_ms=40),
                         [KeyEvent(KEY_UP, "1"), KeyEvent(KEY_DOWN, "2")])

    def test_a_second_contact_does_not_release_the_key_being_held(self):
        """A thumb across two buttons, or a ghost, is not a release."""
        self._press_1()
        self.assertEqual(self.step((22, 26), (13, 26), after_ms=40), [])
        self.assertEqual(self.step((22, 26), after_ms=40), [])

    def test_unmapped_contacts_produce_no_events(self):
        self.step((5, 19))
        self.assertEqual(self.step((5, 19), after_ms=40), [])


class MappingConfigTests(unittest.TestCase):
    def test_pairs_are_unordered(self):
        from zodiac_client.keypad import parse_mapping

        mapping = parse_mapping({"1": [26, 22]}, PINS)
        self.assertEqual(mapping[frozenset({22, 26})], "1")

    def test_a_pin_outside_the_matrix_is_rejected_not_scanned(self):
        from zodiac_client.keypad import parse_mapping

        with self.assertLogs("zodiac.keypad", "WARNING"):
            self.assertEqual(parse_mapping({"9": [22, 17]}, PINS), {})

    def test_labels_survive_yaml_reading_them_as_numbers(self):
        from zodiac_client.keypad import parse_mapping

        self.assertEqual(parse_mapping({1: [22, 26]}, PINS)[frozenset({22, 26})], "1")

    def test_the_shipped_config_maps_the_measured_buttons(self):
        """config.yaml is the source of truth for wiring; check it agrees with
        what was measured on the phone."""
        import yaml

        from zodiac_client.keypad import parse_mapping

        path = Path(__file__).resolve().parent.parent / "zodiac-client" / "config.yaml"
        keypad = yaml.safe_load(path.read_text())["keypad"]
        self.assertNotIn(17, keypad["pins"], "GPIO17 is the hook, not the keypad")
        mapping = parse_mapping(keypad["mapping"], [int(p) for p in keypad["pins"]])
        self.assertEqual(mapping[frozenset({22, 26})], "1")
        self.assertEqual(mapping[frozenset({13, 26})], "2")
        self.assertEqual(mapping[frozenset({6, 26})], "3")

    def test_a_zodiac_without_a_keypad_gets_none(self):
        from zodiac_client.config import DEFAULTS
        from zodiac_client.keypad import make_keypad

        self.assertIsNone(make_keypad(DEFAULTS["keypad"]))
        self.assertIsNone(make_keypad({}))


if __name__ == "__main__":
    unittest.main()
