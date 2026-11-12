#include <sys/dada_touch.h>
#include <assert.h>
#include <stdio.h>
#include <string.h>
struct events {unsigned n,type,id,x,y;};
static void emit(void *p,unsigned type,unsigned id,unsigned x,unsigned y) {
    struct events *e=p;e->n++;e->type=type;e->id=id;e->x=x;e->y=y;
}
static void bits(uint8_t *p,unsigned at,unsigned n,unsigned value) {
    for(unsigned i=0;i<n;i++) {
        unsigned mask=1u<<((at+i)%8);
        p[(at+i)/8]=(uint8_t)((p[(at+i)/8]&~mask)|(((value>>i)&1u)?mask:0));
    }
}
int main(void) {
    unsigned checks=0;
#define CHECK(x) do {assert(x);checks++;}while(0)
    struct dada_tcm_state s={0};s.max_x=1200;s.max_y=2670;s.objects=2;
    struct events e={0};uint8_t p[64]={0},config[512]={0};
    memcpy(config,dada_tcm_report_config,12);config[11]=0x81;config[127]=0xa7;
    CHECK(!dada_tcm_install_config(&s,config,128));
    CHECK(s.config_length==11 && s.loop_start==1 && s.loop_end==9 && !s.active_only);
    p[0]=0x10;p[1]=100;p[3]=200;p[5]=1;
    CHECK(!dada_tcm_frame(&s,p,10,emit,&e));
    CHECK(e.n==1 && e.type==1 && e.id==0 && e.x==100*65535u/1200 && e.y==200*65535u/2670);
    CHECK(dada_tcm_frame(&s,p,9,emit,&e)==DADA_TCM_SIZE && e.n==1 && s.contacts[0].down);
    p[1]=101;CHECK(!dada_tcm_frame(&s,p,10,emit,&e) && e.n==2 && e.type==2);
    p[0]=0;CHECK(!dada_tcm_frame(&s,p,10,emit,&e) && e.n==3 && e.type==3);
    /* Stock active-object loop, timestamp, active count, bit fields and padding. */
    uint8_t active[]={5,16,0x18,4,4,1,6,4,7,4,8,12,9,12,3,0};
    CHECK(!dada_tcm_install_config(&s,active,sizeof(active)) && s.active_only);
    memset(p,0,sizeof(p));bits(p,16,4,1);bits(p,24,4,1);bits(p,28,4,1);
    bits(p,32,12,1000);bits(p,44,12,2000);
    CHECK(!dada_tcm_frame(&s,p,7,emit,&e));
    CHECK(e.n==4 && e.type==1 && e.id==1 && e.x==1000*65535u/1200 && e.y==2000*65535u/2670);
    bits(p,32,12,1201);CHECK(dada_tcm_frame(&s,p,7,emit,&e)==DADA_TCM_PROTOCOL && e.n==4);
    CHECK(dada_tcm_frame(&s,p,6,emit,&e)==DADA_TCM_SIZE && e.n==4);
    bits(p,16,4,3);CHECK(dada_tcm_frame(&s,p,7,emit,&e)==DADA_TCM_PROTOCOL && e.n==4);
    bits(p,16,4,0);CHECK(!dada_tcm_frame(&s,p,3,emit,&e) && e.n==5 && e.type==3);
    /* No explicit active count: derive only a unique bounded loop length. */
    uint8_t inferred[]={1,6,4,7,4,8,16,9,16,3,0};
    CHECK(!dada_tcm_install_config(&s,inferred,sizeof(inferred)));
    memset(p,0,sizeof(p));p[0]=0x10;p[1]=100;p[3]=200;
    CHECK(!dada_tcm_frame(&s,p,5,emit,&e) && e.n==6 && e.type==1);
    p[5]=0x11;p[6]=110;p[8]=210;
    CHECK(!dada_tcm_frame(&s,p,10,emit,&e) && e.n==7 && e.id==1);
    p[5]=0x10;CHECK(dada_tcm_frame(&s,p,10,emit,&e)==DADA_TCM_PROTOCOL && e.n==7);
    CHECK(!dada_tcm_frame(&s,p,0,emit,&e) && e.n==9 && e.type==3);
    /* Unknown scalar metadata is skipped by declared bit length, not decoded as fingers. */
    uint8_t extra[]={0x7f,64,2,6,4,7,4,8,16,9,16,3,0};
    CHECK(!dada_tcm_install_config(&s,extra,sizeof(extra)));
    memset(p,0,sizeof(p));p[8]=0x10;p[9]=120;p[11]=120;p[13]=1;
    CHECK(!dada_tcm_frame(&s,p,18,emit,&e) && e.n==10);
    uint8_t nested[]={2,1,6,4,7,4,8,16,9,16,3,0};
    CHECK(dada_tcm_install_config(&s,nested,sizeof(nested))==DADA_TCM_UNSUPPORTED && !s.config_length);
    uint8_t missing[]={2,6,4,7,4,8,16,3,0};
    CHECK(dada_tcm_install_config(&s,missing,sizeof(missing))==DADA_TCM_UNSUPPORTED);
    CHECK(dada_tcm_install_config(&s,dada_tcm_report_config,10)==DADA_TCM_PROTOCOL);
    uint8_t wide[]={2,6,33,7,4,8,16,9,16,3,0};
    CHECK(dada_tcm_install_config(&s,wide,sizeof(wide))==DADA_TCM_UNSUPPORTED);
    CHECK(dada_tcm_install_config(&s,config,513)==DADA_TCM_SIZE);
    CHECK(dada_tcm_install_config(&s,NULL,1)==DADA_TCM_SIZE);
    CHECK(!dada_tcm_install_config(&s,config,sizeof(config)) && s.config_length==11);
    CHECK(dada_tcm_frame(&s,NULL,0,emit,&e)==DADA_TCM_SIZE);
    CHECK(dada_tcm_frame(&s,p,DADA_TCM_PAYLOAD+1,emit,&e)==DADA_TCM_SIZE);
    /* Exact configuration captured from the dada controller on r9. */
    const uint8_t stock[]={0x10,8,0x1b,48,0x18,8,1,6,4,7,4,8,16,9,16,0x0a,16,0x0b,8,0x0c,8,3,0};
    dada_tcm_release(&s,emit,&e);unsigned previous=e.n;
    CHECK(!dada_tcm_install_config(&s,stock,sizeof(stock)) && s.config_length==23);
    memset(p,0,sizeof(p));p[7]=1;p[8]=0x10;
    bits(p,72,16,600);bits(p,88,16,1335);bits(p,104,16,88);p[15]=4;p[16]=5;
    CHECK(!dada_tcm_frame(&s,p,17,emit,&e) && e.n==previous+1);
    CHECK(e.type==1 && e.id==0 && e.x==32767 && e.y==32767);
    p[7]=0;CHECK(!dada_tcm_frame(&s,p,8,emit,&e) && e.type==3);
    printf("dada r9 stock report configuration: %u checks passed\n",checks);
}
