/*
 * gauguin (SM6350/lagoon) GPU L1 bring-up: light the Adreno 619 CX power
 * domain and dump diagnostics. No GMU firmware, no GX domain, no ringbuffer
 * - this only proves we can power and read the GPU block (facts from the
 * vendor tree: gpucc-lagoon.c, gcc-lagoon.c, a6xx_reg.h, kgsl-3d0 FDT).
 * GPU mmio 0x3d00000+0x40000, GPUCC 0x3d90000+0x9000, GMU 0x3d6a000+0x8000.
 * CX domain: gcc_gpu_cfg_ahb (GCC 0x45004 bit 0) + gpu_cc_ahb (0x1078 bit 0)
 * + gpu_cx_gdsc (0x106c: clear SW_COLLAPSE bit 0, poll PWR_ON bit 31).
 * Diagnostics: VBIF_VERSION (0xC000, CX-readable), RBBM_STATUS (0x840,
 * GX domain - value shows whether GX ever came up), GPUCC readback.
 * Boot-args: gauguingpu=0/1 (default on), gauguingpudelay=<seconds>.
 */
#if defined(ARM64_BOARD_CONFIG_SWEET)
#include <sys/param.h>
#include <sys/systm.h>
#include <kern/clock.h>
#include <kern/thread.h>
#include <kern/sched_prim.h>
#include <machine/machine_routines.h>
#include <pexpert/pexpert.h>
#include <vm/vm_kern.h>
#include <vm/pmap.h>
#include <arm/caches_internal.h>

extern vm_offset_t ml_io_map(vm_offset_t, vm_size_t);
extern kern_return_t thread_terminate(thread_t);
extern void sweet_log_flush_now(void);
extern ppnum_t pmap_find_phys(pmap_t pmap, addr64_t va);
extern void pmap_set_cache_attributes(ppnum_t, unsigned int);
extern const unsigned int gauguin_gmu_fw[5120];
extern const unsigned int gauguin_sqe_fw[8066];
extern const unsigned int gauguin_zap_mdt[1715];
extern const unsigned int gauguin_zap_b02[496];
static void gauguin_gpu_l2(void);
static void gauguin_gx_pdc_rsc(void);
static void gauguin_gx_rail_on(void);
static void gauguin_cp_mem_setup(void);
static void gauguin_cp_start(void);
void gauguin_gpu_init(void);

#define GPU_PA       0x03d00000u
#define GPU_SIZE     0x00040000u
#define GPUCC_PA     0x03d90000u
#define GMU_PA       0x03d6a000u
#define GCC_PA       0x00100000u

#define GCC_GPU_AHB  0x45004u   /* gcc_gpu_cfg_ahb_clk, bit 0 */

#define GPUCC_AHB    0x1078u    /* gpu_cc_ahb_clk, bit 0 (critical) */
#define GPUCC_CX_GDSC 0x106cu   /* gpu_cx_gdsc: bit0 SW_COLLAPSE, bit31 PWR_ON */
#define GPUCC_XO     0x1074u    /* gpu_cc_xo_clk region - read only */
#define GPUCC_GMU_CBCR 0x1098u  /* gpu_cc_cx_gmu_clk */
#define GPUCC_GFX3D_SRC 0x101cu /* gpu_cc_gx_gfx3d_clk_src RCG */
#define GPUCC_GFX3D_CBCR 0x1054u
#define GDSC_SW_COLLAPSE (1u << 0)
#define GDSC_PWR_ON     (1u << 31)

/* kgsl register indices are dwords. */
#define A6XX_RBBM_STATUS_DW  0x210u
#define A6XX_VBIF_VERSION_DW 0x3000u

static struct {
	vm_offset_t gpu, gpucc, gmu, gcc;
	bool started;
} gg;
static int gg_sleep_chan;
static int gg_level;

static uint32_t
gg_rd(vm_offset_t base, uint32_t off)
{
	uint32_t v = *(volatile uint32_t *)(base + off);
	__asm__ volatile("dmb oshld" ::: "memory");
	return v;
}

static void
gg_wr(vm_offset_t base, uint32_t off, uint32_t val)
{
	__asm__ volatile("dmb oshst" ::: "memory");
	*(volatile uint32_t *)(base + off) = val;
	__asm__ volatile("dsb osh" ::: "memory");
}

static void
gg_wait(unsigned ms)
{
	uint64_t deadline;
	clock_interval_to_deadline(ms, NSEC_PER_MSEC, &deadline);
	(void)assert_wait_deadline(&gg_sleep_chan, THREAD_UNINT, deadline);
	thread_block(THREAD_CONTINUE_NULL);
}

static bool
gg_poll_set(vm_offset_t base, uint32_t off, uint32_t mask)
{
	for (unsigned i = 0; i < 2000; ++i) {
		if (gg_rd(base, off) & mask) {
			return true;
		}
		delay(10); /* GDSC_POWER_UP_DELAY is ~20 us */
	}
	return false;
}

static void
gg_thread(void *arg, wait_result_t result)
{
	unsigned delay_s = 30;
	(void)arg;
	(void)result;
	(void)PE_parse_boot_argn("gauguingpudelay", &delay_s, sizeof(delay_s));
	printf("GAUGUIN GPU: starting in %u s\n", delay_s);
	gg_wait(delay_s * 1000u);
	printf("GAUGUIN GPU: mapping GCC/GPUCC\n");
	(void)sweet_log_flush_now();
	gg.gcc = ml_io_map(GCC_PA, 0x60000);
	gg.gpucc = ml_io_map(GPUCC_PA, 0x9000);
	if (!gg.gcc || !gg.gpucc) {
		printf("GAUGUIN GPU: MMIO mapping failed\n");
		goto out;
	}
	printf("GAUGUIN GPU: pre-state gcc_gpu_ahb=%#x gpucc_ahb=%#x cx_gdsc=%#x\n",
	    gg_rd(gg.gcc, GCC_GPU_AHB), gg_rd(gg.gpucc, GPUCC_AHB),
	    gg_rd(gg.gpucc, GPUCC_CX_GDSC));
	/* 1. GCC GPU CFG AHB - the GPUCC register aperture door. */
	gg_wr(gg.gcc, GCC_GPU_AHB, gg_rd(gg.gcc, GCC_GPU_AHB) | 1u);
	/* 2. GPUCC AHB - always-on for the CX domain. */
	gg_wr(gg.gpucc, GPUCC_AHB, gg_rd(gg.gpucc, GPUCC_AHB) | 1u);
	gg_wait(1);
	/* 3. CX GDSC - release SW collapse, wait for power. */
	uint32_t gdsc = gg_rd(gg.gpucc, GPUCC_CX_GDSC);
	printf("GAUGUIN GPU: cx_gdsc=%#x -> collapsing off\n", gdsc);
	gg_wr(gg.gpucc, GPUCC_CX_GDSC, gdsc & ~GDSC_SW_COLLAPSE);
	bool on = gg_poll_set(gg.gpucc, GPUCC_CX_GDSC, GDSC_PWR_ON);
	printf("GAUGUIN GPU: cx_gdsc=%#x powered=%d\n",
	    gg_rd(gg.gpucc, GPUCC_CX_GDSC), on);
	(void)sweet_log_flush_now();
	/* 4. Diagnostics from the now-powered CX domain. */
	printf("GAUGUIN GPU: gpucc xo=%#x gmu_cbcr=%#x gfx3d_src=%#x gfx3d_cbcr=%#x\n",
	    gg_rd(gg.gpucc, GPUCC_XO), gg_rd(gg.gpucc, GPUCC_GMU_CBCR),
	    gg_rd(gg.gpucc, GPUCC_GFX3D_SRC), gg_rd(gg.gpucc, GPUCC_GFX3D_CBCR));
	/*
	 * GPU-core (0x3d00000) and GMU (0x3d6a000) reads are NOT issued:
	 * sweetlog23 showed the first such read wedges its CPU core forever
	 * (never-retiring load), which also eats that core's timer queue -
	 * the touch thread's 45 s deadline never fired and boot slowed to a
	 * crawl. Those domains need the GMU firmware first; revisit in L2
	 * behind gauguingpu=2.
	 */
	if (!on) {
		printf("GAUGUIN GPU: CX GDSC did not power on (CX rail or parent?)\n");
		goto out;
	}
	if (gg_level >= 2) {
		gauguin_gpu_l2();
		goto out;
	}
	printf("GAUGUIN GPU: CX domain up; L1 complete "
	    "(GX/GMU needs firmware - a6xx_gmu.bin + HFI)\n");
out:
	(void)thread_terminate(current_thread());
}

/* ==================== L2: GMU firmware + HFI via the GPU SMMU (gauguingpu=2)
 * v15 died right after G_HFI_QTBL_ADDR: the firmware's first DMA (reading
 * the HFI table through its own SMMU stream) found no SMR route -> global
 * fault -> silent reset. This revision gives the GMU real memory first:
 * a 64 KiB write-combined block with LPAE page tables maps four GMU VAs
 * (dummy, HFI, dump, log), SMMU context bank 0 translates stream IDs 4/5
 * through them, and every register is read back before the CM3 leaves
 * reset - a mismatch aborts the boot and the phone stays alive.
 * Register facts: mxtp 0083b/0085 hardware logs; SM6350 DTB kgsl_smmu
 * (reg 0x3d40000 +0x10000, gpu-offset 0x48000 = SMMU+0x8000 = CB0,
 * gmu stream IDs 5 (kernel) and 4 (user)). GMU register offsets are kgsl
 * dword indices relative to the GPU base (0x3d00000); bytes = off*4-0x6a000.
 */
#define GMU_WORD(off)     (((off) * 4u) - 0x6a000u)
#define G_ITCM            0x1b400u
#define G_DTCM            0x1c400u
#define G_ICACHE_CFG      0x1f400u
#define G_DCACHE_CFG      0x1f401u
#define G_SYS_BUS_CFG     0x1f40fu
#define G_SYSRESET        0x1f800u
#define G_BOOT_CONFIG     0x1f801u
#define G_FW_INIT_RESULT  0x1f81cu
#define G_CM3_CFG         0x1f82du
#define G_PWR_COL_CTRL    0x1f8c0u
#define G_RPMH_CTRL       0x1f8e8u  /* GMU_RPMH_CTRL */
#define RPMH_CTRL_ENABLE  0xf11u    /* iface | LLC | DDR | MX | CX | GFX votes */
#define G_PWR_COL_HYST_IF 0x1f8c1u
#define G_PWR_COL_HYST_SP 0x1f8c2u
#define G_SPTPRAC_STATUS  0x1f8d0u
#define G_PWR_COL_CP_MSG  0x1f900u
#define G_PWR_COL_CP_RESP 0x1f901u
#define G_HFI_STATUS      0x1f980u
#define G_HFI_SFR_ADDR    0x1f982u
#define G_HFI_QTBL_INFO   0x1f984u
#define G_HFI_QTBL_ADDR   0x1f985u
#define G_HFI_CTRL_INIT   0x1f986u
#define G_INTR_CLR        0x1f991u
#define G_INTR_INFO       0x1f992u
#define G_H2G_INTR_SET    0x1f994u
#define G_GENERAL_7       0x1f9ccu
#define G_AHB_FENCE_0     0x23b11u  /* RANGE_0: [17:0] lower, [30:18] len */
#define G_AO_FENCE_CTRL   0x23b10u  /* 0 = allow CPU AHB writes */
#define G_FENCE_RANGE_1   0x23b12u
#define G_FENCE_STATUS    0x23b13u  /* WRITEDROPPED counters */
#define G_FENCE_STATUS_CLR 0x23b14u

/* GCC / GPUCC clocks needed by the GMU (gcc-lagoon.c, gpucc-lagoon.c;
 * unchanged from 0087, hardware-proven by the v15 boot). */
#define GCC_GPLL0_ENA     0x52010u  /* GPLL0 PLL enable, bit 0 */
#define GCC_GPLL0_BRANCH  0x52008u  /* gpll0 (bit7) + gpll0_div (bit8) */
#define GCC_GPLL0_DIVSRC  0x4514cu  /* gpll0_out_main_div src, div bits 1:0 */
#define GCC_MEMNOC_GFX    0x4500cu  /* gpu_memnoc_gfx_clk, bit 0 */
#define GCC_DDRSS_AXI     0x2d038u  /* ddrss_gpu_axi_clk, bit 0 */
#define GCC_GPU_SNOC_DVM  0x45014u  /* gcc_gpu_snoc_dvm_gfx_clk = the GPU SMMU's
                                   * mem_iface_clk (DTB clock-names iface/mem/mem_iface
                                   * = cfg_ahb/memnoc_gfx/snoc_dvm_gfx); without it the
                                   * first SMMU register read hangs the AHB bus */
#define GPUCC_CXO_CBCR    0x109cu   /* gpu_cc_cxo_clk, bit 0 */
#define GPUCC_GMU_SRC_CMD 0x1120u   /* gpu_cc_gmu_clk_src CMD, bit0 update */
#define GPUCC_GMU_SRC_CFG 0x1124u   /* CFG: src=6 (gpll0_out_main_div), div=2 (/1.5 -> 200 MHz) */

/*
 * GMU memory: one physically contiguous 64 KiB block whose pages are made
 * write-combining (pmap updates every alias and flushes the lines), so the
 * SMMU page-table walks and the GMU see exactly what the CPU writes. Four
 * 4 KiB LPAE tables, then the buffers at fixed GMU VAs. Kept under 4 GiB:
 * this XNU's pmap cache-attribute flush would truncate 32-bit ppnums
 * above 4 GiB (mxtp 0082 panic), and the GMU is happy with 32-bit VAs.
 */
#define GMEM_SIZE         0x10000u
#define GMEM_L1           0x0000u
#define GMEM_L2           0x1000u
#define GMEM_L3A          0x2000u   /* 0x60000000-0x601fffff: dummy + HFI */
#define GMEM_L3B          0x3000u   /* 0x60200000-0x603fffff: dump + log */
#define GMEM_DUMMY        0x4000u
#define GMEM_HFI          0x5000u
#define GMEM_HFI_SIZE     0x5000u   /* header page + 3 queue pages */
#define GMEM_DUMP         0xa000u
#define GMEM_DUMP_SIZE    0x4000u
#define GMEM_LOG          0xe000u
#define GMU_VA_DUMMY      0x60000000u
#define GMU_VA_HFI        0x60100000u
#define GMU_VA_DUMP       0x60200000u
#define GMU_VA_LOG        0x60300000u

