/* Select a checksum-verified 64-byte report window without copying it. */
#ifndef C989_READ_WINDOW_H
#define C989_READ_WINDOW_H
#include <stddef.h>
#include <stdint.h>
#include <string.h>

static inline uint32_t c989_byte_checksum(const uint8_t* bytes, size_t length) {
    uint32_t sum = 0;
    for(size_t i = 0; i < length; ++i)
        sum += bytes[i];

    return sum;
}

/* Returns a byte offset (0 or 64), or -1. Distinct windows with the same
 * checksum are ambiguous unless the expected image identifies the right one. */
static inline int c989_select_read_window(
    const uint8_t* reports, size_t length, size_t size, const uint8_t* expected, uint32_t checksum) {
    if(!reports || !size || size % 64 || (length != size && (length < 64 || length - 64 != size)))
        return -1;

    int extra = length != size;
    if(expected) {
        if(c989_byte_checksum(expected, size) != checksum)
            return -1;

        if(!memcmp(reports, expected, size))
            return 0;

        return extra && !memcmp(reports + 64, expected, size) ? 64 : -1;
    }

    int first = c989_byte_checksum(reports, size) == checksum;
    int shifted = extra && c989_byte_checksum(reports + 64, size) == checksum;
    if(first && shifted && memcmp(reports, reports + 64, size))
        return -1;

    return first ? 0 : shifted ? 64 : -1;
}
#endif
