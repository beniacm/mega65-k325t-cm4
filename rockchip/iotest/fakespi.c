/*
 * fakespi.c -- LD_PRELOAD shim for testing m65io on a PC (rockchip/iotest/run_tests.py).
 *
 * m65io runs unchanged with -d /dev/null; this library answers its spidev ioctls with a model of
 * rk_bridge.vhdl (v4) + sd_emu.vhdl: STATUS frames, SD_PUT/GET/WACK/RERR/PREF/INVAL with the
 * same handshake rules as the VHDL (wr_req held until an ack while the card is busy, nothing else
 * clears it; seq + 1 per request; lba/last command/command count follow the core's commands).
 * A scenario (FAKESPI_SCEN) plays a core (card init, reads, writes, CMD25 chains, abandoned
 * writes) and a link (FPGA unconfigured, MISO errors above a rate, garbled frames of several
 * kinds).  Every pwrite() of m65io is checked against the write the model has really captured;
 * at the end the image file is compared with the image the model expects.  Events go to
 * FAKESPI_LOG, one per line; the run ends with "END ..." and _exit(0).
 *
 * Build: gcc -O2 -Wall -shared -fPIC -o fakespi.so fakespi.c -ldl
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <fcntl.h>
#include <linux/spi/spidev.h>
#include <signal.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>

/* ------------------------------------------------------------------ utilities */
static FILE *ev;
static double t0 = -1;
static const char *scen = "";
static uint64_t rs = 88172645463325252ull;

static double mono(void)
{
	struct timespec t;
	clock_gettime(CLOCK_MONOTONIC, &t);
	return t.tv_sec + t.tv_nsec * 1e-9;
}
static double T(void) { return t0 < 0 ? 0 : mono() - t0; }
static uint32_t rnd(void)
{
	rs ^= rs << 13; rs ^= rs >> 7; rs ^= rs << 17;
	return (uint32_t)(rs >> 11);
}
static double frand(void) { return (rnd() & 0xFFFFFF) / 16777216.0; }

static void evf(const char *fmt, ...)
{
	if (!ev)
		return;
	va_list ap;
	fprintf(ev, "%.4f ", T());
	va_start(ap, fmt);
	vfprintf(ev, fmt, ap);
	va_end(ap);
	fputc('\n', ev);
	fflush(ev);
}

/* ------------------------------------------------------------------ the model */
static struct {
	int configured;               /* 0: FPGA unconfigured / configuring, MISO floats */
	int float_kind;               /* 0 ones, 1 random, 2 zeros */
	uint8_t ver, seq, ccnt, lastcmd, inited;
	uint32_t lba;
	int rd_req, rd_ready, rd_error, rd_consume;
	int wr_req, w_busy, ack_cross;   /* wr_req: the flag; w_busy: card busy in M_W_BUSY */
	int fill;                     /* >0: a block is being received (pre-capture), frames left */
	int multi;                    /* CMD25 chain: blocks left after this one */
	uint8_t wbuf[512], wnext[512];
	uint32_t wlba;                /* lba of the captured block (busy) */
	uint16_t vld, hits;
	int inval_drop;               /* ignore this many SD_INVAL (lost) */
	int dmis_once;                /* corrupt the next SD_GET once */
} M;

static uint8_t *img0, *expimg;    /* image at start, image the model expects */
static size_t img_n;
static const char *img_path;

/* counters for the summary */
static unsigned long n_status, n_garbled, n_sane_garbled, n_pw_ok, n_pw_bad, n_ack, n_ack_ign,
	n_rd_ok, n_rd_bad, n_inval, n_put_noreq, n_rd_pending_polls, n_writes_issued, n_reads_issued;
static unsigned long n_kind[16], n_pass_check;
static double last_bad_t = -1;    /* any corrupted STATUS frame */
static double last_detect_t = -1; /* one that fails m65io's bit checks (or MISO floating) */    /* time of the last corrupted STATUS frame */
static double first_pw_t = -1;

