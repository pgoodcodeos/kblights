"""Combined review ordering and partial completion, without device access."""

import tempfile
import threading
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import keyboardlights.assignments.service as assignments
import keyboardlights.lighting.service as lighting
from keyboardlights.assignments.editor import AssignmentEditor
from keyboardlights.assignments.model import Action
from keyboardlights.device.capture import operation_batch, operation_lock
from keyboardlights.lighting.changes import KeyboardChanges
from keyboardlights.lighting.editor import LightingEditor
from support import baseline_image


class KeyboardChangesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        self.light_service = lighting.LightingService(directory / "Lighting")
        self.light_service.baseline = baseline_image()
        self.light_service.identity = {"hidraw": "/dev/not-opened"}
        self.assignment_service = assignments.AssignmentService(directory / "Assignments",
            guard=self.light_service.guard)
        self.assignment_service.baseline = (bytes(640),)
        self.assignment_service.identity = dict(self.light_service.identity)
        self.light = LightingEditor(self.light_service)
        self.assignments = AssignmentEditor(self.assignment_service)
        self.changes = KeyboardChanges(self.light, self.assignments)
        self.events = []
        self.images = (self.assignment_service.baseline[0], self.light_service.baseline, bytes(3072), bytes(64))

    def stage(self):
        self.light.change(brightness=8)
        self.assignments.set(Action("disabled"), 39)

    def write_assignments(self, before, after, draft, identity, output):
        self.events.append("assignments")
        self.images = (after, *self.images[1:])
        return assignments.AssignmentWriteResult((after,), lighting.reader.LockIndicators(5))

    def write_lighting(self, before, after, identity, output):
        self.events.append("lighting")
        self.images = (self.images[0], after, *self.images[2:])
        return {"lock_indicators": 5}

    def refresh(self):
        self.assignment_service.baseline = (self.images[0],)
        self.assignment_service.identity = dict(self.light_service.identity)
        return (self.images[0],)

    def test_batch_allows_its_own_steps_but_excludes_other_workers(self):
        errors = []

        def other_worker():
            try:
                with operation_lock():
                    errors.append("unexpected access")
            except BlockingIOError:
                errors.append("busy")

        with operation_batch():
            with operation_lock():
                worker = threading.Thread(target=other_worker)
                worker.start()
                worker.join(timeout=2)
                self.assertFalse(worker.is_alive())
                self.assertEqual(errors, ["busy"])

        with operation_lock():
            pass

    def test_both_edits_share_one_review_and_apply_in_order(self):
        self.stage()
        review = self.changes.prepare()
        self.assertIsNotNone(review["lighting"])
        self.assertIsNotNone(review["assignments"])
        with (patch.object(assignments, "execute", side_effect=self.write_assignments),
            patch.object(lighting, "execute", side_effect=self.write_lighting),
            patch.object(self.assignment_service, "read", side_effect=self.refresh)):
            message = self.changes.apply(confirmed=True)

        self.assertEqual(self.events, ["assignments", "lighting"])
        self.assertFalse(self.light.has_changes())
        self.assertFalse(self.assignments.has_changes())
        self.assertIn("verified", message)
        self.assertIsNone(self.changes.review)

    def test_assignment_only_changes_do_not_start_a_lighting_write(self):
        self.assignments.set(Action("disabled"), 39)
        review = self.changes.prepare()
        self.assertIsNone(review["lighting"])
        with (patch.object(assignments, "execute", side_effect=self.write_assignments),
            patch.object(lighting, "execute") as light):
            self.changes.apply(confirmed=True)
            light.assert_not_called()

        self.assertEqual(self.events, ["assignments"])

    def test_lighting_only_changes_do_not_start_an_assignment_write(self):
        self.light.change(brightness=8)
        review = self.changes.prepare()
        self.assertIsNone(review["assignments"])
        with (patch.object(assignments, "execute") as assignment,
            patch.object(lighting, "execute", side_effect=self.write_lighting),
            patch.object(self.assignment_service, "read", side_effect=self.refresh)):
            self.changes.apply(confirmed=True)
            assignment.assert_not_called()

        self.assertEqual(self.events, ["lighting"])

    def test_first_failure_leaves_lighting_draft_and_never_starts_lighting(self):
        self.stage()
        draft = deepcopy(self.light.draft)
        self.changes.prepare()
        with (patch.object(assignments, "execute", side_effect=OSError("assignment failure")) as write,
            patch.object(lighting, "execute") as light):
            with self.assertRaisesRegex(ValueError, "update stopped"):
                self.changes.apply(confirmed=True)

            write.assert_called_once()
            light.assert_not_called()

        self.assertEqual(self.light.draft, draft)
        self.assertEqual(self.changes.completed, [])
        self.assertIsNone(self.changes.review)

    def test_second_failure_reports_partial_completion_and_does_not_retry(self):
        self.stage()
        self.changes.prepare()
        with (patch.object(assignments, "execute", side_effect=self.write_assignments) as assignment,
            patch.object(lighting, "execute", side_effect=OSError("lighting failure")) as light):
            with self.assertRaisesRegex(ValueError, "Assignments applied"):
                self.changes.apply(confirmed=True)

            with self.assertRaises(ValueError):
                self.changes.apply(confirmed=True)

            assignment.assert_called_once()
            light.assert_called_once()

        self.assertEqual(self.images[0][156:160], Action("disabled").record())

    def test_verified_assignment_evidence_failure_reports_completion_without_starting_lighting(self):
        self.stage()
        self.changes.prepare()
        error = assignments.AssignmentWriteError("/tmp/assignment-evidence", OSError("disk full"), uncertain=True,
            verified=True)
        with (patch.object(assignments, "execute", side_effect=error) as assignment,
            patch.object(lighting, "execute") as light):
            with self.assertRaisesRegex(ValueError, "Assignments applied; the update stopped"):
                self.changes.apply(confirmed=True)

            assignment.assert_called_once()
            light.assert_not_called()

        self.assertEqual(self.changes.completed, ["Assignments"])
        self.assertTrue(self.light_service.guard.requires_inspection)
        self.assertIsNone(self.changes.review)
        self.assertIsNone(self.light.review)
        self.assertIsNone(self.assignments.review)

    def test_verified_lighting_evidence_failure_reports_every_completed_step(self):
        self.stage()
        self.changes.prepare()
        error = lighting.VerifiedWriteEvidenceError("/tmp/lighting-evidence", OSError("disk full"))
        with (patch.object(assignments, "execute", side_effect=self.write_assignments) as assignment,
            patch.object(lighting, "execute", side_effect=error) as light,
            patch.object(self.assignment_service, "read") as refresh):
            with self.assertRaisesRegex(ValueError, "Assignments and Lighting applied; the update stopped"):
                self.changes.apply(confirmed=True)

            assignment.assert_called_once()
            light.assert_called_once()
            refresh.assert_not_called()

        self.assertEqual(self.changes.completed, ["Assignments", "Lighting"])
        self.assertTrue(self.light_service.guard.requires_inspection)
        self.assertIsNone(self.changes.review)
        self.assertIsNone(self.light.review)
        self.assertIsNone(self.assignments.review)

    def test_writes_do_not_recapture_other_functions(self):
        self.stage()
        self.changes.prepare()
        with (patch.object(assignments, "execute", side_effect=self.write_assignments),
            patch.object(lighting, "execute", side_effect=self.write_lighting),
            patch.object(self.assignment_service, "read") as refresh):
            self.changes.apply(confirmed=True)
            refresh.assert_not_called()

    def test_draft_changed_during_preparation_discards_both_reviews(self):
        self.stage()
        plan = self.assignment_service.plan

        def changed(draft):
            review = plan(draft)
            self.assignments.set(Action("keyboard", 4), 90)
            return review

        with (patch.object(self.assignment_service, "plan", side_effect=changed),
            patch.object(assignments, "execute") as assignment, patch.object(lighting, "execute") as light):
            with self.assertRaisesRegex(ValueError, "changed during review"):
                self.changes.prepare()

            self.assertIsNone(self.changes.review)
            self.assertIsNone(self.assignments.review)
            self.assertIsNone(self.light.review)
            assignment.assert_not_called()
            light.assert_not_called()

    def test_changed_draft_or_missing_confirmation_never_writes(self):
        self.stage()
        self.changes.prepare()
        self.assignments.set(Action("factoryDefault"), 39)
        with patch.object(assignments, "execute") as assignment, patch.object(lighting, "execute") as light:
            with self.assertRaises(ValueError):
                self.changes.apply(confirmed=True)

            self.changes.prepare()
            with self.assertRaises(ValueError):
                self.changes.apply()

            assignment.assert_not_called()
            light.assert_not_called()

    def test_incomplete_paint_or_mismatched_captures_cannot_be_reviewed(self):
        self.stage()
        self.light.clear_paint()
        with self.assertRaisesRegex(ValueError, "blank key"):
            self.changes.prepare()

        self.light.fill_paint()
        self.assignment_service.identity = {"hidraw": "/dev/other"}
        with self.assertRaisesRegex(ValueError, "different captures"):
            self.changes.prepare()
