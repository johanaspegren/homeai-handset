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

echo "▶ creating .venv ..."
python3 -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt
echo "✔ installed: $(.venv/bin/pip list --format=freeze | tr '\n' ' ')"

# gpiozero is only needed once the cradle switch is wired; it's not in
# requirements.txt so a laptop or a Pi without the switch installs cleanly.
if [ "${WITH_GPIO:-}" = "1" ]; then
    echo "▶ installing gpiozero (hook switch) ..."
    .venv/bin/pip install --quiet gpiozero
fi

cat <<'NEXT'

Next:

  1. Find your USB sound card's real ALSA name and put it in config.yaml —
     never a bare card index, they renumber:

       arecord -L | grep -A1 CARD

  2. Check both directions before involving the network:

       .venv/bin/python -m zodiac_client.app --check-audio

  3. Prove the audio path with no AI in it — whatever you say comes straight
     back out of the earpiece:

       HOMEAI_WS_URL=ws://homeai.local:8400/zodiac/echo \
           .venv/bin/python -m zodiac_client.app

  4. Then the real thing. ENTER lifts the handset, ENTER replaces it:

       .venv/bin/python -m zodiac_client.app

NEXT
