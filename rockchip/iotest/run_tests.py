#!/usr/bin/env python3
"""run_tests.py -- PC tests of m65io's SD write guard against a modelled bridge (fakespi.c).

m65io (rockchip/m65io.c, built unchanged) runs with -d /dev/null; the LD_PRELOAD shim fakespi.so
answers its spidev ioctls with a model of rk_bridge + sd_emu and plays a scenario: a core that
initialises the card, reads, writes (single blocks and CMD25 chains), gives up on writes, and a
link that is unconfigured (JTAG load) or garbles STATUS frames (all zeros/ones, random, shifted,
late-sampled bits, frames that pass every bit check with a wrong write flag/lba/seq).  The shim
checks every pwrite() of m65io against the block the model has really captured and compares the
whole image at the end.

    rockchip/iotest/run_tests.py [scenario ...] [--old OLD_m65io.c] [--keep DIR] [--seed N]

--old builds a second m65io from OLD_m65io.c (e.g. git show c744556:rockchip/m65io.c) and runs
the same scenarios with it, to show which faults the guard is what stops (no pass/fail for it).
Needs gcc; about 2 minutes per binary.
"""
import argparse
import os
import random
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "m65io.c")
SCENARIOS = ["good", "spurious", "garbage", "incident", "probe_write", "protect", "abandon",
             "datamis", "usr1", "perf"]
HOLD_S = 2.0
NSECT = 32768                       # 16 MB test image


def make_image(path, seed):
    """MBR (FAT32 at 2048 + a MEGA65 system partition, like the rig's card), a FAT32 boot sector
    with its signature at 2048, random data elsewhere"""
    rnd = random.Random(seed)
    img = bytearray(rnd.getrandbits(8) for _ in range(NSECT * 512))
    mbr = bytearray(512)
    mbr[440:444] = bytes.fromhex("837dcba6")

    def entry(i, ptype, start, size):
        e = 446 + 16 * i
        mbr[e + 4] = ptype
        mbr[e + 8:e + 12] = start.to_bytes(4, "little")
        mbr[e + 12:e + 16] = size.to_bytes(4, "little")
    entry(0, 0x0C, 2048, 20480)
    entry(1, 0x41, 22528, 8192)
    mbr[510:512] = b"\x55\xaa"
    img[0:512] = mbr
    vbr = bytearray(img[2048 * 512:2049 * 512])
    vbr[0:3] = b"\xeb\x58\x90"
    vbr[3:11] = b"MEGA65  "
    vbr[510:512] = b"\x55\xaa"
    img[2048 * 512:2049 * 512] = vbr
    with open(path, "wb") as f:
        f.write(img)


def build(src, out, shim_out=None):
    subprocess.run(["gcc", "-O2", "-Wall", "-o", out, src], check=True)
    if shim_out:
        subprocess.run(["gcc", "-O2", "-Wall", "-shared", "-fPIC", "-o", shim_out,
                        os.path.join(HERE, "fakespi.c"), "-ldl"], check=True)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def parse_kv(line):
    d = {}
    for f in line.split():
        if "=" in f:
            k, v = f.split("=", 1)
            try:
                d[k] = float(v) if "." in v else int(v)
            except ValueError:
                d[k] = v
    return d


def run(binary, shim, scen, workdir, base_img, seed, hz=50000000):
    d = os.path.join(workdir, scen)
    os.makedirs(d, exist_ok=True)
    img = os.path.join(d, "card.img")
    shutil.copyfile(base_img, img)
    evlog, outlog, stats = (os.path.join(d, n) for n in ("events.txt", "m65io.log", "m65io.stats"))
    env = dict(os.environ, LD_PRELOAD=shim, FAKESPI_LOG=evlog, FAKESPI_SCEN=scen,
               FAKESPI_IMG=img, FAKESPI_SEED=str(seed))
    with open(outlog, "w") as out:
        p = subprocess.Popen([binary, "-d", "/dev/null", "-s", str(hz), "-i", img, "-p", str(free_port()),
                              "-S", stats, "-e", "none"], stdout=out, stderr=subprocess.STDOUT, env=env)
        try:
            p.wait(timeout=150)
        except subprocess.TimeoutExpired:
            p.send_signal(signal.SIGKILL)
            p.wait()
    ev = open(evlog).read().splitlines() if os.path.exists(evlog) else []
    log = open(outlog).read()
    st = parse_kv(open(stats).read()) if os.path.exists(stats) else {}
    end = next((parse_kv(l) for l in ev if " END " in l), None)
    done = next((l.split("SCEN_DONE ", 1)[1] for l in ev if "SCEN_DONE" in l), "none")
    return dict(ev=ev, log=log, st=st, end=end or {}, done=done, dir=d)


