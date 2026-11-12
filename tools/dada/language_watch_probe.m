/* Independent process: verify the exact language-page Watch query terminates.
 * No Settings, SpringBoard or preference files are modified by this probe.
 */
#import <Foundation/Foundation.h>
#include <objc/message.h>
#include <unistd.h>
#include "language_watch_compat.inc"

int main(int argc, char **argv)
{
    alarm(10);
    @autoreleasepool {
        if (!language_install_no_watch()) return 2;
        Class cls = objc_getClass("NRPairedDeviceRegistry");
        id registry = ((id (*)(id, SEL))objc_msgSend)((id)cls, sel_registerName("sharedInstance"));
        if (registry || language_empty_registry_calls != 1) return 3;
        if (argc == 1) {
            fprintf(stderr, "language-probe: PASS validated registry=nil\n");
            return 0;
        }
        if (argc != 2 || strcmp(argv[1], "bundle")) return 7;
        const char *path = "/System/Library/PreferenceBundles/InternationalSettings.bundle/InternationalSettings";
        fprintf(stderr, "language-probe: loading InternationalSettings in isolated process\n");
        void *bundle = dlopen(path, RTLD_LAZY | RTLD_LOCAL);
        if (!bundle) { fprintf(stderr, "language-probe: dlopen: %s\n", dlerror()); return 4; }
        BOOL (*query)(void) = (BOOL (*)(void))dlsym(bundle, "COSActivePairingIsTinker");
        if (!query) { fprintf(stderr, "language-probe: missing COSActivePairingIsTinker\n"); return 5; }
        for (int i = 0; i < 3; i++) {
            BOOL paired = query();
            fprintf(stderr, "language-probe: COSActivePairingIsTinker call=%d paired=%d\n", i + 1, paired);
            if (paired) return 6;
        }
        fprintf(stderr, "language-probe: PASS registry=nil queries=3\n");
    }
    return 0;
}
