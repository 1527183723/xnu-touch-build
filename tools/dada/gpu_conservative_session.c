/* Session-only Turnip experiment. No Metal linkage: set flags before device creation. */
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <sys/sysctl.h>
#include <sys/stat.h>
#ifdef GPU_SESSION_LIBRARY
__attribute__((constructor)) static void configure(void) {
    setenv("TU_DEBUG","sysmem,nocb,noubwc,flushall,syncdraw",1);
    setenv("CA_DISABLE_FRAMEBUFFER_COMPRESSION","1",1);
    fprintf(stderr,"gpu-session: %s TU_DEBUG=%s CA_DISABLE_FRAMEBUFFER_COMPRESSION=1\n",getprogname(),getenv("TU_DEBUG"));
}
#else
int main(int argc,char **argv) {
    const char *group="/private/var/tmp/libgpu-safe.dylib SpringBoard backboardd; ";
    char before[512]={0},after[512]={0};size_t size=sizeof before;
    int apply=argc==2&&!strcmp(argv[1],"apply");
    if(argc>2||(argc==2&&!apply))return 2;
    if(sysctlbyname("kern.exec_inject",before,&size,NULL,0)||!memchr(before,0,sizeof before))return 3;
    if(apply){
        struct stat st;if(stat("/private/var/tmp/libgpu-safe.dylib",&st)||strstr(before,"libgpu-safe.dylib"))return 4;
        if(snprintf(after,sizeof after,"%s%s",group,before)>=(int)sizeof after)return 5;
    }else{
        if(strncmp(before,group,strlen(group)))return 6;
        snprintf(after,sizeof after,"%s",before+strlen(group));
    }
    if(sysctlbyname("kern.exec_inject",NULL,NULL,after,strlen(after)+1))return 7;
    size=sizeof before;
    if(sysctlbyname("kern.exec_inject",before,&size,NULL,0)||strcmp(before,after))return 8;
    printf("gpu-session: injection=%s\n",after);return 0;
}
#endif
