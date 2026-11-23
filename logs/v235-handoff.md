# v235 交接说明（0268 PTE 验证轮）

## v235 判读结果（2026-11-21 晨，已定案）

日志 `out\sweetlog-v235\v235.txt`（14120 行）。三分支定案：

1. **页未映射：证伪。** 0268 walk 走完 9 个 blit 周期（cyc=0..8，at=56..280 步长 28），全部地址 `l2ok=1`、`pte&3=3`、`pa≠0`、`pn=0`（GEM 内存正常）。死亡周期 cyc=6（at=224，dst=0x55d6c000、src=0x55db2000、b4c2=0x55130cc0）三项全绿。行号 14038-14074。
2. **死点移动：证伪。** ib2 结构与 v234 逐位同构（305 dwords、10 周期 setup、walk end 305/305）。ROQ 尾 `0x702c0001,0x3,0x70108000,0x70100001` == 内存 ib2[224..227]（CP_BLIT(SCALE) 包头+参数 + 两个 op=0x10 包），ME 死点与 v233b/v234 完全相同：ib2+224 第 7 个 blit。
3. **2D 引擎状态：成立。** SMMU cb1 fsr=0x400 far=0、gfsr=0 仍为 boot 陈旧值，无新错（行 14081-14082）。

本轮布局：sub0=0x5552a000/72、sub1=0x55542000/72、sub2=0x5555a000/2411、sub3=0x5557a000/241；blit 序列 dst 0x55d66000→0x55d6e000（+0x1000/周期），TL=0x500003(x3,y5)、BR=0x200040(x64,y32)。

## v236（0269 flatbr 实验）状态

- 0269 已推送：`xnu/patches/0269-gauguin-flatbr-blit-shrink.patch`（3629 B，roundtrip OK）。内容：`gauguinflatbr=<val>` 复用 0262 flatmk 框架，把 spliced 流里所有 type4 写 GRAS_2D_DST_BR(0xb4c1) 的 payload 改写为 <val>（length-preserving）。
- chain `out\_v236chain.py`：xnu（0268+0269）→ shim（v232 574 cmdline + ` gauguinflatbr=0x80008` = 596 字符）→ artifacts 落盘 `out\v236\shim\out\`，cmdline 逐字节校验。
- 刷机脚本 `out\boot_v236.py`（TAG=v236，phase1/phase2 同 v235 流程，verdict 自动统计 0269 armed/hits、0268 blit、0205 0268 行）。

## v236 判读标准

- **死点消失/跑完全部 blit** → 楔死与 blit 工作量相关（缩小矩形避开），0270 沿此方向细调或直接测试逐步放大。
- **死点仍在第 7 个 blit（at=224 对应位置）** → 面积无关，指向每 blit 需要同步缺失 → 0270 做变长 CP_WAIT_FOR_IDLE 插入（需动 0261 splice 几何：ring size override、右移量、map 检查都要适配）。
- **死点移动到别的周期** → 按新死点重判（工作量阈值类）。
- 注意：0269 只改 BR payload，0268 walk 与 0205 探针仍会全绿输出（页映射本就没问题），判读只看死点位置。

## 目标

0268 patch 已推送（`xnu/patches/0268-gauguin-2d-blit-pte-walk.patch`，TigerShota/ios-sweet main）。本轮目的：wedge dump 时解析 hung ib2 每个 2D blit 周期的地址寄存器并探针 GEM PTE，**一次刷机判定** v233b/v234 共同死点（ib2+224 的 CP_BLIT(SCALE)）是"页未映射"还是"2D 引擎状态问题"。

## 已完成（2026-11-20）

| 事项 | 状态 |
|---|---|
| v234 判读 | 完成。ME 死在 ib2 offset 224 的 CP_BLIT(SCALE)；scratch2=0x600011a=上一发 fence → fence write 未执行；walk 305/305 全合法、parity 全对；SMMU cb1 fsr=0x400 far=0 为 boot 陈旧值，无新转换错 |
| 0268 生成/推送 | 完成，3921 B，roundtrip 验证过，apply-check 通过 |
| v235 CI | 本机后台任务已 dispatch（`out\_v235chain.py`，任务 ID dftlbwpzgd）：xnu.yml → gauguin-shim.yml（v232 原 574 cmdline）→ artifacts 落盘 `out\v235\shim\out\shim-boot.img` |
| 刷机脚本 | `out\boot_v235.py`（TAG=v235，adb 强制 `-P 5038`，IMG/OUTDIR 已改） |

## 过夜全自动链（已挂上，2026-11-20 深夜）

`out\_v235auto.py`（后台任务 llwesqplex）+ CI chain `out\_v235chain.py`（后台任务 olpzaonjmp）已启动：

1. 等 CI 落盘 `out\v235\shim\out\shim-boot.img`（上限 4 h）
2. 自动跑 `boot_v235.py phase1`（清 ring → fastboot boot → 300 s）
3. 自动跑 `boot_v235.py phase2`（等 recovery 上限 2 h → 拉 ring → verdict）

**唯一前提：电脑不休眠。** 醒后直接看 shell 输出尾部 verdict，或看 `out\sweetlog-v235\v235.txt`。

若断链（电脑休眠/重启），恢复顺序：① 若 shim-boot.img 已存在且是本轮的 → 直接 `python out\boot_v235.py phase1`；② 若 CI 没跑完 → 重跑 `python out\_v235chain.py`（幂等，会多等一轮构建）。

## 云端部分（GitHub Actions，本机关机也不影响）

若本机后台 chain 中断，GitHub 侧重跑（gh.exe 在 `D:\hackios\tools\gh\bin\gh.exe`，需清 HTTPS_PROXY 环境变量）：

1. dispatch xnu（构建含 0268 的内核）：

```
gh api -X POST repos/TigerShota/ios-sweet/actions/workflows/xnu.yml/dispatches -f ref=main -f "inputs[keep_going]=true"
```

2. 取新 run id（status=in_progress 那条）：

```
gh api "repos/TigerShota/ios-sweet/actions/workflows/xnu.yml/runs?per_page=3"
```

3. xnu 成功后 dispatch shim。cmdline 必须用 v232 payload 原文（574 字符，offset 0x48），`out\_v235chain.py` 会自动做这一步——优先直接重跑：

```
python out\_v235chain.py
```

（幂等：重新 dispatch 一轮 xnu+shim，多等一轮构建，无副作用。）

4. shim 成功后拉 artifacts：`python out\_artget.py <shim_run_id> v235`
5. 校验产物：`out\v235\shim\out\payload.bin` offset 0x48 的 cmdline 与 v232 逐字节一致；`shim-boot.img` 存在。

## 本地部分（需手机连电脑、OrangeFox recovery、adb 在线）

```
python out\boot_v235.py phase1     # 清 sweetlog → fastboot boot → 等 300 s
（300 s 后把手机弄回 OrangeFox recovery；若 xnu 自杀重启回 recovery 则无需人工）
python out\boot_v235.py phase2     # 自动等 recovery（上限 2 h）→ 拉 ring → 解码 → 打 verdict
```

产物：`out\sweetlog-v235\v235.txt`。

## 判读标准（看 0268 输出）

日志为含 `\0` 的解码文本，**grep 工具会当二进制**，用 python 过滤：

```python
import io
l = io.open(r'D:\hackios\out\sweetlog-v235\v235.txt', encoding='utf-8', errors='replace').read().splitlines()
for i, ln in enumerate(l):
    if '0268' in ln or '0205 0268' in ln:
        print(i + 1, ln)
