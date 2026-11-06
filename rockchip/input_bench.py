#!/usr/bin/env python3
"""input_bench.py -- measure the Rockchip input path with virtual devices (/dev/uinput).
Run as root on the Rockchip while m65d is running:
    input_bench.py [mouse|kbd|pad] [events] [rate_hz]
Creates the device, measures how long m65d takes to pick it up (hot-plug), resets m65d's
latency statistics, sends <events> changes at <rate_hz>, prints m65d's 'latency' line.
The latency is USB event (kernel timestamp) -> INPUT register written over SPI; add the
device's USB polling interval for the whole delay to the FPGA."""
import socket, sys, time
import uinput_test as U


def ctl(cmd):
    with socket.create_connection(("127.0.0.1", 4511), timeout=3) as s:
        s.sendall((cmd + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            buf += s.recv(4096)
    return buf.decode().strip()


def main():
    kind = sys.argv[1] if len(sys.argv) > 1 else "mouse"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 1000
    rate = float(sys.argv[3]) if len(sys.argv) > 3 else 250.0
    name = "m65 bench " + kind
    if kind == "mouse":
        fd = U.make(name, [U.BTN_LEFT, U.BTN_RIGHT], rel_axes=(U.REL_X, U.REL_Y))
    elif kind == "kbd":
        fd = U.make(name, list(range(1, 128)))
    else:
        fd = U.make(name, [0x130, 0x131, 0x13a, 0x13b], abs_axes=(U.ABS_HAT0X, U.ABS_HAT0Y))
    t0 = time.monotonic()
    while "m65_bench" not in ctl("status"):
        if time.monotonic() - t0 > 10:
            sys.exit("m65d did not pick up the device")
        time.sleep(0.01)
    print("hot-plug: picked up after %.0f ms" % ((time.monotonic() - t0) * 1000))
    time.sleep(0.2)
    ctl("latency reset")
    period = 1.0 / rate
    nxt = time.monotonic()
    for i in range(n):
        if kind == "mouse":
            U.ev(fd, U.EV_REL, U.REL_X, 4 if i & 1 else -4)     # +-2 1351 counts: pots change
        elif kind == "kbd":
            U.ev(fd, U.EV_KEY, 30, 1 - (i & 1))                  # A down / up
        else:
            U.ev(fd, U.EV_ABS, U.ABS_HAT0X, 0 if i & 1 else 1)
        nxt += period
        d = nxt - time.monotonic()
        if d > 0:
            time.sleep(d)
    time.sleep(0.2)
    print(ctl("latency"))
    import fcntl
    fcntl.ioctl(fd, U.UI_DEV_DESTROY)


if __name__ == "__main__":
    main()
