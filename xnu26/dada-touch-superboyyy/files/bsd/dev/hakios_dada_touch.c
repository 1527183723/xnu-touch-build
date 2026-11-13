/*
 * The touch screen of the Xiaomi 15 Pro (haotian, SM8750): a Synaptics TouchComm ("TCM")
 * controller on the SPI bus of serial engine 0 of the second QUP wrapper, as a source of touches for
 * /dev/redwood-touch (sys/redwood_input.h). Only with boot-arg dadatouch=1: every address below
 * belongs to this phone (its own device tree, Android's).
 *
 * As redwood_goodix.c does for the POCO X5 Pro, the driver does everything itself:
 *   power    iovdd 1.8 V from LDO12 of PMIC B ("ldob12") through RPMh (the application
 *            processor's DRV 2, RSC version 3 register layout); avdd is a fixed regulator
 *            switched by gpio114 on haotian (dada takes 3.0 V from LDO16 "ldob16")
 *   clocks   the wrapper's AHB and core clocks and the engine's clock, by vote in GCC; the
 *            engine's rate comes from the wrapper's "QSPI reference" clock generator
 *   pins     gpio32..35 to the engine (MISO, MOSI, CLK, CS), gpio161 reset, gpio162 interrupt
 *            (low while the controller has a message)
 *   SPI      the engine ("GENI") in FIFO mode, polled
 *   chip     TouchComm: every read is a message -- 0xa5, a status or report code, a 16-bit length,
 *            the payload, 0x5a -- read as the 4-byte header and then the rest (which the chip sends
 *            again behind 0xa5 0x03, "continued read"); a command is the code, a 16-bit length and
 *            the payload. After reset the chip announces itself with an identify report; we ask
 *            for the application information (the coordinate range, the number of objects) and the
 *            touch report configuration, a list of fields and their widths in bits, by which every
 *            touch report (0x11) is then taken apart.
 * No interrupt is used: a thread looks at the interrupt line every 4 ms (touch_poll_us=N).
 * Lines go to the loader's log (redwood_tl): what was found, the first reports, a count a minute.
 *
 * Layouts: Linux drivers/spi/spi-geni-qcom.c, drivers/soc/qcom/{qcom-geni-se,rpmh-rsc,cmd-db}.c,
 * drivers/clk/qcom/gcc-sm8750.c, drivers/pinctrl/qcom/pinctrl-sm8750.c, and Synaptics' TCM driver
 * (synaptics_tcm_{core,spi,touch}.c of the vendor kernels).
 *
 * Imported from superboyyy/hakios-arm64e-dada at
 * f1ae906fcc43ba8f763663257724ba938fb820ab (APSL-2.0).
 * Primary revision: user-provided repair package 20261110, checked payload
 * padding and stale pending-state reset. Warsaw additions: cooperative stop,
 * GPIO restoration, bounded report parsing; synthetic autoswipe omitted.
 */
#include <kern/clock.h>
#include <kern/thread.h>
#include <libkern/libkern.h>
#include <mach/mach_types.h>
#include <pexpert/pexpert.h>
#include <sys/redwood_input.h>
#include <sys/systm.h>

extern void redwood_tl(const char *format, ...) __printflike(1, 2);

extern vm_offset_t ml_io_map(vm_offset_t phys_addr, vm_size_t size);
extern vm_offset_t ml_io_map_wcomb(vm_offset_t phys_addr, vm_size_t size);
extern kern_return_t thread_terminate(thread_t thread);

#define say(...) redwood_tl("dtouch: " __VA_ARGS__)

/* This copy is the Xiaomi 15 Pro (haotian) port.  The original dada constants stay below
 * behind HAOTIAN_PORT so it is still visible what was changed and why. */
#define HAOTIAN_PORT 1

static vm_offset_t tlmm, gcc, se, rsc, cmd_db;
static uint32_t stopping, running, started;

static bool
stop_requested(void)
{
	return __atomic_load_n(&stopping, __ATOMIC_ACQUIRE) != 0;
}

static inline uint32_t
rd(vm_offset_t address)
{
	return *(volatile uint32_t *)address;
}

static inline void
wr(vm_offset_t address, uint32_t value)
{
	*(volatile uint32_t *)address = value;
}

static void
sleep_us(uint32_t us)
{
	while (us && !stop_requested()) {
		uint32_t step = us > 1000 ? 1000 : us;
		delay_for_interval(step, NSEC_PER_USEC);
		us -= step;
	}
}

static void
sleep_ms(uint32_t ms)
{
	while (ms-- && !stop_requested()) {
		sleep_us(1000);
	}
}

/* ---- pins (TLMM) ------------------------------------------------------------------------------- */

#define TLMM_PHYS               0x0f100000UL    /* GPIO 0 (Android's "sun-tlmm" node starts 1 MB lower) */
#define TLMM_SIZE               0x102000UL
#define GPIO_CFG(n)             (tlmm + 0x1000UL * (n))         /* [1:0] pull, [5:2] function, [8:6] drive, [9] output */
#define GPIO_IO(n)              (tlmm + 0x1000UL * (n) + 4)     /* bit 0 in, bit 1 out */
#define PULL_NONE               0
#define PULL_DOWN               1
#define PULL_UP                 3

#if HAOTIAN_PORT
/* haotian: spi@a80000 is QUP1_SE0 and its four pins are gpio32..35 (qup1_se0_l0..l3).
 * The earlier port used gpio36..39 (qup1_se1), which are listed in qcom,gpios-reserved
 * and are not readable from this VM: the first TLMM read stopped the whole phone. */
#define PIN_MISO                32      /* qup1_se0_l0 */
#define PIN_MOSI                33      /* qup1_se0_l1 */
#define PIN_CLK                 34      /* qup1_se0_l2 */
#define PIN_CS                  35      /* qup1_se0_l3 */
#define PIN_TOUCH_AVDD          114     /* touch_avdd_vreg: gpio114, active high */
#else
#define PIN_MISO                40
#define PIN_MOSI                41
#define PIN_CLK                 42
#define PIN_CS                  43
#endif
#define PIN_RESET               161     /* low = in reset */
#define PIN_INT                 162     /* low = the chip has a message */
#define PIN_FUNCTION_SPI        1       /* PINGROUP(32..35, qup1_se0, ...) -> mux 1 (pinctrl-sm8750.c) */

static void
gpio_configure(unsigned pin, unsigned function, unsigned pull, unsigned ma, bool output)
{
	wr(GPIO_CFG(pin), pull | function << 2 | (ma / 2 - 1) << 6 | (output ? 1u << 9 : 0));
}

static void
gpio_set(unsigned pin, bool high)
{
	wr(GPIO_IO(pin), high ? 2 : 0);
}

static bool
gpio_get(unsigned pin)
{
	return rd(GPIO_IO(pin)) & 1;
}

static void
gpio_output(unsigned pin, bool high, unsigned pull, unsigned ma)
{
	gpio_set(pin, high);
	gpio_configure(pin, 0, pull, ma, true);
}

/* ---- the supplies (RPMh) ------------------------------------------------------------------------- */

#define CMD_DB_PHYS             0x81c60000UL
#define CMD_DB_SIZE             0x20000UL
#define RSC_PHYS                0x16520000UL    /* "drv-2", the application processor's */
#define RSC_SIZE                0x10000UL
#define TCS_BASE                0xd00           /* command slot set 0: the first "active" one (tcs-config) */
#define MSGID_WRITE_4_BYTES     0x10008
#define MSGID_RESPONSE          0x100
#define VRM_VOLTAGE             0
#define VRM_ENABLE              4
#define VRM_MODE                8
#define VRM_MODE_LDO_HPM        7

/* register offsets inside a slot set: RSC version 2 and version 3 (SM8550 on) differ */
static struct {
	uint32_t wait, control, status, cmd_enable, msgid, addr, data, cmd_size;
} tcs_reg = { 0x10, 0x14, 0x18, 0x1c, 0x30, 0x34, 0x38, 20 };
#define TCS_IRQ_ENABLE          0x00
#define TCS_IRQ_STATUS          0x04
#define TCS_IRQ_CLEAR           0x08
#define TCS_CONTROL_ENABLE      (1u << 16)
#define TCS_CONTROL_TRIGGER     (1u << 24)
#define TCS(reg)                (rsc + TCS_BASE + (reg))

struct cmd_db_slave {
	uint16_t id, header_offset, data_offset, count, version, reserved[3];
};
struct cmd_db_header {
	uint32_t version;
	uint8_t magic[4];
	struct cmd_db_slave slave[8];
	uint32_t checksum, reserved;
	uint8_t data[];
};
struct cmd_db_entry {
	char id[8];
	uint32_t priority[2];
	uint32_t address;
	uint16_t length, offset;
};

