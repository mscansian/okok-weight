#!/usr/bin/env python3
"""Runs one command on the gateway's serial console and prints what it says.

Opening the port resets the board, so the command runs against a fresh boot.
"""

import sys

import board


def main() -> None:
    if len(sys.argv) < 4:
        sys.exit(f"usage: {sys.argv[0]} <port> <baud> <command...>")
    device, baud, command = sys.argv[1], int(sys.argv[2]), " ".join(sys.argv[3:])

    with board.open_port(device, baud) as port:
        board.restart(port)
        # A ping on boot may still be in flight, and its answer is worth seeing.
        print(board.send(port, command, settle_s=6.0))


if __name__ == "__main__":
    main()
