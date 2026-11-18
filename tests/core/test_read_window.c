#include "../../core/include/C989Native.h"

/* Offline tests of the production native read-window selector. */
#include "../../core/ReadWindow.h"

#include <assert.h>
#include <stdio.h>

int main(void) {
    uint8_t reports[192] = {0}, expected[128] = {0};

    expected[0] = 7;
    memcpy(reports, expected, 128);
    assert(c989_read_window(reports, 128, 128, expected, 7) == 0);
    assert(c989_read_window(reports, 128, 128, NULL, 7) == 0);
    assert(c989_read_window(reports, 128, 128, expected, 8) == -1);
    memset(reports, 0xA5, 64);
    memcpy(reports + 64, expected, 128);
    assert(c989_read_window(reports, 192, 128, expected, 7) == 64);
    assert(c989_read_window(reports, 192, 128, NULL, 7) == 64);

    // An additive collision cannot stand in for the exact expected bytes.
    reports[64] = 0;
    reports[65] = 7;
    assert(c989_read_window(reports, 192, 128, expected, 7) == -1);

    // Two distinct but equal-checksum windows must be rejected without a baseline.
    memset(reports, 0, sizeof(reports));
    reports[0] = 7;
    reports[128] = 7;
    assert(c989_read_window(reports, 192, 128, NULL, 7) == -1);
    assert(c989_read_window(reports, 192, 128, expected, 7) == 0);
    memset(expected, 0, sizeof(expected));
    expected[64] = 7;
    assert(c989_read_window(reports, 192, 128, expected, 7) == 64);

    // Identical all-zero windows are unambiguous.
    memset(reports, 0, sizeof(reports));
    assert(c989_read_window(reports, 192, 128, NULL, 0) == 0);
    assert(c989_read_window(reports, 192, 128, NULL, 1) == -1);
    assert(c989_read_window(NULL, 128, 128, NULL, 0) == -1);
    assert(c989_read_window(reports, 127, 128, NULL, 0) == -1);
    assert(c989_read_window(reports, 129, 128, NULL, 0) == -1);
    assert(c989_read_window(reports, 128, 0, NULL, 0) == -1);
    puts("PASS: exact/cached read windows, checksum collisions, ambiguity, malformed lengths.");
}
