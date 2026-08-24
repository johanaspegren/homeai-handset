"""ALSA in, ALSA out — the client's whole relationship with sound.

Deliberately just `arecord` and `aplay` over pipes: no PipeWire, no PortAudio,
no numpy. That keeps the client installable on a Pi Zero W (ARMv6) where wheels
are scarce, and it means the audio path can be debugged with the same two
commands by hand.
"""

import asyncio
import logging

log = logging.getLogger("zodiac.audio")


class Microphone:
    """`arecord` streaming raw PCM, read frame by frame."""

    def __init__(self, device: str, rate: int, frame_bytes: int) -> None:
        self.device = device
        self.rate = rate
        self.frame_bytes = frame_bytes
        self._proc = None

    async def start(self) -> None:
        self._proc = await asyncio.create_subprocess_exec(
            "arecord", "-q",
            "-D", self.device,
            "-f", "S16_LE",
            "-r", str(self.rate),
            "-c", "1",
            "-t", "raw",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        log.info("microphone open | device=%s | %d Hz", self.device, self.rate)

    async def frames(self):
        """Yield fixed-size frames until the recorder stops."""
        while self._proc and self._proc.stdout:
            try:
                yield await self._proc.stdout.readexactly(self.frame_bytes)
            except asyncio.IncompleteReadError:
                return

    async def stop(self) -> None:
        await _terminate(self._proc)
        self._proc = None
        log.info("microphone closed")


class Earpiece:
    """`aplay` fed from stdin, so speech starts as the first bytes arrive.

    One player lives for the whole call: restarting ALSA between sentences adds
    a click and a hundred milliseconds. Killing it is also how a hang-up cuts
    Kiri off mid-word — whatever is still buffered dies with the process.
    """

    def __init__(self, device: str, rate: int, buffer_us: int = 200_000,
                 period_us: int = 50_000) -> None:
        self.device = device
        self.rate = rate
        self.buffer_us = buffer_us
        self.period_us = period_us
        self._proc = None

    async def start(self, rate: int = None) -> None:
        """Open the player. `rate` overrides config with the rate the server
        actually announced, so the earpiece can never play back at the wrong
        speed."""
        if rate:
            self.rate = rate
        self._proc = await asyncio.create_subprocess_exec(
            "aplay", "-q",
            "-D", self.device,
            "-f", "S16_LE",
            "-r", str(self.rate),
            "-c", "1",
            "-t", "raw",
            f"--buffer-time={self.buffer_us}",
            f"--period-time={self.period_us}",
            stdin=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        log.info("earpiece open | device=%s | %d Hz", self.device, self.rate)

    async def play(self, pcm: bytes) -> None:
        if not self._proc or not self._proc.stdin:
            return
        try:
            self._proc.stdin.write(pcm)
            await self._proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            log.warning("earpiece pipe closed — playback stopped")

    async def stop(self) -> None:
        await _terminate(self._proc)
        self._proc = None
        log.info("earpiece closed")


async def _terminate(proc) -> None:
    if proc is None or proc.returncode is not None:
        return
    try:
        proc.terminate()
        await asyncio.wait_for(proc.wait(), timeout=2)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
    except ProcessLookupError:
        pass


async def check_devices(capture: str, playback: str) -> list[str]:
    """Diagnostic: report anything obviously wrong with the configured devices."""
    problems = []
    for name, device in (("capture", capture), ("playback", playback)):
        args = (["arecord", "-D", device, "--dump-hw-params", "-d", "1"] if name == "capture"
                else ["aplay", "-D", device, "--dump-hw-params", "/dev/zero"])
        try:
            proc = await asyncio.create_subprocess_exec(
                *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
            )
            _, err = await asyncio.wait_for(proc.communicate(), timeout=5)
            if b"No such" in err or b"cannot open" in err.lower():
                problems.append(f"{name} device {device!r}: {err.decode(errors='replace').strip()}")
        except FileNotFoundError:
            problems.append(f"{name}: alsa-utils not installed (need arecord/aplay)")
        except asyncio.TimeoutError:
            pass
    return problems
