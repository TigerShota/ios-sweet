#!/usr/bin/env python3
"""Pack a directory tree into the archive the gauguin kernel installer reads.

Usage: mkinject.py SRC_DIR OUT [--mode 0644]

Paths are stored relative to SRC_DIR (no leading slash). Format (little endian):
  0x00  u32 magic = 0x544a4e49 ("INJT")
  0x04  u32 version = 1
  0x08  u32 file_count
  0x0c  u32 total_size
  each entry (8-byte aligned):
    u32 path_len, u32 mode, u32 uid, u32 gid
    u64 size, u32 crc32 (FNV-1a), u32 pad
    path bytes, data bytes, pad to 8
"""
import argparse
import os
import struct


def fnv1a(data):
    h = 2166136261
    for b in data:
        h = ((h ^ b) * 16777619) & 0xffffffff
    return h


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('src')
    ap.add_argument('out')
    ap.add_argument('--mode', type=lambda s: int(s, 8), default=0o644)
    a = ap.parse_args()

    files = []
    for root, dirs, names in os.walk(a.src):
        dirs.sort()
        for n in sorted(names):
            p = os.path.join(root, n)
            rel = os.path.relpath(p, a.src).replace(os.sep, '/')
            files.append((rel, open(p, 'rb').read()))

    body = bytearray()
    for rel, data in files:
        path = rel.encode()
        body += struct.pack('<4IQII', len(path), a.mode, 0, 0,
                            len(data), fnv1a(data), 0)
        body += path + data
        body += b'\0' * (-len(body) & 7)

    out = bytearray(16)
    struct.pack_into('<4I', out, 0, 0x544a4e49, 1, len(files), 16 + len(body))
    out += body
    open(a.out, 'wb').write(out)
    print('inject: %d file(s), %d bytes -> %s' % (len(files), len(out), a.out))


if __name__ == '__main__':
    main()
