/*
 * m65io.c -- hot loop of the Rockchip-side MEGA65 service (runs as root on the
 * Luckfox/CM4 module).  Talks to rk_bridge.vhdl over spidev (spi3-m1):
 *
 *   * SD card:  serves every sector the MEGA65 reads/writes from/to a raw image
 *               file (pread/pwrite).  Reads past the end of the image get the
 *               bridge's out-of-range answer (0x13), so FDISK/HYPPO see the
 *               real card size; writes past the end are acknowledged and dropped.
 *   * Monitor:  TCP port 4510 is a transparent pipe to the MEGA65 serial monitor.
 *   * Ethernet: userspace layer-2 bridge between the MEGA65 MAC (via eth_vphy /
 *               rk_bridge) and the module's wired interface (-e eth0).  The vendor
 *               kernel has no TUN/TAP, bridge or NAT, so this uses an AF_PACKET
 *               socket: MEGA65 frames go out unchanged (own MAC -> own DHCP lease
 *               on the LAN); frames for the MEGA65's MAC (learned from its traffic)
 *               plus broadcasts/multicasts come back in (eth0 promiscuous).  The
 *               module's own outgoing frames are skipped (no loops; the MEGA65 and
 *               the module itself cannot talk directly).  FCS: checked/stripped
 *               towards the LAN, computed/appended towards the MEGA65.
 *   * Stats:    rd/wr/... counters written to a small text file twice a second
 *               (read by m65d.py's "status" command).  In /run (RAM): a file
 *               rewritten twice a second must not wear the module's flash;
 *               setup_rockchip.sh links /root/m65/m65io.stats to it.
 *
 * The control side (typing, keys, joysticks, reset) stays in m65d.py; both
 * processes share /dev/spidev3.0 -- every ioctl is one complete CS transaction
 * and the kernel serialises them.
 *
 * Write guard (2026-11-14).  A STATUS frame has no checksum.  On 2026-11-13 a frame read over a
 * marginal link (VIXEN-32 bitstream at 49.5 MHz, MISO sampled late now and then) right after a JTAG
 * load had the magic intact but flags 0x08 (card initialised) read as 0x0C (+ write captured); m65io
 * fetched the fresh bitstream's all-zero write buffer and wrote it to LBA 0 (the partition table).
 * The bridge holds a captured write (wr_req) until SD_WACK and SD_WACK without a pending write does
 * nothing, so a write that is not served now is simply presented again.  m65io therefore writes only
 *   - while the link is verified: >= GOOD_RUN_MIN clean frames in a row at the current rate and
 *     >= HOLD_S since the last bad frame, outage, probe or rate change (writes wait, reads do not);
 *   - after a second STATUS read (three while the request looks stale or the link is on probation)
 *     shows the same version, flags, seq, lba and last command, and the last command was CMD24/25;
 *   - after the block was read twice (SD_GET restarts at byte 0 and the block cannot change while
 *     the card is busy) with identical data;
 *   - if the block is plausible: inside the image, and a write to sector 0 or to the first sector of
 *     a FAT partition listed there must carry the boot signature 55 AA (never acknowledged otherwise).
 * Every frame is also checked for the bits that are always zero (bytes 0-1, the flags' top bits, the
 * counters' unused bits, status 20-21 on v4); a frame failing that counts as a lost bridge.  While on
 * probation (PROBATION_S after such an event) read requests are confirmed by a second frame too.
 * Every write that is not served is logged (rate limited) and counted (wr_refused + reasons).
 *
 * Build:  gcc -O2 -Wall -o m65io m65io.c
 * Run:    m65io [-d /dev/spidev3.0] [-s hz] [-i card.img] [-p 4510] [-S stats]
 *               [-e eth0|none] [-v]
 * Without -e: eth0, or (Radxa CM3: predictable names, "end1") the first en* interface.
 */
#define _GNU_SOURCE
#include <arpa/inet.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/ethtool.h>
#include <linux/if_packet.h>
#include <linux/sockios.h>
#include <net/if.h>
#include <linux/spi/spidev.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <signal.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#define MAX_CLIENTS 8
#define XFER_MAX 4096           /* spidev default bufsiz */
#define SPIN_S 0.02             /* keep spinning this long after the last activity */
#define NAP_S  1.0              /* then poll every 200 us up to this long ...       */
#define NAP_US 200              /*                                                  */
#define DOZE_US 10000           /* ... and every 10 ms after that (idle: module cool) */

static int spi_fd, img_fd, verbose;
/* MEGA65 writes reach the eMMC soon, not after the kernel's ~30 s writeback: fdatasync once
 * no further sector was written for SYNC_IDLE_S (a save of many sectors -> one flush) */
#define SYNC_IDLE_S 0.1
static int img_dirty;
static double last_write;
static double now(void);
/* 40 MHz: linktest.py clean at 25/33/40 MHz (50 MHz fails), and sdlinktest.py: 300 random sectors
 * read by the MEGA65's SD controller through the whole path, 0 mismatches (2026-11-06) */
static uint32_t spi_hz = 40000000;
static uint32_t spi_hz_want;           /* -s: the link runs here unless it proves unreliable */
static uint64_t img_sectors;
static struct {
	unsigned long rd, wr, rd_err, wr_drop, mon_in, mon_out, bridge_down, polls;
	unsigned long eth_out, eth_in, eth_badfcs, eth_big, eth_fpga_drops;
	unsigned long pf, pf_tx, pf_hits;         /* read cache (bridge v4) */
	unsigned pf_valid;
	/* guard: frames with the magic but impossible bits; writes not served (all reasons), by
	 * reason: link not verified yet (once per request), confirmation frames disagreed, the two
	 * data reads differed, implausible (protected sector without boot signature: never acked);
	 * read requests not confirmed on probation; SD_INVAL resent */
	unsigned long st_insane, wr_refused, wr_wait, wr_unconf, wr_dmis, wr_implaus, wr_stale, rd_unconf,
		inval_retry;
} st;
static uint8_t m65_mac[6];
static int m65_mac_known;

/* log line with the wall-clock time (the log is in RAM, one line per event) */
static void logmsg(const char *fmt, ...)
{
	struct timespec ts;
	struct tm tm;
	clock_gettime(CLOCK_REALTIME, &ts);
	localtime_r(&ts.tv_sec, &tm);
	printf("%02d:%02d:%02d.%03ld ", tm.tm_hour, tm.tm_min, tm.tm_sec, ts.tv_nsec / 1000000);
	va_list ap;
	va_start(ap, fmt);
	vprintf(fmt, ap);
	va_end(ap);
	fflush(stdout);
}

