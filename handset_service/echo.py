"""Milestone 3: a loopback call, with no intelligence anywhere in it.

    ws://homeai.local:8400/zodiac/echo

Whatever the mouthpiece hears comes back out of the earpiece a moment later.
That proves capture, transport, playback, sample rates and the hook lifecycle
independently of Whisper, Ollama and Piper — so when the real thing sounds
wrong you can tell in ten seconds which half is at fault.
"""

import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from . import config, protocol

router = APIRouter()
log = logging.getLogger("handset")


@router.websocket("/zodiac/echo")
async def zodiac_echo(websocket: WebSocket) -> None:
    await websocket.accept()
    terminal = "echo"
    try:
        while True:
            event = await websocket.receive()
            if event["type"] == "websocket.disconnect":
                break

            data = event.get("bytes")
            if data is not None:
                await websocket.send_bytes(data)
                continue

            message = json.loads(event.get("text") or "{}")
            kind = message.get("type")
            if kind == protocol.OFF_HOOK:
                terminal = message.get("terminal_id", "echo")
                log.info("echo call started", extra={"stage": "CALL", "terminal": terminal})
                # Echo plays back what the mic captured, so both directions run
                # at the capture rate here — not Piper's.
                await websocket.send_text(json.dumps(protocol.message(
                    protocol.CALL_STARTED,
                    protocol_version=protocol.PROTOCOL_VERSION,
                    audio=protocol.audio_format(config.CAPTURE_RATE),
                    capture=protocol.audio_format(config.CAPTURE_RATE),
                )))
                await websocket.send_text(json.dumps(protocol.message(
                    protocol.MIC, active=True)))
                await websocket.send_text(json.dumps(protocol.message(
                    protocol.LED, state=protocol.LED_LISTENING)))
            elif kind == protocol.ON_HOOK:
                log.info("echo call ended", extra={"stage": "CALL", "terminal": terminal})
                break
    except WebSocketDisconnect:
        pass
