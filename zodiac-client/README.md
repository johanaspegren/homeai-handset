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
sudo apt install alsa-utils python3-venv python3-gpiozero python3-lgpio
git clone <this repo> ~/dev/homeai-handset
cd ~/dev/homeai-handset/zodiac-client
./install.sh
```

The systemd unit expects exactly that path (`/home/pi/dev/homeai-handset`); if
yours differs, edit `WorkingDirectory` and `ExecStart` to match.

GPIO comes from apt, not pip: `pip install lgpio` is a source dist that swigs
and compiles a C extension and fails on a clean Pi with `command 'swig' failed`.
`install.sh` builds the venv with `--system-site-packages` so it can see apt's
prebuilt copies, which keeps everything pip installs pure-Python.

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
| `keypad.enabled` | `true` | off by default in code; on in the shipped config |
| `keypad.pins` | `[27, 22, 5, 6, 13, 19, 26]` | the seven conductors, BCM numbers |
| `keypad.mapping` | `{"1": [22, 26], ...}` | measured pairs, unordered |
| `keypad.debounce_ms` | `30` | how long a reading must hold still to be believed |
| `keypad.scan_hz` | `70` | full sweeps per second |

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

## The keypad

The keypad is the original one, with the phone's electronics removed: seven
bare conductors onto GPIO, a passive switch matrix, no diodes and **no series
resistors**. A pressed key is 25-300 ohms between two of those seven pins.

That last part sets the one rule `keypad.py` exists to keep:

> At any instant exactly **one** matrix pin is an output, it is **LOW**, and
> every other matrix pin is an input with its pull-up on. Nothing is ever
> driven HIGH.

With no resistance in the way, two pins driven as outputs and bridged by a
pressed key would put one straight into the other, and the Pi's pad is what
gives way. So the scan drives one line at a time and restores it before moving
on, even if the sweep raises — and `tests/test_keypad.py` fails if either half
of that is ever broken. Read the header of `keypad.py` before changing the scan.

It uses `lgpio` directly rather than gpiozero, which is otherwise this project's
GPIO library. Scanning means flipping a line between input and output about
seven hundred times a second; through gpiozero that is a device object closed
and another constructed each time, while lgpio re-claims a line on a handle we
already hold. The hook keeps its gpiozero `Button` on GPIO17 — separate handles
on the same chip are fine as long as they don't claim the same lines, and 17 is
not in the matrix.

Keypad watching runs alongside the hook, not inside a call, so a press
registers with the handset still in the cradle.

**Nothing acts on the buttons yet.** They are logged, and that is the whole
feature for now:

```text
21:14:31.204 | KEYPAD | KEY_DOWN 1
21:14:31.402 | KEYPAD | KEY_UP 1
```

Holding a button gives exactly one `KEY_DOWN` and, on release, exactly one
`KEY_UP` — no repeats. One key at a time is enough; a second contact while a
key is held is treated as a thumb across two buttons or a ghost, and keeps the
key that is already down rather than reporting a release that didn't happen.

### Mapping the rest of the buttons

Only 1, 2 and 3 are measured. The client maps the rest for you: press an
unknown button with it running and the pair prints once, ready to paste into
`config.yaml`.

```text
21:15:02.881 | KEYPAD | unmapped contact 5 <-> 26 — add it to keypad.mapping
```

```yaml
  mapping:
    "4": [5, 26]
```

Pairs are unordered — `[5, 26]` and `[26, 5]` are the same button — because
which end of the contact gets driven low is an accident of scan order.
`tools/keypad_mapper.py` does the same job standalone, on a phone whose
conductors haven't been sorted onto pins yet.

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

### After pulling new code

The unit runs the checkout in place, so a `git pull` needs one command and not
the install block above:

```bash
git pull && sudo systemctl restart zodiac-client
journalctl -u zodiac-client -f
```

`daemon-reload` and re-copying the unit are only for when
`systemd/zodiac-client.service` itself changed — `systemctl status
zodiac-client` says `changed on disk` when that's the case. `enable` is once,
ever.

`config.yaml` is tracked in git and edited on the Pi (the ALSA device name, the
keypad mapping), so a pull can land on top of your local edits. If git refuses
to pull, that's what it's protecting: keep the Pi's copy
(`git checkout --ours` / stash and reapply), don't clobber it.

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

**`BadPinFactory` / `No module named 'lgpio'`** — `sudo apt install
python3-gpiozero python3-lgpio` (not pip — see above), check the venv was built
with `--system-site-packages`, and check the account is in the `gpio` group
(`groups`). `install.sh` tests for all three.

**The hook fires twice per lift** — cradle switch bounce beyond the 50 ms
`debounce_ms`. Raise it; the cost is only how fast a hang-up registers.

**A button reports the wrong key, or nothing** — check the pair in
`keypad.mapping` against what the log prints as an unmapped contact. If a press
prints nothing at all, the two conductors for that key aren't both in
`keypad.pins`.

**`KEYPAD` events repeat while a button is held** — raise `keypad.debounce_ms`.
30 ms suits the Zodiac's contacts; older keypads chatter for longer.
