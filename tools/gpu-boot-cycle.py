# -*- coding: utf-8 -*-
"""One gauguin boot cycle: clear the log, boot a shim image, pull + decode it.

  python tools/gpu-boot-cycle.py phase1 <shim-boot.img> [tag]
      zero the SWEETLOG header at the tail of userdata, adb reboot bootloader,
      fastboot boot <image>, wait BOOT_WAIT s for the GPU ladder, then ask the
      operator to power-cycle the phone into recovery.

  python tools/gpu-boot-cycle.py phase2 [tag]
      wait for the phone in recovery, pull the ring, decode it and print a
      verdict (marker histogram, grep hits, pixel summary, tail).

Environment (optional):
  ADB          adb binary        (default: $ANDROID_HOME/platform-tools/adb.exe)
  FB           fastboot binary   (default: same dir)
  SWEETLOG_OUT output root       (default: <repo>/../out)
  SWEETLOG_DEV block device      (default: /dev/block/sda35)
  BOOT_WAIT    seconds after fastboot boot (default 300)
  LOG_GREP     '|'-separated substrings to echo (default 'GAUGUIN CP:')
  LOG_TAIL     tail lines to echo (default 20)
"""
import os
import re
import struct
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SDK = os.environ.get('ANDROID_HOME') or os.environ.get('ANDROID_SDK_ROOT') or ''


def _tool(name):
    """$<NAME>, then $ADB's directory, then $ANDROID_HOME, then PATH."""
    exe = name + ('.exe' if os.name == 'nt' else '')
    env = os.environ.get(name.upper())
    if env:
        return env
    adb_env = os.environ.get('ADB')
    if adb_env:
        cand = os.path.join(os.path.dirname(adb_env), exe)
        if os.path.exists(cand):
            return cand
    cand = os.path.join(SDK, 'platform-tools', exe)
    if SDK and os.path.exists(cand):
        return cand
    return name


ADB = _tool('adb')
FB = _tool('fastboot')
OUT_ROOT = os.environ.get('SWEETLOG_OUT') or os.path.join(HERE, '..', '..',
                                                          'out')
BLOCK = os.environ.get('SWEETLOG_DEV', '/dev/block/sda35')
BOOT_WAIT = int(os.environ.get('BOOT_WAIT', '300'))
TAIL_BLOCKS = 1025
GREP = [s for s in os.environ.get('LOG_GREP', 'GAUGUIN CP:').split('|') if s]
TAIL = int(os.environ.get('LOG_TAIL', '20'))


