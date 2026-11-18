"""Portable lighting profile metadata and an adapter for shared file actions."""

import json
import unicodedata
import uuid
from copy import deepcopy

from keyboardlights.assignments.model import MAX_PROFILE_NAME_BYTES, MAX_PROFILE_NAME_LENGTH
from keyboardlights.lighting.editor import parse_lighting_draft
from keyboardlights.profiles.io import MAX_DRAFT_BYTES
from keyboardlights.profiles.store import ProfileStore


def profile(name, draft):
    return parse_profile(
        {"format": "c989-lighting-draft-v1", "id": str(uuid.uuid4()).upper(), "name": name.strip(), "settings": draft}
    )


def parse_profile(value):
    if len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()) > MAX_DRAFT_BYTES:
        raise ValueError("Lighting drafts must be smaller than 128 KB")

    settings = parse_lighting_draft(value)
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
        raise ValueError("Invalid lighting profile identity") from error

    return {"format": "c989-lighting-draft-v1", "id": identifier, "name": name, "settings": settings}


def decode_profile(text):
    if len(text.encode()) > MAX_DRAFT_BYTES:
        raise ValueError("Lighting drafts must be smaller than 128 KB")

    value = json.loads(text)

    # Older exported lighting drafts had no library name or identifier.
    if isinstance(value, dict) and "id" not in value and "name" not in value:
        return profile("Lighting profile", parse_lighting_draft(value))

    return parse_profile(value)


class LightingProfiles(ProfileStore):
    def __init__(self, directory):
        super().__init__(directory, "lighting-profiles.json", parse_profile)


class LightingProfileEditor:
    """Expose draft operations without changing capture or hardware write behavior."""

    def __init__(self, editor):
        self.editor = editor
        self._saved = None
        self._saved_profile_id = None
        self._saved_lost = False

    @property
    def draft(self):
        return self.editor.snapshot_draft()

    @property
    def unsaved(self):
        if self.editor.has_unexported_edits() or self._saved_lost:
            return True

        if self.editor.draft is None:
            return False

        return self.editor.draft != self._saved if self._saved is not None else self.editor.has_pending_edits()

    def saved(self, draft, *, profile_id=None):
        self._saved = deepcopy(draft)
        self._saved_profile_id = profile_id
        self._saved_lost = False

    def forget_saved(self):
        self._saved = None
        self._saved_profile_id = None
        self._saved_lost = True

    def reconcile_saved_profiles(self, profiles):
        if self._saved_profile_id is not None and not any(
            item["id"] == self._saved_profile_id and item["settings"] == self._saved for item in profiles
        ):
            self.forget_saved()

    def load(self, draft):
        self.editor.import_draft(
            json.dumps({"format": "c989-lighting-draft-v1", "settings": draft}, ensure_ascii=False,
                separators=(",", ":"))
        )

    def clear(self):
        self.editor.select_mode(128)
        self.editor.clear_paint()
