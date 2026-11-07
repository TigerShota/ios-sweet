#!/usr/bin/env python3
"""Emit a git-apply-able patch that creates REPO_PATH with the contents of SRC.

Usage: mknewfilepatch.py SRC REPO_PATH OUT_PATCH
"""
import sys

src, repo_path, out = sys.argv[1:4]
lines = open(src, encoding='utf-8').read().splitlines()

with open(out, 'w', encoding='utf-8', newline='\n') as f:
    f.write('diff --git a/%s b/%s\n' % (repo_path, repo_path))
    f.write('new file mode 100644\n')
    f.write('--- /dev/null\n')
    f.write('+++ b/%s\n' % repo_path)
    f.write('@@ -0,0 +1,%d @@\n' % len(lines))
    for line in lines:
        f.write('+' + line + '\n')
print('%s: %d lines -> %s' % (repo_path, len(lines), out))
