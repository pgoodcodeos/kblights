"""Portable assignment drafts and profiles, matching the Mac editor's records."""

import json
import unicodedata
import uuid
from copy import deepcopy
from dataclasses import dataclass

import keyboardlights.lighting.service as lighting
from keyboardlights.resources import data_file

MAX_PROFILE_BYTES = 128_000
MAX_PROFILE_NAME_LENGTH = 80
MAX_PROFILE_NAME_BYTES = 320
KEYS = {key["led_index"]: key for key in lighting.KEYS}
CATALOG = json.loads(data_file("assignments.json").read_text())["targets"]
TARGETS = {kind: {target["id"]: target for target in entries} for kind, entries in CATALOG.items()}
KINDS = ("factoryDefault", "keyboard", "mouse", "media", "disabled")
KIND_NAMES = ("Factory default", "Keyboard key / shortcut", "Mouse", "Media", "Disable key")
MODIFIERS = ((1, "Left Ctrl"), (2, "Left Shift"), (4, "Left Alt"), (8, "Left Super / GUI"), (16, "Right Ctrl"),
    (32, "Right Shift"), (64, "Right Alt"), (128, "Right Super / GUI"))


def target_label(target):
    return target["name"].replace("Command", "Super / GUI").replace("Option", "Alt")


def source_key(source):
    if type(source) is not int or source not in KEYS:
        raise ValueError("Choose a physical C989 key")

    return KEYS[source]


@dataclass(frozen=True)
class Action:
    kind: str
    target: int | None = None
    modifiers: int = 0

    def record(self):
        lighting.integer(self.modifiers, 0, 255)
        if self.kind in ("factoryDefault", "disabled"):
            if self.target is not None or self.modifiers:
                raise ValueError("Default and disabled actions cannot have a target or modifiers")

            return bytes(4) if self.kind == "factoryDefault" else bytes((5, 3, 0, 0))

        if self.kind not in TARGETS or type(self.target) is not int or self.target not in TARGETS[self.kind]:
            raise ValueError("Choose a supported assignment output")

        if self.kind != "keyboard" and self.modifiers:
            raise ValueError("Only keyboard actions support modifiers")

        record = bytearray(TARGETS[self.kind][self.target]["bytes"])
        if self.kind == "keyboard":
            record[1] |= self.modifiers

        return bytes(record)

    @property
    def intrinsic_modifiers(self):
        return TARGETS["keyboard"][self.target]["bytes"][1] if self.kind == "keyboard" else 0

    @property
    def summary(self):
        self.record()
        if self.kind not in TARGETS:
            return KIND_NAMES[KINDS.index(self.kind)]

        name = target_label(TARGETS[self.kind][self.target])
        extra = self.modifiers & ~self.intrinsic_modifiers if self.kind == "keyboard" else 0
        return " + ".join([label for bit, label in MODIFIERS if extra & bit] + [name])

    def as_dict(self):
        value = {"kind": self.kind, "modifiers": self.modifiers}
        if self.target is not None:
            value["target"] = self.target

        return value

    @classmethod
    def parse(cls, value):
        if not isinstance(value, dict) or not isinstance(value.get("kind"), str):
            raise ValueError("Invalid assignment action")

        action = cls(value["kind"], value.get("target"), value.get("modifiers", 0))
        action.record()
        return action


def parse_draft(value):
    if not isinstance(value, dict) or not isinstance(value.get("assignments"), list):
        raise ValueError("Invalid assignment draft")

    entries = value["assignments"]
    if len(entries) > len(KEYS):
        raise ValueError("Too many assignment changes")

    result = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Invalid assignment entry")

        source = entry.get("source")
        source_key(source)
        if source in result:
            raise ValueError("An assignment draft cannot contain duplicate keys")

        result[source] = Action.parse(entry.get("action"))

    return result


def draft_dict(actions):
    return {
        "assignments": [{"source": source, "action": action.as_dict()} for source, action in sorted(actions.items())]
    }


