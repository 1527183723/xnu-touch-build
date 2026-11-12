/* Check two-way shared buffer/texture coherence over multiple submissions.
 * Owns all tested memory; does not inspect or change SpringBoard resources. */
#define DADA_XR_NO_MAIN 1
#include "native_metal_xr_probe.m"

static void lifecycleCheck(const char *name,unsigned mode,const unsigned char *got,
                           size_t row,unsigned char value) {
    unsigned bad=0,first=0;
    for(unsigned y=0;y<194;++y)for(unsigned x=0;x<194;++x) {
        BOOL different=NO;
        for(unsigned c=0;c<4;++c)different|=got[y*row+x*4+c]!=value;
        if(different&&!bad++)first=y*194+x;
    }
    char label[100];snprintf(label,sizeof label,"mirror-mode%u-%s",mode,name);
    report(label,194,194,bad,first,0,bad?255:0);
}

int main(int argc,char **argv) {
    @autoreleasepool {
        id<MTLDevice> dev=isolatedDevice(argc>1?argv[1]:NULL);
        id<MTLCommandQueue> q=[dev newCommandQueue];
        if(!dev||!q)return 2;
        const NSUInteger row=784,length=row*194;
        for(unsigned mode=0;mode<3;++mode) {
            @autoreleasepool {
                vm_address_t memory=0;NSUInteger allocation=(length+32767)&~16383u;
                if(mode&&vm_allocate(mach_task_self(),&memory,allocation,VM_FLAGS_ANYWHERE))return 3;
                void *pointer=mode?(void *)(memory+(mode==2?4096:0)):NULL;
                if(pointer)memset(pointer,0x31,length);
                id<MTLBuffer> b=mode?[dev newBufferWithBytesNoCopy:pointer length:length
                    options:MTLResourceStorageModeShared deallocator:nil]:
                    [dev newBufferWithLength:length options:MTLResourceStorageModeShared];
                if(!b)return 4;
                memset(b.contents,0x31,length);
                MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:
                    MTLPixelFormatRGB10A2Unorm width:194 height:194 mipmapped:NO];
                d.storageMode=MTLStorageModeShared;d.usage=MTLTextureUsageShaderRead;
                id<MTLTexture> t=[b newTextureWithDescriptor:d offset:0 bytesPerRow:row];
                if(!t)return 5;
                unsigned char *pixels=malloc(length);
                // GPU buffer writes must be visible through the shared CPU address.
                id<MTLCommandBuffer> cb=[q commandBuffer];
                id<MTLBlitCommandEncoder> e=[cb blitCommandEncoder];
                [e fillBuffer:b range:NSMakeRange(0,length) value:0xa6];[e endEncoding];
                if(finish(cb))lifecycleCheck("gpu-fill-cpu",mode,b.contents,row,0xa6);
                [t getBytes:pixels bytesPerRow:row fromRegion:MTLRegionMake2D(0,0,194,194) mipmapLevel:0];
                lifecycleCheck("gpu-fill-texture",mode,pixels,row,0xa6);
                // An empty command is intentionally unrelated to this buffer.
                if(finish([q commandBuffer])) {
                    [t getBytes:pixels bytesPerRow:row fromRegion:MTLRegionMake2D(0,0,194,194) mipmapLevel:0];
                    lifecycleCheck("gpu-fill-survives-next-commit",mode,pixels,row,0xa6);
                }
                // replaceRegion on a buffer-backed shared texture updates the same storage.
                memset(pixels,0x59,length);
                [t replaceRegion:MTLRegionMake2D(0,0,194,194) mipmapLevel:0 withBytes:pixels bytesPerRow:row];
                lifecycleCheck("replace-cpu",mode,b.contents,row,0x59);
                if(finish([q commandBuffer])) {
                    [t getBytes:pixels bytesPerRow:row fromRegion:MTLRegionMake2D(0,0,194,194) mipmapLevel:0];
                    lifecycleCheck("replace-survives-next-commit",mode,pixels,row,0x59);
                }
                // A later CPU update must still reach the GPU.
                memset(b.contents,0xd3,length);
                id<MTLBuffer> readback=[dev newBufferWithLength:length options:MTLResourceStorageModeShared];
                cb=[q commandBuffer];e=[cb blitCommandEncoder];
                [e copyFromBuffer:b sourceOffset:0 toBuffer:readback destinationOffset:0 size:length];
                [e endEncoding];
                if(finish(cb)) {
                    memcpy(pixels,readback.contents,length);
                    lifecycleCheck("later-cpu-update",mode,pixels,row,0xd3);
                }
                free(pixels);t=nil;b=nil;
                if(mode)vm_deallocate(mach_task_self(),memory,allocation);
            }
        }
        printf("mirror-lifecycle: %s checks=%u failures=%u\n",failures?"FAIL":"PASS",checks,failures);
        return failures?1:0;
    }
}
