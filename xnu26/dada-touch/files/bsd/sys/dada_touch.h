/*
 * dada Synaptics TouchCom v1.  Wire definitions were checked against Xiaomi's
 * dada-v-oss touch driver (e02de97c3329367b76201086c9e9b8d995ec809b).
 * This core has no MMIO or kernel dependencies so its failure paths can be tested.
 */
#ifndef _SYS_DADA_TOUCH_H_
#define _SYS_DADA_TOUCH_H_
#include <stdint.h>
#include <stddef.h>

#define DADA_TCM_CONTACTS 16
#define DADA_TCM_PAYLOAD 4096
#define DADA_TCM_CONFIG 512
/* Same vendor continued-read protocol; shorter bounded GPIO transactions. */
#define DADA_TCM_READ_CHUNK 128
#define DADA_TCM_IDENTIFY 0x10
#define DADA_TCM_TOUCH 0x11
#define DADA_TCM_THP 0xc0
#define DADA_TCM_OK 0x01
#define DADA_TCM_CRC 1u
#define DADA_TCM_EXTRA_RC 2u

struct dada_tcm_bus {
    void *context;
    int (*read)(void *, uint8_t *, unsigned);
    int (*write)(void *, const uint8_t *, unsigned);
    void (*delay_us)(void *, unsigned);
};
struct dada_tcm_message {
    uint8_t code;
    unsigned length;
    uint8_t data[DADA_TCM_PAYLOAD];
    /* Bytes after the payload EOM, including the final EOM when enabled. */
    uint8_t trailer[4];
    unsigned trailer_length;
};
struct dada_tcm_contact {
    uint16_t x, y;
    uint8_t down;
};
struct dada_tcm_state {
    struct dada_tcm_contact contacts[DADA_TCM_CONTACTS];
    uint16_t max_x, max_y, max_write, max_config;
    uint8_t objects, mode, version;
    uint32_t build_id;
    char part[17];
    uint8_t report_config[DADA_TCM_CONFIG];
    uint16_t config_length, loop_start, loop_end, config_end;
    uint8_t active_only;
};
/* emit's type values match sys/redwood_input.h: down=1, move=2, up=3. */
typedef void (*dada_touch_emit)(void *, unsigned, unsigned, unsigned, unsigned);

enum dada_tcm_error {
    DADA_TCM_IO = -1, DADA_TCM_PROTOCOL = -2, DADA_TCM_SIZE = -3,
    DADA_TCM_TIMEOUT = -4, DADA_TCM_REJECTED = -5,
    DADA_TCM_RESTARTED = -6, DADA_TCM_UNSUPPORTED = -7
};
extern const uint8_t dada_tcm_report_config[12];
int dada_tcm_read_message(const struct dada_tcm_bus *, struct dada_tcm_message *);
int dada_tcm_read_message_features(const struct dada_tcm_bus *, struct dada_tcm_message *, unsigned);
int dada_tcm_detect_features(const struct dada_tcm_message *, unsigned *);
uint16_t dada_tcm_crc16(const uint8_t *, unsigned, uint16_t);
int dada_tcm_command(const struct dada_tcm_bus *, uint8_t, const uint8_t *, unsigned,
    struct dada_tcm_message *);
int dada_tcm_command_features(const struct dada_tcm_bus *, uint8_t, const uint8_t *, unsigned,
    struct dada_tcm_message *, unsigned);
int dada_tcm_identify(struct dada_tcm_state *, const uint8_t *, unsigned);
int dada_tcm_application(struct dada_tcm_state *, const uint8_t *, unsigned);
int dada_tcm_install_config(struct dada_tcm_state *, const uint8_t *, unsigned);
int dada_tcm_frame(struct dada_tcm_state *, const uint8_t *, unsigned,
    dada_touch_emit, void *);
void dada_tcm_release(struct dada_tcm_state *, dada_touch_emit, void *);
#ifdef KERNEL
void dada_touch_init(void);
void dada_touch_stop(void);
#endif
#endif
