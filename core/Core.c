/* The same portable ABI is compiled by SwiftPM and Linux packaging. */
#include "include/C989Native.h"

#include "AssignmentPrepare.h"
#include "AssignmentWriteCore.h"
#include "LightingPrepare.h"
#include "ReadWindow.h"

#include <stdio.h>

int c989_assignments_prepare(const uint8_t* baseline, size_t size, const C989AssignmentPatch* patches, size_t count,
    uint8_t* candidate, size_t output_size, char* error, size_t error_size) {
    return c989_prepare_assignments(baseline, size, patches, count, candidate, output_size, error, error_size);
}

int c989_lighting_prepare(const uint8_t* before, size_t size, const uint8_t* candidate, size_t candidate_size,
    char* error, size_t error_size) {
    if(c989_lighting_changes_allowed(before, size, candidate, candidate_size, NULL))
        return C989_OK;

    if(error && error_size)
        snprintf(error, error_size, "Invalid lighting candidate or mutation range; no write allowed.");

    return C989_STOPPED;
}

int c989_lighting_upload(C989WriteIO* io, const uint8_t* body, size_t size, C989WriteResult* result) {
    return c989_write_lighting_and_activate(io, body, size, result);
}

int c989_assignments_upload(C989WriteIO* io, const uint8_t* body, size_t size, C989WriteResult* result) {
    return c989_write_assignments_and_activate(io, body, size, result);
}

int c989_lighting_validate(const uint8_t* bytes, size_t size) {
    return c989_lighting_shape_valid(bytes, size);
}

int c989_read_window(const uint8_t* reports, size_t length, size_t size, const uint8_t* expected, uint32_t checksum) {
    return c989_select_read_window(reports, length, size, expected, checksum);
}
