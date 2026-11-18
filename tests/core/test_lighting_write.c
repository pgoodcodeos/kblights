#include "../../core/WriteCore.h"

#include <assert.h>
#include <stdio.h>

typedef struct {
    int sends, reads, pauses, journals;
    int fail_send, fail_read, fail_journal, bad_reply, short_reply, bad_checksum;
    int expect_activation;
    uint8_t body[1152];
} Mock;

static int send_packet(void* ctx, const uint8_t* p, size_t n) {
    Mock* m = ctx;
    int i = ++m->sends;
    assert(n == 64);
    if(i == 1) {
        uint8_t expected[64] = {4, 0x13};

        expected[8] = 18;
        assert(!memcmp(p, expected, 64));
    } else if(i <= 19)
        assert(!memcmp(p, m->body + (i - 2) * 64, 64));
    else if(i == 20) {
        uint8_t expected[64] = {4, 2};

        assert(!memcmp(p, expected, 64));
    } else {
        uint8_t expected[64] = {4, 0xF0};

        assert(m->expect_activation && i == 21);
        assert(!memcmp(p, expected, 64));
    }

    return i != m->fail_send;
}

static int receive_packet(void* ctx, uint8_t* p, size_t n) {
    Mock* m = ctx;
    int i = ++m->reads;
    assert(n == 64);
    if(i == m->fail_read)
        return -1;

    memset(p, 0, n);
    p[0] = 4;
    p[1] = i == 1 ? 0x13 : 2;
    p[3] = 1;
    if(i == 2) {
        uint32_t sum = m->bad_checksum;
        for(unsigned j = 0; j < sizeof(m->body); ++j)
            sum += m->body[j];

        for(unsigned j = 0; j < 4; ++j)
            p[4 + j] = (uint8_t)(sum >> (8 * j));
    }

    if(i == m->bad_reply)
        p[1] ^= 1;

    return i == m->short_reply ? 63 : 64;
}

static void pause_packet(void* ctx, unsigned us) {
    Mock* m = ctx;
    assert(us == (m->pauses == 0 ? 105000 : 15000));
    m->pauses++;
}

static int journal_packet(void* ctx, const char* e, int b) {
    Mock* m = ctx;
    int i = ++m->journals;
    if(i == 1) {
        assert(!strcmp(e, "setup-attempt"));
        assert(b == -1);
    } else if(i <= 19) {
        assert(!strcmp(e, "block-attempt"));
        assert(b == i - 2);
    } else if(i == 20) {
        assert(!strcmp(e, "checksum-attempt"));
        assert(b == -1);
    } else if(i == 21) {
        assert(!strcmp(e, "checksum-verified"));
        assert(b == -1);
    } else if(i == 22) {
        assert(m->expect_activation);
        assert(!strcmp(e, "activation-f0-attempt"));
        assert(b == -1);
    } else {
        assert(m->expect_activation && i == 23);
        assert(!strcmp(e, "activation-f0-host-completed"));
        assert(b == -1);
    }

    return i != m->fail_journal;
}

static int run(Mock* m, size_t size, int activate, C989WriteResult* captured) {
    for(unsigned i = 0; i < sizeof(m->body); ++i)
        m->body[i] = (uint8_t)(i * 17 + i / 64);

    m->expect_activation = activate;
    C989WriteIO io = {m, send_packet, receive_packet, pause_packet, journal_packet};

    C989WriteResult result;
    int ok =
        activate ? c989_lighting_upload(&io, m->body, size, &result) : c989_write_lighting(&io, m->body, size, &result);
    if(captured)
        *captured = result;

    if(ok)
        assert(m->pauses == (activate ? 24 : 23));

    if(ok)
        assert(result.stage == (activate ? 5 : 4) && result.last_attempted_block == 17);

    return ok;
}

int main(void) {
    C989WriteResult result;
    Mock m = {0};

    assert(run(&m, 1152, 0, &result));
    assert(m.sends == 20 && m.reads == 2);
    assert(!result.activation_attempted && !result.activation_host_completed);
    m = (Mock){0};

    assert(run(&m, 1152, 1, &result));
    assert(m.sends == 21 && m.reads == 2 && m.journals == 23);
    assert(result.activation_attempted && result.activation_host_completed);
    for(int i = 1; i <= 20; ++i) {
        m = (Mock){.fail_send = i};

        assert(!run(&m, 1152, 0, NULL));
        assert(m.sends == i);
    }

    for(int i = 1; i <= 21; ++i) {
        m = (Mock){.fail_journal = i};

        assert(!run(&m, 1152, 0, NULL));
        assert(m.sends == (i <= 20 ? i - 1 : 20));
    }

    for(int i = 1; i <= 2; ++i) {
        m = (Mock){.fail_read = i};

        assert(!run(&m, 1152, 0, NULL));
        assert(m.sends == (i == 1 ? 1 : 20));
        m = (Mock){.bad_reply = i};

        assert(!run(&m, 1152, 0, NULL));
        m = (Mock){.short_reply = i};

        assert(!run(&m, 1152, 0, NULL));
    }

    m = (Mock){.bad_checksum = 1};

    assert(!run(&m, 1152, 0, NULL));
    size_t sizes[] = {0, 64, 1151, 1153, 1280};

    for(unsigned i = 0; i < 5; ++i) {
        m = (Mock){0};

        assert(!run(&m, sizes[i], 1, NULL));
        assert(m.sends == 0 && m.journals == 0);
    }

    /* Activation is gated on verified checksum and is attempted exactly once. */
    for(int i = 1; i <= 20; ++i) {
        m = (Mock){.fail_send = i};

        assert(!run(&m, 1152, 1, &result));
        assert(m.sends == i && !result.activation_attempted);
    }

    m = (Mock){.bad_checksum = 1};

    assert(!run(&m, 1152, 1, &result));
    assert(m.sends == 20 && !result.activation_attempted);
    m = (Mock){.fail_journal = 22};

    assert(!run(&m, 1152, 1, &result));
    assert(m.sends == 20 && !result.activation_attempted);
    m = (Mock){.fail_send = 21};

    assert(!run(&m, 1152, 1, &result));
    assert(m.sends == 21 && result.activation_attempted && !result.activation_host_completed);
    m = (Mock){.fail_journal = 23};

    assert(!run(&m, 1152, 1, &result));
    assert(m.sends == 21 && result.activation_attempted && result.activation_host_completed);

    puts("PASS: verified upload ordering, optional single F0 after checksum only, activation ambiguity flags, all "
         "injected failures, invalid sizes; no replay.");
}
