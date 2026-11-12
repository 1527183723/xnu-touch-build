/* Observe the CPU producer of the measured 194x194 SDF, without modifying its
 * decoder, allocation, Metal synchronization, pixels or rendering decisions. */
#include <mach/mach.h>
#include <pthread.h>

static __thread void *sdfContext;
static pthread_mutex_t sdfLock=PTHREAD_MUTEX_INITIALIZER;
static unsigned sdfIndex;
static id (*sdfOldImageInfo)(id,SEL,NSUInteger,NSUInteger,NSUInteger,id);
static id (*sdfOldTexturedImage)(id,SEL,id);

static BOOL sdfRead(const void *pointer,void *destination,size_t length) {
    vm_size_t got=0;
    return pointer && vm_read_overwrite(mach_task_self(),(vm_address_t)pointer,
        length,(vm_address_t)destination,&got)==KERN_SUCCESS && got==length;
}
static BOOL sdfIvar(id object,const char *name,void *value,size_t length) {
    Ivar ivar=class_getInstanceVariable(object_getClass(object),name);
    if(!ivar || ivar_getOffset(ivar)<0 || (size_t)ivar_getOffset(ivar)+length>
       class_getInstanceSize(object_getClass(object)))return NO;
    return sdfRead((const char *)(__bridge void *)object+ivar_getOffset(ivar),value,length);
}
static NSString *sdfSave(const void *pointer,size_t length,NSString *name) {
    if(!pointer || !length || length>(2u<<20))return nil;
    NSMutableData *bytes=[NSMutableData dataWithLength:length];
    if(!sdfRead(pointer,bytes.mutableBytes,length))return nil;
    NSString *path=[@"/private/var/tmp/" stringByAppendingString:name];
    return [bytes writeToFile:path options:NSDataWritingAtomic error:nil]?path:nil;
}
static size_t sdfBitmapLength(const void *bitmap,const uint32_t header[4]) {
    if(!(header[1]&1))return header[3]<(2u<<20)-16?16+header[3]:0;
    // In chunked MLEC, word 3 is a chunk count, not a compressed byte size.
    if(!header[3]||header[3]>128)return 0;
    size_t cursor=16;
    for(unsigned n=0;n<header[3];++n) {
        uint32_t chunk[5];
        if(!sdfRead((const char *)bitmap+cursor,chunk,sizeof chunk))return 0;
        BOOL big=chunk[0]==0x4b434243; // big-endian CKBC spelling
        if(!big&&chunk[0]!=0x4342434b)return 0;
        uint32_t stored=big?__builtin_bswap32(chunk[4]):chunk[4];
        if(stored>(2u<<20)-20-cursor)return 0;
        cursor+=20+stored;
    }
    return cursor;
}
static id sdfTexturedImage(id self,SEL sel,id key) {
    id result=sdfOldTexturedImage(self,sel,key);
    if(!sdfContext || ![result respondsToSelector:sel_registerName("textureImages")])return result;
    NSMutableDictionary *context=(__bridge id)sdfContext;
    id images=((id(*)(id,SEL))objc_msgSend)(result,sel_registerName("textureImages"));
    if(![images isKindOfClass:NSArray.class])return result;
    for(id helper in images) {
        if(strcmp(class_getName(object_getClass(helper)),"CSITextureHelper"))continue;
        uint32_t w=0,h=0,format=0;size_t row=0;void *bitmap=NULL;
        if(!sdfIvar(helper,"width",&w,sizeof w)||!sdfIvar(helper,"height",&h,sizeof h)||
           !sdfIvar(helper,"sourceRowbytes",&row,sizeof row)||
           !sdfIvar(helper,"pixelFormat",&format,sizeof format)||
           !sdfIvar(helper,"bmp",&bitmap,sizeof bitmap))continue;
        if(w!=194||h!=194||row!=784)continue;
        pthread_mutex_lock(&sdfLock);
        unsigned index=sdfIndex++;
        pthread_mutex_unlock(&sdfLock);
        if(index>=16)continue;
        uint32_t header[4]={0};
        if(!sdfRead(bitmap,header,sizeof header))continue;
        NSMutableDictionary *entry=[@{@"index":@(index),@"width":@(w),@"height":@(h),
            @"row":@(row),@"format":@(format),@"signature":@(header[0]),
            @"flags":@(header[1]),@"compression":@(header[2]),@"storedBytes":@(header[3])} mutableCopy];
        entry[@"bitmapPointer"]=[NSString stringWithFormat:@"%p",bitmap];
        size_t bitmapLength=sdfBitmapLength(bitmap,header);
        entry[@"capturedSourceBytes"]=@(bitmapLength);
        if(bitmapLength) {
            NSString *path=sdfSave(bitmap,bitmapLength,
                [NSString stringWithFormat:@"icon-sdf-%02u-compressed.raw",index]);
            if(path)entry[@"compressed"]=path;
        }
        [context[@"sources"] addObject:entry];
    }
    return result;
}
static id sdfImageInfo(id self,SEL sel,NSUInteger level,NSUInteger element,NSUInteger face,id allocator) {
    NSMutableDictionary *context=[@{@"sources":[NSMutableArray array]} mutableCopy];
    void *previous=sdfContext;
    sdfContext=(__bridge void *)context;
    id result;
    @try { result=sdfOldImageInfo(self,sel,level,element,face,allocator); }
    @finally { sdfContext=previous; }
    if(![context[@"sources"] count])return result;
    context[@"level"]=@(level);context[@"element"]=@(element);context[@"face"]=@(face);
    context[@"allocatorClass"]=@(class_getName(object_getClass(allocator))?:"nil");
    if(result && [result respondsToSelector:sel_registerName("buffer")]) {
        id buffer=((id(*)(id,SEL))objc_msgSend)(result,sel_registerName("buffer"));
        context[@"bufferClass"]=@(class_getName(object_getClass(buffer))?:"nil");
        // Only this exact Swift Buffer's runtime-declared begin/end range is read.
        if(!strcmp(class_getName(object_getClass(buffer))?:"", "_TtCV13IconRendering7Texture6Buffer")) {
            uintptr_t range[2]={0};
            if(sdfIvar(buffer,"bytes",range,sizeof range) && range[1]>=range[0] &&
               range[1]-range[0]==152096) {
                unsigned index=[context[@"sources"][0][@"index"] unsignedIntValue];
                NSString *path=sdfSave((void *)range[0],range[1]-range[0],
                    [NSString stringWithFormat:@"icon-sdf-%02u-decoded.raw",index]);
                if(path)context[@"decoded"]=path;
                context[@"length"]=@(range[1]-range[0]);
                context[@"pointer"]=[NSString stringWithFormat:@"%p",(void *)range[0]];
            }
        }
        for(NSString *name in @[@"bytesPerRow",@"bytesPerImage",@"offset"]) {
            SEL field=sel_registerName(name.UTF8String);
            if([result respondsToSelector:field])
                context[name]=@(((NSUInteger(*)(id,SEL))objc_msgSend)(result,field));
        }
    }
    NSData *json=[NSJSONSerialization dataWithJSONObject:context options:0 error:nil];
    pthread_mutex_lock(&sdfLock);
    FILE *f=fopen("/private/var/tmp/icon-sdf-producer.jsonl","a");
    if(f){fwrite(json.bytes,1,json.length,f);fputc('\n',f);fclose(f);}
    pthread_mutex_unlock(&sdfLock);
    return result;
}
static void sdfInstall(unsigned attempt) {
    Class rendition=objc_getClass("_CUIThemeTextureRendition");
    Method image=class_getInstanceMethod(rendition,
        sel_registerName("provideImageInfoAtLevel:element:face:withBufferAllocator:"));
    Method texture=class_getInstanceMethod(rendition,sel_registerName("_texturedImageWithKey:"));
    if(!image||!texture) {
        if(attempt<300)dispatch_after(dispatch_time(DISPATCH_TIME_NOW,100*NSEC_PER_MSEC),
            dispatch_get_main_queue(),^{sdfInstall(attempt+1);});
        return;
    }
    sdfOldTexturedImage=(void *)method_setImplementation(texture,(IMP)sdfTexturedImage);
    sdfOldImageInfo=(void *)method_setImplementation(image,(IMP)sdfImageInfo);
    fprintf(stderr,"icon-sdf: bounded CPU producer trace installed pid=%d\n",getpid());
}
__attribute__((constructor)) static void sdfStart(void) {
    if(strcmp(getprogname(),"SpringBoard"))return;
    dispatch_async(dispatch_get_main_queue(),^{sdfInstall(0);});
}