/* ---- link verification (see the header) */
#define GOOD_RUN_MIN 64         /* clean STATUS frames in a row ...                               */
#define HOLD_S 2.0              /* ... and this long since the last bad frame / rate change       */
#define PROBATION_S 10.0        /* reads confirmed by a second frame this long after such an event */
static unsigned long good_run;
static double link_event_t;
static int frame_ver = -1;

static void link_event(void)
{
	good_run = 0;
	link_event_t = now();
}

static int link_verified(void)
{
	return good_run >= GOOD_RUN_MIN && now() - link_event_t >= HOLD_S;
}

static int on_probation(void)
{
	return now() - link_event_t < PROBATION_S;
}

static void set_hz(uint32_t hz)
{
	if (hz != spi_hz) {
		spi_hz = hz;
		link_event();
	}
}

static int xfer(const uint8_t *tx, uint8_t *rx, unsigned len)
{
	struct spi_ioc_transfer t = {
		.tx_buf = (unsigned long)tx,
		.rx_buf = (unsigned long)rx,
		.len = len,
		.speed_hz = spi_hz,
		.bits_per_word = 8,
	};
	if (ioctl(spi_fd, SPI_IOC_MESSAGE(1), &t) < 0) {
		perror("spi ioctl");
		return -1;
	}
	return 0;
}

struct bstat {
	int ok;
	uint8_t flags, seq, sdcmds, lastcmd, ctrl, ver;
	uint32_t lba;
	unsigned rxcnt, txfree;
	unsigned eth_txlen, eth_rxfree, eth_drops;    /* bridge v3+ */
};

/* The bits a bridge always sends as 0 (all versions: flags b6-7, rx count b15, tx free <= 1024;
 * v3+: Ethernet length b11-15, free slots b4-7; v4: the cmd and dummy bytes and status 20-21 --
 * v3 had debug bytes at 20-51).  A late or early sampled frame usually breaks one of them. */
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

static void bridge_status(struct bstat *b)
{
	uint8_t tx[24] = { 0x01 }, rx[24];
	b->ok = 0;
	if (xfer(tx, rx, sizeof tx)) {
		link_event();
		frame_ver = -1;
		return;
	}
	const uint8_t *r = rx + 2;
	b->ok = frame_sane(rx);
	if (!b->ok && r[0] == 0x4D && r[1] == 0x65)
		st.st_insane++;                        /* the old check would have taken it */
	if (b->ok && (frame_ver < 0 || r[2] == frame_ver)) {
		good_run++;
	} else {
		link_event();
	}
	frame_ver = b->ok ? r[2] : -1;
	b->ver = r[2];
	b->flags = r[3];
	b->seq = r[4];
	b->lba = (uint32_t)r[5] << 24 | r[6] << 16 | r[7] << 8 | r[8];
	b->rxcnt = r[9] << 8 | r[10];
	b->txfree = r[11] << 8 | r[12];
	b->sdcmds = r[13];
	b->lastcmd = r[14];
	b->ctrl = r[15];
	b->eth_txlen = b->ver >= 3 ? ((r[16] & 7) << 8 | r[17]) : 0;
	b->eth_rxfree = b->ver >= 3 ? r[18] : 0;
	b->eth_drops = b->ver >= 3 ? r[19] : 0;
}

/* Link check: n STATUS reads at the current clock, each with the magic and the same version.
 * How fast MISO is clean depends on the bitstream (a build whose MISO register sat far from its
 * pin failed every read above 25 MHz), so the clock is chosen per bitstream: the requested rate
 * if it is clean, else the next lower one.  Run at start, while the bridge is down (another
 * core configuring, or a bad clock) and after repeated single dropouts (a marginal clock). */
static int link_ok(int n)
{
	struct bstat b;
	int ver = -1;
	for (int i = 0; i < n; i++) {
		bridge_status(&b);
		if (!b.ok || (ver >= 0 && b.ver != ver))
			return 0;
		ver = b.ver;
	}
	return 1;
}

static int link_select(uint32_t max_hz)      /* fastest clean rate <= max_hz */
{
	static const uint32_t rates[] = { 0, 33333333, 25000000, 16666666, 10000000 };
	uint32_t old = spi_hz;
	for (unsigned i = 0; i < sizeof rates / sizeof rates[0]; i++) {
		uint32_t hz = i ? rates[i] : spi_hz_want;
		if (hz > max_hz || (i && hz >= spi_hz_want))
			continue;
		set_hz(hz);
		/* One failed read must not demote a good rate for the whole session (2026-11-10: a
		 * bitstream with 0 errors in 9000 raw pairs at 40 MHz started at 25 MHz because the
		 * probe ran during the core's start-up).  A marginal link fails the second pass too. */
		if (link_ok(200) || (usleep(50000), link_ok(200))) {
			if (hz != old || hz != spi_hz_want)
				logmsg("m65io: SPI link at %u Hz%s\n", hz,
				       hz != spi_hz_want ? " (requested rate unreliable with this bitstream)" : "");
			link_event();                  /* a probe ran: writes wait for HOLD_S */
			return 1;
		}
	}
	set_hz(spi_hz_want);                   /* no bridge answering at any rate (yet) */
	link_event();
	return 0;
}

/* While the link is up below the requested rate: try the faster rates again.  A probe that
 * ran while a freshly loaded bitstream was still starting up (or a C64-core session) can leave
 * the link slower than this bitstream allows, and nothing else would ever raise it.  Only
 * status reads, so it is safe between transactions; on failure the old rate is kept. */
static void link_upgrade(void)
{
	static const uint32_t rates[] = { 0, 33333333, 25000000, 16666666 };
	uint32_t old = spi_hz;
	for (unsigned i = 0; i < sizeof rates / sizeof rates[0]; i++) {
		uint32_t hz = i ? rates[i] : spi_hz_want;
		if (hz <= old || (i && hz >= spi_hz_want))
			continue;
		set_hz(hz);
		if (link_ok(200)) {
			logmsg("m65io: SPI link back up to %u Hz\n", hz);
			return;
		}
	}
	set_hz(old);
}

/* ---------------------------------------------------------------- Ethernet */
static uint32_t crc_tab[256];

static void crc_init(void)
{
	for (uint32_t i = 0; i < 256; i++) {
		uint32_t c = i;
		for (int k = 0; k < 8; k++)
			c = c & 1 ? 0xEDB88320u ^ (c >> 1) : c >> 1;
		crc_tab[i] = c;
	}
}

