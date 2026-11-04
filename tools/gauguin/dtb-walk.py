import struct
import sys

d = open(sys.argv[1] if len(sys.argv) > 1 else r'D:\hackios\ios-sweet\gauguin\dtb', 'rb').read()
magic, totalsize, off_struct, off_strings, off_mem, version, lastcomp = struct.unpack_from('>7I', d, 0)

pos = off_struct
path = []
out = []
while pos < len(d):
    token, = struct.unpack_from('>I', d, pos)
    if token == 1:
        end = d.index(b'\x00', pos + 4)
        name = d[pos + 4:end].decode('ascii', 'replace')
        path.append(name)
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
    elif token == 9:  # FDT_END
        break
    else:
        print('unknown token', hex(token), 'at', pos, file=sys.stderr)
        break

kw = sys.argv[2].lower() if len(sys.argv) > 2 else None
for p, name, val in out:
    s = p + ' :: ' + name
    if kw is None or kw in s.lower():
        if isinstance(val, bytes):
            printable = val[:60].split(b'\x00')[0]
            try:
                txt = printable.decode()
            except UnicodeDecodeError:
                txt = val[:24].hex()
        print(f'{s} = {txt}')
