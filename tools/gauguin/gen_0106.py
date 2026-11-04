# -*- coding: utf-8 -*-
"""Generate 0106-gauguin-gpu-zap-atomic.patch (zap PAS: atomic-first + SMC
instrumentation).

v26 verdict: the zap ladder walked cleanly through the PAS probes, the ELF
sanity, the 1 MiB-aligned block and the metadata copy (0 mismatch) - and then
INIT_IMAGE (fn 0x42000201, arginfo 0x82, args (13, meta_pa)) NEVER RETURNED.
No print after "meta copied", no panic, no reset; the system ran the whole
session on the other CPUs (4509 log lines, touch alive). The thread is parked
inside the SMC or spinning in the unbounded status==1 reissue loop with no
output.

Correlation: every SMC that ever returned on this port carries bit 31 (the
"atomic"/complete-now form) - PSCI CPU_ON 0xC4000003, IS_CALL_AVAIL
0xC2000601. INIT_IMAGE was the first bit-31-clear (yielding) SMC we ever
issued, and it is exactly the one that vanished. The yielding path in this
TZ build apparently depends on an interrupt wakeup that never comes in our
environment (fajita/mxtp TZ returned these instantly; gauguin's MIUI TZ does
not).

0106 therefore:
  1. Probes the ATOMIC forms (fn | bit 31) of the four PAS calls too.
  2. Calls INIT_IMAGE / MEM_SETUP / AUTH_AND_RESET / SHUTDOWN atomic-first,
     falling back to the yielding form (both attempts printed).
  3. Instruments gauguin_scm_call: the status==1 reissue loop is now
     bounded (500) and logged (first 3 + every 100th, with the continuation
     token and result), and the -12 busy path logs too - a silent spin can
     no longer hide.
Judgement: "INIT_IMAGE status 0 ... (atomic)" -> ladder proceeds as in v26's
criteria. "calling INIT_IMAGE" printed but neither status line -> the SMC
itself parks even atomic (metadata-pointer/TZ side, next: DT carve-out
0x8b715400 as the metadata page). "interrupted x%u" lines climbing to 500 ->
the yielding path is interrupt-looping; the atomic status line above them
tells whether atomic is usable.
"""
import difflib, os, re, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
P = r"D:\hackios\ios-sweet\xnu\patches"
GPU_C = "bsd/dev/gauguin_gpu.c"
OUT_PATCH = os.path.join(P, "0106-gauguin-gpu-zap-atomic.patch")
APPLYTEST = r"D:\hackios\temp\applytest106"
GIT = r"D:\hackios\tools\git\cmd\git.exe"


def read(p):
    return open(p, "r", newline="").read()


def apply_unified(text, patch_body):
    lines = text.split("\n")
    ph = patch_body.split("\n")
    if ph and ph[-1] == "":
        ph.pop()
    assert ph[0].startswith("--- ") and ph[1].startswith("+++ "), ph[:2]
    out, src, i = [], 0, 2
    while i < len(ph):
        m = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", ph[i])
        assert m, "bad hunk header: " + repr(ph[i])
        old_start = int(m.group(1)) - 1
        assert old_start >= src, (old_start, src)
        out.extend(lines[src:old_start])
        src = old_start
        i += 1
        while i < len(ph) and not ph[i].startswith("@@"):
            l = ph[i]
            if l.startswith("+"):
                out.append(l[1:])
            elif l.startswith("-"):
                assert src < len(lines) and lines[src] == l[1:], \
                    ("ctx/- mismatch", src, repr(lines[src]), repr(l))
                src += 1
            elif l.startswith(" ") or l == "":
                assert src < len(lines) and lines[src] == l[1:], \
                    ("ctx mismatch", src, repr(lines[src]), repr(l))
                out.append(lines[src])
                src += 1
            else:
                raise AssertionError("bad patch line: " + repr(l))
            i += 1
    out.extend(lines[src:])
    return "\n".join(out)


def sub1(text, old, new, tag):
    i = text.find(old)
    assert i >= 0, "anchor not found: " + tag
    assert text.find(old, i + 1) < 0, "anchor not unique: " + tag
    return text[:i] + new + text[i + len(old):]


