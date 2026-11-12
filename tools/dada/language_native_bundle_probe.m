/* Verify that restoring real resources fixes native CFBundle discovery.
 * Writes disposable baseline, XML and binary bundles under /private/var/tmp.
 */
#import <Foundation/Foundation.h>
#include <spawn.h>
#include <sys/wait.h>
#include <unistd.h>
extern char **environ;

int main(int argc, char **argv)
{
    alarm(12);
    if (setgid(501) || setuid(501)) return 2;
    @autoreleasepool {
        if (argc == 2 && !strcmp(argv[1], "verify")) {
            NSBundle *bundle = [NSBundle mainBundle];
            BOOL chinese = ![bundle.bundleIdentifier hasSuffix:@".0"];
            NSString *value = [bundle localizedStringForKey:@"General" value:@"MISSING" table:@"Localizable"];
            NSString *display = [bundle objectForInfoDictionaryKey:@"CFBundleDisplayName"];
            BOOL correct = [value isEqualToString:chinese ? @"\u901a\u7528" : @"General"];
            BOOL info = !chinese || [display isEqualToString:@"\u8bbe\u7f6e"];
            fprintf(stderr, "native-locale-probe: main_bundle=%s translation_ok=%d display_ok=%d\n",
                [bundle.bundleIdentifier UTF8String], correct, info);
            return correct && info ? 0 : 10;
        }
        NSFileManager *fm = [NSFileManager defaultManager];
        int failures = 0;
        for (int localized = 0; localized < 3; localized++) {
            NSString *path = [NSString stringWithFormat:@"/private/var/tmp/native-locale-%d-%d.app", getpid(), localized];
            if (![fm createDirectoryAtPath:[path stringByAppendingPathComponent:@"en.lproj"]
                withIntermediateDirectories:YES attributes:nil error:nil]) return 3;
            if (localized && ![fm createDirectoryAtPath:[path stringByAppendingPathComponent:@"zh-Hans.lproj"]
                withIntermediateDirectories:NO attributes:nil error:nil]) return 4;
            NSDictionary *info = @{@"CFBundleIdentifier":[NSString stringWithFormat:@"com.redwood.test.%d.%d", getpid(), localized],
                @"CFBundleDevelopmentRegion":@"en", @"CFBundleName":@"Preferences",
                @"CFBundlePackageType":@"APPL", @"CFBundleExecutable":@"Probe"};
            if (![info writeToFile:[path stringByAppendingPathComponent:@"Info.plist"] atomically:YES]) return 5;
            for (NSString *name in @[@"Localizable.loctable", @"InfoPlist.loctable"]) {
                NSString *from = [@"/Applications/Preferences.app" stringByAppendingPathComponent:name];
                if (![fm copyItemAtPath:from toPath:[path stringByAppendingPathComponent:name] error:nil]) return 6;
                if (localized) {
                    NSDictionary *all = [NSDictionary dictionaryWithContentsOfFile:from];
                    NSDictionary *chinese = all[@"zh_CN"];
                    NSString *file = [[[path stringByAppendingPathComponent:@"zh-Hans.lproj"]
                        stringByAppendingPathComponent:[name stringByDeletingPathExtension]] stringByAppendingPathExtension:@"strings"];
                    NSData *data = [NSPropertyListSerialization dataWithPropertyList:chinese
                        format:localized == 2 ? NSPropertyListBinaryFormat_v1_0 : NSPropertyListXMLFormat_v1_0
                        options:0 error:nil];
                    if (!data || ![data writeToFile:file atomically:YES]) return 7;
                }
            }
            NSString *executable = [path stringByAppendingPathComponent:@"Probe"];
            if (![fm copyItemAtPath:[NSString stringWithUTF8String:argv[0]] toPath:executable error:nil]) return 8;
            char *arguments[] = {(char *)executable.fileSystemRepresentation, "verify", NULL};
            pid_t child;
            if (posix_spawn(&child, arguments[0], NULL, NULL, arguments, environ)) return 9;
            int status = 0;
            if (waitpid(child, &status, 0) != child || !WIFEXITED(status) || WEXITSTATUS(status)) failures++;
        }
        fprintf(stderr, "native-locale-probe: %s failures=%d\n", failures ? "FAIL" : "PASS", failures);
        return failures ? 1 : 0;
    }
}
