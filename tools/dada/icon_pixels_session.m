/* Session-only observer: export the actual CGImages held by SpringBoard.
 * Loads the unmodified compatibility library; never rewrites layer contents. */
#import <Foundation/Foundation.h>
#import <UIKit/UIKit.h>
#import <QuartzCore/QuartzCore.h>
#import <CoreGraphics/CoreGraphics.h>
#include <dlfcn.h>
#include <notify.h>
#include <objc/message.h>
#include <objc/runtime.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

static id ask(id obj, const char *name) {
    SEL sel=sel_registerName(name);
    return [obj respondsToSelector:sel]?((id(*)(id,SEL))objc_msgSend)(obj,sel):nil;
}
static void visit(UIView *view, NSMutableArray *images, unsigned depth) {
    if(depth>64 || images.count>=64)return;
    if(strstr(object_getClassName(view),"SBIconImageView")) {
        CALayer *layer=view.layer;
        id contents=layer.contents;
        NSMutableDictionary *entry=[@{@"view":NSStringFromClass(view.class),
            @"position":NSStringFromCGPoint(layer.position),@"bounds":NSStringFromCGRect(layer.bounds),
            @"contentsRect":NSStringFromCGRect(layer.contentsRect),@"hidden":@(layer.hidden),
            @"opacity":@(layer.opacity),@"contentsFormat":layer.contentsFormat?:@"",
            @"contentsClass":contents?@([contents class]?object_getClassName(contents):"?"):@"nil"} mutableCopy];
        id icon=ask(view,"icon");
        id bid=ask(icon,"applicationBundleID")?:ask(icon,"bundleIdentifier");
        if(bid)entry[@"bundle"]=[bid description];
        if(contents && CFGetTypeID((__bridge CFTypeRef)contents)==CGImageGetTypeID()) {
            CGImageRef image=(__bridge CGImageRef)contents;
            CFDataRef data=CGDataProviderCopyData(CGImageGetDataProvider(image));
            entry[@"width"]=@(CGImageGetWidth(image));entry[@"height"]=@(CGImageGetHeight(image));
            entry[@"row"]=@(CGImageGetBytesPerRow(image));entry[@"bits"]=@(CGImageGetBitsPerPixel(image));
            entry[@"bitmapInfo"]=@(CGImageGetBitmapInfo(image));
            entry[@"imageDescription"]=(__bridge_transfer NSString *)CFCopyDescription(image);
            if(data){entry[@"pixels"]=[(__bridge NSData *)data base64EncodedStringWithOptions:0];CFRelease(data);}
        }
        [images addObject:entry];
    }
    for(UIView *child in view.subviews)visit(child,images,depth+1);
}
static void dump(void) {
    @autoreleasepool {
        @try {
            NSMutableArray *images=[NSMutableArray array];
            for(UIWindow *window in UIApplication.sharedApplication.windows)visit(window,images,0);
            NSDictionary *record=@{@"pid":@(getpid()),@"time":@(NSDate.date.timeIntervalSince1970),
                @"observerOnly":@YES,@"icons":images};
            NSError *error=nil;
            NSData *data=[NSJSONSerialization dataWithJSONObject:record options:0 error:&error];
            BOOL ok=[data writeToFile:@"/private/var/tmp/icon-layer-pixels.json" options:NSDataWritingAtomic error:&error];
            fprintf(stderr,"icon-pixels: observer dump icons=%lu bytes=%lu ok=%d error=%s\n",
                    (unsigned long)images.count,(unsigned long)data.length,ok,error.description.UTF8String?:"none");
        } @catch(NSException *error){fprintf(stderr,"icon-pixels: exception %s\n",error.description.UTF8String);}
    }
}
__attribute__((constructor)) static void start(void) {
    void *original=dlopen("/redwood/lib/libredwood-sb26.dylib",RTLD_NOW|RTLD_GLOBAL);
    fprintf(stderr,"icon-pixels: observer loaded original=%d\n",original!=NULL);
    if(strcmp(getprogname(),"SpringBoard"))return;
    dispatch_async(dispatch_get_main_queue(),^{
        static int token;
        notify_register_dispatch("com.redwood.icons.pixels",&token,dispatch_get_main_queue(),^(int t){(void)t;dump();});
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW,5*NSEC_PER_SEC),dispatch_get_main_queue(),^{dump();});
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW,35*NSEC_PER_SEC),dispatch_get_main_queue(),^{dump();});
    });
}