static uint32_t
rpmh_address(const char *name)
{
	const struct cmd_db_header *db = (const struct cmd_db_header *)cmd_db;
	char id[8] = { 0 };

	if (db->magic[0] != 0xdb || db->magic[1] != 0x30 || db->magic[2] != 0x03 || db->magic[3] != 0x0c) {
		say("no command database (magic %02x %02x %02x %02x)\n", db->magic[0], db->magic[1], db->magic[2],
		    db->magic[3]);
		return 0;
	}
	strlcpy(id, name, sizeof(id));
	for (unsigned s = 0; s < 8; s++) {
		const struct cmd_db_slave *slave = &db->slave[s];
		const struct cmd_db_entry *entry = (const struct cmd_db_entry *)(db->data + slave->header_offset);

		if (slave->id == 0 || slave->count > 512 || sizeof(*db) + (size_t)slave->header_offset +
		    (size_t)slave->count * sizeof(*entry) > CMD_DB_SIZE) {
			continue;
		}
		for (unsigned i = 0; i < slave->count; i++) {
			if (memcmp(entry[i].id, id, sizeof(id)) == 0) {
				return entry[i].address;
			}
		}
	}
	return 0;
}

static bool
rpmh_wait(uint32_t reg, uint32_t mask, uint32_t value, const char *what)
{
	for (int i = 0; i < 2000; i++) {
		if ((rd(TCS(reg)) & mask) == value) {
			return true;
		}
		sleep_us(100);
	}
	say("RPMh: timeout waiting for %s (register %#x = %#x)\n", what, reg, rd(TCS(reg)));
	return false;
}

static bool
rpmh_write(const uint32_t *address, const uint32_t *data, unsigned count)
{
	uint32_t irq_enable = rd(TCS(TCS_IRQ_ENABLE));
	bool done;

	for (int i = 0; rd(TCS(tcs_reg.status)) == 0; i++) {
		if (i > 2000) {
			say("RPMh: the command slots stay busy\n");
			return false;
		}
		sleep_us(100);
	}
	wr(TCS(tcs_reg.control), rd(TCS(tcs_reg.control)) & ~TCS_CONTROL_TRIGGER);
	wr(TCS(tcs_reg.control), rd(TCS(tcs_reg.control)) & ~TCS_CONTROL_ENABLE);
	wr(TCS(TCS_IRQ_CLEAR), 1);
	for (unsigned i = 0; i < count; i++) {
		wr(TCS(tcs_reg.msgid + tcs_reg.cmd_size * i), MSGID_WRITE_4_BYTES | MSGID_RESPONSE);
		wr(TCS(tcs_reg.addr + tcs_reg.cmd_size * i), address[i]);
		wr(TCS(tcs_reg.data + tcs_reg.cmd_size * i), data[i]);
	}
	wr(TCS(tcs_reg.wait), (1u << count) - 1);
	wr(TCS(tcs_reg.cmd_enable), (1u << count) - 1);
	wr(TCS(TCS_IRQ_ENABLE), irq_enable | 1);
	wr(TCS(tcs_reg.control), TCS_CONTROL_ENABLE);
	wr(TCS(tcs_reg.control), TCS_CONTROL_ENABLE | TCS_CONTROL_TRIGGER);

	done = rpmh_wait(TCS_IRQ_STATUS, 1, 1, "the request");
	wr(TCS(tcs_reg.control), rd(TCS(tcs_reg.control)) & ~TCS_CONTROL_TRIGGER);
	wr(TCS(tcs_reg.control), rd(TCS(tcs_reg.control)) & ~TCS_CONTROL_ENABLE);
	wr(TCS(tcs_reg.cmd_enable), 0);
	wr(TCS(tcs_reg.wait), 0);
	wr(TCS(TCS_IRQ_CLEAR), 1);
	wr(TCS(TCS_IRQ_ENABLE), irq_enable);
	return done;
}

static bool
ldo_on(const char *name, uint32_t millivolts)
{
	uint32_t base = rpmh_address(name);
	uint32_t address[3] = { base + VRM_VOLTAGE, base + VRM_ENABLE, base + VRM_MODE };
	uint32_t data[3] = { millivolts, 1, VRM_MODE_LDO_HPM };

	if (base == 0 || (base >> 16 & 0xf) != 4) {
		say("RPMh: no regulator \"%s\" (address %#x)\n", name, base);
		return false;
	}
	if (!rpmh_write(address, data, 3)) {
		return false;
	}
	say("%s on at %u mV (RPMh address %#x)\n", name, millivolts, base);
	return true;
}

static void
supplies_on(void)
{
	uint32_t id = rd(rsc);
	uint32_t major = id >> 16 & 0xff;

	if (major >= 3) {
		tcs_reg.wait = 0x20;
		tcs_reg.control = 0x24;
		tcs_reg.status = 0x28;
		tcs_reg.cmd_enable = 0x2c;
		tcs_reg.msgid = 0x34;
		tcs_reg.addr = 0x38;
		tcs_reg.data = 0x3c;
		tcs_reg.cmd_size = 24;
	}
	say("RPMh: RSC id %#x (version %u); slot status %#x control %#x\n", id, major, rd(TCS(tcs_reg.status)),
	    rd(TCS(tcs_reg.control)));
	ldo_on("ldob12", 1800);         /* iovdd */
#if HAOTIAN_PORT
	/* haotian's touch_avdd_vreg is a fixed regulator enabled by gpio114, not LDO16; on
	 * haotian LDO16 feeds the camera VAF rail (2.8 V), so leave it alone. */
	gpio_output(PIN_TOUCH_AVDD, true, PULL_NONE, 2);
	sleep_ms(5);
#else
	ldo_on("ldob16", 3000);         /* avdd */
#endif
}

/* ---- clocks (GCC) -------------------------------------------------------------------------------- */

#define GCC_PHYS                0x00100000UL
#define GCC_SIZE                0x60000UL
#define GCC_VOTE                (gcc + 0x52008)
#define VOTE_QUP1_CORE_2X       (1u << 18)
#define VOTE_QUP1_CORE          (1u << 19)
#define VOTE_QUP1_M_AHB         (1u << 20)
#define VOTE_QUP1_S_AHB         (1u << 21)
#define VOTE_QUP1_S2            (1u << 24)
#define GCC_QUP1_M_AHB_CBCR     (gcc + 0x23140)
#define GCC_QUP1_S_AHB_CBCR     (gcc + 0x23144)
#define GCC_QUP1_S2_CBCR        (gcc + 0x1827c)
#define GCC_QUP1_S2_DIV         (gcc + 0x1828c)         /* [3:0] divider - 1, behind the generator */
#define GCC_QSPI_REF_CMD        (gcc + 0x188c0)
#define GCC_QSPI_REF_CFG        (gcc + 0x188c4)
#define GCC_QSPI_REF_DFSR       (gcc + 0x188d4)
#define GCC_QSPI_REF_PERF_CFG(l) (gcc + 0x188dc + 4 * (l))
#define GCC_QSPI_REF_PERF_M(l)  (gcc + 0x1891c + 4 * (l))
#define GCC_QSPI_REF_PERF_N(l)  (gcc + 0x1895c + 4 * (l))
#define CLOCK_OFF               (1u << 31)
#define XO_HZ                   19200000

static uint32_t se_source_hz, se_clock_select;

static uint32_t
rcg_rate(uint32_t cfg, uint32_t m, uint32_t not_n_minus_m)
{
	/* parent map 5: 0 crystal, 1 GPLL0 (600 MHz), 2 GPLL7 (not known here), 6 GPLL0 even (300 MHz) */
	static const uint32_t parent[8] = { XO_HZ, 600000000, 0, 0, 0, 0, 300000000, 0 };
	uint64_t hz = parent[cfg >> 8 & 7];
	uint32_t divider = cfg & 0x1f, mode = cfg >> 12 & 3;

	if (divider) {
		hz = hz * 2 / (divider + 1);
	}
	if (mode) {
		uint32_t n = (~not_n_minus_m & 0xffff) + (m & 0xffff);

		if (n) {
			hz = hz * (m & 0xffff) / n;
		}
	}
	return (uint32_t)hz;
}

