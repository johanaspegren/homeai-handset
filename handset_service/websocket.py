"""The handset service: one WebSocket per telephone call.

    ws://homeai.local:8400/zodiac

Runs on the `homeai` box next to the rest of the stack. The Raspberry Pi in the
Zodiac connects, streams microphone PCM up and plays PCM back down; all the
thinking happens here.
"""

import asyncio
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from . import config, handsfree, protocol, stt, tts
from .echo import router as echo_router
from .logging_config import setup as setup_logging
from .session import Call

setup_logging(config.LOG_LEVEL)
log = logging.getLogger("handset")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Load both models up front: lifting the handset should never wait for a
    # multi-second model load.
    await asyncio.to_thread(stt.load)
    await asyncio.to_thread(tts.load)
    log.info(
        "handset service ready | chat=%s | voice=%s @ %d Hz",
        config.HOMEAI_CHAT_URL, config.PIPER_VOICE, tts.sample_rate(),
        extra={"stage": "BOOT", "terminal": "-"},
    )
    yield


app = FastAPI(title="HomeAI Handset Service", lifespan=lifespan)
app.include_router(echo_router)  # /zodiac/echo — transport-only loopback


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "chat_url": config.HOMEAI_CHAT_URL,
        "whisper": config.WHISPER_MODEL,
        "voice": config.PIPER_VOICE,
        "hands_free": handsfree.voice_running(),
    }


class HandsFree(BaseModel):
    on: bool


@app.get("/handsfree")
async def hands_free_state() -> dict:
    """Is the workshop mic and speaker pair running?"""
    return {"on": handsfree.voice_running()}


@app.post("/handsfree")
async def hands_free_set(cmd: HandsFree) -> dict:
    """Switch the workshop mic and speakers on or off.

    This is what the phone's hands-free button will call once the Zodiac's
    keypad is mapped; until then it's reachable by hand:

        curl -X POST localhost:8400/handsfree -d '{"on": true}' -H 'content-type: application/json'
    """
    return {"on": await handsfree.set_hands_free(cmd.on)}


class _Transport:
    """Serialises sends — the read loop and the reply task both write here."""

    def __init__(self, websocket: WebSocket) -> None:
        self._ws = websocket
        self._lock = asyncio.Lock()

    async def send_json(self, message: dict) -> None:
        async with self._lock:
            await self._ws.send_text(json.dumps(message))

    async def send_audio(self, data: bytes) -> None:
        async with self._lock:
            await self._ws.send_bytes(data)


@app.websocket("/zodiac")
async def zodiac(websocket: WebSocket) -> None:
    await websocket.accept()
    transport = _Transport(websocket)
    call: Call | None = None
    try:
        while True:
            event = await websocket.receive()
            if event["type"] == "websocket.disconnect":
                break

            data = event.get("bytes")
            if data is not None:
                if call is not None:
                    await call.on_audio(data)
                continue

            try:
                message = json.loads(event.get("text") or "{}")
            except json.JSONDecodeError:
                log.warning("bad control frame", extra={"stage": "NET", "terminal": "-"})
                continue

            kind = message.get("type")
            if kind == protocol.OFF_HOOK:
                if call is not None:
                    await call.hang_up()
                call = Call(
                    transport.send_json,
                    transport.send_audio,
                    terminal_id=message.get("terminal_id", "zodiac"),
                )
                await call.start()
            elif call is not None:
                await call.on_control(message)
                if kind == protocol.ON_HOOK:
                    call = None
            else:
                log.warning("%s before off_hook — ignored", kind,
                            extra={"stage": "NET", "terminal": "-"})
    except WebSocketDisconnect:
        pass
    except Exception:
        logging.exception("websocket error")
    finally:
        # A dropped connection is a dropped call: never leave a turn running.
        if call is not None:
            await call.hang_up()


def main() -> None:
    import uvicorn

    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level=config.LOG_LEVEL.lower())


if __name__ == "__main__":
    main()
