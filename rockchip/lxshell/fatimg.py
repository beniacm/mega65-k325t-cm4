"""fatimg.py -- read-only access to files on a FAT12/16/32 image (the module's card.img).

Only what the shell needs to take over an existing card: list a directory and read a file, by
path, case-insensitive, long file names included.  Partition 1 of an MBR is used if there is one,
else the image is a "superfloppy".  Nothing is ever written.
"""

import struct


class FatError(Exception):
    pass


class FatImage:
    def __init__(self, path):
        self.f = open(path, 'rb')
        mbr = self._read(0, 512)
        base = 0
        if mbr[510:512] == b'\x55\xaa' and mbr[0] not in (0xEB, 0xE9):
            ptype = mbr[0x1BE + 4]
            if ptype:
                base = struct.unpack_from('<I', mbr, 0x1BE + 8)[0] * 512
        self.base = base
        bs = self._read(base, 512)
        self.bps = struct.unpack_from('<H', bs, 11)[0]
        self.spc = bs[13]
        reserved = struct.unpack_from('<H', bs, 14)[0]
        nfats = bs[16]
        self.root_entries = struct.unpack_from('<H', bs, 17)[0]
        total = struct.unpack_from('<H', bs, 19)[0] or struct.unpack_from('<I', bs, 32)[0]
        fatsz = struct.unpack_from('<H', bs, 22)[0] or struct.unpack_from('<I', bs, 36)[0]
        if self.bps not in (512, 1024, 2048, 4096) or not self.spc:
            raise FatError('no FAT file system')
        self.fat_off = base + reserved * self.bps
        root_secs = (self.root_entries * 32 + self.bps - 1) // self.bps
        self.root_off = self.fat_off + nfats * fatsz * self.bps
        self.data_off = self.root_off + root_secs * self.bps
        clusters = (total - reserved - nfats * fatsz - root_secs) // self.spc
        self.kind = 12 if clusters < 4085 else 16 if clusters < 65525 else 32
        self.root_cluster = struct.unpack_from('<I', bs, 44)[0] if self.kind == 32 else None
        self.csize = self.spc * self.bps

    def _read(self, off, n):
        self.f.seek(off)
        return self.f.read(n)

    def _next(self, c):
        if self.kind == 32:
            return struct.unpack('<I', self._read(self.fat_off + c * 4, 4))[0] & 0x0FFFFFFF
        if self.kind == 16:
            return struct.unpack('<H', self._read(self.fat_off + c * 2, 2))[0]
        v = struct.unpack('<H', self._read(self.fat_off + c * 3 // 2, 2))[0]
        return v >> 4 if c & 1 else v & 0xFFF

    def _eoc(self, c):
        return c >= {12: 0xFF8, 16: 0xFFF8, 32: 0x0FFFFFF8}[self.kind] or c < 2

    def _chain(self, c):
        seen = set()
        while not self._eoc(c) and c not in seen:
            seen.add(c)
            yield c
            c = self._next(c)

    def _dir_bytes(self, cluster):
        if cluster is None:                     # FAT12/16 root directory
            return self._read(self.root_off, self.root_entries * 32)
        return b''.join(self._read(self.data_off + (c - 2) * self.csize, self.csize)
                        for c in self._chain(cluster))

    def listdir(self, cluster):
        """[(name, is_dir, first_cluster, size)]"""
        raw = self._dir_bytes(cluster)
        out, lfn = [], {}
        for i in range(0, len(raw), 32):
            e = raw[i:i + 32]
            if e[0] == 0:
                break
            if e[0] == 0xE5:
                lfn = {}
                continue
            attr = e[11]
            if attr == 0x0F:
                seq = e[0] & 0x1F
                part = (e[1:11] + e[14:26] + e[28:32]).decode('utf-16-le', 'replace')
                lfn[seq] = part.split('\x00')[0]
                continue
            if attr & 0x08:
                lfn = {}
                continue
            short = e[0:8].decode('latin-1').rstrip()
            ext = e[8:11].decode('latin-1').rstrip()
            name = ''.join(lfn[k] for k in sorted(lfn)) if lfn else (short + ('.' + ext if ext else ''))
            lfn = {}
            hi = struct.unpack_from('<H', e, 20)[0] if self.kind == 32 else 0
            first = hi << 16 | struct.unpack_from('<H', e, 26)[0]
            out.append((name, bool(attr & 0x10), first, struct.unpack_from('<I', e, 28)[0]))
        return out

    def _lookup(self, path):
        cluster = self.root_cluster
        parts = [p for p in path.split('/') if p]
        entry = (None, True, cluster, 0)
        for i, p in enumerate(parts):
            for e in self.listdir(cluster):
                if e[0].upper() == p.upper():
                    entry = e
                    break
            else:
                raise FileNotFoundError(path)
            cluster = entry[2] or self.root_cluster
            if i < len(parts) - 1 and not entry[1]:
                raise FileNotFoundError(path)
        return entry

    def read(self, path, limit=64 << 20):
        name, is_dir, first, size = self._lookup(path)
        if is_dir:
            raise IsADirectoryError(path)
        if size > limit:
            raise FatError('%s: %d bytes is too large' % (path, size))
        data = bytearray()
        for c in self._chain(first):
            data += self._read(self.data_off + (c - 2) * self.csize, self.csize)
            if len(data) >= size:
                break
        return bytes(data[:size])

    def list(self, path):
        name, is_dir, first, size = self._lookup(path)
        if not is_dir:
            raise NotADirectoryError(path)
        return [(n, d, s) for n, d, c, s in self.listdir(first or self.root_cluster) if n not in ('.', '..')]

    def close(self):
        self.f.close()