static void model_fresh(void)
{
	memset(&M, 0, sizeof M);
	M.configured = 1;
	M.ver = 4;
	memset(M.wbuf, 0, sizeof M.wbuf);    /* BRAM after configuration */
}

/* core actions */
static void core_init(void)
{
	M.ccnt += 4;                  /* CMD0, CMD8, CMD55, ACMD41 */
	M.lastcmd = 41 | 0x80;
	M.inited = 1;
}
static void core_cmd0(void)       /* e.g. HYPPO resets the card after a timeout */
{
	M.ccnt++;
	M.lastcmd = 0;
	M.inited = 0;
	M.w_busy = 0;                 /* the controller gave up waiting; wr_req stays */
	M.fill = 0;
	M.multi = 0;
}
static void core_read(uint32_t lba)
{
	M.ccnt++;
	M.lastcmd = 17;
	M.lba = lba;
	M.seq++;
	M.rd_req = 1;
	M.rd_ready = M.rd_error = 0;
	n_reads_issued++;
}
static void block_data(uint8_t *d, uint32_t lba, int zero)
{
	for (int i = 0; i < 512; i++)
		d[i] = zero ? 0 : (uint8_t)(rnd() ^ lba ^ i);
}
/* CMD24 (blocks == 1) or CMD25 chain; data given or random; the block arrives over `frames`
 * STATUS polls (lastcmd/lba already new, seq old, wbuf being overwritten) */
static void core_write(uint32_t lba, const uint8_t *data, int blocks, int frames)
{
	M.ccnt++;
	M.lastcmd = blocks > 1 ? 25 : 24;
	M.lba = lba;
	M.multi = blocks - 1;
	if (data)
		memcpy(M.wnext, data, 512);
	else
		block_data(M.wnext, lba, 0);
	M.fill = frames > 0 ? frames : 1;
	n_writes_issued++;
}
static void capture(void)
{
	memcpy(M.wbuf, M.wnext, 512);
	M.seq++;
	M.wr_req = 1;
	M.w_busy = 1;
	M.wlba = M.lba;
	M.fill = 0;
	/* the bridge invalidates a cached copy of the lba being written */
}
static int core_idle(void)
{
	return !M.rd_req && !M.w_busy && !M.fill && !M.multi;
}

/* the model's state advances once per STATUS frame (the core is fast compared with a poll) */
static void status_tick(void)
{
	if (M.rd_consume && --M.rd_consume == 0) {
		M.rd_req = M.rd_ready = M.rd_error = 0;
	}
	if (M.ack_cross && --M.ack_cross == 0) {
		M.wr_req = 0;
		M.w_busy = 0;
		if (M.multi > 0) {            /* CMD25: next block, lba + 1 at M_W_REL */
			M.multi--;
			M.lba++;
			block_data(M.wnext, M.lba, 0);
			M.fill = 3;
		}
	}
	if (M.fill > 0) {
		/* the block arrives: wbuf is overwritten from the front */
		int part = 512 / (M.fill + 1);
		memcpy(M.wbuf, M.wnext, part);
		if (--M.fill == 0)
			capture();
	}
}

static void build_status(uint8_t *rx, unsigned len)
{
	uint8_t s[80];
	memset(s, 0, sizeof s);
	s[0] = 0x4D; s[1] = 0x65; s[2] = M.ver;
	s[3] = (M.rd_error << 5) | (M.inited << 3) | (M.wr_req << 2) | (M.rd_ready << 1) | M.rd_req;
	s[4] = M.seq;
	s[5] = M.lba >> 24; s[6] = M.lba >> 16; s[7] = M.lba >> 8; s[8] = M.lba;
	s[11] = 0x04; s[12] = 0x00;      /* tx free 1024 */
	s[13] = M.ccnt; s[14] = M.lastcmd;
	s[18] = 4;
	s[52] = 1;
	s[54] = M.vld >> 8; s[55] = M.vld;
	s[56] = M.hits >> 8; s[57] = M.hits;
	s[58] = 16;
	memset(rx, 0, len);
	for (unsigned i = 2; i < len && i - 2 < sizeof s; i++)
		rx[i] = s[i - 2];
}