static bool
clocks_on(void)
{
	/*
	 * haotian（小米 15 Pro）：触摸从 QUP1_SE2 搬到了 SE0（DT 时钟 ID 0x5c -> 0x58），
	 * 这里的 S2 时钟寄存器不再是它的。实测：让它通过 -> 去读 GENI/SE 寄存器 ->
	 * 内核十几秒后整机停死（USB 全断，只能长按电源 15 秒断电）。
	 * 所以直接返回 false，逼调用方走下面的 GPIO 模拟 SPI 路径（dada 本来也走这条）。
	 * 等把 S0 的 GCC 偏移核准了，删掉这一行就能走硬件 SPI。
	 */
	return false;
	uint32_t post = (rd(GCC_QUP1_S2_DIV) & 0xf) + 1;

	wr(GCC_VOTE, rd(GCC_VOTE) | VOTE_QUP1_CORE_2X | VOTE_QUP1_CORE | VOTE_QUP1_M_AHB | VOTE_QUP1_S_AHB);
	if (rd(GCC_QSPI_REF_DFSR) & 1) {
		for (uint32_t level = 0; level < 8; level++) {
			uint32_t hz = rcg_rate(rd(GCC_QSPI_REF_PERF_CFG(level)), rd(GCC_QSPI_REF_PERF_M(level)),
			    rd(GCC_QSPI_REF_PERF_N(level))) / post;

			if (hz >= 4000000 && (se_source_hz == 0 || hz < se_source_hz)) {
				se_source_hz = hz;
				se_clock_select = level;
			}
		}
		if (se_source_hz == 0) {
			say("no usable preset clock rate\n");
			return false;
		}
	} else {
		wr(GCC_QSPI_REF_CFG, (rd(GCC_QSPI_REF_CFG) & ~0x371fu) | 1);
		wr(GCC_QSPI_REF_CMD, rd(GCC_QSPI_REF_CMD) | 1);
		for (int i = 0; i < 500 && (rd(GCC_QSPI_REF_CMD) & 1); i++) {
			sleep_us(10);
		}
		if (rd(GCC_QSPI_REF_CMD) & 1) {
			say("the engine's clock does not take its configuration (%#x)\n", rd(GCC_QSPI_REF_CMD));
			return false;
		}
		se_source_hz = XO_HZ / post;
		se_clock_select = 0;
	}
	wr(GCC_VOTE, rd(GCC_VOTE) | VOTE_QUP1_S2);
	for (int i = 0; i < 1000; i++) {
		if (!((rd(GCC_QUP1_M_AHB_CBCR) | rd(GCC_QUP1_S_AHB_CBCR) | rd(GCC_QUP1_S2_CBCR)) & CLOCK_OFF)) {
			return true;
		}
		sleep_us(10);
	}
	say("clocks stay off: %#x %#x %#x\n", rd(GCC_QUP1_M_AHB_CBCR), rd(GCC_QUP1_S_AHB_CBCR), rd(GCC_QUP1_S2_CBCR));
	return false;
}

/* ---- SPI through the serial engine (GENI) ---------------------------------------------------------- */

#define SE_PHYS                 0x00a80000UL    /* haotian: spi@a80000（dada 是 0xa88000） */
#define SE_SIZE                 0x4000UL
#define GENI_FORCE_DEFAULT      (se + 0x020)
#define GENI_OUTPUT_CTRL        (se + 0x024)
#define GENI_CGC_CTRL           (se + 0x028)
#define GENI_STATUS             (se + 0x040)
#define GENI_SER_M_CLK_CFG      (se + 0x048)
#define GENI_IF_DISABLE_RO      (se + 0x064)
#define GENI_FW_REVISION_RO     (se + 0x068)
#define GENI_CLK_SEL            (se + 0x07c)
#define SPI_CPHA                (se + 0x224)
#define SPI_LOOPBACK            (se + 0x22c)
#define SPI_CPOL                (se + 0x230)
#define SPI_DEMUX_OUTPUT_INV    (se + 0x24c)
#define SPI_DEMUX_SEL           (se + 0x250)
#define GENI_BYTE_GRAN          (se + 0x254)
#define GENI_DMA_MODE_EN        (se + 0x258)
#define SPI_TRANS_CFG           (se + 0x25c)
#define GENI_TX_PACKING_CFG0    (se + 0x260)
#define GENI_TX_PACKING_CFG1    (se + 0x264)
#define SPI_WORD_LEN            (se + 0x268)
#define SPI_TX_TRANS_LEN        (se + 0x26c)
#define SPI_RX_TRANS_LEN        (se + 0x270)
#define SPI_DELAY_COUNTERS      (se + 0x278)
#define GENI_RX_PACKING_CFG0    (se + 0x284)
#define GENI_RX_PACKING_CFG1    (se + 0x288)
#define GENI_M_CMD0             (se + 0x600)
#define GENI_M_CMD_CTRL         (se + 0x604)
#define GENI_M_IRQ_STATUS       (se + 0x610)
#define GENI_M_IRQ_EN           (se + 0x614)
#define GENI_M_IRQ_CLEAR        (se + 0x618)
#define GENI_S_IRQ_CLEAR        (se + 0x648)
#define GENI_TX_FIFO            (se + 0x700)
#define GENI_RX_FIFO            (se + 0x780)
#define GENI_TX_FIFO_STATUS     (se + 0x800)
#define GENI_RX_FIFO_STATUS     (se + 0x804)
#define GENI_TX_WATERMARK       (se + 0x80c)
#define GENI_RX_WATERMARK       (se + 0x810)
#define GENI_RX_RFR_WATERMARK   (se + 0x814)
#define SE_DMA_TX_IRQ_CLR       (se + 0xc44)
#define SE_DMA_RX_IRQ_CLR       (se + 0xd44)
#define SE_GSI_EVENT_EN         (se + 0xe18)
#define SE_IRQ_EN               (se + 0xe1c)
#define SE_HW_PARAM_0           (se + 0xe24)
#define SE_DMA_GENERAL_CFG      (se + 0xe30)

#define M_CMD_DONE              (1u << 0)
#define M_CMD_CANCEL            (1u << 4)
#define M_CMD_ABORT             (1u << 5)
#define M_ERRORS                ((1u << 1) | (1u << 2) | (1u << 3) | (1u << 24) | (1u << 25) | (1u << 28) | (1u << 29))
#define SPI_FULL_DUPLEX         (3u << 27)

static uint32_t fifo_depth;
static bool geni_configured;
static uint32_t saved_m_irq_en, saved_se_irq_en, saved_gsi_event_en;

static const char *
geni_spi_init(uint32_t hz)
{
	uint32_t divider;

	if ((rd(GENI_FW_REVISION_RO) >> 8 & 0xff) != 1) {
		return "the serial engine has no SPI firmware";
	}
	if (rd(GENI_IF_DISABLE_RO) & 1) {
		return "the serial engine's FIFO interface is disabled";
	}
	fifo_depth = rd(SE_HW_PARAM_0) >> 16 & 0x3f;
	if (fifo_depth < 4) {
		return "the serial engine reports no FIFO";
	}
	saved_m_irq_en = rd(GENI_M_IRQ_EN);
	saved_se_irq_en = rd(SE_IRQ_EN);
	saved_gsi_event_en = rd(SE_GSI_EVENT_EN);
	geni_configured = true;
	wr(SE_GSI_EVENT_EN, 0);
	wr(GENI_M_IRQ_CLEAR, 0xffffffff);
	wr(GENI_S_IRQ_CLEAR, 0xffffffff);
	wr(SE_DMA_TX_IRQ_CLR, 0xffffffff);
	wr(SE_DMA_RX_IRQ_CLR, 0xffffffff);
	wr(GENI_CGC_CTRL, rd(GENI_CGC_CTRL) | 0x7f);
	wr(SE_DMA_GENERAL_CFG, rd(SE_DMA_GENERAL_CFG) | 0xf);
	wr(GENI_OUTPUT_CTRL, 0x7f);
	wr(GENI_FORCE_DEFAULT, 1);
	wr(GENI_DMA_MODE_EN, rd(GENI_DMA_MODE_EN) & ~1u);
	wr(GENI_RX_WATERMARK, fifo_depth - 3);
	wr(GENI_RX_RFR_WATERMARK, fifo_depth - 2);
	wr(GENI_TX_WATERMARK, 0);
	wr(SE_IRQ_EN, rd(SE_IRQ_EN) | 0xf);
	wr(GENI_M_IRQ_EN, rd(GENI_M_IRQ_EN) | 0x7fc0007f);

	/* mode 0 (synaptics,spi-mode 0), chip select 0, 8 bits, most significant first */
	wr(SPI_LOOPBACK, 0);
	wr(SPI_DEMUX_SEL, 0);
	wr(SPI_CPHA, 0);
	wr(SPI_CPOL, 0);
	wr(SPI_DEMUX_OUTPUT_INV, 0);
	wr(SPI_TRANS_CFG, rd(SPI_TRANS_CFG) & ~2u);
	wr(SPI_DELAY_COUNTERS, 0);
	wr(GENI_TX_PACKING_CFG0, 0x0007f8fe);
	wr(GENI_TX_PACKING_CFG1, 0x000ffefe);
	wr(GENI_RX_PACKING_CFG0, 0x0007f8fe);
	wr(GENI_RX_PACKING_CFG1, 0x000ffefe);
	wr(GENI_BYTE_GRAN, 0);
	wr(SPI_WORD_LEN, 8 - 4);

	divider = (se_source_hz + hz - 1) / hz;
	wr(GENI_CLK_SEL, se_clock_select & 7);
	wr(GENI_SER_M_CLK_CFG, (divider ? divider : 1) << 4 | 1);
	return NULL;
}

