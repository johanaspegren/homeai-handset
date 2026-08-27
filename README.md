# HomeAI Handset — the Zodiac telephone

A 1980s **Zodiac Sigma 300** answers when you lift it, and Kiri is on the line.

The original motherboard is gone; the original microphone, earpiece, mute
switch, volume control, hook switch and keypad stay. A Raspberry Pi in the base
is a **thin client** — it owns hardware and nothing else. Everything that
thinks runs on `homeai`.

```
  Zodiac handset          Raspberry Pi                    homeai box
 ┌──────────────┐        ┌──────────────┐        ┌──────────────────────────┐
 │ microphone ──┼── USB ─┤              │        │ handset service  :8400   │
 │              │ audio  │ zodiac-client├─ WS ───┤   silero VAD  → turn      │
 │ earpiece  ◄──┼────────┤              │  PCM   │   whisper GPU → text      │
 │ hook switch ─┼─ GPIO ─┤              │  JSON  │   homeai-server /chat ────┼──► Kiri
 │ keypad ──────┼─ GPIO ─┤              │        │   piper       → audio     │
 └──────────────┘        └──────────────┘        └──────────────────────────┘
```

Pick up. Talk. Listen. Hang up. No wake word, no screen, no keyboard — and
while the handset is down, the microphone does not exist.

## Kiri, not a second brain

The handset is a **channel adapter**, exactly like `homeai-discord-bot` and
`homeai-voice`: it turns speech into text, posts it to `homeai-server`'s
`/chat`, and speaks the reply. It never talks to Ollama itself.

That means the telephone gets the whole of Kiri for free and stays in step with
every other channel — her persona, the Obsidian vault's facts and notes, the
`<<note:>>` / `<<remind:>>` / `<<run:>>` markers (so "what's the mower doing?"
really does check the mower down the phone), the journal, reminders.

Requests arrive with `source="handset"`, which earns them a spoken, telephone-
brief system prompt on the server side.

One deliberate deviation from the original spec: conversation state is **not**
wiped when you hang up. The server keeps a short rolling history per user that
voice and Discord share, so a call can pick up where the workshop left off. The
*call* ends cleanly; the memory of it is Kiri's, not the phone's.

## What works today

Milestones 1, 3, 4 and 6 of the spec, with 5 stood in for:

- **Call lifecycle** — off-hook opens a session, the greeting is spoken
  immediately (a fixed line, never a model call), hanging up cancels whatever
  is in flight mid-sentence.
- **Turn taking** — silero-VAD on the server decides when you've stopped
  talking. No push-to-talk, no wake word.
- **Speech** — faster-whisper `small.en` on the GPU (~0.2 s), Piper for the
  reply, sentence-pipelined so the earpiece starts on sentence one while Kiri
  is still writing sentence two.
- **Measured latency** — end of speech → first audio in the earpiece:

  | stage | time |
  |---|---|
  | speech ends → transcript | 0.08–0.22 s |
  | transcript → first token from Kiri | 0.60–0.84 s |
  | first token → first PCM on the wire | 0.08–0.12 s |
  | **total** | **0.84–1.17 s** |

  Comfortably inside the spec's 2 s target, and at the preferred ~1 s. Getting
  there needed one change on the server: gemma4:12b runs a silent reasoning
  pass before its first visible token, worth ~2 s. The handset turns it off
  (`HOMEAI_NO_THINK_SOURCES`); tool markers still fire correctly without it.
- **Loopback mode** — `/zodiac/echo` proves audio transport with no AI in the
  path at all. Verified on the real Zodiac: speaking into the original
  mouthpiece comes back out of the original earpiece, so the microphone, the
  USB card, the WebSocket and the playback path are all known good
  independently of Whisper, Kiri and Piper.
- **The hook switch** — wired to BCM 17 and running (spec Milestone 5). The
  client's hook is an interface with three implementations (`gpio`, `stdin`,
  `always`), so wiring it was a config change and nothing more. The Zodiac
  closes its switch when the handset is *lifted*, the opposite of the classic
  cradle plunger, hence `hook.invert: true`. A systemd unit for running the
  client at boot ships in `zodiac-client/systemd/`.

Not yet: keypad (unmapped — see `tools/keypad_mapper.py`), LEDs (logged, not
lit), the ringer, barge-in, and the earpiece amplifier.

## Prerequisites

- The `homeai-server` stack running (`~/dev/homeai.sh start`) — this service
  needs `/chat`.
