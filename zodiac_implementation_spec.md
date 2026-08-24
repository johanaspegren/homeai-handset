# Zodiac HomeAI Terminal — Codex Implementation Spec

## Goal

Build a local networked voice terminal using a vintage **Zodiac Sigma 300 analogue telephone**.

The Zodiac should retain its original physical interaction:

- original handset microphone
- original handset earpiece
- physical mute button
- physical volume control
- hook switch
- original keypad
- LEDs / ringer where practical

The original Zodiac motherboard will **not** be used.

A Raspberry Pi will act as the thin hardware client.

The `homeai` server will handle:

- speech-to-text
- LLM inference
- conversation state
- tools
- text-to-speech
- audio returned to the Zodiac

Initial development should run on a Raspberry Pi 4/5, but the client architecture must remain light enough to migrate later to an original Raspberry Pi Zero W.

---

## Current proven hardware

### Handset earpiece

The handset earpiece uses:

```text
RED / BLACK
```

Measured DC resistance:

```text
~140 Ω
```

It has been successfully driven from a cheap C-Media / Plexgear USB sound card.

Volume is currently somewhat low, but speech is clearly intelligible.

A small amplifier may be added later.

### Handset microphone

The microphone output from the handset uses:

```text
GREEN / WHITE
```

Inside the handset, these wires pass through the physical mute switch and then become red/black at the microphone capsule.

The original microphone has been successfully captured through the USB sound card.

No additional microphone bias/preamp circuitry has been required.

### USB audio interface

Linux identifies the device as approximately:

```text
USB PnP Sound Device
C-Media Electronics Inc.
```

Example PipeWire source:

```text
alsa_input.usb-C-Media_Electronics_Inc._USB_PnP_Sound_Device-00.analog-mono
```

Successful capture command:

```bash
arecord -D pipewire -f S16_LE -r 48000 -c 1
```

---

# Architecture

```text
                     ZODIAC TERMINAL
 ┌────────────────────────────────────────────────┐
 │                                                │
 │ Original mic ───────► USB audio               │
 │ Original earpiece ◄── USB audio               │
 │                                                │
 │ Hook switch ─────────► GPIO                    │
 │ Keypad ──────────────► GPIO / I/O expander    │
 │ LEDs / ringer ◄─────── GPIO                    │
 │                                                │
 │                Raspberry Pi                    │
 │                     │                          │
 └─────────────────────┼──────────────────────────┘
                       │
                  (Wi-Fi) / LAN
                       │
                       ▼
                  HOMEAI SERVER
       ┌────────────────────────────────┐
       │ STT                            │
       │ conversation state             │
       │ Ollama / Gemma                 │
       │ HomeAI tools                   │
       │ TTS                            │
       │ audio streaming                │
       └────────────────────────────────┘
```

The Raspberry Pi must remain a **thin client**.

Do not run local LLM inference or heavyweight STT/TTS on the Pi unless explicitly enabled as a later fallback.

---

# User experience

The desired interaction is:

```text
handset down
    ↓
idle

handset lifted
    ↓
call starts
    ↓
HomeAI answers immediately
    ↓
"HomeAI. Hello Johan."
    ↓
user speaks
    ↓
HomeAI responds
    ↓
conversation continues

handset replaced
    ↓
call ends
    ↓
conversation state cleared
```

There should be:

- no wake word
- no always-listening microphone while the handset is down
- no keyboard interaction during normal use
- no browser UI required

The physical hook switch is the primary privacy and session control.

---

# Behavioural principles

HomeAI responses are spoken, so they must be optimized for voice.

Use a system prompt similar to:

```text
You are HomeAI speaking through an old telephone handset.

This is a spoken telephone conversation.

Be brief.
Usually answer in one or two short sentences.
Speak naturally and conversationally.
Never use Markdown, headings, bullet points or formatting.
Prefer short sentences.
Do not describe yourself as an AI unless asked.
```

The model currently preferred for prototyping is:

```text
gemma4:12b
```

via Ollama.

Disable thinking/reasoning output where supported.

Prefer low latency over elaborate responses.

---

# Existing proof-of-concept behaviour

A local Python prototype has already demonstrated:

```text
Zodiac microphone
    ↓
arecord / PipeWire
    ↓
Faster Whisper
    ↓
Ollama
    ↓
Gemma 4 12B
    ↓
streaming response
    ↓
sentence buffering
    ↓
TTS
    ↓
Zodiac earpiece
```

LLM output is streamed.

As soon as a complete sentence is available, it is sent to TTS while Gemma continues generating the rest of the response.

This behaviour should be preserved.

---

# Main implementation objective

Split the existing poc (zodiac_poc.py) into two components:

```text
zodiac-client
```

