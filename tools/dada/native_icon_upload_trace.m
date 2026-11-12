/* Copy bounded CPU upload inputs before the original call. No GPU readback,
 * command synchronization, resource retention, pixel replacement or shader edits. */
#import <Metal/Metal.h>
#include <pthread.h>
#include <execinfo.h>
static pthread_mutex_t uploadLock=PTHREAD_MUTEX_INITIALIZER;
static unsigned uploadCount;
static NSMutableSet *uploadSeen;
static NSMapTable *uploadBuffers;
static unsigned uploadCommits;
static char uploadOriginKey;
static unsigned uploadOrigins;
static id (*uploadOldNoCopy)(id,SEL,void *,NSUInteger,MTLResourceOptions,id);
static id (*uploadOldCopy)(id,SEL,const void *,NSUInteger,MTLResourceOptions);
static NSDictionary *uploadOrigin(const void *bytes,NSUInteger length,NSString *kind){
    if(!bytes||length<150000||length>160000)return nil;
    unsigned index;
    @synchronized(uploadSeen){if(uploadOrigins>=16)return nil;index=uploadOrigins++;}
    NSString *path=[NSString stringWithFormat:@"/private/var/tmp/icon-origin-%02u.raw",index];
    NSData *data=[NSData dataWithBytes:bytes length:length];
    BOOL ok=[data writeToFile:path options:NSDataWritingAtomic error:nil];
    void *frames[14];int count=backtrace(frames,14);NSMutableArray *stack=[NSMutableArray array];
    for(int i=1;i<count;++i){Dl_info info={0};if(dladdr(frames[i],&info)&&info.dli_fbase)
        [stack addObject:@{@"image":@(info.dli_fname?:"?"),@"symbol":@(info.dli_sname?:"?"),
            @"offset":@((uintptr_t)frames[i]-(uintptr_t)info.dli_fbase)}];}
    return @{@"raw":path,@"saved":@(ok),@"length":@(length),@"pointer":[NSString stringWithFormat:@"%p",bytes],@"kind":kind,@"stack":stack};
}
static id uploadNoCopy(id self,SEL sel,void *bytes,NSUInteger length,MTLResourceOptions options,id deallocator){
    NSDictionary *origin=uploadOrigin(bytes,length,@"no-copy");
    id b=uploadOldNoCopy(self,sel,bytes,length,options,deallocator);
    if(b&&origin)objc_setAssociatedObject(b,&uploadOriginKey,origin,OBJC_ASSOCIATION_RETAIN_NONATOMIC);
    return b;
}
static id uploadCopy(id self,SEL sel,const void *bytes,NSUInteger length,MTLResourceOptions options){
    NSDictionary *origin=uploadOrigin(bytes,length,@"copy");
    id b=uploadOldCopy(self,sel,bytes,length,options);
    if(b&&origin)objc_setAssociatedObject(b,&uploadOriginKey,origin,OBJC_ASSOCIATION_RETAIN_NONATOMIC);
    return b;
}