```

预期输出两类行：

- `GAUGUIN CP: 0268 blit cyc=N at=<off> dst=<RB_2D_DST> src=<RB_2D_SRC> tl= br= b4c2=`（每 blit 周期一行，at= 是 ib2 内偏移）
- `GAUGUIN CP: 0205 0268 dst|src|b4c2 iova=... l2ok=... tbl=... pte=... pa=... pn=...`（每地址一行，0205 探针）

三种分支：

1. **死亡周期（预期 at=224、dst=0x5697c000）的 dst/src/b4c2 任一 `l2ok=0` 或 `pte&3==0` 或 `pa=0`** → 页未映射实锤 → 0269 修 GEM 映射层（查该 GEM 对象的页数 vs blit 访问范围、gg_gem_map 调用完整性）。
2. **全部地址 `l2ok=1` 且 `pte&3!=0`、pa≠0、pn≠0** → 页映射假设证伪 → 0269 转 2D 引擎状态实验：blit 周期间插 CP_WAIT_FOR_IDLE(0x26)，或减 tile 数（0xb4c0 GRAS_2D_DST_TL/BR 改小），观察死点是否移动。
3. **死点前移/后移/不再 wedge** → 0268 改变了时序（gg_2d_pte_walk 在 dump 路径新增 MMIO 读），按新死点重新判读。

注意：probe 输出里的 `l2ok` 只反映 L2 目录项与 L3 表基址匹配；**PTE 有效性看 `pte&3` 和 `pa`**。pn=0 且 pte 有效表示 PA 后面没有 kern 页（对 GEM 内存正常，不算异常）。

## 关键事实速查

- 死点硬算术：rem=2871，flatten nl=3095 → 3095−2871=224（ib2 内偏移）。
- v234：ib2=0x56162000/305，10 周期，DST 序列 0x56976000→0x5697f000（+0x1000/周期），死亡周期 DST=0x5697c000（v234.txt 行 14246-14344 walk、行 14315 DST 值、行 14238 ROQ 尾=内存 ib2[224..227]）。
- v233b：8 周期 DST 0x55d50000→0x55d57000，死亡周期 DST=0x55d54000（v233b.txt 74153-74216）；两版本死亡时都剩 4 个周期。
- 包格式：type7=(7<<28)|cnt|parity(cnt)<<15|(op&0x7f)<<16|parity(op)<<23（tree 1882-1886）；CP_BLIT=0x2c（tree 1299）；CP_WAIT_FOR_IDLE=0x26；type4=(4<<28)|cnt|parity(cnt)<<7|reg<<8|parity(reg)<<27（tree 1891-1902）。
- scratch2=上一发 fence 证明 fence write 未执行（v234.txt 13491）；rptr=6770>wptr=6606 读零区是 ME 停滞伴生行为。
- SMMU cb1 fsr=0x400 far=0 是 SMMU setup 阶段陈旧值（v234.txt 14346/1915/1923；NOTES.md 行 205），不构成页映射正证或反证。
- CP_HW_FAULT(0x821) bits 20/21 无公开位表（mesa a6xx.xml.h 行 1104 无位域），离线归因已放弃。
- ROQ[1] 单 dword 与内存不一致（0x56999000 vs 0x5697d000）成因未明，次要。

## 工具坑

- adb 永久用 `adb -P 5038`（5037 被死进程孤儿 socket 占用）。
- PowerShell 5：无 `head`、无 `&&`。
- sweetlog 解码后的 txt 含 `\0`，查日志一律用 python 读文件过滤。
- gh API 调用前清 HTTPS_PROXY/HTTP_PROXY（`_put0268.py` 的 ENV 处理模式）。
- 全流程判读后的下一版命名：v236（0269）。

## v237 verdict（2026-11-21）

- v237 = 0268+0269+0270，cmdline 596（v232 + `gauguinflatbr=0x80008`），boot OK，submit OK 67 / HUNG 1（fence=0x6000115）。
- 0270 外层扫描：282 条 `0270 br outer` 行。死流 iova=0x55556000 n=3095 **hits=1 stop=3095**（全流干净走完无早退，最大 hits=2）。0269 depth-0 hits 285（同 v236 小流）。ROQ 尾与 v235/v236 逐位相同。
- **hits=1 推翻原判据**（预期≈10）：改写从未到达 ME 执行的流，面积无关性仍未验证。

### 拓扑裁决（决定性，v237.txt）

blit 流是**嵌套 IB**，0270 平铺扫描从未进入：

- stuck 快照（行 13728/13770/13773）：`ib1=0x55556000/0xb37(2871) ib2=0x55546000/0x131(305)`。
- ib2 dump（行 14458+，地址 0x55546000）= 9 周期 blit 流：type4 reg 0xb4c0 count 5，TL=0x500003 BR=0x200040（payload kx=1 即 0xb4c1），DST 0x55d6e000..0x55d76000（+0x1000/周期），与 0268 九条打印逐位对应。
- 几何：ib1 展平 3095，ME 消费 224（3095−224=2871）。展平 offset 224 = at=32 展开块（tgt=0x55566000 n=58）起点——0x55566000 内嵌 CP_INDIRECT_BUFFER(0x55546000/305)，0261 的 depth==0 门控不展开二层目标，ME 执行到此处压栈跳入 ib2，死在 ib2+224 CP_BLIT(SCALE)（cyc=6）。
- 旧算术"rem 2871 = flatten 3095 − 224"数值相同、语义修正：2871 同时是 CP_IB1 latch 值，ME 实际位置在 ib2 内。

### 0271（已推送，3993 B）

- 外层扫描遇 type7 op 0x3f（CP_INDIRECT_BUFFER，payload lo/hi/size）经 gg_san_map 读 payload，递归扫目标流一层，原地改写 0xb4c1，按页 flush_dcache64（嵌套目标不在外层 fixed flush 范围内）。打印 `0271 br nest iova= n= hits=`。
- BASE=out/v270/V/bsd/dev/gauguin_gpu_post270.c。注意：gg_flat_rd 定义在扫描块之后（6188 行）不可前向引用，payload 读取用 san_map 直读。apply-check OK，PUT+roundtrip OK。

### v238 verdict（2026-11-21）：null result

- CI：xnu=44863601657、shim=44864668349、artifact=146914068。镜像 out/v238/shim/out/shim-boot.img（cmdline 596 校验过）。日志 out/sweetlog-v238/v238.txt（15077 行）。
- submit OK 67 / HUNG 0 / panic 0 / boot-args 正常。0269 armed 1、hits 289；0270 外层 285 条全小流（n=326~751，hits=1~2）。
- **0271 br nest=0、0268 blit=0、0266 regs parse=0、0267 sub=0**。死流 0x55556000 与嵌套 blit IB 0x55546000 全程 0 次出现；fence 0x6000115 经 ib1=0x5041e000 words=229 正常完成（行 13374/13375）；backboardd 越过旧挂点持续渲染。0268 的 37 条匹配全是 WFI hdr 含子串 "0268" 的假阳性。
- **结论**：v233b→v237 五轮逐位相同的挂点本次首次未复现，0271 从未获得触发机会，真实效果未测得。两解：负载抖动，或 0271 代码存在（每包多一次条件判断）引起时序漂移。
- 复现策略：重刷同一 v238 镜像采样。挂点复现 → 按原判据出 0271 真实 verdict（0271 br nest 应命中 iova=0x55546000 n=305 hits≈9）；连续两次不复现 → 挂点对 0271 代码存在敏感，做对照实验（回退 0271 或多次采样）。

### v238 复现样本 verdict（2026-11-21，repro1）：挂点复现，0271 不适用

- 同一镜像第二次 boot 挂点复现。日志 out/sweetlog-v238/v238-repro1.txt（15071 行）；v238.txt 已被本次覆盖，第一样本原始日志丢失（统计存上一节）。
- **同一 SQE 微码楔死点**：sqe_pc=0x4:0x2883003f 与 v237 逐位相同；sqe=0x1、ccu=0x10000004、ncmode=0x2、crash=0、smmu_stalled=0、gx_reset=0 全同；rbbm=0x800005、roq avail=0x70002 同。stat/status1/sqe_stat per-boot 不同（0x74006d/0x8100430e/0x5:0 vs v237 0x780078/0x810077de/0x5:0x4，随流内容波动）；健康值 status1=0x1000000。
- **拓扑 per-boot 不同**：本 boot 原始流 926 dwords@0x50426000（无 0x55556000/0x55546000），0261 两轮内联 5 个嵌套 IB：at=225 tgt=0x5042e000 n=178、at=32 tgt=0x50436000 n=58、at=11 tgt=0x5041f600 n=196、at=32 tgt=0x5555a000 n=58、at=11 tgt=0x5552b600 n=196；flat len=926=原始大小，ring size 补丁 no-op（ring dump size=0x39e=926）。
- **0269 收缩在执行流中生效仍楔死**：flat offset 232 = type4 reg 0xb4c0 count5 payload=[0,0x80008,0,0,0]（BR=0x80008），DST 0x55d76000@250。ME prefetch consumed=480（rem 926→446）；raw dump 仅到 259，CP_BLIT 包在 256-480 未 dump 区。blit config variant 也与 v237 不同（TL=0 vs TL=0x500003）。
- **0271 nest=0 是正确行为**：执行时已无嵌套 IB，递归无对象。0270 外层扫描 iova=0x50426000 n=926 hits=0 **stop=225**——walker 在 225 撞 0x10f00380（type0 overrun 早退）；0268/0267 walk 同死于 225（诊断走读缺陷，未修，不影响主线）。
- **CP_BLIT 编码定案**（v237 ib2 dump）：hdr 0x702c0001 = type7 op 0x2c count 1，payload 0x3（SCALE）；紧随 type4 reg 0xb4c0 count 5 config（payload [0,TL,BR,SRC,0]）。WFI hdr=0x70268000（type7 op 0x26 count 0，既有分析标注）。
- **判据落点**：收缩生效仍死 + 同一 sqe_pc → 面积无关成立、per-blit 2D 引擎状态/同步缺失成立 → **0272：变长 CP_WAIT_FOR_IDLE 插入**——在 spliced 流 depth-0 post-splice 扫描（0269 同位）遇 type7 op 0x2c 时在其前插入 0x70268000 一 dword，len++（flatmk 框架本就支持动态 len，repro1 已证 926 动态生成；ring size 由 0261 尾部补丁覆盖新 len）；0271 嵌套路径同样插入后再 flush。boot arg gauguinwfi。
- v238-first（无挂）vs repro1（挂）同一二进制：挂点触发为运行时非确定（时序/负载调度）；0271 对触发概率影响未定（0271 时代 1/2 vs 无 0271 时代 5/5，样本不足）。

### v239 verdict（2026-11-21）：per-blit 同步缺失否证，楔死=引擎/微码状态本身

- CI：xnu=44876120249、shim=44877379672、artifact=146969059。镜像 out/v239/shim/out/shim-boot.img（cmdline 606：`gauguinflatnop=56 gauguinflatbr=524296 gauguinwfi=1`，逐字节校验过）。日志 out/sweetlog-v239/v239.txt（15077 行）。
- 统计：submit OK 67 / HUNG 1 / panic 0。0269 armed 1、hits 285；0270 outer 282；**0271 br nest=1（iova=0x55552000 n=1905 hits=64）**；**0272 wfi armed=1、外层 at=290 条、nest=1（iova=0x55552000 n=1905 ins=64）**——0271 嵌套路径首次真实触发，BR 重写与 WFI 插入一一对应，父流 size dword 已改写（nsz 1905 含插入）。
- **WFI 已在 ME 消费的流里**：hung 后 ib2 dump（0x55552000）行 56 可见 `0x70268000(WFI) 0x702c0001(CP_BLIT) 0x3` 背靠背——插入点语义正确。
- **楔死点逐位复现**：行 14324 `submit state HUNG fence=0x6000115`；行 14374 `stuck2 ib1=0x55562000 rem=2872 ib2=0x55552000 rem=369 ccu=0x10000004 ncmode=0x2 sqe=0x1 crash=0 status1=0x810077de`；行 14378 `sqe_stat=0x5:0x4 sqe_pc=0x4:0x2883003f`——与 v237/v238-repro1 完全一致；crash/smmu_stalled/gx_reset=0、rbbm=0 同。ME 已穿越 1905−369=1536 dwords（大量 WFI→BLIT 对）仍死于同一 PC。
- **拓扑 per-boot 漂移继续**：本 boot ib1=0x55562000/2872 rem、嵌套 ib2=0x55552000/1905（v237 为 0x55556000/3095+0x55546000/305，v238-repro1 为 926 flat 无嵌套）——三 boot 三布局，同一死点。
- **结论**：0272 判据第一分支成立——每个 CP_BLIT 前置 CP_WAIT_FOR_IDLE 后死点仍逐位相同，"blit 间同步缺失"否证。楔死对面积（0269）、blit 间隔（0272）均免疫 → 死因在 CP_BLIT 微码路径内部状态（0x4:0x2883003f 处指令读取的寄存器/内存/引擎内部状态），或该微码路径硬件 erratum。
- 下一步候选（未预注册）：①0x4 区 ucode dump 围绕 0x2883003f 反汇编，定位该 PC 读什么；②挂点时刻抓 2D 引擎寄存器组（GRAS/SP/UCHE/纹理状态）跨 boot 对照；③改写 CP_BLIT 载荷/换 NOP 看死点是否移动（参数敏感 vs CP_BLIT 本身）。

### v240 verdict（2026-11-21）：blit 执行无关性实锤，"sqe_pc"被证伪为静态回读

- CI：xnu=44909809766、shim=44910866173、artifact=147094036。0273 patch（4671 B 前的 0273-nop-blit）经 _put0273.py requests 直连推送。镜像 out/v240/shim/out/shim-boot.img（cmdline 606：`gauguinflatnop=56 gauguinflatbr=524296 gauguinnop=1`，wfi 已删）。日志 out/sweetlog-v240/v240.txt（14979 行）。
- **0273 置换全量生效**：armed=1；外层 282 处（nop iova 报告）+ 嵌套 64 处（nop nest iova=0x55552000 n=1905 nop=64）——CP_BLIT hdr 全部被置换为 count-1 CP_NOP 0x70100001（吞掉 0x3 payload，长度不变，type4 0xb4c0 config 留原处由 0269 继续改写 BR）。**blit 根本未执行，ROQ 尾部就是 NOP 序列**。
- **楔死点仍逐位相同**：`sqe_pc=0x4:0x2883003f`、`sqe_stat=0x5:0x4`、`status1=0x810077de` 与 v237/v239 完全一致；rbbm=0x800005 [CP_AHB_CX|CP|GPU_BUSY]（ME 卡在 AHB/CX 事务）、rptr=wptr（ring 已被 ME 读空）、ROQ prefetch 正常推进。
- **结论**：面积（0269）、blit 间隔（0272）、blit 执行本身（0273）三项全部免疫 → 死因在流级/上下文状态或微码路径，与 CP_BLIT 是否执行无关。
- **"sqe_pc" 证伪（决定性发现）**：pre-state diag（post273.c 行 2164-2173）写 `CP_SQE_UCODE_DBG_ADDR/CP_SQE_STAT_ADDR` 0..3 并读 4 次 DATA；dbg 端口在 DATA 读后**自动递增**，故每挂点打印重放 ADDR=4、DATA=IMEM[4]/bank[4]——**静态回读，不是 PC**。数学自洽：v237/v239/v240 三轮 + v131-133 时代全部逐位一致，只因它是静态值。挂死点的真实 ME PC 从未被观测过。
- **fw 实锤**：a630_sqe.fw（sweet vendor 唯一 SQE 固件，8034 dwords，out/sqefw/a630_sqe.fw）中字面 0x2883003f 唯一命中字 261（0x105）；IMEM[4]=fw[4+257]=fw[261] → IMEM[0]≈fw[257]，fw 前 257 dword 为 header/前置段（fw[0]=0、fw[1]=0x016ee19a、fw[2]=0x01001ee1、fw[3]=0x01190001、fw[4]=0x88020001 起似指令流）。hi16=0x2883 另有字 5925（上下文 a0444004 指令流）。
- **0280（已推送 4671 B）**：挂点时刻先回读 UCODE_DBG/STAT 端口（验证 auto-inc 遗留论：期望 ADDR=5，因 hang regs 已读过 DATA@4），再 dump SQE IMEM 全镜像 8192 dwords（逐字写 ADDR+读 DATA，对 auto-inc 免疫）+ DMEM 探测（bit16 选择，1024 dwords）。boot arg gauguinimem=1；cmdline 删 flatnop/flatbr/nop（-52）回原始流，加 imem（+14）→ 568 ≤607。与 a630_sqe.fw 逐字比对建立 DBG_ADDR→fw 字映射并验证 ME 实际加载内容。
- v241 链已挂后台（_v241chain.py）；boot_v241.py 就绪（verdict 计数：0280 armed/readback/imem 2048 行/dmem 256 行/done/HUNG/OK）。

## v241 verdict（2026-11-22）：0280 dump 到手，挂点提前至 fence=0x6000003

- 镜像 out/v241/shim/out/shim-boot.img（cmdline 568：删 flatnop/flatbr/nop 回原始流 + gauguinimem=1，行 232 boot-args 逐项核对）。日志 out/sweetlog-v241/v241.txt：0280 armed 行 8485 → submit state HUNG fence=0x6000003 行 8607 → 0280 imem dump done 行 10950（imem 2048 行×4=8192 dwords 全镜像 + dmem 256 行×4=1024 dwords）。submit OK 1（fence=0x6000002）/ HUNG 1（fence=0x6000003）——挂点大幅提前（v237-v240 时代 HUNG fence=0x6000115），再次印证挂点触发为运行时非确定。
- 挂死形态本轮变体：rptr=wptr=1834（ring 被 ME 读空）、ib1=0x5046e000 rem=0、ib2=0/0、fault=0x40000 int=0x4、status1=0x8100032e、sqe_stat=0x5:0x1fbf0180（动态值，随 boot 波动）；不变项 ccu=0x10000004、sqe=0x1、crash=0、smmu_stalled=0、gx_reset=0、rbbm 全 0。ME 消费完整个 ring 但 fence 未写，仍属 ME 停滞族。
- **0280 readback 命中预注册判据（auto-inc 论运行时实锤）**：hang regs 读过 DATA@4 后，0280 回读打印 `0280 readback ucode=0x5:0xcc860065 stat=0x6:0x2`——ucode 端口 ADDR=5（=4+1，自动递增）、stat 端口 ADDR=6，与"旧 sqe_pc=0x2883003f 是 DATA 读后自动递增造成的静态回读"完全一致。IMEM[5]=0xcc860065。
- **数组真身实证**：内核嵌入数组（xnu/patches/0099-gauguin-gpu-cp-memory.patch，8066 dwords）在 shim-boot.img 内偏移 0x91d20 处 8066/8066 逐字命中。v007 轮线性窗口读回=完整 fw（存储侧）与 v241 dbg 端口 dump=icache 当前缓存（缓存侧）两路径不矛盾：一个是存储镜像、一个是 ME 已取指的行集合。

## dbg 端口 0280 结构定案（t11 closed，128 slot 全归账）

SQE icache = 4 way × 32 set × 8 word/line；dbg 地址 addr[12] 选阵列：

- **数据阵列**（addr[12]=0，有效 1024 字）：dump 字 p = 256*bank + 8*set + w；bank=addr[9:8]、set=addr[7:3]、w=addr[2:0]；addr[11:10] 悬空 → imem[0x400..0xfff] 为 bank0 页重复。读全四 bank 用 `data=[imem[i] for i in range(1024)]`。
- **tag 阵列**（addr[12]=1，128 项）：entry e = 4*set + bank（set-major）；addr[9:7] 悬空 → 128 项周期重复（imem[4096+i]，仅 i<128 有效）。
- **tag 位域（定案）**：bits[4:0]=块号 T（实测 ≤14，bit4 从未置位）；bits[21]=filled；bits[20]=取指空间（0=ME/arr 流，1=第二流）；bits[19:18]=2-bit epoch（per-space 填充轮转，使同 set 各 live way 的 n 互异）；bits[23:22]、bits[17:5] 恒 0。
- **行验证公式（91 行全对齐）**：`data[256*b+8*s+j] == arr[8*s+256*T+1+j]`（存储偏移 s ↔ arr[s+1]，固定 +1 字偏斜：fw[0] 不在 PC 空间）。
- **128 slot 归账**：91 ME 对齐 OK + 12 BAD（11 纯第二流 + 1 撕裂 set29b1）+ 25 invalidated（filled=0；其中 4 个残留恰为本 set block0 ME 行：set21b0/set22b0/set24b0/set27b2 = arr[169..176]/[177..184]/[193..200]/[217..224]，15 个残留第二流、6 个 n=0 全零未填充）= 128 ✓。ME 流 fills 95（91 live + 4 残留）vs 第二流 fills 26（11 live + 15 残留）。
- **第二流（t12 未决）**：外来 qrisc 镜像，n∈{c..f}，与 arr 零 4-gram 重合，指令全部合法（`mov $addr,常量`、`call #01cf`、`ret`、`brne $regdata,0,#fe93`）。寄存器常量集：0x88d0/88d4/88d5/88d6/88d7/88d8/88e3、0xbb00/bb01/bb02、0x9840/9841/9842/9880、0xd600/d601、0x00e7、0x0081、0xa83b/a863/b801/b802/a823/b9a0/b9a3。风格为寄存器清零/初始化序列，fills 少（26 vs 95）。身份候选：SQE 自身 boot/初始化所在第二取指空间（ROM 或微码另段）。set29b1 撕裂槽：w0=9883f806 已被第二流覆盖，w1..w7=arr[3591..3597]（4 个 4-gram 首尾相接=真 run）；arr[489]=c9220007≠w0 否定"新行首字"假说 → 其 VA↔存储映射与 arr 网格存在恒定位移（跨 set 行界取整行）。
- **DUP-TAG**（set 0/1/23/25 同 set 两 way 同 T）：分属不同 space+epoch，无冲突，模型自洽。
- **取指闭合率**：缓存行上 175 个分支位点 → 130 目标已缓存 / 45 未缓存（未执行分支路径；样本 (261,breq,362)、(281,breq,328)、(436,brne,3320) 等全部核实不在 spans 内）。缓存覆盖 25 spans 792 字：256..327, 384..495, 560..567, 616..647, 656..679, 696..703, 728..751, 848..887, 1296..1359, 1864..1895, 2200..2215, 2288..2311, 2920..2943, 2952..2967, 3096..3143, 3152..3183, 3192..3215, 3240..3247, 3272..3303, 3328..3351, 3440..3495, 3504..3519, 3560..3567, 3592..3615, 3640..3663——与 BOOT 足迹自洽（ME 取指仍停留在 boot/早期段，与挂点提前一致）。
- **qrisc 分支语义**（mesa src/freedreno/qrisc）：jump/breq/brne/blt/bgt/ble/bge（含 b 后缀=反向）为相对偏移 target=(pc+off)&0xffff；call/bl 为绝对 literal；反汇编串尾 `#xxxx` 即偏移/目标。
- **工具链（out\\）**：_qriscdis.py（isaspec XML→Python 反汇编器，复用方法=exec 其 `# ---- data ----` 前缀段取 disasm_word/arr）；_qriscdis6（128 slot 终验+stale 溯源+n 表+spans）、_qriscdis7（BAD 同 set 旧行溯源全 NONE、inv 旧 T2、DUP-TAG、闭合率）、_qriscdis8（37 非 OK slot 逐词打印+第二流判读）、_qriscdis9（撕裂槽假说检验）、_v241sum.py（verdict 关键行提取）。

