#ifndef STUB_KERN_THREAD_H
#define STUB_KERN_THREAD_H
#include <sys/param.h>
extern thread_t current_thread(void);
extern kern_return_t thread_terminate(thread_t);
extern void thread_deallocate(thread_t);
#define THREAD_UNINT 0
#define THREAD_CONTINUE_NULL ((thread_continue_t)0)
extern wait_result_t thread_block(thread_continue_t);
#endif
