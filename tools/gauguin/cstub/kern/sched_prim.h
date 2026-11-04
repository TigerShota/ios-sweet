#ifndef STUB_KERN_SCHED_PRIM_H
#define STUB_KERN_SCHED_PRIM_H
#include <kern/thread.h>
extern kern_return_t assert_wait_deadline(event_t, wait_interrupt_t, uint64_t);
typedef void (*kernel_thread_continue_t)(void *, wait_result_t);
extern kern_return_t kernel_thread_start(kernel_thread_continue_t, void *, thread_t *);
#endif
