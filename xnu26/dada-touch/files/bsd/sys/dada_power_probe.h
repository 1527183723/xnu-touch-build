/* Read-only command DB parser. Layout verified against Linux cmd-db.c v6.18.
 * This file contains no hardware write operations or regulator requests.
 */
#ifndef _SYS_DADA_POWER_PROBE_H_
#define _SYS_DADA_POWER_PROBE_H_
#include <stdint.h>
#include <stddef.h>
#define DADA_CMDDB_PA 0x81c60000ULL
#define DADA_CMDDB_SIZE 0x20000u
#define DADA_CMDDB_HEADER 0x90u
#define DADA_RSC_PA 0x16520000UL
#define DADA_RSC_SIZE 0x10000u
#define DADA_RSC_TCS_OFFSET 0xd00u
#define DADA_RSC_TCS_STRIDE 0x2a0u

struct dada_power_resource {
    uint32_t address;
    uint16_t slave, version, auxiliary_length;
};
static inline uint16_t dada_db_u16(const volatile uint8_t *p) {
    return (uint16_t)p[0] | (uint16_t)p[1] << 8;
}
static inline uint32_t dada_db_u32(const volatile uint8_t *p) {
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 |
           (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}
/* 0: unique VRM resource; -1: malformed DB; -2: missing; -3: non-VRM.
 * Validate all entry/auxiliary spans, including unrelated resource groups,
 * before returning an address. No unaligned integer loads are required.
 */
static inline int dada_db_resource(const volatile uint8_t *db, size_t size,
                                  const char *name, struct dada_power_resource *out) {
    uint8_t query[8] = {0};
    struct dada_power_resource found = {0};
    unsigned length = 0, matches = 0;
    if (!db || !name || !out || size < DADA_CMDDB_HEADER) return -1;
    out->address = 0; out->slave = 0; out->version = 0; out->auxiliary_length = 0;
    while (length < sizeof(query) && name[length]) {
        query[length] = (uint8_t)name[length]; length++;
    }
    if (!length || (length == sizeof(query) && name[length])) return -1;
    if (dada_db_u32(db + 4) != 0x0c0330dbu) return -1;
    for (unsigned group = 0; group < 8; group++) {
        const volatile uint8_t *h = db + 8 + group * 16;
        uint16_t slave = dada_db_u16(h), count = dada_db_u16(h + 6);
        if (!slave) continue;
        size_t entries = DADA_CMDDB_HEADER + (size_t)dada_db_u16(h + 2);
        size_t auxiliary = DADA_CMDDB_HEADER + (size_t)dada_db_u16(h + 4);
        if (entries > size || auxiliary > size || count > (size - entries) / 24) return -1;
        for (unsigned n = 0; n < count; n++) {
            const volatile uint8_t *e = db + entries + n * 24;
            uint16_t bytes = dada_db_u16(e + 20), offset = dada_db_u16(e + 22);
            if ((size_t)offset > size - auxiliary || bytes > size - auxiliary - offset) return -1;
            unsigned same = 1;
            for (unsigned i = 0; i < sizeof(query); i++) if (e[i] != query[i]) same = 0;
            if (!same) continue;
            if (++matches > 1) return -1;
            found.address = dada_db_u32(e + 16);
            found.slave = slave; found.version = dada_db_u16(h + 8);
            found.auxiliary_length = bytes;
        }
    }
    if (!matches) return -2;
    if (found.slave != 4 || ((found.address >> 16) & 7) != 4 || (found.address & 3)) return -3;
    *out = found;
    return 0;
}
#endif
