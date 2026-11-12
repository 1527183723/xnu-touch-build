/* dada stock touch rails, RSC 3.8 DRV2 active TCS1.
 * Layout/message semantics checked against MiCode dada-v-oss afb5a488:
 * drivers/soc/qcom/rpmh-rsc.c and drivers/regulator/qcom-rpmh-regulator.c.
 * The inherited firmware owns IRQ0. Never enable another IRQ or borrow TCS0.
 */
#ifndef _SYS_DADA_TOUCH_POWER_R6_H_
#define _SYS_DADA_TOUCH_POWER_R6_H_
#include <stdint.h>
#include <sys/dada_power_probe.h>
#define DADA_TCS1 (DADA_RSC_TCS_OFFSET + DADA_RSC_TCS_STRIDE)
#define DADA_TOUCH_POWER_COMMANDS 6u
#define DADA_RPMH_ACK 0x10100u
struct dada_rpmh_bus {
    void *context;
    uint32_t (*read)(void *, unsigned);
    void (*write)(void *, unsigned, uint32_t);
    void (*delay_us)(void *, unsigned);
    int (*stop)(void *);
};
struct dada_rpmh_result {
    unsigned triggered, restored, busy_seen, acknowledged;
    uint32_t irq, control, status;
    uint32_t before[DADA_TOUCH_POWER_COMMANDS];
    uint32_t command_status[DADA_TOUCH_POWER_COMMANDS];
};
static inline int dada_rpmh_set(const struct dada_rpmh_bus *b, unsigned r, uint32_t v) {
    b->write(b->context,r,v);
    return b->read(b->context,r)==v ? 0 : -1;
}
/* One fixed request: stock AVDD first, then IOVDD, as syna_spi_power_on().
 * Each rail gets voltage, HPM, then enable. No arbitrary rail/value API.
 *
 * A masked IRQ is not a completion requirement: require all six fresh
 * issued+completed command statuses AND an idle TCS. All status bits must
 * start clear, otherwise refuse without writing (-8). This rules out stale
 * completions, including a second invocation on an already-used channel.
 * -2: layout/ownership/busy; -4: timeout; -5: incomplete response;
 * -6: not restored; -7: cancelled. Never infer success from idle alone.
 * Acknowledged votes remain on; do not invent a previous shared-rail vote.
 */
