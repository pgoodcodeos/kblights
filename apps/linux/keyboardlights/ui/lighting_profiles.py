"""Lighting uses the same profile file actions as key assignments."""

from keyboardlights.profiles.io import MAX_DRAFT_BYTES
from keyboardlights.profiles.lighting import LightingProfileEditor, decode_profile, profile
from keyboardlights.ui.profiles import ProfilePage


class LightingProfilePage(ProfilePage):
    payload = "settings"
    file_limit = MAX_DRAFT_BYTES
    make_profile = staticmethod(profile)
    decode_profile = staticmethod(decode_profile)

    def __init__(self, window):
        super().__init__(window, LightingProfileEditor(window.editor), "Lighting Profiles", "lighting")
        self.update_actions()

    @property
    def can_edit(self):
        return self.window.editor.draft is not None

    @property
    def draft_available(self):
        return self.can_edit and not self.window.editor.has_unpainted()

    def invalidate_review(self):
        self.window.invalidate_review()

    def render(self):
        self.window.render_draft()