# ==================== 1. gpu.c surgery (base = post-0105) ====================
post105 = read(os.path.join(HERE, "gpu_post105.c"))
print("post-0105 base: %d lines" % post105.count("\n"))
post = post105

# S1: the atomic fn form
post = sub1(post,
    "#define ZAP_FN_AVAIL     0xC2000601u\n",
    "#define ZAP_FN_AVAIL     0xC2000601u\n"
    "#define ZAP_FN_ATOMIC    0x80000000u  /* same fn, complete-now form */\n",
    "S1 atomic define")

# S2a: scm_call declarations
post = sub1(post,
    "\tint64_t status;\n"
    "\tuint64_t cont = 0, res1 = 0;\n"
    "\tunsigned busy = 0;\n",
    "\tint64_t status;\n"
    "\tuint64_t cont = 0, res1 = 0;\n"
    "\tunsigned busy = 0, intr = 0;\n",
    "S2a scm decls")

# S2b: instrumented, bounded retry rules
post = sub1(post,
    "\t\tif (status == 1) {\n"
    "\t\t\tcontinue;          /* interrupted: reissue */\n"
    "\t\t}\n"
    "\t\tif (status == -12 && ++busy <= 67) {\n"
    "\t\t\tdelay(30000);      /* busy: 30 ms, bounded */\n"
    "\t\t\tcontinue;\n"
    "\t\t}\n"
    "\t\tbreak;\n",
    "\t\tif (status == 1) {\n"
    "\t\t\t/* interrupted: reissue with the continuation token.\n"
    "\t\t\t * Bounded and logged - v26 saw INIT_IMAGE vanish\n"
    "\t\t\t * silently; a spin must leave evidence. */\n"
    "\t\t\tif (++intr <= 3 || (intr % 100) == 0) {\n"
    "\t\t\t\tprintf(\"GAUGUIN SCM: fn=%#x interrupted x%u \"\n"
    "\t\t\t\t    \"(cont=%#llx res1=%#llx)\\n\", fn, intr,\n"
    "\t\t\t\t    (unsigned long long)cont,\n"
    "\t\t\t\t    (unsigned long long)res1);\n"
    "\t\t\t\t(void)sweet_log_flush_now();\n"
    "\t\t\t}\n"
    "\t\t\tif (intr >= 500) {\n"
    "\t\t\t\tbreak;     /* give up: caller prints status 1 */\n"
    "\t\t\t}\n"
    "\t\t\tcontinue;\n"
    "\t\t}\n"
    "\t\tif (status == -12 && ++busy <= 67) {\n"
    "\t\t\tif (busy <= 3 || (busy % 10) == 0) {\n"
    "\t\t\t\tprintf(\"GAUGUIN SCM: fn=%#x busy x%u\\n\", fn, busy);\n"
    "\t\t\t\t(void)sweet_log_flush_now();\n"
    "\t\t\t}\n"
    "\t\t\tdelay(30000);      /* busy: 30 ms, bounded */\n"
    "\t\t\tcontinue;\n"
    "\t\t}\n"
    "\t\tbreak;\n",
    "S2b retry instrumentation")

