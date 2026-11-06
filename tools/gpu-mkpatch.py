import difflib
import sys

OLD = r'd:\hackios\temp\gpu-0122\bsd\dev\gauguin_gpu.c'
NEW = r'd:\hackios\temp\gpu-0123\bsd\dev\gauguin_gpu.c'
OUT = r'd:\hackios\ios-sweet\xnu\patches\0123-gauguin-gpu-panel-blit.patch'

a = open(OLD, 'rb').read().decode('utf-8', 'replace').split('\n')
b = open(NEW, 'rb').read().decode('utf-8', 'replace').split('\n')
a = [l.rstrip('\r') for l in a]
b = [l.rstrip('\r') for l in b]

diff = list(difflib.unified_diff(a, b, fromfile='a/bsd/dev/gauguin_gpu.c',
                                 tofile='b/bsd/dev/gauguin_gpu.c', n=3, lineterm=''))
body = '\n'.join(diff)
if not body.endswith('\n'):
    body += '\n'
head = 'diff --git a/bsd/dev/gauguin_gpu.c b/bsd/dev/gauguin_gpu.c\n'
open(OUT, 'wb').write((head + body).encode('utf-8'))
print('wrote %s: %d lines' % (OUT, (head + body).count('\n')))
