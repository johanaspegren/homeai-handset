#!/usr/bin/env bash
# Install the Zodiac client on the Raspberry Pi.
#
# Run it from this directory:  ./install.sh
#
# It exists mostly to stop you installing the repo-root requirements.txt by
# mistake — those are the homeai box's (faster-whisper, onnxruntime, CUDA
# wheels): gigabytes of download that do nothing here. The client needs two
# pure-Python packages.

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

if [ ! -f zodiac_client/app.py ]; then
    echo "✘ run this from the zodiac-client directory"
    exit 1
fi

echo "▶ checking for ALSA tools ..."
missing=""
for tool in arecord aplay; do
    command -v "$tool" >/dev/null || missing="$missing $tool"
done
if [ -n "$missing" ]; then
    echo "✘ missing:$missing — install them with:  sudo apt install alsa-utils"
    exit 1
fi
echo "✔ arecord and aplay present"

echo "▶ checking for GPIO packages ..."
gpio_missing=""
for pkg in gpiozero lgpio; do
    python3 -c "import $pkg" 2>/dev/null || gpio_missing="$gpio_missing python3-$pkg"
done
if [ -n "$gpio_missing" ]; then
    echo "⚠ missing:$gpio_missing — the hook switch needs them:"
    echo "    sudo apt install$gpio_missing"
    echo "  (apt, not pip: pip's lgpio compiles a C extension and wants swig.)"
else
    echo "✔ gpiozero and lgpio present"
fi

echo "▶ creating .venv ..."
# --system-site-packages so the venv can see apt's python3-gpiozero and
# python3-lgpio. pip's lgpio is a source dist that swigs and compiles a C
# extension; apt's is prebuilt. See requirements.txt.
python3 -m venv --system-site-packages .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt
echo "✔ installed: $(.venv/bin/pip list --format=freeze --local | tr '\n' ' ')"

# The import succeeds on any machine; opening a pin doesn't. Finding that out
# at boot under systemd is a worse place to find it out.
echo "▶ checking GPIO access ..."
if .venv/bin/python -c "from gpiozero import Device; Device.ensure_pin_factory()" 2>/dev/null; then
    echo "✔ GPIO reachable"
else
    echo "⚠ no usable GPIO pin factory — the hook switch won't work."
    echo "  On a Pi:     sudo apt install python3-gpiozero python3-lgpio"
    echo "               and check this account is in the 'gpio' group (groups)."
    echo "  On a laptop: set hook.source: stdin in config.yaml."
fi

cat <<'NEXT'

Next:

  1. Find your USB sound card's real ALSA name and put it in config.yaml —
     never a bare card index, they renumber:

       arecord -L | grep -A1 CARD

  2. Check both directions before involving the network:

       .venv/bin/python -m zodiac_client.app --check-audio

  3. Prove the audio path with no AI in it — whatever you say comes straight
     back out of the earpiece. Lift the handset to start, replace it to stop:

       HOMEAI_WS_URL=ws://homeai.local:8400/zodiac/echo \
           .venv/bin/python -m zodiac_client.app

  4. Then the real thing, by hand once before you trust it to boot:

       .venv/bin/python -m zodiac_client.app

     Lifting the handset should log OFF_HOOK. If it logs ON_HOOK instead, the
     switch senses the other way round — flip hook.invert in config.yaml.

  5. Run it at boot:

       sudo cp systemd/zodiac-client.service /etc/systemd/system/
       sudo systemctl enable --now zodiac-client
       journalctl -u zodiac-client -f

NEXT
