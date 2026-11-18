/* Transport-independent, single-attempt 640-byte assignment trial only. */
#ifndef C989_ASSIGNMENT_WRITE_CORE_H
#define C989_ASSIGNMENT_WRITE_CORE_H
#include "WriteCore.h"

static inline int c989_write_assignments_and_activate(
    C989WriteIO* io, const uint8_t* body, size_t size, C989WriteResult* result) {
    if(!result)
        return 0;

    *result = (C989WriteResult){.last_attempted_block = -1};

    if(!io || !body || size != 640 || !io->send || !io->receive || !io->pause || !io->journal)
        return 0;

    for(size_t i = 0; i < size; ++i)
        result->expected_checksum += body[i];

    uint8_t setup[64] = {4, 0x11}, reply[64] = {0};

    setup[8] = 10;
    result->stage = 1;

    /* Vendor flash setup: ceil(640 / 512) * 35 ms = 70 ms. */
    if(!c989_send(io, setup, "setup-attempt", -1, 70000))
        return 0;

    int length = io->receive(io->context, reply, 64);
    io->pause(io->context, 15000);
    if(length != 64 || reply[0] != 4 || reply[1] != 0x11 || reply[3] != 1)
        return 0;

    result->stage = 2;
    for(int block = 0; block < 10; ++block) {
        result->last_attempted_block = block;
        if(!c989_send(io, body + block * 64, "block-attempt", block, 15000))
            return 0;
    }

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
    if(!io->journal(io->context, "checksum-verified", -1))
        return 0;

    const uint8_t activation[64] = {4, 0xF0};

    result->stage = 5;
    if(!io->journal(io->context, "activation-f0-attempt", -1))
        return 0;

    result->activation_attempted = 1;
    int ok = io->send(io->context, activation, 64);
    io->pause(io->context, 15000);
    if(!ok)
        return 0;

    result->activation_host_completed = 1;
    return io->journal(io->context, "activation-f0-host-completed", -1);
}
#endif
