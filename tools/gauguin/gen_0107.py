# -*- coding: utf-8 -*-
"""Generate 0107-gauguin-gpu-zap-abi.patch (zap PAS argument ABI fix).

v27 verdict (the decisive one): the atomic probe worked (all four PAS calls
report available in both forms), the pre-call print landed, and then

    GAUGUIN SCM: fn=0xc2000201 interrupted x1 (cont=0x61900021 res1=0x82)

- the SMC RETURNED (status 1 = interrupted), and the reissue hard-hangs
(the log stops at line 1997 while v26 ran 4509 lines).

ROOT CAUSE: the argument vector has been wrong since 0105. We passed the
PAS id as arg0:

    INIT_IMAGE    arginfo 0x82, args (13, meta_pa)
    MEM_SETUP     arginfo 0x3,  args (13, img_pa, 0x1000)
    AUTH_AND_RESET arginfo 0x1, args (13)

So the TZ read PHYSICAL ADDRESS 13 as the metadata blob, took the fault,
returned interrupted, and wedged on the reissue.

The only PAS sequence proven on hardware is fajita console75c
(tools/gauguin/refs/fajita-NOTES.md:1799-1803):

    INIT_IMAGE      fn=0x42000201 arginfo=0x82 args=0x102501000/0    -> status 0 result 0
    MEM_SETUP       fn=0x42000202 arginfo=0x3  args=0x8c415000/0x1000 -> status 0 result 0
    AUTH_AND_RESET  fn=0x42000205 arginfo=0x1  args=0/0              -> status 0 result 0

NO peripheral id appears in the arguments - this TZ's GPU loader serves
PAS 13 by default. The ABI text (mxtp-NOTES.md:128) confirms the encoding:
fn = 0x40000000 | 0x02000000 | svc<<8 | cmd, arginfo = n | type_i<<(4+2i)
(VAL 0 / RO 1 / RW 2).

0107:
  1. Uses the fajita argument vector verbatim for all four calls (yielding
     form - fajita succeeded on the FIRST call, and our atomic form also
     came back interrupted, so bit 31 buys nothing on this TZ).
  2. Prints fn/arginfo/args before each call so the next log can be diffed
     against fajita line by line.
  3. Caps the interrupted reissue at 2 (fajita needed one; two interrupted
     in a row with a VALID argument set means the service is wedged - give
     up instead of spinning into the v26/v27 hard hang).
"""
import difflib, os, re, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
P = r"D:\hackios\ios-sweet\xnu\patches"
GPU_C = "bsd/dev/gauguin_gpu.c"
OUT_PATCH = os.path.join(P, "0107-gauguin-gpu-zap-abi.patch")
APPLYTEST = r"D:\hackios\temp\applytest107"
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


post106 = read(os.path.join(HERE, "gpu_post106.c"))
print("post-0106 base: %d lines" % post106.count("\n"))
post = post106

# S1: document the argument ABI next to the id define
post = sub1(post,
    "#define ZAP_PAS_ID       13u\n"
    "#define ZAP_FN_INIT      0x42000201u\n",
    "/*\n"
    " * ZAP_PAS_ID (13) is the target this TZ's GPU loader serves by\n"
    " * default: the fajita-proven sequence carries NO peripheral id in\n"
    " * the arguments. v27 proved the old (13, meta_pa) order made the TZ\n"
    " * read physical address 13 as the metadata blob - interrupted on the\n"
    " * first call, hard hang on the reissue. The define is diagnostics\n"
    " * only from here on.\n"
    " */\n"
    "#define ZAP_PAS_ID       13u\n"
    "#define ZAP_FN_INIT      0x42000201u\n",
    "S1 ABI comment")

