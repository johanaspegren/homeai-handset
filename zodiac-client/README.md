# zodiac-client

The Raspberry Pi half of the Zodiac telephone. It owns hardware and transport
and nothing else — it has no idea what speech recognition or a language model
is. Lift the handset, it opens a socket; microphone bytes go up, earpiece bytes
come down; replace the handset and everything stops.

Deliberately small: pure-Python dependencies only (`websockets`, `PyYAML`),
audio via `arecord`/`aplay`. No PipeWire, no numpy, no compiler. That is what
lets the same code move to the ARMv6 Pi Zero W later — spec Milestone 9 should
be a copy, a config edit, and nothing else.

Development hardware: Pi 4/5 at `192.168.68.153`, wired LAN.

## Install (on the Pi)

```bash
sudo apt install alsa-utils python3-venv
git clone <this repo> ~/homeai-handset   # or copy just the zodiac-client/ directory
cd ~/homeai-handset/zodiac-client
./install.sh                             # WITH_GPIO=1 ./install.sh once the switch is wired
```

> **Install from *this* directory's `requirements.txt`, not the repo root's.**
> The root one is the homeai box's — faster-whisper, onnxruntime and two NVIDIA
> CUDA wheels, gigabytes that do nothing on a Pi and that nothing here imports.
> The client needs `websockets` and `PyYAML`. `install.sh` gets this right for
> you; if you already did it the other way, `rm -rf .venv`, run `install.sh`,
> and `pip cache purge` to reclaim the downloads.

Find the USB sound card's real ALSA name and put it in `config.yaml` — never a
bare card index, they renumber:

```bash
arecord -L | grep -A1 CARD
aplay -L  | grep -A1 CARD
# e.g. plughw:CARD=Device,DEV=0
```

Check both directions before involving the network:

```bash
.venv/bin/python -m zodiac_client.app --check-audio

# and by hand, the two commands the client itself runs:
arecord -D plughw:CARD=Device,DEV=0 -f S16_LE -r 16000 -c 1 -t wav /tmp/t.wav
aplay   -D plughw:CARD=Device,DEV=0 /tmp/t.wav
```

## Run

```bash
.venv/bin/python -m zodiac_client.app --config config.yaml
```

With `hook.source: stdin` (the default until the cradle switch is wired),
**ENTER lifts the handset and ENTER replaces it**:

```text
☎  ENTER = lift handset / replace handset, q = quit

21:14:09.101 | GPIO  | OFF_HOOK
21:14:09.104 | NET   | websocket connected | ws://homeai.local:8400/zodiac
21:14:09.220 | NET   | call started | playback 22050 Hz
21:14:09.221 | AUDIO | assistant playback started      <- "HomeAI. Hello Johan."
21:14:12.501 | AUDIO | microphone streaming
21:14:16.301 | STT   | heard | "what's the mower doing?"
21:14:17.063 | AUDIO | assistant playback started
21:14:22.830 | GPIO  | ON_HOOK
```

Prove the audio path with no AI in it (spec Milestone 3) by pointing at the
loopback endpoint — whatever you say comes straight back out of the earpiece:

```bash
HOMEAI_WS_URL=ws://homeai.local:8400/zodiac/echo .venv/bin/python -m zodiac_client.app
```

If the echo sounds right and the real thing doesn't, the problem is on the
homeai box. If the echo is wrong too, it's the wiring, the card or ALSA.

## Configuration (`config.yaml`)

| Key | Default | Notes |
|---|---|---|
| `homeai.websocket_url` | `ws://homeai.local:8400/zodiac` | use the hostname; homeai's DHCP lease moves |
| `audio.capture_device` / `playback_device` | `plughw:CARD=Device,DEV=0` | from `arecord -L`; overridden per Pi |
| `audio.capture_rate` | `16000` | what Whisper wants |
| `audio.playback_rate` | `22050` | fallback only — the server announces the real rate at call start |
| `audio.frame_ms` | `40` | microphone chunk size |
| `audio.buffer_us` | `200000` | ALSA playback buffer: lower = snappier first word, higher = more forgiving |
| `hook.source` | `stdin` | `gpio` \| `stdin` \| `always` |
| `hook.pin` | `17` | BCM number of the cradle switch |
| `hook.invert` | `false` | set true if the switch closes when the handset is *lifted* |

`HOMEAI_WS_URL` overrides the URL from the environment, for debugging on a
strange network.

## The hook switch

The hook is the phone's power button and its privacy guarantee: between "down"
and "lifted" there is no microphone. Because the Zodiac's cradle switch isn't
wired to the Pi yet, `gpio.py` defines a `HookSource` interface with three
implementations that emit identical events:

- **`gpio`** — the real switch, debounced (gpiozero `bounce_time`). Assumes the
  switch closes to ground when the handset is **down**; measure with a
  multimeter and set `invert: true` if the Zodiac disagrees.
- **`stdin`** — ENTER toggles. What the prototype uses today.
- **`always`** — permanently off-hook, for soak tests.

Nothing downstream knows the difference, so wiring the switch is a one-line
config change, not a rewrite.

## Run at boot

`systemd/zodiac-client.service` restarts the client forever and survives HomeAI
being down (it reconnects with backoff, and a dropped socket while the handset
is still up reconnects as a fresh call).

```bash
sudo cp systemd/zodiac-client.service /etc/systemd/system/
sudo systemctl enable --now zodiac-client
journalctl -u zodiac-client -f
```

**Only enable this once `hook.source` is `gpio`.** Under systemd there is no
terminal, so the `stdin` hook can never fire a call.

## Troubleshooting

**"device busy"** — something else holds the card. Only one process may own it;
`fuser -v /dev/snd/*` names the culprit.

**Speech sounds slow or chipmunked** — a sample-rate mismatch. The server
announces its rate in `call_started`; check the `playback NNNNN Hz` log line
matches what Piper produces (22050).

**Crackling or dropouts** — raise `audio.buffer_us`. It costs latency.

**Kiri hears herself** — the mic is gated while she speaks (`mic {active}`),
and the server drops any audio that arrives during her turn. If it still
happens, the handset's earpiece is bleeding into the mouthpiece badly enough to
need real echo cancellation.

**Nothing happens on ENTER** — you are probably running under systemd with the
`stdin` hook. See above.
