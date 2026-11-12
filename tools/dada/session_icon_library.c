/* Switch only the SB compatibility library in the running RAM session.
 * Other injection groups are preserved. No disk or bootloader state is changed.
 * Run without arguments to restore the original library for subsequent execs. */
#include <sys/sysctl.h>
#include <sys/stat.h>
#include <stdio.h>
#include <string.h>

int main(int argc, char **argv) {
    const char *original="/redwood/lib/libredwood-sb26.dylib";
    const char *trial="/private/var/tmp/libsb26-icons.dylib";
    const char *from=argc==2?original:trial;
    const char *to=argc==2?trial:original;
    char before[512]={0}, after[512]; size_t size=sizeof before;
    struct stat st;
    if(argc==4 && !strcmp(argv[1],"replace")) {from=argv[2];to=argv[3];}
    else if(argc>2 || (argc==2 && strcmp(argv[1],"apply")))return 2;
    if(from[0]!='/' || to[0]!='/' || strpbrk(from," ;\t\r\n") ||
       strpbrk(to," ;\t\r\n") || stat(to,&st) || !S_ISREG(st.st_mode))return 2;
    if(sysctlbyname("kern.exec_inject",before,&size,NULL,0)) {perror("get kern.exec_inject");return 3;}
    if(!memchr(before,0,sizeof before))return 4;
    char *at=strstr(before,from);
    if(!at || strstr(at+strlen(from),from) ||
       (at!=before && at[-1]!=' ' && at[-1]!=';') ||
       (at[strlen(from)]!=' ' && at[strlen(from)]!=0))return 5;
    int n=snprintf(after,sizeof after,"%.*s%s%s",(int)(at-before),before,to,at+strlen(from));
    if(n<0 || n>=(int)sizeof after)return 6;
    printf("icons-session: before=%s\nafter=%s\n",before,after);
    if(sysctlbyname("kern.exec_inject",NULL,NULL,after,(size_t)n+1)) {perror("set kern.exec_inject");return 7;}
    size=sizeof before;
    if(sysctlbyname("kern.exec_inject",before,&size,NULL,0) || strcmp(before,after))return 8;
    printf("icons-session: verified; applies at next process start\n");
    return 0;
}
