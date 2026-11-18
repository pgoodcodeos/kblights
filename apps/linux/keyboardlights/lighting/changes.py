"""One review for keyboard edits, using the existing separately verified writers."""

from copy import deepcopy

from keyboardlights.assignments.service import AssignmentWriteError
from keyboardlights.device.capture import operation_batch
from keyboardlights.lighting.service import VerifiedWriteEvidenceError


class KeyboardChanges:
    def __init__(self, lighting, assignments):
        self.lighting = lighting
        self.assignments = assignments
        self.review = None
        self.completed = []

    def has_changes(self):
        return self.lighting.has_changes() or self.assignments.has_changes()

    def can_review(self):
        light = self.lighting.has_changes()
        assignments = self.assignments.has_changes()
        return (
            not self.lighting.has_unpainted()
            and (light or assignments)
            and (not light or (self.lighting.service is not None and self.lighting.service.baseline is not None))
            and (
                not assignments
                or (self.assignments.service is not None and self.assignments.service.baseline is not None)
            )
        )

    def _signature(self):
        return (deepcopy(self.lighting.draft), frozenset(self.lighting.unpainted), deepcopy(self.assignments.draft))

    def prepare(self):
        self.review = None
        self.completed = []
        if self.lighting.has_unpainted():
            raise ValueError("Paint or fill every blank key before reviewing")

        lighting = self.lighting.has_changes()
        assignments = self.assignments.has_changes()
        if not lighting and not assignments:
            raise ValueError("Stage a change before reviewing")

        if lighting and assignments:
            left, right = self.lighting.service, self.assignments.service
            if left is None or right is None or right.baseline is None or left.identity != right.identity:
                raise ValueError(
                    "Lighting and assignments use different captures. Save drafts, reset lighting, then Refresh before reviewing"
                )

        signature = self._signature()
        try:
            light_review = self.lighting.prepare() if lighting else None
            assignment_review = self.assignments.prepare() if assignments else None
            if signature != self._signature():
                raise ValueError("The draft changed during review. Review all changes again.")

            service = self.lighting.service if lighting else self.assignments.service
            self.review = {"lighting": light_review, "assignments": assignment_review, "signature": signature,
                "identity": deepcopy(service.identity)}
            return deepcopy(self.review)
        except Exception:
            self.lighting.review = self.assignments.review = None
            raise

    def apply(self, *, confirmed=False):
        reviewed = self.review
        self.review = None
        self.completed = []
        if (
            not confirmed
            or reviewed is None
            or reviewed["signature"] != self._signature()
            or (reviewed["lighting"] is not None and self.lighting.review != reviewed["lighting"])
            or (reviewed["assignments"] is not None and self.assignments.review != reviewed["assignments"])
        ):
            raise ValueError("Review and confirm all current changes before applying")

        with operation_batch():
            return self._apply_reviewed(reviewed)

    def _apply_reviewed(self, reviewed):
        step = None
        try:
            if reviewed["assignments"] is not None:
                step = "Assignments"
                self.assignments.apply(confirmed=True)
                self.completed.append("Assignments")

            if reviewed["lighting"] is not None:
                step = "Lighting"
                self.lighting.apply(confirmed=True)
                self.completed.append("Lighting")
        except Exception as error:
            if (step == "Assignments" and isinstance(error, AssignmentWriteError) and error.verified) or (
                step == "Lighting" and isinstance(error, VerifiedWriteEvidenceError)
            ):
                self.completed.append(step)

            prefix = " and ".join(self.completed) + " applied; " if self.completed else "No changes verified; "
            raise ValueError(prefix + "the update stopped. " + str(error)) from error
        finally:
            self.lighting.review = self.assignments.review = None

        return " and ".join(self.completed) + " applied and verified."
