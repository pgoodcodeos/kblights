#include "../../core/AssignmentWriteCore.h"

#include <assert.h>
#include <stdio.h>

typedef struct {
    unsigned sends, reads, pauses, journals;
    unsigned fail_send, fail_read, fail_journal, short_reply, bad_echo, bad_status;
    int bad_checksum;
    uint8_t body[640];
} Mock;

static int send_packet(void* opaque, const uint8_t* packet, size_t length) {
    Mock* m = opaque;
    unsigned call = ++m->sends;
    assert(length == 64);
    if(call == 1) {
        uint8_t expected[64] = {4, 0x11};

        expected[8] = 10;
        assert(!memcmp(packet, expected, 64));
    } else if(call <= 11)
        assert(!memcmp(packet, m->body + (call - 2) * 64, 64));
    else if(call == 12) {
        uint8_t expected[64] = {4, 2};

        assert(!memcmp(packet, expected, 64));
    } else {
        uint8_t expected[64] = {4, 0xF0};

        assert(call == 13);
        assert(!memcmp(packet, expected, 64));
    }

    return call != m->fail_send;
}

static int receive_packet(void* opaque, uint8_t* packet, size_t length) {
    Mock* m = opaque;
    unsigned call = ++m->reads;
    assert(length == 64 && call <= 2);
    if(call == m->fail_read)
        return -1;

    memset(packet, 0, 64);
    packet[0] = 4;
    packet[1] = call == 1 ? 0x11 : 2;
    packet[3] = 1;
    if(call == 2) {
        uint32_t sum = m->bad_checksum;
        for(unsigned i = 0; i < 640; ++i)
            sum += m->body[i];

        for(unsigned i = 0; i < 4; ++i)
            packet[i + 4] = (uint8_t)(sum >> (8 * i));
    }

    if(call == m->bad_echo)
        packet[1] ^= 1;

    if(call == m->bad_status)
        packet[3] = 0;

    return call == m->short_reply ? 63 : 64;
}

static void pause_packet(void* opaque, unsigned us) {
    Mock* m = opaque;
    assert(us == (m->pauses == 0 ? 70000u : 15000u));
    ++m->pauses;
}

static int journal_packet(void* opaque, const char* event, int block) {
    Mock* m = opaque;
    unsigned call = ++m->journals;
    if(call == 1) {
        assert(!strcmp(event, "setup-attempt") && block == -1);
    } else if(call <= 11) {
        assert(!strcmp(event, "block-attempt") && block == (int)call - 2);
    } else if(call == 12)
        assert(!strcmp(event, "checksum-attempt"));
    else if(call == 13)
        assert(!strcmp(event, "checksum-verified"));
    else if(call == 14)
        assert(!strcmp(event, "activation-f0-attempt"));
    else {
        assert(call == 15 && !strcmp(event, "activation-f0-host-completed"));
    }

    return call != m->fail_journal;
}

static int run(Mock* mock, size_t size, C989WriteResult* result) {
    for(unsigned i = 0; i < 640; ++i)
        mock->body[i] = (uint8_t)(i * 19 + i / 64);

    C989WriteIO io = {mock, send_packet, receive_packet, pause_packet, journal_packet};

    return c989_assignments_upload(&io, mock->body, size, result);
}

int main(void) {
    C989WriteResult result;
    Mock m = {0};

    assert(run(&m, 640, &result));
    assert(m.sends == 13 && m.reads == 2 && m.journals == 15 && m.pauses == 16);
    assert(result.stage == 5 && result.last_attempted_block == 9);
    assert(result.activation_attempted && result.activation_host_completed);
    for(unsigned call = 1; call <= 13; ++call) {
        m = (Mock){.fail_send = call};

        assert(!run(&m, 640, &result));
        assert(m.sends == call);
        assert(result.activation_attempted == (call == 13));
        assert(!result.activation_host_completed);
    }

    for(unsigned call = 1; call <= 15; ++call) {
        m = (Mock){.fail_journal = call};

        assert(!run(&m, 640, &result));
        assert(m.sends == (call <= 12 ? call - 1 : call <= 14 ? 12 : 13));
        assert(result.activation_attempted == (call == 15));
    }

    for(unsigned call = 1; call <= 2; ++call) {
        m = (Mock){.fail_read = call};

        assert(!run(&m, 640, &result));
        m = (Mock){.short_reply = call};

        assert(!run(&m, 640, &result));
        m = (Mock){.bad_echo = call};

        assert(!run(&m, 640, &result));
        m = (Mock){.bad_status = call};

        assert(!run(&m, 640, &result));
        assert(!result.activation_attempted);
    }

    m = (Mock){.bad_checksum = 1};

    assert(!run(&m, 640, &result));
    assert(m.sends == 12 && !result.activation_attempted);
    const size_t invalid[] = {0, 64, 639, 641, 1152};

    for(unsigned i = 0; i < sizeof(invalid) / sizeof(*invalid); ++i) {
        m = (Mock){0};

        assert(!run(&m, invalid[i], &result));
        assert(!m.sends && !m.journals);
    }

    assert(!c989_assignments_upload(NULL, m.body, 640, &result));
    puts("PASS: exact 13-report assignment stream, 70ms setup, one checksum-gated F0, all "
         "transport/journal failures stop without retry.");
}
