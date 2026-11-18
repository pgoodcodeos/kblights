"""Lazy, process-local bindings to the shared C policy and upload cores.

Importing this module neither loads the library nor accesses the keyboard.
Build the library with scripts/build-linux.sh before launching the app.
"""

import ctypes as C
from functools import cache

from keyboardlights.resources import core_library


class AssignmentPatch(C.Structure):
    _fields_ = [("source", C.c_uint16), ("record", C.c_uint8 * 4)]


SEND = C.CFUNCTYPE(C.c_int, C.c_void_p, C.POINTER(C.c_uint8), C.c_size_t)
PAUSE = C.CFUNCTYPE(None, C.c_void_p, C.c_uint)
JOURNAL = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_char_p, C.c_int)


class WriteIO(C.Structure):
    _fields_ = [("context", C.c_void_p), ("send", SEND), ("receive", SEND), ("pause", PAUSE), ("journal", JOURNAL)]


class WriteResult(C.Structure):
    _fields_ = [("stage", C.c_int), ("last_attempted_block", C.c_int), ("expected_checksum", C.c_uint32),
        ("device_checksum", C.c_uint32), ("activation_attempted", C.c_int), ("activation_host_completed", C.c_int)]


@cache
def native_library():
    path = core_library()
    try:
        return _bind_library(C.CDLL(str(path)))
    except (OSError, AttributeError) as error:
        raise ValueError(
            "The shared keyboard core is incompatible or could not be loaded. "
            "Rebuild it with: sh scripts/build-linux.sh; or reinstall the application."
        ) from error


def _bind_library(library):
    """Resolve the complete ABI before any caller can access hardware."""
    byte_pointer = C.POINTER(C.c_uint8)
    upload_arguments = [C.POINTER(WriteIO), byte_pointer, C.c_size_t, C.POINTER(WriteResult)]
    signatures = {
        "c989_lighting_upload": upload_arguments,
        "c989_assignments_upload": upload_arguments,
        "c989_assignments_prepare": [byte_pointer, C.c_size_t, C.POINTER(AssignmentPatch), C.c_size_t, byte_pointer,
            C.c_size_t, C.c_char_p, C.c_size_t],
        "c989_lighting_validate": [byte_pointer, C.c_size_t],
        "c989_lighting_prepare": [byte_pointer, C.c_size_t, byte_pointer, C.c_size_t, C.c_char_p, C.c_size_t],
        "c989_read_window": [byte_pointer, C.c_size_t, C.c_size_t, byte_pointer, C.c_uint32],
    }
    for name, arguments in signatures.items():
        function = getattr(library, name)
        function.argtypes = arguments
        function.restype = C.c_int

    return library


def byte_buffer(data):
    """Own a stable byte buffer across a synchronous C call."""
    return (C.c_uint8 * len(data)).from_buffer_copy(data)


def validate_lighting(data):
    """Apply the same structural policy used by the macOS backend."""
    if not native_library().c989_lighting_validate(byte_buffer(data), len(data)):
        raise ValueError("Invalid stored lighting image")


def validate_lighting_changes(before, after):
    """Enforce the portable mutation boundary in addition to UI restrictions."""
    error = C.create_string_buffer(1024)
    if native_library().c989_lighting_prepare(byte_buffer(before), len(before), byte_buffer(after), len(after), error,
        len(error)):
        raise ValueError(error.value.decode())


def read_window(reports, size, checksum, expected=None):
    """Return a byte offset into a uniquely authenticated report window."""

    # Validate Python integers before ctypes can truncate them to size_t.
    if type(size) is not int or size <= 0 or size % 64 or len(reports) not in (size, size + 64):
        raise ValueError("Incomplete or invalid report window")

    if type(checksum) is not int or not 0 <= checksum <= 0xFFFFFFFF:
        raise ValueError("Invalid device checksum")

    if expected is not None and len(expected) != size:
        raise ValueError("Incomplete expected image")

    offset = native_library().c989_read_window(byte_buffer(reports), len(reports), size,
        None if expected is None else byte_buffer(expected), checksum)
    if offset < 0:
        raise ValueError("Device checksum or expected image does not identify an unambiguous report window")

    return offset