/* ------------------------------------------------------------------ corruption */
static int getbit(const uint8_t *b, unsigned i) { return b[i >> 3] >> (7 - (i & 7)) & 1; }
static void setbit(uint8_t *b, unsigned i, int v)
{
	if (v) b[i >> 3] |= 0x80 >> (i & 7); else b[i >> 3] &= ~(0x80 >> (i & 7));
}
enum { K_ZERO, K_ONES, K_RANDOM, K_SHR, K_SHL, K_LATE, K_FLAGBIT, K_WRHDR0, K_WRHDR_RAND, K_LATE_FLAGS,
       K_N };
static const char *const kname[] = { "zero", "ones", "random", "shr", "shl", "late", "flagbit",
				     "wrhdr0", "wrhdr_rand", "late_flags" };

static void corrupt(uint8_t *rx, unsigned len, int kind)
{
	uint8_t in[128];
	unsigned nb = len * 8;
	memcpy(in, rx, len);
	switch (kind) {
	case K_ZERO: memset(rx, 0, len); break;
	case K_ONES: memset(rx, 0xFF, len); break;
	case K_RANDOM: for (unsigned i = 0; i < len; i++) rx[i] = rnd(); break;
	case K_SHR: {                     /* sampled k bits late */
		unsigned k = 1 + rnd() % 7;
		for (unsigned i = 0; i < nb; i++)
			setbit(rx, i, i >= k ? getbit(in, i - k) : 0);
		break;
	}
	case K_SHL: {                     /* k bits early */
		unsigned k = 1 + rnd() % 7;
		for (unsigned i = 0; i < nb; i++)
			setbit(rx, i, i + k < nb ? getbit(in, i + k) : 0);
		break;
	}
	case K_LATE:                      /* marginal: a transition is sometimes sampled late */
	case K_LATE_FLAGS: {
		/* K_LATE_FLAGS: only in the flags byte (magic and version intact) */
		unsigned a = kind == K_LATE ? 1 : (2 + 3) * 8, b = kind == K_LATE ? nb : (2 + 4) * 8;
		for (unsigned i = a; i < b; i++)
			if (getbit(in, i) != getbit(in, i - 1) && frand() < 0.3)
				setbit(rx, i, getbit(in, i - 1));
		break;
	}
	case K_FLAGBIT: rx[2 + 3] ^= 0x04; break;
	case K_WRHDR0:
		rx[2 + 3] = 0x0C;
		rx[2 + 5] = rx[2 + 6] = rx[2 + 7] = rx[2 + 8] = 0;
		break;
	case K_WRHDR_RAND: {
		uint32_t l = rnd() % (img_n / 512);
		rx[2 + 3] = 0x0C;
		rx[2 + 4] = rnd();
		rx[2 + 5] = l >> 24; rx[2 + 6] = l >> 16; rx[2 + 7] = l >> 8; rx[2 + 8] = l;
		rx[2 + 13] = rnd();
		rx[2 + 14] = 24;
		break;
	}
	}
}

/* the bits m65io's frame check knows to be 0 (copy of frame_sane() in m65io.c) */
static int frame_sane(const uint8_t *rx)
{
	const uint8_t *r = rx + 2;
	unsigned v = r[2];
	if (r[0] != 0x4D || r[1] != 0x65 || v == 0 || v > 15)
		return 0;
	if ((r[3] & 0xC0) || (r[9] & 0x80) || (r[11] << 8 | r[12]) > 1024)
		return 0;
	if (v >= 3 && ((r[16] & 0xF8) || (r[18] & 0xF0)))
		return 0;
	if (v >= 4 && (rx[0] || rx[1] || r[20] || r[21]))
		return 0;
	return 1;
}

/* ------------------------------------------------------------------ scenarios */
struct link {
	double p;                     /* probability per STATUS frame */
	uint32_t above_hz;            /* only when the rate is higher than this (0: always) */
	unsigned kinds;               /* bit mask of K_* */
	int bias_kind;                /* this kind with probability bias, else one of kinds */
	double bias;
	int also_get;                 /* SD_GET data may be garbled too */
	double rate;                  /* instead of p: this many garbled frames per second */
};
static struct link L;
static int done;                  /* scenario finished: drain, then compare and exit */
static double done_t;
static uint32_t spi_hz_seen;

