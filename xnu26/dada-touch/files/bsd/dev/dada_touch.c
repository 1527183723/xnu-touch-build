/*
 * Xiaomi 15 (dada), Synaptics TouchCom on QUPv3_1 SE2 / CS0.
 * Board: stock dada DT, GPIO40..43, IRQ162, reset161 (active LOW: the
 * stock driver defaults reset_on_state to 0; the DT does not override it).
 * Register layout: upstream pinctrl-sm8750.c, gcc-sm8750.c, spi-geni-qcom.c.
 * This driver never accesses a partition or writes controller flash.
 *
 * dada_touch=1/2: experimental native MMIO, requires dada_touch_mmio=1.
 * Native-only XNU26 port: the unused UEFI ring ABI is not included.
 * dada_touch_reset=1: additionally pulse the verified active-low reset pin.
 * Supplies are inherited from firmware. No unverified PMIC writes are made.
 */
#include <sys/dada_touch.h>
#if defined(__arm64__)
#include <kern/clock.h>
#include <kern/cpu_data.h>
#include <kern/thread.h>
#include <libkern/libkern.h>
#include <mach/mach_types.h>
#include <pexpert/pexpert.h>
#include <sys/redwood_input.h>
#include <sys/systm.h>
#include <sys/dada_input_board.h>
#include <sys/dada_spi_wire.h>
#include <sys/dada_power_probe.h>
#include <sys/dada_touch_power_r6.h>
#include <vm/memory_types.h>

extern vm_offset_t ml_io_map(vm_offset_t, vm_size_t);
extern kern_return_t thread_terminate(thread_t);

/* BEGIN DADA TOUCH LIVE MIRROR
 * printf alone reaches msgbuf, appended to the USB log only on kernel return.
 * Mirror the same bounded line to the live RAM ring without stopping XNU.
 */
extern void loader_log_line(const char *line);
static void dada_touch_log(const char *format, ...) __printflike(1, 2);
static void
dada_touch_log(const char *format, ...)
{
    char line[512];
    va_list args;
    va_start(args, format);
    vsnprintf(line, sizeof(line), format, args);
    va_end(args);
    printf("%s", line);
    loader_log_line(line);
}
/* END DADA TOUCH LIVE MIRROR */
#define printf dada_touch_log

#define GCC_PA 0x00100000UL
#define TLMM_PA DADA_TLMM_PA
#define SE_PA DADA_SPI_SE_PA
#define VOTE 0x52008
#define VOTES ((1u << 20) | (1u << 21) | (1u << 24))
#define OFF (1u << 31)
#define IRQ_PIN 162
#define RESET_PIN 161

static vm_offset_t gcc, tlmm, se;
static uint32_t mode, reset_chip, fifo_depth, original_vote;
static uint32_t supply_request;
static unsigned auxiliary_changed;
static uint32_t saved_pins[6][2];
static const unsigned pins[6] = {40, 41, 42, 43, RESET_PIN, IRQ_PIN};
static unsigned saved_count, board_changed, engine_changed, gpio_changed;
static volatile unsigned stopping, running;
static struct dada_tcm_state touch;
static unsigned tcm_features;
static uint64_t frames, samples;
static void pause_us(void *, unsigned);
/* Only registers which this driver changes, captured while the engine is idle. */
static struct { unsigned offset; uint32_t value; } engine_regs[] = {
    {0x020,0},{0x024,0},{0x028,0},{0x048,0},{0x07c,0},
    {0x224,0},{0x22c,0},{0x230,0},{0x24c,0},{0x250,0},
    {0x254,0},{0x258,0},{0x25c,0},{0x260,0},{0x264,0},
    {0x268,0},{0x26c,0},{0x270,0},{0x278,0},{0x284,0},{0x288,0},
    {0x614,0},{0x80c,0},{0x810,0},{0x814,0},{0xe18,0},{0xe1c,0},{0xe30,0}
};

static uint32_t rd(vm_offset_t base, unsigned offset) { return *(volatile uint32_t *)(base + offset); }
static void wr(vm_offset_t base, unsigned offset, uint32_t v) {
    *(volatile uint32_t *)(base + offset) = v;
    __asm__ volatile("dsb oshst" ::: "memory");
}
static void pause_us(__unused void *ctx, unsigned us) { delay_for_interval(us, NSEC_PER_USEC); }

