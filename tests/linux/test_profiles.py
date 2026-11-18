"""Lighting profile persistence and legacy draft compatibility without hardware."""

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from keyboardlights.lighting.editor import LightingEditor
from keyboardlights.profiles.io import MAX_DRAFT_BYTES, encode_profile
from keyboardlights.profiles.lighting import LightingProfileEditor, LightingProfiles, decode_profile, profile
from keyboardlights.profiles.store import AssignmentProfiles, ProfileConflict
from support import baseline_image


class LightingProfileTests(unittest.TestCase):
    def setUp(self):
        self.editor = LightingEditor(capture=baseline_image())
        self.adapter = LightingProfileEditor(self.editor)

    def test_legacy_and_named_exports_round_trip_without_changing_editor(self):
        original = self.editor.export_draft()
        legacy = decode_profile(original)
        self.assertEqual(legacy["settings"], self.editor.draft)
        named = profile("Desk", self.editor.draft)
        self.assertEqual(decode_profile(json.dumps(named)), named)
        self.assertEqual(self.editor.export_draft(), original)
        named["settings"]["slots"][39]["color"] = "#123456"
        self.assertNotEqual(named["settings"], self.editor.draft)

    def test_invalid_profiles_are_rejected_before_changing_draft(self):
        saved = profile("Desk", self.editor.draft)
        original = self.editor.export_draft()
        for fields in ({"name": ""}, {"name": "x" * 81}, {"id": "invalid"}, {"name": "bad\nname"},
            {"format": "unknown"}, {"settings": {"style": "perKey"}}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                decode_profile(json.dumps({**saved, **fields}))

        self.assertEqual(self.editor.export_draft(), original)

    def test_compact_unicode_profiles_round_trip_without_escape_expansion(self):
        self.editor.draft["notes"] = "☀" * 25000
        draft = self.editor.snapshot_draft()
        value = profile("Desk", draft)
        text = encode_profile(value)
        self.assertLessEqual(len(text.encode()), MAX_DRAFT_BYTES)
        self.assertEqual(decode_profile(text), value)
        self.adapter.load(value["settings"])
        self.assertEqual(self.editor.draft, draft)
        exported = self.editor.export_draft()
        self.assertLessEqual(len(exported.encode()), MAX_DRAFT_BYTES)
        self.editor.import_draft(exported)
        self.assertEqual(self.editor.draft, draft)

    def test_libraries_are_separate_and_concurrent_lighting_saves_conflict(self):
        with tempfile.TemporaryDirectory() as directory:
            first, second = LightingProfiles(directory), LightingProfiles(directory)
            self.assertEqual(first.load(), [])
            self.assertEqual(second.load(), [])
            saved = profile("Desk", self.editor.draft)
            first.save([saved])
            with self.assertRaises(ProfileConflict):
                second.save([])

            self.assertEqual(second.load(), [saved])
            self.assertEqual(AssignmentProfiles(directory).load(), [])
            self.assertFalse(Path(directory, "assignment-profiles.json").exists())

    def test_new_lighting_profile_is_blank_and_cannot_be_saved_until_complete(self):
        baseline = self.editor.image
        self.adapter.clear()
        self.assertTrue(self.editor.has_unpainted())
        self.assertTrue(self.adapter.unsaved)
        self.assertEqual(self.editor.image, baseline)
        with self.assertRaises(ValueError):
            _ = self.adapter.draft

        self.editor.fill_paint()
        self.assertFalse(self.editor.has_unpainted())
        self.adapter.saved(self.adapter.draft)
        self.assertFalse(self.adapter.unsaved)

    def test_draft_snapshots_and_saved_baselines_do_not_share_mutable_editor_state(self):
        draft = self.adapter.draft
        self.adapter.saved(draft)
        draft["slots"][39]["color"] = "#123456"
        self.assertNotEqual(draft, self.editor.draft)
        self.assertFalse(self.adapter.unsaved)
        self.editor.select_brush("#ABCDEF")
        self.editor.paint_key(39)
        self.assertTrue(self.adapter.unsaved)
        self.assertEqual(draft["slots"][39]["color"], "#123456")

    def test_loaded_profile_tracks_edits_and_lost_library_entries(self):
        self.editor.change(color="#123456")
        value = profile("Desk", self.editor.draft)
        self.adapter.load(value["settings"])
        self.adapter.saved(self.adapter.draft, profile_id=value["id"])
        self.assertFalse(self.adapter.unsaved)
        self.adapter.reconcile_saved_profiles([deepcopy(value)])
        self.assertFalse(self.adapter.unsaved)
        self.adapter.reconcile_saved_profiles([])
        self.assertTrue(self.adapter.unsaved)

    def test_saving_animation_does_not_mark_hidden_blank_draft_as_saved(self):
        self.editor.select_mode(11)
        value = profile("Animation", self.adapter.draft)
        self.adapter.saved(self.adapter.draft, profile_id=value["id"])
        self.editor.select_mode(128)
        self.editor.clear_paint()
        self.editor.select_mode(11)
        self.assertEqual(self.adapter.draft, value["settings"])
        self.assertTrue(self.adapter.unsaved)
        self.adapter.saved(self.adapter.draft, profile_id=value["id"])
        self.assertTrue(self.adapter.unsaved)

    def test_saving_animation_does_not_mark_remembered_static_brightness_as_saved(self):
        self.editor.change(brightness=4)
        self.editor.select_mode(11)
        self.assertEqual(self.editor.draft["brightness"], 16)
        self.adapter.saved(self.adapter.draft)
        self.assertTrue(self.adapter.unsaved)
        self.editor.select_mode(128)
        self.assertEqual(self.editor.draft["brightness"], 4)

    def test_lost_profile_restores_protection_even_when_it_matches_the_capture(self):
        value = profile("Current lighting", self.adapter.draft)
        self.adapter.saved(self.adapter.draft, profile_id=value["id"])
        self.assertFalse(self.editor.has_pending_edits())
        self.adapter.reconcile_saved_profiles([])
        self.assertTrue(self.adapter.unsaved)

    def test_oversized_library_is_rejected_before_replacing_the_saved_file(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LightingProfiles(directory)
            store.load()
            first = profile("First", self.adapter.draft)
            second = profile("Second", self.adapter.draft)
            store.save([first])
            before = store.path.read_bytes()
            with patch("keyboardlights.profiles.store.MAX_LIBRARY_BYTES", len(before) + 1):
                with self.assertRaisesRegex(ValueError, "too large"):
                    store.save([first, second])

            self.assertEqual(store.path.read_bytes(), before)
            self.assertEqual(store.load(), [first])
            self.assertEqual(list(store.path.parent.glob(".lighting-profiles-*")), [])

    def test_large_valid_profile_exports_a_file_that_can_be_imported(self):
        draft = self.adapter.draft
        draft["notes"] = "x" * 123000
        value = profile("Large", draft)
        self.assertGreater(len((json.dumps(value, indent=2) + "\n").encode()), MAX_DRAFT_BYTES)
        text = encode_profile(value)
        self.assertLessEqual(len(text.encode()), MAX_DRAFT_BYTES)
        self.assertEqual(decode_profile(text), value)
        with self.assertRaisesRegex(ValueError, "too large"):
            encode_profile(value, limit=100)


if __name__ == "__main__":
    unittest.main()
