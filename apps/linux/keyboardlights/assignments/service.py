"""Mac-equivalent assignment policy and single-channel Linux hidraw transactions."""

import ctypes as C
import os
import secrets
import threading
import time
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

import keyboardlights.device.capture as reader
import keyboardlights.lighting.service as lighting
from keyboardlights.assignments.model import parse_draft, python_candidate
from keyboardlights.device.activity import WriteGuard
from keyboardlights.device.core import AssignmentPatch


def candidate(before, draft):
    """Require independent Python and shared native policies to agree exactly."""
    expected = python_candidate(before, draft)
    actions = parse_draft(draft)
    patches = (AssignmentPatch * len(actions))(
        *(AssignmentPatch(source, (C.c_uint8 * 4)(*action.record())) for source, action in sorted(actions.items()))
    )
    original = (C.c_uint8 * 640).from_buffer_copy(before)
    output = (C.c_uint8 * 640)()
    error = C.create_string_buffer(1024)
    lib = lighting.native_library()
    code = lib.c989_assignments_prepare(original, len(before), patches, len(patches), output, len(output), error,
        len(error))
    if code:
        raise ValueError(error.value.decode("utf-8", errors="replace"))

    after = bytes(output)
    if after != expected:
        raise ValueError("The native writer and assignment editor disagree; no write is allowed")

    return after


def validate_channels(channels):
    if len(channels) != 1 or len(channels[0]) != 640:
        raise ValueError("A channel-0 assignment capture is required")


class AssignmentWriteError(RuntimeError):
    def __init__(self, evidence, cause, *, uncertain, verified=False):
        self.evidence = str(evidence) if evidence is not None else None
        self.uncertain = uncertain
        self.verified = verified
        if verified:
            message = (
                "Key assignments were updated, but the update record "
                "could not be completed. Further writes are blocked; the update needs inspection."
            )
        elif uncertain:
            message = (
                "The assignment upload or activation may be incomplete. Do not retry, restore, reset, "
                "or power-cycle automatically. Further writes are blocked; the update needs inspection."
            )
        else:
            message = "The write stopped before upload. Read the keyboard again before preparing another review."

        super().__init__(f"{message}{lighting.evidence_details(evidence)} Cause: {cause}")


@dataclass(frozen=True)
class AssignmentWriteResult:
    channels: tuple[bytes, ...]
    lock_indicators: reader.LockIndicators


def execute(before, reviewed, draft, identity, output: Path | None):
    validate_channels(before)
    before = bytes(before[0])
    draft, identity = deepcopy(draft), deepcopy(identity)
    after = candidate(before, draft)
    if after != reviewed:
        raise ValueError("The assignment candidate no longer matches its review")

    lib = lighting.native_library()
    try:
        if output is not None:
            lighting.make_directory(output)
            lighting.durable(output / "expected-channel-0.bin", before)
            lighting.durable(output / "candidate-channel-0.bin", after)
            lighting.durable(output / "planned-write-set-reports.bin", lighting.stream(after, 0))
            lighting.save_json(
                output / "plan.json",
                {"identity": identity, "draft": draft, "candidate_sha256": lighting.digest(after),
                    "expected_sha256": [lighting.digest(before)], "automatic_write_retries": 0},
            )
    except Exception as error:
        raise AssignmentWriteError(output, error, uncertain=False) from error

    result = lighting.WriteResult()
    status = {"format": "c989-linux-assignment-write-result-v2", "result": "stopped", "assignment_preflight": False,
        "assignment_postflight": False, "automatic_write_retries": 0, "candidate_sha256": lighting.digest(after)}
    failure = None
    try:
        with (reader.operation_lock(), reader.diagnostic_log(output, "exchanges.jsonl") as log,
            reader.diagnostic_log(output, "journal.jsonl") as journal):
            if output is not None:
                os.fsync(log.fileno())
                os.fsync(journal.fileno())
                lighting.sync_directory(output)

            with reader.open_keyboard(identity) as fd:
                transport = reader.Transport(fd, log)
                for label, expected in (("before", before), ("after", after)):
                    captured, reports, checksum, _ = transport.read(0, expected)
                    if output is not None:
                        lighting.durable(output / f"{label}-channel-0-reports.bin", reports)
                        lighting.durable(output / f"{label}-channel-0.bin", captured)

                    if captured != expected or checksum != sum(expected):
                        raise ValueError(f"Exact {label} channel-0 state does not match")

                    status["assignment_preflight" if label == "before" else "assignment_postflight"] = True
                    if label == "before":
                        lock_indicators = transport.read_lock_indicators()
                        status["lock_indicators"] = lock_indicators.raw_value
                        if output is not None:
                            lighting.durable(output / "lock-indicators.bin", bytes((lock_indicators.raw_value,)))

                        transport.unlock()
                        lighting.upload(transport, after, result, journal, lib, channel=0)

                status.update(result="passed", feature_exchanges=transport.sequence - transport.lock_reads,
                    output_report_reads=transport.lock_reads)
    except BaseException as error:
        failure = error
        status["error"] = str(error)

    status.update({name: getattr(result, name) for name, _ in result._fields_})
    try:
        if output is not None:
            lighting.save_json(output / "result.json", status)
    except Exception as error:
        if failure is None:
            failure = error
        else:
            failure.add_note(f"Could not save final evidence: {error}")

    if failure is not None:
        if not isinstance(failure, Exception):
            raise failure

        raise AssignmentWriteError(output, failure, uncertain=result.stage >= 1,
            verified=status["assignment_postflight"]) from failure

    return AssignmentWriteResult((after,), lock_indicators)