- `homeai-voice` checked out beside this repo: its Piper voice files and its
  sentence-splitting are shared rather than duplicated.
- An NVIDIA GPU for Whisper (falls back to CPU with `WHISPER_DEVICE=cpu`).

## Setup

```bash
cd ~/dev/homeai-handset
uv venv
VIRTUAL_ENV=.venv uv pip install -r requirements.txt
cp .env.example .env      # defaults are fine on the homeai box
```

## Usage

Start the service:

```bash
.venv/bin/python -m handset_service.websocket
# 21:10:18 | BOOT  | - | handset service ready | chat=http://localhost:8000/chat | voice=en_US-lessac-medium @ 22050 Hz
```

Call it without a telephone — this synthesises the caller's voice, streams it
in real time and writes the reply to a WAV:

```bash
.venv/bin/python tools/fake_call.py --say "what's the mower up to?"
# ☎  call started | playback 22050 Hz
# 👤 heard: What's the mower up to?
# ⏱  first audio back: 0.9s after end of speech
# 🔊 reply audio written to /tmp/handset-reply.wav
```

The service log is the latency trail the spec asks for:

```text
21:16:35 | AUDIO | zodiac-01 | speech ended | 2.08s
21:16:35 | STT   | zodiac-01 | transcript | "Hello? Can you hear me?" | +0.219s
21:16:36 | LLM   | zodiac-01 | first token | +1.057s
21:16:36 | TTS   | zodiac-01 | phrase | "I can hear you perfectly." | +1.137s
21:16:36 | AUDIO | zodiac-01 | first bytes sent | +1.172s
```

Every `+` is measured from the moment you stopped speaking.

The Pi side lives in [`zodiac-client/`](zodiac-client/README.md) and is
installed on the Pi, not here.

## Configuration (`.env`)

| Variable | Default | What it does |
|---|---|---|
| `HANDSET_PORT` | `8400` | WebSocket port (`/zodiac`, `/zodiac/echo`) |
| `HOMEAI_CHAT_URL` | `http://localhost:8000/chat` | the brain |
| `HANDSET_USER` | `johan` | whose conversation history this is |
| `HANDSET_GREETING` | `HomeAI. Hello Johan.` | spoken the instant you pick up |
| `HANDSET_MUTES_VOICE` | `1` | hold the workshop's open mic for the call, when it's running at all |
| `HANDSET_HANDSFREE_KEY` | `*` | keypad button that toggles the workshop mic (keypad not mapped yet) |
| `HOMEAI_SH` | `../homeai.sh` | the stack script hands-free uses to start/stop voice |
| `WHISPER_MODEL` / `WHISPER_DEVICE` | `small.en` / `cuda` | speech to text |
| `PIPER_VOICE` | `en_US-lessac-medium` | Kiri's voice (shared with homeai-voice) |
| `HOMEAI_VOICE_DIR` | `../homeai-voice` | where the voice files and helpers live |
| `VAD_HANGOVER_MS` | `700` | silence that ends your turn — the main latency/patience dial |
| `VAD_PREROLL_MS` | `300` | audio kept from before you started speaking |
| `VAD_MIN_SPEECH_MS` | `250` | shorter than this is a door bang, not a turn |

## The telephone is the default way in

`homeai-voice` no longer starts with the stack. The phone is how you talk to
Kiri; the workshop's open mic is the thing you switch on when you want it.

That isn't only about her answering twice (though she did — the room mic hears
the earpiece perfectly well and cheerfully answers the same question again).
**Muting doesn't save any work.** A muted homeai-voice still runs VAD on every
80 ms frame and still puts every utterance in the room through Whisper; mute
only stops her *replying*. Not running it is what gives the GPU back — about a
gigabyte of VRAM, plus the transcription of conversations nobody is having.

So the default is: handset only, room silent, nothing listening to an empty
workshop.

### Hands free

Switching it on brings up the workshop mic and speakers so you can talk to the
room instead of holding the handset:

```bash
curl -X POST localhost:8400/handsfree -H 'content-type: application/json' -d '{"on": true}'
curl localhost:8400/handsfree      # {"on": true}
```

This is what the phone's hands-free button will call. The Zodiac's keypad isn't
mapped yet, so nothing is wired to it — but the handler is: any `key` message
matching `HANDSET_HANDSFREE_KEY` (default `*`) toggles it, and Kiri says
"Switching to hands free, one moment" while homeai-voice loads Whisper. It
takes several seconds, so it's a mode you switch, not a key you hold.

