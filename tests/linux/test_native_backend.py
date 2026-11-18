"""Reject invalid foreign-call arguments and incompatible libraries before I/O."""

import ctypes as C
import unittest
from pathlib import Path
from unittest.mock import patch

import keyboardlights.device.capture as reader
import keyboardlights.device.core as backend


class NativeBoundaryTests(unittest.TestCase):
    def test_invalid_read_sizes_never_reach_ctypes(self):
        # ctypes otherwise wraps this requested length to 128 on a 64-bit host.
        oversized = (1 << (C.sizeof(C.c_size_t) * 8)) + 128
        with patch.object(backend, "native_library") as library:
            for size in (0, -64, 1, 127, True, 128.0, oversized):
                with self.subTest(size=size), self.assertRaises(ValueError):
                    backend.read_window(bytes(192), size, 0)

            with self.assertRaises(ValueError):
                reader.select_window(bytes(192), oversized // 64, 0)

            library.assert_not_called()

    def test_invalid_checksums_and_expected_buffers_never_reach_ctypes(self):
        with patch.object(backend, "native_library") as library:
            for checksum in (-1, 1 << 32, True, 0.0):
                with self.subTest(checksum=checksum), self.assertRaises(ValueError):
                    backend.read_window(bytes(192), 128, checksum)

            for expected in (bytes(127), bytes(129)):
                with self.assertRaises(ValueError):
                    backend.read_window(bytes(192), 128, 0, expected)

            library.assert_not_called()

    def test_loader_reports_missing_library_and_stale_abi_as_actionable_errors(self):
        for error in (OSError("wrong architecture"), AttributeError("old core has no upload symbol")):
            with (self.subTest(error=error),
                patch.object(backend, "core_library", return_value=Path("/not-opened/lighting_core.so")),
                patch.object(backend.C, "CDLL", side_effect=error)):
                # Bypass the successful process cache without invalidating other tests' bindings.
                with self.assertRaisesRegex(ValueError, "Rebuild it") as caught:
                    backend.native_library.__wrapped__()

                self.assertIs(caught.exception.__cause__, error)

    def test_loader_resolves_all_symbols_before_returning_library(self):
        with (patch.object(backend, "core_library", return_value=Path("/not-opened/lighting_core.so")),
            patch.object(backend.C, "CDLL", return_value=object())):
            with self.assertRaisesRegex(ValueError, "incompatible"):
                backend.native_library.__wrapped__()


if __name__ == "__main__":
    unittest.main()
