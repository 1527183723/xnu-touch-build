/* Limit the observer export to fit the resident diagnostic outbox. */
#import <Foundation/Foundation.h>
int main(void) {
    @autoreleasepool {
        NSData *source=[NSData dataWithContentsOfFile:@"/private/var/tmp/icon-layer-pixels.json"];
        NSDictionary *record=source?[NSJSONSerialization JSONObjectWithData:source options:0 error:nil]:nil;
        if(![record isKindOfClass:NSDictionary.class])return 2;
        NSMutableArray *icons=[NSMutableArray array];
        for(NSDictionary *entry in record[@"icons"]) {
            if(entry[@"pixels"]){[icons addObject:entry];if(icons.count==8)break;}
        }
        if(!icons.count)return 3;
        NSMutableDictionary *small=[record mutableCopy];small[@"icons"]=icons;
        small[@"originalCount"]=@([record[@"icons"] count]);
        NSData *result=[NSJSONSerialization dataWithJSONObject:small options:0 error:nil];
        printf("icon-pixels-read: %lu icons, %lu bytes\n",(unsigned long)icons.count,(unsigned long)result.length);
        return [result writeToFile:@"/private/var/tmp/icon-pixels-small.json" atomically:YES]?0:4;
    }
}