## 第二流身份判读（t12，离线证据链闭环）

- **寄存器映射（mesa main a6xx.xml，temp/a6xx_main.xml）**：第二流写的 0x88d0/88d4/88d5/88d6/88d7/88d8/88e3 = **RB_RESOLVE_CNTL_0/1/2、RB_RESOLVE_WINDOW_OFFSET、RB_RESOLVE_GMEM_BUFFER_INFO/BASE、RB_RESOLVE_SYSTEM_BUFFER_INFO/BASE、RB_RESOLVE_OPERATION**；0x9840/0x9841/0x9842/0x9880 = **PC_DRAW_INITIATOR/PC_KERNEL_INITIATOR/PC_EVENT_INITIATOR/PC_MARKER**；0xbb00/0xbb01/0xbb02 = **SP_DRAW_INITIATOR/SP_KERNEL_INITIATOR/SP_EVENT_INITIATOR**；0xd600/0xd601 = **CP_EVENT_START/END**。整个常量集就是 GMEM save/restore + draw/kernel/event 发起序列——上下文 restore 血统。
- **非 IMEM 存储内容实锤（_s2probe.py）**：12 条 BAD 行的 4-gram 与 arr、a630_sqe.fw 均 0 命中；签名序列（mov $addr,0x88d0/0x88d4/0x88d8/0x88e3、call #01cf、撕裂字 9883f806）在两库均 0 命中 → 第二流代码不在 IMEM 存储（v007 已证存储=arr 全量），取指源为片上 ROM（最可能）。
- **同源异构建证据**：零 4-gram 与 set29b1 撕裂线 7 连字（w1..w7=arr[3591..3597]）并存 → ROM restore 段与 fw restore 段同源不同构建（共享大量代码/字面段，个别词被 ROM 版本改写）；arr 内大量同类寄存器写入（mov $addr,0xd600/0x9842/0x9880/0xbb02 多处命中）证明 ME fw 自身也有 restore 路径，但与 ROM 版实现不同。
- **arr ↔ a630_sqe.fw 关系（_arrfwdiff.py）**：8066 vs 8034，前 8034 字逐位对齐比较：7694 字相同 + 346 处单字散差（同源不同构建，厂商寄存器/常量适配 sweet 硬件），0 插入；arr[8034..8065] 为厂商附加 32 字段表元数据（0xc4/0x376/0xe3e 等小偏移值，非代码，_arrtail.py）。
- **时序**：第二流 epoch n∈{c..f} 严格晚于 ME boot 填充 n∈{8..b} → restore 路径在 boot 之后、随 submit/上下文切换触发。
- **结论**：第二流 = SQE 片上 ROM 的 restore/初始化路径，运行于独立取指空间（tag bit20=1），与 ME fw 同血统、非存储内容。
- **设备侧预注册验证（留待后续轮，v242 候选）**：① boot 后 0 submit 即 dump：space-1（bit20=1）行应为 0/极少；N 次 submit 后 dump：space-1 行数随 restore 次数增长；② dbg 地址 addr[15:13]≠0 窗口（0x2000+ 页）探测 ROM 内容，与 BAD 行数据比对。

