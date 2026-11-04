# -*- coding: utf-8 -*-
"""Decode gpu_region reg + kgsl zap-shader linkage from the real gauguin DTB."""
import struct

d = open(r'D:\hackios\ios-sweet\gauguin\dtb', 'rb').read()
magic, totalsize, off_struct, off_strings, off_mem, version, lastcomp = struct.unpack_from('>7I', d, 0)

pos = off_struct
path = []
out = []
while pos < len(d):
    token, = struct.unpack_from('>I', d, pos)
    if token == 1:
        end = d.index(b'\x00', pos + 4)
        path.append(d[pos + 4:end].decode('ascii', 'replace'))
        pos = (end + 1 + 3) & ~3
    elif token == 2:
        if path:
            path.pop()
        pos += 4
    elif token == 3:
        ln, noff = struct.unpack_from('>II', d, pos + 4)
        name = d[off_strings + noff: d.index(b'\x00', off_strings + noff)].decode()
        val = d[pos + 12:pos + 12 + ln]
        out.append(('/'.join(path), name, val))
        pos = (pos + 12 + ln + 3) & ~3
    elif token == 4:
        pos += 4
    elif token == 9:
        break

phandles = {}
for p, name, val in out:
    if name == 'phandle':
        phandles[struct.unpack('>I', val[:4])[0]] = p

for p, name, val in out:
    if 'gpu_region' in p and name in ('reg', 'phandle'):
        if name == 'reg':
            print('gpu_region reg =', val.hex(), '->', ['0x%x' % x for x in struct.unpack('>QQ', val)])
        else:
            print('gpu_region phandle =', struct.unpack('>I', val[:4])[0])
    if 'zap' in p.lower():
        print('ZAP NODE:', p, '::', name, '=', val.hex() if name == 'memory-region' else val[:40])
        if name == 'memory-region':
            ph = struct.unpack('>I', val[:4])[0]
            print('   -> memory-region phandle %d = %s' % (ph, phandles.get(ph)))

# any pas-id / qcom,msm-id / scm firmware hints
for p, name, val in out:
    if name in ('qcom,pas-id', 'pas-id', 'qcom,firmware') or 'pas' in name.lower():
        print('PAS HINT:', p, '::', name, '=', val.hex())
