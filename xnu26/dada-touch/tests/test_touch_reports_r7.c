#include <sys/dada_touch.h>
#include <assert.h>
#include <stdio.h>
#include <string.h>
struct fake {
    unsigned message, offset, reads, writes, bad_prefix, bad_padding, too_large;
    unsigned length[3], code[3];
};
static uint8_t value(unsigned offset) { return (uint8_t)(offset*17+3); }
static int read_bus(void *context,uint8_t *p,unsigned n) {
    struct fake *s=context;s->reads++;assert(n<=DADA_TCM_READ_CHUNK+2);
    unsigned length=s->length[s->message];
    if(!s->offset) {
        assert(n==4);p[0]=0xa5;p[1]=(uint8_t)s->code[s->message];
        p[2]=(uint8_t)length;p[3]=(uint8_t)(length>>8);
        if(!length)s->message++;else s->offset=1;
        return 0;
    }
    unsigned offset=s->offset-1, remaining=length+1-offset;
    unsigned count=remaining>DADA_TCM_READ_CHUNK ? DADA_TCM_READ_CHUNK:remaining;
    assert(n==count+2);p[0]=0xa5;p[1]=(s->bad_prefix && s->reads==3) ? 0x11:0x03;
    for(unsigned i=0;i<count;i++)p[i+2]=(offset+i<length)?value(offset+i):(s->bad_padding?0:0x5a);
    offset+=count;
    if(offset>=length) {s->message++;s->offset=0;}else s->offset=offset+1;
    return 0;
}
static int write_bus(void *context,const uint8_t *p,unsigned n) {
    struct fake *s=context;s->writes++;assert(n>=3 && n<=DADA_TCM_CONFIG+3);
    assert(p[0]==0x20 || p[0]==0x26);return 0;
}
static void delay_bus(void *context,unsigned us) {(void)context;assert(us==100 || us==5000);}
int main(void) {
    unsigned checks=0;
#define CHECK(x) do {assert(x);checks++;}while(0)
    struct fake s={0};struct dada_tcm_message msg;
    struct dada_tcm_bus bus={&s,read_bus,write_bus,delay_bus};
    /* Replay the captured r6 THP header a5 c0 ac 06, followed by a response. */
    s.length[0]=1708;s.code[0]=0xc0;s.code[1]=1;
    CHECK(!dada_tcm_command(&bus,0x20,NULL,0,&msg));
    CHECK(msg.code==1 && msg.length==0 && s.message==2 &&
        s.reads==2+(1709+DADA_TCM_READ_CHUNK-1)/DADA_TCM_READ_CHUNK);
    for(unsigned n=1;n<=DADA_TCM_PAYLOAD;n=n<500?n*2:n+509) {
        memset(&s,0,sizeof(s));s.length[0]=n;s.code[0]=0xc0;
        CHECK(!dada_tcm_read_message(&bus,&msg) && msg.length==n);
        for(unsigned i=0;i<n;i++)assert(msg.data[i]==value(i));
    }
    memset(&s,0,sizeof(s));s.length[0]=DADA_TCM_PAYLOAD;s.code[0]=0xc0;
    CHECK(!dada_tcm_read_message(&bus,&msg) && msg.length==DADA_TCM_PAYLOAD);
    memset(&s,0,sizeof(s));s.length[0]=DADA_TCM_PAYLOAD+1;s.code[0]=0xc0;
    CHECK(dada_tcm_read_message(&bus,&msg)==DADA_TCM_SIZE && s.reads==1);
    memset(&s,0,sizeof(s));s.length[0]=1708;s.code[0]=0xc0;s.bad_prefix=1;
    CHECK(dada_tcm_read_message(&bus,&msg)==DADA_TCM_PROTOCOL);
    memset(&s,0,sizeof(s));s.length[0]=1708;s.code[0]=0xc0;s.bad_padding=1;
    CHECK(dada_tcm_read_message(&bus,&msg)==DADA_TCM_PROTOCOL);
    uint8_t config[DADA_TCM_CONFIG]={0};memset(&s,0,sizeof(s));s.code[0]=1;
    CHECK(!dada_tcm_command(&bus,0x26,config,sizeof(config),&msg));
    CHECK(dada_tcm_command(&bus,0x26,config,sizeof(config)+1,&msg)==DADA_TCM_SIZE);
    struct dada_tcm_state state={0};state.max_write=1024;uint8_t app[38]={0};
    app[12]=0;app[13]=2;app[14]=80;app[32]=0xb0;app[33]=4;app[34]=0x6e;app[35]=0x0a;app[36]=10;
    CHECK(!dada_tcm_application(&state,app,sizeof(app)) && state.max_config==512 && state.max_x==1200 && state.max_y==2670);
    app[12]=1;CHECK(dada_tcm_application(&state,app,sizeof(app))==DADA_TCM_UNSUPPORTED);
    printf("dada r7 vendor report continuation: %u checks passed\n",checks);
}