static int sane_kind(int k) { return k == K_FLAGBIT || k == K_WRHDR0 || k == K_WRHDR_RAND; }

/* a protected-looking MBR: FAT32 at 2048, MEGA65 system partition after it */
static void make_mbr(uint8_t *s, uint32_t sig)
{
	memcpy(s, img0, 512);
	s[440] = sig; s[441] = sig >> 8; s[442] = sig >> 16; s[443] = sig >> 24;
	s[510] = 0x55; s[511] = 0xAA;
}

/* a script: one step at a time, each waits for the core to be idle (or a time) */
static int step;
static double step_t;
static uint32_t nsect;

static int wait_idle(double t, double min_gap)
{
	return core_idle() && !M.wr_req && t - step_t >= min_gap;
}
static void next(double t) { step++; step_t = t; }

static uint8_t blk[512];

static void finish(double t, const char *why)
{
	if (!done) {
		done = 1;
		done_t = t;
		evf("SCEN_DONE %s", why);
	}
}

/* workload used by several scenarios: reads and writes mixed, an MBR rewrite, a zero block,
 * a CMD25 chain; starts at t_start */
static int workload(double t, double t_start, int nops)
{
	static int i;
	if (t < t_start)
		return 0;
	if (!wait_idle(t, 0.002))
		return 0;
	if (i >= nops)
		return 1;
	int k = i % 10;
	uint32_t lba = 4096 + rnd() % (nsect - 8192);
	if (k == 3) {
		make_mbr(blk, 0x12345678 + i);
		core_write(0, blk, 1, 3);
	} else if (k == 5) {
		block_data(blk, lba, 1);
		core_write(lba, blk, 1, 3);          /* an all-zero block elsewhere: legitimate */
	} else if (k == 7) {
		core_write(lba, NULL, 8, 3);         /* CMD25, 8 blocks */
	} else if (k & 1) {
		core_write(lba, NULL, 1, 3);
	} else {
		core_read(lba);
	}
	i++;
	step_t = t;
	return 0;
}

