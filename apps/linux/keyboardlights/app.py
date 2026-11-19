#!/usr/bin/env python3
"""Native GTK 4 lighting and key assignment app for the legacy Cloud Nine C989."""

from __future__ import annotations

import argparse
import os
import sys
import threading
from pathlib import Path

try:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    from gi.repository import Gdk, Gio, GLib, Gtk
except (ImportError, ValueError) as error:
    raise SystemExit("KeyboardLights requires GTK 4 and the Python GObject bindings (python3-gi).") from error

if Gtk.check_version(4, 12, 0):
    raise SystemExit("KeyboardLights requires GTK 4.12 or later.")

import keyboardlights.lighting.service as lighting
from keyboardlights.assignments.model import KEYS, parse_draft
from keyboardlights.assignments.service import AssignmentService
from keyboardlights.device import capture as keyboard_connection
from keyboardlights.lighting.changes import KeyboardChanges
from keyboardlights.lighting.editor import LightingEditor
from keyboardlights.resources import icon_file
from keyboardlights.ui.assignments import AssignmentPage
from keyboardlights.ui.key_inspector import KeyInspector
from keyboardlights.ui.lighting_profiles import LightingProfilePage
from keyboardlights.ui.paint_tools import PaintTool
from keyboardlights.ui.presentation import (BRIGHTNESS_PWM, brightness_percent, error_message, preview_colors,
    review_summary)
from keyboardlights.ui.widgets import KeyboardGrid, LockIndicatorRow

ROOT = Path(__file__).resolve().parent


def widget_tree(root):
    yield root
    child = root.get_first_child()
    while child is not None:
        yield from widget_tree(child)
        child = child.get_next_sibling()


def rgba(color):
    value = Gdk.RGBA()
    value.parse(color)
    return value


def color_hex(value):
    return "#" + "".join(
        f"{min(255, max(0, round(channel * 255))):02X}" for channel in (value.red, value.green, value.blue)
    )


