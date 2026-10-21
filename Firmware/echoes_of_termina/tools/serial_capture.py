"""Capture raw serial output from the Echoes board for a bounded window.

Usage:  python tools/serial_capture.py [port] [seconds] [baud]

Writes every received byte straight to stdout (unbuffered) so the output can be
redirected to a log file. Read-only: it never writes to the port, so it is safe
to attach while a show is running.
"""
import sys
import time

import serial

port = sys.argv[1] if len(sys.argv) > 1 else "COM7"
seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 20.0
baud = int(sys.argv[3]) if len(sys.argv) > 3 else 115200

# dsrdtr/rtscts off so opening the port does not reset or handshake the board.
with serial.Serial(port, baud, timeout=0.2, dsrdtr=False, rtscts=False) as ser:
    ser.reset_input_buffer()
    out = sys.stdout
    deadline = time.time() + seconds
    while time.time() < deadline:
        chunk = ser.read(4096)
        if chunk:
            out.write(chunk.decode("utf-8", "replace"))
            out.flush()