static uint32_t crc32_eth(const uint8_t *p, unsigned n)
{
	uint32_t c = 0xFFFFFFFFu;
	while (n--)
		c = crc_tab[(c ^ *p++) & 0xFF] ^ (c >> 8);
	return c ^ 0xFFFFFFFFu;
}

/* default wired interface: eth0 (Luckfox), else the first en* interface (Radxa CM3: end1) */
static const char *eth_default(void)
{
	static char name[IFNAMSIZ];
	if (if_nametoindex("eth0"))
		return "eth0";
	DIR *d = opendir("/sys/class/net");
	struct dirent *e;
	name[0] = 0;
	while (d && (e = readdir(d)))
		if (!strncmp(e->d_name, "en", 2) && strlen(e->d_name) < IFNAMSIZ &&
		    (!name[0] || strcmp(e->d_name, name) < 0))
			strcpy(name, e->d_name);
	if (d)
		closedir(d);
	return name[0] ? name : "eth0";
}

/* GRO would hand us coalesced super-frames: switch it off (no ethtool binary on the Luckfox).
 * The Radxa CM3's stmmac answers EBUSY while its link is still coming up at boot, so the main
 * loop retries until it works. */
static int gro_off_done;
static void eth_gro_off(int s, const char *ifname)
{
	struct ifreq ifr;
	struct ethtool_value ev = { .cmd = ETHTOOL_SGRO, .data = 0 };
	memset(&ifr, 0, sizeof ifr);
	memcpy(ifr.ifr_name, ifname, strnlen(ifname, IFNAMSIZ - 1));
	ifr.ifr_data = (void *)&ev;
	int ok = ioctl(s, SIOCETHTOOL, &ifr) == 0;
	if (ok && gro_off_done < 0)
		logmsg("m65io: %s GRO off\n", ifname);
	else if (!ok && gro_off_done == 0)
		logmsg("m65io: %s GRO off failed (%s), retrying\n", ifname, strerror(errno));
	gro_off_done = ok ? 1 : -1;
}

static int eth_open(const char *ifname, int *ifindex)
{
	int s = socket(AF_PACKET, SOCK_RAW, htons(ETH_P_ALL));
	if (s < 0) { perror("AF_PACKET socket"); return -1; }
	struct ifreq ifr;
	memset(&ifr, 0, sizeof ifr);
	memcpy(ifr.ifr_name, ifname, strnlen(ifname, IFNAMSIZ - 1));   /* ifr is zeroed */
	if (ioctl(s, SIOCGIFINDEX, &ifr) < 0) { perror(ifname); close(s); return -1; }
	*ifindex = ifr.ifr_ifindex;
	struct sockaddr_ll sll = { .sll_family = AF_PACKET, .sll_protocol = htons(ETH_P_ALL),
				   .sll_ifindex = *ifindex };
	if (bind(s, (struct sockaddr *)&sll, sizeof sll) < 0) { perror("bind"); close(s); return -1; }
	struct packet_mreq mr = { .mr_ifindex = *ifindex, .mr_type = PACKET_MR_PROMISC };
	setsockopt(s, SOL_PACKET, PACKET_ADD_MEMBERSHIP, &mr, sizeof mr);
	eth_gro_off(s, ifname);
	fcntl(s, F_SETFL, O_NONBLOCK);
	return s;
}

/* frames MEGA65 -> LAN, and LAN -> MEGA65; returns 1 if anything moved */
static int serve_eth(const struct bstat *b, int pkt, int ifindex)
{
	static uint8_t tx[XFER_MAX], rx[XFER_MAX], fr[2048];
	int moved = 0;
	st.eth_fpga_drops = b->eth_drops;

	if (b->eth_txlen) {                          /* MEGA65 sent a frame */
		unsigned len = b->eth_txlen;
		memset(tx, 0, len + 2);
		tx[0] = 0x40;
		if (!xfer(tx, rx, len + 2) && len >= 18) {
			const uint8_t *f = rx + 2;
			uint32_t fcs = f[len - 4] | f[len - 3] << 8 | f[len - 2] << 16 | (uint32_t)f[len - 1] << 24;
			if (crc32_eth(f, len - 4) != fcs) {
				st.eth_badfcs++;
			} else {
				memcpy(m65_mac, f + 6, 6);          /* learn the MEGA65's MAC */
				m65_mac_known = 1;
				struct sockaddr_ll to = { .sll_family = AF_PACKET, .sll_ifindex = ifindex,
							  .sll_halen = 6 };
				memcpy(to.sll_addr, f, 6);
				if (sendto(pkt, f, len - 4, 0, (struct sockaddr *)&to, sizeof to) > 0)
					st.eth_out++;
			}
		}
		moved = 1;
	}

	/* LAN -> MEGA65, only while the bridge has free RX slots (else the kernel
	 * socket buffer holds/drops frames, like a congested link) */
	unsigned pushed = 0;
	for (int k = 0; k < 64 && pushed < b->eth_rxfree; k++) {   /* skipped frames don't use slots */
		struct sockaddr_ll from;
		socklen_t fl = sizeof from;
		ssize_t n = recvfrom(pkt, fr, sizeof fr - 4, 0, (struct sockaddr *)&from, &fl);
		if (n <= 0)
			break;
		if (from.sll_pkttype == PACKET_OUTGOING)    /* our own / injected frames */
			continue;
		int bcast = fr[0] & 1;                       /* broadcast or multicast */
		if (!bcast && !(m65_mac_known && !memcmp(fr, m65_mac, 6)))
			continue;
		if (n > 1514) { st.eth_big++; continue; }
		if (n < 60) { memset(fr + n, 0, 60 - n); n = 60; }   /* Ethernet minimum */
		uint32_t fcs = crc32_eth(fr, n);
		fr[n] = fcs; fr[n + 1] = fcs >> 8; fr[n + 2] = fcs >> 16; fr[n + 3] = fcs >> 24;
		n += 4;
		tx[0] = 0x41; tx[1] = n >> 8; tx[2] = n & 0xFF;
		memcpy(tx + 3, fr, n);
		xfer(tx, rx, n + 3);
		st.eth_in++;
		pushed++;
		moved = 1;
	}
	return moved;
}

