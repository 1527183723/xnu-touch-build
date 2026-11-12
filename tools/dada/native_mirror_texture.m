/* Exercise CA's mirrored, buffer-backed textures over repeated CPU updates. */
#define main orientation_main_unused
#include "native_metal_orientation.m"
#undef main
#include "texture_size_fixtures.h"
#include <mach/mach.h>
#include <stdlib.h>

static void pattern(unsigned char *base,size_t stride,unsigned w,unsigned frame) {
    for(unsigned y=0;y<w;++y)for(unsigned x=0;x<w;++x){
        unsigned char *p=base+y*stride+x*4;
        p[0]=((x/4+frame)%4)*64;p[1]=((y/4+frame)%4)*64;p[2]=((x/4+y/4+frame)%4)*64;p[3]=255;
    }
}

int main(void) {
    @autoreleasepool {
        id<MTLDevice> dev=MTLCreateSystemDefaultDevice();
        void *bundle=dlopen("/redwood/lib/RedwoodMetal.bundle/RedwoodMetal",RTLD_LAZY|RTLD_NOLOAD);
        id (*library)(const void *,size_t)=bundle?dlsym(bundle,"native_bridge_metal_library_with_air"):NULL;
        if(!dev||!library)return 2;
        id<MTLCommandQueue> queue=[dev newCommandQueue];
        const unsigned sizes[]={120,256,512};
        for(unsigned n=0;n<3;++n)for(unsigned mirrored=0;mirrored<2;++mirrored){
            unsigned w=sizes[n];size_t row=(w*4+255)&~255u;
            size_t length=(row*w+16383)&~(size_t)16383;
            vm_address_t memory=0;
            if(mirrored&&vm_allocate(mach_task_self(),&memory,length,VM_FLAGS_ANYWHERE)!=KERN_SUCCESS){++failures;continue;}
            id<MTLBuffer> buffer=mirrored?[dev newBufferWithBytesNoCopy:(void *)memory length:length options:MTLResourceStorageModeShared deallocator:^(void *p,NSUInteger size){vm_deallocate(mach_task_self(),(vm_address_t)p,size);}]:[dev newBufferWithLength:length options:MTLResourceStorageModeShared];
            MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm width:w height:w mipmapped:NO];
            d.storageMode=MTLStorageModeShared;d.usage=MTLTextureUsageShaderRead;
            id<MTLTexture> source=[buffer newTextureWithDescriptor:d offset:0 bytesPerRow:row];
            d.usage=MTLTextureUsageRenderTarget;
            id<MTLTexture> target=[dev newTextureWithDescriptor:d];
            id<MTLLibrary> lib=nil;
            for(unsigned i=0;i<sizeof fixtures/sizeof fixtures[0];++i)if(fixtures[i].width==w)lib=library(fixtures[i].bytes,fixtures[i].size);
            id<MTLRenderPipelineState> ps=lib?pipeline(dev,lib,@"sampled_fragment"):nil;
            if(!buffer||!source||!target||!ps){printf("mirror-texture: FAIL setup %u %s\n",w,mirrored?"no-copy":"owned");++failures;continue;}
            unsigned char *expected=malloc(row*w),*got=malloc(w*w*4);
            for(unsigned frame=0;frame<4;++frame){
                pattern(buffer.contents,row,w,frame);pattern(expected,row,w,frame);
                MTLRenderPassDescriptor *pass=[MTLRenderPassDescriptor renderPassDescriptor];
                pass.colorAttachments[0].texture=target;pass.colorAttachments[0].loadAction=MTLLoadActionClear;pass.colorAttachments[0].storeAction=MTLStoreActionStore;
                id<MTLCommandBuffer> cb=[queue commandBuffer];id<MTLRenderCommandEncoder> e=[cb renderCommandEncoderWithDescriptor:pass];
                const float colors[6][4]={{1,1,1,1},{1,1,1,1},{1,1,1,1},{1,1,1,1},{1,1,1,1},{1,1,1,1}};
                [e setRenderPipelineState:ps];[e setViewport:(MTLViewport){0,0,w,w,0,1}];
                [e setVertexBytes:full length:sizeof full atIndex:0];[e setVertexBytes:colors length:sizeof colors atIndex:1];[e setFragmentTexture:source atIndex:0];
                [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:6];[e endEncoding];
                if(finish(cb)){
                    [target getBytes:got bytesPerRow:w*4 fromRegion:MTLRegionMake2D(0,0,w,w) mipmapLevel:0];
                    unsigned bad=0;
                    for(unsigned y=0;y<w;++y)for(unsigned x=0;x<w;++x){
                        BOOL mismatch=NO;for(unsigned c=0;c<4;++c)if(abs((int)got[(y*w+x)*4+c]-(int)expected[y*row+x*4+c])>1)mismatch=YES;
                        bad+=mismatch;
                    }
                    printf("mirror-texture: %s %s %ux%u bytes=%zu frame=%u bad=%u/%u\n",bad?"FAIL":"PASS",mirrored?"no-copy":"owned",w,w,length,frame,bad,w*w);failures+=bad!=0;
                }
            }
            free(expected);free(got);
        }
        printf("mirror-texture: %s failures=%u\n",failures?"FAIL":"PASS",failures);return failures?1:0;
    }
}