static void
geni_drain(uint8_t *rx, uint32_t length, uint32_t *received)
{
	uint32_t status = rd(GENI_RX_FIFO_STATUS), words = status & 0x01ffffff, bytes = words * 4;

	if (status & 0x80000000u) {
		uint32_t valid = status >> 28 & 7;

		if (valid && valid < 4) {
			bytes -= 4 - valid;
		}
	}
	for (uint32_t i = 0; i < words; i++) {
		uint32_t word = rd(GENI_RX_FIFO);

		for (int j = 0; j < 4 && bytes; j++, bytes--) {
			if (*received < length) {
				if (rx) {
					rx[*received] = (uint8_t)(word >> (8 * j));
				}
				(*received)++;
			}
		}
	}
}

static void
geni_recover(void)
{
	wr(GENI_M_CMD_CTRL, 1u << 2);
	for (int i = 0; i < 2000 && !(rd(GENI_M_IRQ_STATUS) & M_CMD_CANCEL); i++) {
		delay_for_interval(100, NSEC_PER_USEC);
	}
	if (!(rd(GENI_M_IRQ_STATUS) & M_CMD_CANCEL)) {
		wr(GENI_M_CMD_CTRL, 1u << 1);
		for (int i = 0; i < 2000 && !(rd(GENI_M_IRQ_STATUS) & M_CMD_ABORT); i++) {
			delay_for_interval(100, NSEC_PER_USEC);
		}
	}
	for (int i = 0; i < 64 && (rd(GENI_RX_FIFO_STATUS) & 0x01ffffff); i++) {
		(void)rd(GENI_RX_FIFO);
	}
	wr(GENI_M_IRQ_CLEAR, 0xffffffff);
}

/* One transfer with the chip selected throughout: `length` bytes out (0xff without tx) and as many in. */
static bool
geni_transfer(const uint8_t *tx, uint8_t *rx, uint32_t length)
{
	static unsigned complaints;
	uint32_t sent = 0, received = 0, status = 0;
	uint64_t deadline;
	if (stop_requested()) {
		return false;
	}

	for (int i = 0; rd(GENI_STATUS) & 1; i++) {
		if (i > 2000) {
			geni_recover();
			break;
		}
		sleep_us(100);
	}
	wr(GENI_M_IRQ_CLEAR, 0xffffffff);
	wr(SPI_TX_TRANS_LEN, length);
	wr(SPI_RX_TRANS_LEN, length);
	wr(GENI_M_CMD0, SPI_FULL_DUPLEX);
	clock_interval_to_deadline(200, NSEC_PER_MSEC, &deadline);
	while (mach_absolute_time() < deadline) {
		while (sent < length && (rd(GENI_TX_FIFO_STATUS) & 0x0fffffff) + 1 < fifo_depth) {
			uint32_t word = 0;

			for (int j = 0; j < 4 && sent < length; j++, sent++) {
				word |= (uint32_t)(tx ? tx[sent] : 0xff) << (8 * j);
			}
			wr(GENI_TX_FIFO, word);
		}
		status = rd(GENI_M_IRQ_STATUS);
		geni_drain(rx, length, &received);
		if (status & M_ERRORS) {
			break;
		}
		if (status & M_CMD_DONE) {
			geni_drain(rx, length, &received);
			wr(GENI_M_IRQ_CLEAR, status);
			if (received == length) {
				return true;
			}
			break;
		}
	}
	if (complaints++ < 8) {
		say("SPI transfer of %u bytes failed: status %#x, sent %u, received %u\n", length, status, sent, received);
	}
	geni_recover();
	return false;
}

/*
 * ---- SPI by hand, on the same pins ----
 * dada's boot firmware hands serial engine 2 over in GSI (DMA) mode only: its FIFO interface is
 * disabled ("qcom,la-vm", a GPI channel in Android's device tree). Until the GPI DMA engine is
 * driven, the four pins are driven as GPIOs: mode 0, clock idle low, data read on the rising edge
 * and changed on the falling one, most significant bit first. Each pin write is a posted store to
 * the TLMM; reading the input pin orders them, which also paces the clock at a few MHz at most.
 */
static void
bitbang_init(void)
{
	gpio_output(PIN_CS, true, PULL_NONE, 6);
	gpio_output(PIN_CLK, false, PULL_NONE, 6);
	gpio_output(PIN_MOSI, true, PULL_NONE, 6);
	gpio_configure(PIN_MISO, 0, PULL_DOWN, 2, false);
}

static uint32_t bitbang_half_ns = 150;
static uint64_t bitbang_half_ticks;

static inline void
bitbang_wait(void)
{
	uint64_t until = mach_absolute_time() + bitbang_half_ticks;

	while (mach_absolute_time() < until) {
	}
}

static void
bitbang_select(bool on)
{
	if (on) {
		gpio_set(PIN_CS, false);
		(void)gpio_get(PIN_CS);
		bitbang_wait();
	} else {
		(void)gpio_get(PIN_MISO);
		bitbang_wait();
		gpio_set(PIN_CS, true);
		(void)gpio_get(PIN_CS);
	}
}

/* bytes with the chip already selected (bitbang_select) */
static void
bitbang_bytes(const uint8_t *tx, uint8_t *rx, uint32_t length)
{
	for (uint32_t i = 0; i < length; i++) {
		uint8_t out = tx ? tx[i] : 0xff, in = 0;

		for (int bit = 7; bit >= 0; bit--) {
			gpio_set(PIN_MOSI, out >> bit & 1);
			(void)gpio_get(PIN_MISO);
			bitbang_wait();
			gpio_set(PIN_CLK, true);
			(void)gpio_get(PIN_CLK);
			bitbang_wait();
			in = (uint8_t)(in << 1 | gpio_get(PIN_MISO));
			gpio_set(PIN_CLK, false);
		}
		if (rx) {
			rx[i] = in;
		}
	}
}

static bool
bitbang_transfer(const uint8_t *tx, uint8_t *rx, uint32_t length)
{
	if (stop_requested()) {
		return false;
	}
	bitbang_select(true);
	bitbang_bytes(tx, rx, length);
	bitbang_select(false);
	return true;
}

static bool (*spi_transfer)(const uint8_t *tx, uint8_t *rx, uint32_t length) = geni_transfer;

/* ---- TouchComm -------------------------------------------------------------------------------------- */

#define TCM_MARKER              0xa5
#define TCM_PADDING             0x5a
#define CMD_IDENTIFY            0x02
#define CMD_RESET               0x04
#define CMD_ENABLE_REPORT       0x05
#define CMD_DISABLE_REPORT      0x06
#define CMD_GET_DYNAMIC_CONFIG  0x23
#define CMD_SET_DYNAMIC_CONFIG  0x24
#define CMD_EXIT_DEEP_SLEEP     0x2d
#define DC_ENABLE_WAKEUP_GESTURE_MODE 0x09
#define DC_TOUCH_SCAN_MODE      0xf2
#define REPORT_XIAOMI_FRAME     0xc0    /* 1708 bytes, unasked, at every frame: Xiaomi's ("frame data") */
#define CMD_RUN_APPLICATION     0x14
#define CMD_GET_APPLICATION_INFO 0x20
#define CMD_GET_TOUCH_REPORT_CONFIG 0x25
#define STATUS_IDLE             0x00
#define STATUS_OK               0x01
#define STATUS_BUSY             0x02
#define STATUS_CONTINUED_READ   0x03
#define STATUS_INVALID          0xff
#define REPORT_IDENTIFY         0x10
#define REPORT_TOUCH            0x11
#define MODE_APPLICATION        0x01
#define TCM_MAX_PAYLOAD         4096
#define TCM_ONE_READ            1024    /* the most one selection carries (identify: max write size 0x400) */
#define MAX_OBJECTS             10

static uint8_t message[TCM_MAX_PAYLOAD + 8];
static uint8_t touch_config[256];
#if HAOTIAN_PORT
static uint32_t touch_config_size, max_x = 1440, max_y = 3200, max_objects = MAX_OBJECTS;
#else
static uint32_t touch_config_size, max_x = 1200, max_y = 2670, max_objects = MAX_OBJECTS;
#endif
static struct {
	unsigned polls, messages, touch_reports, bad, other, touches, resyncs;
	uint32_t last_x, last_y;
} seen;

static uint16_t
le16(const uint8_t *p)
{
	return (uint16_t)(p[0] | p[1] << 8);
}

static void
dump(const char *what, const uint8_t *p, uint32_t length)
{
	char line[3 * 40 + 1];
	uint32_t n = length < 40 ? length : 40;

	for (uint32_t i = 0; i < n; i++) {
		snprintf(line + 3 * i, sizeof(line) - 3 * i, "%02x ", p[i]);
	}
	line[3 * n] = 0;
	say("%s (%u bytes): %s\n", what, length, line);
}

