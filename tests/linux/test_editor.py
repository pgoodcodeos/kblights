"""Native editor workflow tests without GTK, display, or hardware access."""

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import keyboardlights.lighting.service as lighting
from keyboardlights.lighting.editor import LightingEditor
from support import baseline_image


class EditorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.service = lighting.LightingService(Path(self.temporary.name))
        self.service.baseline = baseline_image()
        self.service.identity = {"hidraw": "/dev/not-opened"}
        self.editor = LightingEditor(self.service)

    def test_current_keyboard_preview_remains_verified_while_editing(self):
        current = self.editor.current_keyboard_preview()
        self.editor.change(color="#FF00FF")
        self.assertEqual(self.editor.current_keyboard_preview(), current)
        current["slots"][90]["color"] = "#123456"
        self.assertNotEqual(self.editor.current_keyboard_preview()["slots"][90]["color"], "#123456")
        with patch.object(lighting, "execute", return_value={"lock_indicators": 5}):
            self.editor.prepare()
            self.editor.apply(confirmed=True)

        self.assertEqual(self.editor.current_keyboard_preview()["color"], "#FF00FF")

    def test_initial_cyan_preserves_exact_rgb_and_live_baseline(self):
        self.assertEqual(self.editor.draft["style"], "solid")
        self.assertEqual(self.editor.draft["color"], "#00FEFF")
        before = self.service.baseline
        self.editor.selection = {39, 0}
        self.editor.change(color="#123456", style="perKey")
        self.editor.paint()
        self.assertEqual(self.editor.draft["slots"][39]["color"], "#123456")
        self.assertEqual(self.editor.draft["slots"][0], self.editor.state["slots"][0])
        self.assertEqual(self.service.baseline, before)

    def test_apply_requires_review_and_confirmation_then_is_single_use(self):
        with patch.object(lighting, "execute", return_value={"lock_indicators": 5}) as execute:
            with self.assertRaises(ValueError):
                self.editor.apply(confirmed=True)

            self.editor.change(color="#FF00FF")
            self.assertTrue(self.editor.has_changes())
            self.editor.prepare()
            with self.assertRaises(ValueError):
                self.editor.apply(confirmed=False)

            execute.assert_not_called()
            self.editor.apply(confirmed=True)
            execute.assert_called_once()
            self.assertEqual(self.editor.draft["color"], "#FF00FF")
            self.assertFalse(self.editor.has_changes())
            with self.assertRaises(ValueError):
                self.editor.apply(confirmed=True)

    def test_edit_and_draft_import_invalidate_review(self):
        self.editor.change(color="#FF00FF")
        self.editor.prepare()
        self.editor.change(brightness=8)
        self.assertIsNone(self.editor.review)
        self.editor.prepare()
        saved = self.editor.export_draft()
        self.editor.import_draft(saved)
        self.assertIsNone(self.editor.review)
        self.assertEqual(self.editor.draft["brightness"], 8)

    def test_draft_round_trip_and_bad_inputs_preserve_previous_draft(self):
        self.editor.change(style="animation", mode=11, direction=1, generated=False, color="#123456")
        saved = self.editor.export_draft()
        self.editor.change(color="#FFFFFF")
        self.editor.import_draft(saved)
        self.assertEqual(self.editor.draft["color"], "#123456")
        self.assertEqual(self.editor.draft["direction"], 1)
        original = deepcopy(self.editor.draft)
        for key, value in (("mode", 1), ("color", "bad"), ("speed", 1), ("generated", "false"), ("slots", [])):
            draft = json.loads(saved)
            draft["settings"][key] = value
            with self.assertRaises(ValueError):
                self.editor.import_draft(json.dumps(draft))

            self.assertEqual(self.editor.draft, original)

    def test_offline_capture_cannot_read_plan_or_apply(self):
        editor = LightingEditor(capture=baseline_image())
        editor.change(color="#FF00FF")
        with patch.object(lighting.reader, "open_keyboard") as opened:
            for operation in (editor.read, editor.prepare, lambda: editor.apply(confirmed=True)):
                with self.assertRaises(ValueError):
                    operation()

            opened.assert_not_called()

        self.assertIn("c989-lighting-draft-v1", editor.export_draft())

    def test_failed_apply_discards_baseline_and_review(self):
        self.editor.change(color="#FF00FF")
        self.editor.prepare()
        with patch.object(lighting, "execute", side_effect=OSError("injected")) as execute:
            with self.assertRaises(OSError):
                self.editor.apply(confirmed=True)

            with self.assertRaises(ValueError):
                self.editor.apply(confirmed=True)

            execute.assert_called_once()

        self.assertFalse(self.editor.state["available"])
        self.assertIsNone(self.editor.draft)

    def test_pending_changes_compare_effective_bytes_and_reset_without_io(self):
        self.assertFalse(self.editor.has_changes())
        self.editor.change(direction=1)  # Ignored for solid lighting.
        self.assertFalse(self.editor.has_changes())
        self.editor.change(style="perKey", color="#FF00FF")
        self.assertFalse(self.editor.has_changes())  # Color is not painted yet.
        self.editor.selection = {39}
        self.editor.paint()
        self.assertTrue(self.editor.has_changes())
        with patch.object(lighting.reader, "open_keyboard") as opened:
            self.editor.reset()
            opened.assert_not_called()

        self.assertFalse(self.editor.has_changes())
        self.assertEqual(self.editor.draft["color"], "#00FEFF")
        self.assertEqual(self.editor.workspace_style, self.editor.draft["style"])

    def test_per_key_stages_multiple_edits_and_applies_one_complete_image(self):
        before = self.service.baseline
        with (patch.object(lighting, "execute", return_value={"lock_indicators": 5}) as execute,
            patch.object(lighting.reader, "open_keyboard") as opened):
            self.editor.change(style="perKey", color="#123456")
            self.editor.selection = {39, 56}
            self.editor.paint()
            self.editor.change(color="#ABCDEF")
            self.editor.selection = {56}
            self.editor.paint()  # Recolor one key without losing the other.
            self.editor.selection = {57}
            self.editor.paint(enabled=False)
            self.editor.prepare()
            execute.assert_not_called()
            opened.assert_not_called()
            self.assertEqual(self.service.baseline, before)

            self.editor.apply(confirmed=True)
            execute.assert_called_once()
            expected, after, _, _ = execute.call_args.args
            self.assertEqual(expected, before)
            for index, record in ((39, "80123456"), (56, "80abcdef"), (57, "0000feff")):
                offset = 0x200 + index * 4
                self.assertEqual(after[offset : offset + 4], bytes.fromhex(record))

            for index in range(144):
                if index not in (39, 56, 57):
                    offset = 0x200 + index * 4
                    self.assertEqual(after[offset : offset + 4], before[offset : offset + 4])

            self.assertEqual(self.service.baseline, after)
            self.assertFalse(self.editor.has_changes())

            # Reset uses the latest applied image and invalidates a pending review.
            applied_slots = deepcopy(self.editor.draft["slots"])
            self.editor.change(color="#FF00FF")
            self.editor.selection = {39, 56}
            self.editor.paint()
            self.editor.prepare()
            self.editor.reset()
            self.assertEqual(self.editor.workspace_style, self.editor.draft["style"])
            self.assertEqual(self.editor.draft["slots"], applied_slots)
            self.assertIsNone(self.editor.review)
            self.assertEqual(self.editor.selection, set())
            self.assertFalse(self.editor.has_changes())
            with self.assertRaises(ValueError):
                self.editor.apply(confirmed=True)

            execute.assert_called_once()
            opened.assert_not_called()

    def test_per_key_reset_discards_all_staged_edits_without_device_io(self):
        original = deepcopy(self.editor.draft)
        before = self.service.baseline
        with (patch.object(lighting, "execute") as execute, patch.object(self.service, "read") as read,
            patch.object(lighting.reader, "open_keyboard") as opened):
            self.editor.change(style="perKey", color="#123456", brightness=8)
            self.editor.selection = {39}
            self.editor.paint()
            self.editor.change(color="#ABCDEF")
            self.editor.selection = {56}
            self.editor.paint()
            self.editor.selection = {57}
            self.editor.paint(enabled=False)
            self.editor.prepare()
            self.editor.reset()
            self.assertEqual(self.editor.draft, original)
            self.assertEqual(self.editor.workspace_style, self.editor.draft["style"])
            self.assertIsNone(self.editor.review)
            self.assertFalse(self.editor.has_changes())
            self.assertEqual(self.service.baseline, before)
            with self.assertRaises(ValueError):
                self.editor.apply(confirmed=True)

            execute.assert_not_called()
            read.assert_not_called()
            opened.assert_not_called()

    def test_per_key_reset_preserves_animation_without_staging_a_mode_change(self):
        before = lighting.candidate(self.service.baseline,
            dict(style="animation", mode=11, brightness=16, speed=12, generated=False, color="#445566", direction=0))
        self.service.baseline = before
        self.editor.adopt(self.service.state())
        original = deepcopy(self.editor.draft)
        self.editor.change(style="perKey", color="#123456", brightness=8)
        self.editor.selection = {39}
        self.editor.paint()
        self.editor.prepare()
        with patch.object(lighting, "execute") as execute, patch.object(lighting.reader, "open_keyboard") as opened:
            self.editor.reset()
            self.assertEqual(self.editor.draft, original)
            self.assertEqual(self.editor.workspace_style, self.editor.draft["style"])
            self.assertEqual(self.editor.preview_draft()["style"], "animation")
            self.assertFalse(self.editor.has_changes())
            self.assertIsNone(self.editor.review)
            with self.assertRaises(ValueError):
                self.editor.prepare()

            self.editor.select_brush("#ABCDEF")  # Choosing a brush is still local.
            self.assertFalse(self.editor.has_changes())
            self.editor.selection = {56}
            self.editor.paint()
            self.assertTrue(self.editor.has_changes())
            self.editor.prepare()
            candidate = self.service.pending[2]
            self.assertEqual(candidate[0x440], 128)
            self.assertEqual(candidate[0x200 + 56 * 4 : 0x204 + 56 * 4], bytes.fromhex("80ABCDEF"))
            self.editor.reset()
            self.editor.select_brush("#123456")
            self.editor.select_mode(128)
            self.editor.change(brightness=8)
            self.assertEqual(self.editor.draft["color"], "#123456")
            self.assertEqual(self.editor.preview_draft()["color"], "#123456")
            execute.assert_not_called()
            opened.assert_not_called()

    def test_change_cache_survives_selection_and_invalidates_on_import(self):
        saved = self.editor.export_draft()
        with patch.object(lighting, "candidate", wraps=lighting.candidate) as build:
            self.assertFalse(self.editor.has_changes())
            self.editor.selection.add(39)
            for _ in range(20):
                self.assertFalse(self.editor.has_changes())

            self.assertEqual(build.call_count, 1)
            self.editor.change(color="#A48BFF")
            self.assertTrue(self.editor.has_changes())
            self.assertEqual(build.call_count, 2)
            self.editor.import_draft(saved)
            self.assertFalse(self.editor.has_changes())
            self.assertEqual(build.call_count, 3)


if __name__ == "__main__":
    unittest.main()
