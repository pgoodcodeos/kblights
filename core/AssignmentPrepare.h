/* Shared, independent assignment candidate policy. No device or file I/O. */
#ifndef C989_ASSIGNMENT_PREPARE_H
#define C989_ASSIGNMENT_PREPARE_H
#include "include/C989Native.h"

#include "AssignmentPolicy.h"

#include <stdio.h>

static inline int assignment_prepare_error(int code, const char* message, char* error, size_t size) {
    if(error && size)
        snprintf(error, size, "%s", message);

    return code;
}

static inline int c989_prepare_assignments(const uint8_t* baseline, size_t size, const C989AssignmentPatch* patches,
    size_t count, uint8_t* candidate, size_t output_size, char* error, size_t error_size) {
    if(!baseline || size != 640 || !candidate || output_size != 640 || !patches || count == 0 || count > 115)
        return assignment_prepare_error(C989_STOPPED,
            "A complete baseline and at least one physical-key "
            "change are required.",
            error, error_size);

    if(baseline[636] || baseline[637] ||
        !((baseline[638] == 0 && baseline[639] == 0) || (baseline[638] == 0xAA && baseline[639] == 0x55)))
        return assignment_prepare_error(
            C989_STOPPED, "The captured assignment validity word is not recognized.", error, error_size);

    if(baseline[638] == 0) {
        for(unsigned i = 0; i < 640; ++i)
            if(baseline[i])
                return assignment_prepare_error(C989_STOPPED,
                    "An inactive assignment table contains unknown "
                    "data. Read and inspect it before writing.",
                    error, error_size);
    }

    uint8_t seen[144] = {0};

    for(size_t i = 0; i < count; ++i) {
        unsigned source = patches[i].source;
        if(source >= 144 || !physicalSlots[source] || seen[source] || !assignment_record_valid(patches[i].record))
            return assignment_prepare_error(C989_STOPPED,
                "An assignment contains an invalid key, duplicate "
                "key, or unsupported output record.",
                error, error_size);

        seen[source] = 1;
    }

    uint8_t prepared[640];
    memcpy(prepared, baseline, 640);
    for(size_t i = 0; i < count; ++i)
        memcpy(prepared + patches[i].source * 4, patches[i].record, 4);

    prepared[638] = 0xAA;
    prepared[639] = 0x55;
    if(!memcmp(prepared, baseline, 640))
        return assignment_prepare_error(
            C989_STOPPED, "These assignments already match the keyboard; no write is needed.", error, error_size);

    memcpy(candidate, prepared, 640);
    return C989_OK;
}

#endif
