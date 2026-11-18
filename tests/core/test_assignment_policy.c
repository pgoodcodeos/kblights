/* Exercise the shared native policy without the Python/Swift validation layer. */
#include "../../core/AssignmentPrepare.h"

#include <assert.h>

int main(void) {
    uint8_t before[640] = {0}, after[640], expected[640] = {0};

    char error[1024];
    C989AssignmentPatch patches[2] = {{39, {2, 8, 6, 0}}, {39, {5, 3, 0, 0}}};

    assert(c989_assignments_prepare(before, 640, patches, 1, after, 640, error, sizeof(error)) == C989_OK);
    expected[156] = 2;
    expected[157] = 8;
    expected[158] = 6;
    expected[638] = 0xAA;
    expected[639] = 0x55;
    assert(!memcmp(expected, after, 640));
    assert(c989_assignments_prepare(after, 640, patches, 1, expected, 640, error, sizeof(error)) == C989_STOPPED);
    assert(c989_assignments_prepare(before, 640, patches, 2, after, 640, error, sizeof(error)) == C989_STOPPED);
    assert(c989_assignments_prepare(before, 640, patches, 0, after, 640, error, sizeof(error)) == C989_STOPPED);
    assert(c989_assignments_prepare(before, 639, patches, 1, after, 640, error, sizeof(error)) == C989_STOPPED);
    assert(c989_assignments_prepare(before, 640, patches, 1, after, 639, error, sizeof(error)) == C989_STOPPED);
    for(unsigned source = 0; source < 160; ++source) {
        patches[0].source = source;
        int code = c989_assignments_prepare(before, 640, patches, 1, after, 640, error, sizeof(error));
        assert(code == ((source < 144 && physicalSlots[source]) ? C989_OK : C989_STOPPED));
    }

    patches[0].source = 39;
    patches[0].record[0] = 4; /* Unrecognized action. */
    assert(c989_assignments_prepare(before, 640, patches, 1, after, 640, error, sizeof(error)) == C989_STOPPED);
    patches[0].record[0] = 2;
    before[0] = 1; /* Unknown bytes in an inactive table. */
    assert(c989_assignments_prepare(before, 640, patches, 1, after, 640, error, sizeof(error)) == C989_STOPPED);
    before[638] = 0xAA;
    before[639] = 0x55;
    assert(c989_assignments_prepare(before, 640, patches, 1, after, 640, error, sizeof(error)) == C989_OK);
    assert(after[0] == 1); /* Reserved records remain unchanged. */
    before[636] = 1;
    assert(c989_assignments_prepare(before, 640, patches, 1, after, 640, error, sizeof(error)) == C989_STOPPED);
    puts("PASS: shared native assignment policy, unknown/inactive tables, physical slots, duplicates, no-ops, and "
         "immutable reserved bytes.");
}
