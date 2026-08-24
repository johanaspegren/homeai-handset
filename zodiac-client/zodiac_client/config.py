"""Client configuration.

Everything machine-specific — ALSA device names, the GPIO pin, which HomeAI to
call — lives in config.yaml, never in the source. A second Zodiac should only
need its own config file.
"""

import copy
import logging
import os
from pathlib import Path

import yaml

log = logging.getLogger("zodiac.config")

DEFAULTS = {
    "terminal": {"id": "zodiac-01", "name": "Zodiac Sigma 300"},
    "homeai": {
        "websocket_url": "ws://homeai.local:8400/zodiac",
        "reconnect_delay_s": 1.0,
        "reconnect_max_delay_s": 15.0,
    },
    "audio": {
        "capture_device": "plughw:CARD=Device,DEV=0",
        "playback_device": "plughw:CARD=Device,DEV=0",
        "capture_rate": 16000,
        "playback_rate": 22050,
        "frame_ms": 40,
        "buffer_us": 200000,
        "period_us": 50000,
    },
    "hook": {"source": "stdin", "pin": 17, "invert": False,
             "debounce_ms": 50, "pull_up": True},
    "logging": {"level": "INFO"},
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load(path: str | Path | None = None) -> dict:
    path = Path(path or os.getenv("ZODIAC_CONFIG", "config.yaml"))
    if path.exists():
        with open(path) as fh:
            settings = _merge(DEFAULTS, yaml.safe_load(fh) or {})
        log.info("config loaded | %s", path)
    else:
        settings = copy.deepcopy(DEFAULTS)
        log.warning("no config at %s — using defaults", path)
    # A single env override, because it's the one thing you change while
    # debugging on a strange network.
    url = os.getenv("HOMEAI_WS_URL")
    if url:
        settings["homeai"]["websocket_url"] = url
    return settings


def frame_bytes(audio: dict) -> int:
    """Bytes per microphone frame at the configured rate and frame length."""
    samples = int(audio["capture_rate"] * audio["frame_ms"] / 1000)
    return samples * 2  # int16 mono