def run(cmd, timeout=120):
    p = subprocess.run(cmd, capture_output=True, timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def adb_state():
    rc, out, _ = run([ADB, 'get-state'])
    return out.strip().decode('latin-1') if rc == 0 else ''


def adb_wait_recovery(max_wait=1800):
    print('waiting for device in recovery (adb)...', flush=True)
    t0 = time.time()
    while time.time() - t0 < max_wait:
        if adb_state() in ('recovery', 'device'):
            rc, out, _ = run([ADB, 'shell', 'ls ' + BLOCK])
            if rc == 0 and BLOCK.rsplit('/', 1)[-1].encode() in out:
                print('device online (recovery, %s ok)' % BLOCK, flush=True)
                return True
        time.sleep(5)
    return False


def sweetlog_skip():
    """Offset (in 4096-byte blocks) of the 1025-block log area at userdata's tail."""
    name = BLOCK.rsplit('/', 1)[-1]
    rc, out, _ = run([ADB, 'shell', 'cat /sys/class/block/%s/size' % name])
    if rc == 0 and out.strip().isdigit():
        n4096 = int(out.strip()) * 512 // 4096      # the sysfs size is in 512-byte sectors
    else:
        rc, out, _ = run([ADB, 'shell', 'blockdev --getsize64 ' + BLOCK])
        if rc != 0 or not out.strip().isdigit():
            raise SystemExit('cannot read the userdata size')
        n4096 = int(out.strip()) // 4096
    return n4096 - TAIL_BLOCKS


def clear_sweetlog(skip):
    print('sweetlog skip=%u' % skip, flush=True)
    rc, out, err = run([ADB, 'shell', 'dd if=/dev/zero of=%s bs=4096 seek=%u '
                        'count=1 conv=notrunc' % (BLOCK, skip)])
    print('clear rc=%d %s' % (rc, err.decode('utf-8', 'replace').strip()),
          flush=True)
    p = subprocess.run([ADB, 'exec-out', 'dd if=%s bs=4096 skip=%u count=1' %
                        (BLOCK, skip)], capture_output=True, timeout=60)
    data = p.stdout[:4096]
    ok = p.returncode == 0 and len(data) >= 16 and data == b'\0' * len(data)
    print('verify zeroed:', ok, 'bytes:', len(p.stdout), flush=True)
    return ok


def fastboot_boot(img):
    print('rebooting to bootloader...', flush=True)
    run([ADB, 'reboot', 'bootloader'])
    t0 = time.time()
    while time.time() - t0 < 120:
        rc, out, _ = run([FB, 'devices'])
        if b'fastboot' in out:
            break
        time.sleep(2)
    else:
        raise SystemExit('no fastboot device')
    print(out.decode('latin-1').strip(), flush=True)
    rc, out, err = run([FB, 'boot', img], timeout=300)
    print('fastboot boot rc=%d\n%s%s' % (rc, out.decode('utf-8', 'replace'),
                                         err.decode('utf-8', 'replace')),
          flush=True)
    if rc != 0:
        raise SystemExit('fastboot boot failed')


def pull_and_decode(tag, skip):
    out_dir = os.path.join(OUT_ROOT, 'sweetlog-' + tag)
    os.makedirs(out_dir, exist_ok=True)
    p = subprocess.run([ADB, 'exec-out', 'dd if=%s bs=4096 skip=%u count=%u' %
                        (BLOCK, skip, TAIL_BLOCKS)], capture_output=True,
                       timeout=300)
    raw = p.stdout
    bin_path = os.path.join(out_dir, tag + '.bin')
    open(bin_path, 'wb').write(raw)
    print('log bytes=%d (expect %d)' % (len(raw), TAIL_BLOCKS * 4096),
          flush=True)

    txt = ''
    note = ''
    try:
        magic, version, size, total, seq = struct.unpack_from('<8sIIQQ', raw, 0)
        if magic == b'SWEETLOG' and 0 < size <= len(raw) - 4096:
            ring = raw[4096:4096 + size]
            body = ring[:total] if total <= size else \
                ring[total % size:] + ring[:total % size]
            txt = body.decode('utf-8', 'replace').replace('\r', '')
            note = ('version %u ring %u total %u flush #%u' %
                    (version, size, total, seq))
    except Exception:
        pass
    if not txt:
        chunks = [c for c in raw.split(b'\n') if c.strip(b'\x00')]
        txt = '\n'.join(c.replace(b'\x00', b'').decode('utf-8', 'replace')
                        for c in chunks)
        note = 'recovered from the raw area (no SWEETLOG header)'
    txt_path = os.path.join(out_dir, tag + '.txt')
    open(txt_path, 'w', encoding='utf-8', newline='\n').write(txt)
    print('decode:', note, flush=True)
    return bin_path, txt_path, txt


def verdict(txt):
    lines = txt.splitlines()
    print('\n===== verdict =====', flush=True)
    print('lines: %d' % len(lines), flush=True)

    hist = {}
    for ln in lines:
        m = re.search(r'GAUGUIN [A-Z]+: (0\d{3})\b', ln)
        if m:
            hist[m.group(1)] = hist.get(m.group(1), 0) + 1
    print('markers: ' + ' '.join('%s=%d' % kv for kv in sorted(hist.items())),
          flush=True)

    for kw in ('submit state OK', 'submit state HUNG', 'STUCK', 'HUNG',
               'panic(cpu', 'SWEET FB: swap', '0191 stop', 'nopte'):
        print('  %-22s %d' % (kw, txt.count(kw)), flush=True)

    px = [ln for ln in lines if 'src px' in ln]
    nz = [ln for ln in px if '0x00000000 tl 0x00000000' not in ln]
    print('\nsrc px: %d samples, %d non-zero' % (len(px), len(nz)), flush=True)
    for ln in nz[:8]:
        print('  NZ ' + ln[-200:], flush=True)

    hits = [ln for ln in lines if any(g in ln for g in GREP)]
    print('\ngrep hits (%d, showing up to 400):' % len(hits), flush=True)
    for ln in hits[:400]:
        print('  ' + ln[-200:], flush=True)

    print('\n--- last %d lines ---' % TAIL, flush=True)
    for ln in lines[-TAIL:]:
        print('  ' + ln[-200:], flush=True)
    return len(lines) > 0


def main():
    phase = sys.argv[1] if len(sys.argv) > 1 else 'phase1'
    if phase == 'phase1':
        if len(sys.argv) < 3:
            raise SystemExit('usage: phase1 <shim-boot.img> [tag]')
        img = sys.argv[2]
        tag = sys.argv[3] if len(sys.argv) > 3 else 'run'
        if not os.path.exists(img):
            raise SystemExit('missing image: ' + img)
        if not adb_wait_recovery():
            raise SystemExit('timeout waiting for a recovery device')
        skip = sweetlog_skip()
        if not clear_sweetlog(skip):
            raise SystemExit('SWEETLOG clear verification failed')
        fastboot_boot(img)
        print('booting; waiting %u s for the GPU ladder...' % BOOT_WAIT,
              flush=True)
        time.sleep(BOOT_WAIT)
        print('\n>>> NOW power-cycle the phone back into recovery <<<',
              flush=True)
        print('>>> then run: python tools/gpu-boot-cycle.py phase2 %s <<<' %
              tag, flush=True)
        return
    if phase == 'phase2':
        tag = sys.argv[2] if len(sys.argv) > 2 else 'run'
        if not adb_wait_recovery(7200):
            raise SystemExit('timeout waiting for recovery after the cycle')
        time.sleep(5)
        bin_path, txt_path, txt = pull_and_decode(tag, sweetlog_skip())
        verdict(txt)
        print('\nbin: %s\ntxt: %s' % (bin_path, txt_path), flush=True)
        return
    raise SystemExit('unknown phase ' + phase)


if __name__ == '__main__':
    main()
