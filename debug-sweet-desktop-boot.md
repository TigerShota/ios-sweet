# Debug Session: sweet-desktop-boot
- **Status**: [OPEN]
- **Device**: Redmi Note 10 Pro (sweet, M2007J17C / gauguinpro), iOS 16 shim boot
- **Evidence Directory**: D:\hackios\out\sweetlog-\<version>
- **Log File**: D:\hackios\out\sweetlog-\<version>\<version>.txt
- Full chronological handoffs: `NOTES.md`; Chinese session memory: `memory/2026-11-13.md`

## Reproduction Steps
1. Sync ios-sweet main, confirm latest version/build.
2. `python tools/gpu-build.py <xnu_run_id> <tag>` -> shim image with payload verification.
3. `fastboot boot` the image (`tools/gpu-boot.py`), wait 300 s.
4. Device returns to recovery; `python tools/gpu-pull-log.py <tag>` pulls the persistent sweetlog ring from userdata tail.

## Evidence Timeline (v139 - v146)
- **v139**: 67 submits retired, then 3 HUNG; first hard stop IB1 `0x55562000` len 2411 at offset 1360 (`rem=1051`). Offset 1359 is a parity-valid Type-4 `0x48810385` covering the five zero-payload GRAS_LRZ registers. Nested IB2 `0x5556a000/106` preloaded only (`CP_IB2_REM_SIZE=0`). `RBBM_STATUS=0x800005` (CP/AHB busy), no SMMU fault. Rejected: malformed Type-0, SMMU fault, nested IB2 as direct trigger.
- **v140/0191**: read-only parity-aware framing pinned the wedge to the zero-payload `GRAS_LRZ_BUFFER_BASE` Type-4 write at IB1 offset 1360. 285 submits, `src/panel px` 6/6 zero. Late `kmem_alloc` panic identified as a separate io_submap exhaustion.
- **v141/0192**: LRZ NOPs fired 8x but all landed on short IBs; `0191 stop` unchanged at 1360. Root cause: sanitize clipped the 2411-dword IB to 1024 dwords, so the LRZ hypothesis was never actually exercised.
- **v143** (0192+0193+0194+0195, `gauguinsk=1` skip ALL nested indirects): CP no longer wedges - 409 `submit cp done`, 0 HUNG/STUCK/panic, `0191 stop`=0. But `gauguinsk=1` also skips the draw IBs: 0 pixels, no logo/lock screen. Conclusion: the wedge was the amble/nested-IB entry, NOT the LRZ write.
- **v144** (`gauguinsk=0`): re-wedged at the pkt-11 196-dword PFE amble (8 submits, 3 HUNG, 8 STUCK) + new `pmap_remove_options() @pmap.c:4296` panic (independent failure mode).
- **v145/0197** (`gauguinsk=2` skip only first long indirect): wedge moved from pkt 11 to pkt 32 (`0x70bf8003 -> 0x5042e000`); `0191 next` shows plain type-4 state writes in the target. 8 submits, 1 panic (`pmap_remove_options`).
- **v146/0198** (`gauguinpfd=1 gauguinfclr=1`): PFD rewrite of recursed PFE headers + all-ones W1C fault clear. xnu `43372844107` + shim `43378042091` success; device run reported panic on screen; **persistent sweetlog not yet pulled** - no v146 evidence file exists yet.

## Structural Conclusions
- Three cmdline variants bracket the space: skip-all = 409 submits / 0 pixels; skip-none = wedge at pkt 11; skip-first = wedge at pkt 32. Any executed nested `CP_INDIRECT_BUFFER_PFE` (op 0x3f) wedges the CP regardless of target contents (v122 NOPed everything inside and it still wedged). Choosing which indirects to skip can never produce pixels.
- `CP_HW_FAULT` bit18 + `CP_INTERRUPT_STATUS` bit2 latch the moment the SQE is released and never clear (`0158 clr=4`: `int=0x4->0x4 fault=0x40000->0x40000` on 9/9 submits). Suspect SQE firmware / `CP_SQE_INSTR_BASE` release sequence rather than the command stream.
- The ME demonstrably fetches and executes a GEM command buffer as IB1 (GEM self-test memwrite/event PASS); only the NESTED (IB2) fetch path fails.

## Hypotheses & Verification
| ID | Hypothesis | Status |
|----|------------|--------|
| A | Nested IB target mapping/content/visibility fault | REJECTED - contents irrelevant, entering PFE itself is fatal |
| B | CP fetches nested IB but cache/coherency blocks execution | PARTIALLY OPEN - `0175 nomap`=0, TLB flushed per submit; uncached read-back probe (0196 rebuild, `temp/0196-clean-probe.patch.bak`) still UNTESTED |
| C | CP/SQE hardware state wedges at nested-IB entry | STRONG - sqe_pc frozen at `0x4:0x2883003f` in indirect microcode; latched HW fault bit18 from SQE release |
| D | GPU retires normally; failure is userspace present path | REJECTED - CP demonstrably wedges before any pixels |

## Next Moves
1. Pull v146 sweetlog and judge `0198 pfd` / `0198 fclr` against the pass criteria in `NOTES.md`.
2. If both 0198 hypotheses fail: **IB flattening** - NOP every nested indirect and emit each nested IB as its own ring-level `CP_INDIRECT_BUFFER` (ring path works 409/409); archived draft `xnu/experimental-patches/0198-gauguin-relocate-nested-ib.patch` (relocate nested IBs into the CP-L3 mark block) is an alternative variant, never built or verified.
3. In parallel: investigate SQE firmware / release sequence that latches `CP_HW_FAULT` bit18 at boot.

## Final Evidence Gate
- **progress_status**: collecting
- **Hypothesis Conclusions**: A REJECTED; B PARTIALLY OPEN; C STRONG; D REJECTED
