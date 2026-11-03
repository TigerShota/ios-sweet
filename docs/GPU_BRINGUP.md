# gauguin GPU bring-up 手册

Adreno 619（A615 家族，legacy GMU）在 XNU 上的点亮路径。记录已验证的事实、
每一步的寄存器序列与踩过的坑，供后续 ringbuffer/CP_BLIT 阶段与移植者参考。

## 阶梯

| 阶段 | 内容 | 状态 | boot-arg |
| --- | --- | --- | --- |
| L1 | CX 供电域（GCC cfg_ahb + GPUCC ahb + cx_gdsc） | ✅ 真机闭环 | `gauguingpu=1`（缺省） |
| L2 | GMU 固件 + GPU SMMU 页表 + HFI 握手 | ✅ 真机闭环（fw_init=0xbabeface，5 消息全确认） | `gauguingpu=2` |
| L3a | GX 轨上电（cmd-db ARC 投票 + OOB + SPTPRAC） | 进行中（0097） | 同上 |
| L3b | ringbuffer + CP_BLIT 合成加速 | 未开始 | — |

## 寄存器地图（SM7225）

| 区域 | 地址 | 说明 |
| --- | --- | --- |
| GPU 核心 | `0x3d00000`（+0x40000） | GX 域；RBBM_STATUS @0x840 |
| GPU SMMU | `0x3d40000`（+0x10000） | qcom,kgsl-smmu-v2；GR0 @+0、GR1 @+0x1000、IMPL_DEF1 @+0x6000、CB0 @+0x8000 |
| GMU | `0x3d61000`（+0x8000） | **窗口起始是空洞**——有效寄存器从 `0x3d6a000` 起 |
| GPUCC | `0x3d90000`（+0x9000） | cx_gdsc @0x106c、gx_gdsc @0x100c（在 0x3d9100c） |
| CX 诊断窗 | `0x3d9e000` | VBIF_VERSION 可读（CX 域活着的证据） |

窗口之间存在无映射空洞：读空洞 = 总线死等（`0x3d61000` 起始处即一例）。

## 时钟

GPU SMMU 三时钟（DTB `clock-names: iface_clk, mem_clk, mem_iface_clk`，全部指向 GCC）：

| 名 | GCC 寄存器 | 位 |
| --- | --- | --- |
| iface_clk = gcc_gpu_cfg_ahb_clk | `0x45004` | bit 0（CLK_IS_CRITICAL） |
| mem_clk = gcc_gpu_memnoc_gfx_clk | `0x4500c` | bit 0 |
| mem_iface_clk = gcc_gpu_snoc_dvm_gfx_clk | `0x45014` | bit 0 |

**三者在首次 SMMU 访问前必须全部打开**。GMU 时钟链：GPLL0（`0x52010`/`0x52008`/`0x4514c`）、
memnoc、ddrss_axi（`0x2d038`）、GPUCC cxo（`0x109c`）、gmu RCG 200MHz（`0x1120/0x1124`，CFG=0x602）、cx_gmu（`0x1098`）。

## L2 序列（已验证）

1. CX 域上电（L1）
2. 分配 64K 物理连续 WCOMB GMEM（<4G；XNU pmap 高地址 flush 会截断 32 位 ppnum）
3. GMEM 内建 LPAE 页表（L1/L2/L3A/L3B），映射 GMU VA：dummy `0x60000000`、HFI `0x60100000`、dump `0x60200000`、log `0x60300000`
4. **先开三时钟**，再 `gauguin_smmu_probe()`
5. SMMU CB0 编程（TCR2→TCR→TTBR0 顺序；S2CR 先于 SMR 置 VALID；全部回读校验）
   - CBAR `S1_BYPASS_S2=(1<<16)|(3<<8)|(0xf<<12)`、CBA2R=1、TCR `0x00802020`、TCR2 `0x00038011`、MAIR0 `0x0004ff44`、ACTLR `(1<<30)|(1<<29)|(1<<28)`、SCTLR `0x00c01067`
   - SMR0=sid5（gmu kernel）、SMR1=sid4（gmu user），S2CR=0x300（转 CB0）