class LightingWindow(Gtk.ApplicationWindow):
    def __init__(self, application, editor, *, auto_read=True):
        super().__init__(application=application, title="KeyboardLights", default_width=860, default_height=745)
        self.editor = editor
        self.editor.select_brush(self.editor.last_used_color)
        self.editor.select_pointer()
        self._startup_pending = auto_read
        self.keyboard_connected = editor.service is None or editor.state["available"]
        self.connection_message = "Keyboard not connected"
        self.busy = False
        self.loading = False
        self.worker = None
        self.operation_label = ""
        self._closed = False
        self._close_dialog_pending = False
        self._discard_on_close = False
        self._key_style_key = None
        self._paint_style_key = None
        self._assignment_tooltips = {}
        self._selected_key = None
        self.about_dialog = None
        self.add_css_class("keyboardlights")
        self.connect("close-request", self.on_close)
        Gtk.Settings.get_default().set_property("gtk-application-prefer-dark-theme", True)
        self.theme_css = Gtk.CssProvider()
        self.theme_css.load_from_path(str(ROOT / "ui/style.css"))
        Gtk.StyleContext.add_provider_for_display(self.get_display(), self.theme_css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.set_icon_name(self.get_application().get_application_id())
        self._build_content()
        self._build_footer()
        self._build_review()
        self._connect_controls()
        self._build_navigation()
        self.render_state()
        self.update_actions()
        if editor.service is None:
            self.message("Offline preview. Save a profile to use it later.")
        elif auto_read:
            GLib.idle_add(self.check_keyboard_connection)

    def _build_navigation(self):
        directory = (
            self.editor.service.directory
            if self.editor.service is not None
            else Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "KeyboardLights"
        )
        service = (
            AssignmentService(directory / "Assignments", guard=self.editor.service.guard,
                logging=self.editor.service.logging)
            if self.editor.service is not None
            else None
        )
        self.assignment_page = AssignmentPage(self, service)
        lighting_page = self._root
        self.set_child(None)
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_child(root)
        self.inspection_banner = Gtk.Box(spacing=12, css_classes=["inspection-banner"])
        self.inspection_label = Gtk.Label(xalign=0, hexpand=True, wrap=True, selectable=True)
        self.inspection_banner.append(self.inspection_label)
        evidence = Gtk.Button(label="Show evidence", css_classes=["flat"])
        evidence.connect("clicked", self.show_inspection_evidence)
        self.inspection_banner.append(evidence)
        self.inspection_records_button = evidence
        root.append(self.inspection_banner)
        self.connection_banner = Gtk.Box(spacing=12, margin_top=12, margin_bottom=12, margin_start=28, margin_end=28)
        self.connection_text = Gtk.Label(xalign=0, hexpand=True, wrap=True)
        self.connection_banner.append(self.connection_text)
        root.append(self.connection_banner)
        root.append(lighting_page)
        self.changes = KeyboardChanges(self.editor, self.assignment_page.editor)
        self.inspector = KeyInspector(self)
        self.form.append(self.inspector)
        self.lighting_profile_page = LightingProfilePage(self)
        self.active_profile_page = self.lighting_profile_page
        self._build_menu_bar(root)
        self.add_shortcuts()
        page = lighting_page.get_first_child()
        lighting_page.remove(page)
        self.workspace_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        self.workspace_scroll.set_child(page)
        lighting_page.prepend(self.workspace_scroll)
        page.set_vexpand(False)
        self.form.set_vexpand(False)
        self.preview_card.set_vexpand(False)
        self.keyboard_scroll.set_vexpand(False)

    def render_key_inspector(self):
        if hasattr(self, "inspector") and self.inspector.get_visible():
            self.inspector.render()

    def render_assignment_labels(self):
        if not hasattr(self, "assignment_page"):
            return

        editor = self.assignment_page.editor
        available = editor.service is not None and editor.service.baseline is not None
        selected = editor.selected_source
        if selected != self._selected_key:
            if self._selected_key is not None:
                self.key_buttons[self._selected_key].remove_css_class("selected-key")

            self.key_buttons[selected].add_css_class("selected-key")
            self._selected_key = selected

        for key in lighting.KEYS:
            source = key["led_index"]
            button = self.key_buttons[source]
            current = editor.current_description(source) if available else "Not read"
            action = editor.actions.get(source)
            assignment = action.summary if action else current
            tooltip = key["name"] + "\nAssignment: " + assignment + "\nClick to view key details"
            if self._assignment_tooltips.get(source) != tooltip:
                button.set_tooltip_text(tooltip)
                self._assignment_tooltips[source] = tooltip

    def show_inspection_evidence(self, *_):
        if self.editor.service is None:
            return

        evidence = self.editor.service.guard.evidence
        if evidence is not None:
            try:
                Gio.AppInfo.launch_default_for_uri(Path(evidence).resolve().as_uri(), None)
            except GLib.Error as error:
                self.message(error.message, True)

    def _build_menu_bar(self, root):
        self.file_menu = Gio.Menu()
        for label, page in (("Lighting Profile", self.lighting_profile_page),
            ("Assignment Profile", self.assignment_page)):
            item = Gio.MenuItem.new_submenu(label, page.menu)
            item.set_attribute_value("submenu-action", GLib.Variant("s", "win." + page.submenu_action.get_name()))
            self.file_menu.append_item(item)

        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self.request_quit())
        self.add_action(quit_action)
        self.get_application().set_accels_for_action("win.quit", ["<Primary>q"])
        quit_menu = Gio.Menu()
        quit_menu.append("Quit", "win.quit")
        self.file_menu.append_section(None, quit_menu)

        about = Gio.SimpleAction.new("about", None)
        about.connect("activate", lambda *_: self.show_about())
        self.add_action(about)
        help_menu = Gio.Menu()
        help_menu.append("About", "win.about")
        menu = Gio.Menu()
        menu.append_submenu("File", self.file_menu)
        menu.append_submenu("Help", help_menu)
        self.menu_bar = Gtk.PopoverMenuBar.new_from_model(menu)
        self.menu_bar.set_hexpand(True)
        self.menu_row = Gtk.Box(css_classes=["menu-row"])
        self.menu_row.append(self.menu_bar)
        self.read_button = Gtk.Button(label="Refresh", valign=Gtk.Align.CENTER, margin_end=14, margin_top=2,
            margin_bottom=2, css_classes=["flat"])
        self.read_button.connect("clicked", self.read_keyboard)
        self.menu_row.append(self.read_button)
        root.prepend(self.menu_row)
        for popover in self.menu_popovers():
            if isinstance(popover, Gtk.PopoverMenu) and popover.get_menu_model() == help_menu:
                scroller = popover.get_child()
                if isinstance(scroller, Gtk.ScrolledWindow):
                    # A hidden automatic scrollbar otherwise reserves a second row's height.
                    scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.NEVER)

    def menu_popovers(self):
        for widget in widget_tree(self.menu_bar):
            if isinstance(widget, Gtk.Popover):
                yield widget

    def close_file_menu(self):
        if hasattr(self, "menu_bar"):
            for popover in self.menu_popovers():
                popover.popdown()

    def show_about(self):
        if self._closed:
            return

        self.close_file_menu()
        if self.about_dialog is None:
            version_file = ROOT.parent / "VERSION"
            version = version_file.read_text().strip() if version_file.is_file() else "Development build"
            self.about_dialog = Gtk.AboutDialog(title="About KeyboardLights", transient_for=self, modal=True,
                hide_on_close=True, program_name="KeyboardLights", version=version,
                comments="Lighting and key assignments for legacy Cloud Nine C989 keyboards.",
                copyright="Copyright © 2026 oscoder47", license_type=Gtk.License.APACHE_2_0,
                logo=Gdk.Texture.new_from_filename(str(icon_file())))
            for widget in widget_tree(self.about_dialog):
                if isinstance(widget, Gtk.Label):
                    widget.set_selectable(False)

        self.about_dialog.present()

    def request_quit(self):
        if not self._closed:
            self.close_file_menu()
            self.close()

    def _build_content(self):
        self._root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_child(self._root)
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, vexpand=True, margin_top=12, margin_bottom=12,
            margin_start=28, margin_end=28)
        self._root.append(page)
        self.empty_state = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, valign=Gtk.Align.CENTER,
            vexpand=True)
        empty_icon = Gtk.Image.new_from_icon_name("input-keyboard-symbolic")
        empty_icon.set_pixel_size(56)
        self.empty_state.append(empty_icon)
        self.empty_state.append(Gtk.Label(label="Read keyboard lighting", css_classes=["section-title"]))
        self.empty_state.append(
            Gtk.Label(label="Connect the C989 and read its current lighting settings.", wrap=True, css_classes=["muted"]
            )
        )
        self.connect_button = Gtk.Button(label="Read keyboard", halign=Gtk.Align.CENTER, css_classes=["primary"])
        self.connect_button.connect("clicked", self.read_keyboard)
        self.empty_state.append(self.connect_button)
        page.append(self.empty_state)

        self.form = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, vexpand=True)
        page.append(self.form)
        self._build_settings(self.form)
        self._build_preview(self.form)

    def _build_preview(self, workspace):
        preview = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, hexpand=True, vexpand=True, margin_top=0,
            css_classes=["card", "paint-preview"])
        workspace.append(preview)
        self.preview_card = preview
        heading = Gtk.CenterBox()
        heading.set_start_widget(Gtk.Label(label="KEYBOARD PREVIEW", xalign=0, css_classes=["eyebrow"]))
        self.lock_indicators = LockIndicatorRow()
        heading.set_center_widget(self.lock_indicators)
        status = Gtk.Box(spacing=8)
        self.preview_pill = Gtk.Label(label="ON KEYBOARD", css_classes=["preview-pill"])
        status.append(self.preview_pill)
        self.selection_label = Gtk.Label(xalign=0, css_classes=["hint"])
        status.append(self.selection_label)
        heading.set_end_widget(status)
        preview.append(heading)
        grid = KeyboardGrid(self.key_toggled, signal="toggled")
        self.key_buttons = grid.buttons
        keyboard_scroll = Gtk.ScrolledWindow(vscrollbar_policy=Gtk.PolicyType.NEVER,
            hscrollbar_policy=Gtk.PolicyType.AUTOMATIC, propagate_natural_height=True, vexpand=True)
        keyboard_scroll.set_child(grid)
        self.keyboard_scroll = keyboard_scroll
        preview.append(keyboard_scroll)
        self.key_css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(self.get_display(), self.key_css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def _build_settings(self, parent):
        controls = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=False, min_children_per_line=1,
            max_children_per_line=3, row_spacing=12, column_spacing=16, css_classes=["card", "lighting-bar"])
        parent.append(controls)
        self.lighting_controls = controls
        self.mode = Gtk.DropDown.new_from_strings(["Solid color", *[mode["name"] for mode in lighting.MODES]])
        self.mode.set_size_request(150, -1)
        self.mode.set_halign(Gtk.Align.START)
        self.mode_field = self.field("Lighting Mode", self.mode, controls)
        self.tool_label = Gtk.Label(label="Tools", xalign=0, css_classes=["field-label"])
        self.tool_field = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.tool_field.append(self.tool_label)
        tool_row = Gtk.Box(spacing=8)
        self.pointer_button = Gtk.ToggleButton(tooltip_text="Select keys without painting", css_classes=["pointer-tool"]
        )
        self.pointer_button.set_child(Gtk.Image.new_from_file(str(ROOT / "ui/pointer.svg")))
        self.pointer_button.set_size_request(28, 28)
        self.pointer_button.update_property([Gtk.AccessibleProperty.LABEL], ["Select keys"])
        self.pointer_button.connect("clicked", self.select_pointer)
        tool_row.append(self.pointer_button)
        self.swatch_css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(self.get_display(), self.swatch_css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.paint_tools = {tool: PaintTool(self, tool) for tool in ("brush", "bucket")}
        for button in self.paint_tools.values():
            tool_row.append(button)

        self.paint_actions = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        self.clear_button = Gtk.Button(label="Clear")
        self.fill_button = Gtk.Button(label="Fill")
        for button, action in ((self.clear_button, "clear"), (self.fill_button, "fill")):
            button.connect("clicked", self.paint_action, action)
            self.paint_actions.append(button)

        tool_row.append(self.paint_actions)
        self.tool_field.append(tool_row)
        controls.insert(self.tool_field, -1)
        adjustment = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        adjustment.set_size_request(150, -1)
        controls.insert(adjustment, -1)
        self.adjustment_title = Gtk.Label(xalign=0, css_classes=["field-label"])
        adjustment.append(self.adjustment_title)
        self.brightness = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 16, 1)
        self.speed = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 2, 16, 1)
        for scale in (self.brightness, self.speed):
            scale.set_draw_value(False)
            adjustment.append(scale)

    def choose_color(self, *_, tool="brush"):
        self.paint_tools[tool].popdown()
        dialog = Gtk.ColorDialog(title="Custom color", with_alpha=False)

        def chosen(dialog, result):
            try:
                color = dialog.choose_rgba_finish(result)
            except GLib.Error as error:
                self._file_dialog_error(error)
                return

            if not self.busy and not self._closed:
                self.select_color(color_hex(color), tool=tool)

        dialog.choose_rgba(self, rgba(self.paint_tools[tool].color), None, chosen)

    def select_pointer(self, *_):
        if self.busy or self._closed:
            return

        self.editor.select_pointer()
        self.render_keys()
        self.update_controls()

    def select_paint_tool(self, tool):
        if self.busy or self._closed or self.editor.workspace_style == "animation":
            return

        for name, button in self.paint_tools.items():
            if name != tool:
                button.popdown()

        color = self.paint_tools[tool].color
        (self.editor.select_brush if tool == "brush" else self.editor.select_bucket)(color)
        self.update_controls()
        self.update_actions()

    def select_color(self, color, *, tool="brush"):
        if self.busy or self._closed:
            return

        (self.editor.select_brush if tool == "brush" else self.editor.select_bucket)(color)
        self.paint_tools[tool].popdown()
        self.render_keys()
        self.update_controls()

    def paint_action(self, _, action):
        if self.busy or self._closed or self.editor.workspace_style == "animation":
            return

        if action == "clear":
            self.editor.clear_paint()
        else:
            self.editor.fill_paint()

        self.invalidate_review()
        self.render_draft()

    def _build_footer(self):
        footer = Gtk.Box(spacing=16, css_classes=["footer"])
        self._root.append(footer)
        self.lighting_footer = footer
        self.connection_label = Gtk.Label(label="Not connected", valign=Gtk.Align.CENTER,
            css_classes=["connection", "offline"])
        footer.append(self.connection_label)
        self.spinner = Gtk.Spinner()
        footer.append(self.spinner)
        feedback = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5, hexpand=True)
        self.draft_label = Gtk.Label(label="No pending changes", xalign=0, css_classes=["footer-title"])
        self.status = Gtk.Label(xalign=0, wrap=True, max_width_chars=66, css_classes=["status-message"])
        feedback.append(self.draft_label)
        feedback.append(self.status)
        footer.append(feedback)
        self.reset_button = Gtk.Button(label="Reset", css_classes=["flat"])
        self.reset_button.set_tooltip_text(
            "Reset the preview to the last captured lighting without writing the keyboard"
        )
        self.reset_button.connect("clicked", self.reset_draft)
        footer.append(self.reset_button)
        self.review_button = Gtk.Button(label="Review changes", css_classes=["primary"])
        self.review_button.connect("clicked", self.prepare_review)
        footer.append(self.review_button)

    def _build_review(self):
        self.review_dialog = Gtk.Window(title="Review keyboard changes", transient_for=self, modal=True,
            default_width=440, resizable=False, css_classes=["keyboardlights"])
        self.review_dialog.connect("close-request", lambda *_: self.cancel_review())
        self.review_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20, css_classes=["review-content"])
        self.review_dialog.set_child(self.review_box)
        self.review_box.append(Gtk.Label(label="Apply all keyboard changes?", xalign=0, css_classes=["section-title"]))
        self.review_box.append(
            Gtk.Label(
                label="Reviewed changes are saved in verified steps. If a step fails, the update stops and reports what completed.",
                xalign=0,
                wrap=True,
                css_classes=["muted"],
            )
        )
        self.review_label = Gtk.Label(xalign=0, wrap=True, selectable=True, max_width_chars=52,
            css_classes=["review-summary"])
        self.review_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC, min_content_height=64, max_content_height=300,
            propagate_natural_height=True)
        self.review_scroll.set_child(self.review_label)
        self.review_box.append(self.review_scroll)
        self.confirm = Gtk.CheckButton(label="Apply all reviewed changes to my keyboard")
        self.confirm.connect("toggled", lambda *_: self.update_actions())
        self.review_box.append(self.confirm)
        review_actions = Gtk.Box(spacing=10, halign=Gtk.Align.END)
        self.cancel_button = Gtk.Button(label="Keep editing", css_classes=["flat"])
        self.cancel_button.connect("clicked", lambda *_: self.invalidate_review())
        review_actions.append(self.cancel_button)
        self.apply_button = Gtk.Button(label="Apply changes", css_classes=["primary"])
        self.apply_button.connect("clicked", self.apply_changes)
        review_actions.append(self.apply_button)
        self.review_box.append(review_actions)

    def _connect_controls(self):
        self.mode.connect("notify::selected", self.controls_changed)
        for widget in (self.brightness, self.speed):
            widget.connect("value-changed", self.controls_changed)

        for widget, name in ((self.mode, "Lighting mode"), (self.brightness, "Brightness"),
            (self.speed, "Animation speed")):
            widget.update_property([Gtk.AccessibleProperty.LABEL], [name])

    @staticmethod
    def field(label, widget, parent):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.append(Gtk.Label(label=label, xalign=0, css_classes=["field-label"]))
        box.append(widget)
        if isinstance(parent, Gtk.FlowBox):
            parent.insert(box, -1)
        else:
            parent.append(box)

        return box

    def message(self, text, error=False):
        self.status.set_text(text)
        self.status.set_selectable(error)
        if error:
            self.status.add_css_class("error")
        else:
            self.status.remove_css_class("error")

    def invalidate_review(self):
        self.editor.review = None
        if hasattr(self, "changes"):
            self.changes.review = None
            self.assignment_page.editor.review = None

        self.confirm.set_active(False)
        self.review_box.set_visible(False)
        self.review_dialog.set_visible(False)
        self.update_actions()

    def preview_lock_indicators(self):
        if not self.keyboard_connected:
            return None

        lighting_reading = self.editor.service.lock_indicators if self.editor.service is not None else None
        assignment_service = self.assignment_page.editor.service if hasattr(self, "assignment_page") else None
        assignment_reading = assignment_service.lock_indicators if assignment_service is not None else None
        return keyboard_connection.LockIndicators.latest(lighting_reading, assignment_reading)

    def clear_lock_indicators(self):
        for service in (self.editor.service, self.assignment_page.editor.service):
            if service is not None:
                service.lock_indicators = None

    def update_actions(self):
        self.lock_indicators.update(self.preview_lock_indicators())
        available = self.editor.draft is not None
        changed = self.changes.has_changes() if hasattr(self, "changes") else self.editor.has_changes()
        offline = self.editor.service is None
        per_key = self.editor.workspace_style != "animation"
        pending = self.editor.has_pending_edits()
        blocked = not offline and self.editor.service.guard.requires_inspection
        self.form.set_visible(available)
        self.empty_state.set_visible(not available)
        self.lighting_controls.set_visible(True)
        self.paint_actions.set_visible(self.editor.workspace_style != "animation")
        self.lighting_footer.set_visible(True)
        self.form.set_sensitive(not self.busy)
        self.read_button.set_visible(not offline)
        self.read_button.set_sensitive(not self.busy)
        self.connect_button.set_visible(not offline)
        self.connect_button.set_sensitive(not self.busy)
        self.reset_button.set_sensitive(available and not self.busy and self.keyboard_connected and pending)
        self.reset_button.set_label("Reset")
        self.reset_button.set_tooltip_text(
            "Discard staged key colors and restore the last captured lighting"
            if per_key
            else "Reset the preview to the last captured lighting without writing the keyboard"
        )
        self.review_button.set_label("Review changes")
        self.review_button.set_sensitive(
            not self.busy
            and self.keyboard_connected
            and not offline
            and not blocked
            and changed
            and (self.changes.can_review() if hasattr(self, "changes") else True)
        )
        self.review_button.set_tooltip_text(
            "Review your changes · Ctrl+Enter" if changed else "Change the lighting to prepare a review"
        )
        self.confirm.set_sensitive(not self.busy)
        self.cancel_button.set_sensitive(not self.busy)
        self.apply_button.set_sensitive(
            not self.busy
            and self.keyboard_connected
            and not blocked
            and (self.changes.review is not None if hasattr(self, "changes") else self.editor.review is not None)
            and self.confirm.get_active()
        )
        self.spinner.set_spinning(self.busy)
        self.clear_button.set_sensitive(available and self.editor.unpainted != lighting.PHYSICAL)
        self.fill_button.set_sensitive(available and bool(self.editor.unpainted))
        if offline:
            preview_status = "OFFLINE PREVIEW"
        elif pending:
            preview_status = "STAGED" if per_key else "NOT APPLIED"
        else:
            preview_status = "ON KEYBOARD"

        self.preview_pill.set_text(preview_status)
        if pending:
            self.preview_pill.add_css_class("pending")
        else:
            self.preview_pill.remove_css_class("pending")

        if offline:
            draft_status = "Offline preview"
        elif self.busy:
            draft_status = self.operation_label
        elif pending:
            draft_status = "Key changes staged · apply them together" if per_key else "Changes in preview"
        else:
            draft_status = "All changes applied" if available else "Connect your keyboard"

        if not self.busy and pending and not changed and self.editor.workspace_style == "animation":
            draft_status = "Unfinished solid-color draft kept"
        elif not self.busy and self.editor.has_unpainted():
            draft_status = "Paint or fill every blank key before reviewing"

        self.draft_label.set_text(draft_status)
        connected = available and not offline and self.keyboard_connected
        if offline:
            connection_status = "Offline preview"
        elif self.busy and not available:
            connection_status = "Reading…"
        elif connected:
            connection_status = "C989 connected"
        else:
            connection_status = "Not connected"

        self.connection_label.set_text(connection_status)
        if connected:
            self.connection_label.remove_css_class("offline")
        else:
            self.connection_label.add_css_class("offline")

        if hasattr(self, "assignment_page"):
            self.assignment_page.update_actions()
            self.lighting_profile_page.update_actions()
            self.workspace_scroll.set_sensitive(self.keyboard_connected)
            self.connection_banner.set_visible(not self.keyboard_connected)
            self.connection_text.set_text(self.connection_message)
            self.inspection_banner.set_visible(blocked)
            records = self.editor.service.guard.evidence if self.editor.service is not None else None
            self.inspection_records_button.set_visible(records is not None)
            if blocked:
                self.inspection_label.set_text(
                    "A previous keyboard write needs inspection. Further lighting and assignment writes are blocked."
                )

            self.read_button.set_tooltip_text("Read keyboard settings while keeping staged changes · Ctrl+R")
            if not self.busy and self.assignment_page.editor.has_changes():
                self.draft_label.set_text(
                    "Lighting and assignment changes staged"
                    if self.editor.has_changes()
                    else "Assignment changes staged"
                )

        self.render_key_inspector()

    def cancel_review(self):
        if not self.busy:
            self.invalidate_review()

        return True

    def reset_draft(self, *_):
        if self.busy:
            return

        self.editor.reset()
        self.render_state()
        self.message(
            "Staged changes reset. Your keyboard lighting has not changed."
            if self.editor.workspace_style == "perKey"
            else "Preview reset. Your keyboard lighting has not changed."
        )

    def add_shortcuts(self):
        controller = Gtk.ShortcutController()
        controller.set_scope(Gtk.ShortcutScope.MANAGED)
        for trigger, button in [("<Control>s", "Save"), ("<Control>o", "Open"), ("<Control>r", self.read_button),
            ("<Control>Return", self.review_button)]:

            def activate(*_, target=button):
                if isinstance(target, str):
                    return self.activate_profile_shortcut(saving=target == "Save")

                if target.get_sensitive() and target.get_visible():
                    target.emit("clicked")
                    return True

                return False

            controller.add_shortcut(
                Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string(trigger), Gtk.CallbackAction.new(activate))
            )

        self.add_controller(controller)
        cancel = Gtk.ShortcutController()
        cancel.add_shortcut(
            Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Escape"),
                Gtk.CallbackAction.new(lambda *_: self.cancel_review()))
        )
        self.review_dialog.add_controller(cancel)

    def activate_profile_shortcut(self, *, saving):
        if self.busy or self._closed:
            return False

        action = self.active_profile_page.profile_actions["Save" if saving else "Open"]
        if action.get_enabled():
            action.activate(None)

        return True

    def render_state(self):
        state = self.editor.state
        self.invalidate_review()
        if not state["available"]:
            self.preview_pill.set_tooltip_text("Read the keyboard to capture its current lighting.")
            return

        selected = state["selected"]
        tag = selected["tag"]
        current = (
            "Solid color"
            if tag == 128
            else next((mode["name"] for mode in lighting.MODES if mode["tag"] == tag), f"Legacy Static {tag}")
        )
        if tag == 128 and self.editor.draft["style"] == "solid":
            current = "Solid color · " + self.editor.draft["color"]

        source = "Current lighting" if self.editor.service is not None else "Capture lighting"
        self.preview_pill.set_tooltip_text(f"{source}: {current}")
        self.render_draft()

    def render_draft(self):
        draft = self.editor.preview_draft()
        if draft is None:
            return

        self.loading = True
        try:
            self.mode.set_selected(draft["mode"] - 6 if draft["style"] == "animation" else 0)
            self.brightness.set_value(draft["brightness"])
            self.speed.set_value(draft["speed"])
        finally:
            self.loading = False

        self.update_controls()
        self.render_keys()

    def update_controls(self):
        draft = self.editor.preview_draft()
        if draft is None:
            return

        animation = draft["style"] == "animation"
        self.brightness.set_visible(not animation)
        self.speed.set_visible(animation)
        self.paint_actions.set_visible(not animation)
        self.adjustment_title.set_text(
            f"Speed {draft['speed']}" if animation else f"Brightness {brightness_percent(draft['brightness'])}%"
        )
        names = {"pointer": "Select keys", "brush": "Paint brush", "bucket": "Paint bucket"}
        self.tool_label.set_text("Tools · " + names[self.editor.paint_tool])
        self.pointer_button.set_active(self.editor.pointer_selected)
        for button in self.paint_tools.values():
            if animation:
                button.popdown()

            button.update()

        signature = (self.editor.brush_color, self.editor.bucket_color, self.editor.last_used_color,
            self.editor.last_bucket_color)
        if signature != self._paint_style_key:
            rules = (rule for button in self.paint_tools.values() for rule in button.color_styles())
            self.swatch_css.load_from_string("\n".join(rules))
            self._paint_style_key = signature

    def controls_changed(self, widget, *_):
        if self.loading or self.busy or self._closed or self.editor.draft is None:
            return

        if widget is self.mode:
            self.editor.select_mode(128 if self.mode.get_selected() == 0 else self.mode.get_selected() + 6)
        elif widget is self.brightness:
            self.editor.change(brightness=int(self.brightness.get_value()))
        else:
            self.editor.change(speed=int(self.speed.get_value()))

        self.invalidate_review()
        self.render_draft()
        self.message("Changes stay in the preview until you apply them.")

    def key_toggled(self, button, index):
        if self.loading or self.assignment_page.loading:
            return

        self.loading = True
        button.set_active(False)
        self.loading = False
        if self.busy or self._closed:
            return

        self.assignment_page.select(index)
        if not self.editor.paint_clicked_key(index):
            return

        self.invalidate_review()
        self.render_draft()

    def render_keys(self):
        if self.editor.draft is None:
            return

        draft = self.editor.preview_draft()
        colors = preview_colors(draft)
        blanks = frozenset(self.editor.unpainted) if draft["style"] != "animation" else frozenset()
        visual_key = (BRIGHTNESS_PWM[draft["brightness"]], colors, self.editor.image, blanks)
        restyle = visual_key != self._key_style_key
        self.loading = True
        try:
            if restyle:
                preview_intensity = visual_key[0] / 255
                rules = []
                styles = {}
                for key, color in zip(lighting.KEYS, colors):
                    button = self.key_buttons[key["led_index"]]
                    key_color, intensity = color, preview_intensity
                    style_key = (key_color, intensity)
                    if style_key not in styles:
                        channels = tuple(round(channel * intensity) for channel in lighting.rgb(key_color))
                        glow = "#" + "".join(f"{value:02X}" for value in channels)
                        tint = "#" + "".join(f"{round(27 + value * 0.07):02X}" for value in channels)
                        styles[style_key] = (
                            f"background: linear-gradient(155deg, {tint}, #1b2230); "
                            f"border-bottom: 3px solid {glow}; box-shadow: 0 2px 5px alpha({glow}, 0.12);"
                        )

                    style = styles[style_key]
                    if key["led_index"] in blanks:
                        style = "background: #242a35; border: 1px dashed #8590a5; box-shadow: none;"

                    rules.append(f"#{button.get_name()} {{ {style} }}")

                    description = (
                        "Unpainted" if key["led_index"] in blanks else color if color != "#000000" else "Off / black"
                    )
                    button.update_property([Gtk.AccessibleProperty.DESCRIPTION], [description])

                self.key_css.load_from_string("\n".join(rules))
                self._key_style_key = visual_key

            for button in self.key_buttons.values():
                if button.get_active():
                    button.set_active(False)

        finally:
            self.loading = False

        self.render_assignment_labels()
        self.selection_label.set_text(
            f"{len(lighting.PHYSICAL) - len(blanks)} painted · {len(blanks)} unpainted" if blanks else ""
        )
        self.update_actions()

    def run_operation(self, work, success, *, render=False, progress="Working…", on_error=None):
        if self.busy or self._closed:
            return False

        self.operation_label = progress
        self.busy = True
        self.message(progress)
        self.update_actions()

        def done(result, error):
            self.busy = False
            self.worker = None
            if self._closed:
                return GLib.SOURCE_REMOVE

            try:
                if render:
                    self.render_state()

                if error is not None:
                    raise error

                success(result)
            except Exception as failure:
                self.invalidate_review()
                self.message(error_message(failure), True)
                if on_error is not None:
                    on_error(failure)
            finally:
                self.update_actions()

            return GLib.SOURCE_REMOVE

        def run():
            result, error = None, None
            try:
                result = work()
            except Exception as failure:
                error = failure

            GLib.idle_add(done, result, error)

        self.worker = threading.Thread(target=run, name="KeyboardLights operation", daemon=False)
        try:
            self.worker.start()
        except Exception as failure:
            done(None, failure)
            return False

        return True

    def read_startup_state(self):
        def assignments_loaded(_):
            self.assignment_page.render()
            self.assignment_page.message("Current key assignments loaded.")
            self.message("Current keyboard state loaded.")

        def assignments_failed(error):
            self.assignment_page.render()
            self.assignment_page.message(
                "Could not load key assignments. Choose Refresh to try again. " + error_message(error), True)

        def read_assignments(_):
            self.run_operation(self.assignment_page.editor.read, assignments_loaded, progress="Reading keyboard state…",
                on_error=assignments_failed)

        self.run_operation(self.editor.read, read_assignments, render=True, progress="Reading keyboard state…")
        return GLib.SOURCE_REMOVE

    def check_keyboard_connection(self, *_):
        if self.busy or self._closed or self.editor.service is None:
            return GLib.SOURCE_REMOVE

        self.connection_message = "Checking keyboard connection…"

        def checked(connected):
            self.keyboard_connected = connected
            self.connection_message = "Keyboard not connected" if not connected else ""
            if connected:
                if self._startup_pending:
                    self._startup_pending = False
                    self.read_startup_state()
                else:
                    self.read_connected_keyboard()
            else:
                self.clear_lock_indicators()
                self.message("Keyboard not connected")

        def failed(error):
            self.keyboard_connected = False
            self.clear_lock_indicators()
            self.connection_message = error_message(error)

        self.run_operation(keyboard_connection.is_connected, checked, progress="Checking keyboard connection…",
            on_error=failed)
        return GLib.SOURCE_REMOVE

    def read_keyboard(self, *_):
        return self.check_keyboard_connection()

    def read_connected_keyboard(self):
        if not self.keyboard_connected:
            return GLib.SOURCE_REMOVE

        if not self.busy and not self._closed:
            if self.editor.has_pending_edits():
                self.assignment_page.read()
            else:
                self.read_startup_state()

        return GLib.SOURCE_REMOVE

    def prepare_review(self, *_):
        if self.busy or self._closed or not self.keyboard_connected:
            return

        if not self.review_button.get_sensitive() or self.editor.service is None:
            return

        self.invalidate_review()

        def show(review):
            parts = []
            if review["lighting"] is not None:
                parts.append("Lighting\n" + review_summary(review["lighting"]["settings"], self.editor.state["slots"]))

            if review["assignments"] is not None:
                parts.append(
                    "Assignments\n"
                    + "\n".join(f"{KEYS[source]['name']}: {action.summary}" for source,
                        action in sorted(parse_draft(review["assignments"]["draft"]).items()))
                )

            self.review_label.set_text("\n\n".join(parts))
            self.confirm.set_active(False)
            self.review_box.set_visible(True)
            self.review_dialog.present()
            self.message("Review ready. All changes will be applied in verified steps.")
            self.confirm.grab_focus()

        self.run_operation(self.changes.prepare, show, progress="Preparing keyboard review…")

    def apply_changes(self, *_):
        if (
            self.busy
            or self._closed
            or not self.keyboard_connected
            or not self.confirm.get_active()
            or self.changes.review is None
        ):
            return

        self.review_dialog.set_visible(False)

        def applied(message):
            self.render_state()
            self.assignment_page.render()
            self.message(message)

        def stopped(error):
            self.render_state()
            self.assignment_page.render()
            self.message(str(error), True)

        self.run_operation(lambda: self.changes.apply(confirmed=True), applied, progress="Applying keyboard changes…",
            on_error=stopped)

    @staticmethod
    def file_dialog(title):
        dialog = Gtk.FileDialog(title=title, modal=True)
        file_filter = Gtk.FileFilter(name="KeyboardLights files (*.json)")
        file_filter.add_pattern("*.json")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(file_filter)
        dialog.set_filters(filters)
        dialog.set_default_filter(file_filter)
        return dialog

    def _file_dialog_error(self, error):
        dismissed = any(
            error.matches(Gtk.dialog_error_quark(), code)
            for code in (Gtk.DialogError.DISMISSED, Gtk.DialogError.CANCELLED)
        )
        if not self._closed and not dismissed:
            self.message(error.message, True)

    def on_close(self, *_):
        if self._closed:
            return False

        if self.busy:
            self.message("Please wait for the current operation to finish before closing.")
            return True

        if self._close_dialog_pending:
            return True

        if not self._discard_on_close and (
            self.editor.has_pending_edits() or self.lighting_profile_page.unsaved or self.assignment_page.unsaved
        ):
            dialog = self.close_discard_dialog()
            self._close_dialog_pending = True

            def chosen(dialog, result):
                self._close_dialog_pending = False
                try:
                    discard = dialog.choose_finish(result) == 1
                except GLib.Error:
                    return  # Dismissing the dialog keeps the edits.

                if discard and not self.busy and not self._closed:
                    self._discard_on_close = True
                    self.close()

            dialog.choose(self, None, chosen)
            return True

        self._closed = True
        if self.about_dialog is not None:
            self.about_dialog.destroy()

        self.review_dialog.destroy()
        for provider in (self.key_css, self.swatch_css, self.theme_css):
            Gtk.StyleContext.remove_provider_for_display(self.get_display(), provider)

        return False

    @staticmethod
    def close_discard_dialog():
        return Gtk.AlertDialog(message="Discard staged changes and close?",
            detail="Your pending edits will be lost. Keep editing to save a profile, or apply changes first.",
            buttons=["Keep editing", "Discard and close"], cancel_button=0, default_button=0, modal=True)


