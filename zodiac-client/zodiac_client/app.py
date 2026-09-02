"""The Zodiac client: hook switch in, buttons in, audio through, nothing clever.

The Pi owns hardware and transport only. It has no idea what speech recognition
or a language model is — it pushes microphone bytes up, plays whatever comes
back, and reports which button was pressed without knowing what any of them do.

The socket outlives the handset. It comes up when the client starts and stays
up, with `off_hook` and `on_hook` as frames on it rather than as the thing that
opens and closes it — because the keypad is part of the telephone rather than
part of a conversation, and both things the buttons are for (music on, the room
opened up into a conference call) happen while the handset is in its cradle.

What does *not* outlive the handset is the microphone. `arecord` starts when the
handset is lifted and is killed when it goes down, so between "down" and
"lifted" there is no recording process on the Pi at all. That is the phone's
privacy guarantee, and a live socket must never soften it into a mute.
"""

import argparse
import asyncio
import contextlib
import json
import logging

import websockets

from . import protocol
from .audio import Earpiece, Microphone, check_devices
from .config import frame_bytes, load
from .gpio import make_hook
from .keypad import KEY_DOWN, make_keypad
from .logging_config import setup as setup_logging

log = logging.getLogger("zodiac")


def _log(stage: str, message: str, level: int = logging.INFO) -> None:
    log.log(level, message, extra={"stage": stage})


