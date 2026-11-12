/* Exercise the actual native icon atlas format, including both planes and
 * non-origin updates. All surfaces belong to this diagnostic process. */
#define main orientation_unused_main
#include "native_metal_orientation.m"
#undef main
#include <math.h>
#include <stdlib.h>
#include <mach/mach.h>
#include "xr_probe_fixtures.h"
#include "native_metal_isolated_device.inc"

static unsigned checks;
static unsigned xr_code(float x) { return (unsigned)lrintf(fminf(1023, fmaxf(0, 384 + 510*x))); }

static IOSurfaceRef xr_surface(unsigned w,unsigned h) {
    NSDictionary *props=@{(id)kIOSurfaceWidth:@(w),(id)kIOSurfaceHeight:@(h),
        (id)kIOSurfaceBytesPerElement:@4,(id)kIOSurfacePixelFormat:@(0x62336138),
        (id)kIOSurfaceAllocSize:@(((w*4+63)&~63)*h+((w+63)&~63)*h)};
    return IOSurfaceCreate((__bridge CFDictionaryRef)props);
}

static id<MTLTexture> xr_texture(id<MTLDevice> dev,IOSurfaceRef surface) {
    MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:(MTLPixelFormat)550
        width:IOSurfaceGetWidth(surface) height:IOSurfaceGetHeight(surface) mipmapped:NO];
    d.storageMode=MTLStorageModeShared;d.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead;
    return [dev newTextureWithDescriptor:d iosurface:surface plane:0];
}

static id<MTLRenderPipelineState> xr_pipeline(id<MTLDevice> dev,id<MTLLibrary> lib,
                                            BOOL sampled,BOOL blend,MTLPixelFormat format) {
    MTLRenderPipelineDescriptor *d=[MTLRenderPipelineDescriptor new];
    d.vertexFunction=[lib newFunctionWithName:@"colored_vertex"];
    d.fragmentFunction=[lib newFunctionWithName:sampled?@"sampled_fragment":@"colored_fragment"];
    d.colorAttachments[0].pixelFormat=format;
    d.colorAttachments[0].blendingEnabled=blend;
    d.colorAttachments[0].sourceRGBBlendFactor=MTLBlendFactorOne;
    d.colorAttachments[0].destinationRGBBlendFactor=MTLBlendFactorOneMinusSourceAlpha;
    d.colorAttachments[0].sourceAlphaBlendFactor=MTLBlendFactorOne;
    d.colorAttachments[0].destinationAlphaBlendFactor=MTLBlendFactorOneMinusSourceAlpha;
    NSError *error=nil;
    id<MTLRenderPipelineState> p=[dev newRenderPipelineStateWithDescriptor:d error:&error];
    if(!p){printf("xr-probe: FAIL pipeline pf=%lu sample=%d blend=%d %s\n",(unsigned long)format,sampled,blend,error.description.UTF8String);++failures;}
    return p;
}

static BOOL render(id<MTLCommandQueue> q,id<MTLRenderPipelineState> p,id<MTLTexture> dst,
                   id<MTLTexture> src,BOOL clear,const float color[4],unsigned draws,BOOL patch) {
    id<MTLCommandBuffer> cb=[q commandBuffer];
    MTLRenderPassDescriptor *d=[MTLRenderPassDescriptor renderPassDescriptor];
    d.colorAttachments[0].texture=dst;
    d.colorAttachments[0].loadAction=clear?MTLLoadActionClear:MTLLoadActionLoad;
    d.colorAttachments[0].storeAction=MTLStoreActionStore;
    d.colorAttachments[0].clearColor=MTLClearColorMake(.1,.2,.3,1);
    id<MTLRenderCommandEncoder> e=[cb renderCommandEncoderWithDescriptor:d];
    if(!e){++failures;printf("xr-probe: FAIL encoder\n");return NO;}
    [e setRenderPipelineState:p];
    [e setViewport:(MTLViewport){0,0,dst.width,dst.height,0,1}];
    if(patch)[e setScissorRect:(MTLScissorRect){195,417,192,192}];
    [e setVertexBytes:full length:sizeof full atIndex:0];
    float colors[6][4];for(unsigned i=0;i<6;++i)memcpy(colors[i],color,16);
    [e setVertexBytes:colors length:sizeof colors atIndex:1];
    if(src)[e setFragmentTexture:src atIndex:0];
    for(unsigned i=0;i<draws;++i)[e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:6];
    [e endEncoding];return finish(cb);
}

