/* dada stock DT and SM8750 pinctrl/SPMI v7 register definitions.
 * Downstream sun DT spans F000000; the GPIO window is at +100000.
 * Upstream pinctrl@f100000 and the supplied donor kernel agree on this window.
 */
#ifndef _SYS_DADA_INPUT_BOARD_H_
#define _SYS_DADA_INPUT_BOARD_H_
#include <stdint.h>
#define DADA_TLMM_PA 0x0f100000UL
#define DADA_TLMM_SIZE 0x102000UL
#define DADA_SPI_SE_PA 0x00a88000UL
#define DADA_ARB_CORE_PA 0x0c400000UL
#define DADA_ARB_CNFG_PA 0x0c42d000UL
#define DADA_ARB_OBSERVER_PA 0x0c440000UL
#define DADA_ARB_CORE_SIZE 0x3000UL
#define DADA_ARB_CNFG_SIZE 0x4000UL
#define DADA_ARB_OBSERVER_SIZE 0x80000UL
#define DADA_ARB_MAX_CHANNELS 1024u
#define DADA_ARB_SYSTEM 0u
#define DADA_ARB_CHANNEL(n) (0x2000u + 4u * (n))
#define DADA_ARB_OWNER(n) (4u * (n))
#define DADA_ARB_OBSERVER(n) (0x8000u * DADA_ARB_SYSTEM + 0x20u * (n))
#define DADA_PON_PPID 0x013u
/* pm8550@1/pinctrl@8800, GPIO6: 8800 + 100 * (6 - 1). */
#define DADA_VOLUME_UP_PPID 0x18du
static inline int dada_arb_version_supported(uint32_t v) {
    return v >= 0x70000000u && v < 0x80000000u;
}
#ifdef KERNEL
void dada_buttons_stop(void);
#endif
#endif