static void
power_probe(void)
{
    /* DT: aop_cmd_db_region@81c60000; apps_rsc drv@2, active TCS 0..2.
     * Deliberately read only: do not clear IRQs or claim a TCS here.
     */
    printf("dada-power: read-only probe CMDDB@%#llx RSC2@%#lx; no supply/reset writes\n",
        DADA_CMDDB_PA, DADA_RSC_PA);
    vm_offset_t db = ml_io_map(DADA_CMDDB_PA, DADA_CMDDB_SIZE);
    if (!db) { printf("dada-power: CMDDB mapping failed\n"); return; }
    const volatile uint8_t *data = (const volatile uint8_t *)db;
    printf("dada-power: CMDDB version=%#x magic=%#x\n", dada_db_u32(data), dada_db_u32(data+4));
    const char *names[] = {"ldob12", "ldob16"};
    for (unsigned i = 0; i < 2; i++) {
        struct dada_power_resource resource;
        int result = dada_db_resource(data, DADA_CMDDB_SIZE, names[i], &resource);
        printf("dada-power: %s result=%d address=%#x slave=%u version=%#x aux=%u; stock=%umV\n",
            names[i], result, resource.address, resource.slave, resource.version,
            resource.auxiliary_length, i ? 3000u : 1800u);
        if (result) { printf("dada-power: resource check failed; RSC not accessed\n"); return; }
    }
    vm_offset_t rsc = ml_io_map(DADA_RSC_PA, DADA_RSC_SIZE);
    if (!rsc) { printf("dada-power: RSC mapping failed\n"); return; }
    uint32_t id = rd(rsc,0), config = rd(rsc,0xc);
    unsigned major = (id >> 16) & 255, minor = (id >> 8) & 255;
    unsigned count = (config >> 12) & 63, commands = (config >> 27) & 31;
    printf("dada-power: RSC id=%#x version=%u.%u config=%#x DRV2-tcs=%u cmds=%u\n",
        id, major, minor, config, count, commands);
    unsigned control, status, enable, wait;
    if (major == 3) { control=0x24; status=0x28; enable=0x2c; wait=0x20; }
    else if (major == 2 && minor >= 7) { control=0x14; status=0x18; enable=0x1c; wait=0x10; }
    else { printf("dada-power: unsupported RSC layout; no TCS read\n"); return; }
    if (count < 7 || count > 31 || !commands || commands > 16) {
        printf("dada-power: RSC config disagrees with stock DT; no TCS read\n"); return;
    }
    printf("dada-power: IRQ enable=%#x status=%#x\n",
        rd(rsc,DADA_RSC_TCS_OFFSET),rd(rsc,DADA_RSC_TCS_OFFSET+4));
    for (unsigned i = 0; i < 7; i++) {
        unsigned offset = DADA_RSC_TCS_OFFSET + DADA_RSC_TCS_STRIDE * i;
        printf("dada-power: TCS%u %s ctrl=%#x status=%#x enable=%#x wait=%#x\n",
            i, i < 3 ? "active" : i < 5 ? "sleep" : "wake",
            rd(rsc,offset+control),rd(rsc,offset+status),rd(rsc,offset+enable),rd(rsc,offset+wait));
    }
    printf("dada-power: probe complete; no resource request, IRQ clear, GPIO or PMIC write\n");
}

static void
emit(__unused void *ctx, unsigned type, unsigned id, unsigned x, unsigned y)
{
    redwood_touch_post(type, id, x, y);
    samples++;
    if (samples <= 16 || type != REDWOOD_TOUCH_MOVE || (samples % 100) == 0)
        printf("dada-touch: event %llu type=%u id=%u x=%u y=%u\n", samples, type, id, x, y);
}

static int
clocks_on(void)
{
    return !((rd(gcc,0x23140) | rd(gcc,0x23144) | rd(gcc,0x1827c)) & OFF);
}

static void wire_output(__unused void *ctx, unsigned pin, unsigned value) {
    wr(tlmm,pin*0x1000+4,value<<1);
}
static unsigned wire_input(__unused void *ctx, unsigned pin) {
    return rd(tlmm,pin*0x1000+4)&1;
}
static uint64_t wire_time(__unused void *ctx) {
    uint64_t ns; absolutetime_to_nanoseconds(mach_absolute_time(),&ns);return ns/1000;
}
static int wire_stop(__unused void *ctx) {
    return __atomic_load_n(&stopping,__ATOMIC_ACQUIRE)!=0;
}
static const struct dada_spi_wire wire={NULL,wire_output,wire_input,pause_us,wire_time,wire_stop};