# S3: atomic availability probes after the yielding ones
post = sub1(post,
    "\tfor (i = 0; i < 4; ++i) {\n"
    "\t\tst = gauguin_scm_call(ZAP_FN_AVAIL, 0x1u, fns[i], 0, 0, &r1);\n"
    "\t\tprintf(\"GAUGUIN ZAP: pas %s available: status %lld result %llu\\n\",\n"
    "\t\t    names[i], (long long)st, (unsigned long long)r1);\n"
    "\t\t(void)sweet_log_flush_now();\n"
    "\t}\n",
    "\tfor (i = 0; i < 4; ++i) {\n"
    "\t\tst = gauguin_scm_call(ZAP_FN_AVAIL, 0x1u, fns[i], 0, 0, &r1);\n"
    "\t\tprintf(\"GAUGUIN ZAP: pas %s available: status %lld result %llu\\n\",\n"
    "\t\t    names[i], (long long)st, (unsigned long long)r1);\n"
    "\t\t(void)sweet_log_flush_now();\n"
    "\t}\n"
    "\t/*\n"
    "\t * v26: the yielding INIT_IMAGE (bit 31 clear) never returned - the\n"
    "\t * first non-atomic SMC this port ever issued (PSCI CPU_ON and\n"
    "\t * IS_CALL_AVAIL both carry bit 31 and both work). Probe the atomic\n"
    "\t * forms too; the real calls below go atomic-first with a yielding\n"
    "\t * fallback so one boot answers both.\n"
    "\t */\n"
    "\tfor (i = 0; i < 4; ++i) {\n"
    "\t\tst = gauguin_scm_call(ZAP_FN_AVAIL, 0x1u,\n"
    "\t\t    fns[i] | ZAP_FN_ATOMIC, 0, 0, &r1);\n"
    "\t\tprintf(\"GAUGUIN ZAP: pas %s ATOMIC available: status %lld \"\n"
    "\t\t    \"result %llu\\n\", names[i], (long long)st,\n"
    "\t\t    (unsigned long long)r1);\n"
    "\t\t(void)sweet_log_flush_now();\n"
    "\t}\n",
    "S3 atomic probes")

# S4: INIT_IMAGE atomic-first
post = sub1(post,
    "\tst = gauguin_scm_call(ZAP_FN_INIT, 0x82u, ZAP_PAS_ID, meta_pa, 0, &r1);\n"
    "\tprintf(\"GAUGUIN ZAP: INIT_IMAGE status %lld result %llu\\n\",\n"
    "\t    (long long)st, (unsigned long long)r1);\n"
    "\t(void)sweet_log_flush_now();\n"
    "\tif (st != 0) {\n"
    "\t\treturn false;\n"
    "\t}\n",
    "\tprintf(\"GAUGUIN ZAP: calling INIT_IMAGE (meta=%#llx)\\n\",\n"
    "\t    (unsigned long long)meta_pa);\n"
    "\t(void)sweet_log_flush_now();\n"
    "\tst = gauguin_scm_call(ZAP_FN_INIT | ZAP_FN_ATOMIC, 0x82u, ZAP_PAS_ID,\n"
    "\t    meta_pa, 0, &r1);\n"
    "\tprintf(\"GAUGUIN ZAP: INIT_IMAGE status %lld result %llu (atomic)\\n\",\n"
    "\t    (long long)st, (unsigned long long)r1);\n"
    "\t(void)sweet_log_flush_now();\n"
    "\tif (st != 0) {\n"
    "\t\tst = gauguin_scm_call(ZAP_FN_INIT, 0x82u, ZAP_PAS_ID,\n"
    "\t\t    meta_pa, 0, &r1);\n"
    "\t\tprintf(\"GAUGUIN ZAP: INIT_IMAGE status %lld result %llu \"\n"
    "\t\t    \"(yield)\\n\", (long long)st, (unsigned long long)r1);\n"
    "\t\t(void)sweet_log_flush_now();\n"
    "\t}\n"
    "\tif (st != 0) {\n"
    "\t\treturn false;\n"
    "\t}\n",
    "S4 INIT atomic-first")

