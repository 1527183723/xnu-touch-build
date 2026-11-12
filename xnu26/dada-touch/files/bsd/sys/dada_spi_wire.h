/* Mode-0 software SPI used by the dada TouchCom transport.
 * Tested with fake GPIO callbacks; no board addresses in the edge generator.
 */
#ifndef _SYS_DADA_SPI_WIRE_H_
#define _SYS_DADA_SPI_WIRE_H_
#include <stdint.h>
enum { DADA_SPI_MISO=40, DADA_SPI_MOSI=41, DADA_SPI_CLK=42, DADA_SPI_CS=43 };
struct dada_spi_wire {
    void *context;
    void (*output)(void *, unsigned, unsigned);
    unsigned (*input)(void *, unsigned);
    void (*delay_us)(void *, unsigned);
    uint64_t (*time_us)(void *);
    int (*stop)(void *);
};
enum dada_spi_fault {
    DADA_SPI_GOOD=0, DADA_SPI_ARGUMENT=1, DADA_SPI_CANCELLED=2,
    DADA_SPI_DEADLINE=3, DADA_SPI_SAMPLE=4
};
struct dada_spi_diagnostic {
    unsigned reason, bits;
    uint64_t elapsed_us;
};
static inline int dada_spi_wire_transfer_diagnostic(const struct dada_spi_wire *b,
    const uint8_t *tx, uint8_t *rx, unsigned length, struct dada_spi_diagnostic *diag)
{
    struct dada_spi_diagnostic local={DADA_SPI_ARGUMENT,0,0};
    if (!diag) diag=&local;
    diag->reason=DADA_SPI_ARGUMENT;diag->bits=0;diag->elapsed_us=0;
    if (!b || !b->output || !b->input || !b->delay_us || !b->time_us || !b->stop ||
        !length || length > 515 || (!tx && !rx)) return -1;
    uint64_t start = b->time_us(b->context);
    diag->reason=DADA_SPI_GOOD;
    b->output(b->context,DADA_SPI_CLK,0);
    b->output(b->context,DADA_SPI_CS,0);
    b->delay_us(b->context,2);
    for (unsigned i=0;i<length;i++) {
        unsigned in=0, out=tx ? tx[i]:0xff;
        for (int bit=7;bit>=0;bit--) {
            if (b->stop(b->context)) {diag->reason=DADA_SPI_CANCELLED;goto failed;}
            if (b->time_us(b->context)-start > 500000) {diag->reason=DADA_SPI_DEADLINE;goto failed;}
            b->output(b->context,DADA_SPI_MOSI,(out>>bit)&1);
            b->delay_us(b->context,2);
            b->output(b->context,DADA_SPI_CLK,1);
            b->delay_us(b->context,2);
            unsigned value=b->input(b->context,DADA_SPI_MISO);
            if (value>1) {diag->reason=DADA_SPI_SAMPLE;goto failed;}
            in=(in<<1)|value;
            diag->bits++;
            b->output(b->context,DADA_SPI_CLK,0);
        }
        if (rx) rx[i]=(uint8_t)in;
    }
    b->delay_us(b->context,2);
    b->output(b->context,DADA_SPI_CS,1);
    diag->elapsed_us=b->time_us(b->context)-start;
    return 0;
failed:
    b->output(b->context,DADA_SPI_CS,1);
    b->output(b->context,DADA_SPI_CLK,0);
    diag->elapsed_us=b->time_us(b->context)-start;
    return -1;
}
static inline int dada_spi_wire_transfer(const struct dada_spi_wire *b,
    const uint8_t *tx, uint8_t *rx, unsigned length)
{
    return dada_spi_wire_transfer_diagnostic(b,tx,rx,length,0);
}
#endif
