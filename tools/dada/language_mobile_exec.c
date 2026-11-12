/* Start a test with mobile credentials before Foundation is initialized. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
int main(int argc, char **argv)
{
    if (argc != 2 || strncmp(argv[1], "/private/var/tmp/", 17)) return 2;
    if (setgid(501) || setuid(501)) { perror("mobile credentials"); return 3; }
    unsetenv("HOME");
    unsetenv("CFFIXED_USER_HOME");
    execv(argv[1], &argv[1]);
    perror("mobile exec");
    return 4;
}