static void scen_tick(double t)
{
	if (done)
		return;
	if (!strcmp(scen, "good")) {
		if (step == 0 && t > 0.2) { core_init(); next(t); }
		if (step == 1 && workload(t, 2.5, 60)) finish(t, "workload complete");
		if (t > 30) finish(t, "TIMEOUT");
	} else if (!strcmp(scen, "spurious")) {
		/* garbled frames that pass every bit check: flags/lba/seq/lastcmd wrong */
		L.p = 0.03; L.above_hz = 0; L.kinds = 1 << K_FLAGBIT | 1 << K_WRHDR0 | 1 << K_WRHDR_RAND |
						  1 << K_LATE_FLAGS;
		if (step == 0 && t > 0.2) { core_init(); next(t); }
		if (step == 1 && workload(t, 0.3, 60)) finish(t, "workload complete");
		if (t > 40) finish(t, "TIMEOUT");
	} else if (!strcmp(scen, "garbage")) {
		/* a link that is marginal above 34 MHz, all kinds of garbled frames, writes and reads */
		/* garbled frames of every kind at every rate, ~0.4 per second, SD_GET data too */
		L.rate = 0.4; L.above_hz = 0; L.kinds = (1 << K_N) - 1; L.also_get = 1;
		if (step == 0 && t > 0.2) { core_init(); next(t); }
		if (step == 1 && workload(t, 0.3, 100000)) finish(t, "workload complete");
		if (t > 45 && wait_idle(t, 0)) finish(t, "observation time over");
		if (t > 90) finish(t, "TIMEOUT");
	} else if (!strcmp(scen, "incident")) {
		/* 2026-11-13: JTAG load (unconfigured), fresh bitstream (seq 0, lba 0, wbuf zero), card
		 * initialised, idle; MISO late now and then at 49.5 MHz */
		L.p = 0.05; L.above_hz = 34000000; L.kinds = 1 << K_LATE | 1 << K_SHR;
		L.bias_kind = K_LATE_FLAGS; L.bias = 0.97;
		if (step == 0) { M.configured = 0; M.float_kind = 1; next(t); }
		if (step == 1 && t > 1.0) { model_fresh(); evf("CONFIGURED"); next(t); }
		if (step == 2 && t > 1.5) { core_init(); next(t); }
		if (t > 14) finish(t, "observation time over");
	} else if (!strcmp(scen, "probe_write")) {
		/* a write captured while the link is being probed (just configured, marginal above
		 * 34 MHz): not served while unverified, served once the link is stable */
		L.p = 0.01; L.above_hz = 34000000; L.kinds = (1 << K_N) - 1;
		if (step == 0) { M.configured = 0; M.float_kind = 0; next(t); }
		if (step == 1 && t > 1.0) { model_fresh(); evf("CONFIGURED"); next(t); }
		if (step == 2 && t > 1.1) { core_init(); next(t); }
		if (step == 3 && t > 1.2) { core_write(1234, NULL, 1, 2); evf("WRITE_ISSUED 1234"); next(t); }
		if (step == 4 && !M.wr_req && !M.fill) { evf("WRITE_DONE"); next(t); }
		if (step == 5 && t - step_t > 0.5) finish(t, "write served");
		if (t > 40) finish(t, "TIMEOUT");
	} else if (!strcmp(scen, "protect")) {
		/* zero block / no signature to sector 0 and to the FAT boot sector: refused, never
		 * acked; the core gives up (CMD0, reads with the flag stuck), then valid writes */
		if (step == 0 && t > 0.2) { core_init(); next(t); }
		if (step == 1 && t > 2.6) { block_data(blk, 0, 1); core_write(0, blk, 1, 3); evf("W0_ZERO"); next(t); }
		if (step == 2 && t - step_t > 1.0) { core_cmd0(); core_init(); evf("ABANDON"); next(t); }
		if (step == 3 && t - step_t > 0.05) { core_read(77); next(t); }
		if (step == 4 && !M.rd_req && t - step_t > 0.2) { core_read(2048); next(t); }
		if (step == 5 && !M.rd_req && t - step_t > 0.2) {
			make_mbr(blk, 0xCAFEF00D); core_write(0, blk, 1, 4); evf("W0_VALID"); next(t);
		}
		if (step == 6 && !M.w_busy && !M.fill && t - step_t > 0.3) {
			memcpy(blk, img0 + 2048 * 512, 512); blk[510] = 0; blk[511] = 0;
			core_write(2048, blk, 1, 4); evf("WVBR_NOSIG"); next(t);
		}
		if (step == 7 && t - step_t > 1.0) { core_cmd0(); core_init(); evf("ABANDON"); next(t); }
		if (step == 8 && t - step_t > 0.1) { core_read(99); next(t); }
		if (step == 9 && !M.rd_req && t - step_t > 0.1) {
			memcpy(blk, img0 + 2048 * 512, 512); blk[3] ^= 0x55;
			core_write(2048, blk, 1, 4); evf("WVBR_VALID"); next(t);
		}
		if (step == 10 && !M.w_busy && !M.fill && t - step_t > 0.3) { core_write(5000, NULL, 1, 4); next(t); }
		if (step == 11 && !M.w_busy && !M.fill && !M.wr_req && t - step_t > 0.3) finish(t, "sequence complete");
		if (t > 20) finish(t, "TIMEOUT");
	} else if (!strcmp(scen, "abandon")) {
		/* a write deferred while the link is unverified; the core gives up and reads another
		 * sector (flag stuck, lba of the read): the old block must not land there */
		if (step == 0) { M.configured = 0; next(t); }
		if (step == 1 && t > 0.5) { model_fresh(); evf("CONFIGURED"); next(t); }
		if (step == 2 && t > 0.6) { core_init(); next(t); }
		if (step == 3 && t > 0.7) { core_write(500, NULL, 1, 2); evf("WRITE_ISSUED 500"); next(t); }
		if (step == 4 && t > 1.2) { core_cmd0(); core_init(); evf("ABANDON"); next(t); }
		if (step == 5 && t > 1.3) { core_read(777); next(t); }
		if (step == 6 && t > 4.5) { core_write(500, NULL, 1, 4); evf("RETRY 500"); next(t); }
		if (step == 7 && !M.w_busy && !M.fill && !M.wr_req) finish(t, "retry served");
		if (t > 15) finish(t, "TIMEOUT");
	} else if (!strcmp(scen, "datamis")) {
		if (step == 0 && t > 0.2) { core_init(); next(t); }
		if (step == 1 && t > 2.6) { M.dmis_once = 1; core_write(3333, NULL, 1, 2); next(t); }
		if (step == 2 && !M.wr_req && !M.fill) finish(t, "write served");
		if (t > 10) finish(t, "TIMEOUT");
	} else if (!strcmp(scen, "usr1")) {
		if (step == 0 && t > 0.2) { core_init(); next(t); }
		if (step == 1 && t > 1.0) { M.configured = 0; evf("UNCONFIGURED"); next(t); }
		if (step == 2 && t > 1.2) { evf("SIGUSR1"); raise(SIGUSR1); next(t); }
		if (step == 3 && t > 2.0) { model_fresh(); M.vld = 0xFFFF; M.inval_drop = 1; core_init();
					    evf("CONFIGURED (cache full, first INVAL lost)"); next(t); }
		if (step == 4 && t > 4.0) finish(t, "observation time over");
	} else if (!strcmp(scen, "perf")) {
		/* steady state after the probation: polls per demand read */
		if (step == 0 && t > 0.2) { core_init(); next(t); }
		if (step == 1 && t > 11.0) { n_rd_pending_polls = 0; n_reads_issued = 0; evf("MEASURE"); next(t); }
		if (step == 2 && wait_idle(t, 0.0005)) {
			if (n_reads_issued >= 2000) finish(t, "2000 reads");
			else core_read(100000 + rnd() % 1000000);
		}
		if (t > 40) finish(t, "TIMEOUT");
	} else {
		finish(t, "unknown scenario");
	}
}

