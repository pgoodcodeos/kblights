#ifndef C989_NATIVE_API_H
#define C989_NATIVE_API_H
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    uint16_t source;
    uint8_t record[4];
} C989AssignmentPatch;

enum {
    C989_OK = 0,
    C989_STOPPED = 1,
    C989_UPLOAD_UNCERTAIN = 2,
    C989_ACTIVATION_UNCERTAIN = 3,
    C989_BUSY = 4,
    C989_EVIDENCE_INCOMPLETE = 5
};

/* Pure offline policy check: C989_OK on success, status code on failure. */
int c989_assignments_prepare(const uint8_t* baseline, size_t baseline_size, const C989AssignmentPatch* patches,
    size_t count, uint8_t* candidate, size_t candidate_size, char* error, size_t error_size);

/* Pure offline lighting policy check. No file or device access. */
int c989_lighting_prepare(const uint8_t* baseline, size_t baseline_size, const uint8_t* candidate,
    size_t candidate_size, char* error, size_t error_size);

/* Synchronous callbacks; context and buffers live until upload returns.
 * send/journal: nonzero success, zero failure. receive: exact byte count or
 * negative failure. pause: microseconds. No platform APIs or file access here. */
typedef struct {
    void* context;
    int (*send)(void*, const uint8_t*, size_t);
    int (*receive)(void*, uint8_t*, size_t);
    void (*pause)(void*, unsigned);
    int (*journal)(void*, const char*, int);
} C989WriteIO;

typedef struct {
    int stage;                /* 0 input, 1 setup, 2 body, 3 checksum, 4 verified, 5 activation */
    int last_attempted_block; /* -1 before body; zero-based afterward */
    uint32_t expected_checksum, device_checksum;
    int activation_attempted, activation_host_completed;
} C989WriteResult;

/* Upload and activate once: nonzero success, zero failure. On failure, inspect
 * result; never replay automatically. Validate the candidate before calling. */
int c989_lighting_upload(C989WriteIO* io, const uint8_t* body, size_t size, C989WriteResult* result);
int c989_assignments_upload(C989WriteIO* io, const uint8_t* body, size_t size, C989WriteResult* result);

/* Structural validation: nonzero valid, zero invalid. */
int c989_lighting_validate(const uint8_t* bytes, size_t size);

/* expected is NULL or points to size bytes. Returns byte offset 0/64, or -1. */
int c989_read_window(const uint8_t* reports, size_t length, size_t size, const uint8_t* expected, uint32_t checksum);
#ifdef __cplusplus
}
#endif
#endif
