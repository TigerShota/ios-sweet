import os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
CLANG = (r"C:\Users\14523\AppData\Local\Android\Sdk\ndk\27.3.13750724"
         r"\toolchains\llvm\prebuilt\windows-x86_64\bin\clang.exe")
for f in [os.path.join(HERE, "gpu_post106.c")]:
    r = subprocess.run([CLANG, "--target=aarch64-none-elf", "-fsyntax-only", "-nostdinc",
                        "-isystem", os.path.join(HERE, "cstub"), "-DARM64_BOARD_CONFIG_SWEET",
                        "-Wall", "-Wextra", "-Wno-unused-parameter",
                        f], capture_output=True, text=True)
    print(f, "-> exit:", r.returncode)
    sys.stdout.write(r.stdout)
    sys.stderr.write(r.stderr)
    sys.exit(r.returncode)
