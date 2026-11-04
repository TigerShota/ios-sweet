#!/bin/bash
# Pinned, portable constant-time AES for the SWEET legacy CBC/ECB API.
set -euo pipefail
[[ "${MACHINE_CONFIG:-}" == SWEET ]] || exit 0
[[ -f libkern/crypto/corecrypto_aes.c ]] || exit 1
bearssl_sha=7bea48e5e850ab4cafbe68d3765cdaba13a86d6f
# The upstream git host (bearssl.org) answers 502 to GitHub Actions runners
# (two consecutive runs died on it). The exact pinned tree is vendored under
# bearssl-vendor/ next to this script; xnu.yml copies it into dxb/patches/.
bearssl_src="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/bearssl-vendor"
[[ -d "$bearssl_src/inc" ]] || { echo "bearssl-vendor/inc missing" >&2; exit 1; }
[[ -f "$bearssl_src/src/config.h" ]] || { echo "bearssl-vendor/src/config.h missing" >&2; exit 1; }
for f in inner.h symcipher/aes_ct.c symcipher/aes_ct_enc.c symcipher/aes_ct_dec.c \
         symcipher/aes_ct_cbcenc.c symcipher/aes_ct_cbcdec.c hash/md5.c hash/sha1.c \
         hash/sha2big.c codec/dec32le.c codec/enc32le.c codec/dec32be.c codec/enc32be.c \
         codec/dec64be.c codec/enc64be.c; do
    [[ -f "$bearssl_src/src/$f" ]] || { echo "bearssl-vendor/src/$f missing" >&2; exit 1; }
done
bearssl_tmp="$bearssl_src"
python3 - "$bearssl_tmp" <<'PY'
from pathlib import Path
import shutil
import sys

source = Path(sys.argv[1])
target = Path('libkern/crypto/sweet_bearssl')
if target.exists():
    raise SystemExit(f'refusing to overwrite existing AES source: {target}')
shutil.copytree(source / 'inc', target / 'inc')
(target / 'src/symcipher').mkdir(parents=True)
for file in ('inner.h', 'config.h'):
    shutil.copy2(source / 'src' / file, target / 'src' / file)
# XNU exports machine/limits.h instead of the userspace limits.h.
header = target / 'src/inner.h'
text = header.read_text()
assert text.count('#include <limits.h>') == 1
header.write_text(text.replace('#include <limits.h>',
    '#if defined(KERNEL)\n#include <machine/limits.h>\n'
    '#else\n#include <limits.h>\n#endif'))
for name in ('aes_ct', 'aes_ct_enc', 'aes_ct_dec', 'aes_ct_cbcenc', 'aes_ct_cbcdec'):
    shutil.copy2(source / 'src/symcipher' / (name + '.c'),
                 target / 'src/symcipher' / (name + '.c'))
# MD5, SHA-1 and SHA-384/512 for the legacy hash APIs, with their codecs.
for name in ('hash/md5.c', 'codec/dec32le.c', 'codec/enc32le.c',
             'hash/sha1.c', 'codec/dec32be.c', 'codec/enc32be.c',
             'hash/sha2big.c', 'codec/dec64be.c', 'codec/enc64be.c'):
    (target / 'src' / name).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source / 'src' / name, target / 'src' / name)
shutil.copy2(source / 'LICENSE.txt', target / 'LICENSE.txt')
PY
echo "Staged BearSSL portable AES, MD5, SHA-1 and SHA-384/512 ($bearssl_sha) for SWEET"
