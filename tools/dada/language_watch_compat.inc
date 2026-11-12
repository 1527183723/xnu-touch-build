/* 23G90, Preferences only: this port has no Apple Watch pairing backend.
 * Keep this opt-in at the caller; never change the global cache or daemon state.
 * Validate the actual method, not a nearest exported symbol from a stack trace.
 */
#include <dlfcn.h>
#include <mach-o/dyld.h>
#include <objc/runtime.h>
#include <ptrauth.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

extern const void *_dyld_get_shared_cache_range(size_t *);
static IMP language_original_registry;
static unsigned language_empty_registry_calls;

static id
language_no_watch_registry(id self, SEL selector)
{
    (void)self;
    (void)selector;
    if (++language_empty_registry_calls <= 4)
        fprintf(stderr, "language-compat: no Watch backend; paired registry is nil (%u)\n",
            language_empty_registry_calls);
    return nil;
}

static int
language_install_no_watch(void)
{
    if (language_original_registry) return 1;
    void *handle = dlopen("/System/Library/PrivateFrameworks/NanoRegistry.framework/NanoRegistry",
        RTLD_LAZY | RTLD_LOCAL);
    if (!handle) {
        fprintf(stderr, "language-compat: NanoRegistry load failed: %s\n", dlerror());
        return 0;
    }
    Class cls = objc_getClass("NRPairedDeviceRegistry");
    Method method = cls ? class_getClassMethod(cls, sel_registerName("sharedInstance")) : NULL;
    const char *encoding = method ? method_getTypeEncoding(method) : NULL;
    IMP original = method ? method_getImplementation(method) : NULL;
    uintptr_t address = (uintptr_t)ptrauth_strip(original, ptrauth_key_function_pointer);
    size_t size = 0;
    uintptr_t base = (uintptr_t)_dyld_get_shared_cache_range(&size);
    static const unsigned char bytes[16] = {
        0x7f,0x23,0x03,0xd5,0xff,0x43,0x01,0xd1,
        0xf4,0x4f,0x03,0xa9,0xfd,0x7b,0x04,0xa9
    };
    int valid = size && base >= 0x180000000UL && encoding &&
        !strcmp(encoding, "@16@0:8") &&
        address == 0x1e4a6e368UL + base - 0x180000000UL &&
        !memcmp((const void *)address, bytes, sizeof bytes);
    fprintf(stderr, "language-compat: NRPairedDeviceRegistry sharedInstance address=%lx encoding=%s valid=%d\n",
        address, encoding ? encoding : "missing", valid);
    if (!valid) return 0;
    language_original_registry = method_setImplementation(method, (IMP)language_no_watch_registry);
    int installed = (uintptr_t)ptrauth_strip(method_getImplementation(method),
        ptrauth_key_function_pointer) == (uintptr_t)ptrauth_strip(
        (IMP)language_no_watch_registry, ptrauth_key_function_pointer);
    fprintf(stderr, "language-compat: no-Watch registry installed=%d\n", installed);
    return installed;
}
