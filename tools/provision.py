#!/usr/bin/env python3
"""Sends the board its network settings over serial, so they never enter a build.

Values come from the environment where it has them and from a prompt where it
does not. Secrets are read without echo, are never written to disk, never appear
on a command line, and are never printed back — not here and not by the board.

The signing key is the exception that has to be shared: the server verifies
deliveries with the same value, so it is taken from the environment variable the
server reads rather than a name of its own.
"""

import os
import sys
from getpass import getpass

import board

# Console command, environment variable, what to call it when asking, withhold it.
SETTINGS = (
    ("network", "WIFI_SSID", "network name", False),
    ("passphrase", "WIFI_PASSWORD", "passphrase", True),
    ("url", "SERVER_URL", "server url", False),
    ("key", "SCALE_GATEWAY_KEY", "signing key", True),
    ("scale", "SCALE_ADDRESSES", "scale addresses, separated by spaces", False),
)


def collect() -> list[tuple[str, str, bool]]:
    collected = []
    for command, variable, label, withheld in SETTINGS:
        value = os.environ.get(variable, "").strip()
        if not value:
            ask = getpass if withheld else input
            value = ask(f"{label} (${variable} is unset): ").strip()
        if not value:
            sys.exit(f"no {label} given")
        collected.append((command, value, withheld))
    return collected


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit(f"usage: {sys.argv[0]} <port> <baud>")
    device, baud = sys.argv[1], int(sys.argv[2])

    settings = collect()

    with board.open_port(device, baud) as port:
        board.restart(port)
        for command, value, withheld in settings:
            reply = board.send(port, f"{command} {value}")
            print(f"  {command} {'*' * 8 if withheld else value} -> {reply}")
        # Long enough for the association and the ping that follows it.
        print(f"\n{board.send(port, 'connect', settle_s=10.0)}")
        print(f"\n{board.send(port, 'show', settle_s=1.0)}")


if __name__ == "__main__":
    main()