class ZodiacClient:
    def __init__(self, settings: dict) -> None:
        self.settings = settings
        self.terminal_id = settings["terminal"]["id"]
        # Which room the telephone stands in. The Pi does not work this out and
        # does not act on it — it announces it, and the server decides what
        # "here" means. That is what keeps a second Zodiac in the kitchen a
        # config file rather than a code change.
        self.place = settings["terminal"].get("place")
        self.url = settings["homeai"]["websocket_url"]
        audio = settings["audio"]
        self.mic = Microphone(audio["capture_device"], audio["capture_rate"],
                              frame_bytes(audio))
        self.earpiece = Earpiece(audio["playback_device"], audio["playback_rate"],
                                 audio["buffer_us"], audio["period_us"])
        self._mic_active = False
        self._off_hook = False
        self._ws = None
        self._mic_pump: asyncio.Task | None = None

    # --- top level ---------------------------------------------------------

    async def run(self) -> None:
        hook = make_hook(self.settings["hook"])
        keypad = make_keypad(self.settings.get("keypad", {}))
        # One connection, held from boot to shutdown, with the hook as a pair of
        # frames on it rather than the thing that opens and closes it. The phone
        # is on the network whether or not anyone is holding the handset —
        # which is what makes the buttons work in the cradle, and that is where
        # the handset is when you walk past and want music on.
        connection = asyncio.create_task(self._stay_connected())
        watch_keypad = asyncio.create_task(self._watch_keypad(keypad)) if keypad else None
        try:
            async for off_hook in hook.events():
                _log("GPIO", "OFF_HOOK" if off_hook else "ON_HOOK")
                if off_hook:
                    await self._lift()
                else:
                    await self._replace()
        finally:
            await self._replace()
            for task in (watch_keypad, connection):
                if task is not None:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
            if keypad is not None:
                await keypad.close()
            await hook.close()
            await self.earpiece.stop()

    async def _watch_keypad(self, keypad) -> None:
        """Presses go up the wire whenever there is a wire.

        What a button *means* is decided on the homeai box, exactly like what a
        sentence means: this end knows that button 5 was pressed and nothing
        about music. A press while the socket is down is logged and dropped —
        queueing it would mean music starting minutes later, when the network
        came back and nobody was standing there any more.
        """
        async for event in keypad.events():
            _log("KEYPAD", f"{event.type} {event.key}")
            if event.type != KEY_DOWN:
                continue
            if self._ws is None:
                _log("KEYPAD", f"{event.key} pressed while offline — dropped",
                     logging.WARNING)
                continue
            with contextlib.suppress(Exception):
                await self._ws.send(json.dumps(
                    protocol.message(protocol.KEY, key=event.key)))

    # --- the connection ----------------------------------------------------

    async def _stay_connected(self) -> None:
        """Keep one socket up, for as long as the client runs."""
        delay = self.settings["homeai"]["reconnect_delay_s"]
        max_delay = self.settings["homeai"]["reconnect_max_delay_s"]
        while True:
            try:
                async with websockets.connect(self.url, max_size=None) as ws:
                    self._ws = ws
                    _log("NET", f"websocket connected | {self.url}")
                    await ws.send(json.dumps(protocol.message(
                        protocol.HELLO, terminal_id=self.terminal_id,
                        place=self.place)))
                    # Reconnecting mid-call: the service lost the call when the
                    # socket went, so open a new one rather than leaving someone
                    # holding a handset connected to nothing.
                    if self._off_hook:
                        await self._open_call(ws)
                    delay = self.settings["homeai"]["reconnect_delay_s"]
                    await self._pump_server(ws)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _log("NET", f"connection lost ({exc}) — retrying in {delay:.0f}s",
                     logging.WARNING)
            finally:
                self._ws = None
                # A dropped socket is a dropped call: stop recording. The
                # handset may still be at someone's ear, but nothing is
                # listening at the other end, and a microphone running with
                # nowhere to send is exactly what the hook switch exists to
                # prevent.
                await self._stop_mic()
                await self.earpiece.stop()
            await asyncio.sleep(delay)
            delay = min(delay * 2, max_delay)

    # --- the hook ----------------------------------------------------------

    async def _lift(self) -> None:
        self._off_hook = True
        if self._ws is not None:
            await self._open_call(self._ws)

    async def _replace(self) -> None:
        was_up, self._off_hook = self._off_hook, False
        if was_up and self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.send(json.dumps(protocol.message(protocol.ON_HOOK)))
            _log("NET", "hung up")
        await self._stop_mic()
        await self.earpiece.stop()

    async def _open_call(self, ws) -> None:
        await ws.send(json.dumps(protocol.message(
            protocol.OFF_HOOK, terminal_id=self.terminal_id, place=self.place)))
        # The microphone exists only between "lifted" and "replaced". The socket
        # outliving the handset must never mean arecord does: with the handset
        # down there is no recording process at all, which is the phone's
        # privacy guarantee and not merely a mute.
        await self.mic.start()
        self._mic_pump = asyncio.create_task(self._pump_mic(ws))

    async def _stop_mic(self) -> None:
        self._mic_active = False
        if self._mic_pump is not None:
            self._mic_pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._mic_pump
            self._mic_pump = None
        await self.mic.stop()

    async def _pump_mic(self, ws) -> None:
        async for frame in self.mic.frames():
            if self._mic_active:
                await ws.send(frame)
        _log("AUDIO", "microphone stream ended", logging.WARNING)

    async def _pump_server(self, ws) -> None:
        async for message in ws:
            if isinstance(message, bytes):
                await self.earpiece.play(message)
                continue
            await self._on_control(json.loads(message))

    async def _on_control(self, message: dict) -> None:
        kind = message.get("type")
        if kind == protocol.CALL_STARTED:
            rate = (message.get("audio") or {}).get("rate")
            await self.earpiece.start(rate)
            _log("NET", f"call started | playback {self.earpiece.rate} Hz")
        elif kind == protocol.MIC:
            self._mic_active = bool(message.get("active"))
            _log("AUDIO", "microphone streaming" if self._mic_active else "microphone paused")
        elif kind == protocol.ASSISTANT_SPEAKING:
            _log("AUDIO", "assistant playback started")
        elif kind == protocol.ASSISTANT_FINISHED:
            _log("AUDIO", "assistant finished")
        elif kind == protocol.TRANSCRIPT:
            _log("STT", f'heard | "{message.get("text")}"')
        elif kind == protocol.LED:
            # Nothing is wired to the Zodiac's lamps yet — log the state so the
            # mapping can be worked out before anything depends on it.
            _log("LED", str(message.get("state")))
        elif kind == protocol.ERROR:
            _log("ERROR", str(message.get("message")), logging.ERROR)


async def _main(args) -> None:
    settings = load(args.config)
    setup_logging(settings["logging"]["level"])
    if args.check_audio:
        problems = await check_devices(
            settings["audio"]["capture_device"], settings["audio"]["playback_device"])
        for problem in problems:
            _log("AUDIO", problem, logging.ERROR)
        _log("AUDIO", "devices look usable" if not problems else "device problems found")
        return
    await ZodiacClient(settings).run()


def main() -> None:
    parser = argparse.ArgumentParser(description="Zodiac telephone client for HomeAI.")
    parser.add_argument("--config", default=None, help="path to config.yaml")
    parser.add_argument("--check-audio", action="store_true",
                        help="verify the configured ALSA devices and exit")
    args = parser.parse_args()
    try:
        asyncio.run(_main(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
