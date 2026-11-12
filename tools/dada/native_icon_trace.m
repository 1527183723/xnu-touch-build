/* Bounded RAM-session trace of native icon inputs. Calls all original methods
 * unchanged, writes metadata only, and never replaces a shader or pixel. */
#import <Metal/Metal.h>
#include <pthread.h>

static pthread_mutex_t traceLock=PTHREAD_MUTEX_INITIALIZER;
static unsigned traceCount;
static char tracePassKey,tracePipeKey,traceNameKey;
static NSMutableArray *traceSources;
static BOOL traceSourceDone;
static NSDictionary *traceTexture(id<MTLTexture> t);
static void traceKeep(id<MTLTexture> t){
    if(!t||traceSourceDone||t.width>512||t.height>512)return;
    unsigned kind=t.pixelFormat==90?1:t.pixelFormat==204?2:
        (t.pixelFormat==80&&[t.label isEqual:@"rb:noise-texture"])?3:0;
    if(!kind)return;
    @synchronized(traceSources){
        unsigned count=0;
        for(NSDictionary *d in traceSources){if(d[@"texture"]==t)return;if([d[@"kind"] unsignedIntValue]==kind)++count;}
        if(count>=(kind==1?8:kind==2?12:1))return;
        NSUInteger row=t.width*4,rows=t.height;
        if(t.pixelFormat==204){row=((t.width+3)/4)*16;rows=(t.height+3)/4;}
        NSMutableData *bytes=[NSMutableData dataWithLength:row*rows];
        /* Capture while it is a bound input. Heap resources may become aliasable
         * after this draw, so a later readback is not valid evidence. */
        [t getBytes:bytes.mutableBytes bytesPerRow:row fromRegion:MTLRegionMake2D(0,0,t.width,t.height) mipmapLevel:0];
        NSString *path=[NSString stringWithFormat:@"/private/var/tmp/icon-input-%02lu.raw",(unsigned long)traceSources.count];
        BOOL ok=[bytes writeToFile:path options:NSDataWritingAtomic error:nil];
        NSMutableDictionary *meta=[traceTexture(t) mutableCopy];
        meta[@"raw"]=path;meta[@"bytes"]=@(bytes.length);meta[@"row"]=@(row);
        meta[@"saved"]=@(ok);meta[@"capturedAtBinding"]=@YES;
        [traceSources addObject:@{@"texture":t,@"kind":@(kind),@"metadata":meta}];
    }
}
static void traceDumpSources(void){
    @autoreleasepool {
        NSArray *saved;@synchronized(traceSources){traceSourceDone=YES;saved=[traceSources copy];}
        NSMutableArray *records=[NSMutableArray array];
        for(NSDictionary *d in saved){
            [records addObject:d[@"metadata"]];
        }
        NSData *data=[NSJSONSerialization dataWithJSONObject:records options:0 error:nil];
        [data writeToFile:@"/private/var/tmp/icon-input-sources.json" options:NSDataWritingAtomic error:nil];
        fprintf(stderr,"icon-inputs: saved %lu source textures\n",(unsigned long)records.count);
        @synchronized(traceSources){[traceSources removeAllObjects];}
    }
}
static void traceRecord(NSDictionary *entry) {
    pthread_mutex_lock(&traceLock);
    if(traceCount++<1600){
        NSData *line=[NSJSONSerialization dataWithJSONObject:entry options:0 error:nil];
        FILE *f=fopen("/private/var/tmp/icon-gpu-inputs.jsonl","a");
        if(f){fwrite(line.bytes,1,line.length,f);fputc('\n',f);fclose(f);}
    }
    pthread_mutex_unlock(&traceLock);
}
static NSDictionary *traceTexture(id<MTLTexture> t) {
    if(!t)return @{};
    id<MTLBuffer> b=t.buffer;
    return @{@"object":[NSString stringWithFormat:@"%p",t],@"w":@(t.width),@"h":@(t.height),
        @"format":@(t.pixelFormat),@"mips":@(t.mipmapLevelCount),@"usage":@(t.usage),
        @"storage":@(t.storageMode),@"samples":@(t.sampleCount),
        @"surface":@(t.iosurface?IOSurfaceGetID(t.iosurface):0),@"label":t.label?:@"",
        @"bufferLength":@(b.length),@"bufferOffset":@(t.bufferOffset),@"bufferRow":@(t.bufferBytesPerRow)};
}
static id (*traceOldPass)(id,SEL,MTLRenderPassDescriptor *);
static id tracePass(id self,SEL sel,MTLRenderPassDescriptor *d) {
    id encoder=traceOldPass(self,sel,d);
    BOOL xr=NO;NSMutableArray *attachments=[NSMutableArray array];
    for(unsigned i=0;i<3;++i){
        id<MTLTexture> t=d.colorAttachments[i].texture;
        if(t){[attachments addObject:traceTexture(t)];xr|=t.pixelFormat==550||t.pixelFormat==551;}
    }
    if(xr&&encoder){
        objc_setAssociatedObject(encoder,&tracePassKey,@YES,OBJC_ASSOCIATION_RETAIN_NONATOMIC);
        traceRecord(@{@"event":@"pass",@"encoder":[NSString stringWithFormat:@"%p",encoder],@"targets":attachments});
    }
    return encoder;
}
static id (*traceOldPipeline)(id,SEL,MTLRenderPipelineDescriptor *,MTLPipelineOption,id *,NSError **);
static id tracePipeline(id self,SEL sel,MTLRenderPipelineDescriptor *d,MTLPipelineOption options,id *reflection,NSError **error){
    id p=traceOldPipeline(self,sel,d,options,reflection,error);
    if(p){
        NSDictionary *meta=@{@"label":d.label?:@"",@"vertex":d.vertexFunction.name?:@"",@"fragment":d.fragmentFunction.name?:@""};
        objc_setAssociatedObject(p,&traceNameKey,meta,OBJC_ASSOCIATION_RETAIN_NONATOMIC);
    }
    return p;
}
static void (*traceOldState)(id,SEL,id);
static void traceState(id self,SEL sel,id pipeline){
    traceOldState(self,sel,pipeline);
    objc_setAssociatedObject(self,&tracePipeKey,objc_getAssociatedObject(pipeline,&traceNameKey),OBJC_ASSOCIATION_RETAIN_NONATOMIC);
}
static void (*traceOldTexture)(id,SEL,id<MTLTexture>,NSUInteger,uint32_t);
static void traceInput(id self,SEL sel,id<MTLTexture> t,NSUInteger index,uint32_t stage){
    if(t&&objc_getAssociatedObject(self,&tracePassKey)){
        traceKeep(t);
        traceRecord(@{@"event":@"texture",@"encoder":[NSString stringWithFormat:@"%p",self],
            @"stage":@(stage),@"index":@(index),@"pipeline":objc_getAssociatedObject(self,&tracePipeKey)?:@{},@"texture":traceTexture(t)});
    }
    traceOldTexture(self,sel,t,index,stage);
}
static void (*traceOldDraw)(id,SEL,MTLPrimitiveType,NSUInteger,NSUInteger,NSUInteger,NSUInteger);
static void traceDraw(id self,SEL sel,MTLPrimitiveType type,NSUInteger start,NSUInteger count,NSUInteger instances,NSUInteger base){
    if(objc_getAssociatedObject(self,&tracePassKey))
        traceRecord(@{@"event":@"draw",@"encoder":[NSString stringWithFormat:@"%p",self],
            @"pipeline":objc_getAssociatedObject(self,&tracePipeKey)?:@{},@"vertices":@(count)});
    traceOldDraw(self,sel,type,start,count,instances,base);
}
static void traceInstall(unsigned attempt){
    Class dev=objc_getClass("NativeBridgeMetalDevice"),cb=objc_getClass("NBCommandBuffer"),
        re=objc_getClass("NBRenderEncoder"),en=objc_getClass("NBEncoder");
    if(!dev||!cb||!re||!en){
        if(attempt<200)dispatch_after(dispatch_time(DISPATCH_TIME_NOW,100*NSEC_PER_MSEC),dispatch_get_main_queue(),^{traceInstall(attempt+1);});
        return;
    }
    struct Hook {Class cls;const char *name;IMP impl;IMP *old;};
    struct Hook hooks[]={
        {dev,"newRenderPipelineStateWithDescriptor:options:reflection:error:",(IMP)tracePipeline,(IMP *)&traceOldPipeline},
        {cb,"renderCommandEncoderWithDescriptor:",(IMP)tracePass,(IMP *)&traceOldPass},
        {re,"setRenderPipelineState:",(IMP)traceState,(IMP *)&traceOldState},
        {en,"texture:index:stage:",(IMP)traceInput,(IMP *)&traceOldTexture},
        {re,"drawPrimitives:vertexStart:vertexCount:instanceCount:baseInstance:",(IMP)traceDraw,(IMP *)&traceOldDraw}};
    for(unsigned i=0;i<sizeof hooks/sizeof hooks[0];++i){
        Method m=class_getInstanceMethod(hooks[i].cls,sel_registerName(hooks[i].name));
        if(!m){traceRecord(@{@"event":@"missing",@"selector":@(hooks[i].name)});return;}
    }
    traceSources=[NSMutableArray array];
    for(unsigned i=0;i<sizeof hooks/sizeof hooks[0];++i){
        Method m=class_getInstanceMethod(hooks[i].cls,sel_registerName(hooks[i].name));
        *hooks[i].old=method_setImplementation(m,hooks[i].impl);
    }
    traceRecord(@{@"event":@"installed",@"pid":@(getpid()),@"readOnlyInputs":@YES});
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW,40*NSEC_PER_SEC),dispatch_get_main_queue(),^{traceDumpSources();});
}
__attribute__((constructor)) static void traceStart(void){
    if(strcmp(getprogname(),"SpringBoard"))return;
    dispatch_async(dispatch_get_main_queue(),^{traceInstall(0);});
}
