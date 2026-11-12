/* Exercise the exact edge generator and register layout used by this kernel. */
#include <sys/dada_input_board.h>
#include <sys/dada_spi_wire.h>
#include <sys/dada_touch.h>
#include <assert.h>
#include <stdio.h>
#include <string.h>
struct fake {
    unsigned levels[163], rises, falls, samples, bytes;
    unsigned transmitted, stop_after, invalid_sample;
    uint64_t now, time_step;
};
static void output(void *ctx,unsigned pin,unsigned value) {
    struct fake *s=ctx;
    assert(pin==41 || pin==42 || pin==43);assert(value<2);
    if(pin==42 && value!=s->levels[pin]) {
        if(value) {assert(!s->levels[43]);s->rises++;s->transmitted=(s->transmitted<<1)|s->levels[41];}
        else s->falls++;
    }
    s->levels[pin]=value;
}
static unsigned input(void *ctx,unsigned pin) {
    struct fake *s=ctx;assert(pin==40 && s->levels[42] && !s->levels[43]);
    unsigned bit=(0xa6>>(7-(s->samples++%8)))&1;
    return s->invalid_sample ? 2:bit;
}
static void delay(void *ctx,unsigned us) {((struct fake *)ctx)->now+=us;}
static uint64_t ticks(void *ctx) {
    struct fake *s=ctx;s->now+=s->time_step;return s->now;
}
static int stop(void *ctx) {
    struct fake *s=ctx;return s->stop_after && s->samples>=s->stop_after;
}
struct tcm_fake {unsigned reads,delay,always_bad,io_error;};
static int tcm_read(void *ctx,uint8_t *p,unsigned n) {
    struct tcm_fake *s=ctx;s->reads++;assert(n==4);memset(p,0,n);
    if(s->io_error)return -1;
    p[0]=s->reads==1 || s->always_bad ? 0:0xa5;p[1]=1;return 0;
}
static int tcm_write(void *ctx,const uint8_t *p,unsigned n) {(void)ctx;(void)p;(void)n;return 0;}
static void tcm_delay(void *ctx,unsigned us) {((struct tcm_fake *)ctx)->delay+=us;}
int main(void) {
    unsigned checks=0;
#define CHECK(x) do {assert(x);checks++;} while(0)
    CHECK(DADA_TLMM_PA+40*0x1000==0xf128000UL);
    CHECK(DADA_TLMM_PA+162*0x1000+4 < DADA_TLMM_PA+DADA_TLMM_SIZE);
    CHECK(DADA_SPI_SE_PA==0xa88000);
    CHECK(DADA_ARB_CHANNEL(1023)+4==DADA_ARB_CORE_SIZE);
    CHECK(DADA_ARB_OWNER(1023)+4 <= DADA_ARB_CNFG_SIZE);
    CHECK(DADA_ARB_OBSERVER(1023)+0x20<=DADA_ARB_OBSERVER_SIZE);
    CHECK(DADA_ARB_OBSERVER(0)==0 && DADA_ARB_OBSERVER(1)==0x20);
    CHECK(DADA_PON_PPID==0x13 && DADA_VOLUME_UP_PPID==0x18d);
    CHECK(!dada_arb_version_supported(0x50000000));
    CHECK(dada_arb_version_supported(0x70000000));
    CHECK(dada_arb_version_supported(0x7fffffff));
    CHECK(!dada_arb_version_supported(0x80000000));
    struct fake s={0};s.levels[43]=1;
    struct dada_spi_wire bus={&s,output,input,delay,ticks,stop};
    uint8_t tx[]={0x96,0x2d},rx[515];
    CHECK(!dada_spi_wire_transfer(&bus,tx,rx,2));
    CHECK(s.transmitted==0x962d && rx[0]==0xa6 && rx[1]==0xa6);
    CHECK(s.rises==16 && s.falls==16 && s.samples==16);
    CHECK(s.levels[43] && !s.levels[42]);
    CHECK(dada_spi_wire_transfer(&bus,tx,rx,0)==-1);
    CHECK(dada_spi_wire_transfer(&bus,tx,rx,516)==-1);
    CHECK(dada_spi_wire_transfer(&bus,NULL,NULL,1)==-1);
    memset(&s,0,sizeof(s));s.levels[43]=1;
    CHECK(!dada_spi_wire_transfer(&bus,NULL,rx,515));
    CHECK(s.samples==515*8 && rx[514]==0xa6);
    struct dada_spi_diagnostic diag;
    memset(&s,0,sizeof(s));s.levels[43]=1;s.stop_after=3;
    CHECK(dada_spi_wire_transfer_diagnostic(&bus,tx,rx,2,&diag)==-1);
    CHECK(diag.reason==DADA_SPI_CANCELLED && diag.bits==3);
    CHECK(s.levels[43] && !s.levels[42] && s.samples==3);
    memset(&s,0,sizeof(s));s.levels[43]=1;s.time_step=500001;
    CHECK(dada_spi_wire_transfer_diagnostic(&bus,tx,rx,2,&diag)==-1);
    CHECK(diag.reason==DADA_SPI_DEADLINE && diag.bits==0 && diag.elapsed_us>=500001);
    CHECK(s.levels[43] && !s.levels[42] && !s.samples);
    memset(&s,0,sizeof(s));s.levels[43]=1;s.invalid_sample=1;
    CHECK(dada_spi_wire_transfer_diagnostic(&bus,tx,rx,2,&diag)==-1);
    CHECK(diag.reason==DADA_SPI_SAMPLE && diag.bits==0);
    CHECK(s.levels[43] && !s.levels[42]);
    memset(&s,0,sizeof(s));s.levels[43]=1;
    CHECK(!dada_spi_wire_transfer_diagnostic(&bus,NULL,rx,130,&diag));
    CHECK(diag.reason==DADA_SPI_GOOD && diag.bits==1040 && diag.elapsed_us>=4164);
    CHECK(dada_spi_wire_transfer_diagnostic(&bus,NULL,NULL,4,&diag)==-1 &&
        diag.reason==DADA_SPI_ARGUMENT && !diag.bits && !diag.elapsed_us);
    struct tcm_fake t={0};struct dada_tcm_message message;
    struct dada_tcm_bus tcm={&t,tcm_read,tcm_write,tcm_delay};
    CHECK(!dada_tcm_read_message(&tcm,&message) && message.code==1 && !message.length);
    CHECK(t.reads==2 && t.delay==1000);
    memset(&t,0,sizeof(t));t.always_bad=1;
    CHECK(dada_tcm_read_message(&tcm,&message)==DADA_TCM_PROTOCOL && !message.length);
    CHECK(t.reads==2 && t.delay==1000);
    memset(&t,0,sizeof(t));t.io_error=1;
    CHECK(dada_tcm_read_message(&tcm,&message)==DADA_TCM_IO && t.reads==1 && !t.delay);
    printf("dada input port: %u checks passed\n",checks);
}
