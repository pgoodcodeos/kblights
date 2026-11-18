"""Assignment parity, candidate policy, transport failures, and offline persistence."""

import fcntl
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import keyboardlights.assignments.service as assignments
import keyboardlights.lighting.service as lighting
from keyboardlights.assignments.editor import AssignmentEditor
from keyboardlights.assignments.model import (CATALOG, KEYS, MODIFIERS, Action, decode_profile, describe_assignment,
    describe_record, draft_dict, offline_plan, parse_draft, profile, profile_filename, python_candidate)
from keyboardlights.profiles.store import AssignmentProfiles, ProfileConflict
from support import baseline_image, reply

BASE = bytes(640)


def draft():
    return draft_dict({39: Action("keyboard", 4)})


class ModelTests(unittest.TestCase):
    def test_every_catalog_entry_matches_the_mac_catalog_exactly(self):
        for kind, values in CATALOG.items():
            for target in values:
                self.assertEqual(list(Action(kind, target["id"]).record()), target["bytes"])

    def test_native_and_editor_candidate_preserve_unlisted_keys_and_metadata(self):
        before = bytearray((index * 17) % 256 for index in range(640))
        before[636:] = b"\0\0\xaa\x55"
        value = draft_dict({39: Action("keyboard", 6, 8), 90: Action("factoryDefault")})
        after = assignments.candidate(bytes(before), value)
        self.assertEqual(after, python_candidate(bytes(before), value))
        self.assertEqual(after[156:160], bytes((2, 8, 6, 0)))
        self.assertEqual(after[360:364], bytes(4))
        changed = set(range(156, 160)) | set(range(360, 364))
        for index in range(640):
            if index not in changed:
                self.assertEqual(after[index], before[index])

    def test_every_physical_source_and_all_eight_modifier_bits_match_native_policy(self):
        self.assertEqual(len(KEYS), 115)
        value = draft_dict({source: Action("disabled") for source in KEYS})
        after = assignments.candidate(BASE, value)
        for source in range(144):
            self.assertEqual(after[source * 4 : source * 4 + 4], bytes((5, 3, 0, 0)) if source in KEYS else bytes(4))

        self.assertEqual(after[576:638], bytes(62))
        self.assertEqual(after[638:], b"\xaa\x55")
        for bit, _ in (*MODIFIERS, (255, "all")):
            after = assignments.candidate(BASE, draft_dict({39: Action("keyboard", 4, bit)}))
            self.assertEqual(after[156:160], bytes((2, bit, 4, 0)))

    def test_all_mouse_media_and_modifier_only_actions_match_native_policy(self):
        for kind, targets in CATALOG.items():
            for target in targets:
                with self.subTest(kind=kind, target=target["id"]):
                    action = Action(kind, target["id"])
                    after = assignments.candidate(BASE, draft_dict({39: action}))
                    self.assertEqual(after[156:160], action.record())

    def test_invalid_actions_sources_duplicates_and_inactive_data_are_rejected(self):
        for action in (Action("keyboard", 255), Action("keyboard", True), Action("media", 2, 1), Action("mouse", 8),
            Action("factoryDefault", 4), Action("disabled", modifiers=1), Action("keyboard", 4, True),
            Action("keyboard", 4, 256), Action("macro")):
            with self.subTest(action=action), self.assertRaises(ValueError):
                action.record()

        for source in (0, -1, 144, True, "39"):
            with self.subTest(source=source), self.assertRaises(ValueError):
                parse_draft({"assignments": [{"source": source, "action": Action("disabled").as_dict()}]})

        entries = draft()["assignments"]
        with self.assertRaises(ValueError):
            parse_draft({"assignments": entries * 2})

        for before in (BASE[:-1], BASE + b"\0", b"\1" + BASE[1:], BASE[:-1] + b"\1"):
            with self.assertRaises(ValueError):
                assignments.candidate(before, draft())

    def test_native_policy_rejects_noop_and_independent_policy_disagreement(self):
        after = assignments.candidate(BASE, draft())
        with self.assertRaisesRegex(ValueError, "already match"):
            assignments.candidate(after, draft())

        with patch.object(assignments, "python_candidate", return_value=bytes(640)):
            with self.assertRaisesRegex(ValueError, "disagree"):
                assignments.candidate(BASE, draft())

    def test_offline_plan_and_mac_profile_roundtrip_contain_no_device_commands(self):
        value = draft_dict({90: Action("keyboard", 6, 8)})
        saved = profile("Work", value)
        self.assertEqual(decode_profile(json.dumps(saved)), saved)
        self.assertEqual(saved["keyAssignments"], value)
        plan = offline_plan(value)
        self.assertEqual(plan["format"], "c989-key-assignment-offline-plan-v1")
        self.assertEqual(plan["patches"][0]["bytes"], [2, 8, 6, 0])
        self.assertFalse(plan["hardwareValidated"])
        self.assertNotIn("commands", plan)
        combined = {**saved, "draft": {"lighting": "never applied by the assignment importer"}}
        self.assertEqual(decode_profile(json.dumps(combined)), saved)

    def test_profile_validation_rejects_bad_names_identity_and_oversized_files(self):
        value = profile("Work", draft())
        for changes in ({"name": ""}, {"name": "x" * 81}, {"name": "bad\nname"}, {"id": "bad"}, {"format": "unknown"},
            {"keyAssignments": {"assignments": [{}]}}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                decode_profile(json.dumps({**value, **changes}))

        with self.assertRaises(ValueError):
            decode_profile(" " * 128_001)

    def test_table_status_does_not_claim_inactive_or_unrecognized_records_are_active(self):
        table = bytearray(BASE)
        table[39 * 4 : 40 * 4] = Action("disabled").record()
        self.assertIn("Inactive", describe_assignment(table, 39))
        table[638:] = b"\xaa\x55"
        self.assertEqual(describe_assignment(table, 39), "Disabled")
        self.assertEqual(describe_assignment(table, 56), "Factory default")
        for offset in (636, 637, 638, 639):
            damaged = bytearray(table)
            damaged[offset] = 0xFE
            with self.subTest(offset=offset):
                self.assertIn("Unrecognized assignment table", describe_assignment(damaged, 39))

        self.assertEqual(describe_assignment(BASE, 39), "Factory default")
        self.assertEqual(describe_assignment(None, 39), "Not loaded")
        self.assertEqual(describe_assignment(BASE[:-1], 39), "Not loaded")

    def test_noncanonical_captured_records_are_not_presented_as_known_actions(self):
        for record in ((2, 0, 224, 0), (2, 1, 224, 0), (2, 0, 0, 0), (2, 0, 4, 1)):
            with self.subTest(record=record):
                self.assertIn("unrecognized", describe_record(bytes(record)))

        self.assertEqual(describe_record(bytes((2, 1, 0, 0))), "Left Ctrl")
        self.assertEqual(describe_record(bytes((2, 8, 6, 0))), "Left Super / GUI + C")

    def test_profile_export_names_are_single_filenames(self):
        for name in ("../Work/Code", "/tmp/profile", "..\\Work\\Code", ".", "  ...  "):
            filename = profile_filename(name)
            self.assertEqual(Path(filename).name, filename)
            self.assertNotIn("/", filename)
            self.assertNotIn("\\", filename)
            self.assertTrue(filename.endswith(".json"))


class EditorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.service = assignments.AssignmentService(Path(self.temporary.name))
        self.service.baseline = (BASE,)
        self.service.identity = {"hidraw": "/dev/not-opened", "usb_path": "1-2"}
        self.editor = AssignmentEditor(self.service)

    def test_current_descriptions_reuse_immutable_capture_and_refresh_when_it_changes(self):
        editor = self.editor
        with patch("keyboardlights.assignments.editor.describe_assignment", wraps=describe_assignment) as describe:
            for _ in range(3):
                for source in KEYS:
                    self.assertEqual(editor.current_description(source), "Factory default")

            self.assertEqual(describe.call_count, len(KEYS))
            editor.set(Action("disabled"), 39)
            editor.select(90)
            self.assertEqual(editor.current_description(39), "Factory default")
            self.assertEqual(describe.call_count, len(KEYS))
            table = python_candidate(BASE, draft_dict({39: Action("disabled")}))
            self.service.baseline = (table, *self.service.baseline[1:])
            self.assertEqual(editor.current_description(39), "Disabled")
            self.assertEqual(describe.call_count, len(KEYS) + 1)
            self.service.baseline = None
            self.assertEqual(editor.current_description(39), "Not loaded")

    def test_current_descriptions_never_cache_mutable_tables(self):
        table = bytearray(python_candidate(BASE, draft_dict({39: Action("disabled")})))
        self.service.baseline = (table, *self.service.baseline[1:])
        self.assertEqual(self.editor.current_description(39), "Disabled")
        table[39 * 4 : 40 * 4] = bytes(4)
        self.assertEqual(self.editor.current_description(39), "Factory default")
        table[638:] = bytes(2)
        table[17 * 4 : 18 * 4] = Action("disabled").record()
        self.assertIn("Inactive", self.editor.current_description(39))

    def test_offline_editing_selection_modifiers_clear_and_atomic_import(self):
        editor = AssignmentEditor()
        editor.change_kind("keyboard")
        self.assertEqual(editor.selected_action.record(), bytes((2, 1, 0, 0)))
        editor.set_modifier(2, True)
        self.assertEqual(editor.selected_action.record(), bytes((2, 3, 0, 0)))
        editor.set_modifier(1, False)  # Intrinsic Ctrl cannot be removed.
        self.assertEqual(editor.selected_action.record(), bytes((2, 3, 0, 0)))
        editor.change_target(6)
        editor.set_modifier(8, True)
        saved = editor.draft
        editor.select(90)
        editor.change_kind("disabled")
        editor.select(39)
        self.assertEqual(editor.selected_action.record(), bytes((2, 10, 6, 0)))
        before = editor.draft
        with self.assertRaises(ValueError):
            editor.change_target(999)

        self.assertEqual(editor.draft, before)
        with self.assertRaises(ValueError):
            editor.load({"assignments": [{}]})

        self.assertEqual(editor.draft, before)
        editor.clear()
        editor.load(saved)
        self.assertEqual(editor.draft, saved)
        for work in (editor.read, editor.prepare, lambda: editor.apply(True)):
            with self.assertRaises(ValueError):
                work()

    def test_read_preserves_draft_and_invalidates_review(self):
        self.editor.change_kind("disabled")
        self.editor.prepare()
        before = self.editor.draft
        with patch.object(self.service, "read", return_value=self.service.baseline):
            self.editor.read()

        self.assertEqual(self.editor.draft, before)
        self.assertIsNone(self.editor.review)

    def test_logging_defaults_off_and_output_directories_are_opt_in(self):
        directory = Path(self.temporary.name) / "optional-logs"
        service = assignments.AssignmentService(directory)
        self.assertFalse(service.logging)
        self.assertIsNone(service.folder("capture"))
        self.assertFalse(directory.exists())
        service.logging = True
        output = service.folder("capture")
        self.assertEqual(output.parent, directory)
        self.assertTrue(directory.is_dir())

    def test_service_captures_only_assignments_and_preserves_draft_on_failure(self):
        self.editor.change_kind("disabled")
        before = self.editor.draft
        images = self.service.baseline

        def capture(output, channels):
            self.assertEqual(channels, (0,))
            self.assertIsNone(output)

            return lighting.reader.CapturedState(
                metadata={"identity": {"hidraw": "/dev/not-opened", "usb_path": "1-2"}}, images=dict(enumerate(images)))

        with (patch.object(lighting.reader, "capture_state", side_effect=capture),
            patch.object(Path, "read_bytes", side_effect=AssertionError("Evidence is not an IPC channel"))):
            self.editor.read()

        self.assertEqual(self.service.baseline, images)
        self.assertEqual(self.editor.draft, before)
        with patch.object(lighting.reader, "capture_state", side_effect=OSError("disconnected")):
            with self.assertRaises(OSError):
                self.editor.read()

        self.assertIsNone(self.service.baseline)
        self.assertEqual(self.editor.draft, before)

    def test_review_has_no_io_edits_invalidate_and_apply_is_confirmed_single_use(self):
        self.editor.change_kind("disabled")
        existing = set(Path(self.temporary.name).rglob("*"))
        with patch.object(lighting.reader, "open_keyboard") as opened:
            self.editor.prepare()
            opened.assert_not_called()

        self.assertEqual(set(Path(self.temporary.name).rglob("*")), existing)
        with patch.object(
            assignments,
            "execute",
            return_value=assignments.AssignmentWriteResult((assignments.candidate(BASE, self.editor.draft),),
                lighting.reader.LockIndicators(5)),
        ) as execute:
            with self.assertRaises(ValueError):
                self.editor.apply(False)

            execute.assert_not_called()
            self.editor.prepare()
            self.editor.apply(True)
            execute.assert_called_once()
            self.assertFalse(self.editor.has_changes())
            self.assertFalse(self.editor.unsaved)
            with self.assertRaises(ValueError):
                self.editor.apply(True)

        self.editor.set(Action("keyboard", 4))
        self.editor.prepare()
        self.editor.set(Action("keyboard", 5))
        self.assertIsNone(self.editor.review)

    def test_mutated_review_or_direct_draft_changes_cannot_apply_stale_plan(self):
        self.editor.change_kind("disabled")
        self.editor.prepare()
        self.editor.actions[39] = Action("keyboard", 4)
        with patch.object(assignments, "execute") as execute:
            with self.assertRaises(ValueError):
                self.editor.apply(True)

            execute.assert_not_called()

    def test_backend_rejects_draft_changes_even_if_the_ui_review_is_mutated_too(self):
        self.editor.change_kind("disabled")
        self.editor.prepare()
        self.editor.actions[39] = Action("keyboard", 4)
        self.editor.review["draft"] = self.editor.draft
        with patch.object(assignments, "execute") as execute:
            with self.assertRaisesRegex(ValueError, "reviewed plan"):
                self.editor.apply(True)

            execute.assert_not_called()

        self.assertIsNone(self.service.pending)
        self.assertIsNotNone(self.service.baseline)
        self.assertIsNone(self.service.guard.evidence)

    def test_review_freezes_mutable_capture_images(self):
        original = self.service.baseline
        buffers = tuple(bytearray(image) for image in original)
        self.service.baseline = buffers
        self.editor.change_kind("disabled")
        self.editor.prepare()
        for image in buffers:
            image[0] ^= 1

        self.assertEqual(self.service.pending[1], original)
        self.assertTrue(all(type(image) is bytes for image in self.service.pending[1]))

    def test_successful_assignment_apply_retains_preflight_lock_reading(self):
        self.editor.change_kind("disabled")
        self.editor.prepare()
        after = assignments.candidate(BASE, self.editor.draft)
        result = assignments.AssignmentWriteResult((after,), lighting.reader.LockIndicators(0x82))
        with patch.object(assignments, "execute", return_value=result):
            self.editor.apply(True)

        self.assertEqual(self.service.baseline, (after,))
        self.assertEqual(self.service.lock_indicators.raw_value, 0x82)
        self.assertTrue(self.service.lock_indicators.caps_lock)

    def test_malformed_assignment_result_blocks_writes_without_publishing_a_baseline(self):
        after = assignments.candidate(BASE, draft())
        for malformed in ((BASE,), assignments.AssignmentWriteResult((BASE,), lighting.reader.LockIndicators(5)),
            assignments.AssignmentWriteResult((after,), None)):
            self.service.baseline = (BASE,)
            self.service.identity = {"hidraw": "/dev/not-opened"}
            self.service.guard = assignments.WriteGuard()
            review = self.service.plan(draft())
            with patch.object(assignments, "execute", return_value=malformed):
                with self.assertRaises(assignments.AssignmentWriteError):
                    self.service.apply(review["token"], draft())

            self.assertIsNone(self.service.baseline)
            self.assertIsNone(self.service.lock_indicators)
            self.assertTrue(self.service.guard.requires_inspection)

    def test_apply_marks_only_the_applied_snapshot_as_saved(self):
        self.editor.change_kind("disabled")
        self.editor.prepare()
        applied = self.editor.draft
        verified = (assignments.candidate(BASE, applied), *self.service.baseline[1:])

        def write(*_):
            self.editor.set(Action("keyboard", 4), 90)
            return assignments.AssignmentWriteResult(verified, lighting.reader.LockIndicators(5))

        with patch.object(assignments, "execute", side_effect=write):
            self.editor.apply(True)

        self.assertTrue(self.editor.unsaved)
        self.assertIn(90, self.editor.actions)

    def test_effective_change_cache_preserves_selection_edit_and_capture_semantics(self):
        self.editor.set(Action("keyboard", 4))
        value = self.editor.draft
        applied = assignments.candidate(BASE, value)
        with patch("keyboardlights.assignments.editor.python_candidate", wraps=python_candidate) as candidate:
            self.assertTrue(self.editor.has_changes())
            for source in (39, 90) * 25:
                self.editor.select(source)
                self.assertTrue(self.editor.has_changes())

            candidate.assert_called_once()
            self.service.baseline = (applied, *self.service.baseline[1:])
            self.assertFalse(self.editor.has_changes())
            self.editor.actions[39] = Action("keyboard", 5)
            self.assertTrue(self.editor.has_changes())
            self.editor.load(value)
            self.assertFalse(self.editor.has_changes())

            # Mutable images are never trusted by the cache.
            mutable = bytearray(applied)
            self.service.baseline = (mutable, *self.service.baseline[1:])
            self.assertFalse(self.editor.has_changes())
            mutable[158] = 5
            self.assertTrue(self.editor.has_changes())

    def test_saved_profile_deletion_or_external_change_restores_draft_protection(self):
        self.editor.change_kind("disabled")
        saved = profile("Work", self.editor.draft)
        self.editor.saved(self.editor.draft, profile_id=saved["id"])
        self.editor.reconcile_saved_profiles([saved])
        self.assertFalse(self.editor.unsaved)
        self.editor.reconcile_saved_profiles([])
        self.assertTrue(self.editor.unsaved)
        self.editor.saved(self.editor.draft, profile_id=saved["id"])
        self.editor.reconcile_saved_profiles([{**saved, "keyAssignments": {"assignments": []}}])
        self.assertTrue(self.editor.unsaved)

        # A separately exported or applied copy is independent of this library.
        self.editor.saved(self.editor.draft)
        self.editor.reconcile_saved_profiles([])
        self.assertFalse(self.editor.unsaved)

    def test_uncertain_upload_blocks_lighting_and_assignments_even_after_read(self):
        guard = self.service.guard
        light_service = lighting.LightingService(Path(self.temporary.name) / "Lighting", guard=guard)
        light_service.baseline = baseline_image()
        self.editor.change_kind("disabled")
        self.editor.prepare()
        error = assignments.AssignmentWriteError("/tmp/upload-evidence", OSError("injected"), uncertain=True)
        with patch.object(assignments, "execute", side_effect=error):
            with self.assertRaises(assignments.AssignmentWriteError):
                self.editor.apply(True)

        self.assertIsNone(self.service.baseline)
        self.assertIsNone(self.editor.review)
        self.assertEqual(guard.evidence, "/tmp/upload-evidence")
        self.service.baseline = (BASE,)
        for work in (lambda: self.service.plan(draft()),
            lambda: light_service.plan(dict(style="solid", color="#FF00FF", brightness=16))):
            with self.assertRaisesRegex(ValueError, "inspection"):
                work()

        guard.block("/tmp/new-evidence")
        self.assertEqual(guard.evidence, "/tmp/upload-evidence")

    def test_preupload_failure_invalidates_capture_without_blocking_other_writes(self):
        self.editor.change_kind("disabled")
        self.editor.prepare()
        error = assignments.AssignmentWriteError("/tmp/preflight", OSError("injected"), uncertain=False)
        with patch.object(assignments, "execute", side_effect=error):
            with self.assertRaises(assignments.AssignmentWriteError):
                self.editor.apply(True)

        self.assertIsNone(self.service.baseline)
        self.assertIsNone(self.service.guard.evidence)

    def test_lighting_upload_uncertainty_blocks_assignment_writes(self):
        light_service = lighting.LightingService(Path(self.temporary.name) / "Lighting", guard=self.service.guard)
        light_service.baseline = baseline_image()
        light_service.identity = self.service.identity
        review = light_service.plan(dict(style="solid", color="#FF00FF", brightness=16))
        with patch.object(lighting, "execute",
            side_effect=lighting.IncompleteUploadError("/tmp/light-upload", OSError("injected"))):
            with self.assertRaises(lighting.IncompleteUploadError):
                light_service.apply(review["token"])

        self.assertEqual(self.service.guard.evidence, "/tmp/light-upload")
        with self.assertRaisesRegex(ValueError, "blocked"):
            self.service.plan(draft())

    def test_local_profiles_are_atomic_and_failed_save_preserves_previous_library(self):
        store = AssignmentProfiles(Path(self.temporary.name) / "Profiles")
        value = profile("Work", draft())
        store.save([value])
        self.assertEqual(store.load(), [value])
        before = store.path.read_bytes()
        with patch("keyboardlights.profiles.store.os.replace", side_effect=OSError("injected")):
            with self.assertRaises(OSError):
                store.save([profile("Other", draft())])

        self.assertEqual(store.path.read_bytes(), before)
        self.assertEqual(list(store.path.parent.glob(".assignment-profiles-*")), [])
        with self.assertRaises(ValueError):
            store.save([value, value])

    def test_concurrent_profile_windows_cannot_silently_overwrite_each_other(self):
        a = AssignmentProfiles(Path(self.temporary.name) / "Profiles")
        b = AssignmentProfiles(a.path.parent)
        self.assertEqual(a.load(), [])
        self.assertEqual(b.load(), [])
        first, second = profile("First window", draft()), profile("Second window", draft())
        a.save([first])
        with self.assertRaises(ProfileConflict):
            b.save([second])

        self.assertEqual(b.load(), [first])
        b.save([first, second])
        with self.assertRaises(ProfileConflict):
            a.save([])

        self.assertEqual(a.load(), [first, second])

    def test_profile_library_lock_covers_revision_check_and_replace(self):
        store = AssignmentProfiles(Path(self.temporary.name) / "Profiles")
        value = profile("Work", draft())
        store.save([value])
        with store.path.with_suffix(".lock").open("rb") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(ProfileConflict):
                store.save([])

        self.assertEqual(store.load(), [value])

    def test_uncertain_profile_directory_sync_requires_reload_before_another_save(self):
        store = AssignmentProfiles(Path(self.temporary.name) / "Profiles")
        first, second = profile("First", draft()), profile("Second", draft())
        store.save([first])
        sync = lighting.sync_directory

        def fail_after_replace(path):
            if path == store.path.parent and "Second" in store.path.read_text():
                raise OSError("final directory sync failed")

            sync(path)

        with patch.object(lighting, "sync_directory", side_effect=fail_after_replace):
            with self.assertRaisesRegex(OSError, "final directory sync"):
                store.save([second])

        with self.assertRaises(ProfileConflict):
            store.save([first])

        self.assertEqual(store.load(), [second])

    def test_nonregular_profile_library_and_lock_are_rejected_without_blocking(self):
        store = AssignmentProfiles(Path(self.temporary.name))
        os.mkfifo(store.path)
        with self.assertRaisesRegex(ValueError, "regular file"):
            store.load()

        store.path.unlink()
        os.mkfifo(store.path.with_suffix(".lock"))
        with self.assertRaisesRegex(ValueError, "regular file"):
            store.save([])


class MockAssignmentTransport:
    def __init__(self, after, fail_at=0, bad_checksum=False):
        self.after = after
        self.calls = self.receives = 0
        self.sent = []
        self.fail_at = fail_at
        self.bad_checksum = bad_checksum

    def _exchange(self, payload=None):
        self.calls += 1
        if payload is not None:
            self.sent.append(payload)

        if self.calls == self.fail_at:
            raise OSError("injected ioctl failure")

        if payload is None:
            self.receives += 1
            return reply(0x11) if self.receives == 1 else reply(2, sum(self.after) + self.bad_checksum)

        return payload


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.before = (BASE,)
        self.after = assignments.candidate(BASE, draft())
        self.transport = MockAssignmentTransport(self.after)
        self.opened = self.enterContext(patch.object(lighting.reader, "open_keyboard"))
        self.opened.return_value.__enter__.return_value = 9
        self.read = self.enterContext(
            patch.object(lighting.reader.Transport, "read", side_effect=lambda channel,
                expected: (expected, expected, sum(expected), 0))
        )
        self.enterContext(patch.object(lighting.reader.Transport, "unlock"))
        self.exchange = self.enterContext(
            patch.object(lighting.reader.Transport, "_exchange", side_effect=self.transport._exchange)
        )
        self.lock_read = self.enterContext(
            patch.object(lighting.reader.Transport, "read_lock_indicators",
                return_value=lighting.reader.LockIndicators(5))
        )
        self.sleep = self.enterContext(patch.object(lighting.time, "sleep"))

    def test_lock_read_failure_stops_before_upload(self):
        self.lock_read.side_effect = OSError("lock indicators unavailable")
        with self.assertRaises(assignments.AssignmentWriteError) as caught:
            self.execute()

        self.assertFalse(caught.exception.uncertain)
        self.assertEqual(self.transport.sent, [])
        self.lock_read.assert_called_once()

    def execute(self, name="write"):
        return assignments.execute(self.before, self.after, draft(), {"hidraw": "/dev/not-opened"},
            self.directory / name)

    def test_disabled_logging_verifies_assignment_write_without_creating_files(self):
        with (patch.object(lighting, "durable", side_effect=AssertionError("No diagnostic writes")),
            patch.object(lighting, "make_directory", side_effect=AssertionError("No diagnostic directories"))):
            after = assignments.execute(self.before, self.after, draft(), {"hidraw": "/dev/not-opened"}, None)

        self.assertEqual(after.channels, (self.after,))
        self.assertEqual(after.lock_indicators, lighting.reader.LockIndicators(5))
        self.assertEqual(self.read.call_count, 2)
        self.assertEqual(self.transport.sent.count(lighting.reader.command(0xF0)), 1)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_uncertain_assignment_without_logs_still_blocks_writes(self):
        guard = assignments.WriteGuard()
        guard.block(None)
        self.assertTrue(guard.requires_inspection)
        self.assertIsNone(guard.evidence)
        with self.assertRaisesRegex(ValueError, "Further writes are blocked"):
            guard.check()

    def test_exact_assignment_channel_gates_thirteen_reports_one_f0_and_mac_timing(self):
        after = self.execute()
        self.assertEqual(after.channels, (self.after,))
        self.assertEqual(after.lock_indicators, lighting.reader.LockIndicators(5))
        self.assertEqual(self.read.call_count, 2)
        self.assertEqual([call.args[0] for call in self.read.call_args_list], [0, 0])
        self.assertEqual([call.args[1] for call in self.read.call_args_list],
            [*self.before, self.after, *self.before[1:]])
        self.assertEqual(b"".join(self.transport.sent), lighting.stream(self.after, 0))
        self.assertEqual(self.transport.sent.count(lighting.reader.command(0xF0)), 1)
        self.assertEqual([call.args[0] for call in self.sleep.call_args_list], [0.07] + [0.015] * 15)
        status = json.loads((self.directory / "write/result.json").read_text())
        self.assertTrue(status["assignment_preflight"])
        self.assertTrue(status["assignment_postflight"])

    def test_assignment_preflight_mismatch_prevents_upload(self):
        for mismatch in (0,):
            with self.subTest(channel=mismatch):

                def read(channel, expected):
                    bad = bytes((expected[0] ^ 1,)) + expected[1:]
                    return (bad if channel == mismatch else expected, expected, sum(expected), 0)

                self.read.side_effect = read
                with self.assertRaises(assignments.AssignmentWriteError) as caught:
                    self.execute(f"mismatch-{mismatch}")

                self.assertFalse(caught.exception.uncertain)
                self.assertEqual(self.transport.sent, [])

    def test_all_transport_failures_stop_once_and_classify_upload_as_uncertain(self):
        for failure in range(1, 16):
            self.transport = MockAssignmentTransport(self.after, fail_at=failure)
            self.exchange.side_effect = self.transport._exchange
            with self.subTest(failure=failure), self.assertRaises(assignments.AssignmentWriteError) as caught:
                self.execute(f"failure-{failure}")

            self.assertTrue(caught.exception.uncertain)
            self.assertEqual(self.transport.calls, failure)
            self.assertLessEqual(self.transport.sent.count(lighting.reader.command(0xF0)), 1)

    def test_bad_checksum_never_activates(self):
        self.transport.bad_checksum = True
        with self.assertRaises(assignments.AssignmentWriteError):
            self.execute()

        self.assertNotIn(lighting.reader.command(0xF0), self.transport.sent)

    def test_assignment_postflight_mismatch_is_uncertain(self):
        calls = 0

        def read(channel, expected):
            nonlocal calls
            calls += 1
            bad = bytes((expected[0] ^ 1,)) + expected[1:]
            return (bad if calls == 2 else expected, expected, sum(expected), 0)

        self.read.side_effect = read
        with self.assertRaises(assignments.AssignmentWriteError) as caught:
            self.execute()

        self.assertTrue(caught.exception.uncertain)
        self.assertIn("after channel-0", str(caught.exception))
        self.assertEqual(self.transport.sent.count(lighting.reader.command(0xF0)), 1)

    def test_final_evidence_failure_reports_verified_assignment_success(self):
        save = lighting.save_json

        def fail_result(path, value):
            if path.name == "result.json":
                raise OSError("disk full")

            save(path, value)

        with patch.object(lighting, "save_json", side_effect=fail_result):
            with self.assertRaises(assignments.AssignmentWriteError) as caught:
                self.execute()

        self.assertTrue(caught.exception.verified)
        self.assertTrue(caught.exception.uncertain)
        self.assertIn("Key assignments were updated", str(caught.exception))
        self.assertNotIn("may be incomplete", str(caught.exception))
        self.assertEqual(self.transport.sent.count(lighting.reader.command(0xF0)), 1)

    def test_final_evidence_failure_never_masks_the_original_f0_failure(self):
        self.transport.fail_at = 15  # F0 exchange.
        save = lighting.save_json

        def fail_result(path, value):
            if path.name == "result.json":
                raise OSError("disk full")

            save(path, value)

        with patch.object(lighting, "save_json", side_effect=fail_result):
            with self.assertRaises(assignments.AssignmentWriteError) as caught:
                self.execute()

        self.assertFalse(caught.exception.verified)
        self.assertIn("injected ioctl failure", str(caught.exception))
        self.assertIn("disk full", str(caught.exception.__cause__.__notes__))
        self.assertEqual(self.transport.calls, 15)

    def test_candidate_tampering_and_journal_sync_failure_prevent_device_open(self):
        with self.assertRaisesRegex(ValueError, "review"):
            assignments.execute(self.before, bytes(640), draft(), {}, self.directory / "tamper")

        self.opened.assert_not_called()
        sync = lighting.sync_directory

        def fail_journal(path):
            if (path / "journal.jsonl").exists():
                raise OSError("journal sync failed")

            sync(path)

        with patch.object(lighting, "sync_directory", side_effect=fail_journal):
            with self.assertRaises(assignments.AssignmentWriteError) as caught:
                self.execute()

        self.assertFalse(caught.exception.uncertain)
        self.opened.assert_not_called()


if __name__ == "__main__":
    unittest.main()