static uint32_t power_read(void *ctx,unsigned offset) { return rd((vm_offset_t)ctx,offset); }
static void power_write(void *ctx,unsigned offset,uint32_t value) { wr((vm_offset_t)ctx,offset,value); }
static int power_on(void) {
    vm_offset_t db=ml_io_map(DADA_CMDDB_PA,DADA_CMDDB_SIZE);
    struct dada_power_resource resources[2];
    const char *names[2]={"ldob12","ldob16"};
    const uint32_t addresses[2]={0x40a00,0x41a00};
    if (!db) return -1;
    for(unsigned i=0;i<2;i++) {
        int error=dada_db_resource((const volatile uint8_t *)db,DADA_CMDDB_SIZE,names[i],&resources[i]);
        if(error || resources[i].address!=addresses[i] || resources[i].version!=0x100) {
            printf("dada-power: stock resource %s check failed %d/%#x; no vote sent\n",
                names[i],error,resources[i].address);return -1;
        }
    }
    vm_offset_t rsc=ml_io_map(DADA_RSC_PA,DADA_RSC_SIZE);
    if(!rsc)return -1;
    const struct dada_rpmh_bus power={(void *)rsc,power_read,power_write,pause_us,wire_stop};
    struct dada_rpmh_result result;
    printf("dada-power: r6 fixed six-command vote; AVDD 3000mV then IOVDD 1800mV; IRQ mask unchanged\n");
    int error=dada_rpmh_touch_power(&power,&result);
    printf("dada-power: result=%d triggered=%u restored=%u fresh_ack=%u/6 busy_seen=%u IRQ=%#x ctrl=%#x status=%#x\n",
        error,result.triggered,result.restored,result.acknowledged,result.busy_seen,
        result.irq,result.control,result.status);
    for(unsigned i=0;i<DADA_TOUCH_POWER_COMMANDS;i++)
        printf("dada-power: slot%u before=%#x after=%#x\n",i,result.before[i],result.command_status[i]);
    if(error || !result.restored) {
        printf("dada-power: request not confirmed; no reset/SPI pin changes\n");return -1;
    }
    pause_us(NULL,50000);
    printf("dada-power: both stock touch supplies acknowledged; power delay 50ms complete\n");
    return 0;
}

static int software_spi_begin(void) {
    /* Read every rollback value and check ownership before the first pin write.
     * Explicit dada_touch_power=1 enables stock supplies/reset bring-up only
     * after the command DB and RSC layout have matched the observed hardware.
     */
    for (unsigned i=0;i<6;i++) {
        unsigned function=(saved_pins[i][0]>>2)&15;
        if ((saved_pins[i][0]&(1u<<12)) || function>(i<4 ? 1u:0u)) {
            printf("dada-touch: GPIO%u mux/egpio ownership not accepted\n",pins[i]);return -1;
        }
    }
    /* Refuse an active hardware SPI master before switching its pins to GPIO.
     * With clocks gated the master cannot be active, so do not access its MMIO.
     */
    if (clocks_on()) {
        se=ml_io_map(SE_PA,0x4000);
        if (!se || (rd(se,0x040)&0x1001u)) {
            printf("dada-touch: hardware engine active; software SPI refused\n");return -1;
        }
    }
    if(supply_request) {
        if(reset_chip!=1) {
            printf("dada-touch: supply bring-up requires explicit dada_touch_reset=1\n");return -1;
        }
        if(power_on())return -1;
        auxiliary_changed=1;
        wr(tlmm,IRQ_PIN*0x1000,saved_pins[5][0]&~0x3ffu);
        wr(tlmm,RESET_PIN*0x1000+4,0);
        wr(tlmm,RESET_PIN*0x1000,(saved_pins[4][0]&~0x3ffu)|(1u<<9));
        pause_us(NULL,10000);
        wr(tlmm,RESET_PIN*0x1000+4,2);
        pause_us(NULL,200000);
        printf("dada-touch: stock reset pulse 10ms + 200ms; reset cfg=%#x io=%#x IRQ cfg=%#x io=%#x\n",
            rd(tlmm,RESET_PIN*0x1000),rd(tlmm,RESET_PIN*0x1000+4),
            rd(tlmm,IRQ_PIN*0x1000),rd(tlmm,IRQ_PIN*0x1000+4));
    }
    if (!(rd(tlmm,RESET_PIN*0x1000+4)&1)) {
        printf("dada-touch: reset GPIO161 sampled LOW; SPI not started; supply state not inferred\n");return -1;
    }
    if (rd(tlmm,IRQ_PIN*0x1000)&(1u<<9)) {
        printf("dada-touch: IRQ GPIO162 configured as output; SPI not started\n");return -1;
    }
    gpio_changed=1;
    wire_output(NULL,DADA_SPI_CS,1);
    wire_output(NULL,DADA_SPI_CLK,0);
    wire_output(NULL,DADA_SPI_MOSI,0);
    /* mux 0, no pull, 6mA; the unrelated register fields remain as inherited. */
    for (unsigned i=0;i<4;i++)
        wr(tlmm,pins[i]*0x1000,(saved_pins[i][0]&~0x3ffu) | (2u<<6) | (i ? 1u<<9:0));
    pause_us(NULL,2000);
    printf("dada-touch: software SPI mode0 GPIO40..43 ready; supply_init=%u\n",supply_request);
    return 0;
}

