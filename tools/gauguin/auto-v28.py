# -*- coding: utf-8 -*-
"""Auto: wait fastboot -> boot v28 (0107 zap PAS ABI fix) -> wait recovery -> pull log."""
import os, subprocess, time

FB = r"D:\hackios\adb\fastboot.exe"
ADB = r"D:\hackios\adb\adb.exe"
IMG = r"D:\hackios\out\shim-v28\shim\out\shim-boot.img"
OUT = r"D:\hackios\out\sweetlog-v28"
os.makedirs(OUT, exist_ok=True)

def log(m):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), m), flush=True)

def in_fastboot():
    r = subprocess.run([FB, "devices"], capture_output=True, text=True, timeout=30)
    return bool([l for l in r.stdout.split("\n") if l.strip() and not l.startswith("*")])

for i in range(90):
    if in_fastboot():
        log("fastboot detected")
        break
    if i % 6 == 0:
        log("waiting for fastboot... (%d s)" % (i * 10))
    time.sleep(10)
else:
    raise SystemExit("no fastboot in 15 min")

r = subprocess.run([FB, "boot", IMG], capture_output=True, text=True, timeout=180)
out_all = r.stdout + r.stderr
log("boot exit=%d ok=%s" % (r.returncode, "OKAY" in out_all))
if r.returncode != 0 or "OKAY" not in out_all:
    raise SystemExit("fastboot boot FAILED")
log("v28 BOOTED - watch the phone")

def sh(cmd, binary=False, timeout=120):
    r = subprocess.run([ADB] + cmd, capture_output=True, timeout=timeout)
    if r.returncode != 0:
        raise SystemExit("adb %r failed: %s" % (cmd, r.stderr.decode('utf-8', 'replace')[:300]))
    return r.stdout if binary else r.stdout.decode('utf-8', 'replace').strip()

for i in range(90):
    r = subprocess.run([ADB, "get-state"], capture_output=True, text=True, timeout=30)
    if r.returncode == 0 and r.stdout.strip() == "recovery":
        log("device in recovery")
        break
    if i % 6 == 0:
        log("waiting for recovery... (%d s)" % (i * 10))
    time.sleep(10)
else:
    raise SystemExit("never returned to recovery")

real = sh(["shell", "readlink -f /dev/block/by-name/userdata"])
size = int(sh(["shell", "blockdev --getsize64 %s" % real]))
skip = size // 4096 - 1025
data = sh(["exec-out", "dd", "if=%s" % real, "bs=4096", "skip=%d" % skip, "count=1025"], binary=True)
log("pulled %d bytes" % len(data))
raw = os.path.join(OUT, "v28.bin")
open(raw, "wb").write(data)
r = subprocess.run(["python", r"D:\hackios\ios-sweet\tools\sweet-log.py", raw,
                    os.path.join(OUT, "v28.txt")], capture_output=True, text=True)
log("sweet-log rc=%d" % r.returncode)