running on the Raspberry Pi

and

```text
zodiac-service or handset-service
```

running on `homeai`.

---

# Component 1: zodiac-client

The Raspberry Pi client owns only physical hardware and audio transport.

Suggested structure:

```text
zodiac-client/
├── zodiac_client/
│   ├── app.py
│   ├── audio.py
│   ├── gpio.py
│   ├── protocol.py
│   ├── config.py
│   └── logging_config.py
├── config.yaml
├── requirements.txt
├── systemd/
│   └── zodiac-client.service
└── README.md
```

## Responsibilities

The client should:

1. Detect hook state.
2. Open a session to HomeAI when handset goes off-hook.
3. Capture microphone audio.
4. Stream microphone audio to HomeAI.
5. Receive audio from HomeAI.
6. Play received audio immediately.
7. Send keypad/button events.
8. Receive LED/ringer commands.
9. End the session immediately when handset goes on-hook.
10. Recover automatically from network failures.

The client should **not** know how STT, LLM, tools, or TTS work.

---

# Audio capture

Prefer ALSA directly on the Pi if possible.

Avoid requiring PipeWire on the final Pi client.

Target format:

```text
PCM
16-bit signed little endian
mono
16 kHz
```

Example:

```bash
arecord \
  -D plughw:CARD=Device,DEV=0 \
  -f S16_LE \
  -r 16000 \
  -c 1 \
  -t raw
```

Python should read audio continuously from stdout.

Use small chunks, e.g. around:

```text
20–100 ms
```

per frame.

Do not save temporary WAV files during normal operation.

---

# Audio playback

HomeAI should return PCM or another simple streamable audio format.

Prefer a fixed format initially, e.g.:

```text
PCM
16-bit signed little endian
mono
22050 Hz
```

or use 16 kHz for both directions if the existing TTS stack supports it cleanly.

Playback can initially use:

```bash
aplay
```

with stdin piping.

Example:

```bash
aplay \
  -D plughw:CARD=Device,DEV=0 \
  -f S16_LE \
  -r 22050 \
  -c 1 \
  -t raw
```

Playback should start as soon as the first audio chunk arrives.

Do not wait for a complete generated audio file.

---

# Network protocol

Use a persistent **WebSocket per call/session**.

Suggested endpoint:

```text
ws://homeai.local:8000/zodiac
```

or equivalent within the existing HomeAI backend.

The socket should carry both:

- JSON control messages
- binary PCM audio frames

---

# Suggested protocol

## Client → server

Call begins:

```json
{
  "type": "off_hook",
  "terminal_id": "zodiac-01"
}
```

Audio:

```text
binary frame = PCM microphone audio
```

Speech boundary if client-side VAD/PTT is used:

```json
{
  "type": "end_of_speech"
}
```

Button press:

```json
{
  "type": "key",
  "key": "7"
}
```

Mute:

```json
{
  "type": "mute",
  "active": true
}
```

Call ends:

```json
{
  "type": "on_hook"
}
```

---

## Server → client

Call accepted:

```json
{
  "type": "call_started"
}
```

Assistant speaking:

```json
{
  "type": "assistant_speaking"
}
```

Audio:

```text
binary frame = PCM playback audio
```

Assistant finished:

```json
{
  "type": "assistant_finished"
}
```

LED state:

```json
{
  "type": "led",
  "state": "thinking"
}
```

Error:

```json
{
  "type": "error",
  "message": "STT service unavailable"
}
```

---

# HomeAI server component

Suggested structure:

```text
zodiac-service/
├── zodiac_service/
│   ├── websocket.py
│   ├── session.py
│   ├── stt.py
│   ├── llm.py
│   ├── tts.py
│   ├── audio.py
│   ├── protocol.py
│   └── logging_config.py
└── README.md
```

Where possible, reuse existing HomeAI STT and TTS implementations rather than introducing duplicate infrastructure.

You should first inspect the local HomeAI codebase and identify:

```text
existing STT service
existing TTS service
existing Ollama wrapper
existing streaming infrastructure
existing audio utilities
```

Prefer adapters around existing code.

Do not rewrite functioning HomeAI services unnecessarily.

---

# STT

The server should accept microphone PCM while the user is speaking.

Version 1 may buffer one complete user utterance before transcription.

However, design the interface so that streaming or incremental transcription can be introduced later.

If HomeAI already has streaming STT, use it.

Important logging:

```text
speech started
speech ended
audio duration
STT start
STT end
transcript
STT latency
```

---

# LLM

Use the existing Ollama service if available.

Preferred model:

```text
gemma4:12b
```

Suggested settings:

```text
thinking: false
temperature: ~0.3
num_predict: ~120
```

Maintain message history for the lifetime of the telephone call only.

