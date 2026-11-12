#import <Foundation/Foundation.h>
#include <CoreFoundation/CoreFoundation.h>
#include <dlfcn.h>
#include <string.h>
#include <unistd.h>

static void print_cf(const char *label, CFTypeRef value)
{
    if (value && CFGetTypeID(value) == CFStringGetTypeID()) {
        CFStringRef string = (CFStringRef)value;
        CFIndex count = CFStringGetLength(string);
        fprintf(stderr, "language-resource: %s UTF16 length=%ld units=", label, (long)count);
        for (CFIndex i = 0; i < count && i < 40; i++)
            fprintf(stderr, "%04x,", CFStringGetCharacterAtIndex(string, i));
        fprintf(stderr, "\n");
        return;
    }
    CFStringRef description = value ? CFCopyDescription(value) : NULL;
    char text[4096] = "nil";
    if (description) {
        CFStringGetCString(description, text, sizeof text, kCFStringEncodingUTF8);
        CFRelease(description);
    }
    fprintf(stderr, "language-resource: %s = %s\n", label, text);
}

int main(void)
{
    alarm(12);
    if (setgid(501) || setuid(501)) return 2;
    @autoreleasepool {
        typedef CFStringRef (*localized_fn)(CFBundleRef, CFStringRef, CFStringRef, CFStringRef, CFStringRef);
        localized_fn localized = (localized_fn)dlsym(RTLD_DEFAULT, "CFBundleCopyLocalizedStringForLocalization");
        if (!localized) { fprintf(stderr, "language-resource: explicit localization API unavailable\n"); return 3; }
        CFArrayRef preferred = CFLocaleCopyPreferredLanguages();
        print_cf("preferred languages", preferred);
        const char *paths[] = {"/Applications/Preferences.app",
            "/System/Library/PreferenceBundles/InternationalSettings.bundle"};
        CFStringRef keys[] = {CFSTR("General"), CFSTR("SUGGESTED_LANGUAGES")};
        CFStringRef tables[] = {CFSTR("Localizable"), CFSTR("InternationalSettings")};
        for (int i = 0; i < 2; i++) {
            fprintf(stderr, "language-resource: bundle %s\n", paths[i]);
            CFURLRef url = CFURLCreateFromFileSystemRepresentation(NULL,
                (const UInt8 *)paths[i], strlen(paths[i]), true);
            CFBundleRef bundle = CFBundleCreate(NULL, url);
            CFRelease(url);
            if (!bundle) return 4;
            CFArrayRef all = CFBundleCopyBundleLocalizations(bundle);
            print_cf("bundle localizations", all);
            CFArrayRef chosen = all ? CFBundleCopyPreferredLocalizationsFromArray(all) : NULL;
            print_cf("chosen localizations", chosen);
            if (chosen) CFRelease(chosen);
            if (all) CFRelease(all);
            const void *options[] = {CFSTR("en"), CFSTR("zh_CN"), CFSTR("zh_TW")};
            CFArrayRef candidates = CFArrayCreate(NULL, options, 3, &kCFTypeArrayCallBacks);
            CFArrayRef matched = CFBundleCopyLocalizationsForPreferences(candidates, preferred);
            print_cf("loctable candidate match", matched);
            if (matched) CFRelease(matched);
            CFRelease(candidates);
            CFStringRef value = CFBundleCopyLocalizedString(bundle, keys[i], CFSTR("MISSING"), tables[i]);
            print_cf("default translation", value);
            if (value) CFRelease(value);
            CFStringRef languages[] = {CFSTR("zh_CN"), CFSTR("zh-Hans"), CFSTR("zh-Hans-CN"), CFSTR("en")};
            for (int k = 0; k < 4; k++) {
                print_cf("explicit language", languages[k]);
                value = localized(bundle, keys[i], CFSTR("MISSING"), tables[i], languages[k]);
                print_cf("explicit translation", value);
                if (value) CFRelease(value);
            }
            CFRelease(bundle);
        }
        if (preferred) CFRelease(preferred);
    }
    return 0;
}
