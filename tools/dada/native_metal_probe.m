/* A real Metal-discovery, render-clear and readback check on the running phone. */
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <stdio.h>
#include <stdlib.h>

int main(void)
{
    @autoreleasepool {
        id<MTLDevice> device = MTLCreateSystemDefaultDevice();
        if (!device) {
            fprintf(stderr, "native-metal-probe: FAIL no Metal device\n");
            return 1;
        }
        printf("native-metal-probe: device=%s class=%s\n", device.name.UTF8String,
               NSStringFromClass([device class]).UTF8String);
        MTLTextureDescriptor *desc = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm
                                                                                     width:16 height:16 mipmapped:NO];
        desc.storageMode = MTLStorageModeShared;
        desc.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead;
        id<MTLTexture> texture = [device newTextureWithDescriptor:desc];
        id<MTLCommandQueue> queue = [device newCommandQueue];
        id<MTLCommandBuffer> command = [queue commandBuffer];
        if (!texture || !queue || !command) {
            fprintf(stderr, "native-metal-probe: FAIL resource creation\n");
            return 2;
        }
        MTLRenderPassDescriptor *pass = [MTLRenderPassDescriptor renderPassDescriptor];
        pass.colorAttachments[0].texture = texture;
        pass.colorAttachments[0].loadAction = MTLLoadActionClear;
        pass.colorAttachments[0].storeAction = MTLStoreActionStore;
        pass.colorAttachments[0].clearColor = MTLClearColorMake(1, 0.25, 0, 1);
        id<MTLRenderCommandEncoder> encoder = [command renderCommandEncoderWithDescriptor:pass];
        if (!encoder) {
            fprintf(stderr, "native-metal-probe: FAIL render encoder\n");
            return 3;
        }
        [encoder endEncoding];
        [command commit];
        [command waitUntilCompleted];
        if (command.status != MTLCommandBufferStatusCompleted) {
            fprintf(stderr, "native-metal-probe: FAIL command status=%lu error=%s\n",
                    (unsigned long)command.status, command.error.description.UTF8String ?: "none");
            return 4;
        }
        unsigned char pixels[16 * 16 * 4] = {0};
        [texture getBytes:pixels bytesPerRow:16 * 4 fromRegion:MTLRegionMake2D(0, 0, 16, 16) mipmapLevel:0];
        unsigned bad = 0;
        for (unsigned i = 0; i < 16 * 16; ++i) {
            unsigned char *p = pixels + 4 * i;
            bad += p[0] > 1 || abs((int)p[1] - 64) > 1 || p[2] < 254 || p[3] < 254;
        }
        printf("native-metal-probe: %s clear/readback bad=%u/256 first=%u,%u,%u,%u\n",
               bad ? "FAIL" : "PASS", bad, pixels[0], pixels[1], pixels[2], pixels[3]);
        return bad ? 5 : 0;
    }
}