/* ---- write guard state
 * sd_emu raises wr_req (and seq + 1) when a block is complete and holds it until SD_WACK; nothing
 * else clears it -- not a deselect, not CMD0.  A write that is not acked while the core gives up
 * (HYPPO: ~0.4 s) therefore stays "pending" while lba, seq and the last command follow the core's
 * NEXT commands, and wbuf is refilled by its next write.  So m65io tracks the request it first saw:
 * if the flag stays up while seq/lba/command count/last command change without our ack, the core
 * has moved on ("stuck") and a write is served again only when the frame before showed that write
 * command decoded (last cmd 24/25, same command count) and seq advanced by exactly one = the
 * capture of that command's block. */
static int idle_seq = -1;              /* seq of an idle bridge, seen in two frames in a row */
static int idle_cand = -1;
static int served_seq = -1;            /* the write just served (re-acked, not rewritten, while the */
static uint32_t served_lba;            /* bridge still shows it: the ack crosses into cpuclk)        */
static struct wreq {
	int on;
	uint8_t seq, ccnt, lastcmd;
	uint32_t lba;
} wq, chg, pre, proven;                /* request as first seen; a change of it seen once; stuck: the
					* frame before; a capture proven while stuck (until served) */
static int wq_acked;                   /* the request in wq was served and acked by us */
static int stuck, wr_low_n;
static double stuck_t;
static struct { int seq; uint32_t lba; } noted[6] = { { -1, 0 }, { -1, 0 }, { -1, 0 }, { -1, 0 },
							 { -1, 0 }, { -1, 0 } };   /* per reason */
static int impl_seq = -1;              /* the request refused as implausible (never acked) */
static uint32_t impl_lba;
/* sectors whose content must carry the boot signature: 0 and the first sector of each FAT
 * partition in the MBR (re-read at start, SIGUSR1 and after every write of sector 0) */
static uint32_t prot[5];
static int nprot = 1;

static void prot_from(const uint8_t *s)
{
	nprot = 1;
	prot[0] = 0;
	if (s[510] != 0x55 || s[511] != 0xAA)
		return;
	for (int i = 0; i < 4; i++)                  /* only a real partition table (else: a FAT */
		if (s[446 + 16 * i] & 0x7F)             /* boot sector at 0, no partitions)         */
			return;
	for (int i = 0; i < 4; i++) {
		const uint8_t *e = s + 446 + 16 * i;
		uint8_t t = e[4];
		uint32_t start = e[8] | e[9] << 8 | e[10] << 16 | (uint32_t)e[11] << 24;
		if ((t == 0x01 || t == 0x04 || t == 0x06 || t == 0x0B || t == 0x0C || t == 0x0E) &&
		    start > 0 && start < img_sectors)
			prot[nprot++] = start;
	}
}

static void prot_load(void)
{
	uint8_t s[512];
	if (pread(img_fd, s, 512, 0) == 512)
		prot_from(s);
	char list[80] = "";
	for (int i = 0, n = 0; i < nprot; i++)
		n += snprintf(list + n, sizeof list - n, "%s%u", i ? "," : "", prot[i]);
	logmsg("m65io: write guard on; boot-signature sectors %s\n", list);
}

static int is_wcmd(uint8_t lastcmd)
{
	return (lastcmd & 0x7f) == 24 || (lastcmd & 0x7f) == 25;
}

static int wreq_eq(const struct wreq *a, const struct wreq *c)
{
	return a->seq == c->seq && a->lba == c->lba && a->ccnt == c->ccnt && a->lastcmd == c->lastcmd;
}

/* every clean frame of the main loop: follow the bridge's request state */
static void track_req(const struct bstat *b)
{
	uint8_t f = b->flags;
	if (!(f & 0x05)) {                           /* idle: seq seen twice in a row */
		if (idle_cand == b->seq)
			idle_seq = b->seq;
		idle_cand = b->seq;
	} else {
		idle_cand = -1;
	}
	if (served_seq >= 0 && b->seq != served_seq)
		served_seq = -1;
	if (impl_seq >= 0 && b->seq != impl_seq)
		impl_seq = -1;
	if (!(f & 0x04)) {
		wq.on = chg.on = 0;
		if (stuck && ++wr_low_n >= 2) {      /* two frames without the flag: really gone */
			stuck = 0;
			proven.on = 0;
			logmsg("m65io: write request state clean again\n");
		}
		return;
	}
	wr_low_n = 0;
	struct wreq cur = { 1, b->seq, b->sdcmds, b->lastcmd, b->lba };
	if (!wq.on) {
		wq = cur;
		wq_acked = 0;
		chg.on = 0;
	} else if (!wreq_eq(&cur, &wq)) {
		/* the flag stayed up and the request changed: a new capture after our ack (CMD25), or
		 * the core moved on from a request we did not ack (seen in two frames: not a glitch) */
		if (wq_acked) {
			wq = cur;
			wq_acked = 0;
			chg.on = 0;
		} else if (chg.on && wreq_eq(&cur, &chg)) {
			if (!stuck) {
				stuck = 1;
				stuck_t = now();
				pre.on = proven.on = 0;
				logmsg("m65io: write request seq=%u lba=%u was not acked and the core moved on "
				       "(now seq=%u lba=%u lastcmd=%u): only proven captures are served\n",
				       wq.seq, wq.lba, cur.seq, cur.lba, cur.lastcmd);
			}
			wq = cur;
			chg.on = 0;
		} else {
			chg = cur;
		}
	}
	if (stuck) {
		/* the capture of a write command: the frame before showed it decoded (CMD24/25, same
		 * command count and lba) and seq has advanced by exactly one */
		if (pre.on && is_wcmd(cur.lastcmd) && cur.lastcmd == pre.lastcmd && cur.ccnt == pre.ccnt &&
		    cur.lba == pre.lba && cur.seq == (uint8_t)(pre.seq + 1))
			proven = cur;
		pre = cur;
		pre.on = is_wcmd(cur.lastcmd);
	}
}

/* why a write was not served */
enum { W_WAIT = 1, W_UNCONF, W_DMIS, W_IMPLAUS, W_STALE };
static const char *const wr_why[] = { "", "link not verified yet", "not confirmed by the next status",
				      "data reads differ", "no boot signature on a protected sector",
				      "stale request: no write command / not a proven capture" };

