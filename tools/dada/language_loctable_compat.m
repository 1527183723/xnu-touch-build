/* Selected 23G90 System keeps multilingual loctables but prunes lproj folders.
 * Restore Simplified Chinese lookup from those retained tables. Never invent a
 * translation or force Chinese ahead of another supported user preference.
 */
#import <Foundation/Foundation.h>
#include <CoreFoundation/CoreFoundation.h>
#include <objc/runtime.h>
#include <ptrauth.h>
#include <stdatomic.h>

static NSCache *language_table_cache;
static NSArray *language_preferences;
static IMP language_original_localized_string;
static _Thread_local int language_lookup_active;
static atomic_uint language_translated_count;

static NSDictionary *language_read_table(NSBundle *bundle, NSString *table)
{
    static dispatch_once_t once;
    dispatch_once(&once, ^{ language_table_cache = [[NSCache alloc] init];
        [language_table_cache setCountLimit:256]; });
    NSString *leaf = [(table.length ? table : @"Localizable") stringByAppendingString:@".loctable"];
    NSString *base = bundle.bundlePath;
    if (!base || [leaf rangeOfString:@"/"].location != NSNotFound) return nil;
    for (NSString *folder in @[@"", @"Resources", @"Contents/Resources"]) {
        NSString *path = [[base stringByAppendingPathComponent:folder] stringByAppendingPathComponent:leaf];
        id contents = [language_table_cache objectForKey:path];
        if (!contents) {
            contents = [NSDictionary dictionaryWithContentsOfFile:path];
            if (![contents isKindOfClass:[NSDictionary class]]) contents = [NSNull null];
            [language_table_cache setObject:contents forKey:path];
        }
        if ([contents isKindOfClass:[NSDictionary class]]) return contents;
    }
    return nil;
}

/* Exposed to the isolated test; the returned string is owned by the table. */
NSString *language_loctable_value(NSBundle *bundle, NSString *key, NSString *table, NSArray *preferences)
{
    if (!bundle || !key || !preferences.count) return nil;
    NSDictionary *contents = language_read_table(bundle, table);
    if (![contents[@"zh_CN"] isKindOfClass:[NSDictionary class]]) return nil;
    NSMutableArray *supported = [NSMutableArray array];
    for (id name in contents) {
        if ([name isKindOfClass:[NSString class]] &&
            [contents[name] isKindOfClass:[NSDictionary class]]) [supported addObject:name];
    }
    CFArrayRef chosen = CFBundleCopyLocalizationsForPreferences((CFArrayRef)supported,
        (CFArrayRef)preferences);
    BOOL chinese = chosen && CFArrayGetCount(chosen) &&
        CFEqual(CFArrayGetValueAtIndex(chosen, 0), CFSTR("zh_CN"));
    if (chosen) CFRelease(chosen);
    if (!chinese) return nil;
    id translation = contents[@"zh_CN"][key];
    return [translation isKindOfClass:[NSString class]] ? translation : nil;
}

static id language_localized_string(id self, SEL selector, id key, id value, id table)
{
    if (!language_lookup_active) {
        language_lookup_active = 1;
        NSString *translated = language_loctable_value(self, key, table, language_preferences);
        language_lookup_active = 0;
        if (translated) {
            unsigned count = atomic_fetch_add(&language_translated_count, 1) + 1;
            if (count <= 3) fprintf(stderr, "language-compat: retained zh_CN translation used (%u)\n", count);
            /* A concurrent NSCache eviction must not invalidate the result. */
            return [[translated retain] autorelease];
        }
    }
    return ((id (*)(id, SEL, id, id, id))language_original_localized_string)(self, selector, key, value, table);
}

int language_install_loctable_compat(void)
{
    if (language_original_localized_string) return 1;
    CFArrayRef preferred = CFLocaleCopyPreferredLanguages();
    language_preferences = [(NSArray *)preferred copy];
    if (preferred) CFRelease(preferred);
    Method method = class_getInstanceMethod([NSBundle class], @selector(localizedStringForKey:value:table:));
    const char *encoding = method ? method_getTypeEncoding(method) : NULL;
    if (!encoding || strcmp(encoding, "@40@0:8@16@24@32")) {
        fprintf(stderr, "language-compat: NSBundle method mismatch: %s\n", encoding ? encoding : "missing");
        return 0;
    }
    language_original_localized_string = method_setImplementation(method, (IMP)language_localized_string);
    int installed = (uintptr_t)ptrauth_strip(method_getImplementation(method), ptrauth_key_function_pointer) ==
        (uintptr_t)ptrauth_strip((IMP)language_localized_string, ptrauth_key_function_pointer);
    fprintf(stderr, "language-compat: NSBundle retained loctable lookup installed=%d\n", installed);
    return installed;
}