/* GPU SMMU (qcom,smmu-v2, SM6350 DTB reg 0x3d40000+0x10000). */
#define SMMU_PA           0x03d40000u
#define SMMU_SIZE         0x10000u
#define SMMU_GR1          0x1000u
#define SMMU_CB(n)        (0x8000u + 0x1000u * (n))
#define SMMU_MICRO_MMU_CTRL 0x6000u
#define MICRO_MMU_HALT_REQ  (1u << 2)
#define MICRO_MMU_IDLE      (1u << 3)
#define SMMU_sCR0         0x000u
#define SMMU_IDR0         0x020u
#define SMMU_IDR1         0x024u
#define SMMU_IDR2         0x028u
#define SMMU_GFSR        0x048u
#define SMMU_GFSYNR0     0x050u
#define SMMU_GFSYNR1     0x054u
#define SMMU_TLBIALLNSNH 0x068u
#define SMMU_sTLBGSYNC   0x070u
#define SMMU_sTLBGSTATUS 0x074u
#define SMMU_SMR(n)      (0x800u + 4u * (n))    /* GR0 */
#define SMMU_S2CR(n)     (0xc00u + 4u * (n))    /* GR0 */
#define SMMU_CBAR(n)     (0x000u + 4u * (n))    /* GR1 space */
#define SMMU_CBA2R(n)    (0x800u + 4u * (n))    /* GR1 space */
#define SMMU_CB_SCTLR    0x000u
#define SMMU_CB_ACTLR    0x004u
#define SMMU_CB_TTBCR2   0x010u
#define SMMU_CB_TTBR0    0x020u   /* 64-bit */
#define SMMU_CB_TCR      0x030u
#define SMMU_CB_MAIR0    0x038u
#define SMMU_CB_MAIR1    0x03cu
#define SMMU_CB_FSR      0x058u
#define SMMU_CB_FAR      0x060u
#define SMMU_CB_FSYNR0   0x068u
#define SMMU_CB_TLBIALL  0x618u
#define SMMU_CB_TLBSYNC  0x7f0u
#define SMMU_CB_TLBSTATUS 0x7f4u

/*
 * Context bank 0 as the phone's arm-smmu.c programs a qcom,smmu-v2 stage-1
 * domain (4 KiB granule, non-coherent walks SH_OS NC/NC, IPS 36 bit, MAIR
 * NC/WBRWA/DEV). Every value below was read back on mxtp 0083b hardware.
 */
#define GG_CB             0
#define GG_ASID           1u
#define GG_SID_KERNEL     5u   /* gmu_kernel stream */
#define GG_SID_USER       4u   /* gmu_user stream */
#define CBA2R_RW64_64BIT  1u
#define CBAR_S1_BYPASS_S2 ((1u << 16) | (3u << 8) | (0xfu << 12))
#define TTBCR_GMU         ((2u << 12) | (1u << 23) | 32u)   /* 0x00802020 */
#define TTBCR2_GMU        (1u | (7u << 15) | (1u << 4))     /* 0x00038011 */
#define MAIR0_GMU         0x0004ff44u
#define ACTLR_QCOM        ((1u << 28) | (1u << 29) | (1u << 30))
#define SCTLR_GMU         ((1u << 0) | (1u << 1) | (1u << 2) | (1u << 5) | \
	                   (1u << 6) | (1u << 12) | (3u << 22))  /* 0x00c01067 */
#define S2CR_TRANS_CB0    ((3u << 8) | GG_CB)               /* 0x300 */
#define SMR_VALID         (1u << 31)

/* LPAE stage-1 descriptors (io-pgtable-arm.c). */
#define PTE_TABLE         3ull
#define PTE_PAGE_NC       (3ull | (1ull << 10) | (2ull << 8) | (1ull << 6) | (1ull << 11))

/* DT kgsl_smmu attach-impl-defs: <offset value> pairs (qsmmuv2_device_reset). */
static const uint32_t gg_smmu_impl_defs[][2] = {
	{ 0x6000, 0x2378 }, { 0x6060, 0x1055 }, { 0x678c, 0x08 }, { 0x6794, 0x28 },
	{ 0x6800, 0x06 }, { 0x6900, 0x3ff }, { 0x6924, 0x204 }, { 0x6928, 0x11000 },
	{ 0x6930, 0x800 }, { 0x6960, 0xffffffff }, { 0x6b64, 0x1a5551 }, { 0x6b68, 0x9a82a382 },
};

/* HFI queue table (kgsl_hfi); sizes in dwords. Queue q's data lives at
 * GMEM_HFI + (q+1)*0x1000, one page per queue, matching the iova in its
 * queue header; the 0087 code was off by one queue on the CPU side. */
#define HFI_QHDR_OFF      6u
#define HFI_QHDR_SIZE     12u
#define HFI_TBL_DWORDS    (HFI_QHDR_OFF + 3u * HFI_QHDR_SIZE)  /* 42 */
#define HFI_QDATA(off)    (((off) + 1u) * 0x400u)  /* data dword offset of queue #off */

static vm_offset_t gmem;      /* kernel VA of the WCOMB GMU memory block */
static uint64_t gmem_pa;
static vm_offset_t gg_smmu;   /* GPU SMMU GR0 base */
static uint32_t *hfi_w;
static unsigned hfi_seq = 1;

static uint32_t
gm_rd(uint32_t off)
{
	return gg_rd(gmem, off);
}

static void
gm_wr64(uint32_t off, uint64_t v)
{
	__asm__ volatile("dmb oshst" ::: "memory");
	*(volatile uint64_t *)(gmem + off) = v;
	__asm__ volatile("dsb osh" ::: "memory");
}

/* Map GMU VA va (4 KiB pages within 0x60000000-0x603fffff) to gmem + off. */
static void
gm_map(uint32_t va, uint32_t off, uint32_t size)
{
	for (uint32_t done = 0; done < size; done += 0x1000u) {
		uint32_t v = va + done;
		uint32_t l3 = ((v >> 21) & 0x1ff) == ((GMU_VA_DUMMY >> 21) & 0x1ff) ?
		    GMEM_L3A : GMEM_L3B;
		gm_wr64(l3 + 8u * ((v >> 12) & 0x1ff), (gmem_pa + off + done) | PTE_PAGE_NC);
	}
}

static bool
gauguin_gmem_alloc(void)
{
	vm_offset_t kva = 0;
	kern_return_t kr;
	ppnum_t pn;

	kr = kmem_alloc_contig(kernel_map, &kva, GMEM_SIZE, 0,
	    0xffffffffu, 0u, 0u, 1u /* VM_KERN_MEMORY_OSFMK */);
	if (kr != KERN_SUCCESS) {
		printf("GAUGUIN GPU: gmem alloc failed %d\n", kr);
		return false;
	}
	pn = pmap_find_phys(kernel_pmap, (addr64_t)kva);
	if (!pn) {
		printf("GAUGUIN GPU: gmem has no phys page\n");
		return false;
	}
	gmem_pa = ptoa_64(pn);
	for (unsigned i = 0; i < GMEM_SIZE / PAGE_SIZE; i++) {
		pmap_set_cache_attributes(pn + i, VM_WIMG_WCOMB);
	}
	flush_dcache64((addr64_t)gmem_pa, GMEM_SIZE, TRUE);
	gmem = kva;
	bzero((void *)gmem, GMEM_SIZE);
	/* 32-bit VA, 4 KiB granule: walks start at level 1 (VA[31:30]). */
	gm_wr64(GMEM_L1 + 8u * (GMU_VA_HFI >> 30), (gmem_pa + GMEM_L2) | PTE_TABLE);
	gm_wr64(GMEM_L2 + 8u * ((GMU_VA_DUMMY >> 21) & 0x1ff), (gmem_pa + GMEM_L3A) | PTE_TABLE);
	gm_wr64(GMEM_L2 + 8u * ((GMU_VA_DUMP >> 21) & 0x1ff), (gmem_pa + GMEM_L3B) | PTE_TABLE);
	gm_map(GMU_VA_DUMMY, GMEM_DUMMY, 0x1000u);
	gm_map(GMU_VA_HFI, GMEM_HFI, GMEM_HFI_SIZE);
	gm_map(GMU_VA_DUMP, GMEM_DUMP, GMEM_DUMP_SIZE);
	gm_map(GMU_VA_LOG, GMEM_LOG, 0x1000u);
	printf("GAUGUIN SMMU: GMEM pa=%#llx dummy=%#x hfi=%#x dump=%#x log=%#x\n",
	    gmem_pa, GMU_VA_DUMMY, GMU_VA_HFI, GMU_VA_DUMP, GMU_VA_LOG);
	(void)sweet_log_flush_now();
	return true;
}

static bool
gg_poll_eq(vm_offset_t base, uint32_t off, uint32_t mask, uint32_t val)
{
	for (unsigned i = 0; i < 10000; ++i) {
		if ((gg_rd(base, off) & mask) == val) {
			return true;
		}
		delay(10);
	}
	return false;
}

static bool
gg_tlb_sync(vm_offset_t base, uint32_t sync, uint32_t status)
{
	gg_wr(base, sync, 0);
	if (!gg_poll_eq(base, status, 1u, 0u)) {
		printf("GAUGUIN SMMU: TLB sync timed out\n");
		return false;
	}
	return true;
}

static vm_offset_t
gg_smmu_base(void)
{
	if (!gg_smmu) {
		gg_smmu = ml_io_map(SMMU_PA, SMMU_SIZE);
	}
	return gg_smmu;
}

/* Read-only probe: everything ABL left in the GPU SMMU, one shot. */
static void
gauguin_smmu_probe(void)
{
	vm_offset_t s = gg_smmu_base();
	vm_offset_t cb;

	if (!s) {
		printf("GAUGUIN SMMU: map failed\n");
		return;
	}
	cb = s + SMMU_CB(GG_CB);
	/* v23: one read per printf (back-to-back reads inside one printf's
	 * argument evaluation deadlocked the interconnect on sm6350). */
	printf("GAUGUIN SMMU: sCR0=%#x\n", gg_rd(s, SMMU_sCR0));
	(void)sweet_log_flush_now();
	printf("GAUGUIN SMMU: IDR0=%#x\n", gg_rd(s, SMMU_IDR0));
	(void)sweet_log_flush_now();
	printf("GAUGUIN SMMU: IDR1=%#x\n", gg_rd(s, SMMU_IDR1));
	printf("GAUGUIN SMMU: IDR2=%#x\n", gg_rd(s, SMMU_IDR2));
	printf("GAUGUIN SMMU: micro=%#x\n", gg_rd(s, SMMU_MICRO_MMU_CTRL));
	(void)sweet_log_flush_now();
	printf("GAUGUIN SMMU: gfsr=%#x\n", gg_rd(s, SMMU_GFSR));
	printf("GAUGUIN SMMU: gfsynr0=%#x\n", gg_rd(s, SMMU_GFSYNR0));
	printf("GAUGUIN SMMU: gfsynr1=%#x\n", gg_rd(s, SMMU_GFSYNR1));
	(void)sweet_log_flush_now();
	for (unsigned n = 0; n < 8; n += 4) {
		printf("GAUGUIN SMMU: SMR%u=%#x S2CR%u=%#x\n",
		    n, gg_rd(s, SMMU_SMR(n)), n, gg_rd(s, SMMU_S2CR(n)));
	}
	printf("GAUGUIN SMMU: cbar0=%#x\n", gg_rd(s, SMMU_GR1 + SMMU_CBAR(0)));
	(void)sweet_log_flush_now();
	printf("GAUGUIN SMMU: cba2r0=%#x\n", gg_rd(s, SMMU_GR1 + SMMU_CBA2R(0)));
	(void)sweet_log_flush_now();
	printf("GAUGUIN SMMU: cb0 sctlr=%#x\n", gg_rd(cb, SMMU_CB_SCTLR));
	(void)sweet_log_flush_now();
	printf("GAUGUIN SMMU: cb0 tcr2=%#x tcr=%#x\n",
	    gg_rd(cb, SMMU_CB_TTBCR2), gg_rd(cb, SMMU_CB_TCR));
	printf("GAUGUIN SMMU: cb0 mair0=%#x mair1=%#x\n",
	    gg_rd(cb, SMMU_CB_MAIR0), gg_rd(cb, SMMU_CB_MAIR1));
	(void)sweet_log_flush_now();
	printf("GAUGUIN SMMU: cb0 fsr=%#x\n", gg_rd(cb, SMMU_CB_FSR));
	(void)sweet_log_flush_now();
}

/* Global + CB0 fault registers; call after any failure. */
static void
gauguin_smmu_faults(void)
{
	vm_offset_t s = gg_smmu_base();
	vm_offset_t cb;

	if (!s) {
		return;
	}
	cb = s + SMMU_CB(GG_CB);
	printf("GAUGUIN SMMU: faults gfsr=%#x\n", gg_rd(s, SMMU_GFSR));
	printf("GAUGUIN SMMU: faults gfsynr0=%#x gfsynr1=%#x\n",
	    gg_rd(s, SMMU_GFSYNR0), gg_rd(s, SMMU_GFSYNR1));
	(void)sweet_log_flush_now();
	printf("GAUGUIN SMMU: faults cb0_fsr=%#x cb0_fsynr0=%#x\n",
	    gg_rd(cb, SMMU_CB_FSR), gg_rd(cb, SMMU_CB_FSYNR0));
}

/*
 * Program CB0, then exact mask-0 SMRs for the GMU's two streams (mxtp
 * 0084/0085: masked SMR entries read back 0 here, exact ones stick).
 * Everything is read back; any mismatch aborts and the GMU stays in reset.
 */
