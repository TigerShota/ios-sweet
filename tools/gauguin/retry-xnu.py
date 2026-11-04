# -*- coding: utf-8 -*-
"""Retry loop: rerun the xnu run until it actually builds. Two transients get
rerun automatically: macOS runner capacity rejections ('not acquired by Runner
of type hosted') and upstream fetch failures (bearssl.org / dyld / IPSW hosts
returning 5xx out of the CI's control). Aborts on a real build error."""
import json, os, subprocess, time

GH = r"D:\hackios\tools\gh\bin\gh.exe"
REPO = "TigerShota/ios-sweet"
RUN = 41816789702
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
TRANSIENT_FETCH = ["The requested URL returned error: 502",
                   "The requested URL returned error: 503",
                   "The requested URL returned error: 504",
                   "bearssl.org",
                   "unable to access '"]

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
            if not any(t in m for m in msgs for t in TRANSIENT_FETCH):
                log("REAL FAILURE (not capacity, not upstream) - annotations:")
                for m in msgs:
                    log("  " + m)
                raise SystemExit(2)
            log("attempt %d: upstream fetch failure, rerun" % attempt)
        else:
            log("attempt %d: capacity rejection again (%s), rerun"
                % (attempt, job["conclusion"]))
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
