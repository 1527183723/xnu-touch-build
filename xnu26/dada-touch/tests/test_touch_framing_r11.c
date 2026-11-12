#include <sys/dada_touch.h>
#include <assert.h>
#include <stdio.h>
#include <string.h>

struct packet {unsigned length,code,tail;uint8_t trailer[4];};
struct fake {
    struct packet packet[4];
    unsigned current,offset,reads,writes,bad_eom,bad_final;
    uint8_t written[515];unsigned written_length;
};
static uint8_t data_byte(unsigned at) {return (uint8_t)(at*17+3);}
static int read_bus(void *context,uint8_t *p,unsigned n) {
    struct fake *s=context;assert(s->current<4);s->reads++;
    struct packet *q=&s->packet[s->current];
    if(!s->offset) {
        assert(n==4);p[0]=0xa5;p[1]=(uint8_t)q->code;
        p[2]=(uint8_t)q->length;p[3]=(uint8_t)(q->length>>8);
        if(!q->length)s->current++;else s->offset=1;
        return 0;
    }
    unsigned at=s->offset-1,total=q->length+1+q->tail,remaining=total-at;
    unsigned count=remaining>DADA_TCM_READ_CHUNK?DADA_TCM_READ_CHUNK:remaining;
    assert(n==count+2);p[0]=0xa5;p[1]=3;
    for(unsigned i=0;i<count;i++) {
        unsigned offset=at+i;
        p[i+2]=offset<q->length?data_byte(offset):offset==q->length?
            (s->bad_eom?0:0x5a):q->trailer[offset-q->length-1];
        if(s->bad_final && offset==total-1)p[i+2]=0;
    }
    at+=count;
    if(at==total || (at+1==total && total%DADA_TCM_READ_CHUNK==1)) {
        s->current++;s->offset=0;
    } else s->offset=at+1;
    return 0;
}
static int write_bus(void *context,const uint8_t *p,unsigned n) {
    struct fake *s=context;assert(n<=sizeof(s->written));
    memcpy(s->written,p,n);s->written_length=n;s->writes++;return 0;
}
static void delay_bus(void *context,unsigned us) {(void)context;assert(us==100 || us==5000);}
static unsigned tail_bytes(unsigned features) {
    unsigned n=(features&DADA_TCM_CRC?2u:0u)+(features&DADA_TCM_EXTRA_RC?1u:0u);
    return n?n+1:0;
}
int main(void) {
    unsigned checks=0;
#define CHECK(x) do {assert(x);checks++;} while(0)
    struct fake f={0};struct dada_tcm_message msg;
    struct dada_tcm_bus b={&f,read_bus,write_bus,delay_bus};
    CHECK(dada_tcm_crc16((const uint8_t *)"123456789",9,0xffff)==0x29b1);
    for(unsigned features=0;features<4;features++) {
        for(unsigned length=1;length<=4096;length=length<250?length+1:length+127) {
            memset(&f,0,sizeof(f));unsigned tail=tail_bytes(features);
            f.packet[0]=(struct packet){length,0xc0,tail,{0x34,0x12,0x81,0x5a}};
            if(tail)f.packet[0].trailer[tail-1]=0x5a;
            CHECK(!dada_tcm_read_message_features(&b,&msg,features));
            CHECK(msg.length==length && msg.code==0xc0 && msg.trailer_length==tail && f.current==1);
            for(unsigned j=0;j<length;j++)assert(msg.data[j]==data_byte(j));
        }
    }
    /* Startup probe reads four extra positions even when they are filler. */
    for(unsigned features=0;features<4;features++) {
        memset(&f,0,sizeof(f));
        f.packet[0]=(struct packet){28,0x10,4,{0x5a,0x5a,0x5a,0x5a}};
        if(features&DADA_TCM_CRC){f.packet[0].trailer[0]=0x34;f.packet[0].trailer[1]=0x12;}
        if(features&DADA_TCM_EXTRA_RC)f.packet[0].trailer[2]=0x81;
        unsigned detected=99;
        CHECK(!dada_tcm_read_message_features(&b,&msg,3));
        CHECK(!dada_tcm_detect_features(&msg,&detected) && detected==features && !f.writes);
    }
    memset(&f,0,sizeof(f));f.packet[0]=(struct packet){28,0x10,4,{1,2,3,0x5a}};
    f.bad_eom=1;CHECK(dada_tcm_read_message_features(&b,&msg,3)==DADA_TCM_PROTOCOL);
    memset(&f,0,sizeof(f));f.packet[0]=(struct packet){28,0x10,4,{1,2,3,0x5a}};
    f.bad_final=1;CHECK(dada_tcm_read_message_features(&b,&msg,3)==DADA_TCM_PROTOCOL);
    CHECK(dada_tcm_read_message_features(&b,&msg,4)==DADA_TCM_UNSUPPORTED);
    unsigned detected=99;msg.code=1;
    CHECK(dada_tcm_detect_features(&msg,&detected)==DADA_TCM_PROTOCOL && detected==99);
    const uint8_t report=0xc0;
    for(unsigned features=0;features<4;features++) {
        memset(&f,0,sizeof(f));f.packet[0].code=1;
        CHECK(!dada_tcm_command_features(&b,6,&report,1,&msg,features));
        CHECK(f.writes==1 && f.written_length==(features&1?6u:4u));
        CHECK(!memcmp(f.written,"\x06\x01\x00\xc0",4));
        if(features&1) {
            uint16_t crc=dada_tcm_crc16(f.written,4,0xffff);
            CHECK(f.written[4]==(uint8_t)crc && f.written[5]==(uint8_t)(crc>>8));
        }
    }
    uint8_t payload[512]={0};memset(&f,0,sizeof(f));f.packet[0].code=1;
    CHECK(dada_tcm_command_features(&b,0x26,payload,511,&msg,1)==DADA_TCM_SIZE && !f.writes);
    CHECK(!dada_tcm_command_features(&b,0x26,payload,510,&msg,1) && f.written_length==515);
    /* Asynchronous raw data before a legitimate response keeps the framing. */
    memset(&f,0,sizeof(f));f.packet[0]=(struct packet){1708,0xc0,4,{1,2,3,0x5a}};
    f.packet[1]=(struct packet){2,1,4,{4,5,6,0x5a}};
    CHECK(!dada_tcm_command_features(&b,0x23,&report,1,&msg,3));
    CHECK(msg.code==1 && msg.length==2 && f.current==2 && f.writes==1);
    printf("dada r11 stock startup/CRC framing: %u checks passed\n",checks);
}