static bool
gauguin_smmu_setup(void)
{
	vm_offset_t s = gg_smmu_base();
	vm_offset_t cb;
	uint64_t ttbr_want = (gmem_pa + GMEM_L1) | ((uint64_t)GG_ASID << 48);
	uint64_t ttbr_got;
	bool ok = true;

	if (!s) {
		printf("GAUGUIN SMMU: map failed\n");
		return false;
	}
	cb = s + SMMU_CB(GG_CB);
	if (gg_rd(cb, SMMU_CB_SCTLR) != 0 ||
	    (gg_rd(s, SMMU_SMR(0)) & SMR_VALID) ||
	    (gg_rd(s, SMMU_SMR(1)) & SMR_VALID)) {
		printf("GAUGUIN SMMU: CB0/SMR0/SMR1 already in use; not touched\n");
		printf("GAUGUIN SMMU: in-use sctlr=%#x\n", gg_rd(cb, SMMU_CB_SCTLR));
		printf("GAUGUIN SMMU: in-use smr0=%#x smr1=%#x\n",
		    gg_rd(s, SMMU_SMR(0)), gg_rd(s, SMMU_SMR(1)));
		return false;
	}
	/* qsmmuv2_device_reset: impl-defs while the micro-MMU is halted. */
	gg_wr(s, SMMU_MICRO_MMU_CTRL, gg_rd(s, SMMU_MICRO_MMU_CTRL) | MICRO_MMU_HALT_REQ);
	if (!gg_poll_eq(s, SMMU_MICRO_MMU_CTRL, MICRO_MMU_IDLE, MICRO_MMU_IDLE)) {
		printf("GAUGUIN SMMU: micro-MMU did not halt (%#x)\n",
		    gg_rd(s, SMMU_MICRO_MMU_CTRL));
		return false;
	}
	for (unsigned i = 0; i < sizeof(gg_smmu_impl_defs) / sizeof(gg_smmu_impl_defs[0]); i++) {
		gg_wr(s, gg_smmu_impl_defs[i][0], gg_smmu_impl_defs[i][1]);
	}
	gg_wr(s, SMMU_MICRO_MMU_CTRL, gg_rd(s, SMMU_MICRO_MMU_CTRL) & ~MICRO_MMU_HALT_REQ);

	/* arm_smmu_write_context_bank: TCR2/TCR before the TTBR. */
	gg_wr(cb, SMMU_CB_SCTLR, 0);
	gg_wr(s, SMMU_GR1 + SMMU_CBA2R(GG_CB), CBA2R_RW64_64BIT);
	gg_wr(s, SMMU_GR1 + SMMU_CBAR(GG_CB), CBAR_S1_BYPASS_S2);
	gg_wr(cb, SMMU_CB_TTBCR2, TTBCR2_GMU);
	gg_wr(cb, SMMU_CB_TCR, TTBCR_GMU);
	__asm__ volatile("dmb oshst" ::: "memory");
	*(volatile uint64_t *)(cb + SMMU_CB_TTBR0) = ttbr_want;
	__asm__ volatile("dsb osh" ::: "memory");
	gg_wr(cb, SMMU_CB_MAIR0, MAIR0_GMU);
	gg_wr(cb, SMMU_CB_MAIR1, 0);
	gg_wr(cb, SMMU_CB_ACTLR, ACTLR_QCOM);
	gg_wr(cb, SMMU_CB_FSR, gg_rd(cb, SMMU_CB_FSR)); /* write 1 to clear */
	gg_wr(cb, SMMU_CB_TLBIALL, 0);
	if (!gg_tlb_sync(cb, SMMU_CB_TLBSYNC, SMMU_CB_TLBSTATUS)) {
		return false;
	}
	gg_wr(cb, SMMU_CB_SCTLR, SCTLR_GMU);

	/* arm_smmu_write_sme: the S2CRs before the SMRs become valid. */
	gg_wr(s, SMMU_S2CR(0), S2CR_TRANS_CB0);
	gg_wr(s, SMMU_S2CR(1), S2CR_TRANS_CB0);
	gg_wr(s, SMMU_SMR(0), SMR_VALID | GG_SID_KERNEL);
	gg_wr(s, SMMU_SMR(1), SMR_VALID | GG_SID_USER);
	gg_wr(s, SMMU_TLBIALLNSNH, 0);
	if (!gg_tlb_sync(s, SMMU_sTLBGSYNC, SMMU_sTLBGSTATUS)) {
		return false;
	}

	/* Read everything back; one mismatch aborts the whole boot. */
#define GG_CHK(what, got, want) do { \
	uint32_t g_ = (uint32_t)(got); \
	if (g_ != (uint32_t)(want)) { \
		printf("GAUGUIN SMMU: verify FAIL %s got=%#x want=%#x\n", \
		    what, g_, (uint32_t)(want)); \
		ok = false; \
	} \
} while (0)
	GG_CHK("SMR0", gg_rd(s, SMMU_SMR(0)), SMR_VALID | GG_SID_KERNEL);
	GG_CHK("SMR1", gg_rd(s, SMMU_SMR(1)), SMR_VALID | GG_SID_USER);
	GG_CHK("S2CR0", gg_rd(s, SMMU_S2CR(0)), S2CR_TRANS_CB0);
	GG_CHK("S2CR1", gg_rd(s, SMMU_S2CR(1)), S2CR_TRANS_CB0);
	GG_CHK("CBAR0", gg_rd(s, SMMU_GR1 + SMMU_CBAR(GG_CB)), CBAR_S1_BYPASS_S2);
	GG_CHK("CBA2R0", gg_rd(s, SMMU_GR1 + SMMU_CBA2R(GG_CB)), CBA2R_RW64_64BIT);
	GG_CHK("SCTLR", gg_rd(cb, SMMU_CB_SCTLR), SCTLR_GMU);
	GG_CHK("TCR", gg_rd(cb, SMMU_CB_TCR), TTBCR_GMU);
	GG_CHK("TCR2", gg_rd(cb, SMMU_CB_TTBCR2), TTBCR2_GMU);
	GG_CHK("MAIR0", gg_rd(cb, SMMU_CB_MAIR0), MAIR0_GMU);
	GG_CHK("MAIR1", gg_rd(cb, SMMU_CB_MAIR1), 0);
	GG_CHK("ACTLR", gg_rd(cb, SMMU_CB_ACTLR), ACTLR_QCOM);
#undef GG_CHK
	ttbr_got = *(volatile uint64_t *)(cb + SMMU_CB_TTBR0);
	if (ttbr_got != ttbr_want) {
		printf("GAUGUIN SMMU: verify FAIL TTBR0 got=%#llx want=%#llx\n",
		    ttbr_got, ttbr_want);
		ok = false;
	}
	if (!ok) {
		gauguin_smmu_faults();
		return false;
	}
	printf("GAUGUIN SMMU: verified (CB0 LPAE ttbr0=%#llx SMR0=sid%u SMR1=sid%u)\n",
	    ttbr_got, GG_SID_KERNEL, GG_SID_USER);
	(void)sweet_log_flush_now();
	return true;
}

static void
gauguin_gmu_wr(uint32_t off, uint32_t val)
{
	gg_wr(gg.gmu, GMU_WORD(off), val);
}

static uint32_t
gauguin_gmu_rd(uint32_t off)
{
	return gg_rd(gg.gmu, GMU_WORD(off));
}

static void
hfi_build_table(void)
{
	static const unsigned qtype[3] = { 0, 4, 5 }; /* cmd, response, debug */
	uint32_t *t = hfi_w;

	t[0] = 0;              /* version */
	t[1] = HFI_TBL_DWORDS; /* table size in dwords */
	t[2] = HFI_QHDR_OFF;   /* first queue header offset */
	t[3] = HFI_QHDR_SIZE;  /* queue header size */
	t[4] = 3;              /* num queues */
	t[5] = 3;              /* active queues */
	for (unsigned q = 0; q < 3; ++q) {
		uint32_t *h = t + HFI_QHDR_OFF + q * HFI_QHDR_SIZE;
		h[0] = 1;                          /* status: initialized */
		h[1] = GMU_VA_HFI + 0x1000u + q * 0x1000u; /* iova (via SMMU) */
		h[2] = (10u << 8) | qtype[q];      /* type */
		h[3] = 0x400u;                     /* size (dwords) */
		h[4] = 0;                          /* msg_size */
		h[5] = 0;                          /* dropped */
		h[6] = 1;                          /* rx_watermark */
		h[7] = 1;                          /* tx_watermark */
		h[8] = 1;                          /* rx_request */
		h[9] = 0;                          /* tx_request */
		h[10] = 0;                         /* read_index */
		h[11] = 0;                         /* write_index */
	}
}

static int
hfi_send(unsigned id, const uint32_t *fields, unsigned nfields)
{
	uint32_t *qh = hfi_w + HFI_QHDR_OFF;
	uint32_t *qd = hfi_w + HFI_QDATA(0);
	uint32_t hdr, w, idx, total = 1u + nfields;

	if (total > 0x400u) {
		return -1;
	}
	hdr = ((hfi_seq++ % 0xfffu) << 20) | (0u << 16) | (total << 8) | id;
	w = qh[11];
	idx = w;
	qd[idx] = hdr;
	for (unsigned i = 0; i < nfields; ++i) {
		idx = (idx + 1u) % 0x400u;
		qd[idx] = fields[i];
	}
	__asm__ volatile("dmb oshst" ::: "memory");
	qh[11] = (idx + 1u) % 0x400u;
	gauguin_gmu_wr(G_H2G_INTR_SET, 1);
	printf("GAUGUIN GPU: HFI send id=%u hdr=%#x\n", id, hdr);
	(void)sweet_log_flush_now();
	return 0;
}

/* Returns 0 on matching ack, 1 on error/timeout (details printed). */
static int
hfi_wait_ack(unsigned id, unsigned seq, uint32_t *payload, unsigned npay)
{
	uint32_t buf[19];
	uint32_t *rh = hfi_w + HFI_QHDR_OFF + 1u * HFI_QHDR_SIZE;
	uint32_t *rd = hfi_w + HFI_QDATA(1);

	for (unsigned tries = 0; tries < 5000; ++tries) {
		uint32_t info = gauguin_gmu_rd(G_INTR_INFO);
		if (info & 1u) {
			gauguin_gmu_wr(G_INTR_CLR, info);
			if (rh[10] != rh[11]) { /* read_index != write_index */
				uint32_t r = rh[10];
				uint32_t hdr = rd[r];
				unsigned n = (hdr >> 8) & 0xffu;
				for (unsigned i = 0; i < 19u; ++i) {
					buf[i] = rd[(r + i) % 0x400u];
				}
				rh[10] = (r + (n ? n : 1u)) % 0x400u;
				unsigned rseq = (buf[1] >> 20) & 0xfffu;
				printf("GAUGUIN GPU: HFI resp hdr=%#x ret=%#x err=%u "
				    "p0=%#x p1=%#x\n",
				    buf[0], buf[1], buf[2], buf[3], buf[4]);
				if ((buf[0] >> 8 & 0xffu) == 100) { /* F2H error */
					continue;
				}
				if (rseq == seq) {
					if (buf[2] == 0) {
						if (payload && npay) {
							for (unsigned i = 0; i < npay; ++i) {
								payload[i] = buf[3 + i];
							}
						}
						return 0;
					}
					printf("GAUGUIN GPU: HFI msg %u seq %u error %u\n",
					    id, seq, buf[2]);
					return 1;
				}
			}
		}
		gg_wait(1);
	}
	printf("GAUGUIN GPU: HFI msg %u seq %u TIMEOUT (info=%#x hfi_st=%#x)\n",
	    id, seq, gauguin_gmu_rd(G_INTR_INFO), gauguin_gmu_rd(G_HFI_STATUS));
	return 1;
}

