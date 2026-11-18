"""Legacy lighting candidates and Linux adapter for the shared native writer.

Candidate encoding follows LightingCandidateBuilder in LightingWorkflow.swift.
The key map is the existing hardware-validated reference, not a second mapping.
"""

from __future__ import annotations

import ctypes as C
import hashlib
import json
import os
import re
import secrets
import threading
import time
from copy import deepcopy
from pathlib import Path

import keyboardlights.device.capture as reader
from keyboardlights.device.activity import WriteGuard
from keyboardlights.device.core import (JOURNAL, PAUSE, SEND, WriteIO, WriteResult, native_library,
    validate_lighting_changes)
from keyboardlights.device.core import validate_lighting as validate
from keyboardlights.resources import data_file

KEY_MAP = data_file("layout.json")
KEYS = [slot for slot in json.loads(KEY_MAP.read_text())["slots"] if slot["physical_key"]]
PHYSICAL = {key["led_index"] for key in KEYS}
MODES = [
    {"tag": tag, "name": name, "generatedOnly": tag in (7, 9), "direction": tag == 11}
    for tag, name in enumerate(
        ("Colourful", "Breath", "Spectrum", "Volcano", "Rolling", "Rotating", "Flowing", "Pulsating", "Tilt"), 7)
]
MODE_BY_TAG = {mode["tag"]: mode for mode in MODES}
LEGACY_COLORS = ["#FFFFFF", "#00FFFF", "#FF0000", "#FFFF00", "#0000FF", "#FF00FF"]


def uses_custom_color(settings):
    return settings["style"] != "animation" or (
        not MODE_BY_TAG[settings["mode"]]["generatedOnly"] and not settings["generated"]
    )


def digest(body):
    return hashlib.sha256(body).hexdigest()


def sync_directory(path: Path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def make_directory(path: Path, *, exist_ok=False):
    """Persist each newly created directory entry, including missing parents."""
    if not path.parent.is_dir():
        make_directory(path.parent, exist_ok=True)

    path.mkdir(exist_ok=exist_ok)
    sync_directory(path.parent)


def durable(path: Path, body: bytes):
    with path.open("xb") as file:
        file.write(body)
        file.flush()
        os.fsync(file.fileno())

    sync_directory(path.parent)


def save_json(path, data):
    durable(path, (json.dumps(data, indent=2) + "\n").encode())


def integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"Expected an integer from {low} to {high}")

    return value


def rgb(value):
    if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        raise ValueError("Color must be #RRGGBB")

    return bytes.fromhex(value[1:])


def candidate(before: bytes, settings: dict) -> bytes:
    validate(before)
    if not isinstance(settings, dict):
        raise ValueError("Lighting settings must be an object")

    style = settings.get("style")
    brightness = integer(settings.get("brightness"), 1 if style == "animation" else 0, 16)
    body = bytearray(before)
    if style == "animation":
        tag = integer(settings.get("mode"), 7, 15)
        speed = integer(settings.get("speed"), 2, 16)
        direction = integer(settings.get("direction", 0), 0, 1)
        generated = settings.get("generated", True)
        if type(generated) is not bool:
            raise ValueError("Generated color must be a boolean")

        record = bytearray(16)
        record[0] = tag
        if not MODE_BY_TAG[tag]["generatedOnly"]:
            if generated:
                record[8] = 1
            else:
                record[1:4] = rgb(settings.get("color"))

        record[9:12] = bytes((brightness, speed, direction if MODE_BY_TAG[tag]["direction"] else 0))
        record[14:16] = b"\xaa\x55"
        body[0x440:0x450] = record
    elif style in ("solid", "perKey"):
        if style == "solid":
            color = rgb(settings.get("color"))
            for index in PHYSICAL:
                body[0x200 + index * 4 : 0x204 + index * 4] = b"\x80" + color
        else:
            slots = settings.get("slots")
            if not isinstance(slots, list) or len(slots) != 144:
                raise ValueError("Per-key lighting requires 144 slots")

            for index in PHYSICAL:
                slot = slots[index]
                if not isinstance(slot, dict) or type(slot.get("enabled")) is not bool:
                    raise ValueError("Invalid per-key slot")

                body[0x200 + index * 4 : 0x204 + index * 4] = bytes((128 if slot["enabled"] else 0,)) + rgb(
                    slot.get("color")
                )

        body[0x440] = 128
        body[0x449] = brightness
    else:
        raise ValueError("Choose Solid color, Animation, or Per-key")

    allowed_changes(before, body)
    return bytes(body)


