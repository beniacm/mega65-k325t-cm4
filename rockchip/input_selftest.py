#!/usr/bin/env python3
"""input_selftest.py -- functional tests of m65d's USB input handling with virtual devices
(/dev/uinput).  Run as root on the Rockchip while m65d runs; leaves no devices behind.
    combo      one node with keyboard keys AND a mouse: both must work
    dropped    evdev overrun while m65d is stopped (SIGSTOP): state must be re-read, no stuck key
    ports      pad 1 -> port 2, pad 2 -> port 1; unplug pad 1: pad 2 stays on port 1; Ctrl+Alt+J swaps
    mouse      a fast fling: the POT value never jumps more than 31 counts per 20 ms
    plugheld   a key held while the keyboard is plugged in is seen at once
Prints PASS/FAIL per test; exit status 1 if any failed."""
import fcntl, os, signal, socket, subprocess, sys, time
import uinput_test as U

KEY_A, KEY_B, KEY_LEFTCTRL, KEY_LEFTALT, KEY_J = 30, 48, 29, 56, 36
from m65d import KEYPOS
POS = {k: KEYPOS[k] for k in "AB"}         # MEGA65 matrix (column, row) of A and B


def ctl(cmd):
    with socket.create_connection(("127.0.0.1", 4511), timeout=3) as s:
        s.sendall((cmd + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            buf += s.recv(65536)
    return buf.decode().strip()


def matrix():
    cols = [int(v, 16) for v in ctl("keylog").split("matrix")[1].split()]
    return {k for k, (c, r) in POS.items() if cols[c] >> r & 1}


def pots():
    return [int(v, 16) for v in ctl("mouse 0 0").split("pots")[1].split()]


def wait_inputs(sub, present=True, timeout=3.0):
    t0 = time.monotonic()
    while (sub in ctl("status")) != present:
        if time.monotonic() - t0 > timeout:
            return False
        time.sleep(0.01)
    return True


def destroy(fd):
    fcntl.ioctl(fd, U.UI_DEV_DESTROY)
    os.close(fd)


def kbd_keys():
    return [KEY_A, KEY_B, 28, 57, KEY_LEFTCTRL, KEY_LEFTALT, KEY_J] + list(range(1, 100))


def t_combo():
    fd = U.make("m65 st combo", kbd_keys() + [U.BTN_LEFT, U.BTN_RIGHT], rel_axes=(U.REL_X, U.REL_Y))
    try:
        if not wait_inputs("m65_st_combo"):
            return "not picked up"
        roles = ctl("status").split("m65_st_combo")[0].split()[-1]
        p0 = pots()
        U.ev(fd, U.EV_REL, U.REL_X, 20)
        time.sleep(0.05)
        p1 = pots()
        U.ev(fd, U.EV_KEY, KEY_A, 1)
        time.sleep(0.05)
        m = matrix()
        U.ev(fd, U.EV_KEY, KEY_A, 0)
        time.sleep(0.05)
        if "kbd+mouse" not in roles:
            return "classified as %s" % roles
        if p1[2] == p0[2]:
            return "mouse movement ignored"
        if "A" not in m or matrix():
            return "key A not seen / not released"
        return None
    finally:
        destroy(fd)


def t_dropped():
    fd = U.make("m65 st drop", kbd_keys() + [U.BTN_LEFT], rel_axes=(U.REL_X, U.REL_Y))
    pid = int(subprocess.check_output(["systemctl", "show", "-p", "MainPID", "--value", "m65d"]))
    try:
        if not wait_inputs("m65_st_drop"):
            return "not picked up"
        before = int(ctl("latency").rsplit("syn_dropped=", 1)[1])
        os.kill(pid, signal.SIGSTOP)
        try:
            U.ev(fd, U.EV_KEY, KEY_A, 1)
            for i in range(600):                 # overrun the evdev client buffer
                U.ev(fd, U.EV_REL, U.REL_X, 1)
            U.ev(fd, U.EV_KEY, KEY_A, 0)         # A released inside the lost part ...
            for i in range(600):
                U.ev(fd, U.EV_REL, U.REL_X, 1)
            U.ev(fd, U.EV_KEY, KEY_B, 1)         # ... B pressed in it and still held
            for i in range(600):
                U.ev(fd, U.EV_REL, U.REL_X, 1)
        finally:
            os.kill(pid, signal.SIGCONT)
        time.sleep(0.2)
        m = matrix()
        after = int(ctl("latency").rsplit("syn_dropped=", 1)[1])
        U.ev(fd, U.EV_KEY, KEY_B, 0)
        time.sleep(0.05)
        if after == before:
            return "no SYN_DROPPED seen (buffer not overrun?) matrix %s" % m
        if m != {"B"}:
            return "after resync expected only B down, got %s" % (m or "nothing")
        if matrix():
            return "B stuck after release"
        return None
    finally:
        destroy(fd)


def port_of(name):
    st = ctl("status")
    for tok in st.split():
        if name in tok:
            return int(tok.split("@port")[1]) if "@port" in tok else None
    return None


def t_ports():
    p1 = U.make("m65 st padA", [0x130, 0x131], abs_axes=(U.ABS_HAT0X, U.ABS_HAT0Y))
    if not wait_inputs("m65_st_padA"):
        destroy(p1)
        return "pad A not picked up"
    p2 = U.make("m65 st padB", [0x130, 0x131], abs_axes=(U.ABS_HAT0X, U.ABS_HAT0Y))
    kb = U.make("m65 st swapkbd", kbd_keys())
    try:
        if not wait_inputs("m65_st_padB") or not wait_inputs("m65_st_swapkbd"):
            return "pad B / keyboard not picked up"
        a, b = port_of("m65_st_padA"), port_of("m65_st_padB")
        if (a, b) != (2, 1):
            return "ports A=%s B=%s, expected 2, 1" % (a, b)
        U.ev(p2, U.EV_KEY, 0x130, 1)             # pad B fire -> port 1 (ja)
        time.sleep(0.05)
        destroy(p1)
        p1 = None
        if not wait_inputs("m65_st_padA", present=False):
            return "pad A not dropped"
        if port_of("m65_st_padB") != 1:
            return "pad B moved to port %s after pad A was unplugged" % port_of("m65_st_padB")
        U.ev(p2, U.EV_KEY, 0x130, 0)
        for k, v in ((KEY_LEFTCTRL, 1), (KEY_LEFTALT, 1), (KEY_J, 1), (KEY_J, 0), (KEY_LEFTALT, 0),
                     (KEY_LEFTCTRL, 0)):
            U.ev(kb, U.EV_KEY, k, v)
            time.sleep(0.03)
        r = ctl("joyswap")                       # reports the new state after toggling back
        if "normal" not in r:
            return "Ctrl+Alt+J did not swap (joyswap now: %s)" % r
        return None
    finally:
        for fd in (p1, p2, kb):
            if fd is not None:
                destroy(fd)


def t_mouse():
    fd = U.make("m65 st fling", [U.BTN_LEFT, U.BTN_RIGHT], rel_axes=(U.REL_X, U.REL_Y))
    try:
        if not wait_inputs("m65_st_fling"):
            return "not picked up"
        samples = []
        t0 = time.monotonic()
        nxt = t0
        while time.monotonic() - t0 < 0.6:      # 12000 PC counts/s for 0.3 s, then stop
            if time.monotonic() - t0 < 0.3:
                U.ev(fd, U.EV_REL, U.REL_X, 12)
            samples.append((time.monotonic(), pots()[2] >> 1))
            nxt += 0.001
            time.sleep(max(0, nxt - time.monotonic()))
        worst = 0
        for i, (t, v) in enumerate(samples):
            for t2, v2 in samples[i + 1:]:
                if t2 - t > 0.020:
                    break
                d = (v2 - v) % 64
                worst = max(worst, min(d, 64 - d) if d > 31 else d)
        moved = len({v for _, v in samples})
        if moved < 3:
            return "POT value hardly moved"
        if worst > 31:
            return "POT jumped %d counts within 20 ms" % worst
        return None
    finally:
        destroy(fd)


def t_plugheld():
    fd = U.make("m65 st held", kbd_keys())
    try:
        U.ev(fd, U.EV_KEY, KEY_B, 1)            # before m65d has seen the device
        if not wait_inputs("m65_st_held"):
            return "not picked up"
        time.sleep(0.05)
        m = matrix()
        U.ev(fd, U.EV_KEY, KEY_B, 0)
        time.sleep(0.05)
        if "B" not in m:
            return "key held at plug-in not seen"
        if matrix():
            return "B stuck"
        return None
    finally:
        destroy(fd)


TESTS = {"combo": t_combo, "dropped": t_dropped, "ports": t_ports, "mouse": t_mouse,
         "plugheld": t_plugheld}


def main():
    names = sys.argv[1:] or list(TESTS)
    bad = 0
    for n in names:
        time.sleep(0.3)
        try:
            err = TESTS[n]()
        except Exception as e:
            err = "exception %r" % e
        print("%-9s %s" % (n, "PASS" if err is None else "FAIL: " + err), flush=True)
        bad += err is not None
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