static int
board_init(void)
{
    unsigned i;
    gcc = ml_io_map(GCC_PA, 0x80000);
    tlmm = ml_io_map(TLMM_PA, DADA_TLMM_SIZE);
    if (!gcc || !tlmm) return -1;
    printf("dada-touch: probe GCC@%#lx TLMM@%#lx SE@%#lx\n", GCC_PA, TLMM_PA, SE_PA);
    original_vote = rd(gcc,VOTE);
    printf("dada-touch: clocks vote=%#x se=%#x mahb=%#x sahb=%#x qspi_cfg=%#x postdiv=%#x\n",
        original_vote, rd(gcc,0x1827c), rd(gcc,0x23140), rd(gcc,0x23144),
        rd(gcc,0x188c4), rd(gcc,0x1828c));
    for (i = 0; i < 6; i++) {
        saved_pins[i][0] = rd(tlmm,pins[i] * 0x1000);
        saved_pins[i][1] = rd(tlmm,pins[i] * 0x1000 + 4);
        printf("dada-touch: gpio%u cfg=%#x io=%#x\n", pins[i], saved_pins[i][0], saved_pins[i][1]);
    }
    saved_count = 6;
    if (mode == 4) return software_spi_begin();
    if (mode == 1 && !clocks_on()) {
        printf("dada-touch: probe complete; engine clocks off, no SE read attempted\n");
        return -1;
    }
    if (mode == 2) {
        wr(gcc,VOTE,original_vote | VOTES);
        board_changed = 1;
        for (i = 0; i < 1000 && !clocks_on(); i++) pause_us(NULL,10);
        if (!clocks_on()) {
            printf("dada-touch: engine clock votes did not take effect\n");
            return -1;
        }
    }
    se = ml_io_map(SE_PA,0x4000);
    if (!se) return -1;
    printf("dada-touch: SE firmware=%#x interface=%#x status=%#x hw=%#x\n",
        rd(se,0x068),rd(se,0x064),rd(se,0x040),rd(se,0xe24));
    if (mode == 1) return -1;
    if (((rd(se,0x068) >> 8) & 255) != 1 || (rd(se,0x064) & 1) || (rd(se,0x040) & 1)) {
        printf("dada-touch: unavailable SPI FIFO or engine already owned; refusing takeover\n");
        return -1;
    }
    fifo_depth = (rd(se,0xe24) >> 16) & 63;
    if (fifo_depth < 4 || fifo_depth > 63) return -1;
    for (i = 0; i < sizeof(engine_regs)/sizeof(engine_regs[0]); i++)
        engine_regs[i].value = rd(se,engine_regs[i].offset);
    engine_changed = 1;
    /* Function 1 for GPIO40..43 in SM8750 pinctrl; 6mA, pull-down as stock DT. */
    for (i = 0; i < 4; i++) wr(tlmm,pins[i] * 0x1000,
        (saved_pins[i][0] & ~0x3ffu) | 1u << 2 | 2u << 6 | 1u);
    wr(tlmm,IRQ_PIN * 0x1000,saved_pins[5][0] & ~0x3ffu);
    if (reset_chip) {
        wr(tlmm,RESET_PIN * 0x1000 + 4,0);
        wr(tlmm,RESET_PIN * 0x1000,(saved_pins[4][0] & ~0x3ffu) | 1u << 9);
        pause_us(NULL,10000);
        wr(tlmm,RESET_PIN * 0x1000 + 4,2);
        pause_us(NULL,200000);
    }
    wr(se,0xe18,0); wr(se,0x258,0);
    wr(se,0x028,rd(se,0x028) | 0x7f); wr(se,0xe30,rd(se,0xe30) | 0xf);
    wr(se,0x024,0x7f); wr(se,0x020,1);
    wr(se,0x618,0xffffffff); wr(se,0x648,0xffffffff);
    wr(se,0xc44,0xffffffff); wr(se,0xd44,0xffffffff);
    wr(se,0xe1c,rd(se,0xe1c) | 15); wr(se,0x614,0x7fc0007f);
    wr(se,0x80c,0); wr(se,0x810,fifo_depth-3); wr(se,0x814,fifo_depth-2);
    wr(se,0x22c,0); wr(se,0x250,0); wr(se,0x224,0); wr(se,0x230,0); wr(se,0x24c,0);
    wr(se,0x25c,rd(se,0x25c) & ~2u); wr(se,0x278,0);
    wr(se,0x260,0x0007f8fe); wr(se,0x264,0x000ffefe);
    wr(se,0x284,0x0007f8fe); wr(se,0x288,0x000ffefe);
    wr(se,0x254,0); wr(se,0x268,4);
    /* Preserve the shared QSPI source. /256 stays below the board's 15MHz maximum
     * for every source in gcc-sm8750's QSPI frequency table (max250MHz).
     */
    wr(se,0x07c,0); wr(se,0x048,(256u << 4) | 1);
    return 0;
}

