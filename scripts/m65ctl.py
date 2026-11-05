#!/usr/bin/env python3
"""m65ctl.py — send control commands to m65d on the Rockchip (TCP 4511).
   scripts/m65ctl.py status
   scripts/m65ctl.py type 'PRINT 6*7\n'
   scripts/m65ctl.py key RETURN
   scripts/m65ctl.py mon 'h'        # raw serial-monitor command, prints the reply
Host via $M65_HOST (default 192.168.1.143)."""
import os, socket, sys, time
HOST = os.environ.get("M65_HOST", "192.168.1.143")

def ctl(line):
    with socket.create_connection((HOST, 4511), timeout=10) as s:
        s.sendall((line + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            d = s.recv(65536)
            if not d:
                break
            buf += d
        return buf.decode().rstrip("\n")

def mon(cmd, wait=1.0):
    with socket.create_connection((HOST, 4510), timeout=10) as s:
        s.sendall((cmd + "\r").encode())
        s.settimeout(0.2)
        out, t0 = b"", time.time()
        while time.time() - t0 < wait:
            try:
                d = s.recv(65536)
                if d:
                    out += d
            except socket.timeout:
                pass
        return out.decode("latin-1")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    if sys.argv[1] == "mon":
        print(mon(" ".join(sys.argv[2:])))
    else:
        print(ctl(" ".join(sys.argv[1:])))