def validate_baseline(body):
    if len(body) != 640 or body[636:640] not in (bytes(4), b"\0\0\xaa\x55"):
        raise ValueError("The captured assignment validity word is not recognized")

    if body[638] == 0 and any(body):
        raise ValueError("The inactive assignment table contains unknown data; inspect the capture before writing")


def python_candidate(before, draft):
    validate_baseline(before)
    actions = parse_draft(draft)
    if not actions:
        raise ValueError("Stage at least one assignment before reviewing")

    after = bytearray(before)
    for source, action in actions.items():
        after[source * 4 : source * 4 + 4] = action.record()

    after[638:640] = b"\xaa\x55"
    return bytes(after)


def describe_assignment(table, source):
    source_key(source)
    if table is None or len(table) != 640:
        return "Not loaded"

    table = bytes(table)
    if table[636:638] != bytes(2) or table[638:] not in (bytes(2), b"\xaa\x55"):
        return "Unrecognized assignment table (preserved unless you change it)"

    if table[638:] == bytes(2):
        return "Factory default" if table == bytes(640) else "Inactive assignment table with unknown data"

    return describe_record(table[source * 4 : source * 4 + 4])


def describe_record(record):
    record = bytes(record)
    if record == bytes(4):
        return "Factory default"

    if record == bytes((5, 3, 0, 0)):
        return "Disabled"

    if len(record) == 4 and record[0] == 2 and record[3] == 0:
        if record[2] in TARGETS["keyboard"]:
            action = Action("keyboard", record[2], record[1])
            if action.record() == record:
                return action.summary

        if record[2] == 0 and record[1]:
            return " + ".join(label for bit, label in MODIFIERS if record[1] & bit)

    for kind in ("mouse", "media"):
        for target in CATALOG[kind]:
            if record == bytes(target["bytes"]):
                return target["name"]

    return "Custom assignment (unrecognized)"


def profile_filename(name):
    stem = name.replace("/", "-").replace("\\", "-").strip(" .")
    return (stem or "Key assignments") + ".json"


def offline_plan(draft):
    actions = parse_draft(draft)
    return {
        "format": "c989-key-assignment-offline-plan-v1",
        "hardwareValidated": False,
        "channel": 0,
        "bodySize": 640,
        "requiredZeroOffsets": [636, 637],
        "trailerOffset": 638,
        "trailerBytes": [170, 85],
        "semantics": "Listed key records and the trailer change; unlisted keys and other bytes retain captured values. No device commands are included.",
        "patches": [
            {"source": source, "key": KEYS[source]["name"], "action": action.summary, "offset": source * 4,
                "bytes": list(action.record())}
            for source, action in sorted(actions.items())
        ],
    }


def profile(name, draft):
    value = {"format": "c989-profile-v2", "id": str(uuid.uuid4()).upper(), "name": name.strip(),
        "keyAssignments": deepcopy(draft)}
    return parse_profile(value)


def parse_profile(value):
    if not isinstance(value, dict) or value.get("format") != "c989-profile-v2":
        raise ValueError("Choose a KeyboardLights v2 profile containing key assignments")

    name = value.get("name")
    if (
        not isinstance(name, str)
        or not name.strip()
        or len(name) > MAX_PROFILE_NAME_LENGTH
        or len(name.encode()) > MAX_PROFILE_NAME_BYTES
        or any(unicodedata.category(c) == "Cc" for c in name)
    ):
        raise ValueError("Use a profile name between 1 and 80 characters")

    try:
        identifier = str(uuid.UUID(value["id"])).upper()
    except (KeyError, ValueError, TypeError, AttributeError) as error:
        raise ValueError("Invalid assignment profile identity") from error

    actions = parse_draft(value.get("keyAssignments"))
    return {"format": "c989-profile-v2", "id": identifier, "name": name, "keyAssignments": draft_dict(actions)}


def decode_profile(text):
    if len(text.encode()) > MAX_PROFILE_BYTES:
        raise ValueError("Assignment profiles must be smaller than 128 KB")

    return parse_profile(json.loads(text))
