// Shared no-copy fallback: synchronize only the storage used by this command.
// The owner set retains buffers and buffer-backed views until GPU completion.
static NSArray<NBBuffer *> *mirrorBuffers(NSSet *owners) {
 NSMutableSet<NBBuffer *> *buffers=[NSMutableSet set];
 for(id owner in owners) {
  NBBuffer *b=nil;
  if([owner isKindOfClass:NBBuffer.class])b=owner;
  else if([owner isKindOfClass:NBTexture.class])b=((NBTexture *)owner).buffer;
  if(b.mirror && !b.mirrorUnavailable)[buffers addObject:b];
 }
 return buffers.allObjects;
}
static void syncMirrors(NSArray<NBBuffer *> *buffers,bool publish) {
 if(!buffers.count)return;
 const uint64_t began=nowUs();
 // Never wait for GPU completion while holding this lock: completion publishes
 // through the same lock before invoking the client's completed handlers.
 std::lock_guard<std::mutex> sync(gMirrorSyncLock);
 for(NBBuffer *b in buffers) {
  void *gpu=nmb_buffer_contents(b.handle);
  const NSUInteger length=b.mirrorLength;
  if(!gpu || !length || b.mirrorUnavailable)continue;
  if(publish)sys_dcache_flush(gpu,length);
  const void *source=publish?gpu:b.mirror;
  void *destination=publish?b.mirror:gpu;
  vm_size_t copied=0;
  const bool ok=vm_read_overwrite(mach_task_self(),(vm_address_t)source,length,
      (vm_address_t)destination,&copied)==KERN_SUCCESS && copied==length;
  if(ok) {
   sys_dcache_flush(destination,length);
   if(publish){gMirrorBytesPublished+=length;gMirrorBuffersPublished++;}
   else {gMirrorBytesSynced+=length;gMirrorBuffersCopied++;}
  } else {
   b.mirrorUnavailable=YES;
   static std::atomic<unsigned> said;
   if(said++<5)consoleLine([NSString stringWithFormat:
       @"shared mirror %s failed for %lu bytes; caller mapping unavailable",
       publish?"publish":"prepare",(unsigned long)length]);
  }
 }
 gTimeMirrorSync.add(began);
}
static void waitForMirrorBuffer(NBBuffer *buffer);
static void prepareMirrors(NSSet *owners) {
 NSArray<NBBuffer *> *buffers=mirrorBuffers(owners);
 for(NBBuffer *b in buffers)waitForMirrorBuffer(b);
 syncMirrors(buffers,false);
}
static void publishMirrors(NSSet *owners) {
 syncMirrors(mirrorBuffers(owners),true);
}
