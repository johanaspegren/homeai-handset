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
git clone <this repo> ~/dev/homeai-handset
cd ~/dev/homeai-handset/zodiac-client
./install.sh
```

The systemd unit expects exactly that path (`/home/pi/dev/homeai-handset`); if
yours differs, edit `WorkingDirectory` and `ExecStart` to match.

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

Lift the handset and it calls; replace it and everything stops. (With
`hook.source: stdin`, ENTER stands in for both — useful on a desk.)

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
| `hook.source` | `gpio` | `gpio` \| `stdin` \| `always` |
| `hook.pin` | `17` | BCM number of the cradle switch |
| `hook.invert` | `true` | the Zodiac closes its switch when the handset is *lifted* |

`HOMEAI_WS_URL` overrides the URL from the environment, for debugging on a
strange network.

## The hook switch

The hook is the phone's power button and its privacy guarantee: between "down"
and "lifted" there is no microphone. `gpio.py` defines a `HookSource` interface
with three implementations that emit identical events:

- **`gpio`** — the real switch on BCM 17, debounced (gpiozero `bounce_time`).
  What the Zodiac runs.
- **`stdin`** — ENTER toggles. Bring-up on a desk, with no GPIO at all.
- **`always`** — permanently off-hook, for soak tests.

Nothing downstream knows the difference, which is why wiring the switch was a
config change rather than a rewrite.

**`invert` is a safety setting, not a preference.** `GpioHook` is written for
the classic cradle plunger — switch closed to ground when the handset is
**down**, so `pressed` means on-hook. The Zodiac is the other way round: it
closes when the handset is **lifted**, hence `invert: true`. Get this backwards
and the phone opens a call while sitting in the cradle and hangs up when you
pick it up — meaning the microphone is live at exactly the times the hook is
supposed to guarantee it isn't. Lifting the handset must log `OFF_HOOK`; if it
logs `ON_HOOK`, flip `invert` before going any further.

## Run at boot

`systemd/zodiac-client.service` restarts the client forever and survives HomeAI
being down (it reconnects with backoff, and a dropped socket while the handset
is still up reconnects as a fresh call).

```bash
sudo cp systemd/zodiac-client.service /etc/systemd/system/
sudo systemctl enable --now zodiac-client
journalctl -u zodiac-client -f
```

The unit runs as `pi` from `/home/pi/dev/homeai-handset/zodiac-client`. Edit
both paths if your checkout is elsewhere.

Run the client by hand at least once before enabling this, and confirm lifting
the handset logs `OFF_HOOK` — under systemd there is no terminal, so the
`stdin` hook can never fire a call and a wrong `invert` is harder to spot. Stop
the service before running it by hand again: only one process can own the sound
card.

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

**The call starts and stops backwards** — `hook.invert` is wrong. See "The hook
switch".

**`BadPinFactory` / `No module named 'lgpio'`** — the venv has `gpiozero` but no
pin factory it can use. `.venv/bin/pip install lgpio`, and check the account is
in the `gpio` group (`groups`). `install.sh` tests for this.

**The hook fires twice per lift** — cradle switch bounce beyond the 50 ms
`debounce_ms`. Raise it; the cost is only how fast a hang-up registers.
