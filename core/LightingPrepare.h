/* Pure lighting policy shared by the app and diagnostic writer. */
#ifndef C989_LIGHTING_PREPARE_H
#define C989_LIGHTING_PREPARE_H
#include <stddef.h>
#include <stdint.h>

static inline int c989_lighting_shape_valid(const uint8_t* p, size_t size) {
    if(!p || size != 1152)
        return 0;

    static const uint8_t tags[8] = {1, 2, 3, 4, 5, 6, 7, 0x80};

    for(unsigned record = 0; record < 8; ++record) {
        unsigned offset = record * 64;
        if(p[offset] != tags[record] || p[offset + 62] != 0xAA || p[offset + 63] != 0x55)
            return 0;
    }

    uint8_t selected = p[0x440];
    if(!((selected >= 1 && selected <= 15) || selected == 0x80) || p[0x47E] != 0xAA || p[0x47F] != 0x55)
        return 0;

    if(!((p[0x44E] == 0 && p[0x44F] == 0) || (p[0x44E] == 0xAA && p[0x44F] == 0x55)))
        return 0;

    for(unsigned slot = 0; slot < 144; ++slot) {
        uint8_t control = p[0x200 + slot * 4];
        if(control != 0 && control != 0x80)
            return 0;
    }

    return 1;
}

static inline int c989_lighting_changes_allowed(
    const uint8_t* before, size_t size, const uint8_t* after, size_t after_size, size_t* changed) {
    if(!c989_lighting_shape_valid(before, size) || !c989_lighting_shape_valid(after, after_size))
        return 0;

    size_t count = 0;
    for(unsigned offset = 0; offset < size; ++offset) {
        if(before[offset] == after[offset])
            continue;

        if(offset < 0x200) {
            unsigned field = offset % 64;
            if(!((field >= 1 && field <= 3) || (field >= 5 && field <= 7) || (field >= 57 && field <= 60)))
                return 0;
        } else if(offset >= 0x450)
            return 0;

        ++count;
    }

    if(!count)
        return 0;

    if(changed)
        *changed = count;

    return 1;
}
#endif