static void
recover(void)
{
    unsigned i;
    wr(se,0x604,4);
    for (i = 0; i < 200 && !(rd(se,0x610) & (1u << 4)); i++) pause_us(NULL,100);
    if (!(rd(se,0x610) & (1u << 4))) {
        wr(se,0x604,2);
        for (i = 0; i < 200 && !(rd(se,0x610) & (1u << 5)); i++) pause_us(NULL,100);
    }
    for (i = 0; i < fifo_depth && (rd(se,0x804) & 0x1ffffff); i++) (void)rd(se,0x780);
    wr(se,0x618,0xffffffff);
}

static int
transfer(const uint8_t *tx, uint8_t *rx, unsigned length)
{
    if (mode == 4) {
        struct dada_spi_diagnostic diag;
        static unsigned traced;
        /* GPIO clocks must not sleep or migrate halfway through a CS window.
         * XNU delay_for_interval() may otherwise block on its 2us edge delay.
         * Interrupts stay enabled; the wire routine retains its 500ms deadline
         * and length bound. Never log while preemption is disabled.
         */
        disable_preemption();
        int error=dada_spi_wire_transfer_diagnostic(&wire,tx,rx,length,&diag);
        enable_preemption();
        if(error || traced++<20)
            printf("dada-touch: SPI wire %s len=%u result=%d reason=%u bits=%u us=%llu IRQ=%u\n",
                tx?"write":"read",length,error,diag.reason,diag.bits,
                diag.elapsed_us,rd(tlmm,IRQ_PIN*0x1000+4)&1);
        return error;
    }
    unsigned sent = 0, received = 0, iterations;
    uint32_t status = 0;
    if (!length || length > DADA_TCM_PAYLOAD + 3 || (rd(se,0x040) & 1)) return -1;
    wr(se,0x618,0xffffffff); wr(se,0x26c,length); wr(se,0x270,length);
    wr(se,0x600,3u << 27);
    for (iterations = 0; iterations < 10000; iterations++) {
        unsigned used = rd(se,0x800) & 0xfffffff;
        while (sent < length && used + 1 < fifo_depth) {
            uint32_t word = 0;
            unsigned j;
            for (j = 0; j < 4 && sent < length; j++,sent++)
                word |= (uint32_t)(tx ? tx[sent] : 0xff) << (8*j);
            wr(se,0x700,word);
            used++;
        }
        uint32_t fifo = rd(se,0x804);
        unsigned words = fifo & 0x1ffffff, bytes = words * 4;
        if (words > fifo_depth) break;
        if (words && (fifo & 0x80000000) && ((fifo >> 28) & 7) && ((fifo >> 28) & 7) < 4)
            bytes -= 4 - ((fifo >> 28) & 7);
        while (words--) {
            uint32_t word = rd(se,0x780);
            for (unsigned j = 0; j < 4 && bytes; j++,bytes--) {
                if (received < length) { if (rx) rx[received] = (uint8_t)(word >> (8*j)); received++; }
            }
        }
        status = rd(se,0x610);
        if (status & 0x3300000e) break;
        if ((status & 1) && received == length && sent == length) {
            wr(se,0x618,status); return 0;
        }
        if (stopping) break;
        pause_us(NULL,10);
    }
    printf("dada-touch: SPI failed len=%u sent=%u rx=%u status=%#x\n",length,sent,received,status);
    recover();
    return -1;
}
static int bus_read(__unused void *ctx,uint8_t *p,unsigned n) {
    static unsigned reads;
    int error=transfer(NULL,p,n);
    if(error || reads++<20) {
        if(!error && n>=4) printf("dada-touch: SPI read len=%u bytes=%02x %02x %02x %02x\n",n,p[0],p[1],p[2],p[3]);
        else printf("dada-touch: SPI read len=%u error=%d\n",n,error);
    }
    return error;
}
static int bus_write(__unused void *ctx,const uint8_t *p,unsigned n) { return transfer(p,NULL,n); }
static const struct dada_tcm_bus bus = {NULL,bus_read,bus_write,pause_us};

static int
controller_command(uint8_t command, const uint8_t *payload, unsigned length,
    struct dada_tcm_message *msg)
{
    int error = dada_tcm_command_features(&bus,command,payload,length,msg,tcm_features);
    printf("dada-touch: init command=%#x arg=%#x len=%u crc=%u result=%d reply=%#x bytes=%u\n",
        command,length?payload[0]:0,length,!!(tcm_features&DADA_TCM_CRC),error,msg->code,msg->length);
    return error;
}

static void
dump_packet(const char *name,const uint8_t *data,unsigned length)
{
    for(unsigned i=0;i<length;i+=16) {
        char line[160];int used=snprintf(line,sizeof(line),"dada-touch: %s[%u]",name,i);
        for(unsigned j=i;j<length && j<i+16;j++)
            used+=snprintf(line+used,sizeof(line)-(unsigned)used," %02x",data[j]);
        printf("%s\n",line);
    }
}

