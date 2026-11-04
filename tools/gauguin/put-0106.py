import base64, json, os, subprocess, time

GH = r"D:\hackios\tools\gh\bin\gh.exe"
REPO = "TigerShota/ios-sweet"
ENV = dict(os.environ)
for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
    ENV.pop(k, None)

path = "xnu/patches/0106-gauguin-gpu-zap-atomic.patch"
content = open(r"D:\hackios\ios-sweet\xnu\patches\0106-gauguin-gpu-zap-atomic.patch", "rb").read()
assert not content.startswith(b"\xef\xbb\xbf") and b"\r" not in content
assert content.endswith(b"\n")
for c in [b"bsd/dev/gauguin_gpu.c", b"ZAP_FN_ATOMIC", b"0x80000000u",
          b"GAUGUIN SCM: fn=%#x interrupted x%u ",
          b"(cont=%#llx res1=%#llx)", b"GAUGUIN SCM: fn=%#x busy x%u",
          b"pas %s ATOMIC available", b"calling INIT_IMAGE (meta=%#llx)",
          b"INIT_IMAGE status %lld result %llu (atomic)",
          b"MEM_SETUP status %lld result %llu (atomic)",
          b"SHUTDOWN status %lld (atomic)", b"SHUTDOWN status %lld (yield)",
          b"intr >= 500"]:
    assert c in content, c

r = subprocess.run([GH, "api", f"repos/{REPO}/contents/{path}?ref=main"],
                   capture_output=True, text=True, env=ENV)
if r.returncode == 0:
    sha = json.loads(r.stdout)["sha"]
    print("0106 exists, updating sha:", sha)
else:
    sha = None
    print("0106 not on remote (creating)")

body = {"message": "0106: gauguin GPU zap PAS - atomic-first SMCs + full retry "
                   "instrumentation. v26 verdict: the zap ladder walked cleanly through "
                   "the PAS probes (all four available), the ELF sanity, the 1 MiB-aligned "
                   "block and the metadata copy (0 mismatch), then INIT_IMAGE "
                   "(fn 0x42000201 arginfo 0x82 args (13, meta_pa)) NEVER RETURNED - no "
                   "print after 'meta copied', no panic, no reset, the system ran the whole "
                   "session on the other CPUs. Correlation: every SMC that ever returned on "
                   "this port carries bit 31 (PSCI CPU_ON 0xC4000003, IS_CALL_AVAIL "
                   "0xC2000601); INIT_IMAGE was the first yielding (bit-31-clear) SMC we "
                   "issued and it is the one that vanished. So: probe the atomic forms "
                   "(fn|0x80000000) too, call INIT_IMAGE/MEM_SETUP/AUTH_AND_RESET/SHUTDOWN "
                   "atomic-first with a yielding fallback (both printed), and instrument "
                   "gauguin_scm_call - the status==1 reissue loop is bounded (500) and "
                   "logged (cont/res1), the -12 busy path logs too. A silent spin can no "
                   "longer hide",
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