static void
gauguin_gpu_l2(void)
{
	uint32_t dv, val;
	bool ok;

	printf("GAUGUIN GPU: L2 start\n");
	(void)sweet_log_flush_now();
	gg.gmu = ml_io_map(GMU_PA, 0x31000);
	if (!gg.gmu) {
		printf("GAUGUIN GPU: GMU map failed\n");
		return;
	}
	if (!gauguin_gmem_alloc()) {
		return;
	}
	hfi_w = (uint32_t *)(gmem + GMEM_HFI);
	hfi_build_table();
	/*
	 * The probe reads GR0 immediately, so the SMMU mem_iface clock must be
	 * on FIRST. 0090 enabled it only later in the GMU clock block - after
	 * the probe - which is why v18 still died between "GMEM pa=" and the
	 * sCR0 banner (sweetlog35, total 126699).
	 */
	gg_wr(gg.gcc, GCC_GPU_SNOC_DVM, gg_rd(gg.gcc, GCC_GPU_SNOC_DVM) | 1u);
	/*
	 * v19 proved snoc_dvm alone is not enough: the SMMU still hung. The
	 * vendor arm-smmu enables ALL of iface/mem/mem_iface before the first
	 * register read; mem (gcc_gpu_memnoc_gfx 0x4500c) was still off at
	 * probe time (it only got enabled later, in the GMU clock block).
	 */
	gg_wr(gg.gcc, GCC_MEMNOC_GFX, gg_rd(gg.gcc, GCC_MEMNOC_GFX) | 1u);
	printf("GAUGUIN GPU: smmu_clk snoc_dvm=%#x memnoc=%#x\n",
	    gg_rd(gg.gcc, GCC_GPU_SNOC_DVM), gg_rd(gg.gcc, GCC_MEMNOC_GFX));
	(void)sweet_log_flush_now();
	{
		/*
		 * v20 died on the very first GR0 read with all clocks on.
		 * Scan the address space to find the fence; each probe is
		 * bracketed so the last flushed line marks the dead zone.
		 */
		static const struct { vm_offset_t pa; const char *what; } probe[] = {
			{ 0x03d6a014u, "gmu gsa_cfg (in win2)" },
			{ 0x03d46000u, "smmu impl_def1 +0x6000" },
			{ 0x03d48000u, "smmu cb0 +0x8000" },
			{ 0x03d40000u, "smmu gr0 +0x0" },
		};
		for (unsigned i = 0; i < sizeof(probe) / sizeof(probe[0]); ++i) {
			vm_offset_t m = ml_io_map(probe[i].pa, 0x1000);
			printf("GAUGUIN SCAN: %s @%#llx reading...\n",
			    probe[i].what, (unsigned long long)probe[i].pa);
			(void)sweet_log_flush_now();
			if (!m) {
				printf("GAUGUIN SCAN: %s map failed\n", probe[i].what);
				continue;
			}
			uint32_t v = gg_rd(m, 0);
			printf("GAUGUIN SCAN: %s = %#x\n", probe[i].what, v);
			(void)sweet_log_flush_now();
		}
	}
	printf("GAUGUIN SMMU: reading GR0...\n");
	(void)sweet_log_flush_now();
	{
		/* v22: all four coarse probes read fine (gr0=0x200626) yet the
		 * bundled probe printf died. Read every register one at a time. */
		vm_offset_t s = ml_io_map(SMMU_PA, SMMU_SIZE);
		static const struct { uint32_t off; const char *what; } rp[] = {
			{ 0x000u, "sCR0" }, { 0x020u, "IDR0" }, { 0x024u, "IDR1" },
			{ 0x028u, "IDR2" }, { 0x048u, "GFSR" }, { 0x050u, "GFSYNR0" },
			{ 0x054u, "GFSYNR1" }, { 0x800u, "SMR0" }, { 0xc00u, "S2CR0" },
			{ 0x6000u, "MICRO_MMU" }, { 0x1000u, "GR1+0" }, { 0x1800u, "GR1+CBAR0" },
			{ 0x8000u, "CB0+SCTLR" }, { 0x8020u, "CB0+TTBR0" }, { 0x8030u, "CB0+TCR" },
			{ 0x8058u, "CB0+FSR" },
		};
		for (unsigned i = 0; i < sizeof(rp) / sizeof(rp[0]); ++i) {
			printf("GAUGUIN RP: %s @%#x reading...\n", rp[i].what, rp[i].off);
			(void)sweet_log_flush_now();
			uint32_t v = gg_rd(s, rp[i].off);
			printf("GAUGUIN RP: %s = %#x\n", rp[i].what, v);
			(void)sweet_log_flush_now();
		}
		printf("GAUGUIN RP: all individual reads done\n");
		(void)sweet_log_flush_now();
	}
	gauguin_smmu_probe();

	/* GMU clocks: GPLL0 chain, memnoc, ddrss AXI. */
	gg_wr(gg.gcc, GCC_GPLL0_ENA, gg_rd(gg.gcc, GCC_GPLL0_ENA) | 1u);
	gg_wr(gg.gcc, GCC_GPLL0_BRANCH, gg_rd(gg.gcc, GCC_GPLL0_BRANCH) | (1u << 7 | 1u << 8));
	val = gg_rd(gg.gcc, GCC_GPLL0_DIVSRC);
	printf("GAUGUIN GPU: gpll0_div_src=%#x -> /1\n", val);
	gg_wr(gg.gcc, GCC_GPLL0_DIVSRC, val & ~3u);
	gg_wr(gg.gcc, GCC_MEMNOC_GFX, gg_rd(gg.gcc, GCC_MEMNOC_GFX) | 1u);
	gg_wr(gg.gcc, GCC_DDRSS_AXI, gg_rd(gg.gcc, GCC_DDRSS_AXI) | 1u);
	/* GPU SMMU mem_iface clock - must be on before any SMMU access. */
	gg_wr(gg.gcc, GCC_GPU_SNOC_DVM, gg_rd(gg.gcc, GCC_GPU_SNOC_DVM) | 1u);
	/* GPUCC: cxo, gmu RCG 200 MHz, cx_gmu gate. */
	gg_wr(gg.gpucc, GPUCC_CXO_CBCR, gg_rd(gg.gpucc, GPUCC_CXO_CBCR) | 1u);
	gg_wr(gg.gpucc, GPUCC_GMU_SRC_CFG, 0x602u);
	gg_wr(gg.gpucc, GPUCC_GMU_SRC_CMD, 1u);
	for (unsigned i = 0; i < 1000 && (gg_rd(gg.gpucc, GPUCC_GMU_SRC_CMD) & 1u); ++i) {
		delay(10);
	}
	gg_wr(gg.gpucc, GPUCC_GMU_CBCR, gg_rd(gg.gpucc, GPUCC_GMU_CBCR) | 1u);
	printf("GAUGUIN GPU: clocks cxo=%#x src_cmd=%#x src_cfg=%#x gmu_cbcr=%#x "
	    "memnoc=%#x axi=%#x snoc_dvm=%#x\n",
	    gg_rd(gg.gpucc, GPUCC_CXO_CBCR), gg_rd(gg.gpucc, GPUCC_GMU_SRC_CMD),
	    gg_rd(gg.gpucc, GPUCC_GMU_SRC_CFG), gg_rd(gg.gpucc, GPUCC_GMU_CBCR),
	    gg_rd(gg.gcc, GCC_MEMNOC_GFX), gg_rd(gg.gcc, GCC_DDRSS_AXI),
	    gg_rd(gg.gcc, GCC_GPU_SNOC_DVM));
	(void)sweet_log_flush_now();

	/* Pre-boot state (CX domain is up; GMU reads are safe now). */
	printf("GAUGUIN GPU: pre dtcm_ff8=%#x sysreset=%#x fw_init=%#x\n",
	    gauguin_gmu_rd(G_DTCM + 0xff8u), gauguin_gmu_rd(G_SYSRESET),
	    gauguin_gmu_rd(G_FW_INIT_RESULT));
	(void)sweet_log_flush_now();

	/* Firmware bulk load into ITCM (legacy path). */
	for (unsigned i = 0; i < 5120u; ++i) {
		gauguin_gmu_wr(G_ITCM + i, gauguin_gmu_fw[i]);
	}
	printf("GAUGUIN GPU: fw loaded itcm0=%#x itcm_last=%#x dtcm_ff8=%#x\n",
	    gauguin_gmu_rd(G_ITCM), gauguin_gmu_rd(G_ITCM + 5119u),
	    gauguin_gmu_rd(G_DTCM + 0xff8u));
	(void)sweet_log_flush_now();

	/*
	 * Give the GMU its memory BEFORE the CM3 leaves reset: the firmware
	 * DMAs the HFI table through SMMU stream 4/5 right after boot (v15:
	 * that access with no SMR route reset the phone silently).
	 */
	if (!gauguin_smmu_setup()) {
		printf("GAUGUIN GPU: L2 abort: SMMU setup/verify failed "
		    "(GMU not booted)\n");
		return;
	}

	/*
	 * GX rail prerequisites while the CM3 is still in reset: load the
	 * PDC/RSC sequencers (v18: the OOB vote timed out without them).
	 */
	gauguin_gx_pdc_rsc();

	/* Boot sequence (a6xx_gmu_fw_start); HFI table and buffers by GMU VA. */
	gauguin_gmu_wr(G_GENERAL_7, 1);
	gauguin_gmu_wr(G_FW_INIT_RESULT, 0);
	gauguin_gmu_wr(G_BOOT_CONFIG, 0x02u);
	gauguin_gmu_wr(G_HFI_QTBL_ADDR, GMU_VA_HFI);
	gauguin_gmu_wr(G_HFI_QTBL_INFO, 1);
	gauguin_gmu_wr(G_AHB_FENCE_0, (1u << 31) | (0xau << 18) | 0xa0u);
	gauguin_gmu_wr(G_CM3_CFG, 0x4052u);
	gauguin_gmu_wr(G_HFI_SFR_ADDR, GMU_VA_DUMMY);
	gauguin_gmu_wr(G_PWR_COL_CP_MSG, GMU_VA_LOG | 3u);
	/* a6xx_gmu_power_config (PDC/RSC loaded by gauguin_gx_pdc_rsc). */
	gauguin_gmu_wr(G_SYS_BUS_CFG, 1);
	gauguin_gmu_wr(G_ICACHE_CFG, 1);
	gauguin_gmu_wr(G_DCACHE_CFG, 1);
	gauguin_gmu_wr(G_PWR_COL_HYST_IF, 0xa1680u);
	gauguin_gmu_wr(G_PWR_COL_HYST_SP, 0xa1680u);
	gauguin_gmu_wr(G_PWR_COL_CTRL, 0x9c40400u | 0x7u);
	/* Enable the GMU RPMh client - the GX vote gate (v18 miss). */
	val = gauguin_gmu_rd(G_RPMH_CTRL);
	printf("GAUGUIN GPU: rpmh ctrl %#x -> %#x\n", val,
	    val | RPMH_CTRL_ENABLE);
	(void)sweet_log_flush_now();
	gauguin_gmu_wr(G_RPMH_CTRL, val | RPMH_CTRL_ENABLE);
	/* Start CM3. */
	dv = gauguin_gmu_rd(G_DTCM + 0xff8u);
	gauguin_gmu_wr(G_SYSRESET, 1);
	gauguin_gmu_wr(G_PWR_COL_CP_RESP, 0);
	gauguin_gmu_wr(G_SYSRESET, 0);
	printf("GAUGUIN GPU: CM3 released (dtcm_ff8=%#x), polling fw_init\n", dv);
	(void)sweet_log_flush_now();
	ok = false;
	for (unsigned i = 0; i < 3000; ++i) {
		val = gauguin_gmu_rd(G_FW_INIT_RESULT);
		if (i % 500u == 499u) {
			printf("GAUGUIN GPU: fw poll %u val=%#x\n", i, val);
			(void)sweet_log_flush_now();
		}
		if (val == 0xbabefaceu || (val & 0x1ffu) == 0x100u) {
			ok = true;
			break;
		}
		gg_wait(1);
	}
	printf("GAUGUIN GPU: fw_init=%#x booted=%d\n", val, ok);
	(void)sweet_log_flush_now();
	if (!ok) {
		printf("GAUGUIN GPU: L2 abort after CM3 boot\n");
		gauguin_smmu_faults();
		return;
	}

	/* HFI: clear stale interrupts, start the interface. */
	val = gauguin_gmu_rd(G_INTR_INFO);
	if (val) {
		gauguin_gmu_wr(G_INTR_CLR, val);
	}
	gauguin_gmu_wr(G_HFI_CTRL_INIT, 1);
	ok = false;
	for (unsigned i = 0; i < 200; ++i) {
		if (gauguin_gmu_rd(G_HFI_STATUS) & 1u) {
			ok = true;
			break;
		}
		gg_wait(1);
	}
	printf("GAUGUIN GPU: hfi started=%d status=%#x\n", ok,
	    gauguin_gmu_rd(G_HFI_STATUS));
	(void)sweet_log_flush_now();
	if (!ok) {
		gauguin_smmu_faults();
		return;
	}

	/* MSG_INIT (id 0): seg 0, debug buffer (GMU VA), cold boot. */
	{
		uint32_t f[4];
		f[0] = 0;
		f[1] = GMU_VA_DUMP;
		f[2] = GMEM_DUMP_SIZE;
		f[3] = 1; /* boot_state cold */
		if (hfi_send(0, f, 4) == 0) {
			hfi_wait_ack(0, (hfi_seq - 1) % 0xfffu, NULL, 0);
		}
	}
	/* FW_VERSION (id 1). */
	{
		uint32_t f[1];
		uint32_t p[4];
		f[0] = (1u << 28) | (1u << 19) | (1u << 17);
		if (hfi_send(1, f, 1) == 0) {
			hfi_wait_ack(1, (hfi_seq - 1) % 0xfffu, p, 1);
		}
	}
	/* PERF_TABLE v1 (id 4): one GPU level, one GMU level. */
	{
		uint32_t f[42];
		for (unsigned i = 0; i < 42u; ++i) {
			f[i] = 0;
		}
		f[0] = 1;         /* num_gpu_levels */
		f[1] = 1;         /* num_gmu_levels */
		f[3] = 253000;    /* gx freq kHz (min OPP) */
		f[35] = 200000;   /* cx freq kHz */
		if (hfi_send(4, f, 42) == 0) {
			hfi_wait_ack(4, (hfi_seq - 1) % 0xfffu, NULL, 0);
		}
	}
	/* BW_TABLE (id 3): 159 zero fields - no bus votes. */
	{
		uint32_t f[159];
		for (unsigned i = 0; i < 159u; ++i) {
			f[i] = 0;
		}
		if (hfi_send(3, f, 159) == 0) {
			hfi_wait_ack(3, (hfi_seq - 1) % 0xfffu, NULL, 0);
		}
	}
	/* TEST (id 5). */
	{
		if (hfi_send(5, NULL, 0) == 0) {
			hfi_wait_ack(5, (hfi_seq - 1) % 0xfffu, NULL, 0);
		}
	}

	/* Final diagnostics: GMU state, queue indices, log, SMMU faults. */
	printf("GAUGUIN GPU: post fw_init=%#x hfi_st=%#x intr=%#x sptprac=%#x\n",
	    gauguin_gmu_rd(G_FW_INIT_RESULT), gauguin_gmu_rd(G_HFI_STATUS),
	    gauguin_gmu_rd(G_INTR_INFO), gauguin_gmu_rd(G_SPTPRAC_STATUS));
	printf("GAUGUIN GPU: cmd hdr rw=%u/%u resp rw=%u/%u\n",
	    hfi_w[HFI_QHDR_OFF + 10], hfi_w[HFI_QHDR_OFF + 11],
	    hfi_w[HFI_QHDR_OFF + HFI_QHDR_SIZE + 10],
	    hfi_w[HFI_QHDR_OFF + HFI_QHDR_SIZE + 11]);
	printf("GAUGUIN GPU: log[0..3]=%#x %#x %#x %#x log[12..15]=%#x %#x %#x %#x\n",
	    gm_rd(GMEM_LOG), gm_rd(GMEM_LOG + 4u), gm_rd(GMEM_LOG + 8u),
	    gm_rd(GMEM_LOG + 12u), gm_rd(GMEM_LOG + 48u), gm_rd(GMEM_LOG + 52u),
	    gm_rd(GMEM_LOG + 56u), gm_rd(GMEM_LOG + 60u));
	gauguin_smmu_faults();
	printf("GAUGUIN GPU: L2 done\n");
	(void)sweet_log_flush_now();
	gauguin_gx_rail_on();
}


/* ==================== L3: GX rail on (gauguingpu=2, after L2) ============
 * Legacy a615-family path (vendor a6xx_gmu_gfx_rail_on + drm arc votes):
 * ARC votes come from cmd-db (0x80860000, "gfx.lvl"/"mx.lvl" aux tables).
 */
#define CMDDB_PA        0x80860000u
#define CMDDB_SIZE      0x20000u
#define G_BOOT_SLUMBER  0x1cbf8u
#define G_GX_VOTE_IDX   0x1cbf9u
#define G_MX_VOTE_IDX   0x1cbfau
#define G_SPTPRAC       0x1f8d0u
#define GX_GDSC_POWER_OFF (1u << 6)
#define GX_CLK_OFF        (1u << 7)

static vm_offset_t cmddb;

static bool
gauguin_cmdbdb_aux(const char *id, uint16_t *out, unsigned max, unsigned *outn,
    uint32_t *out_off)
{
	uint16_t hdr_words[8 * 8];
	volatile uint8_t *base = (volatile uint8_t *)cmddb;
	unsigned i, j, k;

	if (!cmddb) {
		return false;
	}
	if (base[4] != 0xdb || base[5] != 0x30 || base[6] != 0x03 || base[7] != 0x0c) {
		printf("GAUGUIN GX: cmd-db magic mismatch %02x %02x %02x %02x\n",
		    base[4], base[5], base[6], base[7]);
		return false;
	}
	/*
	 * cmd-db is Device memory (ml_io_map): memcpy/memcmp can become vector
	 * or wide unaligned loads - v17 panicked at +0x48, a 16-byte SIMD load
	 * inside the 128-byte header copy (ESR ISS 0x21 alignment fault).
	 * Byte-wise scalar volatile reads only.
	 */
	for (i = 0; i < 8 * 8; ++i) {
		hdr_words[i] = (uint16_t)base[8 + 2 * i] |
		    ((uint16_t)base[9 + 2 * i] << 8);
	}
	for (i = 0; i < 8; ++i) {
		uint16_t slv = hdr_words[i * 8];
		uint16_t ehdr_off = hdr_words[i * 8 + 1];
		uint16_t data_off = hdr_words[i * 8 + 2];
		uint16_t cnt = hdr_words[i * 8 + 3];
		volatile uint8_t *ent;
		if (!slv) {
			break;
		}
		ent = base + 144 + ehdr_off;
		for (j = 0; j < cnt; ++j, ent += 24) {
			for (k = 0; k < 7; ++k) {
				if (ent[k] != (uint8_t)id[k]) {
					break;
				}
			}
			if (k == 7) {
				uint16_t len = (uint16_t)ent[20] |
				    ((uint16_t)ent[21] << 8);
				uint16_t loff = (uint16_t)ent[22] |
				    ((uint16_t)ent[23] << 8);
				if (out_off) {
					*out_off = 144u + data_off + loff;
				}
				unsigned n = len / 2;
				volatile uint8_t *d = base + 144 + data_off + loff;
				if (n > max) {
					n = max;
				}
				for (k = 0; k < n; ++k) {
					out[k] = (uint16_t)d[2 * k] |
					    ((uint16_t)d[2 * k + 1] << 8);
				}
				*outn = n;
				return true;
			}
		}
	}
	return false;
}

