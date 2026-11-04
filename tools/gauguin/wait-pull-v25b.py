# -*- coding: utf-8 -*-
"""Pull sweet log from recovery (second v25 run), keep original v25 files."""
import os, subprocess, time

ADB = r"D:\hackios\adb\adb.exe"
OUT = r"D:\hackios\out\sweetlog-v25b"
os.makedirs(OUT, exist_ok=True)

def log(m):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), m), flush=True)

def sh(cmd, binary=False, timeout=120):
    r = subprocess.run([ADB] + cmd, capture_output=True, timeout=timeout)
    if r.returncode != 0:
        raise SystemExit("adb %r failed: %s" % (cmd, r.stderr.decode('utf-8', 'replace')[:300]))
    return r.stdout if binary else r.stdout.decode('utf-8', 'replace').strip()

state = sh(["get-state"])
log("device state: %s" % state)
assert state == "recovery", "not in recovery"

real = sh(["shell", "readlink -f /dev/block/by-name/userdata"])
size = int(sh(["shell", "blockdev --getsize64 %s" % real]))
skip = size // 4096 - 1025
data = sh(["exec-out", "dd", "if=%s" % real, "bs=4096", "skip=%d" % skip, "count=1025"], binary=True)
log("pulled %d bytes" % len(data))
raw = os.path.join(OUT, "v25b.bin")
open(raw, "wb").write(data)
r = subprocess.run(["python", r"D:\hackios\ios-sweet\tools\sweet-log.py", raw,
                    os.path.join(OUT, "v25b.txt")], capture_output=True, text=True)
log("sweet-log rc=%d" % r.returncode)
print("DONE")
