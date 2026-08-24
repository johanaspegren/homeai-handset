"""The Zodiac wire protocol — one WebSocket per call.

A single socket carries both directions of a phone call:

  * **JSON text frames** for control (hook state, keys, LEDs, turn boundaries)
  * **binary frames** for raw PCM audio — client->server is microphone,
    server->client is earpiece.

Audio frames carry no header. The format is fixed for the life of the call and
announced in `call_started`, so neither side has to guess a sample rate.

This module is deliberately dependency-free and is *copied verbatim* into
zodiac-client/zodiac_client/protocol.py — the Pi client must not need the
service package installed. Keep the two files identical.
"""

PROTOCOL_VERSION = 1

# --- client -> server ------------------------------------------------------
OFF_HOOK = "off_hook"            # {"terminal_id": "zodiac-01"} — call begins
ON_HOOK = "on_hook"              # call ends, cancel everything in flight
END_OF_SPEECH = "end_of_speech"  # optional: client-side VAD/PTT boundary
KEY = "key"                      # {"key": "7"}
MUTE = "mute"                    # {"active": true}
PING = "ping"

# --- server -> client ------------------------------------------------------
CALL_STARTED = "call_started"          # {"audio": {...}, "greeting": "..."}
LISTENING = "listening"                # your turn — mic audio is being consumed
TRANSCRIPT = "transcript"              # {"text": "..."} — what we heard (logs/UI)
ASSISTANT_SPEAKING = "assistant_speaking"
ASSISTANT_FINISHED = "assistant_finished"
MIC = "mic"                            # {"active": false} — gate the mic (half-duplex)
LED = "led"                            # {"state": "thinking"}
ERROR = "error"                        # {"message": "..."}
PONG = "pong"

# LED / lamp states, kept as plain strings so the client can map them to
# whatever the Zodiac's original wiring turns out to be.
LED_IDLE = "idle"
LED_CONNECTED = "connected"
LED_LISTENING = "listening"
LED_THINKING = "thinking"
LED_SPEAKING = "speaking"
LED_ERROR = "error"
LED_OFFLINE = "offline"


def message(type_: str, **fields) -> dict:
    """Build a control message. Extra fields are merged in as-is."""
    return {"type": type_, **fields}


def audio_format(rate: int, channels: int = 1, encoding: str = "s16le") -> dict:
    """Describe a PCM stream. `encoding` matches ALSA's naming (`-f S16_LE`)."""
    return {"rate": rate, "channels": channels, "encoding": encoding}
