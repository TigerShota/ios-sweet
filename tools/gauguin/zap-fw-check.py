# -*- coding: utf- -*-
"""Verify gauguin a615_zap firmware files: mdt ELF32 phdrs, b02 size, b00+b01 == mdt."""
import struct

FW = r"D:\hackios\out\fw"
mdt = open(FW + r"\a615_zap.mdt", "rb").read()
b00 = open(FW + r"\a615_zap.b00", "rb").read()
b01 = open(FW + r"\a615_zap.b01", "rb").read()
b02 = open(FW + r"\a615_zap.b02", "rb").read()
elf = open(FW + r"\a615_zap.elf", "rb").read()

print("sizes: mdt=%d b00=%d b01=%d b02=%d elf=%d" % (len(mdt), len(b00), len(b01), len(b02), len(elf)))
print("mdt == b00+b01: %s" % (mdt == b00 + b01))

magic, = struct.unpack_from("<I", mdt, 0)
e_class, e_data, e_version = mdt[4], mdt[5], mdt[6]
e_type, e_machine, e_version2, e_entry, e_phoff, e_shoff, e_flags, e_ehsize, e_phentsize, e_phnum = \
    struct.unpack_from("<HHIIIIIHHH", mdt, 16)
print("magic=%#x class=%d data=%d type=%d machine=%d entry=%#x phoff=%d phentsize=%d phnum=%d" % (
    magic, e_class, e_data, e_type, e_machine, e_entry, e_phoff, e_phentsize, e_phnum))

for i in range(e_phnum):
    p_type, p_offset, p_vaddr, p_paddr, p_filesz, p_memsz, p_flags, p_align = \
        struct.unpack_from("<IIIIIIII", mdt, e_phoff + i * 32)
    print("phdr %d: type %d off %#x vaddr %#x paddr %#x filesz %d memsz %d flags %#x align %#x" % (
        i, p_type, p_offset, p_vaddr, p_paddr, p_filesz, p_memsz, p_flags, p_align))

# b02 must equal the mdt's ph2 file extent
for i in range(e_phnum):
    p_type, p_offset, p_vaddr, p_paddr, p_filesz, p_memsz, p_flags, p_align = \
        struct.unpack_from("<IIIIIIII", mdt, e_phoff + i * 32)
    if p_type == 1:
        seg = mdt[p_offset:p_offset + p_filesz]
        print("ph%d PT_LOAD: b02 matches mdt extent: %s; b02 matches elf extent: %s" % (
            i, seg == b02, elf[p_offset:p_offset + p_filesz] == b02))
print("first 8 bytes b02: %s" % b02[:8].hex())
