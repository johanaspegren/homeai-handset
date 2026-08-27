# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A 1980s Zodiac Sigma 300 telephone as a voice terminal for HomeAI. Lift the
handset, Kiri answers; hang up, everything in flight is cancelled. `README.md`
is the user-facing document and is kept current — read it for behaviour,
latency numbers and roadmap.

## Two deployables, one repo

| | runs on | installed from |
|---|---|---|
| `handset_service/` | the `homeai` box (GPU) | root `requirements.txt` |
| `zodiac-client/` | the Raspberry Pi in the phone's base | `zodiac-client/requirements.txt` |

These dependency sets must never be mixed. The root one pulls faster-whisper,
onnxruntime and two NVIDIA CUDA wheels — gigabytes that do nothing on a Pi and
that the client never imports. The client is deliberately small — `websockets`,
`PyYAML`, and `gpiozero`/`lgpio` for the hook switch — with audio shelled out to
`arecord`/`aplay`, so it can move to an ARMv6 Pi Zero W unchanged. Keep it that
way: no numpy, no ONNX, no compiler, nothing needing a desktop audio stack.
Anything added here must have a prebuilt ARMv6 wheel on piwheels.

## Commands

```bash
# setup (homeai box)
uv venv && VIRTUAL_ENV=.venv uv pip install -r requirements.txt
cp .env.example .env

# run the service
.venv/bin/python -m handset_service.websocket

# tests — stdlib unittest, no models, no sockets (~10s)
.venv/bin/python -m unittest discover -s tests -t .
.venv/bin/python -m unittest tests.test_session -v            # one module
.venv/bin/python -m unittest tests.test_session.CallTests.test_greeting_needs_no_model

# exercise a whole call without a telephone: synthesises the caller's voice,
# streams it, writes the reply to /tmp/handset-reply.wav
.venv/bin/python tools/fake_call.py --say "what's the mower up to?"
```

The Pi client is run on the Pi (`zodiac-client/README.md`); `install.sh` there
exists mainly to stop the wrong `requirements.txt` being installed.

## Architecture

**The handset is a channel adapter, not a second brain.** Like
`homeai-discord-bot` and `homeai-voice`, it does STT/TTS and POSTs text to
`homeai-server`'s `/chat` with `source="handset"`. It must never talk to Ollama
directly — persona, the Obsidian vault, `<<note:>>`/`<<remind:>>`/`<<run:>>`
markers, journal and history all live behind `/chat`, and the phone gets them
for free by staying a thin adapter.

**All intelligence is server-side.** The Pi owns hardware and transport only —
it doesn't know what VAD or a language model is. Turn taking (silero-VAD) runs
in `handset_service/vad.py` precisely so the client stays dumb.

One call = one WebSocket carrying JSON control frames and raw binary PCM.
`session.py` is the state machine (greet → listen → transcribe → ask Kiri →
speak, repeat), with the reply pipeline as a cancellable `asyncio.Task` so
hanging up stops Kiri mid-sentence. `websocket.py` owns the socket and
serialises sends behind a lock (the read loop and the reply task both write).

Audio is half-duplex: `Call._accepting` gates inbound mic PCM, the client is
told via `mic {active}`, and `Segmenter.reset()` runs after every reply so the
earpiece bleeding into the mouthpiece can't become the next question.

Audio frames carry no header — the format is announced once in `call_started`,
so playback can never run at the wrong rate.

### `protocol.py` is duplicated on purpose

`handset_service/protocol.py` and `zodiac-client/zodiac_client/protocol.py` are
byte-identical copies so the Pi needs nothing from the service package.
`tests/test_protocol.py` fails if they drift — after editing one, copy it over
the other.

### Sibling repositories

This repo is one of several under `~/dev/` and reaches into them by path:

- `../homeai-voice` — `voice_reuse.py` puts it on `sys.path` and imports the
  *pure functions* `_first_break` and `strip_markdown` from `speak.py` (the
  sentence-boundary rule, reused rather than re-derived). Piper voice files are
  shared from `../homeai-voice/voices`. Playback is not reused: voice plays to
  the workshop speakers, the handset sends bytes down a socket.
- `../homeai-halo` — `ring.py` imports the `Halo` driver class from there.
- `../homeai.sh` and `../logs/voice.pid` — `handsfree.py` starts and stops
  `homeai-voice` through the stack script and reads its pidfile.

All three are optional at runtime except homeai-voice's `speak.py`; a missing
ring or `homeai.sh` logs once and no-ops.

### Ownership handovers with homeai-voice

`homeai-voice` no longer starts with the stack — the phone is the default way
in. Two resources are handed back and forth and both matter:

- **The LED ring** (one serial port, `/dev/ttyUSB0`). The handset holds it,
  releases it before starting voice, reclaims it when voice stops or fails to
  start, and always releases it at shutdown — the Arduino holds its last frame
  forever, so a frozen animation looks like a fault.
- **The workshop mic.** When voice *is* running, a call holds it via
  `/voice/mute` so Kiri doesn't answer the same question twice. When it isn't
  running, no mute is sent at all — it would sit queued on the server and voice
  would come up already muted.

### Import-order constraints (`stt.py`)

`_netfix` (IPv4-only DNS; the IPv6 route to huggingface.co hangs on this
network) and `_cudalibs` (dlopens the pip-installed CUDA `.so`s with
`RTLD_GLOBAL`, since `LD_LIBRARY_PATH` can't be set after start) must both be
imported before `faster_whisper`. Don't reorder those imports.

## Conventions

Slow things are injected, so `tests/` runs the call state machine in
milliseconds with no models and no sockets. New code on the call path should
keep that seam: pass callables in, default them to the real implementation.

Logging is the latency trail the spec asks for —
`HH:MM:SS.mmm | STAGE | terminal | detail`, with `CallLog.timed()` printing
`+Ns` since the end of speech. Use `log.event`/`log.timed` with a stage tag
rather than bare `logging.info`.

Nothing hardware-specific is baked into service code: models and URLs come from
`.env` (`config.py`), audio devices from the Pi's `config.yaml`.

Comments and commit messages here explain *why*, in prose, at some length —
particularly the non-obvious constraints (why muting isn't enough, why the ring
must be released, why a pipe would hang `homeai.sh`). Match that register.

`zodiac_implementation_spec.md` is the original spec and is historical: the
implementation deviates from it deliberately in places (e.g. conversation state
is not wiped on hang-up). README's "What works today" is the accurate status.
