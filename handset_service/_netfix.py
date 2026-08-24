"""Forces IPv4-only DNS resolution.

This network can't route IPv6 to some hosts (e.g. huggingface.co) — outbound
connections silently hang for minutes before anything times out. Import this
before any code that makes outbound HTTP calls during setup (Whisper model
downloads), so failed IPv6 routes never get attempted in the first place.

(Same shim as homeai-voice/_netfix.py — kept local so the service has no
runtime dependency on another repo's checkout.)
"""

import socket

_original_getaddrinfo = socket.getaddrinfo


def _ipv4_only_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    return [
        info
        for info in _original_getaddrinfo(host, port, family, type, proto, flags)
        if info[0] == socket.AF_INET
    ]


socket.getaddrinfo = _ipv4_only_getaddrinfo
