#!/usr/bin/env python3
"""Work out how the Zodiac's keypad is wired, before writing code that assumes.

The keypad exposes roughly 17 conductors and has not been measured. This tool
makes no assumption about a matrix: it drives each conductor low in turn and
reads the rest, so whatever the layout turns out to be, pressing a key shows up
as a (driven, sensed) pair.

Run it ON THE PI, with the keypad's conductors on GPIO pins you list below:

    python3 tools/keypad_mapper.py --pins 4,5,6,12,13,16,17,18,19,20,21,22,23,24,25,26,27

Then press keys one at a time. Each detected pair is printed and, with
--save keypad.json, written out as a starting map:

    {"pairs": {"1": [17, 27], "2": [17, 22], ...}}

Nothing else in the project reads that file yet — mapping first, integration
after (spec: "Do not assume a matrix layout in production code until measured").
"""

import argparse
import json
import sys
import time


def scan(pins, gpio):
    """One full sweep: drive each pin low, note which others follow it down."""
    found = []
    for driver in pins:
        gpio.setup(driver, gpio.OUT)
        gpio.output(driver, gpio.LOW)
        for sensed in pins:
            if sensed == driver:
                continue
            gpio.setup(sensed, gpio.IN, pull_up_down=gpio.PUD_UP)
            if gpio.input(sensed) == gpio.LOW:
                found.append((driver, sensed))
        gpio.setup(driver, gpio.IN, pull_up_down=gpio.PUD_UP)
    return found


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pins", required=True,
                        help="comma-separated BCM pin numbers the keypad is on")
    parser.add_argument("--save", help="write the discovered map to this JSON file")
    parser.add_argument("--interval", type=float, default=0.05, help="seconds between sweeps")
    args = parser.parse_args()

    try:
        import RPi.GPIO as gpio
    except ImportError:
        print("This tool needs RPi.GPIO and must run on the Pi:  pip install RPi.GPIO",
              file=sys.stderr)
        raise SystemExit(1)

    pins = [int(p) for p in args.pins.split(",") if p.strip()]
    gpio.setmode(gpio.BCM)
    gpio.setwarnings(False)
    for pin in pins:
        gpio.setup(pin, gpio.IN, pull_up_down=gpio.PUD_UP)

    print(f"Watching {len(pins)} conductors: {pins}")
    print("Press ONE key at a time and hold it briefly. Ctrl-C when done.\n")

    mapping: dict[str, list[int]] = {}
    last = None
    try:
        while True:
            pairs = scan(pins, gpio)
            if pairs and pairs != last:
                for driver, sensed in pairs:
                    print(f"  contact: {driver:>2} <-> {sensed:<2}", flush=True)
                if len(pairs) == 1:
                    label = input("    which key is that? (ENTER to skip) ").strip()
                    if label:
                        mapping[label] = list(pairs[0])
                last = pairs
            elif not pairs:
                last = None
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        gpio.cleanup()

    if mapping:
        print("\nMapped:")
        for key, pair in sorted(mapping.items()):
            print(f"  {key}: {pair}")
        if args.save:
            with open(args.save, "w") as fh:
                json.dump({"pairs": mapping}, fh, indent=2)
            print(f"\nwritten to {args.save}")


if __name__ == "__main__":
    main()
