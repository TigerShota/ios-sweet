#ifndef STUB_SYS_SYSTM_H
#define STUB_SYS_SYSTM_H
#include <sys/param.h>
extern int printf(const char *, ...) __attribute__((format(printf, 1, 2)));
extern void delay(uint32_t);
extern void bzero(void *, vm_size_t);
#endif