static int
controller_init(void)
{
    static struct dada_tcm_message msg;
    uint8_t report = DADA_TCM_TOUCH, thp = DADA_TCM_THP;
    int error;
    printf("dada-touch: r11 stock startup CRC negotiation / final local-report selection\n");
    /* Stock v1_detect consumes the unsolicited reset packet first and reads
     * CRC/extra-RC positions before deciding how later commands are framed.
     * Sending IDENTIFY first can leave its response queued behind that packet.
     */
    error=dada_tcm_read_message_features(&bus,&msg,DADA_TCM_CRC|DADA_TCM_EXTRA_RC);
    printf("dada-touch: startup read result=%d code=%#x bytes=%u trailer=%02x %02x %02x %02x\n",
        error,msg.code,msg.length,msg.trailer[0],msg.trailer[1],msg.trailer[2],msg.trailer[3]);
    if(!error)error=dada_tcm_detect_features(&msg,&tcm_features);
    if (!error) error = dada_tcm_identify(&touch,msg.data,msg.length);
    if (error) {
        printf("dada-touch: IDENTIFY failed error=%d code=%#x bytes=%u; check supplies/ownership/protocol\n",
            error,msg.code,msg.length);
        return error;
    }
    printf("dada-touch: chip=%s protocol=%u mode=%u build=%u maxwrite=%u\n",
        touch.part,touch.version,touch.mode,touch.build_id,touch.max_write);
    printf("dada-touch: stock framing CRC=%u extra-RC=%u; startup consumed without IDENTIFY write\n",
        !!(tcm_features&DADA_TCM_CRC),!!(tcm_features&DADA_TCM_EXTRA_RC));
    /* Stock syna_dev_enter_normal_sensing(): exit deep sleep, then clear
     * DC_ENABLE_WAKEUP_GESTURE_MODE. Do not guess LBP/HBP register values.
     */
    error=controller_command(0x2d,NULL,0,&msg);
    if(error)return error;
    const uint8_t gesture_off[3]={0x09,0,0};
    error=controller_command(0x24,gesture_off,sizeof(gesture_off),&msg);
    if(error)return error;
    error=controller_command(0x23,gesture_off,1,&msg);
    if(error)return error;
    if(msg.length<2 || msg.data[0] || msg.data[1]) {
        printf("dada-touch: normal sensing gesture readback rejected bytes=%u\n",msg.length);
        return DADA_TCM_PROTOCOL;
    }
    printf("dada-touch: stock normal sensing awake; wakeup-gesture=0 verified\n");
    error = controller_command(0x20,NULL,0,&msg);
    if (!error && msg.length >= 38)
        printf("dada-touch: app status=%u config=%u maxreport=%u x=%u y=%u objects=%u\n",
            msg.data[2]|msg.data[3]<<8,msg.data[12]|msg.data[13]<<8,
            msg.data[14]|msg.data[15]<<8,msg.data[32]|msg.data[33]<<8,
            msg.data[34]|msg.data[35]<<8,msg.data[36]|msg.data[37]<<8);
    if(!error)dump_packet("app",msg.data,msg.length);
    if(!error && msg.length>=46)
        printf("dada-touch: sensor rows=%u cols=%u hybrid=%u buttons=%u\n",
            msg.data[40]|msg.data[41]<<8,msg.data[42]|msg.data[43]<<8,
            msg.data[44]|msg.data[45]<<8,msg.data[38]|msg.data[39]<<8);
    if (!error) error = dada_tcm_application(&touch,msg.data,msg.length);
    if (error) return error;
    error = controller_command(0x06,&report,1,&msg);
    if (error) return error;
    /* Stock USE_CUSTOM_TOUCH_REPORT_CONFIG is disabled. Preserve its
     * controller-provided layout instead of attempting a custom format.
     */
    error = controller_command(0x25,NULL,0,&msg);
    if (error) return error;
    printf("dada-touch: native report config bytes=%u capacity=%u; no custom SET_CONFIG\n",
        msg.length,touch.max_config);
    for(unsigned i=0;i<msg.length && i<128;i+=8)
        printf("dada-touch: config[%u]=%02x %02x %02x %02x %02x %02x %02x %02x\n",i,
            msg.data[i],i+1<msg.length?msg.data[i+1]:0,i+2<msg.length?msg.data[i+2]:0,
            i+3<msg.length?msg.data[i+3]:0,i+4<msg.length?msg.data[i+4]:0,
            i+5<msg.length?msg.data[i+5]:0,i+6<msg.length?msg.data[i+6]:0,i+7<msg.length?msg.data[i+7]:0);
    error=dada_tcm_install_config(&touch,msg.data,msg.length);
    printf("dada-touch: stock config parser result=%d meaningful=%u active-only=%u loop=%u..%u\n",
        error,touch.config_length,touch.active_only,touch.loop_start,touch.loop_end);
    if(error)return error;
    /* Read-only diagnostics. A rejected optional query is logged without
     * changing any dynamic field or guessing the LBP/HBP value encoding.
     */
    int diagnostic=controller_command(0x32,NULL,0,&msg);
    if(!diagnostic)dump_packet("features",msg.data,msg.length>16?16:msg.length);
    if(diagnostic && diagnostic!=DADA_TCM_REJECTED)return diagnostic;
    const uint8_t lbp_hbp=0x11;
    diagnostic=controller_command(0x23,&lbp_hbp,1,&msg);
    if(!diagnostic && msg.length==2)
        printf("dada-touch: read-only DC_CONTROL_LBP_HBP=%#x; no write\n",msg.data[0]|msg.data[1]<<8);
    if(diagnostic && diagnostic!=DADA_TCM_REJECTED)return diagnostic;
    /* Keep the stock switch pair together AFTER normal-sensing wakeup and
     * report setup: raw/THP off, local REPORT_TOUCH on. STATUS_OK alone does
     * not prove that the firmware is actually generating local reports.
     */
    error=controller_command(0x06,&thp,1,&msg);
    if(error)return error;
    error = controller_command(0x05,&report,1,&msg);
    if (!error) printf("dada-touch: READY %ux%u slots=%u IRQ=GPIO162 polling=125Hz\n",
        touch.max_x,touch.max_y,touch.objects);
    return error;
}