def check(scen, r):
    """list of failed expectations for the guarded m65io"""
    e, st, ev, fails = r["end"], r["st"], r["ev"], []

    def need(cond, what):
        if not cond:
            fails.append(what)
    need(e, "the run did not end normally (no END line)")
    if not e:
        return fails
    need(e.get("pwrite_bad", 1) == 0, "pwrite of a block the model did not capture (%s)" % e.get("pwrite_bad"))
    need(e.get("image_bad_sectors", 1) == 0, "image differs from the expected one in %s sectors (first %s)"
         % (e.get("image_bad_sectors"), e.get("first_bad")))
    need(e.get("reads_bad", 1) == 0, "wrong data for %s reads" % e.get("reads_bad"))
    need(not any("PREF_BAD" in l for l in ev), "prefetch data does not match its lba")
    need("TIMEOUT" not in r["done"], "scenario timed out: " + r["done"])
    if scen == "good":
        need(r["done"] == "workload complete", "workload not complete")
        need(st.get("wr_refused", -1) == 0, "refused writes in normal use: %s" % st.get("wr_refused"))
        need(e.get("acks_ignored", 1) == 0, "acks without a pending write: %s" % e.get("acks_ignored"))
        need(st.get("wr", 0) >= 40, "too few writes served: %s" % st.get("wr"))
    if scen == "spurious":
        need(r["done"] == "workload complete", "workload not complete")
        need(e.get("sane_garbled", 0) > 0, "no garbled frames that pass the bit checks were injected")
        need(st.get("wr_refused", 0) > 0, "no refused writes counted")
    if scen == "garbage":
        need(r["done"] == "observation time over", "the core's last request was not served")
        need(e.get("garbled", 0) >= 10, "too few garbled frames injected: %s" % e.get("garbled"))
        need(st.get("wr", 0) >= 10, "too few writes served under garbled frames: %s" % st.get("wr"))
    if scen == "incident":
        need(e.get("pwrite_ok", 1) == 0 and e.get("pwrite_bad", 1) == 0, "a write reached pwrite")
        need(e.get("sane_garbled", 0) + e.get("garbled", 0) > 0, "no garbled frames injected")
    if scen == "probe_write":
        need(r["done"] == "write served", "the write was not served")
        need(e.get("pwrite_ok") == 1, "pwrite count %s (want 1)" % e.get("pwrite_ok"))
        # the hold counts from the last frame m65io can recognise as bad (garbled frames that pass
        # every bit check are caught by the confirmation reads instead)
        fp = next((parse_kv(l) for l in ev if "FIRST_PWRITE" in l), {})
        t_pw = float(next((l.split()[0] for l in ev if "FIRST_PWRITE" in l), "0"))
        gap = t_pw - fp.get("last_detect_t", 0)
        need(gap >= HOLD_S - 0.05, "write served %.3f s after the last detectable bad frame (hold %.1f s)"
             % (gap, HOLD_S))
    if scen == "protect":
        need(r["done"] == "sequence complete", "valid writes after the refused ones were not served")
        need(st.get("wr_implaus", 0) == 2, "implausible writes counted %s (want 2)" % st.get("wr_implaus"))
        need("not acked and the core moved on" in r["log"], "the stuck request was not detected")
    if scen == "abandon":
        need(r["done"] == "retry served", "the retried write was not served")
        pws = [l for l in ev if " PWRITE lba=" in l]
        need(len(pws) == 1 and "lba=500 " in pws[0], "pwrites: %s" % pws)
        need("not acked and the core moved on" in r["log"], "the stuck request was not detected")
    if scen == "datamis":
        need(r["done"] == "write served", "write not served")
        need(st.get("wr_dmis", 0) >= 1, "data mismatch not counted")
    if scen == "usr1":
        t_conf = float(next((l.split()[0] for l in ev if "CONFIGURED" in l), "1e9"))
        inv_after = [l for l in ev if l.split()[1:2] == ["INVAL"] and float(l.split()[0]) > t_conf]
        need(len(inv_after) >= 1, "no SD_INVAL reached the reconfigured bridge")
        need(any("INVAL lost" in l for l in ev) and st.get("inval_retry", 0) >= 1,
             "a lost SD_INVAL was not sent again")
    if scen == "perf":
        n = e.get("reads_issued", 0)
        polls = e.get("rd_pending_polls", 0)
        need(n >= 2000, "reads issued %s" % n)
        need(n and polls / n <= 1.02, "status polls per demand read %.3f (old m65io: 1.0)" % (polls / max(n, 1)))
    return fails


def summary(r):
    e, st = r["end"], r["st"]
    keys_e = ["status", "garbled", "pass_check", "sane_garbled", "pwrite_ok", "pwrite_bad", "acks", "reads_ok",
              "image_bad_sectors"]
    keys_s = ["wr", "wr_refused", "wr_wait", "wr_unconf", "wr_dmis", "wr_implaus", "wr_stale", "rd_unconf",
              "st_insane", "spi_hz"]
    return (" ".join("%s=%s" % (k, e.get(k)) for k in keys_e) + " | " +
            " ".join("%s=%s" % (k, st.get(k)) for k in keys_s) + " | " + r["done"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scen", nargs="*", default=SCENARIOS)
    ap.add_argument("--old", help="also run an old m65io.c (expected to fail some)")
    ap.add_argument("--keep", help="work directory (kept); default: a temporary one")
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    wd = a.keep or tempfile.mkdtemp(prefix="m65io_test_")
    os.makedirs(wd, exist_ok=True)
    shim = os.path.join(wd, "fakespi.so")
    new = os.path.join(wd, "m65io_new")
    build(SRC, new, shim)
    base = os.path.join(wd, "base.img")
    make_image(base, a.seed)
    bins = [("new", new)]
    if a.old:
        old = os.path.join(wd, "m65io_old")
        build(a.old, old)
        bins.append(("old", old))
    bad = 0
    for name, b in bins:
        for s in a.scen:
            t = time.time()
            r = run(b, shim, s, os.path.join(wd, name), base, a.seed)
            f = check(s, r)
            verdict = ("PASS" if not f else "FAIL") if name == "new" else ("ok  " if not f else "BAD ")
            print("%s %-4s %-12s %5.1fs %s" % (verdict, name, s, time.time() - t, summary(r)), flush=True)
            for x in f:
                print("      - " + x)
            if name == "new" and f:
                bad += 1
    print("%d scenario(s) failed for the guarded m65io; work dir %s" % (bad, wd))
    if not a.keep and not bad:
        shutil.rmtree(wd)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