#define RSCC_LEGACY       0x23400u  /* RSCC lives in the GMU space on legacy GMUs */
#define PDC_PA            0x0b290000u   /* DT gmu_pdc */
#define PDC_SEQ_PA        0x0b490000u   /* DT gmu_pdc_seq */

/*
 * GX rail prerequisites (Linux a6xx_gmu_rpmh_init + the power_config tail),
 * run while the CM3 is still in reset. v18 proved the OOB BOOT_SLUMBER
 * vote never completes without them: the GMU's RPMh client is gated by
 * GMU_RPMH_CTRL (enabled in the boot sequence below), and the PDC/RSC
 * sequencers carry the wake/sleep TCS commands with the ARC resource
 * addresses taken from cmd-db by id (fajita/Bergamota showed the Linux
 * hardcodes are wrong on real phones). On legacy GMUs the RSCC register
 * file sits inside the GMU window at dword 0x23400 (RSCC_OFFSET_LEGACY).
 * Byte-wise access only - cmd-db is Device memory (v17 lesson).
 */
static void
gauguin_gx_pdc_rsc(void)
{
	static const uint32_t rsc_seq[5] = {
		0xa7a506a0, 0xa1e6a6e7, 0xa2e081e1, 0xe9a982e2, 0x0020e8a8,
	};
	static const uint32_t pdc_seq[5] = {
		0xfebea1e1, 0xa5a4a3a2, 0x8382a6e0, 0xbce3e284, 0x002081fc,
	};
	vm_offset_t pdc, pdcseq;
	uint16_t lvl[32];
	unsigned n;
	uint32_t mx_off = 0, cx_off = 0, gfx_off = 0, rb;

	cmddb = ml_io_map(CMDDB_PA, CMDDB_SIZE);
	printf("GAUGUIN GX: cmd-db mapped=%#llx\n", (uint64_t)cmddb);
	(void)sweet_log_flush_now();
	if (!cmddb) {
		return;
	}
	if (!gauguin_cmdbdb_aux("mx.lvl", lvl, 32, &n, &mx_off) ||
	    !gauguin_cmdbdb_aux("cx.lvl", lvl, 32, &n, &cx_off) ||
	    !gauguin_cmdbdb_aux("gfx.lvl", lvl, 32, &n, &gfx_off)) {
		printf("GAUGUIN GX: cmd-db ARC resources missing\n");
		(void)sweet_log_flush_now();
		return;
	}
	printf("GAUGUIN GX: pdc cmd-db offs mx=%#x cx=%#x gfx=%#x\n",
	    mx_off, cx_off, gfx_off);
	(void)sweet_log_flush_now();

	/* RSCC (legacy GMU: dword 0x23400 inside the mapped GMU window). */
	gauguin_gmu_wr(RSCC_LEGACY + 0x004u, 1u << 24);    /* RSC_STATUS0_DRV0 */
	gauguin_gmu_wr(RSCC_LEGACY + 0x00bu, 1);           /* PDC_SLAVE_ID_DRV0 */
	gauguin_gmu_wr(RSCC_LEGACY + 0x00eu, 0);           /* HIDDEN_TCS_CMD0_DATA */
	gauguin_gmu_wr(RSCC_LEGACY + 0x00du, 0);           /* HIDDEN_TCS_CMD0_ADDR */
	gauguin_gmu_wr(RSCC_LEGACY + 0x010u, 0);           /* DATA + 2 */
	gauguin_gmu_wr(RSCC_LEGACY + 0x00fu, 0);           /* ADDR + 2 */
	gauguin_gmu_wr(RSCC_LEGACY + 0x012u, 0x80000000u); /* DATA + 4 */
	gauguin_gmu_wr(RSCC_LEGACY + 0x011u, 0);           /* ADDR + 4 */
	gauguin_gmu_wr(RSCC_LEGACY + 0x100u, 0);           /* OVERRIDE_START_ADDR */
	gauguin_gmu_wr(RSCC_LEGACY + 0x008u, 0x4520);      /* PDC_SEQ_START_ADDR */
	gauguin_gmu_wr(RSCC_LEGACY + 0x009u, 0x4510);      /* PDC_MATCH_VALUE_LO */
	gauguin_gmu_wr(RSCC_LEGACY + 0x00au, 0x4514);      /* PDC_MATCH_VALUE_HI */
	for (unsigned i = 0; i < 5; ++i) {
		gauguin_gmu_wr(RSCC_LEGACY + 0x180u + i, rsc_seq[i]);
	}
	rb = gauguin_gmu_rd(RSCC_LEGACY + 0x180u);
	printf("GAUGUIN GX: rsc sequencer written (seq0=%#x)\n", rb);
	(void)sweet_log_flush_now();

	pdc = ml_io_map(PDC_PA, 0x6000);
	pdcseq = ml_io_map(PDC_SEQ_PA, 0x1000);
	if (!pdc || !pdcseq) {
		printf("GAUGUIN GX: pdc map failed\n");
		(void)sweet_log_flush_now();
		return;
	}
	for (unsigned i = 0; i < 5; ++i) {
		gg_wr(pdcseq, 4u * i, pdc_seq[i]);
	}
	/* PDC TCS1 (wake): mx on, cx/gfx keep. */
	gg_wr(pdc, 4u * 0x1573u, 7);               /* TCS1_CMD_ENABLE_BANK */
	gg_wr(pdc, 4u * 0x1574u, 0);               /* TCS1_CMD_WAIT_FOR_CMPL */
	gg_wr(pdc, 4u * 0x1572u, 0);               /* TCS1_CONTROL */
	gg_wr(pdc, 4u * 0x1575u, 0x10108);         /* CMD0_MSGID */
	gg_wr(pdc, 4u * 0x1576u, mx_off);          /* CMD0_ADDR */
	gg_wr(pdc, 4u * 0x1577u, 1);               /* CMD0_DATA */
	gg_wr(pdc, 4u * 0x1579u, 0x10108);
	gg_wr(pdc, 4u * 0x157au, cx_off);
	gg_wr(pdc, 4u * 0x157bu, 0);
	gg_wr(pdc, 4u * 0x157du, 0x10108);
	gg_wr(pdc, 4u * 0x157eu, gfx_off);
	gg_wr(pdc, 4u * 0x157fu, 0);
	/* PDC TCS3 (sleep): mx/cx low, gfx off. */
	gg_wr(pdc, 4u * 0x15d7u, 7);
	gg_wr(pdc, 4u * 0x15d8u, 0);
	gg_wr(pdc, 4u * 0x15d6u, 0);
	gg_wr(pdc, 4u * 0x15d9u, 0x10108);
	gg_wr(pdc, 4u * 0x15dau, mx_off);
	gg_wr(pdc, 4u * 0x15dbu, 2);
	gg_wr(pdc, 4u * 0x15ddu, 0x10108);
	gg_wr(pdc, 4u * 0x15deu, cx_off);
	gg_wr(pdc, 4u * 0x15dfu, 2);               /* a619 cx sleep level */
	gg_wr(pdc, 4u * 0x15e1u, 0x10108);
	gg_wr(pdc, 4u * 0x15e2u, gfx_off);
	gg_wr(pdc, 4u * 0x15e3u, 3);
	gg_wr(pdc, 4u * 0x1148u, 0);               /* PDC_GPU_SEQ_START_ADDR */
	gg_wr(pdc, 4u * 0x1140u, 0x80000001u);     /* PDC_GPU_ENABLE_PDC */
	rb = gg_rd(pdc, 4u * 0x1140u);
	printf("GAUGUIN GX: pdc sequencer + tcs written (enable=%#x)\n", rb);
	(void)sweet_log_flush_now();
}

/* CP memory (L3b): one 128 KiB WCOMB block, three LPAE tables, and the
 * windows at fixed GPU VAs. Guard gaps between the windows stay unmapped
 * (mxtp 0093 layout). */
#define CPMEM_SIZE       0x20000u
#define CPMEM_L1         0x0000u
#define CPMEM_L2         0x1000u
#define CPMEM_L3         0x2000u
#define CPMEM_RING       0x4000u   /* 32 KiB, RW */
#define CPMEM_SQE        0xc000u   /* 32 KiB, read-only */
#define CPMEM_SCRATCH    0x14000u  /* 16 KiB, RW */
#define CP_VA_RING       0x40000000u
#define CP_VA_SQE        0x40010000u
#define CP_VA_SCRATCH    0x40020000u
#define SQE_DWORDS       8066u     /* incl. the container dword */
#define GG_CB1           1
#define GG_ASID1         2u
#define GG_SID_CP        0u        /* gfx3d_user stream */
#define S2CR_TRANS_CB1   ((3u << 8) | GG_CB1)
#define PTE_PAGE_RO      (3ull | (1ull << 10) | (2ull << 8) | (2ull << 6) | (1ull << 11))

static vm_offset_t cpmem;
static uint64_t cp_pa;

static uint32_t
gm_rd32_raw(uint32_t off)
{
	uint32_t v = *(volatile uint32_t *)(cpmem + off);
	__asm__ volatile("dmb oshld" ::: "memory");
	return v;
}

static void
cp_wr64(uint32_t off, uint64_t v)
{
	__asm__ volatile("dmb oshst" ::: "memory");
	*(volatile uint64_t *)(cpmem + off) = v;
	__asm__ volatile("dsb osh" ::: "memory");
}

/*
 * CP memory stage (no CP registers touched, no SQE start): allocate the
 * block, build the tables, copy the SQE microcode in and read it back,
 * then route stream ID 0 (gfx3d_user) through CB1 and verify every
 * register. Mirrors mxtp 0093.
 */
static void
gauguin_cp_mem_setup(void)
{
	vm_offset_t kva = 0, cb;
	kern_return_t kr;
	ppnum_t pn;
	uint64_t ttbr_want, ttbr_got;
	uint32_t smr2, val;
	unsigned i, bad = 0;
	bool ok = true;

	printf("GAUGUIN CP: mem setup start\n");
	(void)sweet_log_flush_now();
	kr = kmem_alloc_contig(kernel_map, &kva, CPMEM_SIZE, 0,
	    0xffffffffu, 0u, 0u, 1u);
	if (kr != KERN_SUCCESS) {
		printf("GAUGUIN CP: alloc failed %d\n", kr);
		return;
	}
	pn = pmap_find_phys(kernel_pmap, (addr64_t)kva);
	if (!pn) {
		printf("GAUGUIN CP: no phys page\n");
		return;
	}
	cp_pa = ptoa_64(pn);
	for (i = 0; i < CPMEM_SIZE / PAGE_SIZE; i++) {
		pmap_set_cache_attributes(pn + i, VM_WIMG_WCOMB);
	}
	flush_dcache64((addr64_t)cp_pa, CPMEM_SIZE, TRUE);
	cpmem = kva;
	bzero((void *)cpmem, CPMEM_SIZE);
	printf("GAUGUIN CP: block pa=%#llx ring=%#x sqe=%#x scratch=%#x\n",
	    cp_pa, CP_VA_RING, CP_VA_SQE, CP_VA_SCRATCH);
	(void)sweet_log_flush_now();

	/* LPAE: 32-bit VA, 4 KiB granule, level 1 at VA[31:30]. */
	cp_wr64(CPMEM_L1 + 8u * (CP_VA_RING >> 30), (cp_pa + CPMEM_L2) | PTE_TABLE);
	cp_wr64(CPMEM_L2 + 8u * ((CP_VA_RING >> 21) & 0x1ff),
	    (cp_pa + CPMEM_L3) | PTE_TABLE);
	for (i = 0; i < 8; i++) {
		cp_wr64(CPMEM_L3 + 8u * (((CP_VA_RING >> 12) & 0x1ff) + i),
		    (cp_pa + CPMEM_RING + 0x1000u * i) | PTE_PAGE_NC);
	}
	for (i = 0; i < 8; i++) {
		cp_wr64(CPMEM_L3 + 8u * (((CP_VA_SQE >> 12) & 0x1ff) + i),
		    (cp_pa + CPMEM_SQE + 0x1000u * i) | PTE_PAGE_RO);
	}
	for (i = 0; i < 4; i++) {
		cp_wr64(CPMEM_L3 + 8u * (((CP_VA_SCRATCH >> 12) & 0x1ff) + i),
		    (cp_pa + CPMEM_SCRATCH + 0x1000u * i) | PTE_PAGE_NC);
	}
	/* ring..SQE and SQE..scratch gaps (8 + 4 pages) stay 0 = unmapped. */
	val = gm_rd32_raw(CPMEM_L3 + 8u * (((CP_VA_RING >> 12) & 0x1ff) + 8));
	printf("GAUGUIN CP: gap pte0=%#x (want 0)\n", val);
	(void)sweet_log_flush_now();

	/* SQE microcode: skip the container dword, copy, read back every word. */
	for (i = 1; i < SQE_DWORDS; i++) {
		*(volatile uint32_t *)(cpmem + CPMEM_SQE + 4u * (i - 1u)) =
		    gauguin_sqe_fw[i];
	}
	for (i = 1; i < SQE_DWORDS; i++) {
		if (*(volatile uint32_t *)(cpmem + CPMEM_SQE + 4u * (i - 1u)) !=
		    gauguin_sqe_fw[i]) {
			bad++;
		}
	}
	printf("GAUGUIN CP: sqe %u dwords copied, %u mismatch\n",
	    SQE_DWORDS - 1u, bad);
	(void)sweet_log_flush_now();
	if (bad) {
		return;
	}

	/* SMMU CB1 + exact SMR2 for stream ID 0. */
	cb = gg_smmu + SMMU_CB(GG_CB1);
	smr2 = gg_rd(gg_smmu, SMMU_SMR(2));
	if (gg_rd(cb, SMMU_CB_SCTLR) != 0 || (smr2 & SMR_VALID)) {
		printf("GAUGUIN CP: CB1/SMR2 in use, skip (sctlr=%#x smr2=%#x)\n",
		    gg_rd(cb, SMMU_CB_SCTLR), smr2);
		return;
	}
	gg_wr(cb, SMMU_CB_SCTLR, 0);
	gg_wr(gg_smmu, SMMU_GR1 + SMMU_CBA2R(GG_CB1), CBA2R_RW64_64BIT);
	gg_wr(gg_smmu, SMMU_GR1 + SMMU_CBAR(GG_CB1), CBAR_S1_BYPASS_S2);
	gg_wr(cb, SMMU_CB_TTBCR2, TTBCR2_GMU);
	gg_wr(cb, SMMU_CB_TCR, TTBCR_GMU);
	ttbr_want = (cp_pa + CPMEM_L1) | ((uint64_t)GG_ASID1 << 48);
	__asm__ volatile("dmb oshst" ::: "memory");
	*(volatile uint64_t *)(cb + SMMU_CB_TTBR0) = ttbr_want;
	__asm__ volatile("dsb osh" ::: "memory");
	gg_wr(cb, SMMU_CB_MAIR0, MAIR0_GMU);
	gg_wr(cb, SMMU_CB_MAIR1, 0);
	gg_wr(cb, SMMU_CB_ACTLR, ACTLR_QCOM);
	gg_wr(cb, SMMU_CB_FSR, gg_rd(cb, SMMU_CB_FSR));
	gg_wr(cb, SMMU_CB_TLBIALL, 0);
	if (!gg_tlb_sync(cb, SMMU_CB_TLBSYNC, SMMU_CB_TLBSTATUS)) {
		return;
	}
	gg_wr(cb, SMMU_CB_SCTLR, SCTLR_GMU);
	gg_wr(gg_smmu, SMMU_S2CR(2), S2CR_TRANS_CB1);
	gg_wr(gg_smmu, SMMU_SMR(2), SMR_VALID | GG_SID_CP);
	gg_wr(gg_smmu, SMMU_TLBIALLNSNH, 0);
	if (!gg_tlb_sync(gg_smmu, SMMU_sTLBGSYNC, SMMU_sTLBGSTATUS)) {
		return;
	}
#define CP_CHK(what, got, want) do { \
	uint32_t g_ = (uint32_t)(got); \
	if (g_ != (uint32_t)(want)) { \
		printf("GAUGUIN CP: verify FAIL %s got=%#x want=%#x\n", \
		    what, g_, (uint32_t)(want)); \
		ok = false; \
	} \
} while (0)
	CP_CHK("SMR2", gg_rd(gg_smmu, SMMU_SMR(2)), SMR_VALID | GG_SID_CP);
	CP_CHK("S2CR2", gg_rd(gg_smmu, SMMU_S2CR(2)), S2CR_TRANS_CB1);
	CP_CHK("CBAR1", gg_rd(gg_smmu, SMMU_GR1 + SMMU_CBAR(GG_CB1)), CBAR_S1_BYPASS_S2);
	CP_CHK("CBA2R1", gg_rd(gg_smmu, SMMU_GR1 + SMMU_CBA2R(GG_CB1)), CBA2R_RW64_64BIT);
	CP_CHK("SCTLR1", gg_rd(cb, SMMU_CB_SCTLR), SCTLR_GMU);
	CP_CHK("TCR1", gg_rd(cb, SMMU_CB_TCR), TTBCR_GMU);
	CP_CHK("TCR2_1", gg_rd(cb, SMMU_CB_TTBCR2), TTBCR2_GMU);
	CP_CHK("MAIR0_1", gg_rd(cb, SMMU_CB_MAIR0), MAIR0_GMU);
	CP_CHK("ACTLR1", gg_rd(cb, SMMU_CB_ACTLR), ACTLR_QCOM);
