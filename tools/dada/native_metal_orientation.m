/* Diagnose raster, texture-upload, sampling and IOSurface row origins separately.
 * Uses the pinned translator's AIR fixture; no Metal source compiler is needed. */
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <IOSurface/IOSurfaceRef.h>
#include <dlfcn.h>
#include <stdio.h>
#include <string.h>

static unsigned failures;
static const float quarter[6][2] = {{-1,1},{0,1},{-1,0},{0,1},{0,0},{-1,0}};
static const float full[6][2] = {{-1,1},{1,1},{-1,-1},{1,1},{1,-1},{-1,-1}};

static BOOL finish(id<MTLCommandBuffer> cb) {
    [cb commit]; [cb waitUntilCompleted];
    if (cb.status == MTLCommandBufferStatusCompleted) return YES;
    printf("orientation: FAIL command %s\n", cb.error.description.UTF8String);
    ++failures; return NO;
}

static unsigned compare(id<MTLTexture> tex, const unsigned char *expected, const char *name) {
    unsigned char got[256] = {0};
    [tex getBytes:got bytesPerRow:32 fromRegion:MTLRegionMake2D(0,0,8,8) mipmapLevel:0];
    unsigned bad=0, flipped=0;
    for(unsigned y=0;y<8;++y) for(unsigned x=0;x<8;++x) {
        bad += memcmp(got+(y*8+x)*4,expected+(y*8+x)*4,4)!=0;
        flipped += memcmp(got+(y*8+x)*4,expected+((7-y)*8+x)*4,4)!=0;
    }
    printf("orientation: %s %s bad=%u/64 vertical-flip-bad=%u/64 corners=%02x%02x%02x/%02x%02x%02x/%02x%02x%02x/%02x%02x%02x\n",
           bad?"FAIL":"PASS",name,bad,flipped,
           got[0],got[1],got[2],got[28],got[29],got[30],
           got[224],got[225],got[226],got[252],got[253],got[254]);
    failures += bad!=0; return bad;
}

static id<MTLRenderPipelineState> pipeline(id<MTLDevice> dev,id<MTLLibrary> lib,NSString *frag) {
    MTLRenderPipelineDescriptor *p=[MTLRenderPipelineDescriptor new];
    p.vertexFunction=[lib newFunctionWithName:@"colored_vertex"];
    p.fragmentFunction=[lib newFunctionWithName:frag];
    p.colorAttachments[0].pixelFormat=MTLPixelFormatRGBA8Unorm;
    NSError *error=nil;
    id<MTLRenderPipelineState> result=[dev newRenderPipelineStateWithDescriptor:p error:&error];
    if(!result)printf("orientation: FAIL pipeline %s %s\n",frag.UTF8String,error.description.UTF8String);
    return result;
}

static void draw(id<MTLCommandQueue> q,id<MTLRenderPipelineState> p,id<MTLTexture> dst,
                 id<MTLTexture> src,BOOL explicitViewport) {
    id<MTLCommandBuffer> cb=[q commandBuffer];
    MTLRenderPassDescriptor *pass=[MTLRenderPassDescriptor renderPassDescriptor];
    pass.colorAttachments[0].texture=dst;
    pass.colorAttachments[0].loadAction=MTLLoadActionClear;
    pass.colorAttachments[0].storeAction=MTLStoreActionStore;
    pass.colorAttachments[0].clearColor=MTLClearColorMake(0,0,1,1);
    id<MTLRenderCommandEncoder> e=[cb renderCommandEncoderWithDescriptor:pass];
    [e setRenderPipelineState:p];
    if(explicitViewport)[e setViewport:(MTLViewport){0,0,8,8,0,1}];
    const float colors[6][4]={{1,.2,0,1},{1,.2,0,1},{1,.2,0,1},{1,.2,0,1},{1,.2,0,1},{1,.2,0,1}};
    [e setVertexBytes:src?full:quarter length:sizeof full atIndex:0];
    [e setVertexBytes:colors length:sizeof colors atIndex:1];
    if(src)[e setFragmentTexture:src atIndex:0];
    [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:6];
    [e endEncoding]; finish(cb);
}

int main(int argc,char **argv) {
    @autoreleasepool {
        id<MTLDevice> dev=MTLCreateSystemDefaultDevice();
        void *bundle=dlopen("/redwood/lib/RedwoodMetal.bundle/RedwoodMetal",RTLD_LAZY|RTLD_NOLOAD);
        id (*library)(const void *,size_t)=bundle?dlsym(bundle,"native_bridge_metal_library_with_air"):NULL;
        NSData *air=argc==2?[NSData dataWithContentsOfFile:@(argv[1])]:nil;
        id<MTLLibrary> lib=library&&air?library(air.bytes,air.length):nil;
        if(!dev||!lib){printf("orientation: FAIL device/library\n");return 2;}
        printf("orientation: device=%s class=%s\n",dev.name.UTF8String,NSStringFromClass([dev class]).UTF8String);
        id<MTLRenderPipelineState> colored=pipeline(dev,lib,@"colored_fragment");
        id<MTLRenderPipelineState> sampled=pipeline(dev,lib,@"sampled_fragment");
        if(!colored||!sampled)return 3;
        id<MTLCommandQueue> q=[dev newCommandQueue];
        MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm width:8 height:8 mipmapped:NO];
        d.storageMode=MTLStorageModeShared; d.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead;
        id<MTLTexture> target=[dev newTextureWithDescriptor:d],source=[dev newTextureWithDescriptor:d];
        unsigned char expected[256],pattern[256];
        for(unsigned y=0;y<8;++y)for(unsigned x=0;x<8;++x){
            unsigned i=(y*8+x)*4;BOOL tl=x<4&&y<4;
            expected[i]=tl?255:0;expected[i+1]=tl?51:0;expected[i+2]=tl?0:255;expected[i+3]=255;
            pattern[i]=y<4?255:0;pattern[i+1]=x<4?0:255;pattern[i+2]=y<4?0:255;pattern[i+3]=255;
        }
        draw(q,colored,target,nil,NO);compare(target,expected,"raster-default");
        draw(q,colored,target,nil,YES);compare(target,expected,"raster-explicit");
        [source replaceRegion:MTLRegionMake2D(0,0,8,8) mipmapLevel:0 withBytes:pattern bytesPerRow:32];
        compare(source,pattern,"cpu-upload");
        draw(q,sampled,target,source,YES);compare(target,pattern,"sample-texture");
        NSDictionary *props=@{(id)kIOSurfaceWidth:@8,(id)kIOSurfaceHeight:@8,(id)kIOSurfaceBytesPerElement:@4,(id)kIOSurfacePixelFormat:@(0x52474241)};
        IOSurfaceRef surface=IOSurfaceCreate((__bridge CFDictionaryRef)props);
        id<MTLTexture> st=surface?[dev newTextureWithDescriptor:d iosurface:surface plane:0]:nil;
        if(st){
            draw(q,colored,st,nil,YES);compare(st,expected,"raster-iosurface");
            IOSurfaceLock(surface,0,NULL);
            unsigned char *base=IOSurfaceGetBaseAddress(surface);size_t row=IOSurfaceGetBytesPerRow(surface);
            for(unsigned y=0;y<8;++y)memcpy(base+y*row,pattern+y*32,32);
            IOSurfaceUnlock(surface,0,NULL);
            draw(q,sampled,target,st,YES);compare(target,pattern,"sample-iosurface");
            CFRelease(surface);
        }else{printf("orientation: FAIL IOSurface texture\n");++failures;}
        printf("orientation: %s failures=%u\n",failures?"FAIL":"PASS",failures);
        return failures?1:0;
    }
}
