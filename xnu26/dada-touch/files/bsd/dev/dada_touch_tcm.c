/* See sys/dada_touch.h. No controller firmware is uploaded or flashed here. */
#include <sys/dada_touch.h>

/* All slots, not only active slots: explicit lift reports cannot leave fingers down.
 * 4-bit slot, 4-bit classification, 16-bit X, 16-bit Y, LSB-first fields.
 * Use this decoder ONLY after SET_TOUCH_REPORT_CONFIG has returned STATUS_OK.
 */
const uint8_t dada_tcm_report_config[12] = {
    0x02, 0x06, 4, 0x07, 4, 0x08, 16, 0x09, 16, 0x03, 0x00, 0x00
};

static unsigned
le16(const uint8_t *p)
{
    return (unsigned)p[0] | (unsigned)p[1] << 8;
}

int
dada_tcm_read_message_features(const struct dada_tcm_bus *bus, struct dada_tcm_message *msg,
    unsigned features)
{
    uint8_t header[4], chunk[DADA_TCM_READ_CHUNK + 2];
    unsigned length, offset = 0;
    int error;

    msg->length = 0;
    msg->code = 0xff;
    msg->trailer_length = 0;
    for (unsigned i=0;i<sizeof(msg->trailer);i++) msg->trailer[i]=0x5a;
    if (features & ~(DADA_TCM_CRC|DADA_TCM_EXTRA_RC)) return DADA_TCM_UNSUPPORTED;
    unsigned tail=(features&DADA_TCM_CRC ? 2u:0u)+(features&DADA_TCM_EXTRA_RC ? 1u:0u);
    if (tail) tail++; /* vendor protocol adds another EOM after CRC/RC */
    error = bus->read(bus->context, header, sizeof(header));
    if (error) return DADA_TCM_IO;
    /* The supplied donor retries one not-yet-valid marker after 1 ms.
     * Bound the retry and still require the v1 framing before using length.
     */
    if (header[0] != 0xa5) {
        bus->delay_us(bus->context,1000);
        error=bus->read(bus->context,header,sizeof(header));
        if (error) return DADA_TCM_IO;
    }
    /* TCM v2 has different framing and CRC. Never treat it as v1. */
    if (header[0] != 0xa5 || header[1] == 0x03 || header[1] == 0xff)
        return DADA_TCM_PROTOCOL;
    length = le16(header + 2);
    if (length > DADA_TCM_PAYLOAD) return DADA_TCM_SIZE;
    if (length != 0) {
        /* Port the vendor v1 continued-read algorithm, bounded for our SPI
         * transport. Every continuation has its own a5/03 prefix. Consume
         * the complete asynchronous THP report before awaiting a response.
         * Source: MiCode dada-v-oss e02de97, syna_tcm_v1_continued_read().
         */
        unsigned remaining = length + 1 + tail; /* payload, EOM, optional CRC/RC/EOM */
        while (remaining) {
            unsigned count = remaining > DADA_TCM_READ_CHUNK ? DADA_TCM_READ_CHUNK : remaining;
            /* The original protocol synthesizes a final standalone EOM. */
            if (count == 1 && offset == length+tail) break;
            bus->delay_us(bus->context, 100);
            error = bus->read(bus->context, chunk, count + 2);
            if (error) return DADA_TCM_IO;
            if (chunk[0] != 0xa5 || chunk[1] != 0x03) return DADA_TCM_PROTOCOL;
            for (unsigned i = 0; i < count; i++, offset++) {
                if (offset < length) msg->data[offset] = chunk[i + 2];
                else if (offset==length) {
                    if (chunk[i+2]!=0x5a) return DADA_TCM_PROTOCOL;
                } else msg->trailer[offset-length-1]=chunk[i+2];
            }
            remaining -= count;
        }
        msg->trailer_length=tail;
        if (tail && msg->trailer[tail-1]!=0x5a) return DADA_TCM_PROTOCOL;
    }
    msg->code = header[1];
    msg->length = length;
    return 0;
}

int
dada_tcm_read_message(const struct dada_tcm_bus *bus, struct dada_tcm_message *msg)
{
    return dada_tcm_read_message_features(bus,msg,0);
}