/* ------------------------------------------------------------------ SPI transactions */
static void end_run(void)
{
	/* compare the image with the expected one */
	int fd = open(img_path, O_RDONLY);
	unsigned long bad = 0;
	long first = -1;
	uint8_t s[512];
	for (size_t o = 0; fd >= 0 && o < img_n; o += 512) {
		if (pread(fd, s, 512, o) != 512 || memcmp(s, expimg + o, 512)) {
			if (first < 0) first = o / 512;
			bad++;
		}
	}
	if (fd >= 0) close(fd);
	char kinds[256] = "";
	for (int k = 0, n = 0; k < K_N; k++)
		n += snprintf(kinds + n, sizeof kinds - n, "%s%s:%lu", k ? "," : "", kname[k], n_kind[k]);
	evf("KINDS %s", kinds);
	evf("END status=%lu garbled=%lu pass_check=%lu sane_garbled=%lu pwrite_ok=%lu pwrite_bad=%lu acks=%lu "
	    "acks_ignored=%lu reads_ok=%lu reads_bad=%lu put_noreq=%lu invals=%lu writes_issued=%lu "
	    "reads_issued=%lu rd_pending_polls=%lu last_bad_t=%.4f first_pwrite_t=%.4f "
	    "image_bad_sectors=%lu first_bad=%ld", n_status, n_garbled, n_pass_check, n_sane_garbled, n_pw_ok, n_pw_bad,
	    n_ack, n_ack_ign, n_rd_ok, n_rd_bad, n_put_noreq, n_inval, n_writes_issued, n_reads_issued,
	    n_rd_pending_polls, last_bad_t, first_pw_t, bad, first);
	fflush(ev);
	_exit(0);
}

