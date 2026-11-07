#!/usr/bin/env python3
"""plugd.py -- host side of the rig's power-off: cut the smart plug once the Rockchip has halted.

Runs on the PC that controls the Matter plug (systemd --user service, scripts/plugd.service).
Ctrl+Alt+End on the rig (m65d `poweroff`) sends "poweroff" over TCP; the Rockchip then shuts
down, and its last shutdown hook (m65-safe.shutdown, run by systemd-shutdown once every file
system is unmounted or read-only) sends UDP "halted".  The plug is cut

  * 2 s after "halted", or
  * without "halted": once the Rockchip's kernel has stopped answering at all for SILENCE s.
    (A TCP connect to port 22 is answered -- accepted or reset -- until the kernel halts, even
    after sshd has stopped, so silence here means halted, not just "sshd gone".)

If the Rockchip still answers after LIMIT s, the plug is NOT cut (shutdown hung: eMMC risk).
Only requests from the Rockchip's address are accepted.

    scripts/plugd.py                 (ROCK_IP, PLUGD_PORT, PLUG override the defaults)
    echo status | nc HOST 4530       last result
"""
import os
import select
import socket
import subprocess
import sys
import time

ROCK_IP = os.environ.get("ROCK_IP", "192.168.1.143")
PORT = int(os.environ.get("PLUGD_PORT", "4530"))
PLUG = os.environ.get("PLUG", "/home/me/matter-plug/plug")
SILENCE = 15          # s of no answer at all from the Rockchip kernel
LIMIT = 300           # s: still alive after this -> give up, do not cut


def log(msg):
    print(time.strftime("%H:%M:%S ") + msg, flush=True)


def rock_alive():
    """True while the Rockchip's kernel answers a TCP connect (accept OR reset)."""
    s = socket.socket()
    s.settimeout(2)
    try:
        s.connect((ROCK_IP, 22))
        return True
    except (ConnectionRefusedError, ConnectionResetError):
        return True                                   # RST: kernel still running
    except OSError:
        return False                                  # timeout / unreachable
    finally:
        s.close()


def plug_off():
    r = subprocess.run([PLUG, "off"], capture_output=True, text=True)
    log("plug off: %s" % (r.stdout.strip() or r.stderr.strip() or r.returncode))
    return r.returncode == 0


def watch(udp):
    """after a poweroff request: wait for 'halted' or sustained silence, then cut"""
    t0 = time.time()
    silent_since = None
    while time.time() - t0 < LIMIT:
        r, _, _ = select.select([udp], [], [], 1.0)
        if r:
            data, addr = udp.recvfrom(64)
            if addr[0] == ROCK_IP and data.strip() == b"halted":
                log("Rockchip reports halted after %.0f s" % (time.time() - t0))
                time.sleep(2)
                plug_off()
                return "cut after 'halted' (%.0f s)" % (time.time() - t0)
        if rock_alive():
            silent_since = None
        else:
            silent_since = silent_since or time.time()
            if time.time() - silent_since >= SILENCE:
                log("Rockchip silent for %d s (no 'halted' received)" % SILENCE)
                plug_off()
                return "cut after silence (%.0f s, no 'halted')" % (time.time() - t0)
    log("Rockchip still answering after %d s -- NOT cutting power" % LIMIT)
    return "not cut: Rockchip still alive after %d s" % LIMIT


def main():
    tcp = socket.socket()
    tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    tcp.bind(("0.0.0.0", PORT))
    tcp.listen(2)
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.bind(("0.0.0.0", PORT))
    log("plugd: listening on %d (Rockchip %s, plug %s)" % (PORT, ROCK_IP, PLUG))
    last = "none"
    while True:
        r, _, _ = select.select([tcp, udp], [], [])
        if udp in r:
            udp.recvfrom(64)                         # stray 'halted' without a request
        if tcp not in r:
            continue
        c, addr = tcp.accept()
        with c:
            c.settimeout(3)
            try:
                req = c.recv(64).decode(errors="replace").strip()
            except OSError:
                continue
            if req == "status":
                c.sendall(("ok %s\n" % last).encode())
                continue
            if addr[0] != ROCK_IP or req != "poweroff":
                c.sendall(b"err\n")
                log("refused %r from %s" % (req, addr[0]))
                continue
            c.sendall(b"ok\n")
        log("poweroff requested by the Rockchip")
        last = watch(udp)
        log(last)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
