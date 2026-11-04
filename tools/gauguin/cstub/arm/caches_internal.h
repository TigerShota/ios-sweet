#ifndef STUB_ARM_CACHES_H
#define STUB_ARM_CACHES_H
#include <sys/param.h>
extern void flush_dcache64(addr64_t, vm_size_t, boolean_t);
extern addr64_t ptoa_64(ppnum_t);
#endif
