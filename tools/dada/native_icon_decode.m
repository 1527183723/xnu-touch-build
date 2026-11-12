/* Export icon pixels before any Metal upload, without altering SpringBoard.
 * The host compares the PNG, UIKit CGImage bytes and a bitmap-context round trip. */
#import <Foundation/Foundation.h>
#import <UIKit/UIKit.h>
#import <CoreGraphics/CoreGraphics.h>
#include <stdio.h>

static NSDictionary *export_image(CGImageRef image, NSString *prefix) {
    if (!image) return @{ @"error": @"no CGImage" };
    size_t w=CGImageGetWidth(image),h=CGImageGetHeight(image);
    CFDataRef raw=CGDataProviderCopyData(CGImageGetDataProvider(image));
    BOOL written=raw&&[(__bridge NSData *)raw writeToFile:[prefix stringByAppendingString:@".raw"] atomically:YES];
    NSDictionary *result=@{@"width":@(w),@"height":@(h),@"row":@(CGImageGetBytesPerRow(image)),
        @"bpc":@(CGImageGetBitsPerComponent(image)),@"bpp":@(CGImageGetBitsPerPixel(image)),
        @"bitmapInfo":@(CGImageGetBitmapInfo(image)),@"bytes":@(raw?CFDataGetLength(raw):0),@"written":@(written)};
    if(raw)CFRelease(raw);
    return result;
}

int main(int argc,char **argv) {
    @autoreleasepool {
        NSString *output=@"/private/var/tmp/icon-decode";
        NSFileManager *fm=[NSFileManager defaultManager];
        NSError *error=nil;
        if(![fm createDirectoryAtPath:output withIntermediateDirectories:YES attributes:nil error:&error]){
            fprintf(stderr,"icon-decode: mkdir %s\n",error.description.UTF8String);return 2;
        }
        NSArray *bundles=argc==2?@[@(argv[1])]:@[@"/Applications/Preferences.app",@"/Applications/AppStore.app",
            @"/Applications/MobileSafari.app",@"/System/Applications/Preferences.app",@"/System/Applications/AppStore.app"];
        NSArray *names=@[@"AppIcon60x60@2x.png",@"AppIcon60x60@3x.png",@"AppIcon76x76@2x.png",
            @"SettingsApp60x60@2x.png",@"Icon-60@2x.png",@"Icon@2x.png",@"Icon.png"];
        NSMutableArray *results=[NSMutableArray array];
        unsigned count=0;
        for(NSString *bundle in bundles){
            NSString *path=nil;
            for(NSString *name in names){
                NSString *candidate=[bundle stringByAppendingPathComponent:name];
                if([fm isReadableFileAtPath:candidate]){path=candidate;break;}
            }
            if(!path){
                for(NSString *name in [fm contentsOfDirectoryAtPath:bundle error:nil]){
                    if([name hasSuffix:@".png"]&&[[name lowercaseString] containsString:@"icon"]){
                        path=[bundle stringByAppendingPathComponent:name];break;
                    }
                }
            }
            if(!path)continue;
            NSString *prefix=[output stringByAppendingPathComponent:[NSString stringWithFormat:@"%u",count++]];
            UIImage *ui=[UIImage imageWithContentsOfFile:path];CGImageRef image=ui.CGImage;
            if(!image){printf("icon-decode: no CGImage %s\n",path.UTF8String);continue;}
            NSData *source=[NSData dataWithContentsOfFile:path];
            BOOL sourceWritten=[source writeToFile:[prefix stringByAppendingString:@".png"] atomically:YES];
            NSDictionary *original=export_image(image,[prefix stringByAppendingString:@"-source"]);
            size_t w=CGImageGetWidth(image),h=CGImageGetHeight(image);
            CGColorSpaceRef cs=CGColorSpaceCreateDeviceRGB();
            CGContextRef ctx=CGBitmapContextCreate(NULL,w,h,8,w*4,cs,kCGImageAlphaPremultipliedLast|kCGBitmapByteOrder32Big);
            CGColorSpaceRelease(cs);
            NSDictionary *baked=@{@"error":@"no bitmap context"};
            if(ctx){
                CGRect box=CGRectMake(0,0,w,h);CGFloat radius=MIN(w,h)*0.2237;
                CGPathRef clip=CGPathCreateWithRoundedRect(box,radius,radius,NULL);
                if(clip){CGContextAddPath(ctx,clip);CGContextClip(ctx);CGPathRelease(clip);}
                CGContextDrawImage(ctx,box,image);
                CGImageRef copy=CGBitmapContextCreateImage(ctx);
                baked=export_image(copy,[prefix stringByAppendingString:@"-baked"]);
                if(copy)CGImageRelease(copy);CGContextRelease(ctx);
            }
            [results addObject:@{@"index":@(count-1),@"source":path,@"sourcePNGWritten":@(sourceWritten),
                @"original":original,@"baked":baked}];
            printf("icon-decode: saved %u %s %zux%zu\n",count-1,path.UTF8String,w,h);
        }
        NSData *json=[NSJSONSerialization dataWithJSONObject:results options:NSJSONWritingPrettyPrinted error:&error];
        if(!json||![json writeToFile:[output stringByAppendingPathComponent:@"manifest.json"] atomically:YES])return 3;
        printf("icon-decode: exported %lu icons before GPU; not a SpringBoard memory dump\n",(unsigned long)results.count);
        return results.count?0:1;
    }
}
