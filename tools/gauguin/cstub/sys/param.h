
#ifndef STUB_SYS_PARAM_H
#define STUB_SYS_PARAM_H
typedef unsigned char uint8_t;
typedef unsigned short uint16_t;
typedef unsigned int uint32_t;
typedef unsigned long long uint64_t;
typedef long long int64_t;
typedef int boolean_t;
typedef unsigned long vm_offset_t;
typedef unsigned long vm_size_t;
typedef int kern_return_t;
typedef unsigned int ppnum_t;
typedef unsigned long long addr64_t;
typedef int wait_result_t;
typedef int wait_interrupt_t;
typedef void *thread_t;
typedef void *event_t;
typedef void *thread_continue_t;
typedef struct _vm_map *vm_map_t;
typedef struct pmap *pmap_t;
#define KERN_SUCCESS 0
#define TRUE 1
#define FALSE 0
#define PAGE_SIZE 16384u
#define PAGE_MASK (PAGE_SIZE - 1u)
#ifndef NULL
#define NULL ((void *)0)
#define bool _Bool
#define true 1
#define false 0
#endif
extern void *memcpy(void *, const void *, unsigned long);
extern int memcmp(const void *, const void *, unsigned long);
#endif
