#ifndef STUB_VM_KERN_H
#define STUB_VM_KERN_H
#include <sys/param.h>
extern vm_map_t kernel_map;
extern kern_return_t kmem_alloc_contig(vm_map_t, vm_offset_t *, vm_size_t,
    vm_offset_t, vm_offset_t, vm_offset_t, int, unsigned int);
#endif
