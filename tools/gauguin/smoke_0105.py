import subprocess, sys

CLANG = (r"C:\Users\14523\AppData\Local\Android\Sdk\ndk\27.3.13750724"
         r"\toolchains\llvm\prebuilt\windows-x86_64\bin\clang.exe")
for f in [r"D:\hackios\temp\gpu_post105.c", r"D:\hackios\temp\zap_fw.c"]:
    r = subprocess.run([CLANG, "--target=aarch64-none-elf", "-fsyntax-only", "-nostdinc",
                        "-isystem", r"D:\hackios\temp\cstub", "-DARM64_BOARD_CONFIG_SWEET",
                        "-Wall", "-Wextra", "-Wno-unused-parameter",
                        f], capture_output=True, text=True)
    print(f, "-> exit:", r.returncode)
    sys.stdout.write(r.stdout)
    sys.stderr.write(r.stderr)