def allowed_changes(before, after):
    validate(before)
    validate(after)
    changes = [offset for offset, (a, b) in enumerate(zip(before, after)) if a != b]
    for offset in changes:
        # Product surface is narrower than the Mac raw writer: preserve all
        # stored presets, reserved slots, and the selected-record tail.
        if not (0x440 <= offset < 0x450 or 0x200 <= offset < 0x440 and (offset - 0x200) // 4 in PHYSICAL):
            raise ValueError(f"Lighting mutation outside allowed fields: {offset:03X}")

    if not changes:
        raise ValueError("Lighting already matches these settings")

    validate_lighting_changes(before, after)
    return changes


def stream(body, channel=1):
    if channel not in (0, 1) or len(body) != reader.BLOCKS[channel] * 64:
        raise ValueError("Invalid configuration write image")

    return (
        reader.command(0x11 if channel == 0 else 0x13, reader.BLOCKS[channel])
        + body
        + reader.command(2)
        + reader.command(0xF0)
    )


def evidence_details(evidence):
    return f" Records: {evidence}." if evidence is not None else ""


class UncertainWriteError(RuntimeError):
    def __init__(self, evidence, cause):
        self.evidence = str(evidence) if evidence is not None else None
        super().__init__(
            "The lighting result is uncertain: activation may already have reached the keyboard. "
            "Do not retry, restore, reset, or power-cycle automatically; retrying may repeat an "
            f"already applied update.{evidence_details(evidence)} Cause: {cause}"
        )


class IncompleteUploadError(UncertainWriteError):
    def __init__(self, evidence, cause):
        self.evidence = str(evidence) if evidence is not None else None
        RuntimeError.__init__(
            self,
            "The lighting upload may be incomplete. Do not retry or restore automatically; "
            f"the update needs inspection.{evidence_details(evidence)} Cause: {cause}",
        )


class VerifiedWriteEvidenceError(RuntimeError):
    def __init__(self, evidence, cause):
        self.evidence = str(evidence) if evidence is not None else None
        RuntimeError.__init__(
            self,
            "Lighting was applied and verified, but saving evidence failed. "
            f"Further writes are blocked.{evidence_details(evidence)} Cause: {cause}",
        )


def upload(transport, body, result, journal, lib, *, channel=1):
    """C owns sequencing/timing. Every outgoing payload must match the plan."""
    packed = stream(body, channel)
    planned = tuple(packed[offset : offset + 64] for offset in range(0, len(packed), 64))
    sent = 0
    failure = None

    def guarded(function, fallback):
        def call(*args):
            nonlocal failure
            if failure is not None:
                return fallback

            try:
                return function(*args)
            except BaseException as error:
                failure = error
                return fallback

        return call

    def send(_, pointer, length):
        nonlocal sent
        if length != 64:
            raise ValueError("Invalid native send length")

        packet = C.string_at(pointer, length)
        if sent >= len(planned) or packet != planned[sent]:
            raise ValueError("Native write stream differs from reviewed plan")

        sent += 1
        transport._exchange(packet)
        return 1

    def receive(_, pointer, length):
        if length != 64:
            raise ValueError("Invalid native receive length")

        packet = transport._exchange()
        if len(packet) != length:
            raise ValueError("Short native receive report")

        C.memmove(pointer, packet, length)
        return length

    def pause(_, microseconds):
        time.sleep(microseconds / 1_000_000)

    def record(_, event, block):
        if journal is None:
            return 1

        journal.write(json.dumps({"event": event.decode(), "block": block, "monotonic_ns": time.monotonic_ns()}) + "\n")
        journal.flush()
        os.fsync(journal.fileno())
        return 1

    io = WriteIO(None, SEND(guarded(send, 0)), SEND(guarded(receive, -1)), PAUSE(guarded(pause, None)),
        JOURNAL(guarded(record, 0)))
    buffer = (C.c_uint8 * len(body)).from_buffer_copy(body)
    writer = lib.c989_assignments_upload if channel == 0 else lib.c989_lighting_upload
    ok = writer(C.byref(io), buffer, len(body), C.byref(result))
    if failure is not None:
        raise failure

    if not ok or sent != len(planned):
        raise ValueError(f"Write stopped at stage {result.stage}, block {result.last_attempted_block}; no retry")


def execute(before, after, identity, output: Path | None):
    changes = allowed_changes(before, after)
    lib = native_library()  # Resolve before opening hardware or creating output.
    if output is not None:
        make_directory(output)
        durable(output / "expected-channel-1.bin", before)
        durable(output / "candidate-channel-1.bin", after)
        durable(output / "planned-write-set-reports.bin", stream(after))
        save_json(
            output / "plan.json",
            {"identity": identity, "expected_sha256": digest(before), "candidate_sha256": digest(after),
                "changed_bytes": len(changes), "automatic_retries": 0},
        )

    result = WriteResult()
    status = {"result": "stopped", "automatic_retries": 0, "lighting_preflight": False, "lighting_postflight": False,
        "candidate_sha256": digest(after)}
    failure = None
    try:
        with (reader.operation_lock(), reader.diagnostic_log(output, "exchanges.jsonl") as log,
            reader.diagnostic_log(output, "journal.jsonl") as journal):
            if output is not None:
                os.fsync(log.fileno())
                os.fsync(journal.fileno())
                sync_directory(output)

            with reader.open_keyboard(identity) as fd:
                transport = reader.Transport(fd, log)
                for label, expected in (("before", before), ("after", after)):
                    image, reports, checksum, _ = transport.read(1, expected)
                    if output is not None:
                        durable(output / f"{label}-channel-1-reports.bin", reports)
                        durable(output / f"{label}-channel-1.bin", image)

                    if image != expected or checksum != sum(expected):
                        raise ValueError(
                            f"Exact {label} lighting state does not match; capture again before proceeding"
                        )

                    status["lighting_preflight" if label == "before" else "lighting_postflight"] = True
                    if label == "before":
                        lock_indicators = transport.read_lock_indicators()
                        status["lock_indicators"] = lock_indicators.raw_value
                        if output is not None:
                            durable(output / "lock-indicators.bin", bytes((lock_indicators.raw_value,)))

                        transport.unlock()
                        upload(transport, after, result, journal, lib)

                status.update(result="passed", feature_exchanges=transport.sequence - transport.lock_reads,
                    output_report_reads=transport.lock_reads)
    except BaseException as error:
        status["error"] = str(error)
        failure = error

    status.update({name: getattr(result, name) for name, _ in result._fields_})
    try:
        if output is not None:
            save_json(output / "result.json", status)
    except Exception as error:
        if failure is None:
            failure = error
        else:
            failure.add_note(f"Could not save final evidence: {error}")

    if failure is not None:
        if isinstance(failure, Exception) and result.activation_attempted:
            if status["lighting_postflight"]:
                raise VerifiedWriteEvidenceError(output, failure) from failure

            raise UncertainWriteError(output, failure) from failure

        if isinstance(failure, Exception) and result.stage >= 1:
            raise IncompleteUploadError(output, failure) from failure

        raise failure

    return status


def image_state(body: bytes | None):
    """Decode the captured fields used by the native editor."""
    return {
        "available": body is not None,
        "slots": [
            {"enabled": body[0x200 + i * 4] == 128, "color": "#" + body[0x201 + i * 4 : 0x204 + i * 4].hex().upper()}
            for i in range(144)
        ]
        if body is not None
        else [],
        "selected": {"tag": body[0x440], "brightness": body[0x449], "speed": body[0x44A],
            "generated": bool(body[0x448]), "direction": body[0x44B], "color": "#" + body[0x441:0x444].hex().upper()}
        if body is not None
        else None,
    }


class LightingService:
    """One live baseline and one single-use reviewed plan per local GUI."""

    def __init__(self, directory: Path, *, guard=None, logging=False):
        self.logging = logging
        self.directory = directory
        self.baseline = self.identity = self.pending = None
        self.lock_indicators = None
        self.mutex = threading.Lock()
        self.guard = guard if guard is not None else WriteGuard()

    def folder(self, label):
        if not self.logging:
            return None

        make_directory(self.directory, exist_ok=True)
        return self.directory / f"{time.time_ns()}-{label}-{secrets.token_hex(3)}"

    def read(self):
        with self.mutex:
            self.baseline = self.pending = self.identity = None
            self.lock_indicators = None
            output = self.folder("capture")
            captured = reader.capture_state(output, (1,))
            body = bytes(captured.images[1])
            validate(body)
            self.baseline, self.identity = body, deepcopy(captured.metadata["identity"])
            self.lock_indicators = captured.lock_indicators
            return self.state()

    def state(self):
        return image_state(self.baseline)

    def plan(self, settings):
        with self.mutex:
            self.guard.check()
            self.pending = None
            if self.baseline is None:
                raise ValueError("Read the keyboard before preparing a lighting change")

            settings = deepcopy(settings)
            before = bytes(self.baseline)
            after = candidate(before, settings)
            native_library()
            token = secrets.token_hex(24)
            review = {"token": token, "changedBytes": sum(a != b for a, b in zip(before, after)),
                "candidateSha256": digest(after), "settings": settings}
            self.pending = (token, before, bytes(after), deepcopy(self.identity))
            return review

    def apply(self, token):
        with self.mutex:
            self.guard.check()
            plan, self.pending = self.pending, None
            if plan is None or not isinstance(token, str) or not secrets.compare_digest(token, plan[0]):
                raise ValueError("This review is missing or expired; prepare a new review")

            _, before, after, identity = plan
            output = self.folder("write")
            self.baseline = self.identity = None
            self.lock_indicators = None
            try:
                result = execute(before, after, identity, output)
            except (UncertainWriteError, VerifiedWriteEvidenceError) as error:
                self.guard.block(error.evidence)
                raise

            if not isinstance(result, dict) or "lock_indicators" not in result:
                self.guard.block(output)
                raise UncertainWriteError(output, "The lighting result is missing its lock indicator snapshot")

            try:
                lock_indicators = reader.LockIndicators(result["lock_indicators"])
            except (TypeError, ValueError) as error:
                self.guard.block(output)
                raise VerifiedWriteEvidenceError(output, error) from error

            self.baseline, self.identity = after, identity
            self.lock_indicators = lock_indicators
            return self.state()