#undef CP_CHK
	ttbr_got = *(volatile uint64_t *)(cb + SMMU_CB_TTBR0);
	if (ttbr_got != ttbr_want) {
		printf("GAUGUIN CP: verify FAIL TTBR0_1 got=%#llx want=%#llx\n",
		    ttbr_got, ttbr_want);
		ok = false;
	}
	if (!ok) {
		gauguin_smmu_faults();
		return;
	}
	printf("GAUGUIN CP: verified (CB1 LPAE ttbr0=%#llx SMR2=sid0)\n",
	    ttbr_got);
	(void)sweet_log_flush_now();
	gauguin_cp_start();
}

/* CP start (L3b step 2). GPU-core register dwords (base 0x3d00000). */
#define GR(off)          ((off) * 4u)
#define CP_RB_BASE_DW    0x800u
#define CP_RB_CNTL_DW    0x802u
#define CP_RB_RPTR_ADDR_DW 0x804u   /* 64-bit at 0x804/0x805 */
#define CP_RB_RPTR_DW    0x806u
#define CP_RB_WPTR_DW    0x807u
#define CP_SQE_CNTL_DW   0x808u
#define CP_STATUS_1_DW   0x824u
#define CP_HW_FAULT_DW   0x821u
#define CP_SQE_INSTR_BASE_DW 0x830u /* 64-bit at 0x830/0x831 */
#define CP_ROQ_THRESHOLDS_1_DW 0x8c1u
#define CP_ROQ_THRESHOLDS_2_DW 0x8c2u
#define CP_MEM_POOL_SIZE_DW 0x8c3u
#define CP_AHB_CNTL_DW   0x98du
#define RBBM_INT_0_STATUS_DW 0x201u
#define RBBM_PERFCTR_CNTL_DW 0x500u
#define RBBM_PERFCTR_GPU_BUSY_MASKED_DW 0x50bu
#define RBBM_INTERFACE_HANG_INT_CNTL_DW 0x01fu
#define PC_DBG_ECO_CNTL_DW 0x9e00u
#define UCHE_MODE_CNTL_DW 0xe01u
#define UCHE_WRITE_RANGE_MAX_LO_DW 0xe05u
#define UCHE_WRITE_THRU_BASE_LO_DW 0xe07u
#define UCHE_TRAP_BASE_LO_DW 0xe09u
#define UCHE_CACHE_WAYS_DW 0xe17u
#define UCHE_FILTER_CNTL_DW 0xe18u
#define UCHE_CLIENT_PF_DW 0xe19u
#define UCHE_GBIF_GX_CONFIG_DW 0xe3au
#define RB_NC_MODE_CNTL_DW 0x8e08u
#define SP_NC_MODE_CNTL_DW 0xae02u
#define TPL1_NC_MODE_CNTL_DW 0xb604u
#define RB_CNTL_VAL      0x800020cu  /* 32 KiB ring, half-full shadow */

/* ==================== L3b final: zap shader through TrustZone (0105)
 * v25 ended with the ring/SQE configuration sticking, zero faults and
 * zero fence drops, but cp_status1=0 and rptr frozen at 0: the SQE never
 * fetched. The a6xx SQE boots in secure mode; the vendor kgsl loads the
 * signed a615_zap through SCM PAS id 13 before releasing SQE_CNTL (DT
 * qcom,kgsl-hyp pas-id 13, firmware-name a615_zap). mxtp 0092 proved
 * these PAS calls under XNU on sweet, fajita console75c/76 the whole
 * ladder on a630. SMC64 convention (0065 PSCI conduit, smc #0): x0 fn
 * (0x40000000 | 0x02000000 | svc<<8 | cmd), x1 arginfo (n | type <<
 * (4+2i)), x2-x4 args, x6 a6, x7 = 0 (the vendor passes SMCCC a7 = 0);
 * x0 status (0 ok, 1 interrupted, -12 busy -> 30 ms, 67 tries), x1
 * result.
 * ARGUMENT VECTOR - the device's own ABI, not fajita's: the gauguin
 * kernel (Xiaomi gauguin-r-oss drivers/firmware/qcom_scm-64.c) and
 * mainline qcom_scm.c both send the PAS id FIRST - INIT_IMAGE
 * SCM_ARGS(2, VAL, RW) args (13, mdata_pa); MEM_SETUP SCM_ARGS(3)
 * args (13, addr, size); AUTH_AND_RESET and SHUTDOWN SCM_ARGS(1)
 * args (13). fajita's (mdata_pa, 0) form is a different TZ generation
 * and leaves PAS 13 unserved here.
 * INTERRUPTED is a resume, not a reissue: x0 == 1 means the TZ yielded;
 * the next call enters with x0 = 1, the same x1..x5 and x6 = the a6
 * that came back (arm64 smccc-call.S stores x6 in the QCOM_A6 quirk).
 * v27 reissued with the original fn and the second SMC never returned.
 * Carve-out is our own 1 MiB-PA-aligned block: the DT gpu_region
 * 0x8b715400 is an orphan the vendor loader never uses.
 */
#define ZAP_PAS_ID       13u
#define ZAP_FN_INIT      0x42000201u
#define ZAP_FN_MEM       0x42000202u
#define ZAP_FN_AUTH      0x42000205u
#define ZAP_FN_SHUTDOWN  0x42000206u
#define ZAP_FN_AVAIL     0xC2000601u
#define ZAP_MDT_DWORDS   1715u
#define ZAP_B02_DWORDS   496u
#define ZAP_B02_BYTES    1984u
#define ZAPBLK_SIZE      0x110000u  /* 1 MiB image + one metadata page */
#define ZAPBLK_META_OFF  0x100000u

static uint32_t
gg_parity(uint32_t val)
{
	return (0x9669u >> (0xfu & (val ^ (val >> 4) ^ (val >> 8) ^
	    (val >> 12) ^ (val >> 16) ^ (val >> 20) ^ (val >> 24) ^
	    (val >> 28)))) & 1u;
}

/* PM4 type-7 header (vendor adreno_pm4types.h cp_type7_packet):
 * opcode [22:16], count [14:0], odd parity at 23 and 15. The SQE
 * validates the parity bits - the old (7<<28)|(8<<16)|0x42 form
 * decoded as opcode 8 / count 66 and could never retire. */
static uint32_t
gg_pkt7(uint32_t opcode, uint32_t cnt)
{
	return (7u << 28) | cnt | (gg_parity(cnt) << 15) |
	    ((opcode & 0x7fu) << 16) | (gg_parity(opcode) << 23);
}

/*
 * One SCM SMC64 call: x0 fn, x1 arginfo, x2-x4 args, x6 a6, x7 = 0.
 * Interrupted rules from the device's own kernel (qcom_scm-64.c) and
 * mainline (qcom_scm-smc.c): x0 == 1 means the TZ yielded, and the
 * resume enters with x0 = 1, the same x1..x5 and x6 = the a6 that came
 * back. v27 reissued with the ORIGINAL fn and the second SMC never
 * returned. -12 is busy (30 ms, at most 67 tries).
 */
static int64_t
gauguin_scm_call(uint32_t fn, uint32_t arginfo, uint64_t a0, uint64_t a1,
    uint64_t a2, uint64_t *ret1)
{
	int64_t status;
	uint64_t cont = 0, res1 = 0, next = fn;
	unsigned busy = 0, intr = 0;

	for (;;) {
		register uint64_t x0 __asm__("x0") = next;
		register uint64_t x1 __asm__("x1") = arginfo;
		register uint64_t x2 __asm__("x2") = a0;
		register uint64_t x3 __asm__("x3") = a1;
		register uint64_t x4 __asm__("x4") = a2;
		register uint64_t x5 __asm__("x5") = 0;
		register uint64_t x6 __asm__("x6") = cont;
		register uint64_t x7 __asm__("x7") = 0;   /* SMCCC a7 */

		__asm__ volatile("smc #0" : "+r"(x0), "+r"(x1), "+r"(x2),
		    "+r"(x3), "+r"(x4), "+r"(x5), "+r"(x6), "+r"(x7) : :
		    "x8", "x9", "x10", "x11", "x12", "x13", "x14",
		    "x15", "x16", "x17", "cc", "memory");
		status = (int64_t)x0;
		res1 = x1;
		cont = x6;
		if (status == 1) {
			/* Interrupted: bounded and logged - v26 spun in this
			 * loop silently; every beat must leave evidence. */
			if (++intr <= 4 || (intr % 16) == 0) {
				printf("GAUGUIN SCM: fn=%#x interrupted x%u "
				    "(cont=%#llx res1=%#llx)\n", fn, intr,
				    (unsigned long long)cont,
				    (unsigned long long)res1);
				(void)sweet_log_flush_now();
			}
			if (intr >= 64) {
				break;     /* give up: caller prints status 1 */
			}
			next = 1;      /* resume entry, NOT the original fn */
			continue;
		}
		if (status == -12 && ++busy <= 67) {
			if (busy <= 3 || (busy % 10) == 0) {
				printf("GAUGUIN SCM: fn=%#x busy x%u\n", fn, busy);
				(void)sweet_log_flush_now();
			}
			delay(30000);      /* busy: 30 ms, bounded */
			continue;
		}
		break;
	}
	if (ret1 != NULL) {
		*ret1 = res1;
	}
	return status;
}

/*
 * Load the signed zap through PAS 13 (fajita console75c order, mxtp
 * 0092 memory plan): probe the four calls, validate the metadata ELF,
 * put the mdt on its own page and the b02 into a 1 MiB-aligned block,
 * then INIT_IMAGE -> MEM_SETUP -> copy -> AUTH_AND_RESET. The block is
 * never freed; nothing touches it after AUTH.
 */
