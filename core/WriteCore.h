/* Transport-independent, single-attempt lighting transaction. No USB access here. */
#ifndef C989_WRITE_CORE_H
#define C989_WRITE_CORE_H
#include "include/C989Native.h"

#include <stddef.h>
#include <stdint.h>
#include <string.h>

static int c989_send(C989WriteIO* io, const uint8_t* p, const char* event, int block, unsigned delay_us) {
    if(!io->journal(io->context, event, block))
        return 0;

    int ok = io->send(io->context, p, 64);
    io->pause(io->context, delay_us);
    return ok;
}

static int c989_write_lighting(C989WriteIO* io, const uint8_t* body, size_t size, C989WriteResult* result) {
    if(!result)
        return 0;

    *result = (C989WriteResult){.last_attempted_block = -1};

    if(!io || !body || size != 1152 || !io->send || !io->receive || !io->pause || !io->journal)
        return 0;

    for(size_t i = 0; i < size; ++i)
        result->expected_checksum += body[i];

    uint8_t setup[64] = {4, 0x13}, reply[64] = {0};

    setup[8] = 18;
    result->stage = 1;

    /* Flash setup wait: ceil(1152 / 512) * 35 ms = 105 ms. */
    if(!c989_send(io, setup, "setup-attempt", -1, 105000))
        return 0;

    int length = io->receive(io->context, reply, 64);
    io->pause(io->context, 15000);
    if(length != 64 || reply[0] != 4 || reply[1] != 0x13 || reply[3] != 1)
        return 0;

    result->stage = 2;
    for(int block = 0; block < 18; ++block) {
        result->last_attempted_block = block;
        if(!c989_send(io, body + block * 64, "block-attempt", block, 15000))
            return 0;
    }

    /* v1.05 adds CommandDelayTime again after the final block delay. */
    io->pause(io->context, 15000);
    result->stage = 3;
    uint8_t checksum[64] = {4, 2};

    if(!c989_send(io, checksum, "checksum-attempt", -1, 15000))
        return 0;

    memset(reply, 0, sizeof(reply));
    length = io->receive(io->context, reply, 64);
    io->pause(io->context, 15000);
    if(length != 64 || reply[0] != 4 || reply[1] != 2 || reply[3] != 1)
        return 0;

    result->device_checksum =
        (uint32_t)reply[4] | (uint32_t)reply[5] << 8 | (uint32_t)reply[6] << 16 | (uint32_t)reply[7] << 24;
    if(result->device_checksum != result->expected_checksum)
        return 0;

    result->stage = 4;
    return io->journal(io->context, "checksum-verified", -1);
}

/* Normal vendor-compatible write path: upload, verify, then activate with one
 * exact 04 F0 report.  F0 is deliberately outside c989_write_lighting() so
 * historical no-F0 trials retain their original behavior.  Once the durable
 * activation-attempt journal exists, any failure is ambiguous and callers
 * must not replay the transaction automatically. */
static inline int c989_write_lighting_and_activate(
    C989WriteIO* io, const uint8_t* body, size_t size, C989WriteResult* result) {
    if(!c989_write_lighting(io, body, size, result))
        return 0;

    const uint8_t activation[64] = {4, 0xF0};

    result->stage = 5;
    if(!io->journal(io->context, "activation-f0-attempt", -1))
        return 0;

    result->activation_attempted = 1;
    int ok = io->send(io->context, activation, sizeof(activation));
    io->pause(io->context, 15000);
    if(!ok)
        return 0;

    result->activation_host_completed = 1;
    return io->journal(io->context, "activation-f0-host-completed", -1);
}
#endif
