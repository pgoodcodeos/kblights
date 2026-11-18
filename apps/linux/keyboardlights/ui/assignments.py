"""Native assignment workspace: offline drafts, profiles, and guarded batch review."""

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from keyboardlights.assignments.editor import AssignmentEditor
from keyboardlights.assignments.model import CATALOG, KIND_NAMES, KINDS, MODIFIERS, target_label
from keyboardlights.ui.presentation import error_message
from keyboardlights.ui.profiles import ProfilePage


class AssignmentPage(ProfilePage):
    def __init__(self, window, service):
        super().__init__(window, AssignmentEditor(service), "Assignment Profiles", "assignment")
        self._selected_source = None
        self._planned_sources = frozenset()
        self._target_kind = None
        self.loading = False
        self.key_buttons = self.window.key_buttons
        self._build_action_settings()
        for widget, name in ((self.kind, "Assignment action"), (self.target, "Assignment output")):
            widget.update_property([Gtk.AccessibleProperty.LABEL], [name])

        self.render()

    def _build_action_settings(self):
        settings = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.settings = settings
        self.kind = Gtk.DropDown.new_from_strings(["Keep current assignment", *KIND_NAMES])
        self.kind.connect("notify::selected", self.action_changed)
        selectors = Gtk.Box(spacing=10)
        self.selectors = selectors
        settings.append(selectors)
        self.kind.set_hexpand(True)
        selectors.append(self.kind)
        self.target = Gtk.DropDown.new_from_strings([])
        self.target.set_enable_search(True)
        self.target.connect("notify::selected", self.target_changed)
        self.target.set_hexpand(True)
        selectors.append(self.target)
        self.modifiers_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.modifiers_box.append(Gtk.Label(label="Shortcut modifiers", xalign=0, css_classes=["field-label"]))
        modifiers = Gtk.Grid(row_spacing=4, column_spacing=10, column_homogeneous=True)
        self.modifiers = {}
        for index, (bit, name) in enumerate(MODIFIERS):
            button = Gtk.CheckButton(label=name.replace(" / GUI", ""))
            button.connect("toggled", self.modifier_changed, bit)
            modifiers.attach(button, index % 4, index // 4, 1, 1)
            self.modifiers[bit] = button

        self.modifiers_box.append(modifiers)
        settings.append(self.modifiers_box)
        self.action_hint = Gtk.Label(xalign=0, wrap=True, max_width_chars=34, css_classes=["hint"])
        settings.append(self.action_hint)

    def update_actions(self):
        busy = self.window.busy
        self.settings.set_sensitive(not busy)
        super().update_actions()

    def render(self):
        editor = self.editor
        self.loading = True
        try:
            source = editor.selected_source
            if self._selected_source is not None and self._selected_source != source:
                self.key_buttons[self._selected_source].set_active(False)

            self._selected_source = source
            planned = frozenset(editor.actions) if editor.has_changes() else frozenset()
            for index in planned ^ self._planned_sources:
                if index in planned:
                    self.key_buttons[index].add_css_class("planned")
                else:
                    self.key_buttons[index].remove_css_class("planned")

            self._planned_sources = planned
            action = editor.selected_action
            self.kind.set_selected(KINDS.index(action.kind) + 1 if action else 0)
            targets = CATALOG.get(action.kind, []) if action else []
            target_kind = action.kind if action else None
            if target_kind != self._target_kind:
                self._target_kind = target_kind
                self.target.set_model(Gtk.StringList.new([target_label(target) for target in targets]))

            if targets:
                index = next(index for index, target in enumerate(targets) if target["id"] == action.target)
                if self.target.get_selected() != index:
                    self.target.set_selected(index)

            self.target.set_visible(bool(targets))
            keyboard = action is not None and action.kind == "keyboard"
            self.modifiers_box.set_visible(keyboard)
            inherent = action.intrinsic_modifiers if keyboard else 0
            for bit, button in self.modifiers.items():
                button.set_active(bool((action.modifiers | inherent) & bit) if keyboard else False)
                button.set_sensitive(not bool(inherent & bit))

            self.action_hint.set_visible(keyboard or (action is not None and action.kind == "media"))
            self.action_hint.set_text(
                "Shortcuts send keys, not text. Your active keyboard layout determines characters. Super uses the GUI modifier."
                if keyboard
                else "Application and media actions depend on your desktop environment."
                if action and action.kind == "media"
                else "Changes are staged immediately. Review all planned changes together before applying."
            )
        finally:
            self.loading = False

        self.window.render_assignment_labels()
        self.update_actions()
        self.window.render_key_inspector()

    def select(self, source):
        if self.window.busy:
            return

        if source != self.editor.selected_source and hasattr(self.window, "inspector"):
            self.window.inspector.feedback.set_visible(False)

        self.editor.select(source)
        self.render()

    def edit(self, work):
        if self.loading or self.window.busy or self.window._closed:
            return

        try:
            work()
            self.invalidate_review()
            self.render()
            self.message("Assignment draft updated. Your keyboard has not changed.")
        except ValueError as error:
            self.render()
            self.message(str(error), True)

    def action_changed(self, *_):
        index = self.kind.get_selected()
        self.edit(lambda: self.editor.change_kind(KINDS[index - 1] if index else None))

    def target_changed(self, *_):
        if self.loading:
            return

        action = self.editor.selected_action
        targets = CATALOG.get(action.kind, []) if action else []
        index = self.target.get_selected()
        if index < len(targets):
            self.edit(lambda: self.editor.change_target(targets[index]["id"]))

    def modifier_changed(self, button, bit):
        self.edit(lambda: self.editor.set_modifier(bit, button.get_active()))

    def invalidate_review(self):
        self.editor.review = None
        if hasattr(self.window, "changes"):
            self.window.invalidate_review()
        else:
            self.update_actions()

    def read(self, *_):
        if self.window.busy or self.window._closed or not self.window.keyboard_connected or self.editor.service is None:
            return

        self.invalidate_review()

        def read_done(_):
            self.render()
            self.message("Current key assignments loaded. Your edits are preserved.")

        def read_failed(error):
            self.render()
            self.message("Could not load key assignments. Choose Refresh to try again. " + error_message(error), True)

        self.window.run_operation(self.editor.read, read_done, progress="Reading keyboard state…", on_error=read_failed)
