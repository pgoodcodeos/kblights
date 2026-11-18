#include "../../core/include/C989Native.h"

#include "../../core/LightingPrepare.h"

#include <assert.h>
#include <stdio.h>
#include <string.h>

int main(void) {
    uint8_t before[1152] = {0}, after[1152];
    const uint8_t tags[] = {1, 2, 3, 4, 5, 6, 7, 0x80};

    for(unsigned i = 0; i < 8; ++i) {
        before[i * 64] = tags[i];
        before[i * 64 + 62] = 0xAA;
        before[i * 64 + 63] = 0x55;
    }

    before[0x440] = 8;
    before[0x47E] = 0xAA;
    before[0x47F] = 0x55;
    assert(c989_lighting_shape_valid(before, sizeof(before)));
    assert(!c989_lighting_changes_allowed(before, 1152, before, 1152, NULL));
    assert(!c989_lighting_shape_valid(NULL, 1152));
    assert(!c989_lighting_changes_allowed(before, 1151, before, 1152, NULL));
    for(unsigned offset = 0; offset < 1152; ++offset) {
        memcpy(after, before, sizeof(after));

        // Exercise every byte while retaining valid enable and compact trailer values.
        after[offset] ^= offset >= 0x200 && offset < 0x440 && offset % 4 == 0 ? 0x80 : 1;
        unsigned field = offset % 64;
        int range = offset < 0x200
                        ? ((field >= 1 && field <= 3) || (field >= 5 && field <= 7) || (field >= 57 && field <= 60))
                        : offset < 0x450;
        int shape = c989_lighting_shape_valid(after, sizeof(after));
        size_t changed = 0;
        int accepted = c989_lighting_changes_allowed(before, 1152, after, 1152, &changed);
        assert(accepted == (range && shape));
        assert(c989_lighting_validate(after, sizeof(after)) == shape);
        char error[256];
        assert((c989_lighting_prepare(before, 1152, after, 1152, error, sizeof(error)) == C989_OK) == accepted);
        if(accepted)
            assert(changed == 1);
    }

    memcpy(after, before, sizeof(after));
    after[0x441] = 255;
    after[0x442] = 128;
    after[0x44E] = 0xAA;
    after[0x44F] = 0x55;
    size_t changed = 0;
    assert(c989_lighting_changes_allowed(before, 1152, after, 1152, &changed));
    assert(changed == 4);
    after[0x450] = 1;
    assert(!c989_lighting_changes_allowed(before, 1152, after, 1152, NULL));
    puts("PASS: native lighting policy boundaries, structure, and change counts");
    return 0;
}