static void report(const char *name,unsigned w,unsigned h,unsigned bad,unsigned first,
                   unsigned alphaBad,unsigned maxError) {
    ++checks;failures+=bad!=0||alphaBad!=0;
    printf("xr-probe: %s %s %ux%u bad=%u alphaBad=%u first=%u,%u maxError=%u\n",
        bad||alphaBad?"FAIL":"PASS",name,w,h,bad,alphaBad,first%w,first/w,maxError);
    fflush(stdout);
}

static void check_surface(IOSurfaceRef s,const float color[4],BOOL patch,const char *name) {
    unsigned w=IOSurfaceGetWidth(s),h=IOSurfaceGetHeight(s),bad=0,first=0,alphaBad=0,maxError=0;
    IOSurfaceLock(s,kIOSurfaceLockReadOnly,NULL);
    const unsigned char *rgb=IOSurfaceGetBaseAddressOfPlane(s,0),*alpha=IOSurfaceGetBaseAddressOfPlane(s,1);
    size_t row=IOSurfaceGetBytesPerRowOfPlane(s,0),arow=IOSurfaceGetBytesPerRowOfPlane(s,1);
    const float bg[4]={.1,.2,.3,1};
    for(unsigned y=0;y<h;++y)for(unsigned x=0;x<w;++x){
        const float *expect=patch&&!(x>=195&&x<387&&y>=417&&y<609)?bg:color;
        uint32_t word;memcpy(&word,rgb+y*row+x*4,4);BOOL mismatch=NO;
        unsigned actual[3]={(word>>20)&1023,(word>>10)&1023,word&1023};
        for(unsigned c=0;c<3;++c){unsigned err=abs((int)actual[c]-(int)xr_code(expect[c]));if(err>2)mismatch=YES;if(err>maxError)maxError=err;}
        if(mismatch&&!bad++)first=y*w+x;
        alphaBad+=abs((int)alpha[y*arow+x]-(int)lrintf(expect[3]*255))>1;
    }
    IOSurfaceUnlock(s,kIOSurfaceLockReadOnly,NULL);
    report(name,w,h,bad,first,alphaBad,maxError);
}

static void write_pattern(IOSurfaceRef s) {
    unsigned w=IOSurfaceGetWidth(s),h=IOSurfaceGetHeight(s);
    IOSurfaceLock(s,0,NULL);
    unsigned char *rgb=IOSurfaceGetBaseAddressOfPlane(s,0),*alpha=IOSurfaceGetBaseAddressOfPlane(s,1);
    size_t row=IOSurfaceGetBytesPerRowOfPlane(s,0),arow=IOSurfaceGetBytesPerRowOfPlane(s,1);
    for(unsigned y=0;y<h;++y)for(unsigned x=0;x<w;++x){
        unsigned r=xr_code((x/7%4)*.25f),g=xr_code((y/11%4)*.25f),b=xr_code(((x/7+y/11)%4)*.25f);
        uint32_t word=(r<<20)|(g<<10)|b;memcpy(rgb+y*row+x*4,&word,4);alpha[y*arow+x]=255;
    }
    IOSurfaceUnlock(s,0,NULL);
}

