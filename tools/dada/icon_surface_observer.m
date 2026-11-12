/* RAM-session observer for native RenderBox icon surfaces. Never rewrites layers. */
#import <Foundation/Foundation.h>
#import <UIKit/UIKit.h>
#import <QuartzCore/QuartzCore.h>
#import <IOSurface/IOSurfaceRef.h>
#include <dlfcn.h>
#include <notify.h>
#include <math.h>
#include <objc/message.h>
#include <objc/runtime.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

#ifdef DADA_ICON_GPU_TRACE
#include "native_icon_trace.m"
#endif
#ifdef DADA_ICON_UPLOAD_TRACE
#include "native_icon_upload_trace.m"
#endif
#ifdef DADA_ICON_SDF_TRACE
#include "native_icon_sdf_trace.m"
#endif

static NSString *brief(id object) {
    @try {
        NSString *s = [object description] ?: @"nil";
        return s.length > 1800 ? [s substringToIndex:1800] : s;
    } @catch (NSException *e) { return e.name; }
}

static id ask(id object, const char *name) {
    SEL selector = sel_registerName(name);
    return [object respondsToSelector:selector] ?
        ((id (*)(id, SEL))objc_msgSend)(object, selector) : nil;
}

static void surface_metadata(id contents, CGRect contentsRect, NSMutableDictionary *entry,
                             NSMutableSet *exported) {
    Class cls = object_getClass(contents);
    entry[@"contentsClass"] = @(class_getName(cls));
    entry[@"description"] = brief(contents);
    NSMutableArray *ivars = [NSMutableArray array];
    IOSurfaceRef surface = NULL;
    for (Class c = cls; c && c != NSObject.class; c = class_getSuperclass(c)) {
        unsigned count = 0;
        Ivar *list = class_copyIvarList(c, &count);
        for (unsigned i = 0; i < count; ++i) {
            const char *name = ivar_getName(list[i]);
            const char *type = ivar_getTypeEncoding(list[i]);
            [ivars addObject:@{@"name":@(name ?: "?"), @"type":@(type ?: "?"),
                              @"offset":@(ivar_getOffset(list[i]))}];
            /* Only dereference the runtime-declared IOSurface pointer type. */
            BOOL direct = type && !strncmp(type, "^{__IOSurface", 13);
            /* This single-pointer cf_ptr layout was read from this build's runtime. */
            BOOL wrapper = type && !strcmp(type,
                "{cf_ptr<__IOSurface *>=\"_p\"^{__IOSurface}}");
            if (name && !strcmp(name, "_surface") && (direct || wrapper) &&
                ivar_getOffset(list[i]) >= 0 &&
                (size_t)ivar_getOffset(list[i]) + sizeof(void *) <= class_getInstanceSize(cls)) {
                void *pointer = NULL;
                memcpy(&pointer, (const char *)(__bridge void *)contents +
                       ivar_getOffset(list[i]), sizeof pointer);
                if (pointer && CFGetTypeID(pointer) == IOSurfaceGetTypeID())
                    surface = (IOSurfaceRef)pointer;
            }
        }
        free(list);
    }
    entry[@"ivars"] = ivars;
    if (!surface && CFGetTypeID((__bridge CFTypeRef)contents) == IOSurfaceGetTypeID())
        surface = (__bridge IOSurfaceRef)contents;
    if (!surface) return;
    size_t w = IOSurfaceGetWidth(surface), h = IOSurfaceGetHeight(surface);
    size_t row = IOSurfaceGetBytesPerRow(surface), size = IOSurfaceGetAllocSize(surface);
    uint32_t sid = IOSurfaceGetID(surface), format = IOSurfaceGetPixelFormat(surface);
    BOOL xr = format == 0x62336138 && IOSurfaceGetPlaneCount(surface) == 2;
    NSMutableArray *planes = [NSMutableArray array];
    for (size_t i = 0; i < IOSurfaceGetPlaneCount(surface) && i < 4; ++i) {
        [planes addObject:@{@"width":@(IOSurfaceGetWidthOfPlane(surface, i)),
            @"height":@(IOSurfaceGetHeightOfPlane(surface, i)),
            @"row":@(IOSurfaceGetBytesPerRowOfPlane(surface, i)),
            @"element":@(IOSurfaceGetBytesPerElementOfPlane(surface, i))}];
    }
    entry[@"surface"] = @{@"id":@(sid), @"width":@(w), @"height":@(h),
        @"row":@(row), @"format":@(format), @"allocation":@(size),
        @"planes":@(IOSurfaceGetPlaneCount(surface)),
        @"bytesPerElement":@(IOSurfaceGetBytesPerElement(surface))};
    entry[@"planeInfo"] = planes;
    size_t x = llround(contentsRect.origin.x * w), y = llround(contentsRect.origin.y * h);
    size_t cw = llround(contentsRect.size.width * w), ch = llround(contentsRect.size.height * h);
    NSString *key = [NSString stringWithFormat:@"%u-%zu-%zu", sid, x, y];
    BOOL priority = [entry[@"bundle"] isEqual:@"com.apple.Preferences"];
    if ((exported.count >= 4 && !priority) || [exported containsObject:key] || !w || !h ||
        !cw || !ch || cw > 512 || ch > 512 || x > w || y > h ||
        cw > w - x || ch > h - y || h > 4096 || row > (32u << 20) / h || row * h > size ||
        (format != 0x42475241 && format != 0x52474241 && !xr)) return;
    kern_return_t lock = IOSurfaceLock(surface, 1, NULL);
    entry[@"lock"] = @(lock);
    if (lock != 0) return;
    void *base = IOSurfaceGetBaseAddress(surface);
    NSString *file = [NSString stringWithFormat:@"/private/var/tmp/icon-surface-%@.raw", key];
    BOOL ok = NO;
    if (xr) {
        base = IOSurfaceGetBaseAddressOfPlane(surface, 0);
        row = IOSurfaceGetBytesPerRowOfPlane(surface, 0);
    }
    if (base && row >= w * 4) {
        NSMutableData *bytes = [NSMutableData dataWithLength:cw * ch * 4];
        for (size_t iy = 0; iy < ch; ++iy)
            memcpy((char *)bytes.mutableBytes + iy * cw * 4,
                   (char *)base + (y + iy) * row + x * 4, cw * 4);
        ok = [bytes writeToFile:file options:NSDataWritingAtomic error:nil];
    }
    if (xr && ok) {
        void *alpha = IOSurfaceGetBaseAddressOfPlane(surface, 1);
        size_t ar = IOSurfaceGetBytesPerRowOfPlane(surface, 1);
        size_t aw = IOSurfaceGetWidthOfPlane(surface, 1), ah = IOSurfaceGetHeightOfPlane(surface, 1);
        if (alpha && ar >= aw && x <= aw && y <= ah && cw <= aw - x && ch <= ah - y) {
            NSMutableData *bytes = [NSMutableData dataWithLength:cw * ch];
            for (size_t iy = 0; iy < ch; ++iy)
                memcpy((char *)bytes.mutableBytes + iy * cw,
                       (char *)alpha + (y + iy) * ar + x, cw);
            NSString *file = [NSString stringWithFormat:@"/private/var/tmp/icon-alpha-%@.raw", key];
            if ([bytes writeToFile:file options:NSDataWritingAtomic error:nil]) entry[@"alphaRaw"] = file;
        }
    }
    IOSurfaceUnlock(surface, 1, NULL);
    if (ok) { entry[@"raw"] = file;
        entry[@"crop"] = @{@"x":@(x), @"y":@(y), @"width":@(cw), @"height":@(ch), @"row":@(cw*4)};
        [exported addObject:key]; }
}

