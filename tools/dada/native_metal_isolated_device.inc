#include <sys/stat.h>
// Select a private driver only inside this diagnostic process. Never edit the
// discovery path or replace the bundle used by desktop processes.
static id<MTLDevice> isolatedDevice(const char *library) {
    if(!library)return MTLCreateSystemDefaultDevice();
    setenv("FD_DEV_FEATURES","has_early_preamble=0:has_fs_tex_prefetch=0",1);
    setenv("TU_DEBUG","sysmem",1);
    setenv("M2V_BLOCK_MB","8",1);
    mkdir("/private/var/tmp/mirror-cache",0700);
    void *handle=dlopen(library,RTLD_NOW|RTLD_LOCAL);
    id (*openDevice)(const char *,const char *,uint64_t,void *)=handle?dlsym(handle,"native_bridge_metal_open"):NULL;
    if(!openDevice){printf("isolated-metal: dlopen failed %s\n",dlerror());return nil;}
    const char *turnip="/redwood/lib/RedwoodMetal.bundle/libvulkan_freedreno.dylib";
    id<MTLDevice> device=openDevice(turnip,"/private/var/tmp/mirror-cache",0,NULL);
    // Mirror the discovery plugin's import callback setup; without it Turnip
    // cannot associate IOSurface CPU addresses with the RedwoodGPU surface ID.
    void *backend=dlopen(turnip,RTLD_NOW|RTLD_NOLOAD);
    void (*setNote)(void *)=dlsym(handle,"native_bridge_metal_set_surface_note");
    void (*setLost)(void *)=dlsym(handle,"native_bridge_metal_set_lost_reason");
    if(device&&backend&&setNote)setNote(dlsym(backend,"tu_darwin_note_surface"));
    if(device&&backend&&setLost)setLost(dlsym(backend,"tu_darwin_lost_reason"));
    return device;
}