static void check_sample(id<MTLTexture> target,BOOL pattern,const float color[4],const char *name) {
    unsigned w=target.width,h=target.height,bad=0,first=0,maxError=0,alphaBad=0;
    unsigned char *pixels=calloc(h,w*4);
    if(!pixels){++failures;return;}
    [target getBytes:pixels bytesPerRow:w*4 fromRegion:MTLRegionMake2D(0,0,w,h) mipmapLevel:0];
    for(unsigned y=0;y<h;++y)for(unsigned x=0;x<w;++x){
        float p[4]={(x/7%4)*.25f,(y/11%4)*.25f,((x/7+y/11)%4)*.25f,1};
        const float *expect=pattern?p:color;BOOL mismatch=NO;
        for(unsigned c=0;c<4;++c){
            unsigned err=abs((int)pixels[(y*w+x)*4+c]-(int)lrintf(expect[c]*255));
            if(err>2)mismatch=YES;if(err>maxError)maxError=err;
            if(c==3&&err>2)++alphaBad;
        }
        if(mismatch&&!bad++)first=y*w+x;
    }
    free(pixels);report(name,w,h,bad,first,alphaBad,maxError);
}

static void check_rgb10_buffer(id<MTLDevice> dev,id<MTLCommandQueue> q,id<MTLLibrary> lib){
    const unsigned w=194,h=194,row=784,length=row*h;
    id<MTLRenderPipelineState> p=xr_pipeline(dev,lib,YES,NO,MTLPixelFormatRGBA8Unorm);
    if(!p)return;
    for(unsigned mode=0;mode<3;++mode){
        vm_address_t memory=0;NSUInteger allocation=(length+32767)&~16383u;
        if(mode&&vm_allocate(mach_task_self(),&memory,allocation,VM_FLAGS_ANYWHERE)){++failures;continue;}
        void *pointer=mode?(void *)(memory+(mode==2?4096:0)):NULL;
        id<MTLBuffer> b=mode?[dev newBufferWithBytesNoCopy:pointer length:length options:MTLResourceStorageModeShared deallocator:nil]:[dev newBufferWithLength:length options:MTLResourceStorageModeShared];
        MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGB10A2Unorm width:w height:h mipmapped:NO];
        d.storageMode=MTLStorageModeShared;d.usage=MTLTextureUsageShaderRead;
        id<MTLTexture> source=[b newTextureWithDescriptor:d offset:0 bytesPerRow:row];
        d.pixelFormat=MTLPixelFormatRGBA8Unorm;d.usage=MTLTextureUsageRenderTarget;
        id<MTLTexture> target=[dev newTextureWithDescriptor:d];
        if(!source||!target){puts("xr-probe: FAIL RGB10 buffer setup");++failures;continue;}
        for(unsigned frame=0;frame<3;++frame){
            for(unsigned y=0;y<h;++y)for(unsigned x=0;x<w;++x){
                unsigned r=((x/7+frame)%4)*256,g=((y/11+frame)%4)*256,blue=((x/7+y/11+frame)%4)*256;
                uint32_t v=r|(g<<10)|(blue<<20)|(3u<<30);memcpy((char *)b.contents+y*row+x*4,&v,4);
            }
            const float white[4]={1,1,1,1};
            if(render(q,p,target,source,YES,white,1,NO)){
                unsigned char *got=calloc(w*h,4);[target getBytes:got bytesPerRow:w*4 fromRegion:MTLRegionMake2D(0,0,w,h) mipmapLevel:0];
                unsigned bad=0,first=0,maxError=0;
                for(unsigned y=0;y<h;++y)for(unsigned x=0;x<w;++x){
                    unsigned e[4]={((x/7+frame)%4)*256,((y/11+frame)%4)*256,((x/7+y/11+frame)%4)*256,1023};BOOL mismatch=NO;
                    for(unsigned c=0;c<4;++c){unsigned err=abs((int)got[(y*w+x)*4+c]-(int)lrintf(e[c]*255.f/1023));if(err>2)mismatch=YES;if(err>maxError)maxError=err;}
                    if(mismatch&&!bad++)first=y*w+x;
                }
                char label[96];snprintf(label,sizeof label,"rgb10-row784-mode%u-frame%u",mode,frame);report(label,w,h,bad,first,0,maxError);free(got);
            }
        }
        source=nil;b=nil;if(mode)vm_deallocate(mach_task_self(),memory,allocation);
    }
}

