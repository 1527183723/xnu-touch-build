"""Retain desktop process exit evidence without depending on console retention."""
import difflib

CODE = r'''
/* dada app-return diagnostics: bounded history, read-only and per boot. */
LCK_GRP_DECLARE(dada_exit_grp, "dada-exit");
LCK_MTX_DECLARE(dada_exit_lock, &dada_exit_grp);
static char dada_exit_history[1536] = "none";
static unsigned dada_exit_count;
SYSCTL_DECL(_kern_redwood);
static int
dada_exit_sysctl SYSCTL_HANDLER_ARGS
{
    char snapshot[sizeof(dada_exit_history)];
    lck_mtx_lock(&dada_exit_lock);
    strlcpy(snapshot, dada_exit_history, sizeof(snapshot));
    lck_mtx_unlock(&dada_exit_lock);
    return sysctl_handle_string(oidp, snapshot, sizeof(snapshot), req);
}
SYSCTL_PROC(_kern_redwood, OID_AUTO, desktop_exits, CTLTYPE_STRING | CTLFLAG_RD | CTLFLAG_LOCKED,
    0, 0, dada_exit_sysctl, "A", "Recent desktop process exits");

static void
dada_note_desktop_exit(proc_t p, int rv)
{
    const char *name = proc_best_name(p);
    if (strcmp(name, "SpringBoard") && strcmp(name, "backboardd") &&
        strcmp(name, "Preferences")) return;
    char line[256];
    os_reason_t reason = p->p_exit_reason;
    snprintf(line, sizeof(line),
        "pid=%d name=%s rv=%#x signal=%d exit=%d ns=%u code=%#llx flags=%#llx",
        proc_getpid(p), name, rv, WTERMSIG(rv), WEXITSTATUS(rv),
        reason ? reason->osr_namespace : 0, reason ? reason->osr_code : 0,
        reason ? reason->osr_flags : 0);
    lck_mtx_lock(&dada_exit_lock);
    if (!dada_exit_count || strlen(dada_exit_history) + strlen(line) + 32 >= sizeof(dada_exit_history))
        dada_exit_history[0] = 0;
    char entry[280];
    snprintf(entry, sizeof(entry), "#%u %s; ", ++dada_exit_count, line);
    strlcat(dada_exit_history, entry, sizeof(dada_exit_history));
    lck_mtx_unlock(&dada_exit_lock);
    printf("dada desktop exit: %s\n", line);
}

'''

def patch(text):
    anchor = 'void\nproc_prepareexit(proc_t p, int rv, boolean_t perf_notify)\n'
    assert text.count(anchor) == 1 and 'dada_note_desktop_exit' not in text
    text = text.replace(anchor, CODE + anchor)
    anchor = '\ttask_t task = proc_task(p);\n\n\n\tif (p->p_crash_behavior'
    assert text.count(anchor) == 1
    return text.replace(anchor, '\ttask_t task = proc_task(p);\n\n\tdada_note_desktop_exit(p, rv);\n\n\tif (p->p_crash_behavior')