class AssignmentService:
    def __init__(self, directory, *, guard=None, logging=False):
        self.logging = logging
        self.directory = Path(directory)
        self.guard = guard if guard is not None else WriteGuard()
        self.baseline = self.identity = self.pending = None
        self.lock_indicators = None
        self.mutex = threading.Lock()

    def folder(self, label):
        if not self.logging:
            return None

        lighting.make_directory(self.directory, exist_ok=True)
        return self.directory / f"{time.time_ns()}-{label}-{secrets.token_hex(3)}"

    def read(self):
        with self.mutex:
            self.baseline = self.identity = self.pending = None
            self.lock_indicators = None
            output = self.folder("capture")
            captured = reader.capture_state(output, (0,))
            images = (bytes(captured.images[0]),)
            validate_channels(images)
            self.baseline, self.identity = images, deepcopy(captured.metadata["identity"])
            self.lock_indicators = captured.lock_indicators
            return images

    def plan(self, draft):
        with self.mutex:
            self.guard.check()
            self.pending = None
            if self.baseline is None:
                raise ValueError("Read assignments before reviewing a keyboard write")

            validate_channels(self.baseline)
            before = tuple(bytes(image) for image in self.baseline)
            draft = deepcopy(draft)
            after = candidate(before[0], draft)
            token = secrets.token_hex(24)
            changed = [
                source
                for source in parse_draft(draft)
                if after[source * 4 : source * 4 + 4] != before[0][source * 4 : source * 4 + 4]
            ]
            review = {"token": token, "draft": draft, "changedKeys": changed,
                "changedBytes": sum(a != b for a, b in zip(before[0], after)), "identity": deepcopy(self.identity)}
            self.pending = (token, before, after, deepcopy(self.identity), deepcopy(draft))
            return review

    def apply(self, token, current_draft):
        with self.mutex:
            self.guard.check()
            plan, self.pending = self.pending, None
            if plan is None or not isinstance(token, str) or not secrets.compare_digest(token, plan[0]):
                raise ValueError("This assignment review is missing or expired; prepare a new review")

            if current_draft != plan[4]:
                raise ValueError("The current assignment draft no longer matches the reviewed plan")

            _, before, after, identity, draft = plan
            output = self.folder("write")
            self.baseline = self.identity = None
            self.lock_indicators = None
            try:
                verified = execute(before, after, draft, identity, output)
            except AssignmentWriteError as error:
                if error.uncertain:
                    self.guard.block(error.evidence)

                raise
            except Exception:
                # An unclassified handoff failure cannot establish that upload never began.
                self.guard.block(output)
                raise

            if (
                not isinstance(verified, AssignmentWriteResult)
                or verified.channels != (after,)
                or not isinstance(verified.lock_indicators, reader.LockIndicators)
            ):
                self.guard.block(output)
                raise AssignmentWriteError(output, "The result did not authenticate the reviewed assignments",
                    uncertain=True)

            self.baseline, self.identity = verified.channels, identity
            self.lock_indicators = verified.lock_indicators
            return self.baseline