/*
 * Read one message: its code into *code, its payload into message[], its length into *length.
 * The chip sends a message from its start in every transfer (0xa5, code, 16-bit length, payload,
 * 0x5a) until it has all been read; what a transfer leaves, the next one gets behind 0xa5 0x03
 * ("continued read", no length). So the first transfer is a generous one (READ_FIRST bytes,
 * Synaptics' "predictive reading"), and only a longer message takes a second for the rest.
 * false: no message (no marker: the chip is not there, or it is not talking yet).
 */
#define READ_FIRST              64
static unsigned raw_dumps;
static uint32_t pending_payload;        /* a big message whose header was read and whose rest was not yet */
static uint8_t pending_code;

static bool
tcm_payload_complete(uint32_t payload)
{
	if (payload > TCM_MAX_PAYLOAD || message[payload] != TCM_PADDING) {
		seen.bad++;
		return false;
	}
	return true;
}

static bool
tcm_read(uint8_t *code, uint32_t *length)
{
	static uint8_t first[READ_FIRST];
	uint32_t payload, total;
	*length = 0;
	if (stop_requested()) {
		return false;
	}

	if (spi_transfer == bitbang_transfer) {
		/*
		 * By hand the chip stays selected from the header to the padding byte: the whole message in
		 * one go, no "continued read" (which this chip answers with nothing but 0xff by hand).
		 */
		bitbang_select(true);
		bitbang_bytes(NULL, first, 4);
		if (first[0] != TCM_MARKER) {
			bitbang_select(false);
			seen.bad++;
			if (seen.bad <= 4 && first[0] != 0xff) {
				dump("no marker", first, 4);
			}
			return false;
		}
		*code = first[1];
		payload = le16(first + 2);
		*length = 0;
		if (*code == STATUS_CONTINUED_READ && pending_payload >= 2 && pending_payload <= TCM_MAX_PAYLOAD) {
			/*
			 * The rest of the big message whose header the last look had: 0xa5 0x03 and then its
			 * payload from the start (the two bytes read as a length are its first two).
			 */
			message[0] = first[2];
			message[1] = first[3];
			bitbang_bytes(NULL, message + 2, pending_payload - 2 + 1);
			bitbang_select(false);
			*code = pending_code;
			*length = pending_payload;
			pending_payload = 0;
			seen.resyncs++;
			if (!tcm_payload_complete(*length)) {
				*length = 0;
				return false;
			}
			return true;
		}
		if (*code == STATUS_CONTINUED_READ || payload > TCM_MAX_PAYLOAD) {
			/*
			 * Out of step: the chip is still sending the rest of an earlier message (behind 0xa5 0x03,
			 * no length of its own). Take all of it now, in this same selection, and drop it: the
			 * longest message there is (Xiaomi's frame) fits in the buffer, and what follows its end
			 * is 0xff.
			 */
			bitbang_bytes(NULL, message, 2048);
			bitbang_select(false);
			if (seen.resyncs++ < 6) {
				say("out of step (%02x %02x %02x %02x): the rest of a message dropped\n", first[0], first[1],
				    first[2], first[3]);
			}
			return false;
		}
		/* A fresh header supersedes any older, incomplete message. */
		pending_payload = 0;
		if (payload + 1 > TCM_ONE_READ) {
			pending_code = *code;
			/*
			 * More than the chip sends in one selection (Xiaomi's 1708-byte frame): the header was
			 * all of this one; the payload comes in the next, behind 0xa5 0x03 -- once the chip has
			 * it ready, so a selection that finds 0xff is let go and tried again.
			 */
			bitbang_select(false);
			for (int tries = 0;; tries++) {
				uint8_t lead[2];

				if (tries == 200 || stop_requested()) {
					/* (what the chip still has of it, the next look takes as the rest: pending_payload) */
					pending_payload = payload;
					seen.bad++;
					return false;
				}
				delay_for_interval(100, NSEC_PER_USEC);
				bitbang_select(true);
				bitbang_bytes(NULL, lead, 2);
				if (lead[0] == TCM_MARKER && lead[1] == STATUS_CONTINUED_READ) {
					break;
				}
				bitbang_select(false);
			}
		}
		bitbang_bytes(NULL, message, payload + 1);      /* and the padding byte */
		bitbang_select(false);
		if (raw_dumps < 8) {
			raw_dumps++;
			dump("message", first, 4);
		}
		if (*code == STATUS_CONTINUED_READ) {
			seen.bad++;
			return false;
		}
		if (!tcm_payload_complete(payload)) {
			return false;
		}
		*length = payload;
		return true;
	}
	if (!spi_transfer(NULL, first, sizeof(first))) {
		return false;
	}
	if (raw_dumps < 6 && first[0] != 0xff) {
		raw_dumps++;
		dump("raw", first, 16);
	}
	if (first[0] != TCM_MARKER) {
		seen.bad++;
		if (seen.bad <= 4) {
			dump("no marker", first, 8);
		}
		return false;
	}
	*code = first[1];
	*length = 0;
	if (*code == STATUS_CONTINUED_READ) {
		/* the rest of a message we lost track of: what is left of it was just read (and dropped) */
		seen.bad++;
		return false;
	}
	payload = le16(first + 2);
	if (*code == STATUS_IDLE || *code == STATUS_BUSY || *code == STATUS_INVALID || payload == 0) {
		return true;
	}
	if (payload > TCM_MAX_PAYLOAD) {
		say("message %#x of %u bytes: too long\n", *code, payload);
		return false;
	}
	total = 4 + payload + 1;
	if (total <= sizeof(first)) {
		memcpy(message, first + 4, payload + 1);
	} else {
		uint32_t have = sizeof(first) - 4, rest = total - sizeof(first);

		memcpy(message, first + 4, have);
		/* the rest, behind 0xa5 0x03, padding byte included */
		if (!spi_transfer(NULL, message + have - 2, rest + 2)) {
			return false;
		}
		if (message[have - 2] != TCM_MARKER || message[have - 1] != STATUS_CONTINUED_READ) {
			seen.bad++;
			if (seen.bad <= 4) {
				dump("bad continued read", message + have - 2, 16);
			}
			return false;
		}
		/* the two header bytes sat where the payload goes on: put back what they overwrote */
		memcpy(message + have - 2, first + 4 + have - 2, 2);
	}
	if (!tcm_payload_complete(payload)) {
		return false;
	}
	*length = payload;
	return true;
}

static bool
tcm_write(uint8_t command, const uint8_t *payload, uint32_t length)
{
	uint8_t out[64];

	if (stop_requested() || length > sizeof(out) - 3 || (length && payload == NULL)) {
		return false;
	}
	out[0] = command;
	out[1] = (uint8_t)length;
	out[2] = (uint8_t)(length >> 8);
	if (length) {
		memcpy(out + 3, payload, length);
	}
	return spi_transfer(out, NULL, length + 3);
}

/* Wait (up to ms) for the interrupt line and read what comes, until a message with code `want`. */
static bool
tcm_wait_for(uint8_t want, uint32_t *length, uint32_t ms)
{
	uint64_t deadline;

	clock_interval_to_deadline(ms, NSEC_PER_MSEC, &deadline);
	while (!stop_requested() && mach_absolute_time() < deadline) {
		uint8_t code;

		if (gpio_get(PIN_INT)) {
			sleep_us(500);
			continue;
		}
		if (!tcm_read(&code, length)) {
			sleep_ms(1);
			continue;
		}
		if (code == want) {
			return true;
		}
		if (code != STATUS_IDLE && seen.other++ < 12) {
			say("while waiting for %#x: message %#x (%u bytes)\n", want, code, *length);
			if (*length) {
				dump("  payload", message, *length);
			}
		}
	}
	return false;
}

static bool
tcm_command(uint8_t command, const uint8_t *payload, uint32_t payload_length, uint32_t *length)
{
	if (!tcm_write(command, payload, payload_length)) {
		return false;
	}
	if (!tcm_wait_for(STATUS_OK, length, 500)) {
		say("command %#x: no answer\n", command);
		return false;
	}
	return true;
}

static void
touch_reset(void)
{
	gpio_set(PIN_RESET, false);
	sleep_ms(10);                   /* synaptics,reset-active-ms */
	gpio_set(PIN_RESET, true);
	sleep_ms(200);                  /* synaptics,reset-delay-ms */
}