static void visit(CALayer *layer, NSMutableArray *records, NSMutableSet *exported,
                  unsigned depth, NSString *bundle) {
    if (depth > 64 || records.count >= 80) return;
    id delegate = layer.delegate;
    if (delegate && strstr(object_getClassName(delegate), "SBIcon")) {
        id icon = ask(delegate, "icon");
        id bid = ask(icon, "applicationBundleID") ?: ask(icon, "bundleIdentifier");
        if ([bid isKindOfClass:NSString.class]) bundle = bid;
    }
    if (strstr(object_getClassName(layer), "RBSurfaceContentsLayer")) {
        NSMutableDictionary *entry = [@{@"layer":@(object_getClassName(layer)),
            @"bounds":NSStringFromCGRect(layer.bounds), @"position":NSStringFromCGPoint(layer.position),
            @"contentsRect":NSStringFromCGRect(layer.contentsRect), @"hidden":@(layer.hidden)} mutableCopy];
        if (bundle) entry[@"bundle"] = bundle;
        id contents = layer.contents;
        if (contents) surface_metadata(contents, layer.contentsRect, entry, exported);
        [records addObject:entry];
    }
    for (CALayer *child in layer.sublayers) visit(child, records, exported, depth + 1, bundle);
}

static void dump(void) {
    @autoreleasepool {
        @try {
            NSMutableArray *records = [NSMutableArray array];
            NSMutableSet *exported = [NSMutableSet set];
            for (UIWindow *window in UIApplication.sharedApplication.windows)
                visit(window.layer, records, exported, 0, nil);
            NSDictionary *record = @{@"pid":@(getpid()), @"observerOnly":@YES,
                @"time":@(NSDate.date.timeIntervalSince1970), @"surfaces":records};
            NSData *data = [NSJSONSerialization dataWithJSONObject:record options:0 error:nil];
            BOOL ok = [data writeToFile:@"/private/var/tmp/icon-surfaces.json"
                               options:NSDataWritingAtomic error:nil];
            fprintf(stderr, "icon-surfaces: layers=%lu exported=%lu bytes=%lu ok=%d\n",
                    (unsigned long)records.count, (unsigned long)exported.count,
                    (unsigned long)data.length, ok);
        } @catch (NSException *e) {
            fprintf(stderr, "icon-surfaces: exception %s\n", e.description.UTF8String);
        }
    }
}

__attribute__((constructor)) static void start(void) {
    void *base = dlopen("/redwood/lib/libredwood-sb26.dylib", RTLD_NOW | RTLD_GLOBAL);
    fprintf(stderr, "icon-surfaces: observer loaded native-profile=%d\n", base != NULL);
    if (strcmp(getprogname(), "SpringBoard")) return;
    dispatch_async(dispatch_get_main_queue(), ^{
        static int token;
        notify_register_dispatch("com.redwood.icons.surfaces", &token,
            dispatch_get_main_queue(), ^(int ignored) { (void)ignored; dump(); });
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 25 * NSEC_PER_SEC),
                       dispatch_get_main_queue(), ^{ dump(); });
    });
}