# S5: MEM_SETUP atomic-first
post = sub1(post,
    "\tst = gauguin_scm_call(ZAP_FN_MEM, 0x3u, ZAP_PAS_ID, img_pa,\n"
    "\t    0x1000u, &r1);\n"
    "\tprintf(\"GAUGUIN ZAP: MEM_SETUP status %lld result %llu\\n\",\n"
    "\t    (long long)st, (unsigned long long)r1);\n"
    "\t(void)sweet_log_flush_now();\n"
    "\tif (st != 0) {\n"
    "\t\treturn false;\n"
    "\t}\n",
    "\tst = gauguin_scm_call(ZAP_FN_MEM | ZAP_FN_ATOMIC, 0x3u, ZAP_PAS_ID,\n"
    "\t    img_pa, 0x1000u, &r1);\n"
    "\tprintf(\"GAUGUIN ZAP: MEM_SETUP status %lld result %llu (atomic)\\n\",\n"
    "\t    (long long)st, (unsigned long long)r1);\n"
    "\t(void)sweet_log_flush_now();\n"
    "\tif (st != 0) {\n"
    "\t\tst = gauguin_scm_call(ZAP_FN_MEM, 0x3u, ZAP_PAS_ID, img_pa,\n"
    "\t\t    0x1000u, &r1);\n"
    "\t\tprintf(\"GAUGUIN ZAP: MEM_SETUP status %lld result %llu \"\n"
    "\t\t    \"(yield)\\n\", (long long)st, (unsigned long long)r1);\n"
    "\t\t(void)sweet_log_flush_now();\n"
    "\t}\n"
    "\tif (st != 0) {\n"
    "\t\treturn false;\n"
    "\t}\n",
    "S5 MEM atomic-first")

# S6: AUTH_AND_RESET atomic-first
post = sub1(post,
    "\tst = gauguin_scm_call(ZAP_FN_AUTH, 0x1u, ZAP_PAS_ID, 0, 0, &r1);\n"
    "\tprintf(\"GAUGUIN ZAP: AUTH_AND_RESET status %lld result %llu\\n\",\n"
    "\t    (long long)st, (unsigned long long)r1);\n"
    "\t(void)sweet_log_flush_now();\n"
    "\tif (st != 0) {\n"
    "\t\treturn false;\n"
    "\t}\n",
    "\tst = gauguin_scm_call(ZAP_FN_AUTH | ZAP_FN_ATOMIC, 0x1u, ZAP_PAS_ID,\n"
    "\t    0, 0, &r1);\n"
    "\tprintf(\"GAUGUIN ZAP: AUTH_AND_RESET status %lld result %llu \"\n"
    "\t    \"(atomic)\\n\", (long long)st, (unsigned long long)r1);\n"
    "\t(void)sweet_log_flush_now();\n"
    "\tif (st != 0) {\n"
    "\t\tst = gauguin_scm_call(ZAP_FN_AUTH, 0x1u, ZAP_PAS_ID, 0, 0, &r1);\n"
    "\t\tprintf(\"GAUGUIN ZAP: AUTH_AND_RESET status %lld result %llu \"\n"
    "\t\t    \"(yield)\\n\", (long long)st, (unsigned long long)r1);\n"
    "\t\t(void)sweet_log_flush_now();\n"
    "\t}\n"
    "\tif (st != 0) {\n"
    "\t\treturn false;\n"
    "\t}\n",
    "S6 AUTH atomic-first")

# S7: SHUTDOWN atomic-first (cp_start tail)
post = sub1(post,
    "\t\tint64_t st = gauguin_scm_call(ZAP_FN_SHUTDOWN, 0x1u,\n"
    "\t\t    ZAP_PAS_ID, 0, 0, NULL);\n"
    "\t\tprintf(\"GAUGUIN ZAP: SHUTDOWN status %lld\\n\", (long long)st);\n"
    "\t\t(void)sweet_log_flush_now();\n",
    "\t\tint64_t st = gauguin_scm_call(ZAP_FN_SHUTDOWN | ZAP_FN_ATOMIC,\n"
    "\t\t    0x1u, ZAP_PAS_ID, 0, 0, NULL);\n"
    "\t\tprintf(\"GAUGUIN ZAP: SHUTDOWN status %lld (atomic)\\n\",\n"
    "\t\t    (long long)st);\n"
    "\t\t(void)sweet_log_flush_now();\n"
    "\t\tif (st != 0) {\n"
    "\t\t\tst = gauguin_scm_call(ZAP_FN_SHUTDOWN, 0x1u,\n"
    "\t\t\t    ZAP_PAS_ID, 0, 0, NULL);\n"
    "\t\t\tprintf(\"GAUGUIN ZAP: SHUTDOWN status %lld (yield)\\n\",\n"
    "\t\t\t    (long long)st);\n"
    "\t\t\t(void)sweet_log_flush_now();\n"
    "\t\t}\n",
    "S7 SHUTDOWN atomic-first")

