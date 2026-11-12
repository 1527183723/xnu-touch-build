#!/usr/bin/env python3
"""Exercise the real Warsaw send_touch function with an in-memory HID sink."""
import argparse
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREFIX = r'''
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include "redwood_input.h"
#define KERN_SUCCESS 0
typedef bool Boolean;
typedef void *IOHIDServiceClientRef;
struct event { unsigned mask, count; bool range, touch; double x,y; struct { unsigned id,mask; bool touch; } child[10]; };
typedef struct event *IOHIDEventRef;
static struct { bool touching,edge; double x,y; } fingers[10];
struct cpu { int result; uint64_t begin,end; int64_t user_us,system_us; };
struct touch_timing { struct cpu cpu[3]; uint64_t dispatch_begin,dispatch_end; };
static bool touch_latency;
static unsigned latency_samples;
static uint64_t latency_log_previous;
static unsigned sent;
static struct event last;
static void touch_cpu_snapshot(struct cpu *c) { memset(c,0,sizeof(*c)); }
static uint64_t mach_absolute_time(void) { return 1; }
static uint64_t micros(uint64_t n) { return n; }
static void say(const char *fmt,...) { (void)fmt; }
static IOHIDEventRef IOHIDEventCreateDigitizerEvent(void *a,uint64_t t,unsigned d,unsigned idx,unsigned ident,
unsigned mask,unsigned button,double x,double y,double z,double pressure,double twist,int range,int touch,unsigned opts) {
    (void)a;(void)t;(void)d;(void)idx;(void)ident;(void)button;(void)z;(void)pressure;(void)twist;(void)opts;
    IOHIDEventRef e=calloc(1,sizeof(*e)); e->mask=mask;e->range=range;e->touch=touch;e->x=x;e->y=y;return e;
}
static IOHIDEventRef IOHIDEventCreateDigitizerFingerEvent(void *a,uint64_t t,unsigned idx,unsigned ident,
unsigned mask,double x,double y,double z,double pressure,double twist,int range,int touch,unsigned opts) {
    return IOHIDEventCreateDigitizerEvent(a,t,0,idx,ident,mask,0,x,y,z,pressure,twist,range,touch,opts);
}
static void IOHIDEventSetIntegerValue(IOHIDEventRef e,unsigned field,int value) {(void)e;(void)field;(void)value;}
static void IOHIDEventAppendEvent(IOHIDEventRef hand,IOHIDEventRef finger,unsigned opt) {
    (void)opt; unsigned n=hand->count++;if(n>=10)abort();hand->child[n].mask=finger->mask;hand->child[n].touch=finger->touch;
}
static void CFRelease(IOHIDEventRef e) {free(e);}
static Boolean dispatch_input_event(IOHIDServiceClientRef service, IOHIDEventRef event,struct touch_timing *timing) {
    (void)service;(void)timing;last=*event;sent++;return true;
}
'''
SUFFIX = r'''
static unsigned checks;
#define CHECK(c) do {checks++;if(!(c)){fprintf(stderr,"HID line %d: %s\n",__LINE__,#c);abort();}}while(0)
int main(void) {
    struct redwood_touch t={.time=1,.finger=2,.x=32767,.y=60000,.type=REDWOOD_TOUCH_MOVE};
    send_touch(NULL,&t,1);CHECK(sent==0);
    t.type=REDWOOD_TOUCH_UP;send_touch(NULL,&t,1);CHECK(sent==0);
    t.type=REDWOOD_TOUCH_DOWN;send_touch(NULL,&t,1);
    CHECK(sent==1 && last.touch && last.range && last.count==1);
    CHECK(last.mask==DIGITIZER_TOUCH && fingers[2].touching);
    CHECK(last.x>0.49 && last.x<0.51);
    t.type=REDWOOD_TOUCH_MOVE;t.x=65535;send_touch(NULL,&t,1);
    CHECK(sent==2 && last.mask==DIGITIZER_POSITION && last.x==1);
    t.finger=5;t.type=REDWOOD_TOUCH_DOWN;send_touch(NULL,&t,1);
    CHECK(sent==3 && last.count==2 && fingers[5].touching);
    t.finger=2;t.type=REDWOOD_TOUCH_UP;send_touch(NULL,&t,1);
    CHECK(sent==4 && last.touch && !fingers[2].touching && fingers[5].touching);
    t.finger=5;send_touch(NULL,&t,1);CHECK(sent==5 && !last.touch && !last.range);
    send_touch(NULL,&t,1);CHECK(sent==5);
    t.type=REDWOOD_TOUCH_MOVE;send_touch(NULL,&t,1);CHECK(sent==5);
    t.finger=10;t.type=REDWOOD_TOUCH_DOWN;send_touch(NULL,&t,1);CHECK(sent==5);
    t.finger=0;t.type=999;send_touch(NULL,&t,1);CHECK(sent==5);
    memset(fingers,0,sizeof(fingers));sent=0;
    for(unsigned i=0;i<10;i++){t.finger=i;t.type=REDWOOD_TOUCH_DOWN;send_touch(NULL,&t,1);CHECK(last.count==i+1);}
    for(unsigned i=0;i<10;i++){t.finger=i;t.type=REDWOOD_TOUCH_UP;send_touch(NULL,&t,1);CHECK(last.count==10-i);}
    CHECK(sent==20 && !last.touch);
    printf("PASS: %u HID assertions; orphan samples, real DOWN/MOVE/UP and ten-finger lifecycle\n",checks);
}
'''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--work', type=Path, required=True)
    args = ap.parse_args()
    source = (ROOT / 'userland/src/input26_service.c').read_text()
    body = source[source.index('static void\nsend_touch('):source.index('static void\nsend_button(')]
    defines = '\n'.join(line for line in source.splitlines() if re.match(
        r'#define (MAX_FINGERS|EDGE_\w+|DIGITIZER_\w+|FIELD_\w+)\s', line))
    test = args.work / 'hid-state-test.c'
    binary = args.work / 'hid-state-test'
    test.write_text(PREFIX + '\n' + defines + '\n' + body + SUFFIX)
    with (args.work / 'hid-state-tests.log').open('wb') as log:
        subprocess.run(['clang-21', '-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                        '-fsanitize=address,undefined', '-I', str(ROOT / 'userland/src'),
                        str(test), '-o', str(binary)], stdout=log, stderr=log, check=True)
        subprocess.run([str(binary)], stdout=log, stderr=log, timeout=30, check=True)
    print((args.work / 'hid-state-tests.log').read_text())


if __name__ == '__main__':
    main()
