#!/usr/bin/env python3
"""m65_bootreset.py -- run once at boot by m65-bootreset.service: at power-on the FPGA boots (~1 s)
long before m65io serves the SD card, so the MEGA65's HYPPO finds no card and stops
("SD CARD IS NOT SDHC").  Reset the core once, now that the card is served."""
import socket
import time

time.sleep(2)
for attempt in range(10):
    try:
        with socket.create_connection(("127.0.0.1", 4511), timeout=5) as s:
            s.sendall(b"reset\n")
            print("m65_bootreset:", s.recv(64).decode().strip())
            break
    except OSError:
        time.sleep(1)
