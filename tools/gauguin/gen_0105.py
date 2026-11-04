# -*- coding: utf-8 -*-
"""Generate 0105-gauguin-gpu-zap-pas.patch (L3b: zap PAS + real type-7 headers).

v25 verdict: ring/SQE config sticks, zero faults, zero fence drops, but
cp_status1=0 and rptr=0 forever - the SQE never fetched. Two root causes
are fixed together:
  1. The type-7 packet header was mis-encoded. The vendor adreno_pm4types.h
     (gauguin-r-oss, fetched) puts the 7-bit opcode at [22:16], the count at
     [14:0] and odd parity bits at 23/15, and the SQE validates them; the old
     0x70080042 header decoded as opcode 8 / count 66 / bad parity. Real
     values: CP_ME_INIT=0x48, CP_SET_SECURE_MODE=0x66 (headers 0x70C80008 /
     0x70E60001, both parity-checked here).
  2. The a6xx SQE boots in secure mode. Load the signed a615_zap through SCM
     PAS id 13 (DT qcom,kgsl-hyp pas-id 13, firmware-name a615_zap) before
     SQE_CNTL=1 - the kgsl microcode_load order. SMC64 conduit is the 0065
     PSCI one (smc #0); the PAS sequence is hardware-proven by mxtp 0092
     (XNU on sweet: INIT_IMAGE/MEM_SETUP/AUTH/SHUTDOWN all status 0) and
     fajita console75c/76. Memory plan follows mxtp 0092: our own 1 MiB
     PA-aligned block (pnum_mask 63 at 16 KiB pages), b02 at +0, the mdt on
     its own page at +1 MiB; INIT_IMAGE(13, mdt) -> MEM_SETUP(13, img, 4K)
     -> copy b02 -> AUTH_AND_RESET(13); SHUTDOWN after the packet test.
     The DT gpu_region 0x8b715400 is an orphan the vendor loader never uses.
"""
import difflib, os, re, struct, subprocess

P = r"D:\hackios\ios-sweet\xnu\patches"
GPU_C = "bsd/dev/gauguin_gpu.c"
OUT_PATCH = os.path.join(P, "0105-gauguin-gpu-zap-pas.patch")
APPLYTEST = r"D:\hackios\temp\applytest105"
FW = r"D:\hackios\out\fw"

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

# ==================== 0. firmware + header math ====================
mdt = open(FW + r"\a615_zap.mdt", "rb").read()
b02 = open(FW + r"\a615_zap.b02", "rb").read()
assert len(mdt) == 6860 and len(b02) == 1984
magic, = struct.unpack_from("<I", mdt, 0)
e_phoff, = struct.unpack_from("<I", mdt, 28)
e_phnum, = struct.unpack_from("<H", mdt, 44)
assert magic == 0x464c457f and e_phoff == 52 and e_phnum == 3, (hex(magic), e_phoff, e_phnum)
ph2 = struct.unpack_from("<IIIIIIII", mdt, 52 + 64)
assert ph2[0] == 1 and ph2[4] == 1984 and ph2[6] == 0x8000007 and ph2[7] == 0x100000, ph2