static void wr_note(int why, const struct bstat *b, const char *detail)
{
	static double win;
	static unsigned nlog, nsupp;
	/* waiting, stale and implausible requests are seen on every poll: once per request */
	if (why == W_WAIT || why == W_STALE || why == W_IMPLAUS) {
		if (noted[why].seq == b->seq && noted[why].lba == b->lba)
			return;
		noted[why].seq = b->seq;
		noted[why].lba = b->lba;
	}
	if (why == W_IMPLAUS) {
		impl_seq = b->seq;
		impl_lba = b->lba;
	}
	st.wr_refused++;
	switch (why) {
	case W_WAIT:    st.wr_wait++; break;
	case W_UNCONF:  st.wr_unconf++; break;
	case W_DMIS:    st.wr_dmis++; break;
	case W_IMPLAUS: st.wr_implaus++; break;
	case W_STALE:   st.wr_stale++; break;
	}
	double t = now();
	if (t - win > 10.0) {
		if (nsupp)
			logmsg("m65io: (%u more refused-write lines not shown)\n", nsupp);
		win = t;
		nlog = nsupp = 0;
	}
	if (++nlog > 20) {
		nsupp++;
		return;
	}
	logmsg("m65io: write NOT served (%s): seq=%u lba=%u flags=%02x lastcmd=%u cmds=%u ver=%u "
	       "spi_hz=%u run=%lu%s%s\n", wr_why[why], b->seq, b->lba, b->flags, b->lastcmd, b->sdcmds,
	       b->ver, spi_hz, good_run, detail ? " " : "", detail ? detail : "");
}

static int same_req(const struct bstat *a, const struct bstat *c)
{
	return c->ok && c->ver == a->ver && c->flags == a->flags && c->seq == a->seq &&
	       c->lba == a->lba && c->lastcmd == a->lastcmd && c->sdcmds == a->sdcmds;
}

static void sd_get(uint8_t *tx, uint8_t *rx)
{
	memset(tx, 0, 514);
	tx[0] = 0x11;
	if (xfer(tx, rx, 514))
		memset(rx, 0xA5, 514);
}

static int serve_write(const struct bstat *b)
{
	static uint8_t tx[XFER_MAX], rx[XFER_MAX], rx2[XFER_MAX];
	char det[96];

	if (served_seq == b->seq && served_lba == b->lba) {
		tx[0] = 0x12;                        /* served: only the ack again (idempotent) */
		xfer(tx, rx, 1);
		return 0;
	}
	if (impl_seq == b->seq && impl_lba == b->lba)
		return 0;                            /* refused before (never acked) */
	/* a block is captured only after CMD24/25; anything else is a garbled or stale flag */
	if (!is_wcmd(b->lastcmd)) {
		wr_note(W_STALE, b, NULL);
		return 0;
	}
	if (stuck) {
		struct wreq cur = { 1, b->seq, b->sdcmds, b->lastcmd, b->lba };
		if (!proven.on || !wreq_eq(&cur, &proven)) {
			wr_note(W_STALE, b, "(stuck)");
			return now() - stuck_t < 30.0;  /* poll fast for a while: see the next command */
		}
	}
	if (!link_verified()) {
		wr_note(W_WAIT, b, NULL);
		return 0;                            /* still pending when the link is verified */
	}
	/* confirmation: one more frame, three if the request has the seq of an idle bridge or the
	 * link is on probation */
	int odd = b->seq == idle_seq;
	int need = odd || on_probation() ? 3 : 1;
	for (int i = 0; i < need; i++) {
		struct bstat c;
		bridge_status(&c);
		if (!same_req(b, &c)) {
			snprintf(det, sizeof det, "(next: ok=%d flags=%02x seq=%u lba=%u lastcmd=%u%s)",
				 c.ok, c.flags, c.seq, c.lba, c.lastcmd, odd ? " idle-seq" : "");
			wr_note(W_UNCONF, b, det);
			return 0;
		}
	}
	sd_get(tx, rx);
	sd_get(tx, rx2);
	if (memcmp(rx + 2, rx2 + 2, 512)) {
		wr_note(W_DMIS, b, NULL);
		return 1;                            /* retried on the next poll */
	}
	const uint8_t *d = rx + 2;
	if (b->lba < img_sectors) {
		for (int i = 0; i < nprot; i++)
			if (b->lba == prot[i] && (d[510] != 0x55 || d[511] != 0xAA)) {
				int z = 1;
				for (int k = 0; k < 512 && z; k++)
					z = !d[k];
				wr_note(W_IMPLAUS, b, z ? "(all zero)" : NULL);
				return 0;                    /* not acked: the core times out */
			}
		if (pwrite(img_fd, d, 512, (off_t)b->lba * 512) != 512)
			perror("pwrite");
		st.wr++;
		img_dirty = 1;
		last_write = now();
		if (b->lba == 0) {
			prot_from(d);
			logmsg("m65io: sector 0 written (seq=%u), %d boot-signature sectors\n", b->seq, nprot);
		}
	} else {
		st.wr_drop++;
	}
	tx[0] = 0x12;
	xfer(tx, rx, 1);
	served_seq = b->seq;
	served_lba = b->lba;
	wq_acked = 1;
	if (stuck) {
		logmsg("m65io: proven write capture seq=%u lba=%u served after a stuck request\n",
		       b->seq, b->lba);
		proven.on = 0;
	}
	if (verbose)
		fprintf(stderr, "wr %u\n", b->lba);
	return 1;
}

static int serve_sd(const struct bstat *b)
{
	static uint8_t tx[XFER_MAX], rx[XFER_MAX];
	uint8_t f = b->flags;

	track_req(b);
	if ((f & 0x01) && !(f & 0x22)) {                    /* read pending, unanswered */
		if (on_probation()) {                        /* a wrong lba feeds the core wrong data */
			struct bstat c;
			bridge_status(&c);
			if (!c.ok || c.seq != b->seq || c.lba != b->lba || (c.flags & 0x23) != (f & 0x23)) {
				st.rd_unconf++;
				return 1;                    /* look again on the next poll */
			}
		}
		if (b->lba >= img_sectors) {
			tx[0] = 0x13;
			xfer(tx, rx, 1);
			st.rd_err++;
			return 1;
		}
		tx[0] = 0x10;
		ssize_t n = pread(img_fd, tx + 1, 512, (off_t)b->lba * 512);
		if (n < 512)
			memset(tx + 1 + (n > 0 ? n : 0), 0, 512 - (n > 0 ? n : 0));
		xfer(tx, rx, 513);
		st.rd++;
		if (verbose)
			fprintf(stderr, "rd %u\n", b->lba);
		return 1;
	}
	if (f & 0x04)                                        /* write block captured */
		return serve_write(b);
	return 0;
}

