#ifndef DADA_TOUCH_HOST_KERNEL_H
#define DADA_TOUCH_HOST_KERNEL_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#define __unused __attribute__((unused))
#define __printflike(a, b) __attribute__((format(printf, a, b)))
#define NSEC_PER_USEC 1000
#define NSEC_PER_MSEC 1000000
#define NSEC_PER_SEC 1000000000
#define KERN_SUCCESS 0
#define REDWOOD_TOUCH_DOWN 1
#define REDWOOD_TOUCH_MOVE 2
#define REDWOOD_TOUCH_UP 3
typedef uintptr_t vm_offset_t;
typedef size_t vm_size_t;
typedef int kern_return_t;
typedef uintptr_t thread_t;
typedef int wait_result_t;
uint64_t mach_absolute_time(void);
void delay_for_interval(uint32_t, uint32_t);
void clock_interval_to_deadline(uint32_t, uint32_t, uint64_t *);
void clock_interval_to_absolutetime_interval(uint32_t, uint32_t, uint64_t *);
bool PE_parse_boot_argn(const char *, void *, int);
kern_return_t kernel_thread_start(void (*)(void *, wait_result_t), void *, thread_t *);
void thread_deallocate(thread_t);
thread_t current_thread(void);
void redwood_touch_post(uint32_t, uint32_t, uint32_t, uint32_t);
size_t strlcpy(char *, const char *, size_t);
#endif
