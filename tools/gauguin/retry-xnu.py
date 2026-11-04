# -*- coding: utf-8 -*-
"""Retry loop: rerun xnu run 41743370748 until it actually builds (macOS runner
capacity keeps rejecting jobs - 'not acquired by Runner of type hosted').
Aborts if the failure is a real build error (annotation differs)."""
import json, os, subprocess, time

GH = r"D:\hackios\tools\gh\bin\gh.exe"
REPO = "TigerShota/ios-sweet"
RUN = 41806789763
MAX_ATTEMPTS = 8
ENV = dict(os.environ)
for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
    ENV.pop(k, None)

def api(ep, method="GET"):
    cmd = [GH, "api", ep] if method == "GET" else [GH, "api", "-X", method, ep]
    r = subprocess.run(cmd, capture_output=True, env=ENV)
    assert r.returncode == 0, (ep, r.stderr[:300])
    return json.loads(r.stdout) if r.stdout.strip() else {}

def log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)

CAPACITY = "not acquired by Runner"

for attempt in range(1, MAX_ATTEMPTS + 1):
    run = api("repos/%s/actions/runs/%d" % (REPO, RUN))
    if run["status"] != "completed":
        log("attempt %d: run still %s, waiting" % (attempt, run["status"]))
    else:
        if run["conclusion"] == "success":
            log("SUCCESS: %s" % run["html_url"])
            raise SystemExit(0)
        # inspect why it failed
        jobs = api("repos/%s/actions/runs/%d/jobs" % (REPO, RUN))
        job = jobs["jobs"][0]
        ann = api("repos/%s/check-runs/%d/annotations" % (REPO, job["id"]))
        msgs = [a.get("message", "") for a in ann]
        if not any(CAPACITY in m for m in msgs):
            log("REAL FAILURE (not capacity) - annotations:")
            for m in msgs:
                log("  " + m)
            raise SystemExit(2)
        log("attempt %d: capacity rejection again (%s), rerun" % (attempt, job["conclusion"]))
        api("repos/%s/actions/runs/%d/rerun" % (REPO, RUN), "POST")
        time.sleep(30)  # let the rerun register

    # poll until completed (up to 40 min per attempt)
    t0 = time.time()
    while time.time() - t0 < 2400:
        time.sleep(60)
        run = api("repos/%s/actions/runs/%d" % (REPO, RUN))
        if run["status"] == "completed":
            log("run completed: %s" % run["conclusion"])
            break
    else:
        log("poll timeout within attempt")

raise SystemExit("exhausted %d attempts, still capacity-blocked" % MAX_ATTEMPTS)
