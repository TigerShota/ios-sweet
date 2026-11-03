# ios-sweet

**在红米 Note 9 Pro 5G 上运行 Apple 的 iOS 16。**

本项目将 Apple 开源的 XNU 内核（xnu-8796，iOS 16 世代）移植到小米红米 Note 9 Pro 5G（`gauguin`，高通 SM7225 / 骁龙 750G）。其上运行的是**未修改的 iPhone X iOS 16.7.16（20H392）用户态**：launchd、系统守护进程、backboardd、SpringBoard 与自带 App。

本项目移植自 [dipa-studio/ios-sweet](https://github.com/dipa-studio/ios-sweet)（最初的红米 Note 10 Pro / `sweet` / SM7150 移植）。XNU 补丁序列与引导 shim 为两者共用；设备相关部分——设备树、内存映射、PMIC 按键、触摸控制器——均为 gauguin 专属。

截至 **2026-11-03**，手机可**越过 Apple logo 启动进入 SpringBoard UI**。首次启动因冷 MobileGestalt 缓存下的一次性数据库重建需要较长时间（约 10 分钟）。触摸（SPI 上的 Novatek NT36672C）已工作。渲染仍完全由 CPU 完成——但 **GPU 内核侧 bring-up 已完成管理面**：CX 供电域 → GPU SMMU 页表翻译 → GMU 固件加载并启动（`fw_init=0xbabeface`）→ HFI 握手全通（boot-arg `gauguingpu` 分级启用），GX 轨上电与渲染路径（ringbuffer + CP_BLIT）进行中。

> 实验性 bring-up，不是可用的手机操作系统。大量功能缺失或为桩（见下文），Apple 的安全模型基本被关闭。系统安装进 `userdata` 分区，因此**会清空 Android 用户数据**。

## 已工作

| 领域 | 状态 |
| --- | --- |
| 引导 | 小米原厂 bootloader → `fastboot boot` 我们的 shim → XNU。不向任何引导分区刷写内容 |
| CPU | 经 PSCI 启动全部 8 核（`cpus=8`；ADT 携带全部 8 个 CPU 节点——2× Cortex-A76 + 6× Cortex-A55），16K 页，GICv3 中断、定时器 |
| 内存 | 4G 以下高 bank 中的 896 MiB 窗口（`0xc8000000`）——更大或低 bank 的窗口都会撞上开机画面的 framebuffer 切割或非 RAM 空洞（见 NOTES.md） |
| 存储 | 自研 UFS 主机驱动；iOS 根文件系统为 `userdata` 分区内的日志式 HFS+ 卷，可读写 |
| iOS 用户态 | 真实 iPhone X 16.7.16 根文件系统与共享缓存；launchd 与系统守护进程正常运行 |
| 显示 | 内核 IOSurface + IOMobileFramebuffer（`AppleCLCD`）服务；CoreAnimation 软件渲染；帧被拷贝进 bootloader 留下的 framebuffer。以 iPhone X 面板（1125×2436）呈现给 iOS |
| 触摸 | SPI（GENI QUP SE0）上的 Novatek NT36672C：固件每次开机由主机从 vendor 分区加载，中断报点已上机验证（0084） |
| GUI | backboardd 与 SpringBoard 越过 logo 正常启动 |
| 按键 | 电源键走 PMIC PON 块（SPMI，已在真机验证）；音量下键为 PON RESIN 输入；音量上键为 PM6350 SID 0 GPIO2（PPID `0x0c1`）——已在 0060 修复、内核重建完成，开机验证待做 |
| 设备身份 | iPhone X（`iPhone10,3` / `D22AP`）：无 Home 键，手势 UI |
| 激活 | mobileactivationd 的原生 "Hactivation" 路径。不涉及 Apple 服务器 |
| 诊断 | `userdata` 末尾的持久 4 MiB 日志环、按进程的栈采样器、IOKit 追踪与控制台崩溃报告——本机没有串口，整个 bring-up 依赖这些手段 |

## 尚未工作

- **GPU 未参与渲染。** 一切仍由 CPU 软件合成（与原版 sweet 移植一致）。内核侧管理面 bring-up 已完成：0085 点亮 Adreno 619 CX 供电域（`gauguingpu=1`，缺省即开），0087+0088 把 `a619_gmu.bin` 装入 GMU 微控制器并经 GPU SMMU（CB0 LPAE 页表、SMR 流匹配）完成最小 HFI 握手（`gauguingpu=2`，**真机闭环**：`fw_init=0xbabeface`，MSG_INIT/FW_VERSION/PERF_TABLE/BW_TABLE/TEST 全部确认）。0097 经 cmd-db ARC 投票给 GX 轨上电（进行中）。ringbuffer 与 CP_BLIT 合成加速是下一步。
- **无 Wi-Fi、蓝牙、蜂窝、音频、相机、传感器、GPS 及 SEP / Face ID。** `bluetoothd` 与 `fairplayd.H2` 崩溃循环。
- **首次启动慢：** Apple logo 停留约 10 分钟。第二次启动速度尚未验证。
- **安全关闭：** 宽松的代码签名（AMFI）与沙盒策略、桩 AppleKeyStore、无数据保护。不要存放任何个人数据。

## 引导流程

```
ABL（小米原厂 bootloader，已解锁）
  -> fastboot boot shim-boot.img              （临时引导；不向 boot 分区刷写任何内容）
     -> shim：自己的 MMU 设置，读取活动 FDT，从 ramdisk 加载 payload
        -> XNU Mach-O + 生成的 Apple 设备树（ADT）+ boot-args
           -> XNU -> UFS 驱动 -> userdata 分区内的 HFS+ 根
              -> dyld + 共享缓存 -> launchd -> 守护进程 -> backboardd + SpringBoard
```

本机没有串口。内核把控制台画进 ABL 留下的 framebuffer，并在 `userdata` 末尾维护持久 4 MiB 日志环。在 OrangeFox recovery 里用 `tools/sweet-log-pull.sh` 读取该日志，无需拍摄屏幕。

## 自己动手

你需要一台**已解锁 bootloader** 的红米 Note 9 Pro 5G（`gauguin`）和一个 GitHub 账号。全部构建跑在 GitHub Actions 上——本移植全程在 Windows PC 上完成，无需任何本地工具链。**`userdata` 上的一切都会丢失。** Bootloader、recovery、modem 与 persist 分区从不触碰；shim 需要的原厂设备树已携带在 `gauguin/dtb`。

### 0. 备份手机

临时引导 OrangeFox（或任何带 adb root 的 recovery），至少备份 `persist`、`modemst1`、`modemst2`、`fsg`、`fsc` 以及 `boot`、`dtbo`、`vbmeta`。它们包含每台设备的校准数据。不要放进 git。

### 1. 构建内核

内核在 GitHub 的 macOS runner 上通过 [blacktop/darwin-xnu-build](https://github.com/blacktop/darwin-xnu-build) 构建。工作流拉取 xnu-8796，按顺序应用 `xnu/patches/`，并暂存其所需的固定版本开源 libpthread、HFS 与 BearSSL 源码。Fork 本仓库（公开仓库有免费 macOS 分钟数）后运行：

```sh
gh workflow run xnu.yml -R YOU/ios-sweet
gh run download RUN_ID -R YOU/ios-sweet -n xnu-kernel -D xnu/out
```

在花费一次 CI 运行之前，`tools/xnu-cc.py` 可按上一次 CI 日志的编译命令对单文件做本地检查。

### 2. 构建 iOS 根镜像

`gauguin-root.yml` 在 Linux runner 上完成全部流程：从 Apple 下载 iPhone X（iPhone10,3）iOS 16.7.16（20H392）IPSW，提取 OS、SystemOS 与 AppOS 磁盘镜像，并运行 `tools/mkiosroot.sh` + `tools/sweet-root-tweaks.py`（跳过设置向导、激活状态预置、守护进程裁剪）。产物为 gzip 压缩并切分的根镜像：

```sh
gh workflow run gauguin-root.yml -R YOU/ios-sweet
gh run download RUN_ID -R YOU/ios-sweet -n ios16-root -D root
cat root/root.part-* > ios16-root.img.gz && gunzip ios16-root.img.gz
img2simg ios16-root.img ios16-root.simg
```

### 3. 构建引导镜像

`gauguin-shim.yml` 拉取内核产物（默认取最近一次成功的 xnu 运行），用 `tools/mkadt.py` 生成 gauguin 的 Apple 设备树（内存窗口、8 个 CPU 节点、iPhone X 身份），把内核 + ADT + boot-args 打包进 payload，并基于 `gauguin/dtb` 的原厂 DTB 构建 shim 引导镜像：

```sh
gh workflow run gauguin-shim.yml -R YOU/ios-sweet
gh run download RUN_ID -R YOU/ios-sweet -n gauguin-shim-boot -D shim/out
```

不显眼的 boot-args（工作流默认值）说明：

| Boot-arg | 作用 |
| --- | --- |
| `rd=ufsroot0 ufsrw=1 rootrw=1` | 根在 `userdata` 分区，可写 |
| `cpus=8` | 启动八核；ADT 携带全部 8 个（`cpus=1` 是安全回退） |
| `gauguingpu=0/1/2` | GPU bring-up 阶段：缺省 = 1（CX 供电域点亮）；`=2` 额外加载 GMU 固件并握手 HFI；`=0` 关闭 |
| `sweettouch=0`、`sweetkeys=0` | 关闭触摸或按键驱动 |
| `sweetfb_noconsole=1` | 不把内核文本画在 GUI 上。仍写入持久日志；panic 会画 |
| `sweetfb_hz=30` | CPU 拷贝的面板刷新率 |
| `sweetsample=…`、`sweetiolog=…` | 驱动 bring-up 的按进程栈采样器与 IOKit 追踪列表 |

### 4. 刷入根镜像并引导

```sh
fastboot -S 512M flash userdata ios16-root.simg   # 约 7 分钟；不要拔线
# 小米 fastboot 在大刷写后经常卡住：用 电源 + 音量下 重新进入 fastboot
fastboot boot shim/out/shim-boot.img
```

首次启动在 Apple logo 停留约 10 分钟，随后出现 SpringBoard UI。要回到 fastboot，按住 电源 + 音量下。

### 5. 读取内核日志

```sh
fastboot boot recovery/recovery.img        # OrangeFox，临时引导
tools/sweet-log-pull.sh logs/boot.txt      # userdata 末尾的 4 MiB 控制台环
adb reboot bootloader
```

日志包含带回溯的崩溃报告（`CRASH name ...`）、采样器输出的每线程栈分段，以及驱动们的 `SWEET ...` 行。`tools/sweet-crash.py` 可对照共享缓存符号化。

### QEMU

`tools/qemu-sweet.py` 在 QEMU `virt` 上以测试 RAM 盘根引导同一内核；模拟 RAM 大小跟随 mkadt 的内存窗口。适合只改内核的场景；见 `docs/QEMU_TESTING.md`。

## 仓库结构

| 路径 | 内容 |
| --- | --- |
| `gauguin/` | gauguin 的原厂 DTB 与生成的基础 ADT |
| `shim/` | 裸机引导 shim（C + 汇编），打包为 Android boot.img v2 |
| `xnu/patches/` | XNU 补丁序列（由 CI 按序应用），附 libpthread、HFS 与 BearSSL 的暂存脚本 |
| `.github/workflows/xnu.yml` | 内核 CI 构建 |
| `.github/workflows/gauguin-shim.yml` | 引导镜像构建（内核产物 + ADT + shim） |
| `.github/workflows/gauguin-root.yml` | 从 IPSW 构建 iOS 根镜像 |
| `.github/workflows/gauguin-dsc.yml`、`symbolize.yml` | dyld 共享缓存处理与崩溃符号化 |
| `tools/mkadt.py` | 生成 Apple 设备树：gauguin 内存窗口、8 个 CPU 节点、iPhone X 身份 |
| `tools/mkpayload.py` | 把内核 + ADT + boot-args 打包进 shim payload |
| `tools/mkiosroot.sh` | 从 IPSW 镜像构建 HFS+ iOS 根 |
| `tools/sweet-root-tweaks.py` | 根镜像的 bring-up 修改（跳过设置、激活、守护进程） |
| `tools/sweet-log-pull.sh`、`sweet-log.py` | 在 recovery 中读取持久内核日志 |
| `tools/sweet-crash.py` | 符号化控制台崩溃报告 |
| `tools/xnu-cc.py`、`kernel-build-local.sh` | 本地单文件编译检查 / 复刻 CI 的本地内核构建 |
| `tools/qemu-sweet.py` | QEMU 测试引导（含 framebuffer 文本解码） |
| `tools/adt.py`、`remotezip.py`、`unpack_bootv2.py`、`sparsediff.py` | 辅助工具 |
| `NOTES.md` | 详细工程日志：每次引导、崩溃与决策，以及硬件事实 |

不进 git 的内容：分区备份、活动设备树、recovery 镜像、Apple 固件与根镜像、下载或生成的构建树。它们含设备序列号与校准数据，或 Apple 的版权文件。

## 内核补丁

| # | 内容 |
| --- | --- |
| 0001–0009 | 工具链兼容、SWEET 机器配置、非 PAC arm64、Apple 内部件的桩 |
| 0010–0018 | 早期 framebuffer 控制台（0012 为 gauguin 重写：MMU 开启后保留调试控制台）、physmap 修复、无 corecrypto 的 PRNG、设备树 platform expert、定时器 PPI、引导 CPU 驱动 |
| 0019–0020 | Apple 开源 libpthread 内核提供者；BearSSL AES |
| 0021–0027 | RAM 盘根、mockfs、SHA-256、最小 AMFI、Apple 开源 HFS+、MD5 |
| 0028–0034 | UFS 主机驱动与根设备、可读写根、4G 以上 DMA |
| 0035–0045 | Entitlement、bindfs、AppleKeyStore 桩、IOWatchdog、控制台崩溃报告 |
| 0046 | IOSurface（IOCoreSurfaceRoot）与 IOMobileFramebuffer（AppleCLCD）服务 |
| 0047–0055 | 诊断：持久日志、/dev/null tee、IOKit 追踪、堆字符串与用户态栈采样器；宽松沙盒策略；HFS 重命名交换；AppleKeyStore 选择器（含 kAKSWrapKey/UnwrapKey） |
| 0056–0057 | 节流面板拷贝；无外部身份解析器 |
| 0058 | HID 事件服务。Apple IOHIDFamily-1915.140.3 的 user client 与事件队列，匹配 iOS 16.7 用户态 |
| 0059 | iPhone X 身份（设备树读取 `hw.product` / `hw.target`） |
| 0060 | SPMI 电源与音量键；GENI I²C 触摸（gauguin：音量上键读 PM6350 SID 0 GPIO2，PPID `0x0c1`） |
| 0061 | 以 1125×2436 呈现显示 |
| 0062 | RPMh 触摸供电轨投票；电池电源源 |
| 0063 | GENI FIFO 打包与 Goodix GT9896 协议（sweet 的触摸 IC；gauguin 改用 0084 的 Novatek 驱动） |
| 0064 | 授予沙盒权限（PRIV_WORK_INTERVAL、PRIV_GLOBAL_PROC_INFO 等） |
| 0065 | SMP：经 PSCI CPU_ON 启动副核 |
| 0066–0067 | 故障区域崩溃信息；控制台静音 |
| 0068 | 已映射 surface 的 IOSurface 查询语义 |
| 0069 | 持久日志批量刷新 |
| 0070–0071 | pid_hibernate 系统调用；触摸边缘事件（上游 sweet 的导航工作） |
| 0072–0077、0081 | USB 设备模式：DWC3 ep0、VBUS 覆写、CDC-ECM `ue0`、RX mbuf、UDP 实时日志、链路状态（来自上游 sweet；gauguin 上未测试） |
| 0074-gauguin、0075-gauguin | gauguin bring-up 辅助：bsdinit 追踪；fbcon 满屏清除 |
| 0078 | 8 核 SMP（全部 CPU 节点） |
| 0079–0080 | 显示电源控制；网络路由追踪（上游 sweet） |
| 0082-gauguin | 额外的早期 IO L2 槽位——896 MiB 窗口的早期 IO 映射需要两个 L2 槽位 |
| 0084-gauguin | Novatek NT36672C 触摸驱动：GENI SPI 传输、vendor 分区固件主机加载、报点事件（真机闭环） |
| 0085-gauguin | GPU L1：点亮 Adreno 619 CX 供电域并读寄存器诊断（GCC + GPUCC + GDSC；`gauguingpu`，缺省开） |
| 0086 | 恢复内核侧 `IORPC.kernelContent` 字段——Xcode 16 iig 生成的用户客户端需要它 |
| 0087-gauguin | GPU L2：`a619_gmu.bin` 批量装入 GMU ITCM/DTCM、GMU 启动与最小 HFI 握手（`gauguingpu=2`） |
| 0088-gauguin | GPU L2 收尾：GMU 走 GPU SMMU——64K WCOMB GMEM + LPAE 页表，CB0/SMR0/1 精确编程带回读校验，HFI 队列 offset/iova 修复 |
| 0089-gauguin | 死亡点保底：`sweet_log_flush_now()` 强制刷盘——静默复位不再吃掉驱动最后一口气（每条 GPU 诊断打印后落盘） |
| 0090–0092-gauguin | GPU SMMU 三时钟（iface/mem/mem_iface = GCC 0x45004/0x4500c/0x45014）在首次访问前全部打开 |
| 0093–0095-gauguin | SMMU 地址空间扫描与逐寄存器探测（诊断插桩；证明全部寄存器可读） |
| 0096-gauguin | SMMU 稳定读：**所有诊断打印一读一打**——单条 printf 捆绑多个 MMIO 读会在 sm6350 互连上死锁（v17-v23 的全部"挂死"均源于此）；修复后 L2 完整闭环 |
| 0097-gauguin | GPU L3 第一步：解析 cmd-db（0x80860000）gfx.lvl/mx.lvl ARC 表 → OOB boot_slumber 投票 GX 轨 → SPTPRAC 上电 → 首次读 GPU 核心 RBBM_STATUS |

## 关键硬件事实

- 内存：6 GB 双 bank。低 bank `0x9b000000`–`0xbcbfffff`（开机画面 framebuffer 切割于 `0xa0000000`，非 RAM `removed_region` 空洞 `0xbcc00000`–`0xc7afffff`），高 bank `0xc7b00000`–`0x1ffffffff`。内核窗口：`0xc8000000` + 896 MiB——最大的干净窗口：低 bank 384 MiB 窗口饿死 iOS 16 首次启动，512/576 MiB 跨越空洞，4G 以上地址导致板卡复位。
- GICv3（含安全扩展）：定时器走 Group 1 IRQ（虚拟定时器 INTID 19），不像 Apple 芯片那样走 FIQ。PSCI 经 `smc`，19.2 MHz 时基，ARMv8.2 无指针认证。
- 面板：1080×2400；shim 复用 ABL 的持续 splash framebuffer 扫出。
- GPU：Adreno 619（chipid 0x06010900，A615 家族 = legacy GMU 寄存器路径）。寄存器窗口 `0x3d00000`（核心，GX 域）/`0x3d40000`（GPU SMMU，qcom,kgsl-smmu-v2）/`0x3d61000`（GMU）/`0x3d90000`（GPUCC）/`0x3d9e000`（CX 域诊断窗口）；窗口之间存在无映射空洞（读空洞 = 总线死等，如 `0x3d61000` 起始处）。GPU SMMU 三时钟 = GCC `0x45004`（cfg_ahb）/`0x4500c`（memnoc_gfx）/`0x45014`（snoc_dvm_gfx = mem_iface）。GMU 固件 `a619_gmu.bin`（32K ITCM/DTCM 镜像，vendor 分区提取）。ARC 投票表在 cmd-db SRAM `0x80860000`（`gfx.lvl`/`mx.lvl` 条目）。**sm6350 互连对背靠背无间隔 MMIO 读敏感**：单条 printf 求值多个 MMIO 读会死锁整机——诊断打印必须一读一打。
- 触摸：Novatek NT36672C，SPI QUP SE0（`0x880000`），CS 在 gpio3，reset tlmm 21，irq tlmm 22，最高 10 MHz；固件 `novatek_nt36672c_j17_fw*.bin` 每次开机主机加载。
- 按键：电源（PON KPDPWR）与音量下（PON RESIN）在 PM6350 PON 块；音量上在 PM6350 SID 0 GPIO2（PPID `0x0c1`），低有效。

更多见 [`NOTES.md`](NOTES.md)。

## 致谢

基于 [dipa-studio/ios-sweet](https://github.com/dipa-studio/ios-sweet)——最初的红米 Note 10 Pro 移植。构建于 Apple 的开源发布（xnu、IOHIDFamily、libpthread、hfs）、[BearSSL](https://bearssl.org)、[blacktop/darwin-xnu-build](https://github.com/blacktop/darwin-xnu-build)、[ipsw](https://github.com/blacktop/ipsw)、Qualcomm 与 Xiaomi 公开的 Linux 内核源码（按键与触摸事实来自 gauguin vendor 设备树），以及 [OrangeFox Recovery](https://orangefox.download)。iOS 本身属于 Apple，本仓库不分发它。
