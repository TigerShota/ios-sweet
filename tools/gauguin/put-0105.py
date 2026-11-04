import base64, json, os, subprocess, time

GH = r"D:\hackios\tools\gh\bin\gh.exe"
REPO = "TigerShota/ios-sweet"
ENV = dict(os.environ)
for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
    ENV.pop(k, None)

path = "xnu/patches/0105-gauguin-gpu-zap-pas.patch"
content = open(r"D:\hackios\ios-sweet\xnu\patches\0105-gauguin-gpu-zap-pas.patch", "rb").read()
assert not content.startswith(b"\xef\xbb\xbf") and b"\r" not in content
assert content.endswith(b"\n")
for c in [b"bsd/dev/gauguin_gpu.c", b"bsd/dev/gauguin_zap_fw.c", b"gauguin_zap_mdt[1715]",
          b"gauguin_zap_b02[496]", b"smc #0", b"0x42000201", b"0x42000202", b"0x42000205",
          b"0x42000206", b"0xC2000601", b"gg_pkt7", b"0x48u, 8u", b"0x66u, 1u",
          b"authenticated and started (PAS 13)", b"SET_SECURE_MODE submitted",
          b"L3b abort after SEC_MODE", b"0xffffffffu, 63u, 0u, 1u",
          b"GAUGUIN ZAP: meta copied %u dwords, %u mismatch",
          b"GAUGUIN ZAP: segment %u bytes -> img+0"]:
    assert c in content, c

r = subprocess.run([GH, "api", f"repos/{REPO}/contents/{path}?ref=main"],
                   capture_output=True, text=True, env=ENV)
if r.returncode == 0:
    sha = json.loads(r.stdout)["sha"]
    print("0105 exists, updating sha:", sha)
else:
    sha = None
    print("0105 not on remote (creating)")

body = {"message": "0105: gauguin GPU L3b final - zap shader through TrustZone PAS 13 plus "
                   "real type-7 packet headers. v25 proved the ring/SQE config sticks with "
                   "zero faults and zero fence drops while cp_status1=0 rptr=0: the SQE never "
                   "fetched. Root causes: (1) the type-7 header was mis-encoded - vendor "
                   "adreno_pm4types.h (gauguin-r-oss) puts the 7-bit opcode at [22:16], count "
                   "at [14:0], odd parity at 23/15, and the SQE validates it; the old "
                   "0x70080042 decoded as opcode 8/count 66. CP_ME_INIT=0x48 (0x70C80008), "
                   "CP_SET_SECURE_MODE=0x66 (0x70E60001), both parity-checked in the "
                   "generator. (2) the a6xx SQE boots secure: load the signed a615_zap via "
                   "SCM PAS 13 (DT qcom,kgsl-hyp pas-id 13 firmware-name a615_zap) before "
                   "SQE_CNTL=1 - kgsl microcode_load order. SMC64 on the 0065 PSCI conduit; "
                   "mxtp 0092 proved the PAS calls under XNU on sweet, fajita console75c/76 "
                   "the whole ladder. Memory per mxtp 0092: own 1 MiB-PA-aligned block "
                   "(pnum_mask 63), b02 at +0, mdt on its own page, INIT_IMAGE(13,mdt) -> "
                   "MEM_SETUP(13,img,4K) -> copy b02 -> AUTH_AND_RESET(13), SHUTDOWN after "
                   "the packet test; the DT gpu_region 0x8b715400 is an orphan. New "
                   "gauguin_zap_fw.c embeds mdt 6860 B + b02 1984 B",
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
