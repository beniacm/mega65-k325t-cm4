#!/usr/bin/env python3
"""test_m65hdd.py -- m65hdd.py load / save on a sparse image (PC; LXSHELL_TMP = scratch directory)"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))


def run(env, *a):
    return subprocess.run([sys.executable, os.path.join(HERE, 'm65hdd.py')] + list(a), env=env,
                          capture_output=True, text=True, check=True).stdout


def main():
    with tempfile.TemporaryDirectory(dir=os.environ.get('LXSHELL_TMP')) as t:
        sto, ram = os.path.join(t, 'stored.hdf'), os.path.join(t, 'ram.hdf')
        env = dict(os.environ, M65_HDD_IMAGE=sto, M65_HDD_RAM=ram)
        with open(sto, 'wb') as f:
            f.truncate(64 << 20)
            f.seek(5 << 20)
            f.write(os.urandom(300000))
        assert 'nothing in RAM' in run(env, 'save')
        assert 'loaded' in run(env, 'load')
        assert open(ram, 'rb').read() == open(sto, 'rb').read()
        assert os.stat(ram).st_blocks * 512 < 1 << 20, 'holes not kept'
        assert 'kept' in run(env, 'load')                        # a restarted server keeps RAM
        m0 = os.stat(sto).st_mtime_ns
        assert 'nothing written' in run(env, 'save') and os.stat(sto).st_mtime_ns == m0
        fd = os.open(ram, os.O_WRONLY)
        os.pwrite(fd, b'AMIGA' * 100, (5 << 20) + 70000)
        os.pwrite(fd, b'NEW' * 10, (60 << 20) + 3)                # into a hole of the stored image
        os.close(fd)
        assert '2 changed' in run(env, 'status') and os.stat(sto).st_mtime_ns == m0
        assert 'saved 2 changed' in run(env, 'save')
        assert open(ram, 'rb').read() == open(sto, 'rb').read()
        assert 'nothing written' in run(env, 'save')
    print('PASS m65hdd: load keeps holes, save writes only the changed blocks, nothing when unchanged')


if __name__ == '__main__':
    main()