Or from the shell, which is all the endpoint does anyway:

```bash
~/dev/homeai.sh start voice     # room listening
~/dev/homeai.sh stop voice      # back to the handset alone
```

While hands free is on, picking up the handset still holds the room mic for the
length of the call (`HANDSET_MUTES_VOICE=1`, the same button the dashboard has)
and releases it on hang-up — so the two never answer at once. When hands free is
off, no mute is sent at all: it would sit queued on the server and homeai-voice
would come up already muted.

**What you give up with the room mic off:** spoken reminders and the arrival
greeting have no mouth to come out of — they queue on the server until voice is
running again — and there's no spoken boot report to tell you the stack came up
clean. Lifting the handset and hearing her answer does the same job.

To go back to always-on at boot, set in `~/dev/homeai.env`:

```bash
HOMEAI_START_SERVICES="presence mower-ui server discord-bot handset voice"
```

## Protocol

One WebSocket per call, carrying JSON control frames and raw binary PCM.

| client → server | server → client |
|---|---|
| `off_hook` `{terminal_id}` | `call_started` `{audio:{rate,channels,encoding}}` |
| binary PCM (16 kHz mono s16le) | `listening` |
| `end_of_speech` (optional) | `transcript` `{text}` |
| `key` `{key}` | `assistant_speaking` / binary PCM / `assistant_finished` |
| `mute` `{active}` | `mic` `{active}` — half-duplex gate |
| `on_hook` | `led` `{state}`, `error` `{message}` |

Audio frames carry no header: the format is announced once in `call_started`,
so the earpiece can never play back at the wrong speed.
`handset_service/protocol.py` and `zodiac_client/protocol.py` are copies of one
file and a test fails if they drift.

## Tests

```bash
.venv/bin/python -m unittest discover -s tests -t .
# Ran 55 tests ... OK
```

No models and no sockets: the transport, STT, LLM and TTS are all injected, so
the call state machine is tested in milliseconds.

## Troubleshooting

**"Could not import homeai-voice's speak.py"** — set `HOMEAI_VOICE_DIR` to your
`homeai-voice` checkout.

**Whisper is slow / on CPU** — the CUDA wheels must be in *this* venv;
`_cudalibs.py` dlopens them from `.venv/lib/python*/site-packages/nvidia`.

**A throwaway Whisper test hangs** — import `handset_service._netfix` first, or
set `HF_HUB_OFFLINE=1`. The IPv6 route to huggingface.co is broken on this
network and the hang is the revision check, not CUDA.

**She answers slowly** — read the `+` numbers in the log. If `first token` is
the big one, it's homeai-server, not the phone.

**She interrupts you mid-sentence** — raise `VAD_HANGOVER_MS`. If she waits too
long after you stop, lower it.

## Roadmap

1. Map the keypad with `tools/keypad_mapper.py`, then decide what keys do.
2. An amplifier for the ~140 Ω earpiece — it's intelligible but quiet.
3. Barge-in: interrupt Kiri by talking over her. The AEC groundwork exists in
   `~/dev/aec-test.sh`, but it's a PipeWire drop-in on the workshop's analogue
   card — the Pi's USB card needs its own answer.
4. The ringer: Kiri calling *you* — reminders, doorbell, print finished.
5. Pi Zero W migration (spec Milestone 9). The client is already stdlib +
   `websockets` + `PyYAML` for exactly this reason — plus `gpiozero`/`lgpio`
   for the hook now, both of which piwheels has prebuilt for ARMv6.

## Project structure

```text
handset_service/
├── websocket.py       FastAPI app, /zodiac — one socket per call
├── session.py         the call state machine (greet → listen → answer → hang up)
├── vad.py             push-based silero segmenter: mic stream → utterances
├── stt.py             faster-whisper on the GPU
├── llm.py             homeai-server /chat, streamed
├── tts.py             Piper → raw PCM, async sentence pipelining
├── voice_reuse.py     adapter onto homeai-voice's proven helpers
├── echo.py            /zodiac/echo — transport-only loopback
├── protocol.py        the wire vocabulary (copied to the client)
├── config.py          environment configuration
└── logging_config.py  the latency trail
tools/
├── fake_call.py       call the service without a telephone
└── keypad_mapper.py   work out the Zodiac's keypad wiring (run on the Pi)
zodiac-client/         the Raspberry Pi client — deployed to the Pi
tests/                 stdlib unittest, everything slow injected
```
