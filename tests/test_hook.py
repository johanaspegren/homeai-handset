"""The hook switch's wiring sense.

This is the one piece of client logic where being wrong is a privacy problem
rather than an annoyance: get `invert` backwards and the phone opens a call
while sitting in the cradle, so the microphone is live at exactly the times the
hook is supposed to guarantee it isn't.

GpioHook needs gpiozero, which isn't installed on the homeai box (it's an apt
package on the Pi), so a fake Button stands in. That's enough to pin the sense
in both directions and the startup position — the path that shipped broken
because `stdin` was the default and nothing ever ran it.
"""

import asyncio
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "zodiac-client"))


class FakeButton:
    """Just enough gpiozero.Button. Deliberately has no `is_released` — the
    real one doesn't either, and assuming it did is what broke on the Pi."""

    def __init__(self, pin, *, pull_up=True, bounce_time=None):
        self.pin = pin
        self.pull_up = pull_up
        self.bounce_time = bounce_time
        self.is_pressed = False
        self.when_pressed = None
        self.when_released = None
        self.closed = False

    def press(self):
        self.is_pressed = True
        if self.when_pressed:
            self.when_pressed()

    def release(self):
        self.is_pressed = False
        if self.when_released:
            self.when_released()

    def close(self):
        self.closed = True


class HookSenseTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Inject a fake gpiozero before zodiac_client.gpio imports Button.
        self._saved = sys.modules.get("gpiozero")
        sys.modules["gpiozero"] = types.SimpleNamespace(Button=FakeButton)
        self.addCleanup(self._restore)
        for name in [n for n in sys.modules if n.startswith("zodiac_client.gpio")]:
            del sys.modules[name]

    def _restore(self):
        if self._saved is None:
            sys.modules.pop("gpiozero", None)
        else:
            sys.modules["gpiozero"] = self._saved

    def _hook(self, *, invert):
        from zodiac_client.gpio import GpioHook

        hook = GpioHook(17, invert=invert)
        return hook, hook._button

    async def _first(self, hook):
        """The position reported at startup, before any edge."""
        events = hook.events()
        try:
            return await anext(events)
        finally:
            await events.aclose()

    # --- the Zodiac: switch closes when the handset is LIFTED ---------------

    async def test_zodiac_starting_in_the_cradle_is_on_hook(self):
        hook, button = self._hook(invert=True)
        button.is_pressed = False           # handset down, switch open
        self.assertFalse(await self._first(hook))

    async def test_zodiac_starting_lifted_opens_a_call(self):
        hook, button = self._hook(invert=True)
        button.is_pressed = True            # handset already up at boot
        self.assertTrue(await self._first(hook))

    async def test_zodiac_lifting_reports_off_hook(self):
        hook, button = self._hook(invert=True)
        events = hook.events()
        await anext(events)                 # discard the startup position
        button.press()                      # handset lifted
        self.assertTrue(await anext(events))
        button.release()                    # handset replaced
        self.assertFalse(await anext(events))
        await events.aclose()

    # --- the classic cradle plunger: switch closes when the handset is DOWN --

    async def test_plunger_starting_in_the_cradle_is_on_hook(self):
        hook, button = self._hook(invert=False)
        button.is_pressed = True            # handset down, switch closed
        self.assertFalse(await self._first(hook))

    async def test_plunger_lifting_reports_off_hook(self):
        hook, button = self._hook(invert=False)
        events = hook.events()
        await anext(events)
        button.release()                    # handset lifted
        self.assertTrue(await anext(events))
        await events.aclose()

    # --- the two paths must agree ------------------------------------------

    async def test_startup_position_matches_the_edge_for_the_same_state(self):
        """The bug class this guards: startup position and edge events used to
        derive the sense separately, so they could disagree."""
        for invert in (True, False):
            for pressed in (True, False):
                with self.subTest(invert=invert, pressed=pressed):
                    hook, button = self._hook(invert=invert)
                    button.is_pressed = pressed
                    at_start = await self._first(hook)

                    edge_hook, edge_button = self._hook(invert=invert)
                    events = edge_hook.events()
                    await anext(events)
                    # Drive the switch to the same physical state via an edge.
                    edge_button.press() if pressed else edge_button.release()
                    from_edge = await anext(events)
                    await events.aclose()

                    self.assertEqual(at_start, from_edge)


class HookConfigTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._saved = sys.modules.get("gpiozero")
        sys.modules["gpiozero"] = types.SimpleNamespace(Button=FakeButton)
        self.addCleanup(self._restore)
        for name in [n for n in sys.modules if n.startswith("zodiac_client.gpio")]:
            del sys.modules[name]

    def _restore(self):
        if self._saved is None:
            sys.modules.pop("gpiozero", None)
        else:
            sys.modules["gpiozero"] = self._saved

    async def test_make_hook_passes_the_shipped_config_through(self):
        """config.yaml says gpio/17/invert — check it reaches the Button."""
        import yaml

        from zodiac_client.gpio import make_hook

        path = Path(__file__).resolve().parent.parent / "zodiac-client" / "config.yaml"
        settings = yaml.safe_load(path.read_text())["hook"]
        hook = make_hook(settings)
        self.assertEqual(hook._button.pin, settings["pin"])
        self.assertEqual(hook.invert, settings["invert"])
        self.assertEqual(hook._button.bounce_time, settings["debounce_ms"] / 1000)
        await hook.close()
        self.assertTrue(hook._button.closed)


if __name__ == "__main__":
    unittest.main()
