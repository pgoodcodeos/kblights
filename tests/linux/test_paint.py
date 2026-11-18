"""Unified paint workflow checks; no GTK or device access."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import keyboardlights.lighting.service as lighting
from keyboardlights.lighting.editor import LightingEditor
from keyboardlights.ui.presentation import COLOR_PRESETS, key_lighting_description
from support import baseline_image


class PaintTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.service = lighting.LightingService(self.directory)
        self.service.baseline = baseline_image()
        self.service.identity = {"hidraw": "/dev/not-opened"}
        self.editor = LightingEditor(self.service, history_path=self.directory / "last-color.txt")

    def test_pointer_starts_selected_and_does_not_stage_or_invalidate_lighting(self):
        original = self.editor.export_draft()
        self.assertTrue(self.editor.pointer_selected)
        self.assertFalse(self.editor.paint_clicked_key(39))
        self.assertEqual(self.editor.export_draft(), original)
        self.editor.select_brush("#123456")
        self.assertFalse(self.editor.pointer_selected)
        self.assertTrue(self.editor.paint_clicked_key(39))
        review = self.editor.prepare()
        draft = self.editor.export_draft()
        self.editor.select_pointer()
        self.assertFalse(self.editor.paint_clicked_key(56))
        self.assertEqual(self.editor.export_draft(), draft)
        self.assertIs(self.editor.review, review)

    def test_animation_forces_pointer_and_returning_to_solid_keeps_it_selected(self):
        self.editor.select_brush("#FF0000")
        self.assertFalse(self.editor.pointer_selected)
        self.editor.select_mode(11)
        self.assertTrue(self.editor.pointer_selected)
        self.editor.select_brush("#000000")
        self.assertTrue(self.editor.pointer_selected)
        self.editor.select_mode(128)
        self.assertTrue(self.editor.pointer_selected)
        self.editor.select_brush("#000000")
        self.assertFalse(self.editor.pointer_selected)
        self.editor.paint_clicked_key(39)
        self.assertFalse(self.editor.draft["slots"][39]["enabled"])

    def test_per_key_revert_restores_capture_without_touching_other_keys_or_brush(self):
        self.editor.select_mode(128)
        before = self.editor.current_keyboard_preview()
        self.editor.select_brush("#123456")
        self.editor.clear_paint()
        self.editor.paint_key(39)
        self.editor.paint_key(56)
        self.editor.review = object()
        self.editor.revert_key(39)
        self.assertFalse(self.editor.has_key_change(39))
        self.assertTrue(self.editor.has_key_change(56))
        self.assertNotIn(39, self.editor.unpainted)
        self.assertIn(57, self.editor.unpainted)
        self.assertEqual(self.editor.brush_color, "#123456")
        self.assertEqual(self.editor.draft["slots"][56]["color"], "#123456")
        self.assertEqual(self.editor.current_keyboard_preview(), before)
        self.assertIsNone(self.editor.review)

    def test_key_status_distinguishes_animation_blanks_and_off(self):
        self.editor.select_mode(128)
        self.editor.clear_paint()
        self.assertEqual(key_lighting_description(self.editor.preview_draft(), 39, unpainted=self.editor.unpainted),
            "Unpainted")
        self.editor.select_brush("#000000")
        self.editor.paint_key(39)
        self.assertIn("Off", key_lighting_description(self.editor.preview_draft(), 39))
        self.editor.select_mode(11)
        before = self.editor.export_draft()
        self.editor.revert_key(39)
        self.assertEqual(self.editor.export_draft(), before)
        self.assertFalse(self.editor.has_key_change(39))
        self.assertIn("animation on all keys",
            key_lighting_description(self.editor.preview_draft(), 39, unpainted=self.editor.unpainted))

    def test_animation_inspection_exposes_speed_brightness_and_custom_color_changes(self):
        self.editor.select_mode(11)
        before = key_lighting_description(self.editor.preview_draft(), 39)
        self.editor.change(speed=3)
        after = key_lighting_description(self.editor.preview_draft(), 39)
        self.assertNotEqual(after, before)
        self.assertIn("speed 3", after)
        self.assertIn("brightness", after)
        self.assertIn("Full color", after)
        self.editor.change(style="animation", mode=8, color="#123456", generated=False)
        self.assertIn("#123456", key_lighting_description(self.editor.preview_draft(), 39))

    def test_brush_is_local_and_preset_colors_are_normalized(self):
        original = self.editor.export_draft()
        for _, color in COLOR_PRESETS:
            self.editor.select_brush(color.lower())
            self.assertEqual(self.editor.brush_color, color)

        self.assertEqual(self.editor.export_draft(), original)
        self.assertFalse(self.editor.has_pending_edits())
        self.assertFalse(self.editor.history_path.exists())

    def test_clear_fill_off_and_all_preserve_device_indices_and_reserved_slots(self):
        before = self.service.baseline
        with patch.object(lighting.reader, "open_keyboard") as opened:
            self.editor.clear_paint()
            self.assertEqual(self.editor.unpainted, lighting.PHYSICAL)
            self.assertTrue(self.editor.has_pending_edits())
            self.assertFalse(self.editor.has_changes())
            for operation in (self.editor.prepare, self.editor.export_draft):
                with self.assertRaisesRegex(ValueError, "blank key"):
                    operation()

            self.editor.select_brush("#123456")
            self.editor.paint_key(39)
            self.editor.select_brush("#000000")
            self.editor.paint_key(56)
            self.assertEqual(self.editor.last_used_color, "#123456")
            self.editor.select_brush("#ABCDEF")
            self.editor.fill_paint()
            self.assertFalse(self.editor.unpainted)
            slots = self.editor.draft["slots"]
            self.assertEqual(slots[39]["color"], "#123456")
            self.assertFalse(slots[56]["enabled"])
            self.assertEqual(slots[57]["color"], "#ABCDEF")
            self.editor.prepare()
            candidate = self.service.pending[2]
            for index in set(range(144)) - lighting.PHYSICAL:
                offset = 0x200 + index * 4
                self.assertEqual(candidate[offset : offset + 4], before[offset : offset + 4])

            self.editor.fill_paint(overwriting=True)
            self.assertIsNone(self.editor.review)
            self.assertTrue(
                all(slots[index]["enabled"] and slots[index]["color"] == "#ABCDEF" for index in lighting.PHYSICAL)
            )
            opened.assert_not_called()

    def test_blank_drafts_remain_pending_until_reset_or_import(self):
        original = self.editor.export_draft()
        self.editor.clear_paint()
        self.assertTrue(self.editor.has_pending_edits())
        self.editor.reset()
        self.assertFalse(self.editor.unpainted)
        self.assertEqual(self.editor.export_draft(), original)
        self.editor.clear_paint()
        self.editor.import_draft(original)
        self.assertFalse(self.editor.unpainted)

    def test_last_used_color_survives_reopening_without_recording_off(self):
        self.editor.select_brush("#123456")
        self.editor.paint_key(39)
        self.editor.select_brush("#000000")
        self.editor.paint_key(56)
        reopened = LightingEditor(self.service, history_path=self.editor.history_path)
        self.assertEqual(reopened.last_used_color, "#123456")
        self.assertEqual(self.editor.history_path.read_text(), "#123456\n")

    def test_animation_controls_keep_static_blanks_and_existing_profile_settings(self):
        self.editor.clear_paint()
        self.editor.select_mode(11)
        self.assertFalse(self.editor.has_unpainted())
        self.assertTrue(self.editor.draft["generated"])
        self.assertEqual(self.editor.draft["brightness"], 16)
        self.editor.change(speed=7)
        self.editor.prepare()
        self.assertIsNotNone(self.editor.review)
        self.editor.select_mode(128)
        self.assertTrue(self.editor.has_unpainted())
        self.assertIsNone(self.editor.review)

    def test_incomplete_draft_cannot_apply_even_with_an_old_review_restored(self):
        self.editor.select_brush("#123456")
        self.editor.fill_paint(overwriting=True)
        review = self.editor.prepare()
        self.editor.clear_paint()
        self.editor.review = review
        with patch.object(lighting, "execute") as execute:
            with self.assertRaisesRegex(ValueError, "blank key"):
                self.editor.apply(confirmed=True)

            execute.assert_not_called()

    def test_preference_failure_does_not_stop_painting(self):
        self.editor.history_path = self.directory
        self.editor.select_brush("#123456")
        with self.assertLogs(level="WARNING"):
            self.editor.paint_key(39)

        self.assertEqual(self.editor.last_used_color, "#123456")
        self.assertEqual(self.editor.draft["slots"][39]["color"], "#123456")
        self.assertEqual(list(self.directory.glob(".paint-*")), [])

    def test_mode_round_trip_keeps_static_brightness_and_legacy_solid_colors(self):
        image = bytearray(self.service.baseline)
        image[0x440] = 1
        self.service.baseline = bytes(image)
        self.editor.adopt(self.service.state())
        color = self.editor.draft["color"]
        self.editor.change(brightness=8)
        self.editor.select_mode(11)
        self.assertEqual(self.editor.draft["brightness"], 16)
        self.editor.select_mode(128)
        self.assertEqual(self.editor.draft["brightness"], 8)
        self.assertTrue(all(self.editor.draft["slots"][index]["color"] == color for index in lighting.PHYSICAL))

    def test_hidden_blank_draft_still_counts_as_pending_edits(self):
        self.service.baseline = lighting.candidate(self.service.baseline,
            dict(style="animation", mode=11, brightness=16, speed=12, generated=True, direction=0))
        self.editor.adopt(self.service.state())
        self.editor.select_mode(128)
        self.editor.clear_paint()
        self.editor.select_mode(11)
        self.assertFalse(self.editor.has_changes())
        self.assertTrue(self.editor.has_pending_edits())
        self.editor.select_mode(128)
        self.assertEqual(self.editor.unpainted, lighting.PHYSICAL)

    def test_inactive_and_empty_fill_do_not_change_the_review_or_mode(self):
        self.editor.select_mode(11)
        review = self.editor.prepare()
        original = self.editor.export_draft()
        self.editor.fill_paint()
        self.editor.clear_paint()
        self.assertEqual(self.editor.export_draft(), original)
        self.assertIs(self.editor.review, review)
        self.editor.select_mode(128)
        self.editor.select_brush("#123456")
        self.editor.fill_paint(overwriting=True)
        review = self.editor.prepare()
        original = self.editor.export_draft()
        self.editor.fill_paint()
        self.assertEqual(self.editor.export_draft(), original)
        self.assertIs(self.editor.review, review)

    def test_bucket_fills_only_the_connected_matching_region(self):
        editor = self.editor
        before = self.service.baseline
        editor.select_brush("#123456")
        for key in (39, 40, 57, 109):
            editor.paint_key(key)

        editor.prepare()
        reserved = {index: dict(slot) for index,
            slot in enumerate(editor.draft["slots"]) if index not in lighting.PHYSICAL}
        editor.select_bucket("#ABCDEF")
        with patch.object(lighting.reader, "open_keyboard") as opened:
            self.assertTrue(editor.paint_clicked_key(39))
            opened.assert_not_called()

        self.assertIsNone(editor.review)
        for key in (39, 40, 57):
            self.assertEqual(editor.draft["slots"][key]["color"], "#ABCDEF")

        self.assertEqual(editor.draft["slots"][109]["color"], "#123456")
        self.assertEqual(editor.draft["slots"][56]["color"], "#00FEFF")
        self.assertEqual(reserved, {index: editor.draft["slots"][index] for index in reserved})
        self.assertEqual(self.service.baseline, before)
        self.assertEqual(editor.brush_color, "#123456")

    def test_bucket_does_not_cross_the_split_or_fill_diagonal_only_matches(self):
        editor = self.editor
        editor.select_brush("#123456")
        editor.fill_paint(overwriting=True)
        editor.select_bucket("#ABCDEF")
        editor.paint_clicked_key(39)
        self.assertEqual(editor.draft["slots"][56]["color"], "#ABCDEF")
        self.assertEqual(editor.draft["slots"][109]["color"], "#123456")
        editor.select_brush("#00FFFF")
        editor.fill_paint(overwriting=True)
        editor.select_brush("#FF0000")
        editor.paint_key(39)
        editor.paint_key(58)
        editor.select_bucket("#ABCDEF")
        editor.paint_clicked_key(39)
        self.assertEqual(editor.draft["slots"][39]["color"], "#ABCDEF")
        self.assertEqual(editor.draft["slots"][58]["color"], "#FF0000")

    def test_bucket_distinguishes_blanks_from_off_and_respects_color_barriers(self):
        editor = self.editor
        editor.clear_paint()
        editor.select_brush("#123456")
        editor.paint_key(39)
        editor.select_brush("#000000")
        editor.paint_key(56)
        editor.select_bucket("#ABCDEF")
        editor.paint_clicked_key(40)
        self.assertEqual(editor.draft["slots"][40]["color"], "#ABCDEF")
        self.assertNotIn(40, editor.unpainted)
        self.assertEqual(editor.draft["slots"][39]["color"], "#123456")
        self.assertFalse(editor.draft["slots"][56]["enabled"])
        self.assertIn(109, editor.unpainted)
        with self.assertRaisesRegex(ValueError, "blank key"):
            editor.export_draft()

        editor.paint_clicked_key(56)
        self.assertTrue(editor.draft["slots"][56]["enabled"])
        self.assertEqual(editor.draft["slots"][56]["color"], "#ABCDEF")
        self.assertEqual(editor.draft["slots"][39]["color"], "#123456")

    def test_bucket_off_ignores_stale_rgb_and_does_not_recolor_other_regions(self):
        editor = self.editor
        editor.select_brush("#FF0000")
        editor.paint_key(39)
        editor.select_brush("#0000FF")
        editor.paint_key(40)
        editor.select_brush("#000000")
        editor.paint_key(39)
        editor.paint_key(40)
        editor.select_bucket("#ABCDEF")
        editor.paint_clicked_key(39)
        self.assertEqual(editor.draft["slots"][40]["color"], "#ABCDEF")
        editor.select_bucket("#000000")
        editor.paint_clicked_key(40)
        self.assertFalse(editor.draft["slots"][39]["enabled"])
        self.assertFalse(editor.draft["slots"][40]["enabled"])
        self.assertTrue(editor.draft["slots"][57]["enabled"])
        self.assertEqual(editor.last_bucket_color, "#ABCDEF")

    def test_paint_tools_remember_separate_colors_across_reopening(self):
        editor = self.editor
        editor.select_brush("#123456")
        editor.paint_clicked_key(39)
        editor.select_bucket("#ABCDEF")
        editor.paint_clicked_key(39)
        editor.select_pointer()
        self.assertFalse(editor.paint_clicked_key(40))
        editor.select_brush("#123456")
        self.assertEqual(editor.bucket_color, "#ABCDEF")
        reopened = LightingEditor(self.service, history_path=editor.history_path)
        self.assertEqual(reopened.last_used_color, "#123456")
        self.assertEqual(reopened.last_bucket_color, "#ABCDEF")
        self.assertEqual(reopened.bucket_color, "#ABCDEF")
        self.assertEqual(editor.bucket_history_path.read_text(), "#ABCDEF\n")

    def test_animation_forces_bucket_back_to_pointer_without_discarding_its_color(self):
        editor = self.editor
        editor.select_bucket("#ABCDEF")
        editor.select_mode(11)
        before = editor.export_draft()
        editor.flood_fill(39)
        self.assertFalse(editor.paint_clicked_key(39))
        self.assertEqual(editor.export_draft(), before)
        editor.select_mode(128)
        self.assertTrue(editor.pointer_selected)
        self.assertEqual(editor.bucket_color, "#ABCDEF")

    def test_history_rejects_fifo_without_stream_opening_it(self):
        path = self.directory / "color-fifo"
        os.mkfifo(path)
        with patch.object(Path, "open", side_effect=AssertionError("Do not stream-open a FIFO")):
            editor = LightingEditor(self.service, history_path=path)

        self.assertEqual(editor.last_used_color, "#00FFFF")

    def test_inactive_paint_and_brightness_edits_are_protected_even_when_animation_is_unchanged(self):
        self.service.baseline = lighting.candidate(self.service.baseline,
            dict(style="animation", mode=11, brightness=16, speed=12, generated=True, direction=0))
        for edit in ("paint", "brightness"):
            with self.subTest(edit=edit):
                self.editor.adopt(self.service.state())
                self.editor.select_mode(128)
                if edit == "paint":
                    self.editor.select_brush("#123456")
                    self.editor.paint_key(39)
                else:
                    self.editor.change(brightness=8)

                self.editor.select_mode(11)
                self.assertFalse(self.editor.has_changes())
                self.assertTrue(self.editor.has_pending_edits())
                self.editor.select_mode(128)
                if edit == "paint":
                    self.assertEqual(self.editor.draft["slots"][39]["color"], "#123456")
                else:
                    self.assertEqual(self.editor.draft["brightness"], 8)

                self.editor.reset()
                self.assertFalse(self.editor.has_pending_edits())
