# gauguin GPU bring-up build chain

The Windows-side tooling that produced patches 0099-0105 and the v21-v26
boot loop. All builds run on GitHub Actions (`xnu.yml` -> kernel,
`gauguin-shim.yml` -> boot image); this directory only generates patches,
uploads them, watches CI, and drives fastboot/adb + log pulls.

Paths are the old machine's layout (`D:\hackios\...`); adjust on a new box.
`gh` needs `repo` + `workflow` scopes. github.com is GFW-blocked (use the
local proxy for git push/fetch); api.github.com works direct (all `gh api`
calls go without proxy).

## The loop (one patch)

1. `gen_0105.py` - surgical generator: reads `gpu_post104.c` (the source
   state after the previous patch), applies anchored string substitutions,
   emits `xnu/patches/0105-*.patch` (multi-file: bsd/conf/files hunk +
   new-file hunk + gpu.c diff). Self-checks: replay-apply must reproduce
   the post source byte-identically, then a scratch `git apply` must also
   match. For the NEXT patch, copy this script and rebase its anchors on
   `gpu_post105.c` (the current source state, kept here).
2. `smoke_0105.py` - NDK clang `--target=aarch64-none-elf -fsyntax-only`
   against `cstub/` (stub XNU headers). Catches typos/use-before-define
   before spending a CI run. The cstub needs `int64_t` (added for 0105).
3. `put-0105.py` - Contents API PUT of the patch (binary-safe subprocess,
   LF, no BOM, content assertions, round-trip verification). Each PUT is
   one commit on main and auto-triggers `xnu.yml`.
4. `retry-xnu.py` - watches the run; macOS runner capacity rejections
   ("not acquired by Runner") are auto-rerun, real build errors abort.
   **Edit RUN = at the top for each new run.**
5. `v26-build.py` - after the kernel is green: dispatch gauguin-shim with
   the xnu run id + full cmdline (cpus=8, gauguingpu=2...), wait, download
   the artifact, then **payload string verification** - every diagnostic
   printf format the new patch adds must appear in payload.bin exactly
   once (asm template strings like `smc #0` compile to machine code and
   never appear - do not use them as marks).
6. `auto-v26.py` - waits for the phone in fastboot, `fastboot boot`
   (OKAY lands in stderr; success = exit 0 AND OKAY in stdout+stderr),
   waits for recovery, dd's the last 1025 blocks of userdata, decodes via
   `../sweet-log.py`.
7. `wait-pull-v25b.py` - recovery-only log pull (no boot) for re-reading.

## Analysis tools

- `zap-fw-check.py` - verifies the a615_zap firmware set (mdt = b00+b01,
  ELF32 phdrs, b02 = the single RWE PT_LOAD, byte-matches the .elf).
- `zap-region.py` - decodes gpu_region / kgsl-hyp pas-id from the real
  gauguin DTB (finds the carve-out is an orphan).
- `dtb-walk.py` - minimal FDT walker (grep device-tree nodes/props).

## refs/

- `adreno_pm4types.h` - **the load-bearing PM4 encoding reference**
  (MiCode gauguin-r-oss vendor kernel): type-7 header = opcode [22:16],
  count [14:0], odd parity 23/15; CP_ME_INIT=0x48, CP_SET_SECURE_MODE=0x66.
- `sm6350.dtsi`, `adreno_ringbuffer.c`, `adreno_kgsl.h` - vendor kernel
  references for the same SoC.
- `fajita-NOTES.md`, `mxtp-NOTES.md` - hardware-evidence base from the two
  sibling ports (a630/fajita and sweet/mxtp): the SMC64 PAS ABI, the zap
  load order, the cold-CP discipline, the one-read-per-printf lesson all
  came from their hardware logs (fajita console75c/76, mxtp 0088-0098).

## Regenerating the source chain

`gpu_post105.c` is `bsd/dev/gauguin_gpu.c` after 0105 - the base for the
next generator. Older `gpu_postNNN.c` states exist only on the old
machine; the patches on main are the source of truth (apply them in order
to a pristine file, or download the CI buildtree artifact).