When the handset is replaced:

```text
clear conversation state
```

A new call begins a new conversational session.

---

# Streaming response

Do not wait for the complete LLM response.

Tokens should be streamed.

Accumulate them until a natural spoken chunk is available.

Initially use sentence boundaries:

```text
.
?
!
```

Example:

```text
Gemma generates:

"The weather tomorrow should be sunny.
Temperatures should reach twenty-three degrees."
```

As soon as:

```text
"The weather tomorrow should be sunny."
```

is complete:

```text
send sentence to TTS
```

Gemma continues generating sentence 2 concurrently.

---

# TTS

Inspect and reuse the existing HomeAI TTS stack.

Requirements:

- local if practical
- streamable or low-latency
- suitable for spoken dialogue
- output can be converted to a fixed PCM format
- audio should begin returning before the full answer is complete

If the current TTS API generates complete WAV files only, implement sentence-level pipelining first.

Streaming synthesis can be a later optimisation.

---

# Latency goals

Logging must make perceived latency measurable.

Track at least:

```text
end of user speech
STT completed
LLM request started
first LLM token
first sentence completed
TTS started
first audio bytes sent
first audio bytes played
```

Primary metric:

```text
end-of-user-speech → first assistant audio
```

Initial target:

```text
< 2 seconds
```

Preferred target:

```text
~1 second or better
```

Do not sacrifice stability merely to meet the preferred target.

---

# Logging

Both client and server should use structured, readable logging.

Example server log:

```text
20:14:09.103 | CALL | zodiac-01 | started
20:14:12.501 | AUDIO | speech started
20:14:15.742 | AUDIO | speech ended | 3.24s
20:14:15.743 | STT | starting
20:14:16.301 | STT | transcript | "What's the weather tomorrow?"
20:14:16.302 | LLM | request | gemma4:12b
20:14:16.581 | LLM | first token | 0.279s
20:14:16.839 | TTS | sentence | "Tomorrow should be sunny."
20:14:17.041 | AUDIO | first bytes sent
```

Client:

```text
20:14:09.101 | GPIO | OFF_HOOK
20:14:09.104 | NET | websocket connected
20:14:09.220 | AUDIO | playback started
20:14:12.501 | AUDIO | microphone streaming
20:14:17.063 | AUDIO | assistant playback started
20:14:22.830 | GPIO | ON_HOOK
```

Optional JSON logging is welcome, but human-readable output should remain available.

---

# Hook switch

The hook switch is the first GPIO control to implement.

Use Raspberry Pi GPIO with internal pull-up/pull-down where appropriate.

The implementation must debounce the physical switch.

Desired state machine:

```text
ON_HOOK
   │
   │ handset lifted
   ▼
CONNECTING
   │
   ▼
ACTIVE_CALL
   │
   │ handset replaced
   ▼
DISCONNECTING
   │
   ▼
ON_HOOK
```

If the handset is replaced during:

- STT
- LLM generation
- TTS
- audio playback

cancel everything immediately.

---

# Keypad

The Zodiac keypad exposes approximately 17 conductors.

It has not yet been electrically mapped.

Do not assume a matrix layout in production code until measured.

Create a small standalone diagnostic utility:

```bash
python tools/keypad_mapper.py
```

The tool should help determine:

- which conductors are rows
- which conductors are columns
- which combinations correspond to buttons

Potential future implementation:

```text
MCP23017 I²C GPIO expander
```

or direct Pi GPIO if practical.

Keypad support should remain modular.

---

# Mute switch

The original handset mute switch physically interrupts or modifies the microphone circuit.

For privacy, preserve the physical audio muting behaviour.

If possible, separately expose mute state to GPIO so the application can also know:

```text
MUTE_ON
MUTE_OFF
```

This is optional for v1.

---

# Volume control

The existing physical handset volume control should remain in the analogue audio path.

Do not replace it with software volume unless required.

---

# Earpiece amplification

Current USB audio output is audible but relatively quiet.

Do not block the first prototype on this.

Create a documented hardware TODO:

```text
USB audio out
    ↓
small amplifier / impedance-appropriate driver
    ↓
~140 Ω Zodiac earpiece
```

Avoid selecting final amplifier hardware until output requirements are measured.

---

# Ringer / large internal speaker

The Zodiac contains a separate larger speaker apparently intended for:

- ringing
- tones
- beeps

This is not currently part of the prototype.

Future uses:

```text
incoming HomeAI notification
ring tone
system beep
error indication
doorbell
message waiting
```

Keep this as a separate optional output path.

---

# LEDs

Potential states:

```text
idle
connected
listening
thinking
speaking
error
offline
```

Do not hard-code colours until the original LED wiring has been mapped.

---

# Configuration