static void spi_xfer(const uint8_t *tx, uint8_t *rx, unsigned len, uint32_t hz)
{
	double t;
	if (t0 < 0)
		t0 = mono();
	t = T();
	if (hz != spi_hz_seen) {
		evf("RATE %u", hz);
		spi_hz_seen = hz;
	}
	scen_tick(t);
	if (done && t - done_t > 1.2)
		end_run();
	if (!M.configured) {
		for (unsigned i = 0; i < len; i++)
			rx[i] = M.float_kind == 0 ? 0xFF : M.float_kind == 2 ? 0 : rnd();
		if (tx[0] == 0x01)
			last_bad_t = last_detect_t = t;
		return;
	}
	memset(rx, 0, len);
	switch (tx[0]) {
	case 0x01:
		n_status++;
		status_tick();
		if (M.rd_req && !M.rd_ready && !M.rd_error)
			n_rd_pending_polls++;
		build_status(rx, len);
		static double next_inj = -1;
		int inject = 0;
		if (!L.above_hz || hz > L.above_hz) {
			if (L.rate > 0) {
				if (next_inj < 0)
					next_inj = t + 1.0 / L.rate;
				if (t >= next_inj) {
					inject = 1;
					next_inj = t + (0.2 + 1.6 * frand()) / L.rate;
				}
			} else {
				inject = L.p > 0 && frand() < L.p;
			}
		}
		if (inject) {
			int k;
			if (L.bias > 0 && frand() < L.bias)
				k = L.bias_kind;
			else
				do k = rnd() % K_N; while (!(L.kinds & 1u << k));
			uint8_t good[128];
			memcpy(good, rx, len);
			corrupt(rx, len, k);
			if (memcmp(good, rx, len)) {
				n_garbled++;
				n_kind[k]++;
				if (sane_kind(k))
					n_sane_garbled++;
				last_bad_t = t;
				if (len >= 24 && !frame_sane(rx))
					last_detect_t = t;
				else if (len >= 24)
					n_pass_check++;
			}
		}
		break;
	case 0x10:                            /* SD_PUT */
		if (len >= 513 && M.rd_req && !M.rd_ready && !M.rd_error) {
			if (M.lba < nsect && !memcmp(tx + 1, expimg + (size_t)M.lba * 512, 512))
				n_rd_ok++;
			else {
				n_rd_bad++;
				evf("READ_BAD lba=%u", M.lba);
			}
			M.rd_ready = 1;
			M.rd_consume = 1;
		} else {
			n_put_noreq++;
		}
		break;
	case 0x11:                            /* SD_GET */
		for (unsigned i = 2; i < len && i - 2 < 512; i++)
			rx[i] = M.wbuf[i - 2];
		if (M.dmis_once && M.w_busy) {
			M.dmis_once = 0;
			rx[2 + 100] ^= 0x10;
			evf("SD_GET garbled once");
		} else if (L.also_get && (!L.above_hz || hz > L.above_hz) &&
			   frand() < (L.p > 0 ? L.p * 4 : 0.05)) {
			rx[2 + rnd() % 512] ^= 1 << (rnd() % 8);
		}
		break;
	case 0x12:                            /* SD_WACK */
		if (M.wr_req && M.w_busy && !M.ack_cross) {
			n_ack++;
			memcpy(expimg + (size_t)M.wlba * 512, M.wbuf, 512);
			M.ack_cross = 2;              /* the flag is still seen in the next frame */
		} else if (!M.ack_cross) {
			n_ack_ign++;
		}
		break;
	case 0x13:
		if (M.rd_req && !M.rd_ready) {
			M.rd_error = 1;
			M.rd_consume = 1;
		}
		break;
	case 0x14: {                          /* SD_PREF */
		unsigned slot = tx[1] & 15, cnt = tx[2];
		uint32_t lba = (uint32_t)tx[3] << 24 | tx[4] << 16 | tx[5] << 8 | tx[6];
		for (unsigned k = 0; k < cnt && 7 + (k + 1) * 512 <= len; k++) {
			if (lba + k >= nsect || memcmp(tx + 7 + k * 512, expimg + (size_t)(lba + k) * 512, 512))
				evf("PREF_BAD lba=%u", lba + k);
			M.vld |= 1 << ((slot + k) & 15);
		}
		break;
	}
	case 0x15:
		if (M.inval_drop) {
			M.inval_drop--;
			evf("INVAL lost");
		} else {
			M.vld = 0;
			n_inval++;
			evf("INVAL");
		}
		break;
	default:
		break;
	}
}

