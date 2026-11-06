# -*- coding: utf-8 -*-
"""Boot a shim image and wait 300 s for the GPU ladder: python gpu-boot.py <image>"""
import subprocess, sys, time

FB = r"D:\hackios\adb\fastboot.exe"
IMG = sys.argv[1] if len(sys.argv) > 1 else \
    r"D:\hackios\out\shim-v58\shim\out\shim-boot.img"

def log(m):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), m), flush=True)

for i in range(60):
    r = subprocess.run([FB, "devices"], capture_output=True, text=True, timeout=30)
    if [l for l in r.stdout.split("\n") if l.strip() and not l.startswith("*")]:
        log("fastboot detected")
        break
    if i % 6 == 0:
        log("waiting for fastboot... (%d s)" % (i * 10))
    time.sleep(10)
else:
    raise SystemExit("no fastboot in 10 min")

r = subprocess.run([FB, "boot", IMG], capture_output=True, text=True, timeout=180)
out_all = r.stdout + r.stderr
log("boot exit=%d ok=%s" % (r.returncode, "OKAY" in out_all))
if r.returncode != 0 or "OKAY" not in out_all:
    raise SystemExit("fastboot boot FAILED")
log("v54 BOOTED - waiting 300 s for the boot log to cover the GPU ladder")
time.sleep(300)
log("done - power-cycle the phone into recovery now")
