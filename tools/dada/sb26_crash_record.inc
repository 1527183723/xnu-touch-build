/* Write fault context before any stack unwinder or runtime inspection. The
 * original logger called backtrace() first; a second fault lost all evidence. */
#include <sys/ucontext.h>
#include <mach/mach.h>
#include <dlfcn.h>
static int sb26_fault_fd = -1;
static uintptr_t sb26_fault_cache;
static volatile sig_atomic_t sb26_in_fault;

static void
sb26_fault_line(const char *line, size_t size)
{
    if (sb26_fault_fd >= 0) (void)write(sb26_fault_fd, line, size);
    (void)write(2, line, size);
}

static void
sb26_crash_logger(int number, siginfo_t *info, void *context)
{
    if (sb26_in_fault) _exit(128 + number);
    sb26_in_fault = 1;
    char line[512];
    int n = snprintf(line, sizeof(line), "sb26 FAULT pid=%d signal=%d address=%p cache=%#lx\n",
        getpid(), number, info ? info->si_addr : NULL, (unsigned long)sb26_fault_cache);
    sb26_fault_line(line, (size_t)n);
    const ucontext_t *uc = context;
    if (uc && uc->uc_mcontext) {
        const uint64_t *x = uc->uc_mcontext->__ss.__x;
        uintptr_t pc = __darwin_arm_thread_state64_get_pc(uc->uc_mcontext->__ss);
        uintptr_t lr = __darwin_arm_thread_state64_get_lr(uc->uc_mcontext->__ss);
        uintptr_t fp = __darwin_arm_thread_state64_get_fp(uc->uc_mcontext->__ss);
        n = snprintf(line, sizeof(line), "pc=%#lx lr=%#lx fp=%#lx far=%#llx esr=%#x\n",
            (unsigned long)pc, (unsigned long)lr, (unsigned long)fp,
            (unsigned long long)uc->uc_mcontext->__es.__far, uc->uc_mcontext->__es.__esr);
        sb26_fault_line(line, (size_t)n);
        for (unsigned i = 0; i < 29; i += 3) {
            n = snprintf(line, sizeof(line), "x%u=%#llx x%u=%#llx x%u=%#llx\n",
                i, (unsigned long long)x[i], i+1, (unsigned long long)(i+1<29?x[i+1]:0),
                i+2, (unsigned long long)(i+2<29?x[i+2]:0));
            sb26_fault_line(line, (size_t)n);
        }
        for (unsigned i = 0; i < 20 && fp > 0x1000 && !(fp & 15); i++) {
            uint64_t pair[2] = {0}; vm_size_t got = 0;
            if (vm_read_overwrite(mach_task_self(), fp, sizeof(pair),
                (vm_address_t)pair, &got) != KERN_SUCCESS || got != sizeof(pair)) break;
            uintptr_t at = (uintptr_t)ptrauth_strip((void *)pair[1], ptrauth_key_return_address);
            n = snprintf(line, sizeof(line), "frame%u=%#lx\n", i, (unsigned long)at);
            sb26_fault_line(line, (size_t)n);
            if (pair[0] <= fp || pair[0] - fp > (1u << 20)) break;
            fp = pair[0];
        }
    }
    signal(number, SIG_DFL);
    raise(number);
}

static void
sb26_install_crash_logger(void)
{
    char path[160];
    snprintf(path, sizeof(path), "/private/var/tmp/sb26-crash-%d.txt", getpid());
    sb26_fault_fd = open(path, O_WRONLY | O_CREAT | O_APPEND, 0600);
    size_t size = 0;
    sb26_fault_cache = (uintptr_t)_dyld_get_shared_cache_range(&size);
    static const int signals[] = {SIGSEGV, SIGBUS, SIGABRT, SIGILL, SIGFPE, SIGTRAP};
    struct sigaction action = {0};
    action.sa_sigaction = sb26_crash_logger;
    action.sa_flags = SA_SIGINFO | SA_RESETHAND;
    sigemptyset(&action.sa_mask);
    for (unsigned i = 0; i < sizeof(signals)/sizeof(signals[0]); i++)
        sigaction(signals[i], &action, NULL);
    fprintf(stderr, "sb26: persistent fault context ready pid=%d file=%s fd=%d\n",
        getpid(), path, sb26_fault_fd);
}