static void
restore(void)
{
    unsigned i;
    dada_tcm_release(&touch,emit,NULL);
    if (gpio_changed) {
        wire_output(NULL,DADA_SPI_CS,1);wire_output(NULL,DADA_SPI_CLK,0);
        for (i=4;i--;) {
            wr(tlmm,pins[i]*0x1000+4,saved_pins[i][1]&2);
            wr(tlmm,pins[i]*0x1000,saved_pins[i][0]);
        }
        gpio_changed=0;
    }
    if(auxiliary_changed) {
        for(i=4;i<6;i++) {
            wr(tlmm,pins[i]*0x1000+4,saved_pins[i][1]&2);
            wr(tlmm,pins[i]*0x1000,saved_pins[i][0]);
        }
        auxiliary_changed=0;
        printf("dada-touch: reset/IRQ pin configuration restored; shared supply votes left enabled\n");
    }
    if (engine_changed) {
        if (rd(se,0x040) & 1) recover();
        for (i = 0; i < sizeof(engine_regs)/sizeof(engine_regs[0]); i++)
            wr(se,engine_regs[i].offset,engine_regs[i].value);
    }
    if (board_changed) {
        if (engine_changed) for (i = 0; i < saved_count; i++) {
            wr(tlmm,pins[i]*0x1000+4,saved_pins[i][1] & 2);
            wr(tlmm,pins[i]*0x1000,saved_pins[i][0]);
        }
        wr(gcc,VOTE,(rd(gcc,VOTE) & ~(VOTES & ~original_vote)));
    }
    engine_changed = board_changed = 0;
}

