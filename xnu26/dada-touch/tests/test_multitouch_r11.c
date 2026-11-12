#include <sys/dada_touch.h>
#include <assert.h>
#include <stdio.h>
#include <string.h>
struct events {unsigned down,move,up,mask;};
static void emit(void *ctx,unsigned type,unsigned id,unsigned x,unsigned y) {
    struct events *e=ctx;assert(id<10 && x<=65535 && y<=65535);
    if(type==1){e->down++;e->mask|=1u<<id;}
    else if(type==2)e->move++;
    else if(type==3){e->up++;e->mask&=~(1u<<id);}
    else assert(0);
}
static void put16(uint8_t *p,unsigned v) {p[0]=(uint8_t)v;p[1]=(uint8_t)(v>>8);}
int main(void) {
    unsigned checks=0;
#define CHECK(x) do {assert(x);checks++;} while(0)
    const uint8_t stock[]={0x10,8,0x1b,48,0x18,8,1,6,4,7,4,8,16,9,16,0x0a,16,0x0b,8,0x0c,8,3,0};
    struct dada_tcm_state s={0};s.max_x=1200;s.max_y=2670;s.objects=10;
    struct events e={0};uint8_t p[98]={0};
    CHECK(!dada_tcm_install_config(&s,stock,sizeof(stock)));
    p[7]=10;
    for(unsigned i=0;i<10;i++) {
        uint8_t *q=p+8+9*i;unsigned id=9-i;
        q[0]=(uint8_t)((i%2?2u:1u)<<4|id);
        put16(q+1,id*120);put16(q+3,id*267);put16(q+5,100);q[7]=4;q[8]=5;
    }
    CHECK(!dada_tcm_frame(&s,p,sizeof(p),emit,&e));
    CHECK(e.down==10 && e.mask==1023 && !e.up && !e.move);
    for(unsigned i=0;i<10;i++)CHECK(s.contacts[i].down && s.contacts[i].x==i*65535u/10);
    CHECK(!dada_tcm_frame(&s,p,sizeof(p),emit,&e) && e.down==10 && !e.move);
    put16(p+9,1199);
    CHECK(!dada_tcm_frame(&s,p,sizeof(p),emit,&e) && e.move==1);
    /* Palm classification removes that slot; glove classification stays touch. */
    p[8+9*4]=(uint8_t)(6u<<4|5u);
    CHECK(!dada_tcm_frame(&s,p,sizeof(p),emit,&e) && e.up==1 && !s.contacts[5].down);
    p[8+9*4]=(uint8_t)(2u<<4|5u);
    CHECK(!dada_tcm_frame(&s,p,sizeof(p),emit,&e) && e.down==11 && s.contacts[5].down);
    /* Duplicate slot, invalid active count, truncated frame: no partial events. */
    unsigned before=e.down+e.move+e.up;p[8+9]=p[8];
    CHECK(dada_tcm_frame(&s,p,sizeof(p),emit,&e)==DADA_TCM_PROTOCOL);
    CHECK(e.down+e.move+e.up==before && e.mask==1023);
    p[8+9]=(uint8_t)(2u<<4|8u);p[7]=11;
    CHECK(dada_tcm_frame(&s,p,sizeof(p),emit,&e)==DADA_TCM_PROTOCOL);
    p[7]=10;CHECK(dada_tcm_frame(&s,p,sizeof(p)-1,emit,&e)==DADA_TCM_SIZE);
    p[7]=0;CHECK(!dada_tcm_frame(&s,p,8,emit,&e) && e.up==11 && !e.mask);
    dada_tcm_release(&s,emit,&e);CHECK(e.up==11);
    printf("dada r11 ten-finger/glove/palm/release: %u checks passed\n",checks);
}
