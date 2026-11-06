#!/usr/bin/env python3
"""eth_test.py -- end-to-end Ethernet test: MEGA65 MAC <-> eth_vphy <-> rk_bridge <-> Rockchip
(m65io L2 bridge on eth0) <-> LAN <-> this x86 host.  Uses only the serial monitor (TCP 4510)
and ordinary UDP sockets on the host (no root needed).

  TX: the MEGA65 sends a UDP broadcast (port 6565) built byte-by-byte in its TX buffer
      ($FFDE800, length $D6E2/3, $D6E4=1); this host must receive it.
  RX: this host broadcasts UDP to port 6566; the frame must appear in the MEGA65's RX buffer
      ($D6E1.5 = frame received; a 0->1 EDGE on $D6E1.1 releases the shown buffer and
      shows the next frame at $FFDE800: 2-byte header, then the frame).
"""
import os, re, socket, struct, sys, time

HOST = os.environ.get("M65_HOST", "192.168.1.143")
MAC = bytes.fromhex("02654D454741")           # locally administered: 02:65:'M''E''G''A'
M65_IP = "192.168.1.65"                        # source IP we put into the test frame


def mon(cmd, wait=0.4):
    with socket.create_connection((HOST, 4510), timeout=5) as s:
        s.sendall((cmd + "\r").encode())
        s.settimeout(0.1); out, t0 = b"", time.time()
        while time.time() - t0 < wait:
            try:
                out += s.recv(65536)
            except socket.timeout:
                pass
    return out.decode("latin-1")


def poke(addr, data):
    for i in range(0, len(data), 16):
        mon("s%07x %s" % (addr + i, " ".join("%02x" % b for b in data[i:i + 16])), wait=0.25)


def peek(addr, n=16):
    out = b""
    for a in range(addr, addr + n, 16):
        r = mon("m%07x" % a)
        m = re.search(r":0?%07X:([0-9A-F]{32})" % a, r)
        if not m:
            raise RuntimeError("no monitor reply for %07X" % a)
        out += bytes.fromhex(m.group(1))
    return out[:n]


def ip_checksum(h):
    s = sum(struct.unpack("!%dH" % (len(h) // 2), h))
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    return (~s) & 0xFFFF


def udp_broadcast_frame(payload, sport=6565, dport=6565):
    udp = struct.pack("!HHHH", sport, dport, 8 + len(payload), 0) + payload
    ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(udp), 0x6565, 0, 64, 17, 0,
                     socket.inet_aton(M65_IP), socket.inet_aton("255.255.255.255"))
    ip = ip[:10] + struct.pack("!H", ip_checksum(ip)) + ip[12:]
    f = b"\xff" * 6 + MAC + b"\x08\x00" + ip + udp
    return f + b"\x00" * max(0, 60 - len(f))


def next_frame():
    """$D6E1.1 is edge-triggered: write 0 then 1 to advance one frame."""
    mon("sffd36e1 00", wait=0.12)
    mon("sffd36e1 02", wait=0.12)


def main():
    fails = 0
    # MAC up: reset, run, accept broadcasts, set our MAC address
    mon("sffd36e0 00"); mon("sffd36e0 03")
    mon("sffd36e5 10")
    poke(0xFFD36E9, MAC)

    # ---------------- TX: MEGA65 -> LAN -> this host ----------------
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    rx.bind(("", 6565)); rx.settimeout(3)
    payload = b"HELLO FROM MEGA65 %d" % int(time.time())
    frame = udp_broadcast_frame(payload)
    poke(0xFFDE800, frame)
    mon("sffd36e2 %02x %02x" % (len(frame) & 0xFF, len(frame) >> 8))
    mon("sffd36e4 01")                              # STARTTX
    try:
        data, src = rx.recvfrom(2048)
        ok = data == payload
        print("TX: host received %r from %s  %s" % (data, src, "ok" if ok else "MISMATCH"))
        fails += not ok
    except socket.timeout:
        print("TX: host received nothing  FAIL"); fails += 1

    # ---------------- RX: this host -> LAN -> MEGA65 ----------------
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    tx.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    marker = b"HELLO MEGA65 FROM HOST"
    found = False
    for attempt in range(3):
        # LAN broadcasts (ARP etc.) keep filling the MAC's 4 RX buffers, and it drops
        # frames while they are all full ($D6E0.6 RXBLKD).  Release them right before
        # sending: each $D6E1.1 write frees the shown buffer and shows the next frame.
        for _ in range(5):
            next_frame()
        tx.sendto(marker, ("192.168.1.255", 6566))
        time.sleep(0.3)
        for _ in range(4):                          # walk the received frames
            next_frame()                            # show next received frame
            buf = peek(0xFFDE800, 96)
            if marker in buf:
                ln = buf[0] | buf[1] << 8
                print("RX: MEGA65 received the host's broadcast (frame length field %d)  ok" % ln)
                found = True
                break
        if found:
            break
    if not found:
        print("RX: host broadcast not seen in the MEGA65 RX buffers  FAIL"); fails += 1
    print("ETH PASS" if not fails else "ETH FAIL (%d)" % fails)
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