## 第二流程序本体（t12 终局定案，2026-11-22 晨）

三个决定性结果闭环：

1. **DMEM 幻觉排除**（out\_dmcmp.py）：`dmem[a] == icache 数据阵列 data[a]` **1024/1024 全等**——DMEM 窗口是 dump 路径对数据阵列的别名读，非独立存储。昨晚"DMEM 常驻程序"结论作废（见下节）。
2. **PC 空间重建**（out\_s2flow.py）：tag bits[4:0]=T 对 space-1 行同样是 **PC[12:8] 256-word 页号**（与 ME 行 t11 定案同构）→ line PC = `256*T + 8*s .. +7`。检验：space-1 行内 17 条分支/call 目标 **15 条精确命中**另一个 space-1 槽（T、set 双对齐；随机关联概率 ~2e-9）；2 条 MISS（T0set7、T1set18）为目标行未驻留 icache，正常缺位。
3. **程序清单**（out\_s2prog.py → out\_s2prog.txt）：27 条驻留行（页 0：15 行 / 页 1：12 行）按 PC 拼装完整反汇编。

**程序语义 = SQE 片上 ROM 的上下文 restore / GMEM resolve 路径**：

- 清零状态 RAM：`mov $rem,0x400` + `(rep)mov $data,0`（or flag 0x4000）flood **HLSQ_LOAD_STATE_GEOM_CMD(0xb820)** 与 **HLSQ_LOAD_STATE_FRAG_CMD(0xb9a0)** 各 1024 项零（0x4080 为下一基址）。
- disable 全部 shader stage：**SP_VS_CNTL_0(0xa800)、SP_VS_CONFIG(0xa823)、SP_HS_CONFIG(0xa83b)、SP_DS_CONFIG(0xa863)** 写 0。
- resolve 配置：RB_RESOLVE_CNTL_0(0x88d0)、WINDOW_OFFSET(0x88d4)、GMEM_BUFFER_INFO(0x88d5)、SYSTEM_BUFFER_INFO(0x88d7，写 0x4100)、GMEM/SYSTEM_BUFFER_BASE(0x88d6/0x88d8) → **RB_RESOLVE_OPERATION(0x88e3)=0xf2** 触发。
- event 发起：CP_EVENT_START(0xd600) → PC_EVENT_INITIATOR(0x9842)/SP_EVENT_INITIATOR(0xbb02) event 0x1e(30) → PC_MARKER(0x9880, 0x0800) → CP_EVENT_END(0xd601)。
- kernel 发起：PC_KERNEL_INITIATOR(0x9841)/SP_KERNEL_INITIATOR(0xbb01)。
- 子程序：**#01cf**（构造 (val|0x05) 写 PC/SP_EVENT_INITIATOR + PC_MARKER + CP_EVENT_END，5 处调用）；#01de（event 发起包装，3 处）；#01eb（`cread $05,[$00+0x38]` + `brne $05,0,#0000` **自旋等待完成**）；#01c3（kernel 发起）。本地窗口轮询：[$00+0x38]、[$00+0x69]&0x30、[$00+0x8b] bit31。
- 未解：0x00e7（CP 私有寄存器，event 语境，mesa 无定义）。