class KeyboardLightsApplication(Gtk.Application):
    def __init__(self, directory, capture=None, auto_read=True, logging=False):
        super().__init__(application_id="io.github.keyboardlights.KeyboardLights",
            flags=Gio.ApplicationFlags.NON_UNIQUE if capture is not None else Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.directory, self.capture, self.auto_read = directory, capture, auto_read
        self.logging = logging
        self.window = None

    def do_activate(self):
        if self.window is None:
            service = lighting.LightingService(self.directory, logging=self.logging) if self.capture is None else None
            editor = LightingEditor(service, self.capture, history_path=self.directory / "last-lighting-color.txt")
            self.window = LightingWindow(self, editor, auto_read=self.auto_read)
            self.window.connect("destroy", lambda *_: setattr(self, "window", None))

        self.window.present()


def main():
    GLib.set_prgname("KeyboardLights")
    GLib.set_application_name("KeyboardLights")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, help="inspect a channel-1 image offline; device writes are disabled")
    parser.add_argument("--data-dir", type=Path,
        default=Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "KeyboardLights",
        help="directory for preferences and optional diagnostic logs")
    parser.add_argument("--diagnostic-logging", action="store_true",
        help="save detailed capture and write logs for troubleshooting (off by default)")
    parser.add_argument("--no-auto-read", action="store_true",
        help="wait for Read keyboard instead of reading on launch")
    args = parser.parse_args()
    capture = None
    if args.capture:
        try:
            with args.capture.open("rb") as file:
                capture = file.read(1153)

            lighting.validate(capture)
        except (OSError, ValueError) as error:
            parser.error(str(error))

    app = KeyboardLightsApplication(args.data_dir, capture, auto_read=not args.no_auto_read,
        logging=args.diagnostic_logging)
    return app.run([sys.argv[0]])


if __name__ == "__main__":
    raise SystemExit(main())
