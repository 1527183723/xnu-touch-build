/* Test IOSurface pixels across two real processes, including native BGRA imports. */
#define main orientation_main_unused
#include "native_metal_orientation.m"
#undef main
#include "texture_size_fixtures.h"
#include <spawn.h>
#include <sys/wait.h>
#include <stdlib.h>
extern char **environ;

static void pixel(unsigned x,unsigned y,unsigned char p[4]) {
    p[0]=(x/4%4)*64;p[1]=(y/4%4)*64;p[2]=((x/4+y/4)%4)*64;p[3]=255;
}

static int child(unsigned sid) {
    IOSurfaceRef surface=IOSurfaceLookup(sid);
    if(!surface){printf("surface-process: FAIL lookup %u\n",sid);return 2;}
    unsigned w=(unsigned)IOSurfaceGetWidth(surface),h=(unsigned)IOSurfaceGetHeight(surface);
    size_t stride=IOSurfaceGetBytesPerRow(surface);uint32_t format=IOSurfaceGetPixelFormat(surface);
    BOOL bgra=format==0x42475241;unsigned bad=0;
    IOSurfaceLock(surface,kIOSurfaceLockReadOnly,NULL);
    const unsigned char *base=IOSurfaceGetBaseAddress(surface);
    for(unsigned y=0;y<h;++y)for(unsigned x=0;x<w;++x){
        unsigned char p[4];pixel(x,y,p);const unsigned char *q=base+y*stride+x*4;
        bad+=q[0]!=p[bgra?2:0]||q[1]!=p[1]||q[2]!=p[bgra?0:2]||q[3]!=p[3];
    }
    IOSurfaceUnlock(surface,kIOSurfaceLockReadOnly,NULL);
    printf("surface-process: %s cpu-import %ux%u %s stride=%zu bad=%u\n",bad?"FAIL":"PASS",w,h,bgra?"BGRA":"RGBA",stride,bad);
    failures+=bad!=0;
    id<MTLDevice> dev=MTLCreateSystemDefaultDevice();
    void *bundle=dlopen("/redwood/lib/RedwoodMetal.bundle/RedwoodMetal",RTLD_LAZY|RTLD_NOLOAD);
    id (*library)(const void *,size_t)=bundle?dlsym(bundle,"native_bridge_metal_library_with_air"):NULL;
    id<MTLLibrary> lib=nil;
    for(unsigned i=0;library&&i<sizeof fixtures/sizeof fixtures[0];++i)
        if(fixtures[i].width==w)lib=library(fixtures[i].bytes,fixtures[i].size);
    if(!dev||!lib){CFRelease(surface);return 3;}
    id<MTLRenderPipelineState> ps=pipeline(dev,lib,@"sampled_fragment");
    MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:bgra?MTLPixelFormatBGRA8Unorm:MTLPixelFormatRGBA8Unorm width:w height:h mipmapped:NO];
    d.storageMode=MTLStorageModeShared;d.usage=MTLTextureUsageShaderRead;
    id<MTLTexture> src=[dev newTextureWithDescriptor:d iosurface:surface plane:0];
    d.pixelFormat=MTLPixelFormatRGBA8Unorm;d.usage=MTLTextureUsageRenderTarget;
    id<MTLTexture> dst=[dev newTextureWithDescriptor:d];
    id<MTLCommandQueue> queue=[dev newCommandQueue];
    if(!ps||!src||!dst||!queue){CFRelease(surface);return 4;}
    id<MTLCommandBuffer> cb=[queue commandBuffer];
    MTLRenderPassDescriptor *pass=[MTLRenderPassDescriptor renderPassDescriptor];
    pass.colorAttachments[0].texture=dst;pass.colorAttachments[0].loadAction=MTLLoadActionClear;
    pass.colorAttachments[0].storeAction=MTLStoreActionStore;
    id<MTLRenderCommandEncoder> encoder=[cb renderCommandEncoderWithDescriptor:pass];
    [encoder setRenderPipelineState:ps];[encoder setViewport:(MTLViewport){0,0,w,h,0,1}];
    const float colors[6][4]={{1,1,1,1},{1,1,1,1},{1,1,1,1},{1,1,1,1},{1,1,1,1},{1,1,1,1}};
    [encoder setVertexBytes:full length:sizeof full atIndex:0];
    [encoder setVertexBytes:colors length:sizeof colors atIndex:1];
    [encoder setFragmentTexture:src atIndex:0];[encoder drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:6];
    [encoder endEncoding];
    if(finish(cb)){
        unsigned char *got=calloc(w*h,4);bad=0;
        [dst getBytes:got bytesPerRow:w*4 fromRegion:MTLRegionMake2D(0,0,w,h) mipmapLevel:0];
        for(unsigned y=0;y<h;++y)for(unsigned x=0;x<w;++x){
            unsigned char p[4];pixel(x,y,p);BOOL mismatch=NO;
            for(unsigned c=0;c<4;++c)if(abs((int)got[(y*w+x)*4+c]-(int)p[c])>1)mismatch=YES;
            bad+=mismatch;
        }
        free(got);failures+=bad!=0;
        printf("surface-process: %s gpu-import %ux%u %s bad=%u\n",bad?"FAIL":"PASS",w,h,bgra?"BGRA":"RGBA",bad);
    }
    CFRelease(surface);return failures?1:0;
}

int main(int argc,char **argv) {
    @autoreleasepool {
        if(argc==3&&!strcmp(argv[1],"child"))return child((unsigned)strtoul(argv[2],NULL,10));
        unsigned total=0;
        for(unsigned b=0;b<2;++b)for(unsigned n=0;n<2;++n){
            unsigned w=n?256:120;
            NSDictionary *props=@{(id)kIOSurfaceWidth:@(w),(id)kIOSurfaceHeight:@(w),
                (id)kIOSurfaceBytesPerElement:@4,(id)kIOSurfacePixelFormat:@(b?0x42475241:0x52474241)};
            IOSurfaceRef surface=IOSurfaceCreate((__bridge CFDictionaryRef)props);
            if(!surface){++total;continue;}
            IOSurfaceLock(surface,0,NULL);unsigned char *base=IOSurfaceGetBaseAddress(surface);
            size_t stride=IOSurfaceGetBytesPerRow(surface);
            for(unsigned y=0;y<w;++y)for(unsigned x=0;x<w;++x){
                unsigned char p[4];pixel(x,y,p);unsigned char *q=base+y*stride+x*4;
                q[0]=p[b?2:0];q[1]=p[1];q[2]=p[b?0:2];q[3]=p[3];
            }
            IOSurfaceUnlock(surface,0,NULL);
            char sid[32];snprintf(sid,sizeof sid,"%u",IOSurfaceGetID(surface));
            char *args[]={argv[0],"child",sid,NULL};pid_t pid=0;int status=0;
            int error=posix_spawn(&pid,argv[0],NULL,NULL,args,environ);
            if(error||waitpid(pid,&status,0)!=pid||!WIFEXITED(status)||WEXITSTATUS(status)!=0)++total;
            CFRelease(surface);
        }
        printf("surface-process: %s failed-cases=%u\n",total?"FAIL":"PASS",total);
        return total?1:0;
    }
}
