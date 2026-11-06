# -*- coding: utf-8 -*-
"""Pull the persistent sweetlog ring from userdata (device in recovery via adb)."""
import os, subprocess, sys

ADB = r"D:\hackios\adb\adb.exe"
TAG = sys.argv[1] if len(sys.argv) > 1 else 'v41'
OUT = os.path.join(r"D:\hackios\out", 'sweetlog-' + TAG)
os.makedirs(OUT, exist_ok=True)

def sh(cmd, binary=False):
    r = subprocess.run([ADB] + cmd, capture_output=True, timeout=120)
    if r.returncode != 0:
        raise SystemExit("adb %r failed: %s" % (cmd, r.stderr.decode('utf-8', 'replace')[:300]))
    return r.stdout if binary else r.stdout.decode('utf-8', 'replace').strip()

print(sh(["get-state"]))
real = sh(["shell", "readlink -f /dev/block/by-name/userdata"])
print("real:", real)
size = int(sh(["shell", "blockdev --getsize64 %s" % real]))
print("userdata bytes:", size)

skip = size // 4096 - 1025
data = sh(["exec-out", "dd", "if=%s" % real, "bs=4096", "skip=%d" % skip, "count=1025"], binary=True)
print("pulled bytes:", len(data))
raw = os.path.join(OUT, TAG + ".bin")
open(raw, "wb").write(data)

txt = os.path.join(OUT, TAG + ".txt")
r = subprocess.run(["python", r"D:\hackios\ios-sweet\tools\sweet-log.py", raw, txt],
                   capture_output=True, text=True)
print("sweet-log rc:", r.returncode)
print(r.stdout[-300:] if r.stdout else "", r.stderr[:200] if r.stderr else "")
