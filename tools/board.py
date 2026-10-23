"""Talking to the gateway over its serial console."""

import sys
import time

try:
    import serial
except ImportError:
    sys.exit("pyserial is missing: python3 -m pip install --user pyserial")

BOOT_TIMEOUT_S = 10
READY_MARKER = "ble: scanning"


def open_port(device: str, baud: int) -> serial.Serial:
    return serial.Serial(device, baud, timeout=0.3)


def restart(port: serial.Serial) -> None:
    """Reset the board and wait for it, so no command lands while the ROM has the line."""
    port.dtr = False
    port.rts = True
    time.sleep(0.15)
    port.rts = False

    transcript = ""
    deadline = time.time() + BOOT_TIMEOUT_S
    while time.time() < deadline:
        transcript += port.read(4096).decode("utf-8", "replace")
        if READY_MARKER in transcript:
            return
    sys.exit(f"the board never reached '{READY_MARKER}' — is this the right port?")


def send(port: serial.Serial, line: str, settle_s: float = 0.4) -> str:
    port.write(f"{line}\n".encode())
    port.flush()
    time.sleep(settle_s)
    return port.read(8192).decode("utf-8", "replace").strip()
