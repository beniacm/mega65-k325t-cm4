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
} st;
static uint8_t m65_mac[6];
static int m65_mac_known;

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

static void bridge_status(struct bstat *b)
{
	uint8_t tx[22] = { 0x01 }, rx[22];
	b->ok = 0;
	if (xfer(tx, rx, sizeof tx))
		return;
	const uint8_t *r = rx + 2;
	b->ok = (r[0] == 0x4D && r[1] == 0x65);
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
		spi_hz = hz;
		/* One failed read must not demote a good rate for the whole session (2026-11-10: a
		 * bitstream with 0 errors in 9000 raw pairs at 40 MHz started at 25 MHz because the
		 * probe ran during the core's start-up).  A marginal link fails the second pass too. */
		if (link_ok(200) || (usleep(50000), link_ok(200))) {
			if (hz != old || hz != spi_hz_want)
				printf("m65io: SPI link at %u Hz%s\n", hz,
				       hz != spi_hz_want ? " (requested rate unreliable with this bitstream)" : "");
			fflush(stdout);
			return 1;
		}
	}
	spi_hz = spi_hz_want;                  /* no bridge answering at any rate (yet) */
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
		spi_hz = hz;
		if (link_ok(200)) {
			printf("m65io: SPI link back up to %u Hz\n", hz);
			fflush(stdout);
			return;
		}
	}
	spi_hz = old;
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
		printf("m65io: %s GRO off\n", ifname);
	else if (!ok && gro_off_done == 0)
		printf("m65io: %s GRO off failed (%s), retrying\n", ifname, strerror(errno));
	gro_off_done = ok ? 1 : -1;
	fflush(stdout);
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

static int serve_sd(const struct bstat *b)
{
	static uint8_t tx[XFER_MAX], rx[XFER_MAX];
	uint8_t f = b->flags;

	if ((f & 0x01) && !(f & 0x22)) {                    /* read pending, unanswered */
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
	if (f & 0x04) {                                      /* write block captured */
		memset(tx, 0, 514);
		tx[0] = 0x11;
		xfer(tx, rx, 514);
		if (b->lba < img_sectors) {
			if (pwrite(img_fd, rx + 2, 512, (off_t)b->lba * 512) != 512)
				perror("pwrite");
			st.wr++;
			img_dirty = 1;
			last_write = now();
		} else {
			st.wr_drop++;
		}
		tx[0] = 0x12;
		xfer(tx, rx, 1);
		if (verbose)
			fprintf(stderr, "wr %u\n", b->lba);
		return 1;
	}
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
		"m65_mac=%02x:%02x:%02x:%02x:%02x:%02x pf=%lu pf_tx=%lu pf_hits=%lu pf_valid=%04x\n",
		st.rd, st.wr, st.rd_err, st.wr_drop, st.mon_in, st.mon_out,
		st.bridge_down, st.polls, spi_hz, b->ver, b->flags, b->seq, b->lba,
		st.eth_out, st.eth_in, st.eth_badfcs, st.eth_big, st.eth_fpga_drops,
		m65_mac[0], m65_mac[1], m65_mac[2], m65_mac[3], m65_mac[4], m65_mac[5],
		st.pf, st.pf_tx, st.pf_hits, st.pf_valid);
	fclose(f);
	rename(tmp, path);
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
	link_select(spi_hz_want);
	bridge_status(&b);
	printf("m65io: %s at %u Hz, image %s (%llu sectors), bridge %s v%u\n", dev, spi_hz,
	       image, (unsigned long long)img_sectors, b.ok ? "found" : "NOT found", b.ver);
	if (b.ok && b.ver >= 4) {
		uint8_t tx[1] = { 0x15 }, rx[1];      /* SD_INVAL: nothing cached from a previous image */
		xfer(tx, rx, 1);
		pf_on = 1;
		printf("m65io: read cache on (%d streams, 16 slots)\n", PF_STREAMS);
	}
	fflush(stdout);

	crc_init();
	int ifindex = 0, pkt = -1;
	if (strcmp(ethif, "none"))
		pkt = eth_open(ethif, &ifindex);
	printf("m65io: ethernet bridge %s %s\n", ethif, pkt >= 0 ? "up" : "off");
	fflush(stdout);

	int srv = listen_on(port), cli[MAX_CLIENTS], ncli = 0;
	static uint8_t txq[65536];
	size_t txq_len = 0;
	double next_stats = 0, last_activity = 0;
	double down_since = 0, next_probe = 0, glitch_t[3] = { 0 };
	int was_ok = b.ok, glitch_i = 0;
	uint32_t probe_max = spi_hz_want;
	double next_up = now() + 5.0, up_every = 5.0;  /* link_upgrade() schedule, backs off on dropouts */

	for (;;) {
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
					printf("m65io: repeated link dropouts at %u Hz\n", spi_hz);
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
				next_probe = t + 2.0;
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
			if (pf_on) {
				uint8_t tx[1] = { 0x15 }, rx[1];   /* SD_INVAL */
				xfer(tx, rx, 1);
			}
			printf("m65io: bridge v%u, read cache %s\n", b.ver, pf_on ? "on" : "off");
			fflush(stdout);
		}
		was_ok = 1;
		if (spi_hz < spi_hz_want && now() >= next_up) {
			link_upgrade();
			next_up = now() + up_every;
			continue;                            /* re-read the status at the new rate */
		}
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
		if (ppoll(p, ncli + (pkt >= 0 ? 2 : 1), &ts, NULL) > 0) {
			if (p[0].revents & POLLIN) {
				int c = accept(srv, NULL, NULL), one = 1;
				if (c >= 0 && ncli < MAX_CLIENTS) {
					setsockopt(c, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one);
					cli[ncli++] = c;
				} else if (c >= 0)
					close(c);
			}
			for (int i = ncli - 1; i >= 0; i--) {
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
