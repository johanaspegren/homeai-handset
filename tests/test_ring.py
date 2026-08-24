"""The status ring — and, mostly, not letting it lie.

The Arduino holds whatever frame it was last given, so every path that stops
driving the ring has to leave it somewhere honest. A stale "speaking" pulse is
how a working system ends up looking broken.
"""

import unittest
from unittest.mock import patch

from handset_service import config
from handset_service.ring import Ring


class FakeHalo:
    def __init__(self) -> None:
        self.states = []
        self.closed = False

    def set_state(self, state: str) -> None:
        if self.closed:
            raise RuntimeError("port closed")
        self.states.append(state)

    def close(self) -> None:
        self.closed = True


class RingTests(unittest.TestCase):
    def test_states_reach_the_ring(self):
        halo = FakeHalo()
        ring = Ring(halo=halo)
        ring.set("thinking")
        ring.rest()
        self.assertEqual(halo.states, ["thinking", config.HALO_RESTING_STATE])

    def test_release_leaves_it_resting_then_closes(self):
        halo = FakeHalo()
        ring = Ring(halo=halo)
        ring.release(state="resting")
        self.assertEqual(halo.states, ["resting"])
        self.assertTrue(halo.closed)

    def test_a_released_ring_ignores_further_states(self):
        halo = FakeHalo()
        ring = Ring(halo=halo)
        ring.release()
        ring.set("speaking")   # hands free owns the port now — not ours to drive
        self.assertEqual(halo.states, [])

    def test_no_ring_plugged_in_is_not_an_error(self):
        ring = Ring(port="/nonexistent/ttyUSB9")
        self.assertFalse(ring.acquire())
        ring.set("thinking")   # must not raise
        ring.rest()

    def test_a_ring_that_dies_mid_call_is_dropped_quietly(self):
        halo = FakeHalo()
        ring = Ring(halo=halo)
        halo.closed = True     # someone unplugged it
        ring.set("thinking")   # must not raise
        self.assertFalse(ring._owned, "a dead ring should stop being driven")

    def test_disabled_by_config_never_opens_the_port(self):
        with patch.object(config, "HALO_ENABLED", False):
            ring = Ring(port="/dev/ttyUSB0")
            self.assertFalse(ring.acquire())


if __name__ == "__main__":
    unittest.main()