/* ------------------------------------------------------------------ interposed libc calls */
static void __attribute__((constructor)) init(void)
{
	const char *p = getenv("FAKESPI_LOG"), *s = getenv("FAKESPI_SCEN"), *seed = getenv("FAKESPI_SEED");
	img_path = getenv("FAKESPI_IMG");
	if (!p || !img_path)
		return;
	ev = fopen(p, "w");
	scen = s ? s : "good";
	if (seed)
		rs ^= strtoull(seed, NULL, 0) * 2654435761u;
	int fd = open(img_path, O_RDONLY);
	struct stat sb;
	if (fd < 0 || fstat(fd, &sb))
		return;
	img_n = sb.st_size;
	nsect = img_n / 512;
	img0 = malloc(img_n);
	expimg = malloc(img_n);
	if (pread(fd, img0, img_n, 0) != (ssize_t)img_n)
		evf("image read short");
	memcpy(expimg, img0, img_n);
	close(fd);
	model_fresh();
	evf("START scen=%s image=%s sectors=%u", scen, img_path, nsect);
}

int ioctl(int fd, unsigned long req, ...)
{
	static int (*real)(int, unsigned long, ...);
	va_list ap;
	va_start(ap, req);
	void *arg = va_arg(ap, void *);
	va_end(ap);
	if (ev && req == SPI_IOC_MESSAGE(1)) {
		struct spi_ioc_transfer *t = arg;
		spi_xfer((const uint8_t *)(uintptr_t)t->tx_buf, (uint8_t *)(uintptr_t)t->rx_buf, t->len,
			 t->speed_hz);
		return t->len;
	}
	if (ev && (req == SPI_IOC_WR_MODE || req == SPI_IOC_WR_BITS_PER_WORD ||
		   req == SPI_IOC_WR_MAX_SPEED_HZ))
		return 0;
	if (!real)
		real = dlsym(RTLD_NEXT, "ioctl");
	return real(fd, req, arg);
}

static void check_pwrite(const void *buf, size_t n, off_t off)
{
	double t = T();
	uint32_t lba = off / 512;
	int ok = n == 512 && M.wr_req && M.w_busy && M.wlba == lba && !memcmp(buf, M.wbuf, 512);
	if (ok)
		n_pw_ok++;
	else {
		n_pw_bad++;
		int zero = 1;
		for (size_t i = 0; i < n && zero; i++)
			zero = !((const uint8_t *)buf)[i];
		evf("PWRITE_BAD lba=%u len=%zu zero=%d (model: wr_req=%d busy=%d wlba=%u lba=%u)", lba, n, zero,
		    M.wr_req, M.w_busy, M.wlba, M.lba);
	}
	if (first_pw_t < 0) {
		first_pw_t = t;
		evf("FIRST_PWRITE last_bad_t=%.4f last_detect_t=%.4f", last_bad_t, last_detect_t);
	}
	evf("PWRITE lba=%u ok=%d", lba, ok);
}

ssize_t pwrite(int fd, const void *buf, size_t n, off_t off)
{
	static ssize_t (*real)(int, const void *, size_t, off_t);
	if (!real)
		real = dlsym(RTLD_NEXT, "pwrite");
	if (ev)
		check_pwrite(buf, n, off);
	return real(fd, buf, n, off);
}

ssize_t pwrite64(int fd, const void *buf, size_t n, off_t off)
{
	static ssize_t (*real)(int, const void *, size_t, off_t);
	if (!real)
		real = dlsym(RTLD_NEXT, "pwrite64");
	if (ev)
		check_pwrite(buf, n, off);
	return real(fd, buf, n, off);
}
