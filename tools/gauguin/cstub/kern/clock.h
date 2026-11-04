#ifndef STUB_KERN_CLOCK_H
#define STUB_KERN_CLOCK_H
#include <sys/param.h>
#define NSEC_PER_MSEC 1000000ull
extern void clock_interval_to_deadline(uint32_t, uint32_t, uint64_t *);
#endif
