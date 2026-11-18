"""Shared per-key status and staged editing for both keyboard modes."""

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from keyboardlights.assignments.model import KEYS
from keyboardlights.ui.presentation import key_lighting_description


class KeyInspector(Gtk.Box):
    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10, css_classes=["card", "key-details"])
        self.window = window
        self.loading = False
        self._capture_state = None
        self._capture_preview = None
        heading = Gtk.Box(spacing=12)
        self.title = Gtk.Label(xalign=0, hexpand=True, css_classes=["section-title"])
        heading.append(self.title)
        self.append(heading)
        self.sources = list(KEYS)
        self.source = Gtk.DropDown.new_from_strings([KEYS[source]["name"] for source in self.sources])
        self.source.set_enable_search(True)
        self.source.connect("notify::selected", self.source_changed)
        self.source.update_property([Gtk.AccessibleProperty.LABEL], ["Physical key"])
        heading.append(self.source)
        details = Gtk.Box(spacing=20)
        self.append(details)
        self.lighting_details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, valign=Gtk.Align.START,
            hexpand=True)
        self.assignment_details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, valign=Gtk.Align.START,
            hexpand=True)
        details.append(self.lighting_details)
        details.append(self.assignment_details)
        box = self.lighting_details
        box.append(Gtk.Label(label="Lighting", xalign=0, css_classes=["field-label"]))
        self.lighting_status = Gtk.Label(xalign=0, wrap=True, selectable=True)
        box.append(self.lighting_status)
        actions = Gtk.Box(spacing=8)
        self.paint = Gtk.Button(label="Paint selected color")
        self.paint.connect("clicked", lambda *_: self.edit_lighting(revert=False))
        actions.append(self.paint)
        self.revert_lighting = Gtk.Button(label="Revert", tooltip_text="Revert key lighting")
        self.revert_lighting.update_property([Gtk.AccessibleProperty.LABEL], ["Revert key lighting"])
        self.revert_lighting.connect("clicked", lambda *_: self.edit_lighting(revert=True))
        actions.append(self.revert_lighting)
        box.append(actions)
        box = self.assignment_details
        box.append(Gtk.Label(label="Assignment", xalign=0, css_classes=["field-label"]))
        self.assignment_status = Gtk.Label(xalign=0, wrap=True, selectable=True)
        box.append(self.assignment_status)
        self.feedback = Gtk.Label(xalign=0, wrap=True, css_classes=["status-message"])
        self.feedback.set_visible(False)
        self.append(self.feedback)
        box.append(window.assignment_page.settings)
        self.revert_assignment = Gtk.Button(label="Revert", tooltip_text="Revert key assignment", halign=Gtk.Align.START
        )
        self.revert_assignment.update_property([Gtk.AccessibleProperty.LABEL], ["Revert key assignment"])
        self.revert_assignment.connect("clicked",
            lambda *_: window.assignment_page.edit(lambda: window.assignment_page.editor.set(None)))
        window.assignment_page.selectors.append(self.revert_assignment)

    def source_changed(self, *_):
        if not self.loading and not self.window.busy:
            self.feedback.set_visible(False)
            self.window.assignment_page.select(self.sources[self.source.get_selected()])

    def message(self, text, error=False):
        self.feedback.set_text(text)
        self.feedback.set_selectable(error)
        self.feedback.set_visible(error)
        self.window.message(text, error)
        if error:
            self.feedback.add_css_class("error")
        else:
            self.feedback.remove_css_class("error")

    def edit_lighting(self, *, revert):
        window = self.window
        if window.busy or window._closed:
            return

        source = window.assignment_page.editor.selected_source
        try:
            if revert:
                window.editor.revert_key(source)
            elif window.editor.paint_tool == "bucket":
                window.editor.flood_fill(source)
            else:
                window.editor.paint_key(source)

            window.invalidate_review()
            window.render_draft()
            self.message("Lighting preview updated. Your keyboard has not changed.")
        except ValueError as error:
            self.message(str(error), True)

    def render(self):
        window = self.window
        editor = window.assignment_page.editor
        source = editor.selected_source
        self.loading = True
        try:
            self.source.set_selected(self.sources.index(source))
        finally:
            self.loading = False

        self.title.set_text("Selected key: " + KEYS[source]["name"])
        if window.editor.state is not self._capture_state:
            self._capture_preview = window.editor.current_keyboard_preview()
            self._capture_state = window.editor.state

        current = key_lighting_description(self._capture_preview, source)
        staged = key_lighting_description(window.editor.preview_draft(), source, unpainted=window.editor.unpainted)
        self.lighting_status.set_text(current if current == staged else f"On keyboard: {current}\nPreview: {staged}")
        editable = not window.busy and window.editor.draft is not None and window.editor.workspace_style != "animation"
        self.paint.set_sensitive(editable)
        self.paint.set_label("Fill connected keys" if window.editor.paint_tool == "bucket" else "Paint selected color")
        self.revert_lighting.set_sensitive(editable and window.editor.has_key_change(source))
        current_action = editor.current_description(source)
        staged_action = editor.selected_action.summary if editor.selected_action else current_action
        self.assignment_status.set_text(
            current_action
            if current_action == staged_action
            else f"On keyboard: {current_action}\nPreview: {staged_action}"
        )
        self.revert_assignment.set_sensitive(not window.busy and editor.selected_action is not None)
        self.source.set_sensitive(not window.busy)