static bool
gauguin_zap_load(void)
{
	static const uint32_t fns[4] = { ZAP_FN_INIT, ZAP_FN_MEM, ZAP_FN_AUTH,
		ZAP_FN_SHUTDOWN };
	static const char *names[4] = { "INIT_IMAGE", "MEM_SETUP",
		"AUTH_AND_RESET", "SHUTDOWN" };
	vm_offset_t kva = 0;
	kern_return_t kr;
	ppnum_t pn;
	uint64_t pa, meta_pa, img_pa, r1;
	uint32_t magic, phoff, phnum;
	int64_t st;
	unsigned i, bad = 0;

	printf("GAUGUIN ZAP: start\n");
	(void)sweet_log_flush_now();
	for (i = 0; i < 4; ++i) {
		st = gauguin_scm_call(ZAP_FN_AVAIL, 0x1u, fns[i], 0, 0, &r1);
		printf("GAUGUIN ZAP: pas %s available: status %lld result %llu\n",
		    names[i], (long long)st, (unsigned long long)r1);
		(void)sweet_log_flush_now();
	}
	/* Metadata sanity: ELF32, phoff 52, 3 program headers, ph2 = the
	 * RWE segment (dword indices: e_phoff at 28 -> dw7, e_phnum at 44
	 * -> dw11 low half, phdr i at 52 + 32*i -> dw13 + 8*i). */
	magic = gauguin_zap_mdt[0];
	phoff = gauguin_zap_mdt[7];
	phnum = gauguin_zap_mdt[11] & 0xffffu;
	printf("GAUGUIN ZAP: meta magic=%#x phoff=%u phnum=%u\n",
	    magic, phoff, phnum);
	(void)sweet_log_flush_now();
	if (magic != 0x464c457fu || phoff != 52u || phnum != 3u) {
		printf("GAUGUIN ZAP: bad metadata ELF\n");
		(void)sweet_log_flush_now();
		return false;
	}
	for (i = 0; i < phnum; ++i) {
		printf("GAUGUIN ZAP: phdr %u: type %u off %#x paddr %#x filesz %u memsz %u flags %#x\n",
		    i, gauguin_zap_mdt[13 + 8u * i], gauguin_zap_mdt[14 + 8u * i],
		    gauguin_zap_mdt[16 + 8u * i], gauguin_zap_mdt[17 + 8u * i],
		    gauguin_zap_mdt[18 + 8u * i], gauguin_zap_mdt[19 + 8u * i]);
		(void)sweet_log_flush_now();
	}
	if (gauguin_zap_mdt[29] != 1u || gauguin_zap_mdt[35] != 0x8000007u ||
	    gauguin_zap_mdt[33] != ZAP_B02_BYTES) {
		printf("GAUGUIN ZAP: ph2 is not the RWE segment\n");
		(void)sweet_log_flush_now();
		return false;
	}

	kr = kmem_alloc_contig(kernel_map, &kva, ZAPBLK_SIZE, 0,
	    0xffffffffu, 63u, 0u, 1u);   /* PA 1 MiB-aligned, 16 KiB pages */
	if (kr != KERN_SUCCESS) {
		printf("GAUGUIN ZAP: alloc failed %d\n", kr);
		(void)sweet_log_flush_now();
		return false;
	}
	pn = pmap_find_phys(kernel_pmap, (addr64_t)kva);
	if (!pn) {
		printf("GAUGUIN ZAP: no phys page\n");
		(void)sweet_log_flush_now();
		return false;
	}
	pa = ptoa_64(pn);
	img_pa = pa;                    /* 1 MiB-aligned via pnum_mask */
	meta_pa = pa + ZAPBLK_META_OFF;
	for (i = 0; i < ZAPBLK_SIZE / PAGE_SIZE; i++) {
		pmap_set_cache_attributes(pn + i, VM_WIMG_WCOMB);
	}
	flush_dcache64((addr64_t)pa, ZAPBLK_SIZE, TRUE);
	bzero((void *)kva, ZAPBLK_SIZE);
	printf("GAUGUIN ZAP: block pa=%#llx img=%#llx meta=%#llx\n",
	    (uint64_t)pa, img_pa, meta_pa);
	(void)sweet_log_flush_now();

	/* The metadata onto its own page before INIT_IMAGE (TZ reads it). */
	for (i = 0; i < ZAP_MDT_DWORDS; ++i) {
		*(volatile uint32_t *)(kva + ZAPBLK_META_OFF + 4u * i) =
		    gauguin_zap_mdt[i];
	}
	for (i = 0; i < ZAP_MDT_DWORDS; ++i) {
		if (*(volatile uint32_t *)(kva + ZAPBLK_META_OFF + 4u * i) !=
		    gauguin_zap_mdt[i]) {
			bad++;
		}
	}
	flush_dcache64((addr64_t)meta_pa, 0x1000u, TRUE);
	printf("GAUGUIN ZAP: meta copied %u dwords, %u mismatch\n",
	    ZAP_MDT_DWORDS, bad);
	(void)sweet_log_flush_now();
	if (bad) {
		return false;
	}

	/* qcom_scm_pas_init_image(): SCM_ARGS(2, VAL, RW), (pas id, mdata). */
	printf("GAUGUIN ZAP: INIT_IMAGE fn=%#x arginfo=%#x "
	    "args=%#llx/%#llx\n", ZAP_FN_INIT, 0x82u,
	    (unsigned long long)ZAP_PAS_ID, (unsigned long long)meta_pa);
	(void)sweet_log_flush_now();
	st = gauguin_scm_call(ZAP_FN_INIT, 0x82u, ZAP_PAS_ID, meta_pa, 0, &r1);
	printf("GAUGUIN ZAP: INIT_IMAGE status %lld result %llu\n",
	    (long long)st, (unsigned long long)r1);
	(void)sweet_log_flush_now();
	if (st != 0) {
		return false;
	}
	/* qcom_scm_pas_mem_setup(): SCM_ARGS(3), (pas id, addr, size). */
	printf("GAUGUIN ZAP: MEM_SETUP fn=%#x arginfo=%#x "
	    "args=%#llx/%#llx\n", ZAP_FN_MEM, 0x3u,
	    (unsigned long long)ZAP_PAS_ID, (unsigned long long)img_pa);
	(void)sweet_log_flush_now();
	st = gauguin_scm_call(ZAP_FN_MEM, 0x3u, ZAP_PAS_ID, img_pa, 0x1000u,
	    &r1);
	printf("GAUGUIN ZAP: MEM_SETUP status %lld result %llu\n",
	    (long long)st, (unsigned long long)r1);
	(void)sweet_log_flush_now();
	if (st != 0) {
		return false;
	}

	/* The single loadable segment -> img+0 (copy between MEM_SETUP and
	 * AUTH, the Linux/fajita order; no readback once TZ owns it). */
	for (i = 0; i < ZAP_B02_DWORDS; ++i) {
		*(volatile uint32_t *)(kva + 4u * i) = gauguin_zap_b02[i];
	}
	flush_dcache64((addr64_t)img_pa, 0x1000u, TRUE);
	printf("GAUGUIN ZAP: segment %u bytes -> img+0\n", ZAP_B02_BYTES);
	(void)sweet_log_flush_now();

	/* qcom_scm_pas_auth_and_reset(): SCM_ARGS(1), (pas id). */
	printf("GAUGUIN ZAP: AUTH_AND_RESET fn=%#x arginfo=%#x args=%#llx/0\n",
	    ZAP_FN_AUTH, 0x1u, (unsigned long long)ZAP_PAS_ID);
	(void)sweet_log_flush_now();
	st = gauguin_scm_call(ZAP_FN_AUTH, 0x1u, ZAP_PAS_ID, 0, 0, &r1);
	printf("GAUGUIN ZAP: AUTH_AND_RESET status %lld result %llu\n",
	    (long long)st, (unsigned long long)r1);
	(void)sweet_log_flush_now();
	if (st != 0) {
		return false;
	}
	printf("GAUGUIN ZAP: authenticated and started (PAS 13)\n");
	(void)sweet_log_flush_now();
	return true;
}

/*
 * Configure and start the CP (Linux a6xx_start + a6xx_rb_start order, the
 * fajita/Bergamota write set). Every write is read back; CP_ME_INIT is the
 * first packet the CP ever fetches - RPTR catching up with WPTR means the
 * SQE fetched and executed it through CB1.
 */