static inline int dada_rpmh_touch_power(const struct dada_rpmh_bus *b,
                                      struct dada_rpmh_result *out) {
    static const uint32_t address[DADA_TOUCH_POWER_COMMANDS] = {
        0x41a00,0x41a08,0x41a04,0x40a00,0x40a08,0x40a04
    };
    static const uint32_t value[DADA_TOUCH_POWER_COMMANDS] = {3000,7,1,1800,7,1};
    const unsigned offsets[3]={0x34,0x38,0x3c};
    uint32_t saved[DADA_TOUCH_POWER_COMMANDS][3], ctrl, wait;
    int error=-3;
    if (!b || !out || !b->read || !b->write || !b->delay_us || !b->stop) return -1;
    out->triggered=out->restored=out->busy_seen=out->acknowledged=0;
    out->irq=out->control=out->status=0;
    for(unsigned i=0;i<DADA_TOUCH_POWER_COMMANDS;i++) out->before[i]=out->command_status[i]=0;
    if (b->stop(b->context)) return -7;
    if (b->read(b->context,0)!=0x30800 ||
        b->read(b->context,0xc)!=0x800c8104 ||
        (b->read(b->context,DADA_RSC_TCS_OFFSET)&2) ||
        (b->read(b->context,DADA_RSC_TCS_OFFSET+4)&2) ||
        b->read(b->context,DADA_TCS1+0x28)!=1 ||
        b->read(b->context,DADA_TCS1+0x2c)!=0) return -2;
    ctrl=b->read(b->context,DADA_TCS1+0x24);
    if (ctrl!=0 && ctrl!=0x10000) return -2;
    wait=b->read(b->context,DADA_TCS1+0x20);
    for(unsigned i=0;i<DADA_TOUCH_POWER_COMMANDS;i++) {
        out->before[i]=b->read(b->context,DADA_TCS1+0x40+24*i);
        if(out->before[i]&DADA_RPMH_ACK) return -8;
        for(unsigned j=0;j<3;j++) saved[i][j]=b->read(b->context,DADA_TCS1+offsets[j]+24*i);
    }
    /* Recheck ownership and the fresh baseline before the first mutation. */
    if (b->read(b->context,DADA_TCS1+0x2c) ||
        b->read(b->context,DADA_TCS1+0x28)!=1 ||
        b->read(b->context,DADA_TCS1+0x24)!=ctrl ||
        (b->read(b->context,DADA_RSC_TCS_OFFSET)&2) ||
        (b->read(b->context,DADA_RSC_TCS_OFFSET+4)&2)) return -2;
    for(unsigned i=0;i<DADA_TOUCH_POWER_COMMANDS;i++)
        if(b->read(b->context,DADA_TCS1+0x40+24*i)!=out->before[i]) return -8;
    for(unsigned i=0;i<DADA_TOUCH_POWER_COMMANDS;i++) {
        if (dada_rpmh_set(b,DADA_TCS1+0x34+24*i,0x10108) ||
            dada_rpmh_set(b,DADA_TCS1+0x38+24*i,address[i]) ||
            dada_rpmh_set(b,DADA_TCS1+0x3c+24*i,value[i])) goto restore;
    }
    if (b->stop(b->context)) { error=-7; goto restore; }
    if (dada_rpmh_set(b,DADA_TCS1+0x20,0x3f) ||
        dada_rpmh_set(b,DADA_TCS1+0x2c,0x3f) ||
        dada_rpmh_set(b,DADA_TCS1+0x24,0) ||
        dada_rpmh_set(b,DADA_TCS1+0x24,0x10000)) goto restore;
    out->triggered=1;
    b->write(b->context,DADA_TCS1+0x24,0x1010000);
    error=-4;
    for(unsigned attempt=0;attempt<2000;attempt++) {
        out->irq=b->read(b->context,DADA_RSC_TCS_OFFSET+4);
        out->control=b->read(b->context,DADA_TCS1+0x24);
        out->status=b->read(b->context,DADA_TCS1+0x28);
        out->acknowledged=0;
        for(unsigned i=0;i<DADA_TOUCH_POWER_COMMANDS;i++) {
            out->command_status[i]=b->read(b->context,DADA_TCS1+0x40+24*i);
            if((out->command_status[i]&DADA_RPMH_ACK)==DADA_RPMH_ACK) out->acknowledged++;
        }
        if(out->status!=1) out->busy_seen=1;
        if((b->read(b->context,DADA_RSC_TCS_OFFSET)&2) || out->control!=0x1010000) {
            error=-2; goto restore;
        }
        if(out->status==1 && out->acknowledged==DADA_TOUCH_POWER_COMMANDS) { error=0; break; }
        if(out->status==1 && (out->irq&2)) { error=-5; break; }
        if(b->stop(b->context)) { error=-7; break; }
        b->delay_us(b->context,100);
    }
restore:
    /* Never tear down a running/foreign TCS or clear an unrelated IRQ. */
    if (b->read(b->context,DADA_TCS1+0x28)!=1 ||
        (b->read(b->context,DADA_RSC_TCS_OFFSET)&2)) return error ? error : -6;
    if(out->triggered && b->read(b->context,DADA_TCS1+0x24)!=0x1010000)
        return error ? error : -6;
    if (dada_rpmh_set(b,DADA_TCS1+0x24,0x10000) ||
        dada_rpmh_set(b,DADA_TCS1+0x24,0) ||
        dada_rpmh_set(b,DADA_TCS1+0x2c,0)) return -6;
    if (out->triggered && (out->irq&2)) b->write(b->context,DADA_RSC_TCS_OFFSET+8,2);
    for(unsigned i=0;i<DADA_TOUCH_POWER_COMMANDS;i++) for(unsigned j=0;j<3;j++)
        if (dada_rpmh_set(b,DADA_TCS1+offsets[j]+24*i,saved[i][j])) return -6;
    if (dada_rpmh_set(b,DADA_TCS1+0x20,wait) ||
        dada_rpmh_set(b,DADA_TCS1+0x24,ctrl)) return -6;
    if (b->read(b->context,DADA_RSC_TCS_OFFSET+4)&2) return -6;
    out->restored=1;
    return error;
}
#endif