**结论**：第二流 = sweet SQE 片上 ROM 的 restore/resolve 固件，独立取指空间（tag bit20=1），与 mesa 替换的 IMEM fw 同血统不同构建；mesa 占据 IMEM 后 ROM 路径仍随 submit/上下文切换执行（epoch c..f，页 0/1 足迹 PC 0x020..0x10f、0x198..0x1ef）。

**与 wedge 主线关联假说（v242 验证）**：ROM restore 路径与 mesa ME 自身 restore 路径并存，若 ROM 路径在 mesa 管理的状态上执行（HLSQ RAM flood 清零 / SP stage disable / GMEM resolve），可能破坏 mesa 调度一致性。v242 预注册维持：0-submit dump 看 space-1 缺位；submit 数增长对照；addr[15:13] 其他窗口需别名判别（读数==data 同址读数→别名，≠→真存储；_dmcmp.py 模式）。

## ⚠ 关机暂停点 → 已定案修正（2026-11-22 晨，_dmcmp.py 决定性反证）

**昨晚"DMEM=第二流程序"结论错误，已推翻**。`python out\_dmcmp.py`：`dmem[a] == imem 数据阵列 data[a]` **1024/1024 全等（diff=0）**——所谓 DMEM 窗口是 dump 路径对 icache 数据阵列的别名读（addr[15:13] 不同窗口返回同一阵列），不是独立存储。