static bool
tcm_probe(void)
{
	uint32_t length = 0;
	bool identified;

	touch_reset();
	identified = tcm_wait_for(REPORT_IDENTIFY, &length, 1000);
	if (!identified) {
		say("no identify report after reset (interrupt line %u); asking\n", gpio_get(PIN_INT));
		if (!tcm_write(CMD_IDENTIFY, NULL, 0)) {
			return false;
		}
		identified = tcm_wait_for(STATUS_OK, &length, 500);
	}
	if (!identified || length < 2) {
		say("the chip does not identify itself\n");
		return false;
	}
	dump("identify", message, length);
	if (length >= 22) {
		char part[17];

		memcpy(part, message + 2, 16);
		part[16] = 0;
		say("identify: version %u, mode %#x, part \"%s\", build %u\n", message[0], message[1], part,
		    message[18] | message[19] << 8 | message[20] << 16 | (uint32_t)message[21] << 24);
	}
	if (message[1] != MODE_APPLICATION) {
		say("not in application mode (%#x): running the application firmware\n", message[1]);
		if (!tcm_write(CMD_RUN_APPLICATION, NULL, 0) || !tcm_wait_for(REPORT_IDENTIFY, &length, 1000) ||
		    length < 2 || message[1] != MODE_APPLICATION) {
			say("still not in application mode\n");
			return false;
		}
	}
	{
		/* the chip sends a 1708-byte frame of its own unasked, every frame: too much for pins driven by hand */
		uint8_t report = REPORT_XIAOMI_FRAME;

		if (!tcm_command(CMD_DISABLE_REPORT, &report, 1, &length)) {
			say("report %#x could not be turned off\n", report);
		}
	}
	if (tcm_command(CMD_GET_APPLICATION_INFO, NULL, 0, &length) && length >= 40) {
		dump("application info", message, length);
		max_x = le16(message + 32);
		max_y = le16(message + 34);
		max_objects = le16(message + 36);
		say("application info: status %u, max x %u, max y %u, %u objects\n", le16(message + 2), max_x, max_y,
		    max_objects);
		if (max_objects == 0 || max_objects > MAX_OBJECTS) {
			max_objects = MAX_OBJECTS;
		}
		if (max_x == 0 || max_y == 0) {
#if HAOTIAN_PORT
			max_x = 1440;
			max_y = 3200;
#else
			max_x = 1200;
			max_y = 2670;
#endif
		}
	}
	if (!tcm_command(CMD_GET_TOUCH_REPORT_CONFIG, NULL, 0, &length) || length == 0 || length > sizeof(touch_config)) {
		say("no touch report configuration\n");
		return false;
	}
	memcpy(touch_config, message, length);
	touch_config_size = length;
	dump("touch report configuration", touch_config, length);
	{
		uint8_t report = REPORT_TOUCH;

		tcm_command(CMD_ENABLE_REPORT, &report, 1, &length);
	}
	/*
	 * Out of any low-power state the boot left it in: out of deep sleep, the wake-up gesture mode
	 * off (Xiaomi's firmware may sit in it, scanning but not reporting fingers, until the display
	 * is said to be on), and what scan mode it says it is in: 0/1 normal idle/active, 2/3 the
	 * low-power wake-up gesture mode, 4 asleep.
	 */
	{
		uint8_t off_gesture[3] = { DC_ENABLE_WAKEUP_GESTURE_MODE, 0, 0 }, mode_id = DC_TOUCH_SCAN_MODE;

		say("exit deep sleep: %s\n", tcm_command(CMD_EXIT_DEEP_SLEEP, NULL, 0, &length) ? "ok" : "no answer");
		say("wake-up gesture mode off: %s\n",
		    tcm_command(CMD_SET_DYNAMIC_CONFIG, off_gesture, sizeof(off_gesture), &length) ? "ok" : "no answer");
		if (tcm_command(CMD_GET_DYNAMIC_CONFIG, &mode_id, 1, &length) && length >= 2) {
			say("scan mode %u\n", le16(message));
		} else {
			say("scan mode: no answer\n");
		}
	}
	return true;
}

/* ---- touch reports ---------------------------------------------------------------------------------- */

enum {
	TOUCH_END = 0, TOUCH_FOREACH_ACTIVE_OBJECT, TOUCH_FOREACH_OBJECT, TOUCH_FOREACH_END, TOUCH_PAD_TO_NEXT_BYTE,
	TOUCH_TIMESTAMP, TOUCH_OBJECT_N_INDEX, TOUCH_OBJECT_N_CLASSIFICATION, TOUCH_OBJECT_N_X_POSITION,
	TOUCH_OBJECT_N_Y_POSITION, TOUCH_NUM_OF_ACTIVE_OBJECTS = 24,
};
#define CLASS_LIFT              0
#define CLASS_FINGER            1
#define CLASS_GLOVED_FINGER     2

static struct {
	uint32_t status, x, y;
} object[MAX_OBJECTS];
static bool down[MAX_OBJECTS];
static uint32_t last_x[MAX_OBJECTS], last_y[MAX_OBJECTS];  /* so an UP carries where the finger was, not 0,0 */

static uint32_t
bits_at(const uint8_t *report, uint32_t size, uint32_t offset, uint32_t bits)
{
	uint32_t value = 0;

	if (bits == 0 || bits > 32 || offset + bits > size * 8) {
		return 0;
	}
	for (uint32_t i = 0; i < bits; i++) {
		uint32_t at = offset + i;

		value |= (uint32_t)(report[at / 8] >> (at % 8) & 1) << i;
	}
	return value;
}

static void
touch_report(const uint8_t *report, uint32_t size)
{
	uint32_t idx = 0, offset = 0, obj = 0, next = 0, objects = 0, active = 0, end_of_foreach = 0;
	bool active_only = false, have_active = false;
	uint32_t budget = (uint32_t)sizeof(touch_config) * (MAX_OBJECTS + 1);
	uint32_t loop_offset = 0;
	bool in_loop = false;

	if (size > TCM_MAX_PAYLOAD || max_x == 0 || max_y == 0 ||
	    max_objects == 0 || max_objects > MAX_OBJECTS || touch_config_size > sizeof(touch_config)) {
		seen.bad++;
		return;
	}

	memset(object, 0, sizeof(object));
	while (idx < touch_config_size) {
		if (budget-- == 0) {
			goto malformed;
		}
		uint8_t code = touch_config[idx++];
		uint32_t bits, value;

		switch (code) {
		case TOUCH_END:
			if (in_loop) {
				goto malformed;
			}
			goto done;
		case TOUCH_FOREACH_ACTIVE_OBJECT:
		case TOUCH_FOREACH_OBJECT:
			if (in_loop) {
				goto malformed;
			}
			in_loop = true;
			obj = 0;
			objects = 0;
			next = idx;
			loop_offset = offset;
			active_only = code == TOUCH_FOREACH_ACTIVE_OBJECT;
			if (active_only && have_active && active == 0) {
				/* No object payload exists. Skip descriptors, not report bits. */
				while (idx < touch_config_size && touch_config[idx] != TOUCH_FOREACH_END) {
					uint8_t field = touch_config[idx++];
					if (field < TOUCH_PAD_TO_NEXT_BYTE ||
					    (field != TOUCH_PAD_TO_NEXT_BYTE && idx >= touch_config_size)) {
						goto malformed;
					}
					if (field != TOUCH_PAD_TO_NEXT_BYTE) {
						idx++;
					}
				}
				if (idx >= touch_config_size) {
					goto malformed;
				}
				end_of_foreach = ++idx;
				in_loop = false;
			}
			break;
		case TOUCH_FOREACH_END:
			if (!in_loop || offset <= loop_offset) {
				goto malformed;
			}
			end_of_foreach = idx;
			in_loop = false;
			if (active_only) {
				if (have_active) {
					objects++;
					if (objects < active) {
						idx = next;
						in_loop = true;
					}
				} else if (offset < size * 8) {
					if (++objects >= max_objects) {
						goto malformed;
					}
					idx = next;
					in_loop = true;
				}
			} else {
				obj = ++objects;
				if (objects < max_objects) {
					idx = next;
					in_loop = true;
				}
			}
			loop_offset = offset;
			break;
		case TOUCH_PAD_TO_NEXT_BYTE:
			offset = (offset + 7) / 8 * 8;
			break;
		default:
			if (idx >= touch_config_size) {
				goto malformed;
			}
			bits = touch_config[idx++];
			if (bits == 0 || offset > size * 8 || bits > size * 8 - offset) {
				goto malformed;
			}
			value = bits <= 32 ? bits_at(report, size, offset, bits) : 0;
			offset += bits;
			if (code == TOUCH_OBJECT_N_INDEX) {
				if (value >= max_objects) {
					goto malformed;
				}
				obj = value;
			} else if (obj < MAX_OBJECTS && code == TOUCH_OBJECT_N_CLASSIFICATION) {
				object[obj].status = value;
			} else if (obj < MAX_OBJECTS && code == TOUCH_OBJECT_N_X_POSITION) {
				object[obj].x = value;
			} else if (obj < MAX_OBJECTS && code == TOUCH_OBJECT_N_Y_POSITION) {
				object[obj].y = value;
			} else if (code == TOUCH_NUM_OF_ACTIVE_OBJECTS) {
				active = value;
				if (active > max_objects) {
					goto malformed;
				}
				have_active = true;
				if (active == 0 && end_of_foreach != 0) {
					idx = end_of_foreach;
				}
			}
			break;
		}
	}
	if (in_loop) {
		goto malformed;
	}
done:
	for (uint32_t i = 0; i < max_objects && i < MAX_OBJECTS; i++) {
		bool touching = object[i].status == CLASS_FINGER || object[i].status == CLASS_GLOVED_FINGER;
		uint32_t raw_x = object[i].x > max_x ? max_x : object[i].x;
		uint32_t raw_y = object[i].y > max_y ? max_y : object[i].y;
		uint32_t x = (uint32_t)((uint64_t)raw_x * 65535 / max_x);
		uint32_t y = (uint32_t)((uint64_t)raw_y * 65535 / max_y);

		if (touching) {
			if (seen.touches++ < 6) {
				say("finger %u at %u, %u (raw %u, %u)\n", i, x, y, object[i].x, object[i].y);
			}
			seen.last_x = object[i].x;
			seen.last_y = object[i].y;
			redwood_touch_post(down[i] ? REDWOOD_TOUCH_MOVE : REDWOOD_TOUCH_DOWN, i, x, y);
			last_x[i] = x;
			last_y[i] = y;
			down[i] = true;
		} else if (down[i]) {
			/* the finger has lifted where it last was -- not 0,0, which UIKit reads as a drag off the
			 * control and cancels the tap (a Settings row highlights but does not open) */
			redwood_touch_post(REDWOOD_TOUCH_UP, i, last_x[i], last_y[i]);
			down[i] = false;
		}
	}
	return;
malformed:
	/* A partial frame must neither invent contacts nor release existing fingers. */
	seen.bad++;
}

