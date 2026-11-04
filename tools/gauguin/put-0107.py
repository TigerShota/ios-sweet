import base64, json, os, subprocess, time

GH = r"D:\hackios\tools\gh\bin\gh.exe"
REPO = "TigerShota/ios-sweet"
ENV = dict(os.environ)
for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
    ENV.pop(k, None)

path = "xnu/patches/0107-gauguin-gpu-zap-abi.patch"
content = open(r"D:\hackios\ios-sweet\xnu\patches\0107-gauguin-gpu-zap-abi.patch", "rb").read()
assert not content.startswith(b"\xef\xbb\xbf") and b"\r" not in content
assert content.endswith(b"\n")
for c in [b"bsd/dev/gauguin_gpu.c",
          b"ZAP_FN_INIT, 0x82u, meta_pa, 0, 0, &r1",
          b"ZAP_FN_MEM, 0x3u, img_pa, 0x1000u, 0, &r1",
          b"ZAP_FN_AUTH, 0x1u, 0, 0, 0, &r1",
          b"INIT_IMAGE fn=%#x arginfo=%#x ",
          b"if (intr >= 2)"]:
    assert c in content, c

r = subprocess.run([GH, "api", f"repos/{REPO}/contents/{path}?ref=main"],
                   capture_output=True, text=True, env=ENV)
sha = None
if r.returncode == 0:
    sha = json.loads(r.stdout)["sha"]
    print("0107 exists, updating sha:", sha)
else:
    print("0107 not on remote (creating)")

body = {"message": "0107: gauguin GPU zap PAS argument ABI fix. v27 returned the decisive "
                   "evidence: 'GAUGUIN SCM: fn=0xc2000201 interrupted x1 (cont=0x61900021 "
                   "res1=0x82)' - the SMC RETURNED interrupted and the reissue hard-hangs "
                   "(log stops at 1997 while v26 ran 4509 lines). Root cause: the argument "
                   "vector has been wrong since 0105 - we passed the PAS id as arg0, so "
                   "INIT_IMAGE went out as arginfo 0x82 args (13, meta_pa) and the TZ read "
                   "PHYSICAL ADDRESS 13 as the metadata blob; MEM_SETUP as (13, img_pa, "
                   "0x1000); AUTH as (13). Fault -> interrupted -> wedged reissue. The only "
                   "hardware-proven sequence is fajita console75c: INIT_IMAGE fn=0x42000201 "
                   "arginfo=0x82 args=(0x102501000, 0) -> status 0 result 0; MEM_SETUP "
                   "arginfo=0x3 (0x8c415000, 0x1000); AUTH_AND_RESET arginfo=0x1 (0). No "
                   "peripheral id in the arguments - this TZ serves PAS 13 by default. 0107 "
                   "adopts that vector verbatim for all four calls (yielding form - fajita "
                   "succeeded on the FIRST call and our atomic form also came back "
                   "interrupted, so bit 31 buys nothing here), prints fn/arginfo/args before "
                   "each call for line-by-line diffing against fajita, and caps the "
                   "interrupted reissue at 2 so a wedged service gives up instead of spinning "
                   "into the v26/v27 hard hang",
        "content": base64.b64encode(content).decode()}
if sha:
    body["sha"] = sha
r = subprocess.run([GH, "api", "-X", "PUT", f"repos/{REPO}/contents/{path}", "--input", "-"],
                   input=json.dumps(body), capture_output=True, text=True, env=ENV)
assert r.returncode == 0, r.stderr[:500]
print("PUT done")

back = subprocess.run([GH, "api", f"repos/{REPO}/contents/{path}?ref=main",
                       "-H", "Accept: application/vnd.github.raw"], capture_output=True, env=ENV).stdout
assert back == content, "ROUNDTRIP FAILED"
print("roundtrip OK (%d B)" % len(content))

time.sleep(20)
runs = subprocess.run([GH, "api", f"repos/{REPO}/actions/workflows/xnu.yml/runs?per_page=2"],
                      capture_output=True, env=ENV)
for r_ in json.loads(runs.stdout).get("workflow_runs", []):
    print("xnu run %d: %s/%s (created %s) %s" % (r_["id"], r_["status"], r_["conclusion"],
                                                 r_["created_at"], r_["html_url"]))
    break
