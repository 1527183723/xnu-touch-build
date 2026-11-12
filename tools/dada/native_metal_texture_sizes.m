/* Live regression probe: icon-sized, multi-page textures and padded IOSurface rows.
 * Uses a separately assembled AIR fixture for each size (fragment position / width).
 * The original 8x8 fixture cannot be reused unchanged at larger viewports. */
#define main small_orientation_probe_main
#include "native_metal_orientation.m"
#undef main
#include <stdlib.h>
#include "texture_size_fixtures.h"

static unsigned check_pixels(id<MTLTexture> texture, const unsigned char *expected,
                             unsigned width, unsigned height, const char *label) {
    size_t row = width * 4u;
    unsigned char *got = calloc(height, row);
    if (!got) { ++failures; return 1; }
    [texture getBytes:got bytesPerRow:row fromRegion:MTLRegionMake2D(0,0,width,height) mipmapLevel:0];
    unsigned bad = 0, first = 0;
    for (unsigned i=0; i<width*height; ++i) {
        BOOL mismatch=NO;
        for(unsigned c=0;c<4;++c)
            if(abs((int)got[i*4+c]-(int)expected[i*4+c])>1) mismatch=YES;
        if(mismatch && !bad++) first=i;
    }
    printf("texture-sizes: %s %s %ux%u bad=%u/%u first=%u,%u got=%u,%u,%u,%u expected=%u,%u,%u,%u\n",
           bad?"FAIL":"PASS", label, width, height, bad, width*height, first%width, first/width,
           got[first*4],got[first*4+1],got[first*4+2],got[first*4+3],
           expected[first*4],expected[first*4+1],expected[first*4+2],expected[first*4+3]);
    free(got); failures+=bad!=0; return bad;
}

static BOOL sample_large(id<MTLCommandQueue> queue,id<MTLRenderPipelineState> pipeline,
                         id<MTLTexture> target,id<MTLTexture> source) {
    id<MTLCommandBuffer> cb=[queue commandBuffer];
    MTLRenderPassDescriptor *pass=[MTLRenderPassDescriptor renderPassDescriptor];
    pass.colorAttachments[0].texture=target;
    pass.colorAttachments[0].loadAction=MTLLoadActionClear;
    pass.colorAttachments[0].storeAction=MTLStoreActionStore;
    id<MTLRenderCommandEncoder> encoder=[cb renderCommandEncoderWithDescriptor:pass];
    if(!encoder){printf("texture-sizes: FAIL render encoder\n");++failures;return NO;}
    [encoder setRenderPipelineState:pipeline];
    [encoder setViewport:(MTLViewport){0,0,target.width,target.height,0,1}];
    const float colors[6][4]={{1,1,1,1},{1,1,1,1},{1,1,1,1},{1,1,1,1},{1,1,1,1},{1,1,1,1}};
    [encoder setVertexBytes:full length:sizeof full atIndex:0];
    [encoder setVertexBytes:colors length:sizeof colors atIndex:1];
    [encoder setFragmentTexture:source atIndex:0];
    [encoder drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:6];
    [encoder endEncoding]; return finish(cb);
}

int main(int argc,char **argv) {
    @autoreleasepool {
        id<MTLDevice> dev=MTLCreateSystemDefaultDevice();
        void *bundle=dlopen("/redwood/lib/RedwoodMetal.bundle/RedwoodMetal",RTLD_LAZY|RTLD_NOLOAD);
        id (*library)(const void *,size_t)=bundle?dlsym(bundle,"native_bridge_metal_library_with_air"):NULL;
        if(!dev||!library){printf("texture-sizes: FAIL device/library\n");return 2;}
        printf("texture-sizes: device=%s class=%s\n",dev.name.UTF8String,NSStringFromClass([dev class]).UTF8String);
        id<MTLCommandQueue> queue=[dev newCommandQueue];
        if(!queue)return 3;
        for(unsigned n=0;n<sizeof fixtures/sizeof fixtures[0];++n) {
            @autoreleasepool {
                unsigned w=fixtures[n].width,h=w;
                id<MTLLibrary> lib=library(fixtures[n].bytes,fixtures[n].size);
                id<MTLRenderPipelineState> sampled=lib?pipeline(dev,lib,@"sampled_fragment"):nil;
                if(!sampled){++failures;continue;}
                size_t row=w*4u;
                unsigned char *pattern=malloc(row*h);
                if(!pattern){++failures;break;}
                for(unsigned y=0;y<h;++y)for(unsigned x=0;x<w;++x){
                    unsigned char *p=pattern+(y*w+x)*4;
                    p[0]=(x/4%4)*64;p[1]=(y/4%4)*64;p[2]=((x/4+y/4)%4)*64;p[3]=255;
                }
                MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm width:w height:h mipmapped:NO];
                d.storageMode=MTLStorageModeShared;d.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead;
                id<MTLTexture> source=[dev newTextureWithDescriptor:d],target=[dev newTextureWithDescriptor:d];
                if(!source||!target){printf("texture-sizes: FAIL allocation %u\n",w);++failures;free(pattern);continue;}
                [source replaceRegion:MTLRegionMake2D(0,0,w,h) mipmapLevel:0 withBytes:pattern bytesPerRow:row];
                check_pixels(source,pattern,w,h,"cpu-upload");
                if(sample_large(queue,sampled,target,source))check_pixels(target,pattern,w,h,"sample-texture");
                NSDictionary *props=@{(id)kIOSurfaceWidth:@(w),(id)kIOSurfaceHeight:@(h),
                    (id)kIOSurfaceBytesPerElement:@4,(id)kIOSurfacePixelFormat:@(0x52474241)};
                IOSurfaceRef surface=IOSurfaceCreate((__bridge CFDictionaryRef)props);
                id<MTLTexture> st=surface?[dev newTextureWithDescriptor:d iosurface:surface plane:0]:nil;
                if(st){
                    IOSurfaceLock(surface,0,NULL);
                    unsigned char *base=IOSurfaceGetBaseAddress(surface);size_t stride=IOSurfaceGetBytesPerRow(surface);
                    printf("texture-sizes: surface %ux%u stride=%zu tight=%zu\n",w,h,stride,row);
                    for(unsigned y=0;y<h;++y)memcpy(base+y*stride,pattern+y*row,row);
                    IOSurfaceUnlock(surface,0,NULL);
                    if(sample_large(queue,sampled,target,st))check_pixels(target,pattern,w,h,"sample-iosurface");
                }else{printf("texture-sizes: FAIL IOSurface %u\n",w);++failures;}
                if(surface)CFRelease(surface);
                free(pattern);
            }
        }
        printf("texture-sizes: %s failures=%u\n",failures?"FAIL":"PASS",failures);
        return failures?1:0;
    }
}