/*
 * Xiaomi's 0xc0 frame (1708 bytes): what is in it is not known yet. The first one is written out
 * whole; after it, for each frame, how many bytes differ from that first one and where the first
 * differences are -- with a finger on the screen and without, that tells a capacitance image from
 * a list of touches.
 */
static uint8_t frame0[2048];
static uint32_t frame0_length;

static void
xiaomi_frame(const uint8_t *p, uint32_t length)
{
	static unsigned frames;
	char line[200];
	uint32_t differ = 0, n = 0;

	frames++;
	if (frame0_length == 0 && length <= sizeof(frame0)) {
		memcpy(frame0, p, length);
		frame0_length = length;
		for (uint32_t at = 0; at < length; at += 48) {
			uint32_t k = 0;

			for (uint32_t i = at; i < length && i < at + 48; i++) {
				k += snprintf(line + k, sizeof(line) - k, "%02x", p[i]);
			}
			say("frame0 %4u: %s\n", at, line);
		}
		return;
	}
	if (length != frame0_length || frames > 40) {
		return;
	}
	for (uint32_t i = 12; i < length; i++) {        /* (the first 12: a counter and time stamps) */
		differ += p[i] != frame0[i];
	}
	if (differ == 0 && frames % 20 != 0) {
		return;
	}
	n = (uint32_t)snprintf(line, sizeof(line), "frame %u: %u bytes differ:", frames, differ);
	for (uint32_t i = 12, shown = 0; i < length && shown < 14; i++) {
		if (p[i] != frame0[i]) {
			n += (uint32_t)snprintf(line + n, sizeof(line) - n, " %u=%02x", i, p[i]);
			shown++;
		}
	}
	say("%s\n", line);
}

/* one message; false when there was none (idle, or nothing readable) */
static bool
poll_once(void)
{
	uint8_t code;
	uint32_t length;

	seen.polls++;
	if (!tcm_read(&code, &length)) {
		return false;
	}
	if (code == STATUS_IDLE) {
		return false;
	}
	seen.messages++;
	if (code == REPORT_TOUCH) {
		if (seen.touch_reports++ < 3) {
			dump("touch report", message, length);
		}
		touch_report(message, length);
	} else if (code == REPORT_XIAOMI_FRAME) {
		static unsigned asked;

		xiaomi_frame(message, length);
		/*
		 * In step with the chip again: what Android's driver does for enable_touch_raw=0 -- the
		 * frames for Xiaomi's host-side touch processing off, the chip's own touch reports on.
		 * (Seen to work there: 319 touch reports in 15 s, and the frames stop.)
		 */
		if (asked < 5) {
			uint8_t frame = REPORT_XIAOMI_FRAME, touch = REPORT_TOUCH;

			asked++;
			tcm_write(CMD_DISABLE_REPORT, &frame, 1);
			delay_for_interval(2, NSEC_PER_MSEC);
			tcm_write(CMD_ENABLE_REPORT, &touch, 1);
			say("frame %u read whole: asked for the chip's own touch reports instead (time %u)\n", seen.messages,
			    asked);
		}
	} else if (code != STATUS_IDLE) {
		if (seen.other++ < 8) {
			say("message %#x (%u bytes)\n", code, length);
			if (length) {
				dump("  payload", message, length);
			}
		}
		if (code == REPORT_IDENTIFY) {
			/* the chip has reset itself: its reports are to be asked for again */
			uint8_t report = REPORT_TOUCH;

			tcm_write(CMD_ENABLE_REPORT, &report, 1);
		}
	}
	return true;
}

/* ---- all of it together ---------------------------------------------------------------------------- */

static void
show_state(const char *when)
{
	say("%s: votes %#x, clocks %#x %#x %#x, s2 div %#x, ref cfg %#x dfs %#x; pins %#x %#x %#x %#x, reset %#x/%u, "
	    "int %#x/%u\n", when, rd(GCC_VOTE), rd(GCC_QUP1_M_AHB_CBCR), rd(GCC_QUP1_S_AHB_CBCR), rd(GCC_QUP1_S2_CBCR),
	    rd(GCC_QUP1_S2_DIV), rd(GCC_QSPI_REF_CFG), rd(GCC_QSPI_REF_DFSR), rd(GPIO_CFG(PIN_MISO)), rd(GPIO_CFG(PIN_MOSI)),
	    rd(GPIO_CFG(PIN_CLK)), rd(GPIO_CFG(PIN_CS)), rd(GPIO_CFG(PIN_RESET)), rd(GPIO_IO(PIN_RESET)),
	    rd(GPIO_CFG(PIN_INT)), rd(GPIO_IO(PIN_INT)));
}

static bool
bring_up(void)
{
	const char *problem;
	uint32_t spi_hz = 5000000;
	PE_parse_boot_argn("touch_half_ns", &bitbang_half_ns, sizeof(bitbang_half_ns));
	if (bitbang_half_ns < 50 || bitbang_half_ns > 10000) {
		bitbang_half_ns = 150;
	}
	clock_interval_to_absolutetime_interval(bitbang_half_ns, 1, &bitbang_half_ticks);
	if (bitbang_half_ticks == 0) {
		bitbang_half_ticks = 1;
	}

	show_state("as found");
	gpio_output(PIN_RESET, false, PULL_NONE, 2);
	gpio_configure(PIN_INT, 0, PULL_NONE, 2, false);
	supplies_on();
	sleep_ms(50);                   /* synaptics,power-delay-ms */

	if (!clocks_on()) {
		show_state("no clocks");
		say("driving the pins by hand\n");
		spi_transfer = bitbang_transfer;
		bitbang_init();
		return tcm_probe();
	}
	say("serial engine: firmware %#x, interface %#x, parameters %#x, source clock %u Hz (select %u)\n",
	    rd(GENI_FW_REVISION_RO), rd(GENI_IF_DISABLE_RO), rd(SE_HW_PARAM_0), se_source_hz, se_clock_select);
	/* boot-arg touch_spi_hz=N (the device tree allows the chip 15 MHz) */
	PE_parse_boot_argn("touch_spi_hz", &spi_hz, sizeof(spi_hz));
	if (spi_hz < 100000 || spi_hz > 15000000) {
		spi_hz = 5000000;
	}
	problem = geni_spi_init(spi_hz);
	if (problem == NULL) {
		gpio_configure(PIN_MISO, PIN_FUNCTION_SPI, PULL_DOWN, 6, false);
		gpio_configure(PIN_MOSI, PIN_FUNCTION_SPI, PULL_DOWN, 6, false);
		gpio_configure(PIN_CLK, PIN_FUNCTION_SPI, PULL_DOWN, 6, false);
		gpio_configure(PIN_CS, PIN_FUNCTION_SPI, PULL_DOWN, 6, false);
		show_state("powered, on the serial engine");
		say("the bus at %u Hz\n", spi_hz);
		return tcm_probe();
	}
	/* boot-arg touch_half_ns=N: half a clock period of the hand-driven bus (default 200 ns) */
	PE_parse_boot_argn("touch_half_ns", &bitbang_half_ns, sizeof(bitbang_half_ns));
	say("%s: driving the pins by hand (half period %u ns)\n", problem, bitbang_half_ns);
	spi_transfer = bitbang_transfer;
	bitbang_init();
	show_state("powered, pins by hand");
	return tcm_probe();
}

