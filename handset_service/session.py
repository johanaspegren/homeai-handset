"""One telephone call.

Owns the state machine between "handset lifted" and "handset replaced":

    off_hook -> greet -> [ listen -> transcribe -> ask Kiri -> speak ] * n -> on_hook

Everything slow (Whisper, Piper) runs in a thread; the reply pipeline runs as a
cancellable task so replacing the handset stops it mid-sentence rather than
politely finishing. The transport is injected, so this class is testable without
a socket, and the STT/LLM/TTS callables are injected so tests don't need models.
"""

import asyncio
import logging

from . import config, handsfree, llm, protocol, stt, tts
from .logging_config import CallLog
from .vad import Segmenter, Vad
from .voice_control import set_voice_muted

# PCM slice size for outbound audio frames (~93 ms at 22050 Hz mono) — small
# enough that the earpiece starts within a frame of synthesis finishing.
AUDIO_FRAME_BYTES = 4096


class Call:
    def __init__(
        self,
        send_json,
        send_audio,
        *,
        terminal_id: str = "zodiac",
        transcribe=None,
        chat=None,
        synthesize=None,
        sample_rate=None,
        segmenter=None,
        greeting: str = None,
        mute_voice=None,
    ) -> None:
        self._send_json = send_json
        self._send_audio = send_audio
        self.terminal_id = terminal_id
        self.log = CallLog(terminal_id)

        self._transcribe = transcribe or stt.transcribe_pcm
        self._chat = chat or llm.stream_reply
        self._synthesize = synthesize or tts.synthesize
        self._sample_rate = sample_rate or tts.sample_rate
        self._greeting = config.GREETING if greeting is None else greeting
        self._mute_voice = mute_voice or set_voice_muted

        self._segmenter = segmenter or Segmenter(Vad(), on_speech_start=self._speech_started)
        self._turn: asyncio.Task | None = None
        self._accepting = False   # mic audio is consumed only between turns
        self._muted = False
        self.active = False

    # --- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        """Handset lifted: answer immediately."""
        self.active = True
        self.log.event("CALL", "started")
        # Hold the workshop's open mic first: it hears the earpiece too, and
        # would answer the same question a second time. homeai-voice picks the
        # command up on its next heartbeat (up to ~2s), so it's sent before the
        # greeting rather than alongside it.
        if await self._mute_voice(True):
            self.log.event("VOICE", "workshop mic held for the call")
        rate = self._sample_rate()
        await self._send_json(protocol.message(
            protocol.CALL_STARTED,
            protocol_version=protocol.PROTOCOL_VERSION,
            audio=protocol.audio_format(rate),
            capture=protocol.audio_format(config.CAPTURE_RATE),
        ))
        await self._led(protocol.LED_CONNECTED)
        if self._greeting:
            # Fixed text, not a model call — picking up a phone must never wait
            # on an LLM. The conversation proper starts on the first thing said.
            self._turn = asyncio.create_task(self._greet())
        else:
            await self._listen()

    async def _greet(self) -> None:
        try:
            await self._speak_text(self._greeting)
        except Exception:
            logging.exception("greeting failed")
        finally:
            await self._listen()

    async def hang_up(self) -> None:
        """Handset replaced: drop everything in flight, now."""
        if not self.active:
            return
        self.active = False
        self._accepting = False
        await self._cancel_turn()
        self._segmenter.reset()
        if await self._mute_voice(False):
            self.log.event("VOICE", "workshop mic released")
        self.log.event("CALL", "ended")

    async def _cancel_turn(self) -> None:
        turn, self._turn = self._turn, None
        if turn and not turn.done():
            turn.cancel()
            try:
                await turn
            except (asyncio.CancelledError, Exception):
                pass

    # --- incoming ----------------------------------------------------------

    async def on_audio(self, data: bytes) -> None:
        """Microphone PCM from the handset."""
        if not (self.active and self._accepting and not self._muted):
            return  # our own turn, or muted: half-duplex for now
        for pcm in self._segmenter.push(data):
            await self._start_turn(pcm)

    async def on_control(self, message: dict) -> None:
        kind = message.get("type")
        if kind == protocol.ON_HOOK:
            await self.hang_up()
        elif kind == protocol.END_OF_SPEECH:
            pcm = self._segmenter.flush()
            if pcm:
                await self._start_turn(pcm)
        elif kind == protocol.MUTE:
            self._muted = bool(message.get("active"))
            self._segmenter.reset()
            self.log.event("MUTE", "on" if self._muted else "off")
        elif kind == protocol.KEY:
            key = str(message.get("key"))
            self.log.event("KEY", key)
            if config.HANDSFREE_KEY and key == config.HANDSFREE_KEY:
                await self._toggle_hands_free()
        elif kind == protocol.PING:
            await self._send_json(protocol.message(protocol.PONG))

    async def _toggle_hands_free(self) -> None:
        """The phone's hands-free button: bring the workshop mic and speakers
        up (or put them away). Slow — homeai-voice loads Whisper — so say so
        rather than leaving a silent handset."""
        going_on = not handsfree.voice_running()
        self._accepting = False
        try:
            await self._speak_text(
                "Switching to hands free, one moment." if going_on
                else "Back to the handset.")
            on = await handsfree.toggle()
            self.log.event("HANDS", f"hands free {'on' if on else 'off'}")
            if going_on and not on:
                await self._speak_text("Sorry, the workshop microphone didn't come up.")
        except Exception:
            logging.exception("hands-free toggle failed")
        finally:
            if self.active:
                await self._listen()

    def _speech_started(self) -> None:
        self.log.mark("speech_start")
        self.log.event("AUDIO", "speech started")

    # --- the turn ----------------------------------------------------------

    async def _start_turn(self, pcm: bytes) -> None:
        self.log.mark("speech_end")
        self.log.event(
            "AUDIO",
            f"speech ended | {len(pcm) / 2 / config.CAPTURE_RATE:.2f}s",
        )
        self._accepting = False
        await self._send_json(protocol.message(protocol.MIC, active=False))
        self._turn = asyncio.create_task(self._run_turn(pcm))

    async def _run_turn(self, pcm: bytes) -> None:
        try:
            await self._led(protocol.LED_THINKING)
            text = await asyncio.to_thread(self._transcribe, pcm)
            self.log.timed("STT", f'transcript | "{text or "[nothing]"}"')
            if not text:
                return
            await self._send_json(protocol.message(protocol.TRANSCRIPT, text=text))
            await self._speak_reply(text)
        except asyncio.CancelledError:
            self.log.event("TURN", "cancelled (handset replaced)")
            raise
        except Exception as exc:
            logging.exception("turn failed")
            await self._error(str(exc))
        finally:
            if self.active:
                await self._listen()

    async def _speak_reply(self, text: str) -> None:
        """Stream Kiri's answer to the earpiece, a phrase at a time."""
        self.log.event("LLM", f"request | {config.HOMEAI_CHAT_URL}")
        first_token = True
        spoken = []

        async def tokens():
            nonlocal first_token
            async for token in self._chat(text):
                if first_token:
                    first_token = False
                    self.log.timed("LLM", "first token")
                yield token

        async for phrase in tts.speakable_chunks(tokens()):
            self.log.timed("TTS", f'phrase | "{phrase}"')
            spoken.append(phrase)
            await self._play(phrase, first=len(spoken) == 1)
        if spoken:
            await self._finish_speaking()
        else:
            self.log.event("LLM", "empty reply", level=logging.WARNING)

    async def _speak_text(self, text: str) -> None:
        """Say a fixed line (the greeting) — no model involved."""
        await self._play(text, first=True)
        await self._finish_speaking()

    async def _play(self, text: str, *, first: bool) -> None:
        audio = await asyncio.to_thread(self._synthesize, text)
        if not audio:
            return
        if first:
            await self._send_json(protocol.message(protocol.ASSISTANT_SPEAKING))
            await self._led(protocol.LED_SPEAKING)
        for i in range(0, len(audio), AUDIO_FRAME_BYTES):
            await self._send_audio(audio[i : i + AUDIO_FRAME_BYTES])
        if first:
            self.log.timed("AUDIO", "first bytes sent")

    async def _finish_speaking(self) -> None:
        await self._send_json(protocol.message(protocol.ASSISTANT_FINISHED))

    # --- transitions -------------------------------------------------------

    async def _listen(self) -> None:
        """Hand the turn back to the caller."""
        # Forget anything the VAD half-heard while we were talking, so the
        # earpiece bleeding into the mouthpiece can't become the next question.
        self._segmenter.reset()
        self._accepting = True
        await self._led(protocol.LED_LISTENING)
        await self._send_json(protocol.message(protocol.MIC, active=True))
        await self._send_json(protocol.message(protocol.LISTENING))

    async def _led(self, state: str) -> None:
        await self._send_json(protocol.message(protocol.LED, state=state))

    async def _error(self, detail: str) -> None:
        self.log.event("ERROR", detail, level=logging.ERROR)
        await self._led(protocol.LED_ERROR)
        await self._send_json(protocol.message(protocol.ERROR, message=detail))
