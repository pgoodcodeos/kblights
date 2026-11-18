"""Offline assignment editing; captures never replace the user's planned changes."""

import json
from copy import deepcopy
from dataclasses import replace

from keyboardlights.assignments.model import (CATALOG, Action, describe_assignment, draft_dict, offline_plan,
    parse_draft, python_candidate, source_key)


class AssignmentEditor:
    def __init__(self, service=None):
        self.service = service
        self.actions = {}
        self.selected_source = 39
        self.review = None
        self._saved = self.draft
        self._saved_profile_id = None
        self._change_cache = None
        self._description_cache = None

    @property
    def draft(self):
        return draft_dict(self.actions)

    @property
    def selected_action(self):
        return self.actions.get(self.selected_source)

    @property
    def unsaved(self):
        return self.draft != self._saved

    def current_description(self, source):
        """Reuse decoded records only while the immutable captured table is unchanged."""
        source_key(source)
        baseline = self.service.baseline if self.service is not None else None
        table = baseline[0] if baseline is not None else None
        if type(table) is not bytes:
            return describe_assignment(table, source)

        if self._description_cache is None or self._description_cache[0] is not table:
            self._description_cache = (table, {})

        descriptions = self._description_cache[1]
        if source not in descriptions:
            descriptions[source] = describe_assignment(table, source)

        return descriptions[source]

    def saved(self, draft, *, profile_id=None):
        self._saved = deepcopy(draft)
        self._saved_profile_id = profile_id

    def forget_saved(self):
        self._saved = None
        self._saved_profile_id = None

    def reconcile_saved_profiles(self, profiles):
        if self._saved_profile_id is not None and not any(
            value["id"] == self._saved_profile_id and value["keyAssignments"] == self._saved for value in profiles
        ):
            self.forget_saved()

    def select(self, source):
        source_key(source)
        self.selected_source = source

    def set(self, action, source=None):
        source = self.selected_source if source is None else source
        source_key(source)
        if action is not None:
            action.record()

        self.review = None
        if action is None:
            self.actions.pop(source, None)
        else:
            self.actions[source] = action

    def change_kind(self, kind):
        if self.selected_action is not None and self.selected_action.kind == kind:
            return

        target = CATALOG[kind][0]["id"] if kind in CATALOG else None
        self.set(Action(kind, target) if kind is not None else None)

    def change_target(self, target):
        if self.selected_action is None:
            raise ValueError("Choose an assignment action first")

        self.set(replace(self.selected_action, target=target))

    def set_modifier(self, bit, enabled):
        action = self.selected_action
        if action is None or action.kind != "keyboard" or bit not in (1, 2, 4, 8, 16, 32, 64, 128):
            raise ValueError("Only keyboard actions support modifiers")

        modifiers = (action.modifiers | bit if enabled else action.modifiers & ~bit) & ~action.intrinsic_modifiers
        self.set(replace(action, modifiers=modifiers))

    def clear(self):
        self.actions = {}
        self.review = None

    def load(self, draft):
        actions = parse_draft(draft)
        self.actions = actions
        self.review = None
        if self.selected_source not in actions and actions:
            self.selected_source = min(actions)

    def has_changes(self):
        """Compare effective bytes once per immutable capture and action snapshot."""
        if not self.actions:
            return False

        baseline = self.service.baseline if self.service is not None else None
        if baseline is None:
            return True

        image = baseline[0]
        signature = tuple(sorted(self.actions.items()))

        # Include the actual immutable actions, so even direct draft replacement
        # cannot reuse a result from an older draft. Selection is irrelevant.
        cacheable = type(image) is bytes
        if cacheable and self._change_cache is not None:
            cached_image, cached_signature, changed = self._change_cache
            if image is cached_image and signature == cached_signature:
                return changed

        try:
            changed = python_candidate(image, self.draft) != image
        except ValueError:
            changed = True  # Invalid baselines must be explained by review, never discarded silently.

        if cacheable:
            self._change_cache = (image, signature, changed)

        return changed

    def read(self):
        if self.service is None:
            raise ValueError("Device access is disabled in offline preview")

        self.review = None
        return self.service.read()

    def prepare(self):
        self.review = None
        if self.service is None:
            raise ValueError("Read assignments before reviewing a write")

        self.review = self.service.plan(self.draft)
        return self.review

    def apply(self, confirmed=False):
        if confirmed is not True or self.service is None or self.review is None or self.review["draft"] != self.draft:
            self.review = None
            raise ValueError("Review and confirm the current assignment draft first")

        token = self.review["token"]
        draft = self.draft
        self.review = None
        result = self.service.apply(token, draft)
        self.saved(draft)
        return result

    def export_plan(self):
        return json.dumps(offline_plan(self.draft), indent=2) + "\n"