static void
worker(__unused void *arg, __unused wait_result_t wait)
{
    printf("dada-touch: worker started mode=%u; live log mirror active\n",mode);
    if (mode == 5) {
        power_probe();
        __atomic_store_n(&running,0,__ATOMIC_RELEASE);
        thread_terminate(current_thread());
        return;
    }
    if (mode == 6) {
        int result=power_on();
        printf("dada-power: supply-only diagnostic result=%d; GPIO/SPI untouched\n",result);
        __atomic_store_n(&running,0,__ATOMIC_RELEASE);
        thread_terminate(current_thread());
        return;
    }
    static struct dada_tcm_message msg;
    unsigned errors = 0;
    uint64_t polls=0,reads=0,thp_reports=0,touch_reports=0,idle_reports=0,other_reports=0;
    uint64_t next_summary=0,next_probe=0;
    unsigned last_code=0xff,last_length=0,forced_probes=0;
    int error = board_init();
    if (!error) error = controller_init();
    if (error) printf("dada-touch: inactive after probe/init (%d); iOS continues without physical input\n",error);
    while (!error && !stopping) {
        uint64_t now=wire_time(NULL);
        unsigned irq=rd(tlmm,IRQ_PIN*0x1000+4)&1;
        polls++;
        if(now>=next_summary) {
            printf("dada-touch: live polls=%llu reads=%llu irq=%u touch=%llu thp=%llu idle=%llu other=%llu frames=%llu samples=%llu last=%#x/%u\n",
                polls,reads,irq,touch_reports,thp_reports,idle_reports,other_reports,frames,samples,last_code,last_length);
            next_summary=now+2000000;
        }
        int forced=irq && forced_probes<4 && now>=next_probe;
        if (!irq || forced) {
            if(forced){forced_probes++;next_probe=now+250000;}
            reads++;
            error = dada_tcm_read_message_features(&bus,&msg,tcm_features);
            last_code=msg.code;last_length=msg.length;
            if(forced || reads<=8)
                printf("dada-touch: runtime read=%llu irq=%u forced=%u result=%d code=%#x bytes=%u\n",
                    reads,irq,forced,error,msg.code,msg.length);
            if(!error) {
                if(msg.code==DADA_TCM_TOUCH)touch_reports++;
                else if(msg.code==DADA_TCM_THP)thp_reports++;
                else if(msg.code==0 || msg.code==7)idle_reports++;
                else other_reports++;
                if(msg.code==DADA_TCM_THP && thp_reports<=3) {
                    /* Stock tp_raw has a 64-byte header. Record bounded raw
                     * evidence only; do not turn an unverified matrix into
                     * synthetic iOS touches or assume its actual dimensions.
                     */
                    dump_packet("THP-head",msg.data,msg.length>64?64:msg.length);
                    if(msg.length>=64)
                        printf("dada-touch: THP metadata signature=%#x head-count=%u state=%u scan-mode=%u rate=%u frame=%u cols=%u rows=%u bytes=%u\n",
                            msg.data[0]|msg.data[1]<<8,msg.data[2]|msg.data[3]<<8,
                            msg.data[20],msg.data[24],msg.data[25],msg.data[28]|msg.data[29]<<8,
                            msg.data[48],msg.data[49],msg.length);
                    if(msg.length>64)dump_packet("THP-tail",msg.data+msg.length-64,64);
                }
            }
            if (!error && msg.code == DADA_TCM_IDENTIFY) error = DADA_TCM_RESTARTED;
            if (!error && msg.code == DADA_TCM_TOUCH) {
                error = dada_tcm_frame(&touch,msg.data,msg.length,emit,NULL);
                if (!error) frames++;
            }
            if (error) {
                dada_tcm_release(&touch,emit,NULL);
                /* A bounded read-only probe with ATTN high may yield no
                 * valid packet. Report it without claiming a touch failure.
                 * Never hide a controller restart or an ATTN-low error.
                 */
                if(forced && error!=DADA_TCM_RESTARTED) {
                    printf("dada-touch: IRQ-high probe had no valid report error=%d\n",error);
                    error=0;pause_us(NULL,8000);continue;
                }
                printf("dada-touch: report error=%d consecutive=%u\n",error,errors+1);
                if (++errors >= 8 || error == DADA_TCM_RESTARTED) break;
                error = 0;
            } else errors = 0;
        }
        pause_us(NULL,8000);
    }
    restore();
    printf("dada-touch: stopped frames=%llu samples=%llu\n",frames,samples);
    __atomic_store_n(&running,0,__ATOMIC_RELEASE);
    thread_terminate(current_thread());
}

void
dada_touch_init(void)
{
    thread_t thread;
    uint32_t allow_mmio = 0;
    if (!PE_parse_boot_argn("dada_touch",&mode,sizeof(mode)) || !mode) {
        printf("dada-touch: disabled (dada_touch=0 or missing); no native input\n");
        return;
    }
    printf("dada-touch: init requested mode=%u; live log mirror active\n",mode);
    if (mode == 3) {
        printf("dada-touch: UEFI ring mode is not part of this native XNU26 port\n");
        return;
    }
    if (mode > 6) { printf("dada-touch: native 1/2/4, read-only power 5, supply-only 6\n"); return; }
    PE_parse_boot_argn("dada_touch_mmio",&allow_mmio,sizeof(allow_mmio));
    if (allow_mmio != 1) {
        printf("dada-touch: native MMIO disabled after the hardware reset; use the UEFI SPI port\n");
        return;
    }
    PE_parse_boot_argn("dada_touch_reset",&reset_chip,sizeof(reset_chip));
    PE_parse_boot_argn("dada_touch_power",&supply_request,sizeof(supply_request));
    printf("dada-touch: native init mmio=%u power_request=%u reset_request=%u\n",
        allow_mmio,supply_request,reset_chip);
    stopping = 0; running = 1;
    if (kernel_thread_start(worker,NULL,&thread) != KERN_SUCCESS) {
        running = 0; printf("dada-touch: cannot create worker\n");
    } else thread_deallocate(thread);
}

void
dada_touch_stop(void)
{
    __atomic_store_n(&stopping,1,__ATOMIC_RELEASE);
    for (unsigned i = 0; i < 1000 && __atomic_load_n(&running,__ATOMIC_ACQUIRE); i++) pause_us(NULL,1000);
    if (running) printf("dada-touch: stop timeout; inspect controller before another hardware run\n");
}
#else
void dada_touch_init(void) {}
void dada_touch_stop(void) {}
#endif
