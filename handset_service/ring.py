"""Kiri's status ring (homeai-halo), driven by the telephone.

The ring used to be homeai-voice's to drive. Voice doesn't run by default any
more, so the handset — which does — takes it over: resting when nothing is
happening, and following the call while one is in progress.

Two things this deliberately gets right:

*Handing back.* When hands free starts homeai-voice, that process wants the
same serial port and has richer telemetry to show (a live mic VU). So the ring
is released before voice starts and reclaimed after it stops.

*Letting go.* The Arduino holds whatever state it was last told, forever. Kill
the process that was driving it and the ring freezes mid-animation — which is
exactly how a stale orange "speaking" pulse ended up looking like a fault. So
shutdown always sends the ring somewhere sensible.

Entirely optional: no ring plugged in, no pyserial, no port — it logs once and
no-ops. The telephone works exactly the same.
"""

import logging
import os
import sys

from . import config

log = logging.getLogger("handset")


def _load_halo_class():
    """Import the Halo driver from the homeai-halo repo (single-sourced there)."""
    halo_dir = str(config.HALO_DIR)
    if halo_dir not in sys.path:
        sys.path.insert(0, halo_dir)
    from halo import Halo

    return Halo


class Ring:
    """The LED ring, or a polite pretence of one if it isn't there."""

    def __init__(self, *, port=None, halo=None) -> None:
        self.port = port or config.HALO_PORT
        self._halo = halo          # injectable for tests
        self._owned = halo is not None
        self._warned = False

    # --- the port ----------------------------------------------------------

    def acquire(self) -> bool:
        """Open the port and take the ring to its resting state."""
        if not config.HALO_ENABLED or self._owned:
            return self._owned
        if not os.path.exists(self.port):
            self._warn("no ring at %s — running without it", self.port)
            return False
        try:
            self._halo = _load_halo_class()(port=self.port)
            self._halo.open()
            self._owned = True
            log.info("ring on %s", self.port, extra={"stage": "RING", "terminal": "-"})
            self.set(config.HALO_RESTING_STATE)
            return True
        except Exception as exc:
            self._warn("could not open the ring (%s) — running without it", exc)
            self._halo = None
            return False

    def release(self, state: str = None) -> None:
        """Put the ring somewhere sensible and let the port go.

        Used before hands free starts homeai-voice (which wants the same port),
        and at shutdown — never leave a frozen animation behind.
        """
        if not self._owned or self._halo is None:
            return
        try:
            if state:
                self.set(state)
            self._halo.close()   # the driver sends 'off' as it closes
        except Exception:
            logging.exception("releasing the ring failed")
        finally:
            self._halo = None
            self._owned = False

    # --- what it shows -----------------------------------------------------

    def set(self, state: str) -> None:
        """Show a state. Never raises — a ring is not worth a dropped call."""
        if not self._owned or self._halo is None:
            return
        try:
            self._halo.set_state(state)
        except Exception as exc:
            self._warn("lost the ring (%s)", exc)
            self._halo = None
            self._owned = False

    def rest(self) -> None:
        self.set(config.HALO_RESTING_STATE)

    def _warn(self, message: str, *args) -> None:
        # Once only: a missing ring shouldn't fill the log on every call.
        if not self._warned:
            self._warned = True
            log.warning(message, *args, extra={"stage": "RING", "terminal": "-"})


# The service's one ring, wired up in websocket.py's lifespan.
ring = Ring()