连带作废：
- 映射公式 `line(s,b) ← DMEM[256b+8s..+7]` 作废——_s2dis.py 的 full=122/partial=0 是平凡真理（cache 读数恒等于"dmem"读数），不构成任何来源证据。
- 昨晚 1024 字"全量反汇编"（out\_s2dis_out.txt）无单一程序语义——PC0..1023 线性读出是数据阵列跨槽混合（ME 行 + space-1 行），PC 连续性跨 bank/set 打断；PC0..5（9803f806/d8000000/981f0806/2883003f/cc860065）来自 slot(s0,b0) tag=0x00280001 的 **ME 行（T=1，arr[257..264]）**，不是第二流入口。

第二流身份判定回到 t12 原结论（上一节）：内容不在 arr（8066 全量）、不在 a630_sqe.fw、不在"DMEM"（别名读排除）→ **片上 ROM/第三存储**仍是唯一存活假说；RB_RESOLVE_* restore 语义序列（0x88d0/88d4/88d8/88e3、call #01cf/#0c2a、0xd40001cf、9883f806）在 space-1 行中真实存在，结论不变。set29b1 撕裂机理改判：w0=9883f806 是 space-1 行自身内容（数据阵列该物理位置最后写入者），w1..w7=arr[3591..3597] 是**旧 ME 行残留**——同一物理槽先 ME 行后 space-1 行覆写中断（或反序撕裂），与 DMEM 无关。

**设备侧预注册验证（v242 候选，维持原两条）**：① boot 后 0 submit 即 dump：space-1（bit20=1）行应为 0/极少；N 次 submit 后 dump：space-1 行数随 restore 次数增长；② dbg 地址 addr[15:13]≠0 窗口（0x2000+ 页）探测——注意 _dmcmp.py 已证 0x0..0xfff 范围 dmem 窗口==数据阵列别名，其他窗口可能同样别名，探测时需预注册"若读数==data 同址读数则别名、若不同则真存储"的判别标准。

副产物：out\_s2dis_out.txt 中 `mov $addr` 常量直方图与 call/bl 目标列表来自混合读出，解读价值有限，仅 space-1 单行反汇编（_qriscdis8_out.txt 等）可信。

## v242+v243 verdict（2026-11-22）：ROM restore 触发时机 = boot-time load（定案）

三轮设备验证闭环（patch 链：0281-gauguin-sqe-dbg-boot-dump → 0282-gauguin-sqe-ph01-selfparse，均已在 repo main）：

- **0281 设计**：`gg_sqe_dump(int ph)` 函数化（ph=0/1/2 三点共用一份 dump 体：readback 4 寄存器 + IMEM 8192 dword + DMEM select bit16 1024 dword + done 行，每段 sweet_log_flush_now）。ph0/ph1 触发块插 submit 入口检查之后（ph0=本 submit 任何 ring 写之前；ph1=submit 0 完成后），ph2 保留 0280 原点位（挂死后 gg_rbbm_busy() 之后）。DMEM select 写法：addr|0x10000（bit16）选 DMEM 别名窗口，其余同 0280。
- **v242 结果**：ph0/ph1 未触发、ph2 完整。根因：ph0/ph1 触发条件 `gg_imem_opt >= 0` 依赖 gg_ib_sanitize 的 armed parse 点，而实测 armed 发生在 submit #1 **之后**、submit #2 路径内部（v242.txt 行 1910 submit OK → 行 8480 armed → 行 8602 HUNG）——触发块在 armed 之前执行时 gg_imem_opt 仍为 -2。**0282 修复**：触发块自 parse `gauguinimem`（static 局部 + -2 哨兵），不再依赖 armed 时序；armed 打印与 ph2 逻辑不变。附带实证：ph0 的 2313 行大 dump 在 submit #1 入口执行后，该 submit 仍 OK 完成（fence=0x6000002）——大 dump 不破坏 submit 路径。
- **v243 三点数据（预注册判据命中）**：ph0 filled=108/**space1=43**/inv=20，ph1 111/39/17，ph2 122/27/6。ph0（ME 未见过任何 ring 工作）space-1=43>0 → **VERDICT: boot-time load**——SQE ROM restore/resolve 固件在 CP 初始化阶段已被 ME 取指驻留，与 submit 无关。
- **演化细节**：43→39→27 单调递减 = 第一流（space-0）取指逐出第二流行（ph0 比 ph2 多出的全是 bank1/T0 行 s0..s23）；epoch 2/3 各恒满 32 slots（8 sets×4 banks，每 bank 一路固定归第二流替换结构）；ph2 与 v241/v242 基线逐位一致（三轮 boot 可复现）。DMEM 别名窗口三点 dump 内容与数据阵列同构（v241 _dmcmp.py 结论的动态重证）。
- **wedge 主线定位更新**：ROM restore 固件与挂死无因果（boot 期执行完毕，挂死点活跃代码为第一流 95 行 space-0）；v243 挂死形态与 v241 同族（submit OK fence=0x6000002 + HUNG fence=0x6000003，walk hung 序列含 UCHE_CLIENT_PF(0xe19) 写 val=0x4）。下一轮调查方向：第一流 walk 序列挂死点（walk hung dump 的 reg/type4 解码已有，需定位卡住的寄存器写）。
- **工具**：out\_v242cmp.py（三点统计 + 预注册判据自动判定，LOG 路径作 argv）、out\_v242cmp2.py（v241 vs v242-ph2 可复现性比对）、out\_mk0281.py（repo patch 链本地 apply 重建 + diff/roundtrip，prep/diff 两模式）、out\_mk0282.py（精简版 diff+roundtrip）、out\_put0282.py（contents API 上传）、out\_v242chain.py/_v243chain.py（CI dispatch + artifact 落盘）、boot_v242.py/boot_v243.py（phase1 清 ring+fastboot boot / phase2 拉日志+verdict 计数）。
- **坑**：本机 git core.autocrlf=true 会把 Write/Edit 产物写成 CRLF → git apply 上下文失配 + roundtrip 假阳性；所有 apply 端须 `-c core.autocrlf=false`，编辑后归一化 LF。PS5 下 `python -c` 内含单引号字符串必写脚本文件。CI push 自动 run 与 dispatch run 同 concurrency 组互斥，**取消方向不定**（v244 时代 push run 被取消、dispatch 存活；v245 时代 push run 反向取消 dispatch run 45019796484）→ PUT patch 后 dispatch 的 run 仍可能被取消，chain 必须核对所等 run 的 conclusion，cancelled 就重 dispatch 续跑（`out\_v245chain2.py` 模式：跳过 PUT、复用原 chain 的函数，从 dispatch xnu 起继续）。

## v244 verdict（2026-11-22）：ROQ=raw PM4（奇校验）定案；挂死引擎级定位 = CP_SET_DRAW_STATE，SDS 对象 0x50419900 rem=0x18

