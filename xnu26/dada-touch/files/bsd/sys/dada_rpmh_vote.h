/* Limited dada RPMh VRM vote on the observed RSC 3.8 DRV2 active TCS1.
 * Register semantics: Linux v6.18 rpmh-rsc.c / qcom-rpmh-regulator.c.
 * Uses only the two stock touch resources and never changes IRQ enable.
 */
#ifndef _SYS_DADA_RPMH_VOTE_H_
#define _SYS_DADA_RPMH_VOTE_H_
#include <stdint.h>
#include <sys/dada_power_probe.h>
#define DADA_TCS1 (DADA_RSC_TCS_OFFSET + DADA_RSC_TCS_STRIDE)
struct dada_rpmh_bus {
    void *context;
    uint32_t (*read)(void *, unsigned);
    void (*write)(void *, unsigned, uint32_t);
    void (*delay_us)(void *, unsigned);
    int (*stop)(void *);
};
struct dada_rpmh_result {
    unsigned triggered, restored;
    uint32_t irq, command_status[3];
};
static inline int dada_rpmh_set(const struct dada_rpmh_bus *b, unsigned r, uint32_t v) {
    b->write(b->context,r,v);
    return b->read(b->context,r)==v ? 0 : -1;
}
/* Returns 0 only after a fresh IRQ completion and issued/completed statuses.
 * -2 means no claim (busy/layout/IRQ ownership); -4 means timeout.
 * If restored==0 after triggered==1, callers must stop hardware bring-up.
 * Votes remain on: an unknown shared rail's previous vote cannot be guessed.
 */
static inline int dada_rpmh_vote(const struct dada_rpmh_bus *b, uint32_t address,
                               unsigned mv, struct dada_rpmh_result *out) {
    uint32_t saved[3][3], ctrl, wait;
    const unsigned offsets[3]={0x34,0x38,0x3c};
    int error=-3;
    if (!b || !out || !b->read || !b->write || !b->delay_us || !b->stop) return -1;
    out->triggered=out->restored=0; out->irq=0;
    for(unsigned i=0;i<3;i++) out->command_status[i]=0;
    if (!((address==0x40a00 && mv==1800) || (address==0x41a00 && mv==3000))) return -1;
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
    for(unsigned i=0;i<3;i++) for(unsigned j=0;j<3;j++)
        saved[i][j]=b->read(b->context,DADA_TCS1+offsets[j]+24*i);
    /* Recheck the channel immediately before the first mutation. */
    if (b->read(b->context,DADA_TCS1+0x2c) ||
        b->read(b->context,DADA_TCS1+0x28)!=1 ||
        b->read(b->context,DADA_TCS1+0x24)!=ctrl ||
        (b->read(b->context,DADA_RSC_TCS_OFFSET)&2) ||
        (b->read(b->context,DADA_RSC_TCS_OFFSET+4)&2)) return -2;
    for(unsigned i=0;i<3;i++) {
        /* Voltage (mV), enable, PMIC5 LDO HPM; all request responses. */
        uint32_t value=i==0 ? mv : i==1 ? 1u : 7u;
        if (dada_rpmh_set(b,DADA_TCS1+0x34+24*i,0x10108) ||
            dada_rpmh_set(b,DADA_TCS1+0x38+24*i,address+4*i) ||
            dada_rpmh_set(b,DADA_TCS1+0x3c+24*i,value)) goto restore;
    }
    if (b->stop(b->context)) { error=-7; goto restore; }
    if (dada_rpmh_set(b,DADA_TCS1+0x20,7) ||
        dada_rpmh_set(b,DADA_TCS1+0x2c,7) ||
        dada_rpmh_set(b,DADA_TCS1+0x24,0) ||
        dada_rpmh_set(b,DADA_TCS1+0x24,0x10000)) goto restore;
    out->triggered=1;
    b->write(b->context,DADA_TCS1+0x24,0x1010000);
    error=-4;
    for(unsigned attempt=0;attempt<2000;attempt++) {
        out->irq=b->read(b->context,DADA_RSC_TCS_OFFSET+4);
        if (out->irq&2) {
            error=0;
            for(unsigned i=0;i<3;i++) {
                out->command_status[i]=b->read(b->context,DADA_TCS1+0x40+24*i);
                if ((out->command_status[i]&0x10100)!=0x10100) error=-5;
            }
            break;
        }
        b->delay_us(b->context,100);
    }
restore:
    /* Never clear somebody else's IRQ or tear down a still-running command. */
    if (b->read(b->context,DADA_TCS1+0x28)!=1 ||
        (b->read(b->context,DADA_RSC_TCS_OFFSET)&2)) return error ? error : -6;
    if (dada_rpmh_set(b,DADA_TCS1+0x24,0x10000) ||
        dada_rpmh_set(b,DADA_TCS1+0x24,0) ||
        dada_rpmh_set(b,DADA_TCS1+0x2c,0)) return -6;
    if (out->triggered && (out->irq&2)) b->write(b->context,DADA_RSC_TCS_OFFSET+8,2);
    for(unsigned i=0;i<3;i++) for(unsigned j=0;j<3;j++)
        if (dada_rpmh_set(b,DADA_TCS1+offsets[j]+24*i,saved[i][j])) return -6;
    if (dada_rpmh_set(b,DADA_TCS1+0x20,wait) ||
        dada_rpmh_set(b,DADA_TCS1+0x24,ctrl)) return -6;
    if (b->read(b->context,DADA_RSC_TCS_OFFSET+4)&2) return -6;
    out->restored=1;
    return error;
}
#endif