# S2: interrupted reissue bounded at 2
post = sub1(post,
    "\t\t\tif (intr >= 500) {\n"
    "\t\t\t\tbreak;     /* give up: caller prints status 1 */\n"
    "\t\t\t}\n",
    "\t\t\tif (intr >= 2) {\n"
    "\t\t\t\t/*\n"
    "\t\t\t\t * fajita's INIT_IMAGE returned on the FIRST\n"
    "\t\t\t\t * call. Two interrupted in a row with a valid\n"
    "\t\t\t\t * argument set means the service is wedged -\n"
    "\t\t\t\t * stop instead of spinning into the v26/v27\n"
    "\t\t\t\t * hard hang (the log just ends mid-loop).\n"
    "\t\t\t\t */\n"
    "\t\t\t\tbreak;\n"
    "\t\t\t}\n",
    "S2 reissue bound")

# S3: INIT_IMAGE - fajita vector, yielding form
post = sub1(post,
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
    "\t/*\n"
    "\t * fajita console75c, the only PAS sequence proven on hardware:\n"
    "\t * fn=0x42000201 arginfo=0x82 args=(metadata_pa, 0) -> status 0\n"
    "\t * result 0, on the first call. No peripheral id in the\n"
    "\t * arguments (v27's (13, meta_pa) made the TZ read PA 13).\n"
    "\t */\n"
    "\tprintf(\"GAUGUIN ZAP: INIT_IMAGE fn=%#x arginfo=%#x \"\n"
    "\t    \"args=%#llx/%#llx\\n\", ZAP_FN_INIT, 0x82u,\n"
    "\t    (unsigned long long)meta_pa, (unsigned long long)0);\n"
    "\t(void)sweet_log_flush_now();\n"
    "\tst = gauguin_scm_call(ZAP_FN_INIT, 0x82u, meta_pa, 0, 0, &r1);\n"
    "\tprintf(\"GAUGUIN ZAP: INIT_IMAGE status %lld result %llu\\n\",\n"
    "\t    (long long)st, (unsigned long long)r1);\n"
    "\t(void)sweet_log_flush_now();\n"
    "\tif (st != 0) {\n"
    "\t\treturn false;\n"
    "\t}\n",
    "S3 INIT_IMAGE vector")

# S4: MEM_SETUP - fajita vector
post = sub1(post,
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
    "\t/* fajita form: arginfo 0x3, (image_pa, size), both VAL. */\n"
    "\tprintf(\"GAUGUIN ZAP: MEM_SETUP fn=%#x arginfo=%#x \"\n"
    "\t    \"args=%#llx/%#llx\\n\", ZAP_FN_MEM, 0x3u,\n"
    "\t    (unsigned long long)img_pa, (unsigned long long)0x1000);\n"
    "\t(void)sweet_log_flush_now();\n"
    "\tst = gauguin_scm_call(ZAP_FN_MEM, 0x3u, img_pa, 0x1000u, 0, &r1);\n"
    "\tprintf(\"GAUGUIN ZAP: MEM_SETUP status %lld result %llu\\n\",\n"
    "\t    (long long)st, (unsigned long long)r1);\n"
    "\t(void)sweet_log_flush_now();\n"
    "\tif (st != 0) {\n"
    "\t\treturn false;\n"
    "\t}\n",
    "S4 MEM_SETUP vector")

# S5: AUTH_AND_RESET - fajita vector (arginfo 0x1, (0))
post = sub1(post,
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
    "\t/* fajita form: arginfo 0x1, (0). */\n"
    "\tprintf(\"GAUGUIN ZAP: AUTH_AND_RESET fn=%#x arginfo=%#x args=0/0\\n\",\n"
    "\t    ZAP_FN_AUTH, 0x1u);\n"
    "\t(void)sweet_log_flush_now();\n"
    "\tst = gauguin_scm_call(ZAP_FN_AUTH, 0x1u, 0, 0, 0, &r1);\n"
    "\tprintf(\"GAUGUIN ZAP: AUTH_AND_RESET status %lld result %llu\\n\",\n"
    "\t    (long long)st, (unsigned long long)r1);\n"
    "\t(void)sweet_log_flush_now();\n"
    "\tif (st != 0) {\n"
    "\t\treturn false;\n"
    "\t}\n",
    "S5 AUTH vector")