0283（已在 repo main，355518 B applied 链顶 = applied3.c）退役 ph0/ph1、ROQ dbg port（CP_ROQ_DBG_ADDR 0x90c / DATA 0x90d，与 SQE dbg port 0x908/0x910 同族）在挂死点读 144 dwords、walk dump limit 2→8。日志 out\sweetlog-v244\v244.txt（10867 行）。工具链：`out\_mk0283.py`（prep 重建 0085..0282 链 + diff/roundtrip）、`out\_mk0284.py`（同构，0085..0283）、`out\_put0284.py`、`out\_roqdec2.py`（144 项按 walk 规则流式解码）、`out\boot_v244.py`/`boot_v245.py`、`out\_v245chain.py`。

**ROQ 解码规则（定案，推翻此前全部位变换假设）**：ROQ dbg dump 的每个 dword 就是原始 PM4 流（无标记位、无字段重排、与内存格式一致），校验为**奇校验惯例**。BAD 条件 = `((popcount(field)&1) ^ parity_bit) == 0`（偶结果才 BAD）——此前把方向记反（以为 ==1 才 BAD）是"0x40bb0801 头不合法"假矛盾的根源。编码：type7（h>>28==7）op=(h>>16)&0x7f、cnt=h&0x7fff、校验位 bit23(op)/bit15(cnt)；type4（h>>28==4）cnt=h&0x7f、reg=(h>>8)&0x7ffff、校验位 bit27(reg)/bit7(cnt)；type0（h>>30==0）c0=((h>>16)&0x3fff)+1。**0x40bb0801 合法**（type4 reg=0xbb08=HLSQ_INVALIDATE_CMD cnt=1 val=0xfffff，"标准头应为 0xC0BB0800"假设废弃）。raw 日志全部 type4 头（0x48960001/0x400e1201/0x48810901 等）逐个验证通过。注意 gg_ib_walk 的 type4 解码 printf 在奇偶校验**之前**→ 出现 type4 行不代表校验通过。

**ROQ 窗口拓扑（144 dwords，v244.txt raw 行 10608 起）**：CP_ROQ_RB_STAT rptr=0x53=83=**下一个待执行 dword**、wptr=0x5c=92=下一个写入位置。d[63..82]=5×CP_INDIRECT_BUFFER_PFE（hdr 0x70bf8003，type7 op=0x3f cnt=3）：#1 0x5041a000/0x187、#2 0x50432000/0x146、#3 0x50442000/0x146、#4 0x50452000/0x187、#5 **0x5046a000/0x2ef（主 IB packet，751 dwords）**。d[83..89]=submit#3 fence 段（[83] type4 reg=0x885 val=0x6000003；[85] type7 op=0x46 cnt=4 payload [0x4,0x40020140,0x0,0x6000003] fence 写内存）——**全部未执行**（scratch2=上一发 fence 0x6000002 佐证）。d[90..91]=0 padding；d[92..143]=已消费旧 submit 残余（两轮 per-frame 寄存器序 8c18/8c19/8c1a/702c/7026/8405/8406/8c00…，值 0x4090f280/0x40910340/0x40911440 递增）。

**挂死点定案（引擎级）**：ME 消费到 rptr=83 → 第 5 IB packet 已弹出执行、IB1_REM=0（主 IB 已执行完），fence 段未执行 → ME 挂死在主 IB 末尾的 **CP_SET_DRAW_STATE 处理**：0210 csq 读数 **sds base=0x50419900 rem=0x18**（独立 GEM buffer，不在 5 个 IB 内；SDS 对象剩 24 dwords 未执行；mrq=0 ahb=1 pool=128，v244.txt 行 8142）。stuck2 sqe_pc=0x4:0x2883003f 为静态回读（v240 已证伪为真实 PC；IMEM[4]=arr[261]），与 v133 NOP 全部可疑 packet 后的挂死同点 → 命令流内容无关的固定停滞点。

**v244 主 IB walk 缺失原因**：4 MiB ring 写满截断——4 个 preamble IB（每轮逐位重复、各 ~59 行 dump）耗尽 ring 预算，主 IB gg_ib_all raw dump 在 hung 112 dwords 处截断（walk 未及主 IB）；pull 多出的 11067 字节 = dd 读取时 ring 仍在写。0191 stop 无输出属设计（源码 9834 行 rem==0u 时 continue）。

**0284（已推送 repo main，2923 B，roundtrip OK）**：① IB dump 循环头加 `if ((uint32_t)addresses[k] != stuck) continue;` 跳过非 STUCK IB 的 dwords/all/walk（省 ring 预算，主 IB walk 必然完整）；② IB 循环后读 GR(0x92e)/GR(0x930) 打 `0284 sds base= rem=`，base/rem 非零则 `gg_ib_all("sds", base, rem+32)` + `gg_ib_walk("sds", …)`（0x50419900&0xfff=0x900，4×56=224+2528≤4096 页界 OK）。cmdline 与 v244 逐字节相同（568 字符）。

**v245 判读标准**：① `walk hung end` 行 = 主 IB 0x5046a000 完整 walk 解码 → 取主 IB 末尾 CP_SET_DRAW_STATE packet（op 号以解码输出为准）的 count 与对象引用，对照 rem=0x18=24 定出执行停点（total−24 处）；② `walk sds` 解码 0x50419900 头 56 dwords → 定位剩 24 dwords 所属 packet → 确定 ME 停滞时的 SDS 对象内容。若 SDS 内容解码全部合法 → 挂死不在命令流内容本身，指向 CP_SET_DRAW_STATE 微码/引擎状态（与 sqe_pc 固定回读证据链一致），0285 转向引擎内部探针（SDS 相关寄存器快照或拆包重放实验）。

## v245 verdict（2026-11-22）：SDS rem=0x18 跨 boot 精确复现；0284 SDS dump 因排布丢输出，0285 前移

日志 out\sweetlog-v245\v245.txt（11175 行）。cmdline 568 与 v244 逐字节相同，boot OK，submit OK 1 / HUNG 1（fence=0x6000003，**挂死提前到第 3 个 submit**，v244 是第 21 个）。

**挂死形态与 v244 引擎级同构，per-boot 参数漂移**：STUCK `ib1=0x50476000/0 ib2=0/0`（IB1 size latch=0 vs v244 的 751）、stuck2 `ccu=0x10000004 ncmode=0x2 sqe=0x1 crash=0 status1=0x8100032e`、0210 csq **sds=0x50425900/0x18**（v244: 0x50419900/0x18）。两次独立 boot、不同流、不同地址、不同挂死时机，SDS 剩余量恒为 24 dwords → 挂死点定案强化：ME 在 CP_SET_DRAW_STATE 状态对象内部消费到剩 24 dwords 处固定停滞。

**0284 SDS dump 未捕获（0 条输出）的原因**：SDS 块排布在 `gg_ib_walk("hung")` **之后**；本 boot 的 hung dump 对象是 0x50476000/241（非 v244 的主 IB 0x5046a000），all dump 148 行完成后 walk 输出在 offset 35 半行处截断（4 MiB ring 耗尽，与 v244 preamble 同机制），SDS 块即便执行输出也全丢。

**v245 IB1 raw dump 解码（全 241 dwords 完整，行 11093-11155）**：head=CP_SET_MARKER(type7 op 0x63 cnt1 payload 0)+3×CP_EVENT_WRITE(type7 op 0x46 cnt1，event 0x19/0x18/0x31)+type4 寄存器写序（0xbb08=HLSQ_INVALIDATE_CMD val 0xfffff、0xae00/0x9600/0xab20/0xb182/0xbb11/0x0e12=1/0x0e19=4/0xa9a8/0x9107/0x8811 等，大量 NOP op 0x10 间隔）+3×op 0x55 cnt3 payload [0x5041e600,0,0x1000cf]/[0,0,0]/[0,0,0x200000]（0x5041e600 邻接 v244 的 fence IB 0x5041e000）+0x70460001 payload 0x26 + 尾部 type4 序（0x8c00/0x8400=0x10f00380、0xacc0=0xf018、0x8c01、0xb4c0 cnt5 全 0）+末包 type4 reg 0x8c2c cnt4 仅 2/4 payload（IB 边界截断，words=241 是 submit 声明尺寸，ME rem=0 已越过）。**IB1 内无 op 0x3e（CP_SET_DRAW_STATE）** → SDS packet 在主 ring 流（IB1 之前的 ring 段），与 v244 定位一致。