6. GMU 时钟链 → 固件 bulk 装入 ITCM（5120 字）→ G_BOOT_CONFIG=2、G_HFI_QTBL_ADDR=GMU_VA_HFI、G_CM3_CFG=0x4052、G_SYSRESET 1→0 放 CM3 出复位
7. 轮询 G_FW_INIT_RESULT 至 `0xbabeface`（或低 9 位 0x100）
8. G_HFI_CTRL_INIT=1 → 轮询 G_HFI_STATUS bit0 → HFI 消息（id 0/1/4/3/5）

## GX 轨上电（0097，vendor legacy 路径）

1. 解析 cmd-db（SRAM `0x80860000`）：版本 u32、magic `db 30 03 0c`、
   `rsc_hdr[8]`（16B：slv_id/hdr_off/data_off/cnt/ver/res[3]）、数据区从 +144 起；
   条目 24B（id[8]/pri/addr/len/offset），aux 数据 = u16 数组。
   需要的条目：`gfx.lvl`（pri 表）与 `mx.lvl`（sec 表）。
2. 投票（drm `a6xx_gmu_rpmh_arc_votes_init` 算法）：目标 level = OPP 的
   `qcom,level`（本机 DTB 唯一一档 850MHz，level 416）；
   pindex = 首个 pri[i] ≥ 416；sindex = 首个 sec[i] ≥ 416，否则最大的非零 sec。
3. `G_BOOT_SLUMBER_OPTION(0x1cbf8)=0`、`G_GX_VOTE_IDX(0x1cbf9)=pindex`、
   `G_MX_VOTE_IDX(0x1cbfa)=sindex`
4. OOB boot_slumber（A615 家族编码）：`G_H2G_INTR_SET(0x1f994) = BIT(22)` →
   轮询 `G_INTR_INFO(0x1f992) & BIT(30)` → `G_INTR_CLR` 清除
5. 轮询 `G_SPTPRAC(0x1f8d0)`：`!(val & (BIT(6)|BIT(7)))` = GX 上电
   （bit6 GX_GDSC_POWER_OFF、bit7 GX_CLK_OFF）
6. 此后 GPU 核心寄存器（`0x3d00000` 窗口）可读：RBBM_STATUS(0x840)、RBBM_STATUS3(0x848)

## 坑（按代价排序）

1. **单条 printf 捆绑多个 MMIO 读 = 整机死锁**。C 变参求值让编译器把多个
   volatile 读排成背靠背无间隔的总线事务，sm6350 互连直接挂死。
   v17–v23 的所有"SMMU 挂死"都是这个（16 个寄存器逐个读全部正常）。
   **诊断打印必须一读一打**（0096）。
2. **静默复位吃日志**：NOC 级故障把 1Hz 刷盘线程一起带走，RAM 里的打印
   全部丢失，磁盘日志停在死亡前几分钟。`sweet_log_flush_now()`（0089）
   在每条关键打印后强制落盘。
3. **窗口空洞**：reg 窗口起始未必是有效寄存器（0x3d61000 即空洞）。
   "读 A 挂死"不等于"A 所在窗口全封"——先扫窗口内部已知活点再下结论。
4. **时钟开在访问之后**：补丁里加时钟使能的位置要对着实际执行序检查
   （0090 加在 GMU 时钟块 = probe 之后，等于没开）。
5. patch 上传后 **grep 构建产物字符串**验证驱动真的编进去了（v14 教训：
   trailing LF 丢失 → git apply 静默跳过 → 内核无驱动）。
6. XNU 的 `ml_io_map` 粒度（0x1000 vs 0x10000）不影响行为（早期 IO 映射
   按 2MB L2 block）。

## 工具链

- 构建全走 GitHub Actions（macos-15 + Xcode_16.1，`BUILD_WERROR=0`）。
  本地链式补丁验证用 mini git-apply 克隆（Temp\build-0089..0097.py 模式）。
- 上机：`fastboot boot shim/out/shim-boot.img`；死亡后回 recovery，
  `dd` userdata 末尾 4M+4K（skip=28055540 count=1031 of=/dev/block/sda35）
   → `sweet-log.py` 解码。
- 每轮迭代保留：sweetlogNN.txt（环解码）+ 环残留区（上上次启动的尾巴）。