#ifndef DADA_XR_NO_MAIN
int main(int argc,char **argv) {
    @autoreleasepool {
        id<MTLDevice> dev=isolatedDevice(argc>1?argv[1]:NULL);
        void *bundle=dlopen(argc>1?argv[1]:"/redwood/lib/RedwoodMetal.bundle/RedwoodMetal",RTLD_LAZY|RTLD_NOLOAD);
        id (*library)(const void *,size_t)=bundle?dlsym(bundle,"native_bridge_metal_library_with_air"):NULL;
        if(!dev||!library){puts("xr-probe: FAIL device/library");return 2;}
        printf("xr-probe: device=%s\n",dev.name.UTF8String);
        id<MTLCommandQueue> q=[dev newCommandQueue];
        for(unsigned n=0;n<sizeof xr_fixtures/sizeof xr_fixtures[0];++n){
            @autoreleasepool {
                unsigned w=xr_fixtures[n].w,h=xr_fixtures[n].h;
                id<MTLLibrary> lib=library(xr_fixtures[n].bytes,xr_fixtures[n].size);
                if(w==194)check_rgb10_buffer(dev,q,lib);
                IOSurfaceRef s=xr_surface(w,h);
                id<MTLTexture> xr=s?xr_texture(dev,s):nil;
                if(!xr){puts("xr-probe: FAIL xr surface/texture");++failures;if(s)CFRelease(s);continue;}
                printf("xr-probe: surface %ux%u format=%08x planes=%zu rows=%zu/%zu allocation=%zu\n",w,h,IOSurfaceGetPixelFormat(s),IOSurfaceGetPlaneCount(s),IOSurfaceGetBytesPerRowOfPlane(s,0),IOSurfaceGetBytesPerRowOfPlane(s,1),IOSurfaceGetAllocSize(s));
                if(IOSurfaceGetPlaneCount(s)!=2){++failures;CFRelease(s);continue;}
                id<MTLRenderPipelineState> opaque=xr_pipeline(dev,lib,NO,NO,(MTLPixelFormat)550),blend=xr_pipeline(dev,lib,NO,YES,(MTLPixelFormat)550),sample=xr_pipeline(dev,lib,YES,NO,MTLPixelFormatRGBA8Unorm);
                MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm width:w height:h mipmapped:NO];
                d.storageMode=MTLStorageModeShared;d.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead;
                id<MTLTexture> target=[dev newTextureWithDescriptor:d];
                if(!opaque||!blend||!sample||!target){CFRelease(s);continue;}
                const float fill[4]={.25,.5,.75,1},src[4]={.2,.3,.4,.5},once[4]={.25,.4,.55,1},twice[4]={.325,.5,.675,1};
                if(render(q,opaque,xr,nil,YES,fill,1,NO))check_surface(s,fill,NO,"render-solid-two-planes");
                if(render(q,sample,target,xr,YES,fill,1,NO))check_sample(target,NO,fill,"sample-rendered-xr");
                write_pattern(s);
                if(render(q,sample,target,xr,YES,fill,1,NO))check_sample(target,YES,fill,"sample-cpu-xr-pattern");
                if(render(q,blend,xr,nil,YES,src,1,NO))check_surface(s,once,NO,"blend-one-draw");
                if(render(q,blend,xr,nil,YES,src,2,NO))check_surface(s,twice,NO,"blend-two-draws-same-pass");
                if(render(q,opaque,xr,nil,YES,fill,1,NO)&&render(q,blend,xr,nil,NO,src,1,NO)){
                    const float separate[4]={.325,.55,.775,1};check_surface(s,separate,NO,"blend-load-prior-pass");
                }
                if(w>=512&&h>=640&&render(q,blend,xr,nil,YES,src,2,YES))check_surface(s,twice,YES,"atlas-nonorigin-blend");
                CFRelease(s);
            }
        }
        printf("xr-probe: %s checks=%u failures=%u\n",failures?"FAIL":"PASS",checks,failures);
        return failures?1:0;
    }
}
#endif
