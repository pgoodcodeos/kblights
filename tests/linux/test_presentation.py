"""Physical keyboard geometry and error guidance without GTK or device access."""

import errno
import unittest

import keyboardlights.lighting.service as lighting
from keyboardlights.ui.presentation import KEY_POSITIONS, error_message


class PresentationTests(unittest.TestCase):
    def test_keyboard_display_covers_every_physical_key_without_overlap(self):
        self.assertEqual(set(KEY_POSITIONS), lighting.PHYSICAL)
        occupied = set()
        for index, (left, top, width, height) in KEY_POSITIONS.items():
            with self.subTest(index=index):
                self.assertGreater(width, 0)
                self.assertGreater(height, 0)
                cells = {(column, row) for column in range(left, left + width) for row in range(top, top + height)}
                self.assertFalse(occupied & cells)
                occupied.update(cells)

    def test_navigation_and_number_pad_follow_physical_groups(self):
        positions = {key["name"]: KEY_POSITIONS[key["led_index"]] for key in lighting.KEYS}
        columns = (("Print", "Insert", "Delete"), ("Scroll Lock", "Home", "End"), ("Pause", "Page Up", "Page Down"))
        for column in columns:
            with self.subTest(column=column):
                self.assertEqual(len({positions[name][0] for name in column}), 1)
                self.assertEqual([positions[name][1] for name in column], [0, 1, 2])

        self.assertEqual(positions["Up"][0], positions["Down"][0])
        self.assertLess(positions["Up"][1], positions["Down"][1])
        self.assertEqual(len({positions[name][1] for name in ("Left", "Down", "Right")}), 1)
        self.assertLess(positions["Left"][0], positions["Down"][0])
        self.assertLess(positions["Down"][0], positions["Right"][0])
        self.assertLess(positions["Right"][0], positions["Numpad 0"][0])
        self.assertEqual(positions["Numpad 0"][2], 2 * positions["Numpad 1"][2])
        self.assertEqual(positions["Numpad +"][3], 2)
        self.assertEqual(positions["Numpad Enter"][3], 2)

    def test_macro_columns_run_from_m1_to_m5_and_m6_to_m10(self):
        positions = {key["name"]: KEY_POSITIONS[key["led_index"]] for key in lighting.KEYS}
        for number in range(1, 6):
            left = positions[f"M{number}"]
            right = positions[f"M{number + 5}"]
            self.assertEqual(left[1], number)
            self.assertEqual(right[1], number)
            self.assertEqual(left[0], positions["M1"][0])
            self.assertEqual(right[0], positions["M6"][0])
            self.assertLess(left[0], right[0])
            self.assertLess(right[0], positions["Tab"][0])

    def test_keyboard_permission_errors_explain_reconnection_and_access_setup(self):
        for path in ("/dev/hidraw0", "/dev/hidraw22"):
            with self.subTest(path=path):
                message = error_message(PermissionError(errno.EACCES, "Permission denied", path))
                self.assertIn("Unplug and reconnect", message)
                self.assertIn("Refresh", message)
                self.assertIn("device access rule", message)
                self.assertIn("normal desktop user", message)

    def test_other_errors_keep_their_original_details(self):
        for error in (PermissionError(errno.EACCES, "Permission denied", "/tmp/profile.json"),
            PermissionError("Cannot inspect keyboard"),
            PermissionError(errno.EACCES, "Permission denied", "/dev/hidraw2/profile.json"),
            OSError(errno.EIO, "Input/output error", "/dev/hidraw2"), ValueError("Invalid profile")):
            with self.subTest(error=error):
                self.assertEqual(error_message(error), str(error))

    def test_uncertain_write_guidance_is_preserved(self):
        cause = PermissionError(errno.EACCES, "Permission denied", "/dev/hidraw2")
        error = lighting.UncertainWriteError("/tmp/write-evidence", cause)
        self.assertEqual(error_message(error), str(error))
        self.assertIn("Do not retry", error_message(error))
        self.assertIn("/tmp/write-evidence", error_message(error))
