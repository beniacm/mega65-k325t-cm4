"""simlink.py -- hostbus's link interface over the Verilator bench's host link (PROTOCOL.md 6a).

The bench (K325T/sim/aga, --host-link REQ RSP) reads one request line from REQ and answers one
line on RSP: S (STATUS, 82 bytes hex), P <hex> (HOST_PUT), G <n> (HOST_GET), R (HOST_RESET),
W <ticks> (run the machine; answer "OK <frame> <tick>"), Q (quit).  The simulation runs only
inside W, so this link advances it by the time each transaction would take on the wire (33 MHz
SPI, ~46 us per transfer, PROTOCOL.md section 6) and keeps the bench's time, which the shell then
uses as its clock (now()).
"""

from lxcore import bus as hostbus

TICK_HZ = 28375000.0                # machine ticks per second (28.375 MHz)
SPI_HZ = 33000000.0
XFER_OVERHEAD_S = 46e-6


class SimLink:
    def __init__(self, req_path, rsp_path):
        self.wq = open(req_path, 'w')
        self.rq = open(rsp_path, 'r')
        self.tick = 0
        self.frame = 0
        self.wire_s = 0.0           # wire time not yet given to the bench

    def tx(self, line):
        self.wq.write(line + '\n')
        self.wq.flush()
        r = self.rq.readline()
        if not r:
            raise hostbus.BusError('bench link closed')
        return r.strip()

    def _wire(self, nbytes):
        self.wire_s += XFER_OVERHEAD_S + nbytes * 8 / SPI_HZ
        if self.wire_s * TICK_HZ >= 200:
            self.run(self.wire_s)

    def run(self, seconds):
        n = max(1, int(round(seconds * TICK_HZ)))
        r = self.tx('W %d' % n).split()
        if r[0] != 'OK':
            raise hostbus.BusError('bench W: %s' % r)
        self.frame, self.tick = int(r[1]), int(r[2])
        self.wire_s = 0.0

    def now(self):
        return (self.tick + self.wire_s * TICK_HZ) / TICK_HZ

    # ---- the link interface (hostbus.SpiLink)
    def status(self):
        self._wire(84)
        return hostbus.HostStatus(bytes.fromhex(self.tx('S')))

    def put(self, data):
        self._wire(1 + len(data))
        if self.tx('P ' + bytes(data).hex()) != 'OK':
            raise hostbus.BusError('bench P refused')

    def get(self, n):
        self._wire(2 + n)
        return bytes.fromhex(self.tx('G %d' % n))

    def reset(self):
        self._wire(1)
        if self.tx('R') != 'OK':
            raise hostbus.BusError('bench R refused')

    def quit(self):
        try:
            self.wq.write('Q\n')
            self.wq.flush()
        except OSError:
            pass
