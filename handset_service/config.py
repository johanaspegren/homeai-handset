"""Service configuration, read from the environment (see .env.example).

Nothing machine-specific is baked into the code: audio devices live on the Pi
client's config.yaml, models and URLs live here.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

REPO_DIR = Path(__file__).resolve().parent.parent

# --- where we listen -------------------------------------------------------
HOST = os.getenv("HANDSET_HOST", "0.0.0.0")
PORT = int(os.getenv("HANDSET_PORT", "8400"))

# --- the brain -------------------------------------------------------------
# The handset is a channel adapter like the Discord bot and homeai-voice: it
# does STT/TTS and hands the text to homeai-server, which owns the persona,
# memory, tools and journal.
HOMEAI_CHAT_URL = os.getenv("HOMEAI_CHAT_URL", "http://localhost:8000/chat")
HANDSET_USER = os.getenv("HANDSET_USER", "johan")
HANDSET_SOURCE = os.getenv("HANDSET_SOURCE", "handset")
# Where the telephone is. The Pi announces this in its off_hook frame, because
# a second Zodiac in another room is the same code with a different config; this
# is only the fallback for a client too old to send one. The server turns a
# place into actual speakers and lights — neither the Pi nor this service ever
# learns their names, so "play something" down the phone can mean the workshop
# speakers rather than the earpiece pressed against your ear.
HANDSET_PLACE = os.getenv("HANDSET_PLACE", "workshop")
CHAT_TIMEOUT_S = float(os.getenv("HANDSET_CHAT_TIMEOUT_S", "60"))

# --- the other Kiri --------------------------------------------------------
# The workshop's always-on mic hears the telephone conversation too, and
# answers it — two of her at once. On by default: the handset holds the
# workshop mic for the length of the call. Turn it off (0) to keep both
# listening, which makes the pair a hands-free setup instead.
MUTE_VOICE_DURING_CALL = os.getenv("HANDSET_MUTES_VOICE", "1").strip().lower() not in (
    "0", "false", "no", "off",
)
# Same endpoint the dashboard's mute button uses; derived from the chat URL so
# there's only one address to configure.
VOICE_MUTE_URL = os.getenv(
    "HANDSET_VOICE_MUTE_URL", HOMEAI_CHAT_URL.replace("/chat", "/voice/mute"),
)

# --- hands free ------------------------------------------------------------
# The telephone is the default input, so homeai-voice isn't started with the
# stack: a muted voice service still VADs every frame and Whispers every
# utterance in the room, which is GPU spent on a conversation nobody is having.
# Hands free starts it on demand — the workshop mic and speakers, when wanted.
HOMEAI_SH = Path(os.getenv("HOMEAI_SH", str(REPO_DIR.parent / "homeai.sh"))).expanduser()
VOICE_PIDFILE = Path(
    os.getenv("HOMEAI_VOICE_PIDFILE", str(REPO_DIR.parent / "logs" / "voice.pid"))
).expanduser()
# Which keypad button toggles it. The Zodiac's keypad isn't mapped yet (see
# tools/keypad_mapper.py), so until it is, use POST /handsfree.
HANDSFREE_KEY = os.getenv("HANDSET_HANDSFREE_KEY", "*").strip()
# Starting homeai-voice loads Whisper onto the GPU — allow for it.
HANDSFREE_TIMEOUT_S = float(os.getenv("HANDSET_HANDSFREE_TIMEOUT_S", "45"))

# --- the keypad ------------------------------------------------------------
# What the Zodiac's buttons say. Each binding is a key and the words it puts to
# Kiri, exactly as if they had been spoken down the line: the button is a stored
# phrase, not a command, so a press goes through /chat like everything else and
# gets the vault, the persona and the `<<run:>>` markers for free. That is why
# "put some music on" reaches Spotify without this service ever having heard of
# Spotify — and why it lands on the speakers in the room the phone is in.
#
# Separated by `|` because the phrases contain commas:
#     HANDSET_KEYS="5=put some music on|6=next track"
#
# The default set follows the phone's own shape rather than a menu nobody can
# remember: the middle row is the transport you'd expect on any music player,
# 0 stops it, and `*` (HANDSET_HANDSFREE_KEY, handled as an action rather than a
# phrase) opens the room up into what is effectively a conference call.
KEY_PHRASES = {
    key.strip(): phrase.strip()
    for key, _, phrase in (
        binding.partition("=")
        for binding in os.getenv(
            "HANDSET_KEYS",
            "4=previous track"
            "|5=put some music on"
            "|6=next track"
            "|0=pause the music",
        ).split("|")
    )
    if key.strip() and phrase.strip()
}

# --- the status ring (homeai-halo) -----------------------------------------
# Voice used to drive Kiri's LED ring; it doesn't run by default now, so the
# handset owns it — and hands it back while hands free is on.
HALO_ENABLED = os.getenv("HALO_ENABLED", "1").strip().lower() not in ("0", "false", "no")
HALO_PORT = os.getenv("HALO_PORT", "/dev/ttyUSB0")
HALO_DIR = Path(
    os.getenv("HALO_DIR", str(REPO_DIR.parent / "homeai-halo"))
).expanduser()
# What the ring shows when nothing is happening: 'resting' is the slow breathe
# (needs the firmware from 2026-08-24 or later); 'idle' is the gliding dot.
HALO_RESTING_STATE = os.getenv("HALO_RESTING_STATE", "resting")

# --- speech to text --------------------------------------------------------
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small.en")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cuda")
# Empty -> ctranslate2's per-device default (float16 on cuda). int8_float16
# shrinks VRAM when gemma is resident.
WHISPER_COMPUTE = os.getenv("WHISPER_COMPUTE", "").strip()

# --- text to speech --------------------------------------------------------
# Reuse homeai-voice's Piper voice files rather than downloading a second copy.
HOMEAI_VOICE_DIR = Path(
    os.getenv("HOMEAI_VOICE_DIR", str(REPO_DIR.parent / "homeai-voice"))
).expanduser()
PIPER_VOICE = os.getenv("PIPER_VOICE", "en_US-lessac-medium")
PIPER_VOICE_DIR = Path(
    os.getenv("PIPER_VOICE_DIR", str(HOMEAI_VOICE_DIR / "voices"))
).expanduser()

# --- audio format ----------------------------------------------------------
# Capture is fixed at what Whisper wants. Playback rate is whatever the loaded
# Piper voice produces (22050 for the medium voices) — the server tells the
# client in `call_started` so the two can never drift apart.
CAPTURE_RATE = int(os.getenv("HANDSET_CAPTURE_RATE", "16000"))
FRAME_SAMPLES = 1280  # 80 ms — the frame size silero-VAD expects
FRAME_BYTES = FRAME_SAMPLES * 2  # int16

# --- turn taking -----------------------------------------------------------
VAD_THRESHOLD = float(os.getenv("VAD_THRESHOLD", "0.5"))
VAD_HANGOVER_MS = int(os.getenv("VAD_HANGOVER_MS", "700"))      # silence that ends a turn
VAD_PREROLL_MS = int(os.getenv("VAD_PREROLL_MS", "300"))        # kept from before speech onset
VAD_MIN_SPEECH_MS = int(os.getenv("VAD_MIN_SPEECH_MS", "250"))  # ignore shorter blips
VAD_MAX_UTTERANCE_MS = int(os.getenv("VAD_MAX_UTTERANCE_MS", "20000"))

# --- call behaviour --------------------------------------------------------
# Spoken as the handset comes off the hook, before any model is involved —
# picking up a phone should never wait on an LLM.
#
# Several lines separated by "|", one picked per call, so she doesn't greet you
# with exactly the same words every time. A single line (no "|") behaves as it
# always did; empty means answer silently.
GREETINGS = [
    line.strip()
    for line in os.getenv(
        "HANDSET_GREETING",
        "HomeAI. Hello Johan."
        "|Kiri here. Hello Johan."
        "|Hello Johan."
        "|Kiri speaking.",
    ).split("|")
    if line.strip()
]
# Lifting a handset and getting it to your ear takes a moment. Without this she
# has already started the first word by the time it arrives. It's dead time on
# the clock, but it's dead time you spend moving your arm, so it doesn't read
# as latency the way a pause after a question does.
GREETING_DELAY_S = int(os.getenv("HANDSET_GREETING_DELAY_MS", "800")) / 1000

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