**0285（制作完成 2559 B，roundtrip 双向 OK；v246 chain 后台推进）**：把 0284 的 SDS 块（读 GR(0x92e)/GR(0x930) → `0285 sds base= rem=` → 非零则 `gg_ib_all/gg_ib_walk("sds", base, rem+32)`）移到 hung dump 循环**之前**——SDS 对象仅 rem+32≈56 dwords、all+walk 输出 ~3 KiB，ring 余量内必然完整。制作方法：edited3.c（0285 版）反向 Edit 得 edited4.c（0284 基线），`git diff --no-index` 字节级生成，避开 PowerShell 重定向 BOM；`out\_mk0285.py` roundtrip。chain `out\_v246chain.py`（**不 dispatch xnu**：PUT 0285 触发的 push run 直接复用为 xnu run id，规避 concurrency 互取消；之后 shim→artget v246→auto phase1 `out\boot_v246.py`）。verdict 统计 key=`0285 sds base`，新增 `all sds ib` 展示。

**v246 判读标准**：① `0285 sds base=` 应必现；② `walk sds` 解码 SDS 对象（rem+32≈56 dwords）→ 定位剩 24 dwords 所属 packet：type7/type4 包头合法但 ME 停滞 → SDS 引擎内部状态问题（0286 转引擎探针）；包内容本身异常（非法/罕见编码）→ 按内容定位。③ `walk hung end` 应恢复（SDS 前移后 ring 有余量，IB1 walk 完整输出）。

**新坑（rt5 实验定案）**：本机 git core.autocrlf=true 使 `git apply` 把目标文件全文重写为 CRLF（+行数×1 bytes，如 rt5 +10467），apply rc=0 且内容正确——roundtrip 字节比较前必须 `\r\n→\n` 归一化；CI Linux 无此现象，patch 本身 LF 不受影响。另：`git diff --no-index` 的 header 路径需重写为 `a/bsd/dev/...`（用 python 字节处理，勿走 PowerShell 重定向——PS5 `>` 默认 UTF-16 BOM 会污染 diff）。

## v246 verdict（2026-11-22）：SDS 对象首次捕获——内容完全合法，挂死定案为 ME/SDS 引擎内部状态

日志 out\sweetlog-v246\v246.txt（11188 行）。cmdline 568 与 v244/v245 逐字节相同，boot OK，HUNG fence=0x6000003。0285 SDS dump 首次成功：`0285 sds base=0x5041d900 rem=24`。

**SDS 对象内容定案（walk sds 56/56 无 BAD 包）**：12 条完全合法的 type4 SP/PC 寄存器写——0xbb08=0xff、0xb800=0x102、0xb801/2/3=0、0xbb10=0x100、0xa823=0x100、0xa83b=0、0xa863=0、0xa894=0、0xab04=0x100、0xab20=0。寄存器区归属：0xa800=SP_VS_CTRL_REG0 区、0xbb00=PC 区——典型 draw-state 组。dwords 24-55 全零（越界未用内存，无信息量）。rem=24 的语义二选一未决：ME 停在组起点（组全长未消费）vs 消费到 offset 32 后停在零区。

**引擎状态（三轮 v244/v245/v246 挂死完全同构）**：主 ring 完全排空 rptr==wptr==1834、CP_IB1_REM=0、ib2=0、ROQ 空（st roq=0x380038）、sqe=0x1、ccu/ncmode/crash/status1=0x8100032e 与 v244/v245 全同（仅 per-boot GEM 地址漂移）。**CP_HW_FAULT(0x821)=0x40000（bit18）+ CP_INT bit2（CP_HW_FAULT_ERROR，a6xx.xml.h 行 1074 附近）挂起**，protect=0。IB1 241 dwords 内无 op 0x3e（SDS packet 在主 ring 流）；v246 IB1 前 92 dwords 与 v245 逐位同构（CP_SET_MARKER op 0x63 + 3×CP_EVENT_WRITE 0x19/0x18/0x31 + type4 序）。ROQ trace 144 dwords：5 个嵌套 IB dispatch（0x5041e000/0x187 → 0x50436000/0x146 → 0x50446000/0x146 → 0x50456000/0x187 → 0x5046e000/0x2ef，与 v245 同大小不同地址）+ fence 0x6000003 写入（ROQ[83..84] type4 0x885=0x6000003）。0210 exec mark 框架存在本轮未触发。walk hung 仍被 ring 预算截断（前 92 dwords 后截断），无碍主线。

**结论**：ME 卡在 SDS 引擎内部于完全合法的数据上 → 0286 转引擎内部探针（只读寄存器窗口，零风险）。

## v247（0286 SDS 窗口探针）状态与判读标准

**0286（`xnu/patches/0286-gauguin-sds-window.patch`，1449 B，1 hunk，roundtrip 双向 OK）**：在 0210 csq printf 之后新增两个只读寄存器窗口 dump 循环——`0x900..0x94f` 步长 8、`0x980..0x98f` 步长 8，每行 `GAUGUIN CP: 0286 win %x:` + 内层 8 个 ` %x=%#x`（gg_rd(gg.gpu, GR(q))），共 14 行。目标：CP SQE/SDS 引擎内部 FIFO fill/credit/parked 状态。制作 `temp\mk0281\mk0286.py`（edited5.c=0285 基线 == main → edited3.c=+0286，mk0285 已验证模式：正斜杠 diff 路径 + header 逐行重写 + 全新 rt6 树 + LF 归一化 roundtrip）。

**v247 chain（`out\_v247chain.py`，后台推进）**：PUT 0286 → 等 PUT 触发的 push xnu run（10 min 窗口，success 上限 5400 s）→ dispatch shim（inputs xnu_run_id + cmdline）→ `out\_artget.py <sid> v247` → payload cmdline 校验 → `DONE (no boot - manual flash tomorrow)`。**用户指令：只构建不刷机，明天手动刷。**

**明早刷机流程**（`out\boot_v247.py`，TAG=v247，IMG=out\v247\shim\out\shim-boot.img）：

```
python out\boot_v247.py phase1     # 清 sweetlog 全 ring → fastboot boot → 300 s
（把手机弄回 OrangeFox recovery）
python out\boot_v247.py phase2     # 自动等 recovery（上限 2 h）→ 拉尾 1025 块 → verdict
```

cmdline 与 v244-v246 逐字节相同（568 字符）。verdict 新增 `0286 win` 计数与 `show('0286 win', 14)`，其余 key 同 v246。

**v247 判读标准**：① 14 行 `0286 win` 全部出现——0x900-0x94f/0x980-0x98f 各寄存器值对照 mesa a6xx CP 寄存器表解读 SDS/SQE 内部状态（FIFO fill、credit、prefetch 指针）；② 挂死形态同构性检查：sds rem=0x18、rptr==wptr、CP_HW_FAULT 0x40000、status1=0x8100032e 应与 v246 全同；③ 若窗口值在挂死与健康 boot 间无差异 → 探针位选错区，下一轮换 0x800/0x860 区或拆包重放实验（CP_SET_DRAW_STATE 拆成裸 type4 序重放）。