# S6: SHUTDOWN - fajita form
post = sub1(post,
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
    "\t\tint64_t st = gauguin_scm_call(ZAP_FN_SHUTDOWN, 0x1u,\n"
    "\t\t    0, 0, 0, NULL);\n"
    "\t\tprintf(\"GAUGUIN ZAP: SHUTDOWN status %lld\\n\", (long long)st);\n"
    "\t\t(void)sweet_log_flush_now();\n",
    "S6 SHUTDOWN vector")

# ==================== sanity ====================
assert "ZAP_FN_INIT, 0x82u, meta_pa, 0, 0, &r1" in post
assert "ZAP_FN_MEM, 0x3u, img_pa, 0x1000u, 0, &r1" in post
assert "ZAP_FN_AUTH, 0x1u, 0, 0, 0, &r1" in post
assert "ZAP_FN_SHUTDOWN, 0x1u,\n\t\t    0, 0, 0, NULL" in post
assert post.count("ZAP_PAS_ID") == 2, post.count("ZAP_PAS_ID")   # define + comment
assert post.count("ZAP_FN_ATOMIC") == 2, post.count("ZAP_FN_ATOMIC")  # define + probe
assert post.count("(atomic)") == 0, post.count("(atomic)")
assert post.count("(yield)") == 0, post.count("(yield)")
assert "GAUGUIN ZAP: INIT_IMAGE fn=%#x arginfo=%#x " in post
assert "GAUGUIN ZAP: MEM_SETUP fn=%#x arginfo=%#x " in post
assert "GAUGUIN ZAP: AUTH_AND_RESET fn=%#x arginfo=%#x args=0/0" in post
assert 'GAUGUIN ZAP: INIT_IMAGE status %lld result %llu\\n' in post
assert post.count("if (intr >= 2)") == 1
# v26/v27 marks that must survive
for m in ["GAUGUIN ZAP: pas %s available: status %lld result %llu",
          "GAUGUIN ZAP: pas %s ATOMIC available: status %lld ",
          "GAUGUIN ZAP: meta copied %u dwords, %u mismatch",
          "GAUGUIN ZAP: segment %u bytes -> img+0",
          "GAUGUIN ZAP: authenticated and started (PAS 13)",
          "GAUGUIN SCM: fn=%#x interrupted x%u ",
          "GAUGUIN CP: ME_INIT %s (rptr=%u wptr=%u)",
          "GAUGUIN CP: started - the CP executes our packets"]:
    assert m in post, m

# ==================== diff ====================
gd = list(difflib.unified_diff(post106.split("\n"), post.split("\n"),
                               fromfile="a/" + GPU_C, tofile="b/" + GPU_C,
                               lineterm="", n=3))
text = "\n".join(gd) + "\n"
assert "\r" not in text

# ==================== self-check ====================
gpu_section = "\n".join(gd) + "\n"
back = apply_unified(post106, gpu_section)
assert back == post, "gpu.c self-check failed"
print("gpu.c self-check ok")

if os.path.exists(APPLYTEST):
    subprocess.run(["cmd", "/c", "rmdir", "/s", "/q", APPLYTEST])
os.makedirs(os.path.join(APPLYTEST, "bsd", "dev"))
open(os.path.join(APPLYTEST, "bsd", "dev", "gauguin_gpu.c"), "w",
     newline="\n").write(post106)
subprocess.run([GIT, "-C", APPLYTEST, "init"], capture_output=True)
open(OUT_PATCH, "w", newline="\n").write(text)
r = subprocess.run([GIT, "-C", APPLYTEST, "-c", "core.autocrlf=false", "apply",
                    OUT_PATCH], capture_output=True, text=True)
assert r.returncode == 0, r.stderr[:600]
got = open(os.path.join(APPLYTEST, "bsd", "dev", "gauguin_gpu.c"), "rb").read()
assert got == post.encode(), "git apply gpu.c differs"
print("git apply ok: gpu.c byte-identical")

print("wrote", OUT_PATCH, len(text), "bytes,",
      sum(1 for l in gd if l.startswith("@@")), "hunks")
open(os.path.join(HERE, "gpu_post107.c"), "w", newline="\n").write(post)
print("gpu_post107.c saved")
