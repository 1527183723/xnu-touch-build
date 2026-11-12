#import <Foundation/Foundation.h>
#include <unistd.h>
extern NSString *language_loctable_value(NSBundle *, NSString *, NSString *, NSArray *);
extern int language_install_loctable_compat(void);

int main(void)
{
    alarm(12);
    if (setgid(501) || setuid(501)) return 2;
    @autoreleasepool {
        NSBundle *settings = [NSBundle bundleWithPath:@"/Applications/Preferences.app"];
        NSBundle *international = [NSBundle bundleWithPath:@"/System/Library/PreferenceBundles/InternationalSettings.bundle"];
        int checks = 0, failures = 0;
#define CHECK(test) do { checks++; if (!(test)) { failures++; fprintf(stderr,"loctable-probe: FAIL line=%d\n",__LINE__); } } while (0)
        CHECK([language_loctable_value(settings, @"General", @"Localizable", @[@"zh-Hans-CN", @"en"]) isEqualToString:@"\u901a\u7528"]);
        CHECK([language_loctable_value(international, @"SUGGESTED_LANGUAGES", @"InternationalSettings", @[@"zh-Hans", @"en"]) isEqualToString:@"\u5efa\u8bae\u8bed\u8a00"]);
        CHECK(language_loctable_value(settings, @"General", @"Localizable", @[@"en", @"zh-Hans"]) == nil);
        CHECK(language_loctable_value(settings, @"not-a-real-translation", @"Localizable", @[@"zh-Hans"]) == nil);
        CHECK(language_loctable_value(settings, @"General", @"not-a-table", @[@"zh-Hans"]) == nil);
        CHECK(language_install_loctable_compat());
        NSArray *preferred = CFBridgingRelease(CFLocaleCopyPreferredLanguages());
        NSString *expected = language_loctable_value(settings, @"General", @"Localizable", preferred);
        if (expected) CHECK([[settings localizedStringForKey:@"General" value:@"MISSING" table:@"Localizable"] isEqualToString:expected]);
        fprintf(stderr, "loctable-probe: %s checks=%d failures=%d\n", failures ? "FAIL" : "PASS", checks, failures);
        return failures ? 1 : 0;
    }
}