int
dada_tcm_detect_features(const struct dada_tcm_message *msg, unsigned *features)
{
    /* Match syna_tcm_v1_detect: initially read CRC+RC+EOM, then compare
     * the returned positions with the controller's 0x5a filler bytes.
     * Use only the unsolicited startup IDENTIFY, before sending commands.
     */
    if (!features || msg->code!=DADA_TCM_IDENTIFY || !msg->length ||
        msg->trailer_length!=4 || msg->trailer[3]!=0x5a) return DADA_TCM_PROTOCOL;
    *features=0;
    if (le16(msg->trailer)!=0x5a5a) *features|=DADA_TCM_CRC;
    if (msg->trailer[2]!=0x5a) *features|=DADA_TCM_EXTRA_RC;
    return 0;
}

uint16_t
dada_tcm_crc16(const uint8_t *p, unsigned length, uint16_t value)
{
    /* Same CCITT polynomial/initial value as the stock table implementation. */
    for (unsigned i=0;i<length;i++) {
        value^=(uint16_t)((uint16_t)p[i]<<8);
        for(unsigned bit=0;bit<8;bit++)
            value=(uint16_t)((value<<1)^((value&0x8000u)?0x1021u:0u));
    }
    return value;
}

int
dada_tcm_command_features(const struct dada_tcm_bus *bus, uint8_t command,
    const uint8_t *payload, unsigned length, struct dada_tcm_message *msg, unsigned features)
{
    uint8_t tx[DADA_TCM_CONFIG + 3];
    unsigned i;
    int error;

    unsigned crc_bytes=features&DADA_TCM_CRC ? 2u:0u;
    if (features & ~(DADA_TCM_CRC|DADA_TCM_EXTRA_RC)) return DADA_TCM_UNSUPPORTED;
    if (length > sizeof(tx) - 3 - crc_bytes || (length && !payload)) return DADA_TCM_SIZE;
    tx[0] = command;
    tx[1] = (uint8_t)length;
    tx[2] = (uint8_t)(length >> 8);
    for (i = 0; i < length; i++) tx[i + 3] = payload[i];
    if (crc_bytes) {
        uint16_t crc=dada_tcm_crc16(tx,length+3,0xffff);
        tx[length+3]=(uint8_t)crc;tx[length+4]=(uint8_t)(crc>>8);
    }
    if (bus->write(bus->context, tx, length + 3 + crc_bytes)) return DADA_TCM_IO;
    for (i = 0; i < 100; i++) {
        bus->delay_us(bus->context, 5000);
        error = dada_tcm_read_message_features(bus, msg, features);
        if (error) return error;
        if (msg->code == DADA_TCM_IDENTIFY)
            return command == 0x02 ? 0 : DADA_TCM_RESTARTED;
        if (msg->code == DADA_TCM_OK) return 0;
        /* idle/no-report/ACK are transient. Touch reports may precede a reply. */
        if (msg->code == 0 || msg->code == 4 || msg->code == 7 || msg->code >= 0x10)
            continue;
        return DADA_TCM_REJECTED;
    }
    return DADA_TCM_TIMEOUT;
}

int
dada_tcm_command(const struct dada_tcm_bus *bus, uint8_t command,
    const uint8_t *payload, unsigned length, struct dada_tcm_message *msg)
{
    return dada_tcm_command_features(bus,command,payload,length,msg,0);
}

int
dada_tcm_identify(struct dada_tcm_state *state, const uint8_t *p, unsigned length)
{
    unsigned i;
    if (length < 24) return DADA_TCM_SIZE;
    state->version = p[0];
    state->mode = p[1];
    state->max_write = (uint16_t)le16(p + 22);
    for (i = 0; i < 16; i++) state->part[i] = p[i + 2] >= 32 && p[i + 2] < 127 ? p[i + 2] : '.';
    state->part[16] = 0;
    state->build_id = (uint32_t)p[18] | (uint32_t)p[19] << 8 |
        (uint32_t)p[20] << 16 | (uint32_t)p[21] << 24;
    return state->mode == 1 ? 0 : DADA_TCM_UNSUPPORTED;
}

int
dada_tcm_application(struct dada_tcm_state *state, const uint8_t *p, unsigned length)
{
    unsigned max_x, max_y, objects, config;
    if (length < 38) return DADA_TCM_SIZE;
    if (le16(p + 2) != 0) return DADA_TCM_UNSUPPORTED;
    config = le16(p + 12);
    max_x = le16(p + 32);
    max_y = le16(p + 34);
    objects = le16(p + 36);
    if (!max_x || !max_y || !objects || objects > DADA_TCM_CONTACTS ||
        config < sizeof(dada_tcm_report_config) || config > DADA_TCM_CONFIG ||
        le16(p + 14) < objects * 5 ||
        (state->max_write && state->max_write < config + 3)) return DADA_TCM_UNSUPPORTED;
    state->max_x = (uint16_t)max_x;
    state->max_y = (uint16_t)max_y;
    state->objects = (uint8_t)objects;
    state->max_config = (uint16_t)config;
    return 0;
}

