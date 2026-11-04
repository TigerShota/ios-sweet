# -*- coding: utf-8 -*-
"""Generate 0107-gauguin-gpu-zap-abi.patch (zap PAS ABI, device-exact).

SOURCES OF TRUTH (not sibling-port logs):
 - Xiaomi gauguin-r-oss drivers/firmware/qcom_scm-64.c (the kernel this phone
   shipped): qcom_scm_pas_init_image() -> SCM_ARGS(2, SCM_VAL, SCM_RW),
   args[0] = pas id, args[1] = metadata pa; pas_mem_setup() -> SCM_ARGS(3)
   (pas id, addr, size); pas_auth_and_reset()/pas_shutdown() -> SCM_ARGS(1)
   (pas id).
 - mainline drivers/firmware/qcom/qcom_scm.c: identical vector.
 - The real calls go out as ARM_SMCCC_STD_CALL (bit 31 clear) | SMC_64
   (bit 30) | OWNER_SIP (0x02 << 24) = 0x4200020x; only the convention probe
   uses FAST_CALL. x7 (SMCCC a7) is 0.
 - Interrupted handling (mainline qcom_scm-smc.c + arm64 smccc-call.S QCOM_A6
   quirk): x0 == 1 means the TZ yielded; the resume re-enters with x0 = 1,
   the same x1..x5 and x6 = the a6 value the call returned.

WHY THE PREVIOUS 0107 WAS WRONG: it copied fajita console75c's vector
(args = (mdata_pa, 0), no pas id). That is a different TZ generation. The
v26/v27 argument vector (pas id first) was already correct for gauguin, so
the interrupted status was never an argument problem.

WHAT ACTUALLY CHANGED FROM v27 (0106):
 1. x7 = 0 (SMCCC a7). v27 left x7 as compiler garbage and clobbered it.
 2. Interrupted resume uses x0 = 1 instead of the original fn (mainline's
    form). v27 reissued with the original fn; the second SMC never returned.
 3. Reissue bounded at 64 with logs (v26 spun silently).
 4. The atomic (bit 31) first attempt and its probes are dropped: v27 proved
    the fast form is interrupted too, so it buys nothing.
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

# S1: ABI header - cite the device kernel, x7, the resume protocol
post = sub1(post,
    " * qcom,kgsl-hyp pas-id 13, firmware-name a615_zap). mxtp 0092 proved\n"
    " * these PAS calls under XNU on sweet, fajita console75c/76 the whole\n"
    " * ladder on a630. SMC64 convention (0065 PSCI conduit, smc #0): x0 fn\n"
    " * (bit30 | bit25 | svc<<8 | cmd, bit31 only for atomic probes), x1\n"
    " * arginfo (n | type << (4+2i)), x2-x4 args, x6 continuation; x0 status\n"
    " * (0 ok, 1 interrupted -> reissue, -12 busy -> 30 ms, 67 tries), x1\n"
    " * result. Carve-out is our own 1 MiB-PA-aligned block: the DT\n"
    " * gpu_region 0x8b715400 is an orphan the vendor loader never uses.\n"
    " */\n"
    "#define ZAP_PAS_ID       13u\n",
    " * qcom,kgsl-hyp pas-id 13, firmware-name a615_zap). mxtp 0092 proved\n"
    " * these PAS calls under XNU on sweet, fajita console75c/76 the whole\n"
    " * ladder on a630. SMC64 convention (0065 PSCI conduit, smc #0): x0 fn\n"
    " * (0x40000000 | 0x02000000 | svc<<8 | cmd), x1 arginfo (n | type <<\n"
    " * (4+2i)), x2-x4 args, x6 a6, x7 = 0 (the vendor passes SMCCC a7 = 0);\n"
    " * x0 status (0 ok, 1 interrupted, -12 busy -> 30 ms, 67 tries), x1\n"
    " * result.\n"
    " * ARGUMENT VECTOR - the device's own ABI, not fajita's: the gauguin\n"
    " * kernel (Xiaomi gauguin-r-oss drivers/firmware/qcom_scm-64.c) and\n"
    " * mainline qcom_scm.c both send the PAS id FIRST - INIT_IMAGE\n"
    " * SCM_ARGS(2, VAL, RW) args (13, mdata_pa); MEM_SETUP SCM_ARGS(3)\n"
    " * args (13, addr, size); AUTH_AND_RESET and SHUTDOWN SCM_ARGS(1)\n"
    " * args (13). fajita's (mdata_pa, 0) form is a different TZ generation\n"
    " * and leaves PAS 13 unserved here.\n"
    " * INTERRUPTED is a resume, not a reissue: x0 == 1 means the TZ yielded;\n"
    " * the next call enters with x0 = 1, the same x1..x5 and x6 = the a6\n"
    " * that came back (arm64 smccc-call.S stores x6 in the QCOM_A6 quirk).\n"
    " * v27 reissued with the original fn and the second SMC never returned.\n"
    " * Carve-out is our own 1 MiB-PA-aligned block: the DT gpu_region\n"
    " * 0x8b715400 is an orphan the vendor loader never uses.\n"
    " */\n"
    "#define ZAP_PAS_ID       13u\n",
    "S1 ABI header")

# S2: drop the atomic-form define
post = sub1(post,
    "#define ZAP_FN_AVAIL     0xC2000601u\n"
    "#define ZAP_FN_ATOMIC    0x80000000u  /* same fn, complete-now form */\n"
    "#define ZAP_MDT_DWORDS   1715u\n",
    "#define ZAP_FN_AVAIL     0xC2000601u\n"
    "#define ZAP_MDT_DWORDS   1715u\n",
    "S2 drop atomic define")

# S3: SMC call - x7 = 0, resume with x0 = 1, bounded
post = sub1(post,
    "/* One SCM SMC64 call with the interrupted/busy retry rules. */\n"
    "static int64_t\n"
    "gauguin_scm_call(uint32_t fn, uint32_t arginfo, uint64_t a0, uint64_t a1,\n"
    "    uint64_t a2, uint64_t *ret1)\n"
    "{\n"
    "\tint64_t status;\n"
    "\tuint64_t cont = 0, res1 = 0;\n"
    "\tunsigned busy = 0, intr = 0;\n"
    "\n"
    "\tfor (;;) {\n"
    "\t\tregister uint64_t x0 __asm__(\"x0\") = fn;\n"
    "\t\tregister uint64_t x1 __asm__(\"x1\") = arginfo;\n"
    "\t\tregister uint64_t x2 __asm__(\"x2\") = a0;\n"
    "\t\tregister uint64_t x3 __asm__(\"x3\") = a1;\n"
    "\t\tregister uint64_t x4 __asm__(\"x4\") = a2;\n"
    "\t\tregister uint64_t x5 __asm__(\"x5\") = 0;\n"
    "\t\tregister uint64_t x6 __asm__(\"x6\") = cont;\n"
    "\n"
    "\t\t__asm__ volatile(\"smc #0\" : \"+r\"(x0), \"+r\"(x1), \"+r\"(x2),\n"
    "\t\t    \"+r\"(x3), \"+r\"(x4), \"+r\"(x5), \"+r\"(x6) : :\n"
    "\t\t    \"x7\", \"x8\", \"x9\", \"x10\", \"x11\", \"x12\", \"x13\",\n"
    "\t\t    \"x14\", \"x15\", \"x16\", \"x17\", \"cc\", \"memory\");\n"
    "\t\tstatus = (int64_t)x0;\n"
    "\t\tres1 = x1;\n"
    "\t\tcont = x6;\n"
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
    "\t\tbreak;\n"
    "\t}\n"
    "\tif (ret1 != NULL) {\n"
    "\t\t*ret1 = res1;\n"
    "\t}\n"
    "\treturn status;\n"
    "}\n",
    "/*\n"
    " * One SCM SMC64 call: x0 fn, x1 arginfo, x2-x4 args, x6 a6, x7 = 0.\n"
    " * Interrupted rules from the device's own kernel (qcom_scm-64.c) and\n"
    " * mainline (qcom_scm-smc.c): x0 == 1 means the TZ yielded, and the\n"
    " * resume enters with x0 = 1, the same x1..x5 and x6 = the a6 that came\n"
    " * back. v27 reissued with the ORIGINAL fn and the second SMC never\n"
    " * returned. -12 is busy (30 ms, at most 67 tries).\n"
    " */\n"
    "static int64_t\n"
    "gauguin_scm_call(uint32_t fn, uint32_t arginfo, uint64_t a0, uint64_t a1,\n"
    "    uint64_t a2, uint64_t *ret1)\n"
    "{\n"
    "\tint64_t status;\n"
    "\tuint64_t cont = 0, res1 = 0, next = fn;\n"
    "\tunsigned busy = 0, intr = 0;\n"
    "\n"
    "\tfor (;;) {\n"
    "\t\tregister uint64_t x0 __asm__(\"x0\") = next;\n"
    "\t\tregister uint64_t x1 __asm__(\"x1\") = arginfo;\n"
    "\t\tregister uint64_t x2 __asm__(\"x2\") = a0;\n"
    "\t\tregister uint64_t x3 __asm__(\"x3\") = a1;\n"
    "\t\tregister uint64_t x4 __asm__(\"x4\") = a2;\n"
    "\t\tregister uint64_t x5 __asm__(\"x5\") = 0;\n"
    "\t\tregister uint64_t x6 __asm__(\"x6\") = cont;\n"
    "\t\tregister uint64_t x7 __asm__(\"x7\") = 0;   /* SMCCC a7 */\n"
    "\n"
    "\t\t__asm__ volatile(\"smc #0\" : \"+r\"(x0), \"+r\"(x1), \"+r\"(x2),\n"
    "\t\t    \"+r\"(x3), \"+r\"(x4), \"+r\"(x5), \"+r\"(x6), \"+r\"(x7) : :\n"
    "\t\t    \"x8\", \"x9\", \"x10\", \"x11\", \"x12\", \"x13\", \"x14\",\n"
    "\t\t    \"x15\", \"x16\", \"x17\", \"cc\", \"memory\");\n"
    "\t\tstatus = (int64_t)x0;\n"
    "\t\tres1 = x1;\n"
    "\t\tcont = x6;\n"
    "\t\tif (status == 1) {\n"
    "\t\t\t/* Interrupted: bounded and logged - v26 spun in this\n"
    "\t\t\t * loop silently; every beat must leave evidence. */\n"
    "\t\t\tif (++intr <= 4 || (intr % 16) == 0) {\n"
    "\t\t\t\tprintf(\"GAUGUIN SCM: fn=%#x interrupted x%u \"\n"
    "\t\t\t\t    \"(cont=%#llx res1=%#llx)\\n\", fn, intr,\n"
    "\t\t\t\t    (unsigned long long)cont,\n"
    "\t\t\t\t    (unsigned long long)res1);\n"
    "\t\t\t\t(void)sweet_log_flush_now();\n"
    "\t\t\t}\n"
    "\t\t\tif (intr >= 64) {\n"
    "\t\t\t\tbreak;     /* give up: caller prints status 1 */\n"
    "\t\t\t}\n"
    "\t\t\tnext = 1;      /* resume entry, NOT the original fn */\n"
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
    "\t\tbreak;\n"
    "\t}\n"
    "\tif (ret1 != NULL) {\n"
    "\t\t*ret1 = res1;\n"
    "\t}\n"
    "\treturn status;\n"
    "}\n",
    "S3 SMC call")

# S4: drop the atomic probe loop (bit 31 buys nothing - v27 proved it)
post = sub1(post,
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
    "\t}\n"
    "\n",
    "",
    "S4 drop atomic probe")

# S5: INIT_IMAGE - device vector (pas id first)
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
    "\t/* qcom_scm_pas_init_image(): SCM_ARGS(2, VAL, RW), (pas id, mdata). */\n"
    "\tprintf(\"GAUGUIN ZAP: INIT_IMAGE fn=%#x arginfo=%#x \"\n"
    "\t    \"args=%#llx/%#llx\\n\", ZAP_FN_INIT, 0x82u,\n"
    "\t    (unsigned long long)ZAP_PAS_ID, (unsigned long long)meta_pa);\n"
    "\t(void)sweet_log_flush_now();\n"
    "\tst = gauguin_scm_call(ZAP_FN_INIT, 0x82u, ZAP_PAS_ID, meta_pa, 0, &r1);\n"
    "\tprintf(\"GAUGUIN ZAP: INIT_IMAGE status %lld result %llu\\n\",\n"
    "\t    (long long)st, (unsigned long long)r1);\n"
    "\t(void)sweet_log_flush_now();\n"
    "\tif (st != 0) {\n"
    "\t\treturn false;\n"
    "\t}\n",
    "S5 INIT vector")

# S6: MEM_SETUP - device vector (pas id, addr, size)
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
    "\t/* qcom_scm_pas_mem_setup(): SCM_ARGS(3), (pas id, addr, size). */\n"
    "\tprintf(\"GAUGUIN ZAP: MEM_SETUP fn=%#x arginfo=%#x \"\n"
    "\t    \"args=%#llx/%#llx\\n\", ZAP_FN_MEM, 0x3u,\n"
    "\t    (unsigned long long)ZAP_PAS_ID, (unsigned long long)img_pa);\n"
    "\t(void)sweet_log_flush_now();\n"
    "\tst = gauguin_scm_call(ZAP_FN_MEM, 0x3u, ZAP_PAS_ID, img_pa, 0x1000u,\n"
    "\t    &r1);\n"
    "\tprintf(\"GAUGUIN ZAP: MEM_SETUP status %lld result %llu\\n\",\n"
    "\t    (long long)st, (unsigned long long)r1);\n"
    "\t(void)sweet_log_flush_now();\n"
    "\tif (st != 0) {\n"
    "\t\treturn false;\n"
    "\t}\n",
    "S6 MEM_SETUP vector")

# S7: AUTH_AND_RESET - device vector (pas id)
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
    "\t/* qcom_scm_pas_auth_and_reset(): SCM_ARGS(1), (pas id). */\n"
    "\tprintf(\"GAUGUIN ZAP: AUTH_AND_RESET fn=%#x arginfo=%#x args=%#llx/0\\n\",\n"
    "\t    ZAP_FN_AUTH, 0x1u, (unsigned long long)ZAP_PAS_ID);\n"
    "\t(void)sweet_log_flush_now();\n"
    "\tst = gauguin_scm_call(ZAP_FN_AUTH, 0x1u, ZAP_PAS_ID, 0, 0, &r1);\n"
    "\tprintf(\"GAUGUIN ZAP: AUTH_AND_RESET status %lld result %llu\\n\",\n"
    "\t    (long long)st, (unsigned long long)r1);\n"
    "\t(void)sweet_log_flush_now();\n"
    "\tif (st != 0) {\n"
    "\t\treturn false;\n"
    "\t}\n",
    "S7 AUTH vector")

# S8: SHUTDOWN - device vector (pas id)
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
    "\t\t    ZAP_PAS_ID, 0, 0, NULL);\n"
    "\t\tprintf(\"GAUGUIN ZAP: SHUTDOWN status %lld\\n\", (long long)st);\n"
    "\t\t(void)sweet_log_flush_now();\n",
    "S8 SHUTDOWN vector")

# ==================== sanity ====================
assert "ZAP_FN_INIT, 0x82u, ZAP_PAS_ID, meta_pa, 0, &r1" in post
assert "ZAP_FN_MEM, 0x3u, ZAP_PAS_ID, img_pa, 0x1000u" in post
assert "ZAP_FN_AUTH, 0x1u, ZAP_PAS_ID, 0, 0, &r1" in post
assert "ZAP_FN_SHUTDOWN, 0x1u,\n\t\t    ZAP_PAS_ID, 0, 0, NULL" in post
assert post.count("ZAP_PAS_ID") == 8, post.count("ZAP_PAS_ID")
assert post.count("ZAP_FN_ATOMIC") == 0, post.count("ZAP_FN_ATOMIC")
assert post.count("(atomic)") == 0, post.count("(atomic)")
assert post.count("(yield)") == 0, post.count("(yield)")
assert 'next = 1;      /* resume entry' in post
assert 'register uint64_t x7 __asm__("x7") = 0;' in post
assert '"x8", "x9", "x10", "x11", "x12", "x13", "x14",' in post
assert post.count("if (intr >= 64)") == 1
assert "GAUGUIN ZAP: INIT_IMAGE fn=%#x arginfo=%#x " in post
assert "GAUGUIN ZAP: MEM_SETUP fn=%#x arginfo=%#x " in post
assert "GAUGUIN ZAP: AUTH_AND_RESET fn=%#x arginfo=%#x args=%#llx/0" in post
assert 'GAUGUIN ZAP: INIT_IMAGE status %lld result %llu\\n' in post
# v26/v27 marks that must survive
for m in ["GAUGUIN ZAP: pas %s available: status %lld result %llu",
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
back = apply_unified(post106, text)
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
print("gpu_post107.c saved (device-exact ABI, x7=0, resume x0=1)")