/*
 * Under Gunyah a register this VM may not touch is no fault of ours to catch: the whole phone
 * stops. So the first time, every block is looked at alone, with a line before it and a pause
 * after (the loader's log is fetched every few seconds), and boot-arg dadatouch_step=N stops
 * before step N: the last line seen says which block it was.
 */
static bool
careful_look(void)
{
	static const struct {
		const char *what;
		vm_offset_t *base;
		uint32_t offset;
	} steps[] = {
		{ "TLMM MISO cfg", &tlmm, 0x1000 * PIN_MISO },
		{ "TLMM reset cfg", &tlmm, 0x1000 * PIN_RESET },
		{ "TLMM interrupt io", &tlmm, 0x1000 * PIN_INT + 4 },
		{ "GCC vote 0x52008", &gcc, 0x52008 },
		{ "GCC m_ahb 0x23140", &gcc, 0x23140 },
		{ "GCC s2 cbcr 0x1827c", &gcc, 0x1827c },
		{ "GCC s2 div 0x1828c", &gcc, 0x1828c },
		{ "GCC qspi ref cmd 0x188c0", &gcc, 0x188c0 },
		{ "GCC qspi ref dfsr 0x188d4", &gcc, 0x188d4 },
		{ "RSC drv2 id", &rsc, 0 },
		{ "RSC tcs0 status", &rsc, TCS_BASE + 0x28 },
		{ "cmd-db magic", &cmd_db, 4 },
		{ "SE fw revision", &se, 0x68 },
		{ "SE hw param", &se, 0xe24 },
	};
	uint32_t stop = 99, pause_ms = 2500;

	PE_parse_boot_argn("dadatouch_step", &stop, sizeof(stop));
	PE_parse_boot_argn("dadatouch_pause", &pause_ms, sizeof(pause_ms));
	for (uint32_t i = 0; i < sizeof(steps) / sizeof(steps[0]); i++) {
		if (stop_requested()) {
			return false;
		}
		if (i >= stop) {
			say("stopping before step %u (dadatouch_step)\n", i);
			return false;
		}
		say("step %u: reading %s ...\n", i, steps[i].what);
		sleep_ms(pause_ms);
		if (stop_requested()) {
			return false;
		}
		say("step %u: %s = %#x\n", i, steps[i].what, rd(*steps[i].base + steps[i].offset));
	}
	return true;
}

/* Only this worker touches controller MMIO. Shutdown requests never restore
 * pins while an SPI transfer is still using them. Shared RPMh/GCC votes stay
 * enabled; removing a shared power vote here could break another device. */
static const unsigned owned_pins[] = { PIN_MISO, PIN_MOSI, PIN_CLK, PIN_CS, PIN_RESET, PIN_INT };
static uint32_t saved_cfg[6], saved_output[6];
static bool pins_saved;

static void
restore_hardware(void)
{
	for (uint32_t i = 0; i < MAX_OBJECTS; i++) {
		if (down[i]) {
			redwood_touch_post(REDWOOD_TOUCH_UP, i, last_x[i], last_y[i]);
			down[i] = false;
		}
	}
	if (geni_configured) {
		wr(GENI_M_IRQ_EN, 0);
		geni_recover();
		wr(GENI_M_IRQ_EN, saved_m_irq_en);
		wr(SE_IRQ_EN, saved_se_irq_en);
		wr(SE_GSI_EVENT_EN, saved_gsi_event_en);
		geni_configured = false;
	}
	if (pins_saved) {
		for (unsigned i = 0; i < sizeof(owned_pins) / sizeof(owned_pins[0]); i++) {
			wr(GPIO_IO(owned_pins[i]), saved_output[i]);
			wr(GPIO_CFG(owned_pins[i]), saved_cfg[i]);
		}
		__asm__ volatile("dsb sy" ::: "memory");
		pins_saved = false;
	}
}

static void
dada_touch_thread(__unused void *parameter, __unused wait_result_t result)
{
	uint32_t poll_us = 4000;
	unsigned idle = 0;

	PE_parse_boot_argn("touch_poll_us", &poll_us, sizeof(poll_us));
	if (poll_us < 250 || poll_us > 100000) {
		poll_us = 4000;
	}
	{
		uint32_t start_s = 15;

		PE_parse_boot_argn("dadatouch_delay", &start_s, sizeof(start_s));
		if (start_s > 60) {
			start_s = 15;
		}
		say("thread up; looking at the hardware in %u s\n", start_s);
		sleep_ms(start_s * 1000);
	}
	if (stop_requested()) {
		goto finished;
	}
	tlmm = ml_io_map(TLMM_PHYS, TLMM_SIZE);
	gcc = ml_io_map(GCC_PHYS, GCC_SIZE);
	se = ml_io_map(SE_PHYS, SE_SIZE);
	rsc = ml_io_map(RSC_PHYS, RSC_SIZE);
	cmd_db = ml_io_map_wcomb(CMD_DB_PHYS, CMD_DB_SIZE);
	if (!tlmm || !gcc || !se || !rsc || !cmd_db) {
		say("MMIO mapping failed; no controller registers changed\n");
		goto finished;
	}

	uint32_t careful = 0;

	PE_parse_boot_argn("dadatouch_careful", &careful, sizeof(careful));
	if (careful && !careful_look()) {
		goto finished;
	}
	if (stop_requested()) {
		goto finished;
	}
	for (unsigned i = 0; i < sizeof(owned_pins) / sizeof(owned_pins[0]); i++) {
		saved_cfg[i] = rd(GPIO_CFG(owned_pins[i]));
		saved_output[i] = rd(GPIO_IO(owned_pins[i])) & 2;
	}
	pins_saved = true;
	if (!bring_up()) {
		say("no touch screen\n");
		goto finished;
	}
	say("touch screen ready (%u x %u, %u objects); the interrupt line looked at every %u us\n", max_x, max_y,
	    max_objects, poll_us);
	uint64_t next_report;

	clock_interval_to_deadline(30, NSEC_PER_SEC, &next_report);
	while (!stop_requested()) {
		/*
		 * Read until the chip says it has nothing more: it queues a report per frame (Xiaomi's 0xc0
		 * frames among them, tens a second), and one read per look let the queue overflow and drop
		 * the touch reports. (The interrupt line, gpio162, has not been seen low here: not relied on.)
		 */
		(void)idle;
		for (int n = 0; n < 32 && !stop_requested() && poll_once(); n++) {
		}
		if (mach_absolute_time() >= next_report) {
			clock_interval_to_deadline(30, NSEC_PER_SEC, &next_report);
			say("so far %u messages in %u looks (%u touch reports, %u other, %u bad), %u finger positions, "
			    "the last at %u, %u; interrupt line %s\n", seen.messages, seen.polls, seen.touch_reports, seen.other,
			    seen.bad, seen.touches, seen.last_x, seen.last_y, gpio_get(PIN_INT) ? "high" : "low");
		}
		sleep_us(poll_us);
	}
finished:
	restore_hardware();
	say("worker stopped; pressed contacts released and owned pins restored\n");
	__atomic_store_n(&running, 0, __ATOMIC_RELEASE);
	thread_terminate(current_thread());
}

void
hakios_dada_touch_init(void)
{
	uint32_t enabled = 0;
	thread_t thread;

	if (!PE_parse_boot_argn("dadatouch", &enabled, sizeof(enabled)) || !enabled) {
		return;
	}
	if (__atomic_exchange_n(&started, 1, __ATOMIC_ACQ_REL)) {
		return;
	}
	__atomic_store_n(&running, 1, __ATOMIC_RELEASE);
	if (stop_requested()) {
		__atomic_store_n(&running, 0, __ATOMIC_RELEASE);
		return;
	}
	if (kernel_thread_start(dada_touch_thread, NULL, &thread) == KERN_SUCCESS) {
		thread_deallocate(thread);
	} else {
		__atomic_store_n(&running, 0, __ATOMIC_RELEASE);
		say("could not start touch worker\n");
	}
}

bool
hakios_dada_touch_stop(void)
{
	/* Latch shutdown even before init: a late init must not start MMIO work. */
	__atomic_store_n(&stopping, 1, __ATOMIC_RELEASE);
	if (!__atomic_load_n(&started, __ATOMIC_ACQUIRE)) {
		return true;
	}
	for (unsigned i = 0; i < 3000 && __atomic_load_n(&running, __ATOMIC_ACQUIRE); i++) {
		/* Must keep waiting after setting stopping; sleep_us intentionally exits early. */
		delay_for_interval(1000, NSEC_PER_USEC);
	}
	if (__atomic_load_n(&running, __ATOMIC_ACQUIRE)) {
		say("stop timed out; do not return to firmware with an active SPI worker\n");
		return false;
	}
	return true;
}