static unsigned
normalized(unsigned value, unsigned max)
{
    return (value * 65535u) / max;
}

/* Adapt the stock syna_tcm_parse_touch_report control flow. Configuration
 * ends at TOUCH_REPORT_END; returned bytes beyond END are not instructions.
 * Support one bounded all/active-object loop and byte-alignment operations.
 * Scalar metadata with a declared bit length is skipped, never guessed.
 */
int
dada_tcm_install_config(struct dada_tcm_state *s, const uint8_t *p, unsigned n)
{
    unsigned i=0, start=0, end=0, fields=0, bits=0, active=0;
    int inside=0, loop=0, closed=0;
    s->config_length=0;
    if (!p || !n || n>DADA_TCM_CONFIG) return DADA_TCM_SIZE;
    while (i<n) {
        unsigned at=i, code=p[i++];
        if (!code) {
            if (inside || !closed || fields!=15 || bits<8) return DADA_TCM_UNSUPPORTED;
            for (unsigned j=0;j<i;j++) s->report_config[j]=p[j];
            s->config_length=(uint16_t)i;s->loop_start=(uint16_t)start;
            s->loop_end=(uint16_t)end;s->config_end=(uint16_t)at;s->active_only=(uint8_t)active;
            return 0;
        }
        if (code==1 || code==2) {
            if (loop || inside || closed) return DADA_TCM_UNSUPPORTED;
            loop=inside=1;active=code==1;start=i;continue;
        }
        if (code==3) {
            if (!inside) return DADA_TCM_PROTOCOL;
            inside=0;closed=1;end=at;continue;
        }
        if (code==4) continue;
        if (i>=n || !p[i]) return DADA_TCM_PROTOCOL;
        unsigned width=p[i++];
        if (inside) {
            bits+=width;
            if (code>=6 && code<=9) {
                unsigned flag=1u<<(code-6);
                if ((fields&flag) || width>32) return DADA_TCM_UNSUPPORTED;
                fields|=flag;
            }
        } else if (code>=6 && code<=9) return DADA_TCM_UNSUPPORTED;
        if (code==0x18 && (inside || width>32)) return DADA_TCM_UNSUPPORTED;
    }
    return DADA_TCM_PROTOCOL;
}

struct dada_report_fields { unsigned id,kind,x,y,count,has_count; };
static int
report_fields(const struct dada_tcm_state *s, unsigned begin, unsigned end,
    const uint8_t *p, unsigned total_bits, unsigned *offset, struct dada_report_fields *f)
{
    for (unsigned i=begin;i<end;) {
        unsigned code=s->report_config[i++];
        if (code==4) {
            *offset=(*offset+7u)&~7u;
            if (*offset>total_bits) return DADA_TCM_SIZE;
            continue;
        }
        if (i>=end || code<5) return DADA_TCM_PROTOCOL;
        unsigned width=s->report_config[i++], value=0;
        if (*offset>total_bits || width>total_bits-*offset) return DADA_TCM_SIZE;
        if (p && ((code>=6 && code<=9) || code==0x18)) {
            if (width>32) return DADA_TCM_UNSUPPORTED;
            for (unsigned bit=0;bit<width;bit++)
                value|=(unsigned)((p[(*offset+bit)/8]>>((*offset+bit)%8))&1u)<<bit;
        }
        *offset+=width;
        if (f) switch(code) {
        case 6:f->id=value;break;case 7:f->kind=value;break;
        case 8:f->x=value;break;case 9:f->y=value;break;
        case 0x18:f->count=value;f->has_count=1;break;
        default:break;
        }
    }
    return 0;
}