static int listen_on(int port)
{
	int s = socket(AF_INET, SOCK_STREAM, 0), one = 1;
	struct sockaddr_in a = { .sin_family = AF_INET, .sin_port = htons(port) };
	setsockopt(s, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
	if (bind(s, (struct sockaddr *)&a, sizeof a) || listen(s, 4)) {
		perror("listen");
		exit(1);
	}
	fcntl(s, F_SETFL, O_NONBLOCK);
	return s;
}

static double now(void)
{
	struct timespec t;
	clock_gettime(CLOCK_MONOTONIC, &t);
	return t.tv_sec + t.tv_nsec * 1e-9;
}

/* ---- read cache (bridge v4): sequential read-ahead into the bridge's 16 prefetch slots, for up
 * to PF_STREAMS interleaved streams (e.g. a demo reading its video and its music file in turns).
 * A stream starts with two consecutive sector numbers (random accesses -- FAT, directories --
 * prefetch nothing) and follows the reads within its window; re-reads of a sector change nothing.
 * The slots are split among the active streams (1: 16, 2: 8 each, 3-4: 4 each); within its part
 * a stream maps sector L to slot base + L mod size and stays <= size - 2 sectors ahead, so the
 * slot being read and the one before (re-reads) are never targets (the bridge refuses to
 * overwrite the slot being read anyway, and it matches all 16 tags on every request, so data
 * left in another part by a repartition still hits).  Misses are always served first; each round
 * prefetches for the stream that is furthest behind. */
#define PF_STREAMS 4
#define PF_CHUNK 7                   /* 7 x 512 + 7 header bytes fit spidev's 4096-byte buffer */
static int pf_on;
static struct pfs {
	int used, active;            /* entry in use / two consecutive reads seen */
	uint32_t last, next;         /* last sector read, next sector to prefetch */
	unsigned long age;           /* for LRU replacement */
} pfs[PF_STREAMS];
static unsigned long pf_clock;
static uint32_t pf_seen = 0xFFFFFFFFu;

static int pf_nactive(void)
{
	int n = 0;
	for (int i = 0; i < PF_STREAMS; i++)
		n += pfs[i].active;
	return n;
}

/* the slot part of active stream number idx (0..nactive-1) */
static void pf_part(int idx, int nact, unsigned *base, unsigned *size)
{
	unsigned sz = nact <= 1 ? 16 : nact == 2 ? 8 : 4;
	*size = sz;
	*base = (idx * sz) & 15;
}

static void pf_track(uint32_t cur)
{
	int i, n0 = pf_nactive();
	pf_clock++;
	for (i = 0; i < PF_STREAMS; i++)              /* continues a known stream? */
		if (pfs[i].used && cur >= pfs[i].last && cur - pfs[i].last <= 16 + 2)
			break;
	if (i < PF_STREAMS) {
		if (cur != pfs[i].last && !pfs[i].active) {
			pfs[i].active = 1;
			pfs[i].next = cur + 1;
		}
		pfs[i].last = cur;
		pfs[i].age = pf_clock;
	} else {                                      /* new candidate: replace the LRU entry */
		int v = 0;
		for (i = 1; i < PF_STREAMS; i++)
			if (!pfs[i].used || (pfs[v].used && pfs[i].age < pfs[v].age))
				v = i;
		pfs[v].used = 1;
		pfs[v].active = 0;
		pfs[v].last = cur;
		pfs[v].next = cur + 1;
		pfs[v].age = pf_clock;
	}
	if (pf_nactive() != n0)                       /* parts changed: refill within the new windows */
		for (i = 0; i < PF_STREAMS; i++)
			if (pfs[i].active)
				pfs[i].next = pfs[i].last + 1;
}

static int prefetch_step(const struct bstat *b)
{
	if (!pf_on)
		return 0;
	if ((b->lastcmd & 0x7f) == 17 && b->lba != pf_seen) {   /* a read command we have not seen */
		pf_seen = b->lba;
		pf_track(b->lba);
	}
	if ((b->flags & 0x01) && !(b->flags & 0x22))   /* a read is waiting: serve_sd first */
		return 0;
	int nact = pf_nactive(), idx = 0, best = -1;
	unsigned bbase = 0, bsize = 16;
	uint32_t best_ahead = 0xFFFFFFFFu;
	for (int i = 0; i < PF_STREAMS; i++) {        /* the stream furthest behind its window */
		if (!pfs[i].active)
			continue;
		unsigned base, size;
		pf_part(idx++, nact, &base, &size);
		struct pfs *p = &pfs[i];
		if (p->next < p->last + 1)
			p->next = p->last + 1;
		uint32_t ahead = p->next - (p->last + 1);
		if (ahead >= size - 2 || p->next >= img_sectors)
			continue;
		if (ahead < best_ahead) {
			best_ahead = ahead;
			best = i;
			bbase = base;
			bsize = size;
		}
	}
	if (best < 0)
		return 0;
	struct pfs *p = &pfs[best];
	uint32_t n = (p->last + 1 + bsize - 2) - p->next;   /* room in the window */
	unsigned off = p->next % bsize;
	if (n > PF_CHUNK)
		n = PF_CHUNK;
	if (n > bsize - off)                                 /* stay inside the part (no wrap) */
		n = bsize - off;
	if (p->next + n > img_sectors)
		n = img_sectors - p->next;
	static uint8_t tx[XFER_MAX], rx[XFER_MAX];
	tx[0] = 0x14;
	tx[1] = bbase + off;
	tx[2] = n;
	tx[3] = p->next >> 24; tx[4] = p->next >> 16; tx[5] = p->next >> 8; tx[6] = p->next;
	ssize_t got = pread(img_fd, tx + 7, n * 512, (off_t)p->next * 512);
	if (got < (ssize_t)(n * 512))
		memset(tx + 7 + (got > 0 ? got : 0), 0, n * 512 - (got > 0 ? got : 0));
	if (xfer(tx, rx, 7 + n * 512))
		return 1;
	p->next += n;
	st.pf += n;
	st.pf_tx++;
	return 1;
}

/* long STATUS: prefetch valid bitmap and the bridge's 16-bit hit counter */
static void cache_stats(void)
{
	static int prev = -1;
	uint8_t tx[62] = { 0x01 }, rx[62];
	if (!pf_on || xfer(tx, rx, sizeof tx) || rx[2] != 0x4D || rx[3] != 0x65)
		return;
	const uint8_t *r = rx + 2;
	int h = r[56] << 8 | r[57];
	if (prev >= 0)
		st.pf_hits += (uint16_t)(h - prev);
	prev = h;
	st.pf_valid = r[54] << 8 | r[55];
}

static void write_stats(const char *path, const struct bstat *b)
{
	char tmp[256];
	snprintf(tmp, sizeof tmp, "%s.tmp", path);
	FILE *f = fopen(tmp, "w");
	if (!f)
		return;
	fprintf(f, "rd=%lu wr=%lu rd_err=%lu wr_drop=%lu mon_in=%lu mon_out=%lu "
		"bridge_down=%lu polls=%lu spi_hz=%u bridge_ver=%u flags=%u seq=%u lba=%u "
		"eth_out=%lu eth_in=%lu eth_badfcs=%lu eth_big=%lu eth_fpga_drops=%lu "
		"m65_mac=%02x:%02x:%02x:%02x:%02x:%02x pf=%lu pf_tx=%lu pf_hits=%lu pf_valid=%04x "
		"st_insane=%lu wr_refused=%lu wr_wait=%lu wr_unconf=%lu wr_dmis=%lu wr_implaus=%lu "
		"wr_stale=%lu rd_unconf=%lu inval_retry=%lu link_run=%lu link_verified=%d\n",
		st.rd, st.wr, st.rd_err, st.wr_drop, st.mon_in, st.mon_out,
		st.bridge_down, st.polls, spi_hz, b->ver, b->flags, b->seq, b->lba,
		st.eth_out, st.eth_in, st.eth_badfcs, st.eth_big, st.eth_fpga_drops,
		m65_mac[0], m65_mac[1], m65_mac[2], m65_mac[3], m65_mac[4], m65_mac[5],
		st.pf, st.pf_tx, st.pf_hits, st.pf_valid,
		st.st_insane, st.wr_refused, st.wr_wait, st.wr_unconf, st.wr_dmis, st.wr_implaus,
		st.wr_stale, st.rd_unconf, st.inval_retry, good_run, link_verified());
	fclose(f);
	rename(tmp, path);
}

/* SIGUSR1 (2026-11-12): the card image was changed from the module side (loop mount, mtools):
 * drop the bridge's read cache, else the core may read stale copies of the sectors it read last
 * (the FAT, a directory, a settings file) -- e.g. kill -USR1 $(pidof m65io) after the umount. */
static volatile sig_atomic_t inval_req;
static void on_usr1(int sig) { (void)sig; inval_req = 1; }

/* SD_INVAL owed to the bridge (start, SIGUSR1, a new bitstream): sent while the link is up and
 * checked in the long STATUS (valid bitmap 0), else sent again -- an invalidate sent while the
 * FPGA is down or being configured would be lost and leave stale sectors in the cache */
static int inval_pending;
static void inval_step(void)
{
	uint8_t tx[62] = { 0x15 }, rx[62];
	xfer(tx, rx, 1);
	memset(pfs, 0, sizeof pfs);
	pf_seen = 0xFFFFFFFFu;
	tx[0] = 0x01;
	if (!xfer(tx, rx, sizeof tx) && frame_sane(rx) && rx[2 + 54] == 0 && rx[2 + 55] == 0) {
		inval_pending = 0;
		return;
	}
	st.inval_retry++;
}

int main(int argc, char **argv)
{
	const char *dev = "/dev/spidev3.0", *image = "/root/m65/card.img";
	const char *stats = "/run/m65io.stats";   /* RAM: rewritten twice a second */
	const char *ethif = NULL;
	int port = 4510, opt;

	while ((opt = getopt(argc, argv, "d:s:i:p:S:e:v")) != -1) {
		switch (opt) {
		case 'd': dev = optarg; break;
		case 's': spi_hz = strtoul(optarg, NULL, 0); break;
		case 'i': image = optarg; break;
		case 'p': port = atoi(optarg); break;
		case 'S': stats = optarg; break;
		case 'v': verbose = 1; break;
		case 'e': ethif = optarg; break;
		default:
			fprintf(stderr, "usage: m65io [-d dev] [-s hz] [-i image] [-p port] [-S stats] [-e eth0|none] [-v]\n");
			return 2;
		}
	}
	signal(SIGPIPE, SIG_IGN);
	signal(SIGUSR1, on_usr1);
	if (!ethif)
		ethif = eth_default();

	spi_fd = open(dev, O_RDWR);
	if (spi_fd < 0) { perror(dev); return 1; }
	uint8_t mode = SPI_MODE_0, bits = 8;
	ioctl(spi_fd, SPI_IOC_WR_MODE, &mode);
	ioctl(spi_fd, SPI_IOC_WR_BITS_PER_WORD, &bits);
	ioctl(spi_fd, SPI_IOC_WR_MAX_SPEED_HZ, &spi_hz);
	spi_hz_want = spi_hz;

	img_fd = open(image, O_RDWR);
	if (img_fd < 0) { perror(image); return 1; }
	struct stat sb;
	fstat(img_fd, &sb);
	img_sectors = sb.st_size / 512;

	struct bstat b;
	link_event();                            /* no write before the link is verified */
	link_select(spi_hz_want);
	bridge_status(&b);
	logmsg("m65io: %s at %u Hz, image %s (%llu sectors), bridge %s v%u\n", dev, spi_hz,
	       image, (unsigned long long)img_sectors, b.ok ? "found" : "NOT found", b.ver);
	if (b.ok && b.ver >= 4) {
		pf_on = 1;                           /* SD_INVAL: nothing cached from a previous image */
		inval_pending = 1;
		logmsg("m65io: read cache on (%d streams, 16 slots)\n", PF_STREAMS);
	}
	prot_load();

	crc_init();
	int ifindex = 0, pkt = -1;
	if (strcmp(ethif, "none"))
		pkt = eth_open(ethif, &ifindex);
	logmsg("m65io: ethernet bridge %s %s\n", ethif, pkt >= 0 ? "up" : "off");

	int srv = listen_on(port), cli[MAX_CLIENTS], ncli = 0;
	static uint8_t txq[65536];
	size_t txq_len = 0;
	double next_stats = 0, last_activity = 0;
	double down_since = 0, next_probe = 0, glitch_t[3] = { 0 };
	int was_ok = b.ok, glitch_i = 0;
	uint32_t probe_max = spi_hz_want;
	double next_up = now() + 5.0, up_every = 5.0;  /* link_upgrade() schedule, backs off on dropouts */

	for (;;) {
		if (inval_req) {
			inval_req = 0;
			if (pf_on)
				inval_pending = 1;           /* SD_INVAL as soon as the link is up */
			prot_load();                         /* the partition table may have changed */
			logmsg("m65io: read cache dropped (SIGUSR1: card image changed)\n");
		}
		int busy = 0;
		bridge_status(&b);
		st.polls++;
		if (!b.ok) {
			/* FPGA being reprogrammed / other bitstream (MISO floats), or a clock this
			 * bitstream's MISO cannot follow.  Act on nothing from the bridge, keep the
			 * sockets and the stats alive, and look for a clock that works. */
			double t = now();
			st.bridge_down++;
			if (was_ok) {
				down_since = t;
				glitch_t[glitch_i] = t;              /* 3 dropouts in 10 s: marginal clock */
				glitch_i = (glitch_i + 1) % 3;
				if (glitch_t[glitch_i] && t - glitch_t[glitch_i] < 10.0) {
					logmsg("m65io: repeated link dropouts at %u Hz\n", spi_hz);
					next_probe = 0;
					probe_max = spi_hz - 1;      /* only slower rates now */
					if (up_every < 3600) up_every *= 4;   /* and fewer retries up */
					memset(glitch_t, 0, sizeof glitch_t);
				}
			}
			was_ok = 0;
			if ((t - down_since > 0.5 || next_probe == 0) && t >= next_probe) {
				link_select(probe_max);
				probe_max = spi_hz_want;         /* next outage (core switch): try all */
				/* 2026-11-12: every 0.1 s for the first 10 s of an outage, then every 2 s.
				 * A freshly loaded core (JTAG, core switch) reads its SD card early: the
				 * AExp core's QNICE reads its settings file 2 s after its start, once.  With
				 * a 2-s probe the card came back too late about every other time. */
				next_probe = t + (t - down_since < 10.0 ? 0.1 : 2.0);
				next_up = t + up_every;
			}
			usleep(50000);
			goto pace;
		}
		if (!was_ok && pf_on != (b.ver >= 4)) {
			/* The bridge (re)appeared with another version than m65io decided on at its
			 * start (e.g. m65io started under a design without the bridge, then a v4
			 * bitstream was loaded): switch the read cache to match. */
			pf_on = b.ver >= 4;
			memset(pfs, 0, sizeof pfs);
			pf_seen = 0xFFFFFFFFu;
			inval_pending = pf_on;
			logmsg("m65io: bridge v%u, read cache %s\n", b.ver, pf_on ? "on" : "off");
		}
		if (!was_ok && down_since > 0)
			logmsg("m65io: bridge back after %.1f s\n", now() - down_since);
		was_ok = 1;
		if (spi_hz < spi_hz_want && now() >= next_up) {
			link_upgrade();
			next_up = now() + up_every;
			continue;                            /* re-read the status at the new rate */
		}
		if (inval_pending && pf_on && good_run >= GOOD_RUN_MIN)
			inval_step();
		busy |= serve_sd(&b);
		busy |= prefetch_step(&b);
		if (pkt >= 0 && b.ver >= 3)
			busy |= serve_eth(&b, pkt, ifindex);

		/* monitor output -> TCP clients */
		if (b.rxcnt) {
			static uint8_t tx[XFER_MAX], rx[XFER_MAX];
			unsigned n = b.rxcnt > XFER_MAX - 2 ? XFER_MAX - 2 : b.rxcnt;
			memset(tx, 0, n + 2);
			tx[0] = 0x21;
			if (!xfer(tx, rx, n + 2)) {
				st.mon_out += n;
				for (int i = 0; i < ncli; i++)
					if (send(cli[i], rx + 2, n, MSG_NOSIGNAL) < 0) {
						close(cli[i]);
						cli[i--] = cli[--ncli];
					}
			}
			busy = 1;
		}
		/* TCP input -> monitor */
		if (txq_len && b.txfree) {
			static uint8_t tx[XFER_MAX], rx[XFER_MAX];
			unsigned n = txq_len;
			if (n > b.txfree) n = b.txfree;
			if (n > XFER_MAX - 1) n = XFER_MAX - 1;
			tx[0] = 0x20;
			memcpy(tx + 1, txq, n);
			if (!xfer(tx, rx, n + 1)) {
				memmove(txq, txq + n, txq_len - n);
				txq_len -= n;
				st.mon_in += n;
			}
			busy = 1;
		}

		/* Poll pacing.  Latency matters while sectors flow, CPU/heat when idle:
		 *   < SPIN_S since the last activity : spin (no sleep), a new CMD17 is
		 *                                      answered within one status poll
		 *   < NAP_S                          : poll every NAP_US
		 *   otherwise                        : poll every DOZE_US
		 * The first sector after a long idle waits <= 10 ms (HYPPO allows ~0.4 s);
		 * monitor input still wakes us at once (ppoll on the sockets); monitor
		 * output from the MEGA65 buffers in the bridge's 16 KB FIFO (~2 KB / 10 ms
		 * at 2 Mbaud). */
pace:	;
		double tnow = now();
		if (busy)
			last_activity = tnow;
		double idle = tnow - last_activity;
		long sleep_us = idle < SPIN_S ? 0 : idle < NAP_S ? NAP_US : DOZE_US;
		struct pollfd p[MAX_CLIENTS + 2];
		p[0].fd = srv; p[0].events = POLLIN;
		for (int i = 0; i < ncli; i++) { p[i + 1].fd = cli[i]; p[i + 1].events = POLLIN; }
		/* LAN frames wake us too (only to forward; the loop above reads them) */
		p[ncli + 1].fd = pkt; p[ncli + 1].events = POLLIN;
		struct timespec ts = { 0, sleep_us * 1000 };
		int npolled = ncli;   /* the clients in p[1..]: a client accepted below has no entry */
		if (ppoll(p, ncli + (pkt >= 0 ? 2 : 1), &ts, NULL) > 0) {
			if (p[0].revents & POLLIN) {
				int c = accept(srv, NULL, NULL), one = 1;
				if (c >= 0 && ncli < MAX_CLIENTS) {
					setsockopt(c, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one);
					cli[ncli++] = c;
				} else if (c >= 0)
					close(c);
			}
			for (int i = npolled - 1; i >= 0; i--) {
				if (!(p[i + 1].revents & (POLLIN | POLLHUP | POLLERR)))
					continue;
				ssize_t n = recv(cli[i], txq + txq_len, sizeof txq - txq_len, 0);
				if (n <= 0) {
					close(cli[i]);
					cli[i] = cli[--ncli];
				} else
					txq_len += n;
			}
		}

		double t = now();
		if (img_dirty && t - last_write >= SYNC_IDLE_S) {
			fdatasync(img_fd);
			img_dirty = 0;
		}
		if (t >= next_stats) {
			if (pkt >= 0 && gro_off_done < 0)
				eth_gro_off(pkt, ethif);
			cache_stats();
			write_stats(stats, &b);
			next_stats = t + 0.5;
		}
	}
}