# ==================== 2. sanity ====================
assert "#define ZAP_FN_ATOMIC    0x80000000u" in post
assert post.count("ZAP_FN_ATOMIC") == 6, post.count("ZAP_FN_ATOMIC")
# 1 define + 1 probe loop + INIT + MEM + AUTH + SHUTDOWN call sites
assert post.count("unsigned busy = 0, intr = 0;") == 1
assert post.count("if (intr >= 500)") == 1
# split-literal forms: adjacent C string literals merge at compile time,
# so v27-build.py checks the concatenated forms against the payload
assert "GAUGUIN SCM: fn=%#x interrupted x%u " in post
assert "(cont=%#llx res1=%#llx)" in post
assert "GAUGUIN SCM: fn=%#x busy x%u" in post
assert "pas %s ATOMIC available" in post
assert "calling INIT_IMAGE (meta=%#llx)" in post
assert "INIT_IMAGE status %lld result %llu (atomic)" in post
assert "MEM_SETUP status %lld result %llu (atomic)" in post
assert "AUTH_AND_RESET status %lld result %llu " in post
assert "SHUTDOWN status %lld (atomic)" in post
assert "SHUTDOWN status %lld (yield)" in post
assert post.count("(atomic)") == 4, post.count("(atomic)")
assert post.count("(yield)") == 4, post.count("(yield)")
# the v26 marks that must survive unchanged
for m in ["GAUGUIN ZAP: pas %s available: status %lld result %llu",
          "GAUGUIN ZAP: meta copied %u dwords, %u mismatch",
          "GAUGUIN ZAP: segment %u bytes -> img+0",
          "GAUGUIN ZAP: authenticated and started (PAS 13)",
          "GAUGUIN CP: ME_INIT %s (rptr=%u wptr=%u)",
          "GAUGUIN CP: SET_SECURE_MODE %s (rptr=%u wptr=%u)",
          "GAUGUIN CP: started - the CP executes our packets"]:
    assert m in post, m
assert post.count("smc #0") == 2, post.count("smc #0")  # comment + asm

# ==================== 3. diff ====================
old_l = post105.split("\n")
new_l = post.split("\n")
gd = list(difflib.unified_diff(old_l, new_l, fromfile="a/" + GPU_C,
                               tofile="b/" + GPU_C, lineterm="", n=3))
out = gd
text = "\n".join(out) + "\n"
assert "\r" not in text
assert text.count("\n@@") or text.startswith("--- ")

# ==================== 4. self-check: replay ====================
gpu_section = "\n".join(gd) + "\n"
back = apply_unified(post105, gpu_section)
assert back == post, "gpu.c self-check failed"
print("gpu.c self-check ok")

# ==================== 5. git apply on a simulated tree ====================
if os.path.exists(APPLYTEST):
    subprocess.run(["cmd", "/c", "rmdir", "/s", "/q", APPLYTEST])
os.makedirs(os.path.join(APPLYTEST, "bsd", "dev"))
open(os.path.join(APPLYTEST, "bsd", "dev", "gauguin_gpu.c"), "w",
     newline="\n").write(post105)
subprocess.run([GIT, "-C", APPLYTEST, "init"], capture_output=True)
open(OUT_PATCH, "w", newline="\n").write(text)
r = subprocess.run([GIT, "-C", APPLYTEST, "-c", "core.autocrlf=false", "apply",
                    OUT_PATCH], capture_output=True, text=True)
assert r.returncode == 0, r.stderr[:600]
got = open(os.path.join(APPLYTEST, "bsd", "dev", "gauguin_gpu.c"), "rb").read()
assert got == post.encode(), "git apply gpu.c differs"
print("git apply ok: gpu.c byte-identical")

print("wrote", OUT_PATCH, len(text), "bytes,",
      sum(1 for l in out if l.startswith("@@")), "hunks")
open(os.path.join(HERE, "gpu_post106.c"), "w", newline="\n").write(post)
print("gpu_post106.c saved")