static int
configured_frame(struct dada_tcm_state *s, const uint8_t *p, unsigned n,
    dada_touch_emit emit, void *context)
{
    struct dada_tcm_contact next[DADA_TCM_CONTACTS]={{0,0,0}};
    struct dada_report_fields prefix={0};
    unsigned total=n*8, offset=0, count=s->objects, seen=0;
    if (!p || n>DADA_TCM_PAYLOAD || !s->objects || s->objects>DADA_TCM_CONTACTS ||
        !s->max_x || !s->max_y) return DADA_TCM_SIZE;
    int error=report_fields(s,0,s->loop_start-1,p,total,&offset,&prefix);
    if (error) return error;
    if (s->active_only && prefix.has_count) count=prefix.count;
    else if (s->active_only) {
        unsigned candidates=0, trial=offset;
        for (unsigned k=0;k<=s->objects;k++) {
            unsigned tail=trial;
            if (!report_fields(s,s->loop_end+1,s->config_end,NULL,total,&tail,NULL) &&
                (tail+7)/8==n) {count=k;candidates++;}
            if (k==s->objects || report_fields(s,s->loop_start,s->loop_end,NULL,total,&trial,NULL)) break;
        }
        if (candidates!=1) return DADA_TCM_SIZE;
    }
    if (count>s->objects) return DADA_TCM_PROTOCOL;
    for (unsigned i=0;i<count;i++) {
        struct dada_report_fields f={0};
        error=report_fields(s,s->loop_start,s->loop_end,p,total,&offset,&f);
        if (error) return error;
        if (f.id>=s->objects || (seen&(1u<<f.id))) return DADA_TCM_PROTOCOL;
        seen|=1u<<f.id;
        if (f.kind==1 || f.kind==2) {
            if (f.x>s->max_x || f.y>s->max_y) return DADA_TCM_PROTOCOL;
            next[f.id].down=1;next[f.id].x=(uint16_t)normalized(f.x,s->max_x);
            next[f.id].y=(uint16_t)normalized(f.y,s->max_y);
        }
    }
    error=report_fields(s,s->loop_end+1,s->config_end,p,total,&offset,NULL);
    if (error || (offset+7)/8!=n) return error?error:DADA_TCM_SIZE;
    /* Do not mutate or post any contact until the complete packet validates. */
    for (unsigned i=0;i<DADA_TCM_CONTACTS;i++) {
        struct dada_tcm_contact *old=&s->contacts[i], *v=&next[i];
        if(v->down && !old->down)emit(context,1,i,v->x,v->y);
        else if(v->down && (old->x!=v->x || old->y!=v->y))emit(context,2,i,v->x,v->y);
        else if(!v->down && old->down)emit(context,3,i,old->x,old->y);
        *old=*v;
    }
    return 0;
}

void
dada_tcm_release(struct dada_tcm_state *state, dada_touch_emit emit, void *context)
{
    unsigned i;
    for (i = 0; i < DADA_TCM_CONTACTS; i++) {
        if (state->contacts[i].down) {
            emit(context, 3, i, state->contacts[i].x, state->contacts[i].y);
            state->contacts[i].down = 0;
        }
    }
}

int
dada_tcm_frame(struct dada_tcm_state *state, const uint8_t *p, unsigned length,
    dada_touch_emit emit, void *context)
{
    if (state->config_length) return configured_frame(state,p,length,emit,context);
    struct dada_tcm_contact next[DADA_TCM_CONTACTS] = { { 0, 0, 0 } };
    unsigned seen = 0, i;

    if (!state->max_x || !state->max_y || !state->objects ||
        state->objects > DADA_TCM_CONTACTS || length != state->objects * 5u)
        return DADA_TCM_SIZE;
    /* Validate the entire frame before changing state or dispatching any sample. */
    for (i = 0; i < state->objects; i++) {
        unsigned id = p[5 * i] & 15, kind = p[5 * i] >> 4;
        unsigned x = le16(p + 5 * i + 1), y = le16(p + 5 * i + 3);
        if (id >= state->objects || (seen & (1u << id))) return DADA_TCM_PROTOCOL;
        seen |= 1u << id;
        /* Only contacts, never palms, hover, pen or unknown objects. */
        if (kind == 1 || kind == 2) {
            if (x > state->max_x || y > state->max_y) return DADA_TCM_PROTOCOL;
            next[id].down = 1;
            next[id].x = (uint16_t)normalized(x, state->max_x);
            next[id].y = (uint16_t)normalized(y, state->max_y);
        }
    }
    for (i = 0; i < DADA_TCM_CONTACTS; i++) {
        struct dada_tcm_contact *old = &state->contacts[i], *n = &next[i];
        if (n->down && !old->down) emit(context, 1, i, n->x, n->y);
        else if (n->down && (old->x != n->x || old->y != n->y)) emit(context, 2, i, n->x, n->y);
        else if (!n->down && old->down) emit(context, 3, i, old->x, old->y);
        *old = *n;
    }
    return 0;
}
