"""The Zodiac client: hook switch in, audio through, nothing clever.

The Pi owns hardware and transport only. It has no idea what speech recognition
or a language model is — it lifts a socket when the handset lifts, pushes
microphone bytes up, plays whatever comes back, and drops everything when the
handset goes down.
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
from .logging_config import setup as setup_logging

log = logging.getLogger("zodiac")


def _log(stage: str, message: str, level: int = logging.INFO) -> None:
    log.log(level, message, extra={"stage": stage})


class ZodiacClient:
    def __init__(self, settings: dict) -> None:
        self.settings = settings
        self.terminal_id = settings["terminal"]["id"]
        self.url = settings["homeai"]["websocket_url"]
        audio = settings["audio"]
        self.mic = Microphone(audio["capture_device"], audio["capture_rate"],
                              frame_bytes(audio))
        self.earpiece = Earpiece(audio["playback_device"], audio["playback_rate"],
                                 audio["buffer_us"], audio["period_us"])
        self._mic_active = False
        self._hangup = asyncio.Event()
        self._call_task: asyncio.Task | None = None

    # --- top level ---------------------------------------------------------

    async def run(self) -> None:
        hook = make_hook(self.settings["hook"])
        try:
            async for off_hook in hook.events():
                _log("GPIO", "OFF_HOOK" if off_hook else "ON_HOOK")
                if off_hook:
                    await self._start_call()
                else:
                    await self._end_call()
        finally:
            await self._end_call()
            await hook.close()

    async def _start_call(self) -> None:
        await self._end_call()
        self._hangup.clear()
        self._call_task = asyncio.create_task(self._call())

    async def _end_call(self) -> None:
        if self._call_task is None:
            return
        self._hangup.set()
        task, self._call_task = self._call_task, None
        try:
            # Give it a moment to say goodbye politely, then take it apart.
            await asyncio.wait_for(task, timeout=3)
        except asyncio.TimeoutError:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        except Exception:
            log.exception("call ended badly")

    # --- one call ----------------------------------------------------------

    async def _call(self) -> None:
        """Stay connected for as long as the handset is up, reconnecting if the
        network or the server blinks."""
        delay = self.settings["homeai"]["reconnect_delay_s"]
        max_delay = self.settings["homeai"]["reconnect_max_delay_s"]
        try:
            while not self._hangup.is_set():
                try:
                    await self._session()
                    delay = self.settings["homeai"]["reconnect_delay_s"]
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    if self._hangup.is_set():
                        break
                    _log("NET", f"connection lost ({exc}) — retrying in {delay:.0f}s",
                         logging.WARNING)
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, max_delay)
        finally:
            self._mic_active = False
            await self.mic.stop()
            await self.earpiece.stop()

    async def _session(self) -> None:
        async with websockets.connect(self.url, max_size=None) as ws:
            _log("NET", f"websocket connected | {self.url}")
            await ws.send(json.dumps(protocol.message(
                protocol.OFF_HOOK, terminal_id=self.terminal_id)))

            await self.mic.start()
            pump_mic = asyncio.create_task(self._pump_mic(ws))
            pump_server = asyncio.create_task(self._pump_server(ws))
            hangup = asyncio.create_task(self._hangup.wait())
            try:
                done, _ = await asyncio.wait(
                    [pump_mic, pump_server, hangup],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if hangup in done:
                    with contextlib.suppress(Exception):
                        await ws.send(json.dumps(protocol.message(protocol.ON_HOOK)))
                    _log("NET", "hung up")
                for task in done:  # surface a pump that died on an exception
                    if task is not hangup:
                        task.result()
            finally:
                for task in (pump_mic, pump_server, hangup):
                    task.cancel()
                await self.mic.stop()
                await self.earpiece.stop()

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
