/* Runs the imported production C file, including its static parser and framing. */
#include "host_kernel.h"
#include <assert.h>
#include "../files/bsd/dev/hakios_dada_touch.c"

static unsigned assertions;
#define CHECK(c) do { assertions++; if (!(c)) { \
    fprintf(stderr, "line %d: %s\n", __LINE__, #c); abort(); } } while (0)
static struct { uint32_t type, id, x, y; } events[128];
static unsigned event_count;
static uint64_t now;
static bool finish_on_delay;
static uint8_t reads[2][TCM_MAX_PAYLOAD + 8];
static uint32_t read_sizes[2], read_count, read_at;

void redwood_tl(const char *fmt, ...) { (void)fmt; }
uint64_t mach_absolute_time(void) { return now++; }
void delay_for_interval(uint32_t n, uint32_t scale) {
    now += (uint64_t)n * scale;
    if (finish_on_delay) __atomic_store_n(&running, 0, __ATOMIC_RELEASE);
}
void clock_interval_to_deadline(uint32_t n, uint32_t scale, uint64_t *out) {
    *out = now + (uint64_t)n * scale;
}
void clock_interval_to_absolutetime_interval(uint32_t n, uint32_t scale, uint64_t *out) {
    *out = (uint64_t)n * scale;
}
bool PE_parse_boot_argn(const char *name, void *out, int size) {
    (void)name; (void)out; (void)size; return false;
}
kern_return_t kernel_thread_start(void (*fn)(void *, wait_result_t), void *arg, thread_t *t) {
    (void)fn; (void)arg; *t = 1; return 0;
}
void thread_deallocate(thread_t t) { (void)t; }
thread_t current_thread(void) { return 1; }
kern_return_t thread_terminate(thread_t t) { (void)t; return 0; }
vm_offset_t ml_io_map(vm_offset_t p, vm_size_t s) { (void)p; return (vm_offset_t)calloc(1, s); }
vm_offset_t ml_io_map_wcomb(vm_offset_t p, vm_size_t s) { return ml_io_map(p, s); }
size_t strlcpy(char *dst, const char *src, size_t size) {
    size_t n = strlen(src);
    if (size) { size_t copied = n < size - 1 ? n : size - 1; memcpy(dst, src, copied); dst[copied] = 0; }
    return n;
}
void redwood_touch_post(uint32_t type, uint32_t id, uint32_t x, uint32_t y) {
    CHECK(event_count < 128);
    events[event_count].type = type; events[event_count].id = id;
    events[event_count].x = x; events[event_count++].y = y;
}
static bool mock_spi(const uint8_t *tx, uint8_t *rx, uint32_t n) {
    (void)tx;
    CHECK(read_at < read_count);
    CHECK(n == read_sizes[read_at]);
    if (rx) memcpy(rx, reads[read_at], n);
    read_at++;
    return true;
}
static void reset(void) {
    stopping = started = running = 0;
    event_count = 0; now = 0; finish_on_delay = false;
    memset(down, 0, sizeof(down)); memset(&seen, 0, sizeof(seen));
    max_x = 1200; max_y = 2670; max_objects = 10;
    touch_config_size = 0; pending_payload = 0;
    read_at = read_count = 0; spi_transfer = mock_spi;
}
static void config(const uint8_t *p, size_t n) {
    CHECK(n <= sizeof(touch_config)); memcpy(touch_config, p, n); touch_config_size = n;
}
static void put_bits(uint8_t *p, unsigned *at, uint32_t v, unsigned n) {
    for (unsigned i = 0; i < n; i++, (*at)++)
        p[*at / 8] |= ((v >> i) & 1) << (*at % 8);
}
static unsigned contact(uint8_t *p, unsigned at, unsigned id, unsigned cls, unsigned x, unsigned y) {
    put_bits(p, &at, id, 4); put_bits(p, &at, cls, 4);
    put_bits(p, &at, x, 16); put_bits(p, &at, y, 16); return at;
}
static const uint8_t active_config[] = {24, 8, 1, 6, 4, 7, 4, 8, 16, 9, 16, 3, 0};

static void test_contacts(void) {
    reset(); config(active_config, sizeof(active_config));
    uint8_t p[64] = {1}; contact(p, 8, 3, CLASS_FINGER, 600, 1335);
    touch_report(p, 6);
    CHECK(event_count == 1 && events[0].type == REDWOOD_TOUCH_DOWN && events[0].id == 3);
    CHECK(events[0].x == 32767 && events[0].y == 32767);
    memset(p, 0, sizeof(p)); p[0] = 1; contact(p, 8, 3, CLASS_FINGER, 1200, 2670);
    touch_report(p, 6);
    CHECK(event_count == 2 && events[1].type == REDWOOD_TOUCH_MOVE);
    CHECK(events[1].x == 65535 && events[1].y == 65535);
    unsigned bad = seen.bad; touch_report(p, 4);
    CHECK(seen.bad == bad + 1 && event_count == 2 && down[3]);
    p[0] = 0; touch_report(p, 1);
    CHECK(event_count == 3 && events[2].type == REDWOOD_TOUCH_UP);
    CHECK(events[2].x == 65535 && events[2].y == 65535 && !down[3]);
    touch_report(p, 1); CHECK(event_count == 3);
    memset(p, 0, sizeof(p)); p[0] = 2;
    unsigned at = contact(p, 8, 1, CLASS_FINGER, 100, 100);
    contact(p, at, 8, CLASS_GLOVED_FINGER, 65535, 65535);
    touch_report(p, 11);
    CHECK(event_count == 5 && down[1] && down[8]);
    CHECK(events[4].x == 65535 && events[4].y == 65535);
    p[0] = 0; touch_report(p, 1);
    CHECK(event_count == 7 && !down[1] && !down[8]);

    reset(); max_objects = 2;
    const uint8_t all[] = {2, 7, 4, 8, 16, 9, 16, 3, 0}; config(all, sizeof(all));
    memset(p, 0, sizeof(p)); at = 0;
    for (unsigned i = 0; i < 2; i++) {
        put_bits(p, &at, 1, 4); put_bits(p, &at, 300 * i, 16); put_bits(p, &at, 600 * i, 16);
    }
    touch_report(p, 9); CHECK(event_count == 2 && down[0] && down[1]);
}

static void test_malformed(void) {
    reset();
    uint8_t p[64] = {0};
    const uint8_t zero_progress[] = {1, 3, 0}; config(zero_progress, sizeof(zero_progress));
    touch_report(p, sizeof(p)); CHECK(seen.bad == 1 && event_count == 0);
    const uint8_t nested[] = {1, 1, 3, 0}; config(nested, sizeof(nested));
    touch_report(p, sizeof(p)); CHECK(seen.bad == 2);
    config(active_config, sizeof(active_config)); p[0] = 11;
    touch_report(p, sizeof(p)); CHECK(seen.bad == 3 && event_count == 0);
    const uint8_t incomplete[] = {7}; config(incomplete, sizeof(incomplete));
    touch_report(p, sizeof(p)); CHECK(seen.bad == 4);
    /* Deterministic invalid/truncated configurations must always terminate. */
    uint32_t random = 0x35dada;
    for (unsigned trial = 0; trial < 20000; trial++) {
        for (unsigned i = 0; i < 32; i++) { random = random * 1664525 + 1013904223; touch_config[i] = random >> 24; }
        touch_config_size = 32; event_count = 0;
        touch_report(p, trial % 64);
    }
    CHECK(true);
}

static void framing(unsigned n, bool corrupt) {
    reset();
    uint8_t frame[TCM_MAX_PAYLOAD + 5];
    frame[0] = TCM_MARKER; frame[1] = REPORT_TOUCH; frame[2] = n; frame[3] = n >> 8;
    for (unsigned i = 0; i < n; i++) frame[4 + i] = i * 17;
    frame[4 + n] = corrupt ? 0 : TCM_PADDING;
    memset(reads, 0xff, sizeof(reads));
    unsigned first = n + 5 < READ_FIRST ? n + 5 : READ_FIRST;
    memcpy(reads[0], frame, first); read_sizes[0] = READ_FIRST; read_count = 1;
    if (n + 5 > READ_FIRST) {
        reads[1][0] = TCM_MARKER; reads[1][1] = STATUS_CONTINUED_READ;
        memcpy(reads[1] + 2, frame + READ_FIRST, n + 5 - READ_FIRST);
        read_sizes[1] = n + 5 - READ_FIRST + 2; read_count = 2;
    }
    uint8_t code = 0; uint32_t length = 0;
    CHECK(tcm_read(&code, &length) == !corrupt);
    if (!corrupt) {
        CHECK(code == REPORT_TOUCH && length == n);
        CHECK(memcmp(message, frame + 4, n) == 0);
    }
}

static void test_shutdown(void) {
    reset(); CHECK(hakios_dada_touch_stop());
    started = running = 1; finish_on_delay = true;
    CHECK(hakios_dada_touch_stop() && stopping && !running);
    reset(); started = running = 1;
    CHECK(!hakios_dada_touch_stop());
    CHECK(stopping && running); /* Never claim an active worker has stopped. */
    reset();
    tlmm = (vm_offset_t)calloc(1, TLMM_SIZE); CHECK(tlmm != 0);
    pins_saved = true;
    for (unsigned i = 0; i < 6; i++) { saved_cfg[i] = i + 16; saved_output[i] = (i & 1) * 2; }
    down[2] = true; last_x[2] = 123; last_y[2] = 456;
    restore_hardware();
    CHECK(!pins_saved && !down[2] && event_count == 1);
    CHECK(events[0].type == REDWOOD_TOUCH_UP && events[0].x == 123 && events[0].y == 456);
    for (unsigned i = 0; i < 6; i++) {
        CHECK(rd(GPIO_CFG(owned_pins[i])) == saved_cfg[i]);
        CHECK(rd(GPIO_IO(owned_pins[i])) == saved_output[i]);
    }
    free((void *)tlmm); tlmm = 0;
}

int main(void) {
    test_contacts(); test_malformed();
    unsigned sizes[] = {1, 16, 59, 60, 256, 1024, 1708, 4096};
    for (unsigned i = 0; i < sizeof(sizes) / sizeof(sizes[0]); i++) {
        framing(sizes[i], false); framing(sizes[i], true);
    }
    test_shutdown();
    printf("PASS: %u assertions; 20000 malformed-config trials; contacts/framing/shutdown\n", assertions);
}