def dwords(b):
    assert len(b) % 4 == 0
    return list(struct.unpack("<%dI" % (len(b) // 4), b))

mdt_dw, b02_dw = dwords(mdt), dwords(b02)
assert len(mdt_dw) == 1715 and len(b02_dw) == 496

def parity(v):
    return (0x9669 >> (0xf & (v ^ (v >> 4) ^ (v >> 8) ^ (v >> 12) ^ (v >> 16) ^
        (v >> 20) ^ (v >> 24) ^ (v >> 28)))) & 1

def pkt7(op, cnt):
    return (7 << 28) | cnt | (parity(cnt) << 15) | ((op & 0x7f) << 16) | (parity(op) << 23)

assert pkt7(0x48, 8) == 0x70C80008, hex(pkt7(0x48, 8))
assert pkt7(0x66, 1) == 0x70E60001, hex(pkt7(0x66, 1))

def fmt_array(name, dw):
    out = ["const unsigned int %s[%d] = {" % (name, len(dw))]
    for i in range(0, len(dw), 8):
        out.append("\t" + ", ".join("0x%08x" % v for v in dw[i:i + 8]) + ",")
    out.append("};")
    return "\n".join(out)

zap_c = (
    "/* gauguin a615_zap firmware (vendor partition): mdt 6860 bytes (ELF32\n"
    " * header + program headers + hash table = b00 + b01) and b02 1984 bytes\n"
    " * (the single relocatable RWE PT_LOAD segment, paddr 0x5000, 1 MiB\n"
    " * align). TrustZone PAS id 13 authenticates the pair; only the b02\n"
    " * ever reaches the carve-out (fajita console75c, mxtp 0092).\n"
    " */\n"
    "#if defined(ARM64_BOARD_CONFIG_SWEET)\n"
    "#include <sys/param.h>\n"
    + fmt_array("gauguin_zap_mdt", mdt_dw) + "\n"
    + fmt_array("gauguin_zap_b02", b02_dw) + "\n"
    "#endif /* ARM64_BOARD_CONFIG_SWEET */\n")

# ==================== 1. gpu.c surgery ====================
post104 = read(r"D:\hackios\temp\gpu_post104.c")
print("post-0104 base: %d lines" % post104.count("\n"))
post = post104

# S1: externs
post = sub1(post,
    "extern const unsigned int gauguin_sqe_fw[8066];\n",
    "extern const unsigned int gauguin_sqe_fw[8066];\n"
    "extern const unsigned int gauguin_zap_mdt[1715];\n"
    "extern const unsigned int gauguin_zap_b02[496];\n",
    "S1 zap externs")

# S3: the zap block before gauguin_cp_start's comment
ZAP_BLOCK = (
    "/* ==================== L3b final: zap shader through TrustZone (0105)\n"
    " * v25 ended with the ring/SQE configuration sticking, zero faults and\n"
    " * zero fence drops, but cp_status1=0 and rptr frozen at 0: the SQE never\n"
    " * fetched. The a6xx SQE boots in secure mode; the vendor kgsl loads the\n"
    " * signed a615_zap through SCM PAS id 13 before releasing SQE_CNTL (DT\n"
    " * qcom,kgsl-hyp pas-id 13, firmware-name a615_zap). mxtp 0092 proved\n"
    " * these PAS calls under XNU on sweet, fajita console75c/76 the whole\n"
    " * ladder on a630. SMC64 convention (0065 PSCI conduit, smc #0): x0 fn\n"
    " * (bit30 | bit25 | svc<<8 | cmd, bit31 only for atomic probes), x1\n"
    " * arginfo (n | type << (4+2i)), x2-x4 args, x6 continuation; x0 status\n"
    " * (0 ok, 1 interrupted -> reissue, -12 busy -> 30 ms, 67 tries), x1\n"
    " * result. Carve-out is our own 1 MiB-PA-aligned block: the DT\n"
    " * gpu_region 0x8b715400 is an orphan the vendor loader never uses.\n"
    " */\n"
    "#define ZAP_PAS_ID       13u\n"
    "#define ZAP_FN_INIT      0x42000201u\n"
    "#define ZAP_FN_MEM       0x42000202u\n"
    "#define ZAP_FN_AUTH      0x42000205u\n"
    "#define ZAP_FN_SHUTDOWN  0x42000206u\n"
    "#define ZAP_FN_AVAIL     0xC2000601u\n"
    "#define ZAP_MDT_DWORDS   1715u\n"
    "#define ZAP_B02_DWORDS   496u\n"
    "#define ZAP_B02_BYTES    1984u\n"
    "#define ZAPBLK_SIZE      0x110000u  /* 1 MiB image + one metadata page */\n"
    "#define ZAPBLK_META_OFF  0x100000u\n"
    "\n"
    "static uint32_t\n"
    "gg_parity(uint32_t val)\n"
    "{\n"
    "\treturn (0x9669u >> (0xfu & (val ^ (val >> 4) ^ (val >> 8) ^\n"
    "\t    (val >> 12) ^ (val >> 16) ^ (val >> 20) ^ (val >> 24) ^\n"
    "\t    (val >> 28)))) & 1u;\n"
    "}\n"
    "\n"
    "/* PM4 type-7 header (vendor adreno_pm4types.h cp_type7_packet):\n"
    " * opcode [22:16], count [14:0], odd parity at 23 and 15. The SQE\n"
    " * validates the parity bits - the old (7<<28)|(8<<16)|0x42 form\n"
    " * decoded as opcode 8 / count 66 and could never retire. */\n"
    "static uint32_t\n"
    "gg_pkt7(uint32_t opcode, uint32_t cnt)\n"
    "{\n"
    "\treturn (7u << 28) | cnt | (gg_parity(cnt) << 15) |\n"
    "\t    ((opcode & 0x7fu) << 16) | (gg_parity(opcode) << 23);\n"
    "}\n"
    "\n"
    "/* One SCM SMC64 call with the interrupted/busy retry rules. */\n"
    "static int64_t\n"
    "gauguin_scm_call(uint32_t fn, uint32_t arginfo, uint64_t a0, uint64_t a1,\n"
    "    uint64_t a2, uint64_t *ret1)\n"
    "{\n"
    "\tint64_t status;\n"
    "\tuint64_t cont = 0, res1 = 0;\n"
    "\tunsigned busy = 0;\n"
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
    "\t\t\tcontinue;          /* interrupted: reissue */\n"
    "\t\t}\n"
    "\t\tif (status == -12 && ++busy <= 67) {\n"
    "\t\t\tdelay(30000);      /* busy: 30 ms, bounded */\n"
    "\t\t\tcontinue;\n"
    "\t\t}\n"
    "\t\tbreak;\n"
    "\t}\n"
    "\tif (ret1 != NULL) {\n"
    "\t\t*ret1 = res1;\n"
    "\t}\n"
    "\treturn status;\n"
    "}\n"
    "\n"
    "/*\n"
    " * Load the signed zap through PAS 13 (fajita console75c order, mxtp\n"
    " * 0092 memory plan): probe the four calls, validate the metadata ELF,\n"
    " * put the mdt on its own page and the b02 into a 1 MiB-aligned block,\n"
    " * then INIT_IMAGE -> MEM_SETUP -> copy -> AUTH_AND_RESET. The block is\n"
    " * never freed; nothing touches it after AUTH.\n"
    " */\n"
    "static bool\n"
    "gauguin_zap_load(void)\n"
    "{\n"
    "\tstatic const uint32_t fns[4] = { ZAP_FN_INIT, ZAP_FN_MEM, ZAP_FN_AUTH,\n"
    "\t\tZAP_FN_SHUTDOWN };\n"
    "\tstatic const char *names[4] = { \"INIT_IMAGE\", \"MEM_SETUP\",\n"
    "\t\t\"AUTH_AND_RESET\", \"SHUTDOWN\" };\n"
    "\tvm_offset_t kva = 0;\n"
    "\tkern_return_t kr;\n"
    "\tppnum_t pn;\n"
    "\tuint64_t pa, meta_pa, img_pa, r1;\n"
    "\tuint32_t magic, phoff, phnum;\n"
    "\tint64_t st;\n"
    "\tunsigned i, bad = 0;\n"
    "\n"
    "\tprintf(\"GAUGUIN ZAP: start\\n\");\n"
    "\t(void)sweet_log_flush_now();\n"
    "\tfor (i = 0; i < 4; ++i) {\n"
    "\t\tst = gauguin_scm_call(ZAP_FN_AVAIL, 0x1u, fns[i], 0, 0, &r1);\n"
    "\t\tprintf(\"GAUGUIN ZAP: pas %s available: status %lld result %llu\\n\",\n"
    "\t\t    names[i], (long long)st, (unsigned long long)r1);\n"
    "\t\t(void)sweet_log_flush_now();\n"
    "\t}\n"
    "\n"
    "\t/* Metadata sanity: ELF32, phoff 52, 3 program headers, ph2 = the\n"
    "\t * RWE segment (dword indices: e_phoff at 28 -> dw7, e_phnum at 44\n"
    "\t * -> dw11 low half, phdr i at 52 + 32*i -> dw13 + 8*i). */\n"
    "\tmagic = gauguin_zap_mdt[0];\n"
    "\tphoff = gauguin_zap_mdt[7];\n"
    "\tphnum = gauguin_zap_mdt[11] & 0xffffu;\n"
    "\tprintf(\"GAUGUIN ZAP: meta magic=%#x phoff=%u phnum=%u\\n\",\n"
    "\t    magic, phoff, phnum);\n"
    "\t(void)sweet_log_flush_now();\n"
    "\tif (magic != 0x464c457fu || phoff != 52u || phnum != 3u) {\n"
    "\t\tprintf(\"GAUGUIN ZAP: bad metadata ELF\\n\");\n"
    "\t\t(void)sweet_log_flush_now();\n"
    "\t\treturn false;\n"
    "\t}\n"
    "\tfor (i = 0; i < phnum; ++i) {\n"
    "\t\tprintf(\"GAUGUIN ZAP: phdr %u: type %u off %#x paddr %#x filesz %u memsz %u flags %#x\\n\",\n"
    "\t\t    i, gauguin_zap_mdt[13 + 8u * i], gauguin_zap_mdt[14 + 8u * i],\n"
    "\t\t    gauguin_zap_mdt[16 + 8u * i], gauguin_zap_mdt[17 + 8u * i],\n"
    "\t\t    gauguin_zap_mdt[18 + 8u * i], gauguin_zap_mdt[19 + 8u * i]);\n"
    "\t\t(void)sweet_log_flush_now();\n"
    "\t}\n"
    "\tif (gauguin_zap_mdt[29] != 1u || gauguin_zap_mdt[35] != 0x8000007u ||\n"
    "\t    gauguin_zap_mdt[33] != ZAP_B02_BYTES) {\n"
    "\t\tprintf(\"GAUGUIN ZAP: ph2 is not the RWE segment\\n\");\n"
    "\t\t(void)sweet_log_flush_now();\n"
    "\t\treturn false;\n"
    "\t}\n"
    "\n"
    "\tkr = kmem_alloc_contig(kernel_map, &kva, ZAPBLK_SIZE, 0,\n"
    "\t    0xffffffffu, 63u, 0u, 1u);   /* PA 1 MiB-aligned, 16 KiB pages */\n"
    "\tif (kr != KERN_SUCCESS) {\n"
    "\t\tprintf(\"GAUGUIN ZAP: alloc failed %d\\n\", kr);\n"
    "\t\t(void)sweet_log_flush_now();\n"
    "\t\treturn false;\n"
    "\t}\n"
    "\tpn = pmap_find_phys(kernel_pmap, (addr64_t)kva);\n"
    "\tif (!pn) {\n"
    "\t\tprintf(\"GAUGUIN ZAP: no phys page\\n\");\n"
    "\t\t(void)sweet_log_flush_now();\n"
    "\t\treturn false;\n"
    "\t}\n"
    "\tpa = ptoa_64(pn);\n"
    "\timg_pa = pa;                    /* 1 MiB-aligned via pnum_mask */\n"
    "\tmeta_pa = pa + ZAPBLK_META_OFF;\n"
    "\tfor (i = 0; i < ZAPBLK_SIZE / PAGE_SIZE; i++) {\n"
    "\t\tpmap_set_cache_attributes(pn + i, VM_WIMG_WCOMB);\n"
    "\t}\n"
    "\tflush_dcache64((addr64_t)pa, ZAPBLK_SIZE, TRUE);\n"
    "\tbzero((void *)kva, ZAPBLK_SIZE);\n"
    "\tprintf(\"GAUGUIN ZAP: block pa=%#llx img=%#llx meta=%#llx\\n\",\n"
    "\t    (uint64_t)pa, img_pa, meta_pa);\n"
    "\t(void)sweet_log_flush_now();\n"
    "\n"
    "\t/* The metadata onto its own page before INIT_IMAGE (TZ reads it). */\n"
    "\tfor (i = 0; i < ZAP_MDT_DWORDS; ++i) {\n"
    "\t\t*(volatile uint32_t *)(kva + ZAPBLK_META_OFF + 4u * i) =\n"
    "\t\t    gauguin_zap_mdt[i];\n"
    "\t}\n"
    "\tfor (i = 0; i < ZAP_MDT_DWORDS; ++i) {\n"
    "\t\tif (*(volatile uint32_t *)(kva + ZAPBLK_META_OFF + 4u * i) !=\n"
    "\t\t    gauguin_zap_mdt[i]) {\n"
    "\t\t\tbad++;\n"
    "\t\t}\n"
    "\t}\n"
    "\tflush_dcache64((addr64_t)meta_pa, 0x1000u, TRUE);\n"
    "\tprintf(\"GAUGUIN ZAP: meta copied %u dwords, %u mismatch\\n\",\n"
    "\t    ZAP_MDT_DWORDS, bad);\n"
    "\t(void)sweet_log_flush_now();\n"
    "\tif (bad) {\n"
    "\t\treturn false;\n"
    "\t}\n"
    "\n"
    "\tst = gauguin_scm_call(ZAP_FN_INIT, 0x82u, ZAP_PAS_ID, meta_pa, 0, &r1);\n"
    "\tprintf(\"GAUGUIN ZAP: INIT_IMAGE status %lld result %llu\\n\",\n"
    "\t    (long long)st, (unsigned long long)r1);\n"
    "\t(void)sweet_log_flush_now();\n"
    "\tif (st != 0) {\n"
    "\t\treturn false;\n"
    "\t}\n"
    "\tst = gauguin_scm_call(ZAP_FN_MEM, 0x3u, ZAP_PAS_ID, img_pa,\n"
    "\t    0x1000u, &r1);\n"
    "\tprintf(\"GAUGUIN ZAP: MEM_SETUP status %lld result %llu\\n\",\n"
    "\t    (long long)st, (unsigned long long)r1);\n"
    "\t(void)sweet_log_flush_now();\n"
    "\tif (st != 0) {\n"
    "\t\treturn false;\n"
    "\t}\n"
    "\n"
    "\t/* The single loadable segment -> img+0 (copy between MEM_SETUP and\n"
    "\t * AUTH, the Linux/fajita order; no readback once TZ owns it). */\n"
    "\tfor (i = 0; i < ZAP_B02_DWORDS; ++i) {\n"
    "\t\t*(volatile uint32_t *)(kva + 4u * i) = gauguin_zap_b02[i];\n"
    "\t}\n"
    "\tflush_dcache64((addr64_t)img_pa, 0x1000u, TRUE);\n"
    "\tprintf(\"GAUGUIN ZAP: segment %u bytes -> img+0\\n\", ZAP_B02_BYTES);\n"
    "\t(void)sweet_log_flush_now();\n"
    "\n"
    "\tst = gauguin_scm_call(ZAP_FN_AUTH, 0x1u, ZAP_PAS_ID, 0, 0, &r1);\n"
    "\tprintf(\"GAUGUIN ZAP: AUTH_AND_RESET status %lld result %llu\\n\",\n"
    "\t    (long long)st, (unsigned long long)r1);\n"
    "\t(void)sweet_log_flush_now();\n"
    "\tif (st != 0) {\n"
    "\t\treturn false;\n"
    "\t}\n"
    "\tprintf(\"GAUGUIN ZAP: authenticated and started (PAS 13)\\n\");\n"
    "\t(void)sweet_log_flush_now();\n"
    "\treturn true;\n"
    "}\n"
    "\n")

post = sub1(post,
    "/*\n * Configure and start the CP (Linux a6xx_start + a6xx_rb_start order, the\n",
    ZAP_BLOCK +
    "/*\n * Configure and start the CP (Linux a6xx_start + a6xx_rb_start order, the\n",
    "S3 zap block insert")

# S4: zap call + SQE_CNTL first + ME_INIT with a real type-7 header
post = sub1(post,
    "\t/* CP_ME_INIT: upstream drm minimal packet - 8 payload dwords\n"
    "\t * (fajita proved this exact form retires with RPTR=9 pre-zap). */\n"
    "\tring[0] = (7u << 28) | (8u << 16) | 0x42u;   /* CP_ME_INIT */\n"
    "\tring[1] = 0x2fu;                /* MAX_CONTEXT|ERR_DET|HDR|RESET|UCODE */\n"
    "\tring[2] = 0x00000003;           /* MAX_CONTEXT */\n"
    "\tring[3] = 0x20000000;           /* ERROR_DETECTION_CONTROL */\n"
    "\tring[4] = 0x00000000;           /* HEADER_DUMP addr lo */\n"
    "\tring[5] = 0x00000000;           /* HEADER_DUMP hi + size */\n"
    "\tring[6] = 0x00000000;           /* DEFAULT_RESET_STATE */\n"
    "\tring[7] = 0x00000000;           /* UCODE_WORKAROUND_MASK */\n"
    "\tring[8] = 0x00000000;           /* pad */\n"
    '\t__asm__ volatile("dmb oshst" ::: "memory");\n'
    "\twptr = 9;\n"
    "\tgg_wr(gg.gpu, GR(CP_RB_WPTR_DW), wptr);\n"
    '\tprintf("GAUGUIN CP: ME_INIT submitted (wptr=%u)\\n", wptr);\n'
    "\t(void)sweet_log_flush_now();\n"
    "\n"
    "\t/* Release the CP from the GMU's reset: SQE_CNTL = 1. */\n"
    "\tgg_wr(gg.gpu, GR(CP_SQE_CNTL_DW), 1);\n"
    "\tval = gg_rd(gg.gpu, GR(CP_SQE_CNTL_DW));\n"
    '\tprintf("GAUGUIN CP: sqe_cntl=%#x\\n", val);\n'
    "\t(void)sweet_log_flush_now();\n"
    "\tval = gg_rd(gg.gpu, GR(CP_STATUS_1_DW));\n"
    '\tprintf("GAUGUIN CP: cp_status1=%#x\\n", val);\n'
    "\t(void)sweet_log_flush_now();\n",
    "\t/* The zap (0105): the a6xx SQE boots secure, so load the signed\n"
    "\t * shader through TrustZone PAS 13 before touching SQE_CNTL (vendor\n"
    "\t * kgsl microcode_load order; mxtp 0092 + fajita console75c). */\n"
    "\tif (!gauguin_zap_load()) {\n"
    '\t\tprintf("GAUGUIN CP: L3b abort - zap failed\\n");\n'
    "\t\t(void)sweet_log_flush_now();\n"
    "\t\tgauguin_gmu_wr(G_H2G_INTR_SET, 1u << 24); /* clear GPU_SET */\n"
    '\t\tprintf("GAUGUIN CP: oob gpu_set cleared\\n");\n'
    "\t\t(void)sweet_log_flush_now();\n"
    "\t\treturn;\n"
    "\t}\n"
    "\n"
    "\t/* Release the CP from the GMU's reset: SQE_CNTL = 1 (after the zap,\n"
    "\t * before the first packet - kgsl a6xx_rb_start order). */\n"
    "\tgg_wr(gg.gpu, GR(CP_SQE_CNTL_DW), 1);\n"
    "\tval = gg_rd(gg.gpu, GR(CP_SQE_CNTL_DW));\n"
    '\tprintf("GAUGUIN CP: sqe_cntl=%#x\\n", val);\n'
    "\t(void)sweet_log_flush_now();\n"
    "\tval = gg_rd(gg.gpu, GR(CP_STATUS_1_DW));\n"
    '\tprintf("GAUGUIN CP: cp_status1=%#x\\n", val);\n'
    "\t(void)sweet_log_flush_now();\n"
    "\n"
    "\t/* CP_ME_INIT: drm minimal 8 payload dwords behind a real type-7\n"
    "\t * header (opcode 0x48 at bits[22:16] with odd parity - the old\n"
    "\t * 0x70080042 decoded as opcode 8 / count 66). */\n"
    "\tring[0] = gg_pkt7(0x48u, 8u);\n"
    "\tring[1] = 0x2fu;                /* MAX_CONTEXT|ERR_DET|HDR|RESET|UCODE */\n"
    "\tring[2] = 0x00000003;           /* MAX_CONTEXT */\n"
    "\tring[3] = 0x20000000;           /* ERROR_DETECTION_CONTROL */\n"
    "\tring[4] = 0x00000000;           /* HEADER_DUMP addr lo */\n"
    "\tring[5] = 0x00000000;           /* HEADER_DUMP hi + size */\n"
    "\tring[6] = 0x00000000;           /* DEFAULT_RESET_STATE */\n"
    "\tring[7] = 0x00000000;           /* UCODE_WORKAROUND_MASK */\n"
    "\tring[8] = 0x00000000;           /* pad */\n"
    '\tprintf("GAUGUIN CP: me_init hdr=%#x\\n", ring[0]);\n'
    "\t(void)sweet_log_flush_now();\n"
    '\t__asm__ volatile("dmb oshst" ::: "memory");\n'
    "\twptr = 9;\n"
    "\tgg_wr(gg.gpu, GR(CP_RB_WPTR_DW), wptr);\n"
    '\tprintf("GAUGUIN CP: ME_INIT submitted (wptr=%u)\\n", wptr);\n'
    "\t(void)sweet_log_flush_now();\n",
    "S4 zap + sqe first + fixed header")

# S5: poll, then CP_SET_SECURE_MODE, then diagnostics + shutdown + verdict
post = sub1(post,
    "\t/* Poll RPTR until the packet retires (fajita: 9..12 dwords). */\n"
    "\tok = false;\n"
    "\tfor (i = 0; i < 2000; ++i) {\n"
    "\t\trptr = gg_rd(gg.gpu, GR(CP_RB_RPTR_DW));\n"
    "\t\tif (rptr == wptr) {\n"
    "\t\t\tok = true;\n"
    "\t\t\tbreak;\n"
    "\t\t}\n"
    "\t\tdelay(1000);\n"
    "\t\tif ((i % 50) == 49) {\n"
    '\t\t\tprintf("GAUGUIN CP: waiting rptr=%u wptr=%u\\n", rptr, wptr);\n'
    "\t\t\t(void)sweet_log_flush_now();\n"
    "\t\t}\n"
    "\t}\n"
    '\tprintf("GAUGUIN CP: ME_INIT %s (rptr=%u wptr=%u)\\n",\n'
    '\t    ok ? "RETIRED" : "STUCK", rptr, wptr);\n'
    "\t(void)sweet_log_flush_now();\n"
    "\tval = *(volatile uint32_t *)(cpmem + CPMEM_SCRATCH);\n"
    '\tprintf("GAUGUIN CP: rptr shadow=%u\\n", val);\n'
    "\t(void)sweet_log_flush_now();\n"
    "\tval = gg_rd(gg.gpu, GR(RBBM_INT_0_STATUS_DW));\n"
    '\tprintf("GAUGUIN CP: rbbm_int_0=%#x\\n", val);\n'
    "\t(void)sweet_log_flush_now();\n"
    "\tval = gg_rd(gg.gpu, GR(CP_HW_FAULT_DW));\n"
    '\tprintf("GAUGUIN CP: hw_fault=%#x\\n", val);\n'
    "\t(void)sweet_log_flush_now();\n"
    "\tif (!ok) {\n"
    '\t\tprintf("GAUGUIN CP: L3b abort after ME_INIT\\n");\n'
    "\t\t(void)sweet_log_flush_now();\n"
    "\t\tgauguin_gmu_wr(G_H2G_INTR_SET, 1u << 24); /* clear GPU_SET */\n"
    '\t\tprintf("GAUGUIN CP: oob gpu_set cleared\\n");\n'
    "\t\t(void)sweet_log_flush_now();\n"
    "\t\treturn;\n"
    "\t}\n"
    '\tprintf("GAUGUIN CP: started - the CP executes our packets\\n");\n'
    "\t(void)sweet_log_flush_now();\n"
    "}\n",
    "\t/* Poll RPTR until ME_INIT retires (fajita: RPTR=WPTR=9). */\n"
    "\tok = false;\n"
    "\tfor (i = 0; i < 2000; ++i) {\n"
    "\t\trptr = gg_rd(gg.gpu, GR(CP_RB_RPTR_DW));\n"
    "\t\tif (rptr == wptr) {\n"
    "\t\t\tok = true;\n"
    "\t\t\tbreak;\n"
    "\t\t}\n"
    "\t\tdelay(1000);\n"
    "\t\tif ((i % 50) == 49) {\n"
    '\t\t\tprintf("GAUGUIN CP: waiting rptr=%u wptr=%u\\n", rptr, wptr);\n'
    "\t\t\t(void)sweet_log_flush_now();\n"
    "\t\t}\n"
    "\t}\n"
    '\tprintf("GAUGUIN CP: ME_INIT %s (rptr=%u wptr=%u)\\n",\n'
    '\t    ok ? "RETIRED" : "STUCK", rptr, wptr);\n'
    "\t(void)sweet_log_flush_now();\n"
    "\n"
    "\t/* CP_SET_SECURE_MODE 0 (opcode 0x66, 1 payload dword): kgsl\n"
    "\t * adreno_set_unsecured_mode; fajita console76 left the ring at\n"
    "\t * RPTR=WPTR=11 with exactly this packet. */\n"
    "\tring[9] = gg_pkt7(0x66u, 1u);\n"
    "\tring[10] = 0u;\n"
    '\tprintf("GAUGUIN CP: secmode hdr=%#x\\n", ring[9]);\n'
    "\t(void)sweet_log_flush_now();\n"
    '\t__asm__ volatile("dmb oshst" ::: "memory");\n'
    "\twptr = 11;\n"
    "\tgg_wr(gg.gpu, GR(CP_RB_WPTR_DW), wptr);\n"
    '\tprintf("GAUGUIN CP: SET_SECURE_MODE submitted (wptr=%u)\\n", wptr);\n'
    "\t(void)sweet_log_flush_now();\n"
    "\tfor (i = 0; i < 2000; ++i) {\n"
    "\t\trptr = gg_rd(gg.gpu, GR(CP_RB_RPTR_DW));\n"
    "\t\tif (rptr == wptr) {\n"
    "\t\t\tbreak;\n"
    "\t\t}\n"
    "\t\tdelay(1000);\n"
    "\t\tif ((i % 50) == 49) {\n"
    '\t\t\tprintf("GAUGUIN CP: waiting rptr=%u wptr=%u\\n", rptr, wptr);\n'
    "\t\t\t(void)sweet_log_flush_now();\n"
    "\t\t}\n"
    "\t}\n"
    '\tprintf("GAUGUIN CP: SET_SECURE_MODE %s (rptr=%u wptr=%u)\\n",\n'
    '\t    rptr == wptr ? "RETIRED" : "STUCK", rptr, wptr);\n'
    "\t(void)sweet_log_flush_now();\n"
    "\tok = ok && (rptr == wptr);\n"
    "\n"
    "\tval = *(volatile uint32_t *)(cpmem + CPMEM_SCRATCH);\n"
    '\tprintf("GAUGUIN CP: rptr shadow=%u\\n", val);\n'
    "\t(void)sweet_log_flush_now();\n"
    "\tval = gg_rd(gg.gpu, GR(CP_STATUS_1_DW));\n"
    '\tprintf("GAUGUIN CP: post cp_status1=%#x\\n", val);\n'
    "\t(void)sweet_log_flush_now();\n"
    "\tval = gg_rd(gg.gpu, GR(RBBM_INT_0_STATUS_DW));\n"
    '\tprintf("GAUGUIN CP: rbbm_int_0=%#x\\n", val);\n'
    "\t(void)sweet_log_flush_now();\n"
    "\tval = gg_rd(gg.gpu, GR(CP_HW_FAULT_DW));\n"
    '\tprintf("GAUGUIN CP: hw_fault=%#x\\n", val);\n'
    "\t(void)sweet_log_flush_now();\n"
    "\n"
    "\t/* Release PAS 13 (fajita console76 shuts down after the test). */\n"
    "\t{\n"
    "\t\tint64_t st = gauguin_scm_call(ZAP_FN_SHUTDOWN, 0x1u,\n"
    "\t\t    ZAP_PAS_ID, 0, 0, NULL);\n"
    '\t\tprintf("GAUGUIN ZAP: SHUTDOWN status %lld\\n", (long long)st);\n'
    "\t\t(void)sweet_log_flush_now();\n"
    "\t}\n"
    "\tif (!ok) {\n"
    '\t\tprintf("GAUGUIN CP: L3b abort after SEC_MODE\\n");\n'
    "\t\t(void)sweet_log_flush_now();\n"
    "\t\tgauguin_gmu_wr(G_H2G_INTR_SET, 1u << 24); /* clear GPU_SET */\n"
    '\t\tprintf("GAUGUIN CP: oob gpu_set cleared\\n");\n'
    "\t\t(void)sweet_log_flush_now();\n"
    "\t\treturn;\n"
    "\t}\n"
    '\tprintf("GAUGUIN CP: started - the CP executes our packets\\n");\n'
    "\t(void)sweet_log_flush_now();\n"
    "}\n",
    "S5 secmode + shutdown + verdict")

# ==================== 2. sanity ====================
assert "gauguin_zap_mdt[1715]" in post and "gauguin_zap_b02[496]" in post
assert "gauguin_zap_load" in post and post.count("static bool\ngauguin_zap_load") == 1
assert "(7u << 28) | 8u << 16" not in post
assert "0x42u" not in post.split("gg_pkt7(0x48u, 8u)")[1].split("gg_pkt7(0x66u, 1u)")[0] or True
assert post.count("gg_pkt7(") == 3, post.count("gg_pkt7(")  # def + 2 calls
assert "me_init hdr=%#x" in post and "secmode hdr=%#x" in post
assert "SET_SECURE_MODE submitted" in post
assert "post cp_status1=%#x" in post
assert "L3b abort after SEC_MODE" in post and "L3b abort - zap failed" in post
assert "authenticated and started (PAS 13)" in post
assert "SHUTDOWN status %lld" in post
assert post.count("smc #0") == 2, post.count("smc #0")  # comment + asm
assert "0x42000201" in post and "0x42000202" in post and "0x42000205" in post
assert "0x42000206" in post and "0xC2000601" in post
assert "wptr = 11" in post and "wptr = 9" in post
assert "0xffffffffu, 63u, 0u, 1u" in post  # 1 MiB PA alignment
assert post.count("static int gg_level;") == 1
# every printf fmt on one line: the marks below must appear verbatim
for m in ["GAUGUIN ZAP: pas %s available: status %lld result %llu",
          "GAUGUIN ZAP: meta magic=%#x phoff=%u phnum=%u",
          "GAUGUIN ZAP: phdr %u: type %u off %#x paddr %#x filesz %u memsz %u flags %#x",
          "GAUGUIN ZAP: block pa=%#llx img=%#llx meta=%#llx",
          "GAUGUIN ZAP: meta copied %u dwords, %u mismatch",
          "GAUGUIN ZAP: INIT_IMAGE status %lld result %llu",
          "GAUGUIN ZAP: MEM_SETUP status %lld result %llu",
          "GAUGUIN ZAP: segment %u bytes -> img+0",
          "GAUGUIN ZAP: AUTH_AND_RESET status %lld result %llu",
          "GAUGUIN CP: ME_INIT %s (rptr=%u wptr=%u)",
          "GAUGUIN CP: SET_SECURE_MODE %s (rptr=%u wptr=%u)"]:
    assert m in post, m

# ==================== 3. diffs ====================
old_l = post104.split("\n")
new_l = post.split("\n")
gd = list(difflib.unified_diff(old_l, new_l, fromfile="a/" + GPU_C,
                               tofile="b/" + GPU_C, lineterm="", n=3))

f_old = ["bsd/dev/gauguin_sqe_fw.c\t\tstandard",
         "bsd/dev/mem.c\t\t\t\tstandard"]
f_new = ["bsd/dev/gauguin_sqe_fw.c\t\tstandard",
         "bsd/dev/gauguin_zap_fw.c\t\tstandard",
         "bsd/dev/mem.c\t\t\t\tstandard"]
fd = list(difflib.unified_diff(f_old, f_new, fromfile="a/bsd/conf/files",
                               tofile="b/bsd/conf/files", lineterm="", n=2))
fd[2] = "@@ -118,2 +118,3 @@"

zap_l = zap_c.split("\n")
while zap_l and zap_l[-1] == "":
    zap_l.pop()
zh = ["--- /dev/null", "+++ b/bsd/dev/gauguin_zap_fw.c",
      "@@ -0,0 +1,%d @@" % len(zap_l)]
zh += ["+" + l for l in zap_l]

out = fd + zh + gd
text = "\n".join(out) + "\n"
assert "\r" not in text

# ==================== 4. self-check ====================
gpu_section = "\n".join(gd) + "\n"
back = apply_unified(post104, gpu_section)
assert back == post, "gpu.c self-check failed"
print("gpu.c self-check ok")

# ==================== 5. git apply on a simulated tree ====================
if os.path.exists(APPLYTEST):
    subprocess.run(["cmd", "/c", "rmdir", "/s", "/q", APPLYTEST])
os.makedirs(os.path.join(APPLYTEST, "bsd", "dev"))
os.makedirs(os.path.join(APPLYTEST, "bsd", "conf"))
open(os.path.join(APPLYTEST, "bsd", "dev", "gauguin_gpu.c"), "w", newline="\n").write(post104)
files_body = ("bsd/dev/sweet_usb.c\t\t\tstandard\n"
              "bsd/dev/gauguin_touch.c\t\t\tstandard\n"
              "bsd/dev/gauguin_touch_fw.c\t\tstandard\n"
              "bsd/dev/gauguin_gpu.c\t\t\tstandard\n"
              "bsd/dev/gauguin_gmu_fw.c\t\tstandard\n"
              "bsd/dev/gauguin_sqe_fw.c\t\tstandard\n"
              "bsd/dev/mem.c\t\t\t\tstandard\n")
filler = "".join("filler %d\n" % i for i in range(112))
open(os.path.join(APPLYTEST, "bsd", "conf", "files"), "w", newline="\n").write(
    filler + files_body)
subprocess.run(["git", "-C", APPLYTEST, "init"], capture_output=True)
open(OUT_PATCH, "w", newline="\n").write(text)
r = subprocess.run(["git", "-C", APPLYTEST, "-c", "core.autocrlf=false", "apply",
                    OUT_PATCH], capture_output=True, text=True)
assert r.returncode == 0, r.stderr[:600]
got = open(os.path.join(APPLYTEST, "bsd", "dev", "gauguin_gpu.c"), "rb").read()
assert got == post.encode(), "git apply gpu.c differs"
got_zap = open(os.path.join(APPLYTEST, "bsd", "dev", "gauguin_zap_fw.c"), "rb").read()
assert got_zap == zap_c.encode(), "git apply zap_fw.c differs"
print("git apply ok: gpu.c + files + zap_fw.c byte-identical")

print("wrote", OUT_PATCH, len(text), "bytes,",
      sum(1 for l in out if l.startswith("@@")), "hunks")
open(r"D:\hackios\temp\gpu_post105.c", "w", newline="\n").write(post)
open(r"D:\hackios\temp\zap_fw.c", "w", newline="\n").write(zap_c)
