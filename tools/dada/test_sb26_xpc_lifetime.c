#define _POSIX_C_SOURCE 200809L
#include <assert.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

typedef struct mock_xpc { atomic_uint refs; int type; } *xpc_object_t;
typedef struct mock_semaphore {
    pthread_mutex_t lock;
    pthread_cond_t changed;
    unsigned count;
} *dispatch_semaphore_t;

#define XPC_TYPE_DICTIONARY 1
#define DISPATCH_TIME_NOW 0
#define NSEC_PER_MSEC 1000000LL
static atomic_uint live_objects;

static xpc_object_t xpc_new(int type)
{
    xpc_object_t obj = calloc(1, sizeof(*obj));
    assert(obj);
    atomic_init(&obj->refs, 1);
    obj->type = type;
    atomic_fetch_add(&live_objects, 1);
    return obj;
}

static xpc_object_t xpc_retain(xpc_object_t obj)
{
    if (obj) atomic_fetch_add(&obj->refs, 1);
    return obj;
}

static void xpc_release(xpc_object_t obj)
{
    if (obj && atomic_fetch_sub(&obj->refs, 1) == 1) {
        atomic_fetch_sub(&live_objects, 1);
        free(obj);
    }
}

static int xpc_get_type(xpc_object_t obj) { return obj->type; }
static xpc_object_t xpc_dictionary_create(const char **keys, xpc_object_t *values, size_t n)
{
    (void)keys; (void)values; (void)n;
    return xpc_new(XPC_TYPE_DICTIONARY);
}

static dispatch_semaphore_t dispatch_semaphore_create(long value)
{
    dispatch_semaphore_t sem = calloc(1, sizeof(*sem));
    assert(sem);
    pthread_mutex_init(&sem->lock, NULL);
    pthread_cond_init(&sem->changed, NULL);
    sem->count = (unsigned)value;
    return sem;
}

static long dispatch_semaphore_signal(dispatch_semaphore_t sem)
{
    pthread_mutex_lock(&sem->lock);
    sem->count++;
    pthread_cond_signal(&sem->changed);
    pthread_mutex_unlock(&sem->lock);
    return 0;
}

static void dispatch_release(dispatch_semaphore_t sem)
{
    pthread_cond_destroy(&sem->changed);
    pthread_mutex_destroy(&sem->lock);
    free(sem);
}

static int64_t dispatch_time(int64_t when, int64_t delta) { (void)when; return delta; }

static long dispatch_semaphore_wait(dispatch_semaphore_t sem, int64_t timeout_ns)
{
    struct timespec until;
    clock_gettime(CLOCK_REALTIME, &until);
    until.tv_sec += timeout_ns / 1000000000LL;
    until.tv_nsec += timeout_ns % 1000000000LL;
    if (until.tv_nsec >= 1000000000L) { until.tv_sec++; until.tv_nsec -= 1000000000L; }
    pthread_mutex_lock(&sem->lock);
    int rc = 0;
    while (!sem->count && rc == 0) rc = pthread_cond_timedwait(&sem->changed, &sem->lock, &until);
    if (sem->count) { sem->count--; rc = 0; }
    pthread_mutex_unlock(&sem->lock);
    return rc == 0 ? 0 : 1;
}

#include "../../userland/src/inject_sb26_xpc_compat.inc"

enum { OBJECTS = 128, WORKERS = 8, ITERATIONS = 50000 };
static void *cache_worker(void *arg)
{
    uintptr_t id = (uintptr_t)arg;
    unsigned state = (unsigned)(id + 1) * 0x9e3779b9U;
    char key[72];
    for (unsigned i = 0; i < ITERATIONS; i++) {
        state = state * 1664525U + 1013904223U;
        unsigned index = state % OBJECTS;
        snprintf(key, sizeof(key), "icon-%u", index);
        xpc_object_t fresh = xpc_new(XPC_TYPE_DICTIONARY);
        sb26_xpc_cache_put(key, fresh);
        xpc_release(fresh); /* only the cache now owns this object */
        xpc_object_t found = sb26_xpc_cache_get(key);
        if (found) xpc_release(found);
        found = sb26_xpc_cache_any();
        if (found) xpc_release(found);
    }
    return NULL;
}

struct late_callback { struct sb26_xpc_reply_wait *wait; unsigned delay_ms; };

static void *complete_later(void *arg)
{
    struct late_callback *cb = arg;
    struct timespec delay = { .tv_sec = cb->delay_ms / 1000,
        .tv_nsec = (long)(cb->delay_ms % 1000) * 1000000L };
    nanosleep(&delay, NULL);
    xpc_object_t reply = xpc_new(XPC_TYPE_DICTIONARY);
    sb26_xpc_reply_wait_complete(cb->wait, reply);
    xpc_release(reply);
    return NULL;
}

static void test_late_callback(void)
{
    struct sb26_xpc_reply_wait *wait = sb26_xpc_reply_wait_create();
    assert(wait);
    struct late_callback cb = { wait, 350 };
    pthread_t thread;
    assert(pthread_create(&thread, NULL, complete_later, &cb) == 0);
    int timed_out = dispatch_semaphore_wait(wait->done,
        dispatch_time(DISPATCH_TIME_NOW, 300LL * NSEC_PER_MSEC)) != 0;
    assert(timed_out);
    sb26_xpc_reply_wait_release(wait);
    pthread_join(thread, NULL);
    assert(atomic_load(&sb26_xpc_pending) == 0);
}

static void test_early_callback(void)
{
    struct sb26_xpc_reply_wait *wait = sb26_xpc_reply_wait_create();
    assert(wait);
    xpc_object_t reply = xpc_new(XPC_TYPE_DICTIONARY);
    sb26_xpc_reply_wait_complete(wait, reply);
    assert(dispatch_semaphore_wait(wait->done,
        dispatch_time(DISPATCH_TIME_NOW, 300LL * NSEC_PER_MSEC)) == 0);
    xpc_object_t received = sb26_xpc_reply_wait_take(wait);
    assert(received == reply);
    xpc_release(received);
    xpc_release(reply);
    sb26_xpc_reply_wait_release(wait);
    assert(atomic_load(&sb26_xpc_pending) == 0);
}

int main(void)
{
    int used_cache = 1;
    xpc_object_t cold = sb26_xpc_cache_fallback("missing", &used_cache);
    assert(cold && !used_cache);
    xpc_release(cold);
    pthread_t workers[WORKERS];
    for (uintptr_t i = 0; i < WORKERS; i++)
        assert(pthread_create(&workers[i], NULL, cache_worker, (void *)i) == 0);
    for (unsigned i = 0; i < WORKERS; i++) pthread_join(workers[i], NULL);
    assert(sb26_xpc_cache_n == SB26_XPC_CACHE_MAX);
    xpc_object_t fallback = sb26_xpc_cache_fallback("missing", &used_cache);
    assert(fallback && used_cache);
    xpc_release(fallback);
    for (unsigned i = 0; i < sb26_xpc_cache_n; i++) {
        xpc_release(sb26_xpc_cache[i].reply);
        sb26_xpc_cache[i].reply = NULL;
    }
    sb26_xpc_cache_n = 0;
    assert(atomic_load(&live_objects) == 0);

    test_late_callback();
    test_early_callback();
    assert(atomic_load(&live_objects) == 0);
    puts("PASS: concurrent cache replacement, retained reads, late timeout callback, early reply");
    return 0;
}
