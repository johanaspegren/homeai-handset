#!/usr/bin/env python3
"""Call the handset service without a telephone.

Plays the Pi's part of the protocol from the homeai box itself: lifts the
handset, streams synthesised "user speech" up in real time, and writes whatever
comes back to a WAV you can listen to. Useful for testing the whole path — VAD,
Whisper, Kiri, Piper — when the Zodiac isn't on the bench.

    python tools/fake_call.py --say "what's the mower doing?"
    python tools/fake_call.py --url ws://homeai.local:8400/zodiac --say "hello"
    python tools/fake_call.py --wav question.wav      # a real recording instead

It prints the latency that matters: end of your speech -> first audio back.
"""

import argparse
import asyncio
import json
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import websockets

from handset_service import config, protocol

FRAME_MS = 40
FRAME_BYTES = int(config.CAPTURE_RATE * FRAME_MS / 1000) * 2


def resample(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    if src_rate == dst_rate:
        return pcm
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    n = int(len(samples) * dst_rate / src_rate)
    resampled = np.interp(
        np.linspace(0, len(samples) - 1, n), np.arange(len(samples)), samples)
    return resampled.astype(np.int16).tobytes()


def speech_pcm(text: str) -> bytes:
    """Synthesise the caller's line at the microphone's sample rate."""
    from handset_service import tts

    return resample(tts.synthesize(text), tts.sample_rate(), config.CAPTURE_RATE)


def wav_pcm(path: Path) -> bytes:
    with wave.open(str(path), "rb") as wav:
        pcm = wav.readframes(wav.getnframes())
        return resample(pcm, wav.getframerate(), config.CAPTURE_RATE)


def silence(ms: int) -> bytes:
    return b"\x00\x00" * int(config.CAPTURE_RATE * ms / 1000)


async def main(args) -> None:
    lines = [wav_pcm(Path(args.wav))] if args.wav else [speech_pcm(s) for s in args.say]
    reply_audio = bytearray()
    rate = 22050

    async with websockets.connect(args.url, max_size=None) as ws:
        await ws.send(json.dumps(protocol.message(
            protocol.OFF_HOOK, terminal_id="fake-call")))

        speech_end = None
        first_audio = None
        pending = list(lines)
        listening = asyncio.Event()

        async def talk():
            nonlocal speech_end
            while pending:
                await listening.wait()
                listening.clear()
                pcm = pending.pop(0)
                print(f"\n☎  speaking {len(pcm) / 2 / config.CAPTURE_RATE:.2f}s of audio")
                # Stream in real time so the server's VAD behaves as it would
                # with a live microphone.
                stream = silence(300) + pcm + silence(1200)
                for i in range(0, len(stream), FRAME_BYTES):
                    await ws.send(stream[i : i + FRAME_BYTES])
                    await asyncio.sleep(FRAME_MS / 1000)
                speech_end = time.perf_counter()

        talker = asyncio.create_task(talk())
        try:
            async for message in ws:
                if isinstance(message, bytes):
                    if first_audio is None and speech_end:
                        first_audio = time.perf_counter()
                        print(f"⏱  first audio back: {first_audio - speech_end:.2f}s "
                              "after end of speech")
                    reply_audio.extend(message)
                    continue
                event = json.loads(message)
                kind = event.get("type")
                if kind == protocol.CALL_STARTED:
                    rate = event["audio"]["rate"]
                    print(f"☎  call started | playback {rate} Hz")
                elif kind == protocol.TRANSCRIPT:
                    print(f"👤 heard: {event['text']}")
                elif kind == protocol.LISTENING:
                    listening.set()
                elif kind == protocol.ASSISTANT_FINISHED:
                    first_audio = None
                    if not pending and talker.done():
                        break
                elif kind == protocol.ERROR:
                    print(f"✖  {event['message']}")
        finally:
            talker.cancel()
            await ws.send(json.dumps(protocol.message(protocol.ON_HOOK)))

    if reply_audio:
        with wave.open(args.out, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(rate)
            wav.writeframes(bytes(reply_audio))
        print(f"\n🔊 reply audio written to {args.out} "
              f"({len(reply_audio) / 2 / rate:.1f}s) — play it with: aplay {args.out}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=f"ws://localhost:{config.PORT}/zodiac")
    parser.add_argument("--say", action="append", default=[],
                        help="a line for the caller to say (repeat for a multi-turn call)")
    parser.add_argument("--wav", help="a real recording to send instead of synthesised speech")
    parser.add_argument("--out", default="/tmp/handset-reply.wav")
    args = parser.parse_args()
    if not args.say and not args.wav:
        args.say = ["Hello, can you hear me?"]
    asyncio.run(main(args))