static void uploadCapture(NSString *event,MTLPixelFormat pf,NSUInteger w,NSUInteger h,
                           const void *bytes,NSUInteger row,NSUInteger length,NSUInteger offset,
                           NSString *identity){
    if(pf!=90||!bytes||!w||!h||w>512||h>512||row<w*4||row>65536||offset>length||
       (h-1)*row+w*4>length-offset)return;
    pthread_mutex_lock(&uploadLock);
    if(uploadCount<12&&![uploadSeen containsObject:identity]){
        [uploadSeen addObject:identity];unsigned index=uploadCount++;
        NSMutableData *copy=[NSMutableData dataWithLength:w*h*4];
        for(NSUInteger y=0;y<h;++y)memcpy((char *)copy.mutableBytes+y*w*4,(const char *)bytes+offset+y*row,w*4);
        NSString *path=[NSString stringWithFormat:@"/private/var/tmp/icon-upload-%02u.raw",index];
        BOOL ok=[copy writeToFile:path options:NSDataWritingAtomic error:nil];
        NSDictionary *record=@{@"event":event,@"format":@(pf),@"w":@(w),@"h":@(h),
            @"inputRow":@(row),@"inputLength":@(length),@"offset":@(offset),@"raw":path,
            @"pid":@(getpid()),@"saved":@(ok),@"identity":identity};
        NSData *line=[NSJSONSerialization dataWithJSONObject:record options:0 error:nil];
        FILE *f=fopen("/private/var/tmp/icon-cpu-uploads.jsonl","a");
        if(f){fwrite(line.bytes,1,line.length,f);fputc('\n',f);fclose(f);}
    }
    pthread_mutex_unlock(&uploadLock);
}
static void (*uploadOldReplace)(id,SEL,MTLRegion,NSUInteger,NSUInteger,const void *,NSUInteger,NSUInteger);
static void uploadReplace(id<MTLTexture> self,SEL sel,MTLRegion r,NSUInteger level,NSUInteger slice,const void *bytes,NSUInteger row,NSUInteger image){
    NSString *identity=[NSString stringWithFormat:@"%p:%lu:%lu:%lu,%lu",self,(unsigned long)level,(unsigned long)slice,(unsigned long)r.origin.x,(unsigned long)r.origin.y];
    uploadCapture(@"replaceRegion",self.pixelFormat,r.size.width,r.size.height,bytes,row,row*r.size.height,0,identity);
    uploadOldReplace(self,sel,r,level,slice,bytes,row,image);
}
static id (*uploadOldBufferTexture)(id,SEL,MTLTextureDescriptor *,NSUInteger,NSUInteger);
static id uploadBufferTexture(id<MTLBuffer> self,SEL sel,MTLTextureDescriptor *d,NSUInteger offset,NSUInteger row){
    NSString *identity=[NSString stringWithFormat:@"%p:%lu:%lu",self,(unsigned long)offset,(unsigned long)row];
    uploadCapture(@"bufferTextureCreate",d.pixelFormat,d.width,d.height,self.contents,row,self.length,offset,identity);
    if(d.pixelFormat==90){
        NSDictionary *origin=objc_getAssociatedObject(self,&uploadOriginKey);
        NSMutableDictionary *meta=[@{@"textureFormat":@(d.pixelFormat),@"w":@(d.width),@"h":@(d.height),@"row":@(row),@"buffer":[NSString stringWithFormat:@"%p",self],@"contents":[NSString stringWithFormat:@"%p",self.contents]} mutableCopy];
        if(origin)meta[@"origin"]=origin;
        NSData *line=[NSJSONSerialization dataWithJSONObject:meta options:0 error:nil];
        FILE *f=fopen("/private/var/tmp/icon-buffer-origins.jsonl","a");if(f){fwrite(line.bytes,1,line.length,f);fputc('\n',f);fclose(f);}
    }
    if(d.pixelFormat==90&&d.width==194&&d.height==194){
        @synchronized(uploadBuffers){
            if(uploadBuffers.count<8)[uploadBuffers setObject:@{@"w":@(d.width),@"h":@(d.height),@"offset":@(offset),@"row":@(row)} forKey:self];
        }
    }
    return uploadOldBufferTexture(self,sel,d,offset,row);
}
static void (*uploadOldCommit)(id,SEL);
static void uploadCommit(id self,SEL sel){
    @synchronized(uploadBuffers){
        if(uploadBuffers.count&&uploadCommits<6){
            unsigned frame=uploadCommits++;
            for(id<MTLBuffer> buffer in uploadBuffers.keyEnumerator){
                NSDictionary *d=[uploadBuffers objectForKey:buffer];
                NSUInteger w=[d[@"w"] unsignedIntegerValue],h=[d[@"h"] unsignedIntegerValue],
                    row=[d[@"row"] unsignedIntegerValue],off=[d[@"offset"] unsignedIntegerValue];
                if(!buffer.contents||off>buffer.length||(h-1)*row+w*4>buffer.length-off)continue;
                NSMutableData *copy=[NSMutableData dataWithLength:w*h*4];
                for(NSUInteger y=0;y<h;++y)memcpy((char *)copy.mutableBytes+y*w*4,(const char *)buffer.contents+off+y*row,w*4);
                NSString *path=[NSString stringWithFormat:@"/private/var/tmp/icon-commit-%u-%p.raw",frame,buffer];
                BOOL ok=[copy writeToFile:path options:NSDataWritingAtomic error:nil];
                NSDictionary *meta=@{@"pid":@(getpid()),@"frame":@(frame),@"w":@(w),@"h":@(h),@"raw":path,@"saved":@(ok)};
                NSData *line=[NSJSONSerialization dataWithJSONObject:meta options:0 error:nil];
                FILE *f=fopen("/private/var/tmp/icon-cpu-commits.jsonl","a");if(f){fwrite(line.bytes,1,line.length,f);fputc('\n',f);fclose(f);}
            }
        }
    }
    uploadOldCommit(self,sel);
}
static void uploadInstall(unsigned attempt){
    Class tex=objc_getClass("NBTexture"),buf=objc_getClass("NBBuffer"),cb=objc_getClass("NBCommandBuffer"),dev=objc_getClass("NativeBridgeMetalDevice");
    if(!tex||!buf||!cb||!dev){
        if(attempt<300)dispatch_after(dispatch_time(DISPATCH_TIME_NOW,100*NSEC_PER_MSEC),dispatch_get_main_queue(),^{uploadInstall(attempt+1);});
        return;
    }
    Method replace=class_getInstanceMethod(tex,sel_registerName("replaceRegion:mipmapLevel:slice:withBytes:bytesPerRow:bytesPerImage:"));
    Method make=class_getInstanceMethod(buf,sel_registerName("newTextureWithDescriptor:offset:bytesPerRow:"));
    Method commit=class_getInstanceMethod(cb,sel_registerName("commit"));
    if(!replace||!make||!commit)return;
    uploadSeen=[NSMutableSet set];
    uploadBuffers=[NSMapTable weakToStrongObjectsMapTable];
    uploadOldReplace=(void *)method_setImplementation(replace,(IMP)uploadReplace);
    uploadOldBufferTexture=(void *)method_setImplementation(make,(IMP)uploadBufferTexture);
    uploadOldCommit=(void *)method_setImplementation(commit,(IMP)uploadCommit);
    Method nc=class_getInstanceMethod(dev,sel_registerName("newBufferWithBytesNoCopy:length:options:deallocator:"));
    Method cp=class_getInstanceMethod(dev,sel_registerName("newBufferWithBytes:length:options:"));
    if(nc)uploadOldNoCopy=(void *)method_setImplementation(nc,(IMP)uploadNoCopy);
    if(cp)uploadOldCopy=(void *)method_setImplementation(cp,(IMP)uploadCopy);
    fprintf(stderr,"icon-upload: CPU input trace installed pid=%d\n",getpid());
}
__attribute__((constructor)) static void uploadStart(void){
    if(strcmp(getprogname(),"SpringBoard"))return;
    dispatch_async(dispatch_get_main_queue(),^{uploadInstall(0);});
}
