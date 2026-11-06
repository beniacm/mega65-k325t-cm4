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
 *               (read by m65d.py's "status" command).
 *
 * The control side (typing, keys, joysticks, reset) stays in m65d.py; both
 * processes share /dev/spidev3.0 -- every ioctl is one complete CS transaction
 * and the kernel serialises them.
 *
 * Build:  gcc -O2 -Wall -o m65io m65io.c
 * Run:    m65io [-d /dev/spidev3.0] [-s hz] [-i card.img] [-p 4510] [-S stats]
 *               [-e eth0|none] [-v]
 */
#define _GNU_SOURCE
#include <arpa/inet.h>
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
static uint32_t spi_hz = 25000000;   /* verified clean; 33 MHz fails (linktest.py) */
static uint64_t img_sectors;
static struct {
	unsigned long rd, wr, rd_err, wr_drop, mon_in, mon_out, bridge_down, polls;
	unsigned long eth_out, eth_in, eth_badfcs, eth_big, eth_fpga_drops;
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

static int eth_open(const char *ifname, int *ifindex)
{
	int s = socket(AF_PACKET, SOCK_RAW, htons(ETH_P_ALL));
	if (s < 0) { perror("AF_PACKET socket"); return -1; }
	struct ifreq ifr;
	memset(&ifr, 0, sizeof ifr);
	strncpy(ifr.ifr_name, ifname, IFNAMSIZ - 1);
	if (ioctl(s, SIOCGIFINDEX, &ifr) < 0) { perror(ifname); close(s); return -1; }
	*ifindex = ifr.ifr_ifindex;
	struct sockaddr_ll sll = { .sll_family = AF_PACKET, .sll_protocol = htons(ETH_P_ALL),
				   .sll_ifindex = *ifindex };
	if (bind(s, (struct sockaddr *)&sll, sizeof sll) < 0) { perror("bind"); close(s); return -1; }
	struct packet_mreq mr = { .mr_ifindex = *ifindex, .mr_type = PACKET_MR_PROMISC };
	setsockopt(s, SOL_PACKET, PACKET_ADD_MEMBERSHIP, &mr, sizeof mr);
	/* GRO would hand us coalesced super-frames; best effort to switch it off
	 * (no ethtool binary on the module). */
	struct ethtool_value ev = { .cmd = ETHTOOL_SGRO, .data = 0 };
	ifr.ifr_data = (void *)&ev;
	if (ioctl(s, SIOCETHTOOL, &ifr) < 0)
		perror("GRO off (ignored)");
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
		"m65_mac=%02x:%02x:%02x:%02x:%02x:%02x\n",
		st.rd, st.wr, st.rd_err, st.wr_drop, st.mon_in, st.mon_out,
		st.bridge_down, st.polls, spi_hz, b->ver, b->flags, b->seq, b->lba,
		st.eth_out, st.eth_in, st.eth_badfcs, st.eth_big, st.eth_fpga_drops,
		m65_mac[0], m65_mac[1], m65_mac[2], m65_mac[3], m65_mac[4], m65_mac[5]);
	fclose(f);
	rename(tmp, path);
}

int main(int argc, char **argv)
{
	const char *dev = "/dev/spidev3.0", *image = "/root/m65/card.img";
	const char *stats = "/root/m65/m65io.stats";
	const char *ethif = "eth0";
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

	spi_fd = open(dev, O_RDWR);
	if (spi_fd < 0) { perror(dev); return 1; }
	uint8_t mode = SPI_MODE_0, bits = 8;
	ioctl(spi_fd, SPI_IOC_WR_MODE, &mode);
	ioctl(spi_fd, SPI_IOC_WR_BITS_PER_WORD, &bits);
	ioctl(spi_fd, SPI_IOC_WR_MAX_SPEED_HZ, &spi_hz);

	img_fd = open(image, O_RDWR);
	if (img_fd < 0) { perror(image); return 1; }
	struct stat sb;
	fstat(img_fd, &sb);
	img_sectors = sb.st_size / 512;

	struct bstat b;
	bridge_status(&b);
	printf("m65io: %s at %u Hz, image %s (%llu sectors), bridge %s v%u\n", dev, spi_hz,
	       image, (unsigned long long)img_sectors, b.ok ? "found" : "NOT found", b.ver);
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

	for (;;) {
		int busy = 0;
		bridge_status(&b);
		st.polls++;
		if (!b.ok) {
			/* FPGA being reprogrammed / other bitstream: MISO floats, every
			 * field would read 0xFF.  Act on nothing. */
			st.bridge_down++;
			usleep(50000);
			continue;
		}
		busy |= serve_sd(&b);
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
		if (t >= next_stats) {
			write_stats(stats, &b);
			next_stats = t + 0.5;
		}
	}
}
