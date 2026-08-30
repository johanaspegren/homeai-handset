Zodiac Sigma 300 — Keypad GPIO Support
Goal

Add support for the original Zodiac telephone keypad to the existing Raspberry Pi zodiac client.

The Pi should treat the keypad as hardware input only, just like the existing hook switch. For now, keypad events should only be logged. Do not change the HomeAI websocket protocol yet.

Hardware

The keypad is a passive switch matrix disconnected from the original phone electronics.

Seven Raspberry Pi GPIO pins are connected directly to the keypad:

GPIO27
GPIO22
GPIO5
GPIO6
GPIO13
GPIO19
GPIO26

Known mapping:

Button 1 = GPIO22 ↔ GPIO26
Button 2 = GPIO13 ↔ GPIO26
Button 3 = GPIO6 ↔ GPIO26

More button mappings will be added as they are measured.

Pressed contacts have approximately 25–300 Ω resistance.

There are currently no series resistors between the keypad and GPIO.

Critical GPIO safety rule

Matrix scanning must never drive two GPIO lines as outputs simultaneously.

At any instant:

ONE matrix GPIO = OUTPUT LOW
ALL OTHER matrix GPIOs = INPUT with pull-up

Never drive a matrix GPIO HIGH.

Before selecting another scan line, return the current output line to INPUT first.

Thus a pressed key only connects an input/pull-up GPIO to the single LOW GPIO.

Configuration

Extend config.yaml with something along these lines:

keypad:
  enabled: true
  pins: [27, 22, 5, 6, 13, 19, 26]
  debounce_ms: 30

  mapping:
    "1": [22, 26]

The mapping must be configuration-driven so additional buttons can be added without changing Python code.

Treat GPIO pairs as unordered: [22,26] and [26,22] represent the same key.

Software design

Add a module such as:

zodiac/keypad.py

following the same general philosophy as the existing hook GPIO implementation.

Provide an async interface roughly equivalent to:

async for event in keypad.events():
    ...

Events should distinguish at least:

KEY_DOWN
KEY_UP

and contain the mapped key.

For example:

KeyEvent(type="KEY_DOWN", key="1")
KeyEvent(type="KEY_UP", key="1")

A simple dict/dataclass is fine; follow existing project conventions.

Scanning

Continuously scan the seven GPIO lines at a reasonable rate, roughly 50–100 complete scans/sec.

For each candidate drive line:

Ensure all matrix pins are inputs.
Configure that line as OUTPUT LOW.
Allow a very short settling period if necessary.
Read all other lines as INPUT with pull-up.
Any LOW input represents a closed contact between the two GPIOs.
Look up that unordered pair in the configured mapping.
Return the drive GPIO to INPUT before scanning another line.

Avoid repeatedly constructing/destroying GPIO resources if the GPIO library/API provides a cleaner way of changing direction on persistent lines.

Debouncing

Implement software debounce using keypad.debounce_ms.

Holding a button should produce exactly:

KEY_DOWN 1

and releasing it:

KEY_UP 1

It must not repeatedly generate KEY_DOWN while held.

Initially, supporting one simultaneous key press is sufficient. We don't need heroic handling of someone playing chords on a telephone keypad.

ZodiacClient integration

Keypad monitoring should run independently alongside the existing hook monitoring.

The existing behaviour must remain unchanged:

hook → start/end call
microphone → websocket
websocket audio → earpiece

Add keypad monitoring as another asynchronous hardware event source.

For this first implementation, simply log:

KEYPAD  KEY_DOWN 1
KEYPAD  KEY_UP 1

Do not send keypad events to HomeAI yet.

Keypad monitoring should preferably work whether the handset is on-hook or off-hook.

Existing hook

The cradle switch already uses:

GPIO17

Do not modify its behaviour or configuration.

First acceptance test

With this configuration:

mapping:
  "1": [22, 26]

start the Zodiac client and verify:

No button pressed → no keypad events.
Press 1 → exactly one KEY_DOWN 1.
Hold 1 → no repeated events.
Release 1 → exactly one KEY_UP 1.
Hook operation on GPIO17 continues to work normally.
Audio/call operation remains unaffected.

Once this works, the remaining keypad GPIO pairs will be measured and added to config.yaml