Use a YAML or TOML config file.

Example:

```yaml
terminal:
  id: zodiac-01
  name: Zodiac Sigma 300

homeai:
  websocket_url: ws://homeai.local:8000/zodiac

audio:
  capture_device: plughw:CARD=Device,DEV=0
  playback_device: plughw:CARD=Device,DEV=0
  capture_rate: 16000
  playback_rate: 22050

gpio:
  hook_pin: 17

conversation:
  greeting: "HomeAI. Hello Johan."

logging:
  level: INFO
```

Do not bake machine-specific ALSA device indexes into source code.

---

# Resilience

The terminal should behave like an appliance.

Requirements:

- reconnect automatically after Wi-Fi / LAN interruption. LAN is default
- survive HomeAI server restarts
- restart automatically if the Python client crashes
- reconnect when the next call is made
- never remain permanently stuck in a speaking/listening state
- hanging up must always reset the local state

Use `systemd` for the Raspberry Pi client.

---

# Security

This initially runs only on the trusted home LAN.

Do not over-engineer authentication for v1.

However:

- keep protocol design compatible with later token/auth support
- never expose the WebSocket endpoint publicly by default
- bind HomeAI appropriately for LAN-only use

---

# Raspberry Pi compatibility

Development hardware:

```text
Raspberry Pi 4 or 5
```

Target hardware:

```text
Raspberry Pi Zero W v1.1
ARMv6
512 MB RAM
```

Therefore:

- keep client dependencies lightweight
- avoid browser/UI dependencies
- avoid heavy frameworks
- avoid local AI inference
- prefer standard library + asyncio + websockets/aiohttp + GPIO library
- use ALSA rather than requiring a desktop audio stack
- avoid dependencies requiring NEON or newer ARM instruction sets

---

# Suggested implementation order

## Milestone 1 — Discover HomeAI services

Inspect the local HomeAI repository.

Document:

```text
STT implementation
TTS implementation
Ollama integration
reusable audio utilities
existing HTTP/WebSocket services
the handsset RPi is on cable on the same LAN, 192.168.68.153 (not WiFi)
```

Do not modify behaviour yet.

## Milestone 2 — Local Pi audio

On Raspberry Pi 4/5:

```text
USB sound card detected
microphone capture works
earpiece playback works
```

Provide diagnostic scripts.

## Milestone 3 — Network audio loop

Implement:

```text
Pi microphone
    ↓
WebSocket
    ↓
HomeAI
    ↓
echo audio back
    ↓
Pi earpiece
```

No AI yet.

This proves audio transport independently.

## Milestone 4 — STT / LLM / TTS

Replace echo server with:

```text
audio
 ↓
STT
 ↓
Gemma
 ↓
TTS
 ↓
audio
```

Maintain conversation state.

## Milestone 5 — Hook switch

Physical handset controls call lifetime.

Remove keyboard-based simulation.

## Milestone 6 — Latency optimisation

Implement:

- sentence-level LLM → TTS pipelining
- audio streaming
- VAD if useful
- incremental STT if supported

## Milestone 7 — Keypad

Map and integrate physical buttons.

## Milestone 8 — Appliance mode

Add:

```text
systemd
automatic boot
reconnect
health logging
configuration
```

## Milestone 9 — Pi Zero migration

Deploy the exact same client architecture to the Pi Zero W.

Only hardware/configuration-specific changes should be necessary.

---

# Definition of Done for first usable prototype

The prototype is successful when:

1. Zodiac is connected to a Raspberry Pi.
2. Raspberry Pi connects over LAN to HomeAI.
3. Lifting the physical handset starts a call. We dont have this wired, so we need a nother solution termporarily
4. HomeAI greets through the original earpiece.
5. User speaks using the original microphone.
6. Speech is transcribed on HomeAI.
7. Gemma generates a concise response.
8. HomeAI TTS creates spoken output.
9. Audio begins returning before an unnecessarily long pause.
10. Response plays through the original earpiece.
11. Multiple conversational turns work.
12. Replacing the handset immediately ends the call and clears conversation state.
13. Logs clearly show the complete path and latency.

---

# Non-goals for v1

Do not spend time yet on:

- cloud AI providers
- graphical UI
- touchscreen support
- local LLM on the Pi
- battery operation
- PoE
- final enclosure mounting
- perfect audio amplification
- sophisticated keypad behaviours
- multi-user identity
- wake words
- remote access outside the LAN

First make the telephone work beautifully as a telephone.

---

# Guiding principle

The goal is not:

```text
put a Raspberry Pi into an old phone
```

The goal is:

```text
make HomeAI inhabit a telephone
```

From the user's perspective, the computer should disappear.

Pick up.

Talk.

Listen.

Hang up.