static void
gauguin_cp_start(void)
{
	volatile uint32_t *ring = (volatile uint32_t *)(cpmem + CPMEM_RING);
	unsigned i, wptr, rptr;
	uint32_t val;
	bool ok = true;

	printf("GAUGUIN CP: start - pre-state\n");
	(void)sweet_log_flush_now();
	val = gg_rd(gg.gpu, GR(CP_SQE_CNTL_DW));
	printf("GAUGUIN CP: pre sqe_cntl=%#x\n", val);
	(void)sweet_log_flush_now();
	val = gg_rd(gg.gpu, GR(CP_HW_FAULT_DW));
	printf("GAUGUIN CP: pre hw_fault=%#x\n", val);
	(void)sweet_log_flush_now();
	val = gg_rd(gg.gpu, GR(CP_RB_RPTR_DW));
	printf("GAUGUIN CP: pre rptr=%u\n", val);
	(void)sweet_log_flush_now();
	val = gg_rd(gg.gpu, GR(CP_RB_WPTR_DW));
	printf("GAUGUIN CP: pre wptr=%u\n", val);
	(void)sweet_log_flush_now();
	/*
	 * mxtp 0094-0098 lesson: CP registers only stick when written in
	 * the cold state (SQE_CNTL 0, no inherited pointers/faults); an
	 * early SQE_CNTL write raises illegal-instruction IRQ 0x80 and
	 * later writes read back 0. Refuse anything but a cold CP.
	 */
	if (gg_rd(gg.gpu, GR(CP_SQE_CNTL_DW)) != 0 ||
	    gg_rd(gg.gpu, GR(CP_RB_RPTR_DW)) != 0 ||
	    gg_rd(gg.gpu, GR(CP_RB_WPTR_DW)) != 0 ||
	    gg_rd(gg.gpu, GR(CP_HW_FAULT_DW)) != 0) {
		printf("GAUGUIN CP: not cold - abort (sqe=%#x rptr=%u wptr=%u "
		    "hw_fault=%#x)\n",
		    gg_rd(gg.gpu, GR(CP_SQE_CNTL_DW)),
		    gg_rd(gg.gpu, GR(CP_RB_RPTR_DW)),
		    gg_rd(gg.gpu, GR(CP_RB_WPTR_DW)),
		    gg_rd(gg.gpu, GR(CP_HW_FAULT_DW)));
		(void)sweet_log_flush_now();
		return;
	}
	printf("GAUGUIN CP: cold - proceeding\n");
	(void)sweet_log_flush_now();

	/*
	 * OOB GPU_SET (set bit 16, ack bit 24): Linux a6xx_hw_init's first step
	 * - "Make sure the GMU keeps the GPU on while we set it up". v23 proved
	 * the GMU's power arbitration drops CP writes without it (every readback
	 * returned the reset default). Held for the whole CP sequence and cleared
	 * on every exit (clear = set bit 24).
	 */
	gauguin_gmu_wr(G_H2G_INTR_SET, 1u << 16);
	ok = false;
	for (i = 0; i < 5000; ++i) {
		val = gauguin_gmu_rd(G_INTR_INFO);
		if (val & (1u << 24)) {
			ok = true;
			break;
		}
		delay(10);
	}
	printf("GAUGUIN CP: oob gpu_set info=%#x acked=%d\n", val, ok);
	(void)sweet_log_flush_now();
	if (!ok) {
		printf("GAUGUIN CP: L3b abort - GPU_SET not acked\n");
		(void)sweet_log_flush_now();
		return;
	}

	/* Fence state: WRITEDROPPED is direct evidence of dropped writes. */
	val = gauguin_gmu_rd(G_AO_FENCE_CTRL);
	printf("GAUGUIN CP: fence ctrl=%#x\n", val);
	(void)sweet_log_flush_now();
	val = gauguin_gmu_rd(G_FENCE_STATUS);
	printf("GAUGUIN CP: fence status=%#x\n", val);
	(void)sweet_log_flush_now();
	val = gauguin_gmu_rd(G_AHB_FENCE_0);
	printf("GAUGUIN CP: fence range0=%#x\n", val);
	(void)sweet_log_flush_now();
	val = gauguin_gmu_rd(G_FENCE_RANGE_1);
	printf("GAUGUIN CP: fence range1=%#x\n", val);
	(void)sweet_log_flush_now();
	gauguin_gmu_wr(G_FENCE_STATUS_CLR, 0x7);
	gauguin_gmu_wr(G_AO_FENCE_CTRL, 0);


	/*
	 * The CP's core clock. v21 proved the SQE never fetches (ME_INIT stuck
	 * at rptr=0, zero faults) and the L1 log showed gx_gfx3d gated: on this
	 * SoC the GX domain does not auto-enable it. Route GPLL0_OUT_MAIN (600
	 * MHz; both GCC branches already voted on for the GMU) through the RCG
	 * and open the branch (mainline gpucc-sm6350: cmd_rcgr 0x101c, CFG +4,
	 * parent_map_1 GPLL0=5, CBCR 0x1054 bit 0).
	 */
	val = gg_rd(gg.gpucc, GPUCC_GFX3D_SRC);
	printf("GAUGUIN CP: gfx3d src_cmd=%#x\n", val);
	(void)sweet_log_flush_now();
	val = gg_rd(gg.gpucc, GPUCC_GFX3D_SRC + 4u);
	printf("GAUGUIN CP: gfx3d src_cfg=%#x\n", val);
	(void)sweet_log_flush_now();
	val = gg_rd(gg.gpucc, GPUCC_GFX3D_CBCR);
	printf("GAUGUIN CP: gfx3d cbcr=%#x\n", val);
	(void)sweet_log_flush_now();
	gg_wr(gg.gpucc, GPUCC_GFX3D_SRC + 4u, 0x501u); /* src=5 gpll0, /1 */
	gg_wr(gg.gpucc, GPUCC_GFX3D_SRC, 1u);          /* update */
	for (i = 0; i < 1000 && (gg_rd(gg.gpucc, GPUCC_GFX3D_SRC) & 1u); ++i) {
		delay(10);
	}
	gg_wr(gg.gpucc, GPUCC_GFX3D_CBCR,
	    gg_rd(gg.gpucc, GPUCC_GFX3D_CBCR) | 1u);
	val = gg_rd(gg.gpucc, GPUCC_GFX3D_CBCR);
	printf("GAUGUIN CP: gfx3d on (cbcr=%#x)\n", val);
	(void)sweet_log_flush_now();

	/* hw_init essentials (a619/a630 family), each write verified. */
#define CP_WRV(dw, v) do { \
		gg_wr(gg.gpu, GR(dw), (v)); \
		val = gg_rd(gg.gpu, GR(dw)); \
		if (val != (uint32_t)(v)) { \
			printf("GAUGUIN CP: write FAIL dw=%#x got=%#x want=%#x\n", \
			    (unsigned)(dw), val, (unsigned)(v)); \
			(void)sweet_log_flush_now(); \
			ok = false; \
		} \
	} while (0)
	CP_WRV(UCHE_WRITE_RANGE_MAX_LO_DW, 0xffffffc0);
	gg_wr(gg.gpu, GR(UCHE_WRITE_RANGE_MAX_LO_DW) + 4u, 0x0001ffff);
	CP_WRV(UCHE_TRAP_BASE_LO_DW, 0xfffff000);
	gg_wr(gg.gpu, GR(UCHE_TRAP_BASE_LO_DW) + 4u, 0x0001ffff);
	gg_wr(gg.gpu, GR(UCHE_WRITE_THRU_BASE_LO_DW), 0xfffff000);
	gg_wr(gg.gpu, GR(UCHE_WRITE_THRU_BASE_LO_DW) + 4u, 0x0001ffff);
	CP_WRV(UCHE_FILTER_CNTL_DW, 0x804);
	CP_WRV(UCHE_CACHE_WAYS_DW, 0x4);
	{
		unsigned tries = 0;
		uint32_t roq2 = 0;
		for (tries = 0; tries < 3; ++tries) {
			gg_wr(gg.gpu, GR(CP_ROQ_THRESHOLDS_2_DW), 0x010000c0);
			delay(1000);
			roq2 = gg_rd(gg.gpu, GR(CP_ROQ_THRESHOLDS_2_DW));
			if (roq2 == 0x010000c0) {
				break;
			}
		}
		printf("GAUGUIN CP: roq2=%#x after %u tries (canary)\n", roq2, tries);
		(void)sweet_log_flush_now();
		(void)roq2;
	}
	CP_WRV(CP_ROQ_THRESHOLDS_1_DW, 0x8040362c);
	CP_WRV(CP_MEM_POOL_SIZE_DW, 128);
	CP_WRV(PC_DBG_ECO_CNTL_DW, 0x00018000);
	CP_WRV(CP_AHB_CNTL_DW, 0x1);
	CP_WRV(RBBM_PERFCTR_CNTL_DW, 0x1);
	CP_WRV(RBBM_PERFCTR_GPU_BUSY_MASKED_DW, 0xffffffff);
	CP_WRV(RB_NC_MODE_CNTL_DW, 0x0);
	CP_WRV(TPL1_NC_MODE_CNTL_DW, 0x0);
	CP_WRV(SP_NC_MODE_CNTL_DW, 0x0);
	CP_WRV(UCHE_MODE_CNTL_DW, 0x0);
	CP_WRV(RBBM_INTERFACE_HANG_INT_CNTL_DW, 0x41000000);
	CP_WRV(UCHE_CLIENT_PF_DW, 0x1);
	printf("GAUGUIN CP: hw_init %s (flaky regs WARN, continuing)\n",
	    ok ? "verified" : "warnings");
	(void)sweet_log_flush_now();

	/* rb_start: RPTR shadow in scratch, then CNTL/BASE, then the SQE. */
	gg_wr(gg.gpu, GR(CP_RB_RPTR_ADDR_DW), CP_VA_SCRATCH);
	gg_wr(gg.gpu, GR(CP_RB_RPTR_ADDR_DW) + 4u, 0);
	gg_wr(gg.gpu, GR(CP_RB_CNTL_DW), RB_CNTL_VAL);
	gg_wr(gg.gpu, GR(CP_RB_BASE_DW), CP_VA_RING);
	gg_wr(gg.gpu, GR(CP_RB_BASE_DW) + 4u, 0);
	gg_wr(gg.gpu, GR(CP_SQE_INSTR_BASE_DW), CP_VA_SQE);
	gg_wr(gg.gpu, GR(CP_SQE_INSTR_BASE_DW) + 4u, 0);
	val = gg_rd(gg.gpu, GR(CP_RB_CNTL_DW));
	printf("GAUGUIN CP: rb cntl=%#x\n", val);
	(void)sweet_log_flush_now();
	val = gg_rd(gg.gpu, GR(CP_RB_BASE_DW));
	printf("GAUGUIN CP: rb base=%#x\n", val);
	(void)sweet_log_flush_now();
	val = gg_rd(gg.gpu, GR(CP_SQE_INSTR_BASE_DW));
	printf("GAUGUIN CP: sqe base=%#x\n", val);
	(void)sweet_log_flush_now();

	/* The zap (0105): the a6xx SQE boots secure, so load the signed
	 * shader through TrustZone PAS 13 before touching SQE_CNTL (vendor
	 * kgsl microcode_load order; mxtp 0092 + fajita console75c). */
	if (!gauguin_zap_load()) {
		printf("GAUGUIN CP: L3b abort - zap failed\n");
		(void)sweet_log_flush_now();
		gauguin_gmu_wr(G_H2G_INTR_SET, 1u << 24); /* clear GPU_SET */
		printf("GAUGUIN CP: oob gpu_set cleared\n");
		(void)sweet_log_flush_now();
		return;
	}

	/* Release the CP from the GMU's reset: SQE_CNTL = 1 (after the zap,
	 * before the first packet - kgsl a6xx_rb_start order). */
	gg_wr(gg.gpu, GR(CP_SQE_CNTL_DW), 1);
	val = gg_rd(gg.gpu, GR(CP_SQE_CNTL_DW));
	printf("GAUGUIN CP: sqe_cntl=%#x\n", val);
	(void)sweet_log_flush_now();
	val = gg_rd(gg.gpu, GR(CP_STATUS_1_DW));
	printf("GAUGUIN CP: cp_status1=%#x\n", val);
	(void)sweet_log_flush_now();

	/* CP_ME_INIT: drm minimal 8 payload dwords behind a real type-7
	 * header (opcode 0x48 at bits[22:16] with odd parity - the old
	 * 0x70080042 decoded as opcode 8 / count 66). */
	ring[0] = gg_pkt7(0x48u, 8u);
	ring[1] = 0x2fu;                /* MAX_CONTEXT|ERR_DET|HDR|RESET|UCODE */
	ring[2] = 0x00000003;           /* MAX_CONTEXT */
	ring[3] = 0x20000000;           /* ERROR_DETECTION_CONTROL */
	ring[4] = 0x00000000;           /* HEADER_DUMP addr lo */
	ring[5] = 0x00000000;           /* HEADER_DUMP hi + size */
	ring[6] = 0x00000000;           /* DEFAULT_RESET_STATE */
	ring[7] = 0x00000000;           /* UCODE_WORKAROUND_MASK */
	ring[8] = 0x00000000;           /* pad */
	printf("GAUGUIN CP: me_init hdr=%#x\n", ring[0]);
	(void)sweet_log_flush_now();
	__asm__ volatile("dmb oshst" ::: "memory");
	wptr = 9;
	gg_wr(gg.gpu, GR(CP_RB_WPTR_DW), wptr);
	printf("GAUGUIN CP: ME_INIT submitted (wptr=%u)\n", wptr);
	(void)sweet_log_flush_now();

	/* Poll RPTR until ME_INIT retires (fajita: RPTR=WPTR=9). */
	ok = false;
	for (i = 0; i < 2000; ++i) {
		rptr = gg_rd(gg.gpu, GR(CP_RB_RPTR_DW));
		if (rptr == wptr) {
			ok = true;
			break;
		}
		delay(1000);
		if ((i % 50) == 49) {
			printf("GAUGUIN CP: waiting rptr=%u wptr=%u\n", rptr, wptr);
			(void)sweet_log_flush_now();
		}
	}
	printf("GAUGUIN CP: ME_INIT %s (rptr=%u wptr=%u)\n",
	    ok ? "RETIRED" : "STUCK", rptr, wptr);
	(void)sweet_log_flush_now();

	/* CP_SET_SECURE_MODE 0 (opcode 0x66, 1 payload dword): kgsl
	 * adreno_set_unsecured_mode; fajita console76 left the ring at
	 * RPTR=WPTR=11 with exactly this packet. */
	ring[9] = gg_pkt7(0x66u, 1u);
	ring[10] = 0u;
	printf("GAUGUIN CP: secmode hdr=%#x\n", ring[9]);
	(void)sweet_log_flush_now();
	__asm__ volatile("dmb oshst" ::: "memory");
	wptr = 11;
	gg_wr(gg.gpu, GR(CP_RB_WPTR_DW), wptr);
	printf("GAUGUIN CP: SET_SECURE_MODE submitted (wptr=%u)\n", wptr);
	(void)sweet_log_flush_now();
	for (i = 0; i < 2000; ++i) {
		rptr = gg_rd(gg.gpu, GR(CP_RB_RPTR_DW));
		if (rptr == wptr) {
			break;
		}
		delay(1000);
		if ((i % 50) == 49) {
			printf("GAUGUIN CP: waiting rptr=%u wptr=%u\n", rptr, wptr);
			(void)sweet_log_flush_now();
		}
	}
	printf("GAUGUIN CP: SET_SECURE_MODE %s (rptr=%u wptr=%u)\n",
	    rptr == wptr ? "RETIRED" : "STUCK", rptr, wptr);
	(void)sweet_log_flush_now();
	ok = ok && (rptr == wptr);

	val = *(volatile uint32_t *)(cpmem + CPMEM_SCRATCH);
	printf("GAUGUIN CP: rptr shadow=%u\n", val);
	(void)sweet_log_flush_now();
	val = gg_rd(gg.gpu, GR(CP_STATUS_1_DW));
	printf("GAUGUIN CP: post cp_status1=%#x\n", val);
	(void)sweet_log_flush_now();
	val = gg_rd(gg.gpu, GR(RBBM_INT_0_STATUS_DW));
	printf("GAUGUIN CP: rbbm_int_0=%#x\n", val);
	(void)sweet_log_flush_now();
	val = gg_rd(gg.gpu, GR(CP_HW_FAULT_DW));
	printf("GAUGUIN CP: hw_fault=%#x\n", val);
	(void)sweet_log_flush_now();

	/* Release PAS 13 (fajita console76 shuts down after the test). */
	{
		int64_t st = gauguin_scm_call(ZAP_FN_SHUTDOWN, 0x1u,
		    ZAP_PAS_ID, 0, 0, NULL);
		printf("GAUGUIN ZAP: SHUTDOWN status %lld\n", (long long)st);
		(void)sweet_log_flush_now();
	}
	if (!ok) {
		printf("GAUGUIN CP: L3b abort after SEC_MODE\n");
		(void)sweet_log_flush_now();
		gauguin_gmu_wr(G_H2G_INTR_SET, 1u << 24); /* clear GPU_SET */
		printf("GAUGUIN CP: oob gpu_set cleared\n");
		(void)sweet_log_flush_now();
		return;
	}
	printf("GAUGUIN CP: started - the CP executes our packets\n");
	(void)sweet_log_flush_now();
}

static void
gauguin_gx_rail_on(void)
{
	uint16_t pri[32], sec[32];
	unsigned npri = 0, nsec = 0, pindex = 0, sindex = 0, i;
	uint32_t val;
	bool on = false;

	cmddb = ml_io_map(CMDDB_PA, CMDDB_SIZE);
	printf("GAUGUIN GX: cmd-db mapped=%#llx\n", (uint64_t)cmddb);
	if (!cmddb) {
		return;
	}
	(void)sweet_log_flush_now();
	if (!gauguin_cmdbdb_aux("gfx.lvl", pri, 32, &npri, NULL) ||
	    !gauguin_cmdbdb_aux("mx.lvl", sec, 32, &nsec, NULL)) {
		printf("GAUGUIN GX: gfx.lvl/mx.lvl not found in cmd-db\n");
		return;
	}
	printf("GAUGUIN GX: gfx.lvl n=%u:", npri);
	for (i = 0; i < npri; ++i) {
		printf(" %u", pri[i]);
	}
	printf("\n");
	(void)sweet_log_flush_now();
	printf("GAUGUIN GX: mx.lvl n=%u:", nsec);
	for (i = 0; i < nsec; ++i) {
		printf(" %u", sec[i]);
	}
	printf("\n");
	(void)sweet_log_flush_now();

	/* drm a6xx_gmu_rpmh_arc_votes_init for level 416 (the one OPP). */
	for (i = 0; i < npri; ++i) {
		if (pri[i] >= 416u) {
			pindex = i;
			break;
		}
	}
	if (i == npri) {
		printf("GAUGUIN GX: level 416 not in gfx.lvl\n");
		return;
	}
	for (i = 0; i < nsec; ++i) {
		if (sec[i] >= 416u) {
			sindex = i;
			break;
		}
		if (sec[i]) {
			sindex = i;
		}
	}
	printf("GAUGUIN GX: votes pri=%u(%u) sec=%u(%u)\n",
	    pindex, pri[pindex], sindex, nsec ? sec[sindex] : 0);
	(void)sweet_log_flush_now();

	/* gfx_rail_on: BOOT_SLUMBER_OPTION=0 + vote idxs + OOB boot_slumber. */
	gauguin_gmu_wr(G_BOOT_SLUMBER, 0);
	gauguin_gmu_wr(G_GX_VOTE_IDX, pindex);
	gauguin_gmu_wr(G_MX_VOTE_IDX, sindex);
	gauguin_gmu_wr(G_H2G_INTR_SET, 1u << 22);
	for (i = 0; i < 5000; ++i) {
		val = gauguin_gmu_rd(G_INTR_INFO);
		if (val & (1u << 30)) {
			break;
		}
		delay(10);
	}
	printf("GAUGUIN GX: oob info=%#x acked=%d\n", val, !!(val & (1u << 30)));
	(void)sweet_log_flush_now();
	gauguin_gmu_wr(G_INTR_CLR, 1u << 30);
	if (!(val & (1u << 30))) {
		printf("GAUGUIN GX: OOB boot_slumber timed out\n");
		return;
	}

	/* Poll SPTPRAC for GX power. */
	for (i = 0; i < 2000; ++i) {
		val = gauguin_gmu_rd(G_SPTPRAC);
		if (!(val & (GX_GDSC_POWER_OFF | GX_CLK_OFF))) {
			on = true;
			break;
		}
		delay(10);
	}
	printf("GAUGUIN GX: sptprac=%#x on=%d\n", val, on);
	(void)sweet_log_flush_now();
	if (!on) {
		return;
	}

	/* The GPU core window, alive for the first time. */
	gg.gpu = ml_io_map(GPU_PA, GPU_SIZE);
	if (!gg.gpu) {
		printf("GAUGUIN GX: gpu map failed\n");
		return;
	}
	printf("GAUGUIN GX: reading RBBM_STATUS...\n");
	(void)sweet_log_flush_now();
	val = gg_rd(gg.gpu, 0x840u);
	printf("GAUGUIN GX: rbbm_status=%#x\n", val);
	(void)sweet_log_flush_now();
	val = gg_rd(gg.gpu, 0x848u);
	printf("GAUGUIN GX: rbbm_status3=%#x\n", val);
	(void)sweet_log_flush_now();
	printf("GAUGUIN GX: GX ALIVE (L3 next: ringbuffer + CP_BLIT)\n");
	(void)sweet_log_flush_now();
	gauguin_cp_mem_setup();
}

void
gauguin_gpu_init(void)
{
	int enabled = 1;
	thread_t thread;
	if (gg.started) {
		return;
	}
	gg.started = true;
	(void)PE_parse_boot_argn("gauguingpu", &enabled, sizeof(enabled));
	gg_level = enabled;
	if (!enabled) {
		printf("GAUGUIN GPU: disabled (gauguingpu=0)\n");
		return;
	}
	kern_return_t kr = kernel_thread_start(gg_thread, NULL, &thread);
	if (kr == KERN_SUCCESS) {
		thread_deallocate(thread);
	} else {
		printf("GAUGUIN GPU: kernel_thread_start failed %d\n", kr);
	}
}
#endif /* ARM64_BOARD_CONFIG_SWEET */
