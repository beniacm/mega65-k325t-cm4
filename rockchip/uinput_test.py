#!/usr/bin/env python3
"""uinput_test.py -- virtual USB keyboard + gamepad (via /dev/uinput) to test the m65input
bridge end-to-end without hardware.  Run as root on the Rockchip while m65d is running:
    uinput_test.py type 'print 6*7\\n'     type a line on the virtual keyboard
    uinput_test.py fire [seconds]          hold gamepad A (fire) on the virtual pad
    uinput_test.py dpad up|down|left|right [seconds]
    uinput_test.py keys 29,56,88            hold a key combination (here Ctrl+Alt+F12)
    uinput_test.py mouse <dx> <dy> [steps] [left|right]   move a virtual mouse (PC counts,
                                           dy > 0 = down), optionally click at the end
The devices appear, m65d picks them up within ~2 s (hot-plug scan), then events flow."""
import fcntl, os, struct, sys, time

UI_SET_EVBIT, UI_SET_KEYBIT, UI_SET_ABSBIT = 0x40045564, 0x40045565, 0x40045567
UI_SET_RELBIT = 0x40045566
EV_REL, REL_X, REL_Y = 2, 0, 1
BTN_LEFT, BTN_RIGHT = 0x110, 0x111
UI_DEV_CREATE, UI_DEV_DESTROY = 0x5501, 0x5502
EV_SYN, EV_KEY, EV_ABS = 0, 1, 3
ABS_HAT0X, ABS_HAT0Y = 0x10, 0x11
CHARS = {c: 2 + i for i, c in enumerate("1234567890")}
CHARS.update(zip("qwertyuiop", range(16, 26)))
CHARS.update(zip("asdfghjkl", range(30, 39)))
CHARS.update(zip("zxcvbnm", range(44, 51)))
CHARS.update({" ": 57, "\n": 28, ",": 51, ".": 52, "/": 53, "-": 12})
SHIFTED = {"*": 9, '"': 3, "!": 2, "(": 9, ")": 10}      # C64 positional: SHIFT+8 = (, SHIFT+2 = "
KP_ASTERISK = 55


def make(name, keys, abs_axes=(), rel_axes=()):
    fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
    fcntl.ioctl(fd, UI_SET_EVBIT, EV_KEY)
    for k in keys:
        fcntl.ioctl(fd, UI_SET_KEYBIT, k)
    if rel_axes:
        fcntl.ioctl(fd, UI_SET_EVBIT, EV_REL)
        for r in rel_axes:
            fcntl.ioctl(fd, UI_SET_RELBIT, r)
    absmin, absmax = [0] * 64, [0] * 64
    if abs_axes:
        fcntl.ioctl(fd, UI_SET_EVBIT, EV_ABS)
        for a in abs_axes:
            fcntl.ioctl(fd, UI_SET_ABSBIT, a)
            absmin[a], absmax[a] = -1, 1
    dev = struct.pack("80sHHHHi", name.encode(), 3, 0x1234, 0x5678, 1, 0)
    dev += struct.pack("64i", *absmax) + struct.pack("64i", *absmin) + struct.pack("128i", *([0] * 128))
    os.write(fd, dev)
    fcntl.ioctl(fd, UI_DEV_CREATE)
    return fd


def ev(fd, typ, code, val):
    os.write(fd, struct.pack("llHHi", 0, 0, typ, code, val))
    os.write(fd, struct.pack("llHHi", 0, 0, EV_SYN, 0, 0))


def tap(fd, code, shift=False):
    if shift:
        ev(fd, EV_KEY, 42, 1); time.sleep(0.04)
    ev(fd, EV_KEY, code, 1); time.sleep(0.07)
    ev(fd, EV_KEY, code, 0); time.sleep(0.04)
    if shift:
        ev(fd, EV_KEY, 42, 0); time.sleep(0.04)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "type"
    if cmd == "type":
        kbd = make("m65 virtual keyboard", list(range(1, 128)))
        time.sleep(3)                                  # let m65d's hot-plug scan find it
        for ch in sys.argv[2].replace("\\n", "\n"):
            if ch == "*":
                tap(kbd, KP_ASTERISK)
            elif ch in SHIFTED:
                tap(kbd, SHIFTED[ch], shift=True)
            else:
                tap(kbd, CHARS[ch])
        time.sleep(0.5)
        fcntl.ioctl(kbd, UI_DEV_DESTROY)
    elif cmd == "keys":
        # keys <code,code,...> [hold s]: press in order, hold, release in reverse (combos)
        kbd = make("m65 virtual keyboard", list(range(1, 128)))
        time.sleep(3)
        codes = [int(c) for c in sys.argv[2].split(",")]
        for c in codes:
            ev(kbd, EV_KEY, c, 1); time.sleep(0.05)
        time.sleep(float(sys.argv[3]) if len(sys.argv) > 3 else 0.2)
        for c in reversed(codes):
            ev(kbd, EV_KEY, c, 0); time.sleep(0.05)
        time.sleep(0.5)
        fcntl.ioctl(kbd, UI_DEV_DESTROY)
    elif cmd == "mouse":
        m = make("m65 virtual mouse", [BTN_LEFT, BTN_RIGHT], rel_axes=(REL_X, REL_Y))
        time.sleep(3)
        dx, dy = int(sys.argv[2]), int(sys.argv[3])
        steps = int(sys.argv[4]) if len(sys.argv) > 4 else 20
        for i in range(steps):
            ev(m, EV_REL, REL_X, dx // steps)
            ev(m, EV_REL, REL_Y, dy // steps)
            time.sleep(0.02)
        if len(sys.argv) > 5:
            b = BTN_LEFT if sys.argv[5] == "left" else BTN_RIGHT
            time.sleep(0.2)
            ev(m, EV_KEY, b, 1); time.sleep(0.15); ev(m, EV_KEY, b, 0)
        time.sleep(1)
        fcntl.ioctl(m, UI_DEV_DESTROY)
    else:
        pad = make("m65 virtual gamepad", list(range(0x130, 0x13f)), (ABS_HAT0X, ABS_HAT0Y))
        time.sleep(3)
        secs = float(sys.argv[-1]) if sys.argv[-1].replace(".", "").isdigit() else 2.0
        if cmd == "fire":
            ev(pad, EV_KEY, 0x130, 1); time.sleep(secs); ev(pad, EV_KEY, 0x130, 0)
        else:
            axis, val = {"up": (ABS_HAT0Y, -1), "down": (ABS_HAT0Y, 1),
                         "left": (ABS_HAT0X, -1), "right": (ABS_HAT0X, 1)}[sys.argv[2]]
            ev(pad, EV_ABS, axis, val); time.sleep(secs); ev(pad, EV_ABS, axis, 0)
        time.sleep(0.3)
        fcntl.ioctl(pad, UI_DEV_DESTROY)


if __name__ == "__main__":
    main()
