"""Opt-in GTK checks with hidden windows by default; all device work is mocked."""

import errno
import json
import os
import re
import stat
import sys
import tempfile
import threading
import time
import traceback
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import keyboardlights.assignments.service as assignments
import keyboardlights.lighting.service as lighting
from keyboardlights.assignments.model import CATALOG, Action
from keyboardlights.lighting.editor import LightingEditor
from keyboardlights.profiles.lighting import decode_profile
from support import baseline_image


@unittest.skipUnless(os.environ.get("KEYBOARDLIGHTS_GTK_TEST") == "1",
    "set KEYBOARDLIGHTS_GTK_TEST=1 for GTK widget tests")
class GtkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import keyboardlights.app as gui

        cls.gui = gui
        cls.app = gui.Gtk.Application(application_id="io.github.keyboardlights.Tests",
            flags=gui.Gio.ApplicationFlags.NON_UNIQUE)
        cls.app.register(None)

    def setUp(self):
        self.callback_errors = []
        self.enterContext(patch.object(sys, "excepthook", self.callback_failed))
        self.addCleanup(self.assert_no_callback_errors)
        keyboard_access = self.enterContext(
            patch.object(lighting.reader, "open_keyboard",
                side_effect=AssertionError("GTK tests must not access physical keyboards"))
        )
        self.addCleanup(keyboard_access.assert_not_called)
        self.enterContext(
            patch.object(lighting.reader, "capture_state",
                side_effect=AssertionError("GTK tests must mock keyboard captures"))
        )
        self.enterContext(patch.object(self.gui.keyboard_connection, "is_connected", return_value=True))
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.service = lighting.LightingService(Path(self.temporary.name))
        self.service.baseline = baseline_image()
        self.service.identity = {"hidraw": "/dev/not-opened"}
        self.enterContext(
            patch.object(self.gui.GLib, "get_user_config_dir", return_value=str(Path(self.temporary.name) / ".config"))
        )
        self.editor = LightingEditor(self.service)
        self.window = self.create_window(self.editor)

        def refresh_assignments():
            service = self.window.assignment_page.editor.service
            table = service.baseline[0] if service.baseline is not None else bytes(640)
            service.baseline = (table,)
            service.identity = dict(self.service.identity or {"hidraw": "/dev/not-opened"})
            return service.baseline

        self.enterContext(
            patch.object(self.window.assignment_page.editor.service, "read", side_effect=refresh_assignments)
        )
        self.pump()

    def tearDown(self):
        self.wait_idle()

    def create_window(self, editor, *, auto_read=False):
        window = self.gui.LightingWindow(self.app, editor, auto_read=auto_read)
        self.addCleanup(self.close_window, window)
        present_review = window.review_dialog.present

        # Ordinary workflow checks exercise real widgets without mapping windows.
        # Tests that present the main window also exercise native review dialogs.
        def present_if_visible():
            if window.get_visible():
                present_review()

        self.enterContext(patch.object(window.review_dialog, "present", side_effect=present_if_visible))
        return window

    def close_window(self, window):
        self.wait_idle(window)
        window._discard_on_close = True
        window._close_dialog_pending = False
        try:
            if not window._closed:
                self.assertFalse(window.on_close())
        finally:
            window.review_dialog.destroy()
            window.destroy()
            self.pump()

        self.assertNotIn(window, self.app.get_windows())

    def callback_failed(self, kind, error, trace):
        self.callback_errors.append("".join(traceback.format_exception(kind, error, trace)))

    def assert_no_callback_errors(self):
        if self.callback_errors:
            self.fail("Unhandled GTK callback exceptions:\n" + "\n".join(self.callback_errors))

    def present_window(self):
        self.window.present()
        self.wait_until(self.window.get_mapped, "GTK window was not mapped")

    def wait_until(self, ready, message):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            self.pump()
            if ready():
                return

            time.sleep(0.005)

        self.fail(message)

    def pump(self):
        context = self.gui.GLib.MainContext.default()

        # Bound each batch so a repeating source cannot defeat wait timeouts.
        for _ in range(100):
            if not context.pending():
                break

            context.iteration(False)

    def wait_idle(self, window=None):
        window = self.window if window is None else window
        self.wait_until(lambda: not window.busy, "GTK worker did not finish")

    def stage_color(self, color, window=None):
        window = self.window if window is None else window
        window.select_color(color)
        window.editor.fill_paint(overwriting=True)
        window.render_draft()

    def test_callback_exceptions_are_reported_as_test_failures(self):
        self.assert_no_callback_errors()
        button = self.gui.Gtk.Button()

        def fail(_):
            raise RuntimeError("injected GTK callback failure")

        button.connect("clicked", fail)
        button.emit("clicked")
        self.assertEqual(len(self.callback_errors), 1)
        with self.assertRaisesRegex(self.failureException, "injected GTK callback failure"):
            self.assert_no_callback_errors()

        self.callback_errors.pop()

    def test_event_pump_returns_with_a_continuously_ready_source(self):
        calls = 0

        def repeat():
            nonlocal calls
            calls += 1
            return calls < 1000

        source = self.gui.GLib.idle_add(repeat)
        try:
            self.pump()
            self.assertGreater(calls, 0)
            self.assertLess(calls, 1000)
        finally:
            if calls < 1000:
                self.gui.GLib.source_remove(source)

    def test_secondary_window_cleanup_waits_for_its_worker(self):
        other = self.create_window(LightingEditor(capture=baseline_image()))
        completed = []

        def work():
            time.sleep(0.05)
            return "done"

        def start():
            other.run_operation(work, completed.append)
            return self.gui.GLib.SOURCE_REMOVE

        self.gui.GLib.idle_add(start)
        self.close_window(other)
        self.assertEqual(completed, ["done"])
        self.assertTrue(other._closed)

    def test_initial_keyboard_fits_and_color_swatches_stay_circular_when_resized(self):
        self.present_window()
        w = self.window
        grid = w.keyboard_scroll.get_child().get_child()
        self.wait_until(lambda: grid.get_width() > 0, "GTK keyboard was not allocated")
        success, bounds = grid.compute_bounds(w.workspace_scroll)
        self.assertTrue(success)
        self.assertGreaterEqual(bounds.get_x(), 0)
        self.assertGreaterEqual(bounds.get_y(), 0)
        self.assertLessEqual(bounds.get_x() + bounds.get_width(), w.workspace_scroll.get_width())
        self.assertLessEqual(bounds.get_y() + bounds.get_height(), w.workspace_scroll.get_height())
        for adjustment in (w.keyboard_scroll.get_hadjustment(), w.keyboard_scroll.get_vadjustment()):
            self.assertLessEqual(adjustment.get_upper(), adjustment.get_page_size())

        self.assertIs(w.connection_label.get_parent(), w.lighting_footer)
        self.assertIs(w.get_child().get_first_child(), w.menu_row)
        self.assertIs(w.menu_row.get_first_child(), w.menu_bar)
        self.assertIs(w.menu_row.get_last_child(), w.read_button)
        _, refresh = w.read_button.compute_bounds(w.menu_row)
        self.assertAlmostEqual(refresh.get_x() + refresh.get_width(),
            w.menu_row.get_width() - w.read_button.get_margin_end(), delta=1)
        self.assertFalse(hasattr(w, "all_button"))
        self.assertIsNone(w.get_titlebar())
        self.assertEqual(w.mode_field.get_first_child().get_text(), "Lighting Mode")
        self.assertIs(w.paint_actions.get_parent(), w.pointer_button.get_parent())
        self.assertIs(w.paint_tools["bucket"].get_next_sibling(), w.paint_actions)
        self.assertIs(w.clear_button.get_parent(), w.paint_actions)
        self.assertIs(w.fill_button.get_parent(), w.paint_actions)
        self.assertIs(w.form.get_first_child(), w.lighting_controls)
        self.assertIs(w.lighting_controls.get_next_sibling(), w.preview_card)
        self.assertIs(w.preview_card.get_next_sibling(), w.inspector)
        self.assertIsNone(w.inspector.get_next_sibling())
        _, left = w.inspector.lighting_details.compute_bounds(w.inspector)
        _, right = w.inspector.assignment_details.compute_bounds(w.inspector)
        self.assertGreater(right.get_x(), left.get_x())
        self.assertEqual(left.get_y(), right.get_y())
        _, inspector = w.inspector.compute_bounds(w.workspace_scroll)
        self.assertLessEqual(inspector.get_y() + inspector.get_height(), w.workspace_scroll.get_height())
        self.assertLessEqual(w.workspace_scroll.get_height() - inspector.get_y() - inspector.get_height(), 32)
        adjustment = w.workspace_scroll.get_vadjustment()
        self.assertLessEqual(adjustment.get_upper(), adjustment.get_page_size())
        _, keyboard = grid.compute_bounds(w.preview_card)
        _, heading = w.lock_indicators.get_parent().compute_bounds(w.preview_card)
        self.assertAlmostEqual(heading.get_x(), keyboard.get_x(), delta=1)
        self.assertAlmostEqual(heading.get_width(), keyboard.get_width(), delta=1)

        buttons = [w.pointer_button, *w.paint_tools.values()]
        initial = [(button.get_width(), button.get_height()) for button in buttons]
        initial_width, initial_height = w.get_width(), w.get_height()
        for width, height in ((initial_width * 9 // 10, initial_height * 9 // 10), (initial_width, initial_height)):
            with self.subTest(width=width, height=height):
                w.set_default_size(width, height)
                self.wait_until(lambda: (w.get_width(), w.get_height()) == (width, height), "GTK window did not resize")
                self.assertEqual([(button.get_width(), button.get_height()) for button in buttons], initial)
                for button in buttons:
                    self.assertGreater(button.get_width(), 0)

        popover = self.open_profile_menu(w.assignment_page)
        self.assertTrue(w.assignment_page.profile_actions["Open"].get_enabled())
        self.assertFalse(w.inspector.feedback.get_visible())
        popover.popdown()
        popover = self.open_profile_menu(w.lighting_profile_page)
        self.assertTrue(w.lighting_profile_page.profile_actions["Open"].get_enabled())
        popover.popdown()

    def test_file_and_help_menus_expose_profile_actions_and_about(self):
        w = self.window
        model = w.menu_bar.get_menu_model()
        self.assertEqual(
            [model.get_item_attribute_value(i, "label", None).get_string() for i in range(model.get_n_items())],
            ["File", "Help"])
        self.assertEqual([w.file_menu.get_item_attribute_value(i, "label", None).get_string() for i in range(2)],
            ["Lighting Profile", "Assignment Profile"])
        self.assertEqual(w.file_menu.get_n_items(), 3)
        quit_menu = w.file_menu.get_item_link(2, "section")
        self.assertEqual(quit_menu.get_n_items(), 1)
        self.assertEqual(quit_menu.get_item_attribute_value(0, "label", None).get_string(), "Quit")
        self.assertEqual(quit_menu.get_item_attribute_value(0, "action", None).get_string(), "win.quit")
        for index, page in enumerate((w.lighting_profile_page, w.assignment_page)):
            submenu = w.file_menu.get_item_link(index, "submenu")
            labels = [
                submenu.get_item_attribute_value(i, "label", None).get_string() for i in range(submenu.get_n_items())
            ]
            self.assertEqual(labels, ["New", "Open", "Save", "Save As…", "Delete current"])
            for i, label in enumerate(labels):
                self.assertEqual(submenu.get_item_attribute_value(i, "action", None).get_string(),
                    "win." + page.profile_actions[label].get_name())
                self.assertIs(w.lookup_action(page.profile_actions[label].get_name()), page.profile_actions[label])

        help_menu = model.get_item_link(1, "submenu")
        self.assertEqual(help_menu.get_n_items(), 1)
        self.assertEqual(help_menu.get_item_attribute_value(0, "label", None).get_string(), "About")
        self.assertEqual(help_menu.get_item_attribute_value(0, "action", None).get_string(), "win.about")

    def test_assignment_controls_fit_default_window_width_for_long_outputs_and_shortcuts(self):
        w = self.window
        p = self.assignment_workspace(captured=True)
        self.present_window()

        def settle():
            ready = []
            self.gui.GLib.timeout_add(50, lambda: ready.append(True) and False)
            self.wait_until(lambda: bool(ready), "GTK assignment layout did not settle")

        settle()
        width = w.get_width()
        for action in (Action("keyboard", 4), Action("keyboard", 231), Action("keyboard", 100), Action("mouse", 2),
            Action("media", 4), Action("disabled"), Action("factoryDefault"), None):
            with self.subTest(action=action):
                p.editor.set(action, 39)
                p.render()
                w.update_actions()
                settle()
                self.assertEqual(w.get_width(), width)
                for widget in (p.kind, p.target, *p.modifiers.values()):
                    if not widget.is_visible():
                        continue

                    success, bounds = widget.compute_bounds(w.inspector.assignment_details)
                    self.assertTrue(success)
                    self.assertGreaterEqual(bounds.get_x(), 0)
                    self.assertLessEqual(bounds.get_x() + bounds.get_width(), w.inspector.assignment_details.get_width()
                    )

    def test_file_quit_keeps_unsaved_edits_until_discard_is_confirmed(self):
        self.present_window()
        w = self.window
        self.stage_color("#123456", w)
        draft = self.editor.export_draft()
        popover = self.open_profile_menu(w.lighting_profile_page)
        dialog = Mock()
        dialog.choose_finish.return_value = 0
        with patch.object(w, "close_discard_dialog", return_value=dialog):
            w.lookup_action("quit").activate(None)
            self.assertFalse(popover.get_visible())
            self.assertFalse(w._closed)
            self.assertTrue(w.get_visible())
            dialog.choose.assert_called_once()
            dialog.choose.call_args.args[2](dialog, None)
            self.assertFalse(w._closed)
            self.assertEqual(self.editor.export_draft(), draft)
            dialog.choose_finish.return_value = 1
            w.lookup_action("quit").activate(None)
            dialog.choose.call_args.args[2](dialog, None)
            self.pump()

        self.assertTrue(w._closed)
        self.assertFalse(w.get_visible())
        self.assertTrue(self.app.get_accels_for_action("win.quit"))

    def test_file_quit_waits_for_current_operation_and_closes_a_clean_window(self):
        self.present_window()
        w = self.window
        w.busy = True
        try:
            w.lookup_action("quit").activate(None)
            self.assertFalse(w._closed)
            self.assertTrue(w.get_visible())
            self.assertIn("current operation", w.status.get_text())
        finally:
            w.busy = False

        with patch.object(w, "close_discard_dialog") as dialog:
            w.lookup_action("quit").activate(None)
            self.pump()
            dialog.assert_not_called()

        self.assertTrue(w._closed)
        self.assertFalse(w.get_visible())

    def test_native_profile_submenu_selection_routes_shortcuts(self):
        self.present_window()
        w = self.window
        popover = self.open_profile_menu(w.lighting_profile_page)
        widgets = [popover]
        assignment_item = None
        while widgets:
            widget = widgets.pop()
            if widget.__gtype__.name == "GtkModelButton" and widget.get_property("text") == "Assignment Profile":
                assignment_item = widget
                break

            child = widget.get_first_child()
            while child is not None:
                widgets.append(child)
                child = child.get_next_sibling()

        self.assertIsNotNone(assignment_item)
        self.assertTrue(assignment_item.activate())
        self.wait_until(lambda: w.active_profile_page is w.assignment_page, "Assignment submenu was not selected")
        popover.popdown()
        path = Path(self.temporary.name) / "Assignments.json"
        with patch.object(w, "file_dialog", return_value=self.profile_dialog(path)):
            w.activate_profile_shortcut(saving=True)
            self.wait_idle()

        self.assertEqual(w.assignment_page.current_file.get_path(), str(path))
        self.assertIsNone(w.lighting_profile_page.current_file)

    def test_help_menu_fits_its_single_about_row(self):
        self.present_window()
        w = self.window
        help_menu = w.menu_bar.get_menu_model().get_item_link(1, "submenu")
        popover = next(
            p for p in w.menu_popovers() if isinstance(p, self.gui.Gtk.PopoverMenu) and p.get_menu_model() == help_menu
        )
        popover.popup()
        scroller = popover.get_child()
        self.wait_until(lambda: scroller.get_height() > 0, "Help menu was not allocated")
        content_height = scroller.get_child().measure(self.gui.Gtk.Orientation.VERTICAL, scroller.get_width()).natural
        self.assertAlmostEqual(scroller.get_height(), content_height, delta=1)
        items = [widget for widget in self.gui.widget_tree(popover) if widget.__gtype__.name == "GtkModelButton"]
        self.assertEqual([item.get_property("text") for item in items], ["About"])
        popover.popdown()

    def test_about_dialog_shows_metadata_closes_and_reopens(self):
        self.present_window()
        w = self.window
        popover = self.open_profile_menu(w.lighting_profile_page)
        (Path(self.temporary.name) / "VERSION").write_text("1.0.0\n")
        with patch.object(self.gui, "ROOT", Path(self.temporary.name) / "keyboardlights"):
            w.lookup_action("about").activate(None)

        dialog = w.about_dialog
        self.assertFalse(popover.get_visible())
        self.wait_until(dialog.get_mapped, "About dialog was not mapped")
        self.assertEqual(dialog.get_title(), "About KeyboardLights")
        self.assertEqual(dialog.get_program_name(), "KeyboardLights")
        self.assertEqual(dialog.get_version(), "1.0.0")
        self.assertEqual(dialog.get_license_type(), self.gui.Gtk.License.APACHE_2_0)
        self.assertIs(dialog.get_transient_for(), w)
        self.assertTrue(dialog.get_modal())
        self.assertIsNotNone(dialog.get_logo())
        name = next(
            widget
            for widget in self.gui.widget_tree(dialog)
            if isinstance(widget, self.gui.Gtk.Label) and widget.get_text() == "KeyboardLights"
        )
        self.assertFalse(name.get_selectable())
        self.assertFalse(name.get_selection_bounds()[0])
        dialog.close()
        self.pump()
        self.assertFalse(dialog.get_visible())
        w.lookup_action("about").activate(None)
        self.assertIs(w.about_dialog, dialog)
        self.assertTrue(dialog.get_visible())
        self.assertFalse(name.get_selection_bounds()[0])
        self.close_window(w)
        self.assertFalse(dialog.get_visible())
        w.show_about()
        self.assertFalse(dialog.get_visible())

    def test_missing_keyboard_disables_options_without_reading_and_refresh_reconnects(self):
        self.close_window(self.window)
        service = lighting.LightingService(Path(self.temporary.name) / "disconnected")
        editor = LightingEditor(service)
        with (patch.object(self.gui.keyboard_connection, "is_connected", return_value=False),
            patch.object(editor, "read") as read, patch.object(lighting.reader, "open_keyboard") as opened):
            self.window = self.create_window(editor, auto_read=True)
            self.pump()
            self.wait_idle()
            read.assert_not_called()
            opened.assert_not_called()

        self.assertEqual(self.window.connection_text.get_text(), "Keyboard not connected")
        self.assertFalse(self.window.workspace_scroll.get_sensitive())
        self.assertTrue(self.window.read_button.is_sensitive())
        self.assertFalse(self.window.lighting_profile_page.save_action.get_enabled())

        def capture():
            service.baseline = baseline_image()
            service.identity = {"hidraw": "/dev/not-opened"}
            editor.adopt(service.state())

        with (patch.object(editor, "read", side_effect=capture) as read,
            patch.object(self.window.assignment_page.editor, "read") as assignment_read):
            self.window.read_button.emit("clicked")
            self.wait_idle()
            read.assert_called_once()
            assignment_read.assert_called_once()

        self.assertTrue(self.window.workspace_scroll.get_sensitive())
        self.assertFalse(self.window.connection_banner.get_visible())

    def test_disconnecting_a_previously_captured_keyboard_updates_its_badge_and_keeps_edits(self):
        w = self.window
        self.stage_color("#123456", w)
        w.assignment_page.editor.set(Action("disabled"), 39)
        lighting_draft = self.editor.export_draft()
        assignment_draft = w.assignment_page.editor.draft
        with (patch.object(self.gui.keyboard_connection, "is_connected", return_value=False),
            patch.object(w.editor, "read") as lighting_read,
            patch.object(w.assignment_page.editor, "read") as assignment_read):
            w.read_button.emit("clicked")
            self.wait_idle()
            lighting_read.assert_not_called()
            assignment_read.assert_not_called()

        self.assertEqual(w.connection_label.get_text(), "Not connected")
        self.assertTrue(w.connection_label.has_css_class("offline"))
        self.assertFalse(w.workspace_scroll.get_sensitive())
        self.assertFalse(w.review_button.get_sensitive())
        self.assertEqual(self.editor.export_draft(), lighting_draft)
        self.assertEqual(w.assignment_page.editor.draft, assignment_draft)

    def test_keyboard_permission_guidance_survives_startup_and_shared_refresh(self):
        w = self.window
        p = w.assignment_page
        denied = PermissionError(errno.EACCES, "Permission denied", "/dev/hidraw2")
        for startup, step in ((True, "lighting"), (True, "assignments"), (False, "assignments")):
            with self.subTest(startup=startup, step=step):
                w.update_actions()
                self.wait_idle()
                w._startup_pending = startup
                with (patch.object(w.editor, "read", side_effect=denied if step == "lighting" else None) as read,
                    patch.object(p.editor, "read", side_effect=denied) as assignment_read):
                    w.read_keyboard()
                    self.wait_idle()
                    self.assertEqual(read.call_count, 1)
                    self.assertEqual(assignment_read.call_count, int(step == "assignments"))

                status = w.status
                self.assertIn("Unplug and reconnect", status.get_text())
                self.assertIn("device access rule", status.get_text())
                self.assertIn("normal desktop user", status.get_text())
                self.assertTrue(status.has_css_class("error"))
                self.assertTrue(w.read_button.get_sensitive())

    def test_worker_error_is_reported_once_and_callback_receives_original_exception(self):
        w = self.window
        denied = PermissionError(errno.EACCES, "Permission denied", "/dev/hidraw2")
        for use_callback in (False, True):
            with self.subTest(use_callback=use_callback):
                callback = Mock() if use_callback else None
                success = Mock()
                with patch.object(w, "message", wraps=w.message) as message:
                    self.window.run_operation(Mock(side_effect=denied), success, on_error=callback)
                    self.wait_idle()

                errors = [call.args for call in message.call_args_list if len(call.args) > 1 and call.args[1]]
                self.assertEqual(len(errors), 1)
                self.assertIn("Unplug and reconnect", errors[0][0])
                success.assert_not_called()
                if callback is not None:
                    callback.assert_called_once_with(denied)

    def test_lock_indicator_row_does_not_update_unchanged_labels(self):
        row = self.window.lock_indicators
        row.update(lighting.reader.LockIndicators(5))
        label = row.labels["Caps Lock"]
        with (patch.object(label, "set_tooltip_text", wraps=label.set_tooltip_text) as tooltip,
            patch.object(label, "update_property", wraps=label.update_property) as accessible):
            row.update(lighting.reader.LockIndicators(0x85))
            tooltip.assert_not_called()
            accessible.assert_not_called()
            row.update(lighting.reader.LockIndicators(4))
            self.assertIn("Off", row.labels["Num Lock"].get_tooltip_text())
            tooltip.assert_not_called()
            accessible.assert_not_called()
            row.update(None)
            self.assertIs(row.get_visible_child(), row.unavailable_label)
            self.assertEqual(row.unavailable_label.get_text(), "Lock states unavailable")
            tooltip.assert_not_called()
            accessible.assert_not_called()

        observed = []
        row.connect(
            "notify::visible-child",
            lambda *_: observed.append(
                [(label.has_css_class("on"), label.get_tooltip_text()) for label in row.labels.values()]
            ),
        )
        row.update(lighting.reader.LockIndicators(0))
        self.assertEqual(observed, [[(False, f"{name}: Off. Last keyboard reading.") for name in row.labels]])

    def test_disconnect_clears_snapshots_before_a_failed_reconnect_with_pending_edits(self):
        w = self.window
        self.stage_color("#123456")
        draft = self.editor.export_draft()
        for connection_error in (None, PermissionError("Cannot discover keyboard")):
            with self.subTest(connection_error=connection_error):
                self.service.lock_indicators = lighting.reader.LockIndicators(7)
                w.assignment_page.editor.service.lock_indicators = lighting.reader.LockIndicators(7)
                with patch.object(self.gui.keyboard_connection, "is_connected", return_value=False,
                    side_effect=connection_error):
                    w.read_keyboard()
                    self.wait_idle()

                self.assertIsNone(self.service.lock_indicators)
                self.assertIsNone(w.assignment_page.editor.service.lock_indicators)
                with patch.object(w.assignment_page.editor.service, "read", side_effect=OSError("Read failed")):
                    w.read_keyboard()
                    self.wait_idle()

                self.assertTrue(w.keyboard_connected)
                self.assertIs(w.lock_indicators.get_visible_child(), w.lock_indicators.unavailable_label)
                self.assertEqual(w.lock_indicators.unavailable_label.get_text(), "Lock states unavailable")
                self.assertEqual(self.editor.export_draft(), draft)

    def test_lock_indicators_use_latest_reading_without_moving_keys_or_growing_preview(self):
        w = self.window
        p = self.assignment_workspace(captured=True)
        self.present_window()
        self.wait_until(lambda: w.lock_indicators.get_width() > 0, "GTK lock indicators were not allocated")
        success, bounds = w.lock_indicators.compute_bounds(w.preview_card)
        self.assertTrue(success)
        self.assertAlmostEqual(bounds.get_x() + bounds.get_width() / 2, w.preview_card.get_width() / 2, delta=1)
        baseline = None
        for lighting_reading, assignment_reading, expected in ((None, None, None),
            (lighting.reader.LockIndicators(7, sampled_at=2), lighting.reader.LockIndicators(0, sampled_at=1), 7),
            (lighting.reader.LockIndicators(7, sampled_at=2), lighting.reader.LockIndicators(0, sampled_at=3), 0)):
            self.service.lock_indicators = lighting_reading
            p.editor.service.lock_indicators = assignment_reading
            w.update_actions()
            self.pump()
            self.assertEqual(w.lock_indicators.get_visible_child_name(), "unavailable" if expected is None else "states"
            )
            for name, label in w.lock_indicators.labels.items():
                self.assertEqual(label.get_label(), name)
                if expected is not None:
                    self.assertEqual(label.has_css_class("on"), expected == 7)
                    self.assertIn("On" if expected == 7 else "Off", label.get_tooltip_text())

            widgets = [w.lock_indicators, w.key_buttons[39], w.key_buttons[90], w.preview_card]
            frames = []
            for widget in widgets:
                success, bounds = widget.compute_bounds(w)
                self.assertTrue(success)
                frames.append((bounds.get_x(), bounds.get_y(), bounds.get_width(), bounds.get_height()))

            if baseline is None:
                baseline = frames
            else:
                self.assertEqual(frames, baseline)

        w.keyboard_connected = False
        w.update_actions()
        self.assertIs(w.lock_indicators.get_visible_child(), w.lock_indicators.unavailable_label)
        self.assertEqual(w.lock_indicators.unavailable_label.get_text(), "Lock states unavailable")

    def test_startup_loads_lighting_and_assignments_in_order_without_writes(self):
        w = self.window
        order = []
        w._startup_pending = True

        def lighting_read():
            order.append("lighting")
            self.editor.adopt(self.service.state())

        def assignment_read():
            order.append("assignments")
            w.assignment_page.editor.service.baseline = (bytes(640),)
            w.assignment_page.editor.service.identity = dict(self.service.identity)

        with (patch.object(self.editor, "read", side_effect=lighting_read),
            patch.object(w.assignment_page.editor, "read", side_effect=assignment_read),
            patch.object(lighting, "execute") as lighting_write,
            patch.object(assignments, "execute") as assignment_write):
            w.read_keyboard()
            self.wait_idle()
            self.assertEqual(order, ["lighting", "assignments"])
            self.assertIsNotNone(w.assignment_page.editor.service.baseline)
            self.assertIn("Factory default", w.inspector.assignment_status.get_text())
            lighting_write.assert_not_called()
            assignment_write.assert_not_called()

    def test_disconnected_or_closed_window_blocks_review_and_apply_entry_points(self):
        w = self.window
        p = self.assignment_workspace(captured=True)
        for disconnected in (True, False):
            w.keyboard_connected = not disconnected
            w._closed = not disconnected
            w.review_button.set_sensitive(True)
            w.confirm.set_active(True)
            w.editor.review = {"token": "stale"}
            p.editor.review = {"token": "stale"}
            with (patch.object(w.editor, "prepare") as prepare_lighting,
                patch.object(w.editor, "apply") as apply_lighting,
                patch.object(p.editor, "prepare") as prepare_assignments,
                patch.object(p.editor, "apply") as apply_assignments):
                w.prepare_review()
                w.apply_changes()
                prepare_lighting.assert_not_called()
                apply_lighting.assert_not_called()
                prepare_assignments.assert_not_called()
                apply_assignments.assert_not_called()

        w._closed = False
        w.keyboard_connected = True
        w.invalidate_review()
        p.invalidate_review()

    def test_inline_details_update_automatically_after_painting(self):
        w = self.window
        self.stage_color("#123456", w)
        w.key_buttons[90].set_active(True)
        self.assertEqual(w.assignment_page.editor.selected_source, 90)
        self.assertIn("#123456", w.inspector.lighting_status.get_text())
        self.assertIn("Selected key:", w.inspector.title.get_text())
        self.assertIs(w.inspector.get_parent(), w.form)

    def test_native_controls_follow_animation_policy(self):
        self.present_window()
        w = self.window
        self.wait_until(lambda: w.mode.get_width() > 0, "Lighting Mode was not allocated")
        mode_width = w.mode.get_width()
        self.assertEqual(mode_width, w.mode.measure(self.gui.Gtk.Orientation.HORIZONTAL, -1).natural)
        self.assertIsInstance(w, self.gui.Gtk.ApplicationWindow)
        self.assertIsInstance(w.file_dialog("Test"), self.gui.Gtk.FileDialog)
        self.assertEqual(w.mode.get_model().get_n_items(), 10)
        self.assertEqual(len(w.key_buttons), 115)
        self.assertTrue(w.paint_tools["brush"].get_visible())
        self.assertTrue(w.brightness.get_visible())
        self.assertFalse(w.speed.get_visible())
        for index in range(1, 10):
            w.mode.set_selected(index)
            self.pump()
            self.assertEqual(w.mode.get_width(), mode_width)
            self.assertFalse(w.paint_tools["brush"].get_visible())
            self.assertFalse(w.brightness.get_visible())
            self.assertTrue(w.speed.get_visible())
            self.assertFalse(w.paint_actions.get_visible())

        w.mode.set_selected(0)
        self.pump()
        self.assertEqual(w.mode.get_width(), mode_width)
        self.assertTrue(w.paint_tools["brush"].get_visible())
        self.assertTrue(w.brightness.get_visible())

    def test_brush_and_bucket_palettes_choose_independent_colors_without_painting(self):
        self.present_window()
        w = self.window
        draft = self.editor.export_draft()
        for tool, color in (("brush", "#5D9CFF"), ("bucket", "#A48BFF")):
            with self.subTest(tool=tool):
                button = w.paint_tools[tool]
                button.popup()
                self.wait_until(button.palette.get_mapped, "Paint palette was not mapped")
                self.assertEqual(self.editor.paint_tool, tool)
                self.assertTrue(button.has_css_class("active"))
                self.assertFalse(w.pointer_button.get_active())
                for swatch in (button.last_button, *button.swatches.values()):
                    self.assertGreater(swatch.get_width(), 0)
                    self.assertEqual(swatch.get_width(), swatch.get_height())

                button.swatches[color].emit("clicked")
                self.assertFalse(button.palette.get_visible())
                self.assertEqual(button.color, color)
                self.assertEqual(self.editor.export_draft(), draft)

        self.assertEqual(self.editor.brush_color, "#5D9CFF")
        self.assertEqual(self.editor.bucket_color, "#A48BFF")
        w.pointer_button.emit("clicked")
        self.assertTrue(self.editor.pointer_selected)
        w.paint_tools["brush"].popup()
        self.wait_until(w.paint_tools["brush"].palette.get_mapped, "Brush palette was not mapped")
        self.assertEqual(self.editor.brush_color, "#5D9CFF")
        self.assertFalse(w.paint_tools["bucket"].has_css_class("active"))
        w.paint_tools["brush"].popdown()

    def test_bucket_clicks_fill_connected_keys_and_preserve_other_regions_and_assignments(self):
        w = self.window
        w.select_color("#123456")
        for key in (39, 40, 57, 109):
            w.key_buttons[key].set_active(True)

        w.assignment_page.editor.set(Action("disabled"), 39)
        assignments_before = w.assignment_page.editor.draft
        w.select_color("#ABCDEF", tool="bucket")
        with patch.object(lighting, "execute") as execute:
            w.key_buttons[39].set_active(True)
            execute.assert_not_called()

        self.assertEqual(w.assignment_page.editor.selected_source, 39)
        self.assertEqual(w.assignment_page.editor.draft, assignments_before)
        self.assertEqual(self.editor.draft["slots"][40]["color"], "#ABCDEF")
        self.assertEqual(self.editor.draft["slots"][57]["color"], "#ABCDEF")
        self.assertEqual(self.editor.draft["slots"][109]["color"], "#123456")
        self.assertEqual(self.editor.last_bucket_color, "#ABCDEF")
        self.assertEqual(self.editor.brush_color, "#123456")
        self.assertEqual(w.inspector.paint.get_label(), "Fill connected keys")
        w.select_color("#5D9CFF")
        w.key_buttons[39].set_active(True)
        self.assertEqual(self.editor.draft["slots"][39]["color"], "#5D9CFF")
        self.assertEqual(self.editor.draft["slots"][40]["color"], "#ABCDEF")

    def test_each_paint_tool_custom_picker_preserves_color_on_cancel_and_dismisses_palette(self):
        self.present_window()
        w = self.window
        for tool in ("brush", "bucket"):
            with self.subTest(tool=tool):
                button = w.paint_tools[tool]
                button.popup()
                self.wait_until(button.palette.get_mapped, "Paint palette was not mapped")
                draft, color = self.editor.export_draft(), button.color
                dialog = Mock()
                dismissed = self.gui.GLib.Error.new_literal(self.gui.Gtk.dialog_error_quark(), "Dismissed",
                    self.gui.Gtk.DialogError.DISMISSED)
                dialog.choose_rgba_finish.side_effect = dismissed

                def choose(parent, initial, cancellable, callback):
                    self.assertFalse(button.palette.get_visible())
                    callback(dialog, None)

                dialog.choose_rgba.side_effect = choose
                with patch.object(self.gui.Gtk, "ColorDialog", return_value=dialog):
                    button.custom_button.emit("clicked")
                    self.assertEqual(button.color, color)
                    self.assertEqual(self.editor.export_draft(), draft)
                    dialog.choose_rgba_finish.side_effect = None
                    dialog.choose_rgba_finish.return_value = self.gui.rgba("#123456")
                    button.custom_button.emit("clicked")

                self.assertEqual(button.color, "#123456")
                self.assertEqual(self.editor.paint_tool, tool)
                self.assertEqual(self.editor.export_draft(), draft)
                self.assertEqual(button.custom_button.get_label(), "Custom ✓")

    def test_animation_hides_paint_palettes_and_returns_to_pointer(self):
        self.present_window()
        w = self.window
        button = w.paint_tools["bucket"]
        button.popup()
        self.wait_until(button.palette.get_mapped, "Bucket palette was not mapped")
        w.mode.set_selected(3)
        self.assertTrue(self.editor.pointer_selected)
        self.assertFalse(button.palette.get_visible())
        self.assertTrue(w.pointer_button.get_active())
        for paint in w.paint_tools.values():
            self.assertFalse(paint.get_visible())

        draft = self.editor.export_draft()
        w.key_buttons[39].set_active(True)
        self.assertEqual(self.editor.export_draft(), draft)
        w.mode.set_selected(0)
        self.assertTrue(self.editor.pointer_selected)
        for paint in w.paint_tools.values():
            self.assertTrue(paint.get_visible())

    def test_shared_keyboard_moves_between_modes_without_losing_paint(self):
        w = self.window
        p = w.assignment_page
        self.assertIs(w.key_buttons, p.key_buttons)
        self.assertIs(w.keyboard_scroll.get_parent(), w.preview_card)
        w.select_color("#123456")
        for index in (17, 90, 109, 113):
            w.key_buttons[index].set_active(True)
            self.assertEqual(self.editor.draft["slots"][index]["color"], "#123456")

        draft = self.editor.export_draft()
        w.update_actions()
        self.assertIs(w.keyboard_scroll.get_parent(), w.preview_card)
        p.key_buttons[90].emit("clicked")
        self.assertEqual(p.editor.selected_source, 90)
        self.assertTrue(w.inspector.get_visible())
        self.assertEqual(self.editor.export_draft(), draft)
        p.kind.set_selected(5)
        self.assertIn(Action("disabled").summary, p.key_buttons[90].get_tooltip_text())
        w.update_actions()
        self.assertIs(w.keyboard_scroll.get_parent(), w.preview_card)
        self.assertEqual(self.editor.export_draft(), draft)
        self.assertEqual(p.editor.actions[90], Action("disabled"))

    def test_pointer_default_selects_keys_and_colors_enable_painting(self):
        w = self.window
        original = self.editor.export_draft()
        self.assertTrue(w.pointer_button.get_active())
        w.key_buttons[90].set_active(True)
        self.assertEqual(w.assignment_page.editor.selected_source, 90)
        self.assertEqual(self.editor.export_draft(), original)
        w.select_color("#123456")
        self.assertFalse(w.pointer_button.get_active())
        w.key_buttons[90].set_active(True)
        self.assertEqual(self.editor.draft["slots"][90]["color"], "#123456")
        w.pointer_button.emit("clicked")
        draft = self.editor.export_draft()
        w.key_buttons[56].set_active(True)
        self.assertEqual(self.editor.export_draft(), draft)
        w.mode.set_selected(5)
        self.assertTrue(w.pointer_button.get_active())
        self.assertTrue(w.tool_field.get_visible())
        self.assertFalse(w.paint_tools["brush"].get_visible())

    def test_animation_click_selects_details_without_painting_or_opening_a_window(self):
        w = self.window
        w.mode.set_selected(5)
        draft = self.editor.export_draft()
        w.key_buttons[109].set_active(True)
        self.assertEqual(w.assignment_page.editor.selected_source, 109)
        self.assertIn("Selected key:", w.inspector.title.get_text())
        self.assertEqual(self.editor.export_draft(), draft)
        self.assertIsInstance(w.inspector, self.gui.Gtk.Box)

    def test_selected_key_remains_identifiable_in_both_modes(self):
        w = self.window
        w.mode.set_selected(5)
        draft = self.editor.export_draft()
        for source in (109, 90):
            w.key_buttons[source].set_active(True)
            selected = [index for index, button in w.key_buttons.items() if button.has_css_class("selected-key")]
            self.assertEqual(selected, [source])
            self.assertEqual(w.assignment_page.editor.selected_source, source)

        self.assertEqual(self.editor.export_draft(), draft)
        w.update_actions()
        self.assertTrue(w.key_buttons[90].has_css_class("selected-key"))
        w.update_actions()
        self.assertTrue(w.key_buttons[90].has_css_class("selected-key"))

    def test_disconnected_workspace_locks_shared_review_and_preserves_both_drafts(self):
        w = self.window
        self.stage_color("#123456", w)
        w.assignment_page.editor.set(Action("disabled"), 39)
        draft = self.editor.export_draft()
        assignments_draft = w.assignment_page.editor.draft
        w.keyboard_connected = False
        w.update_actions()
        self.assertTrue(w.lighting_footer.get_sensitive())
        self.assertTrue(w.read_button.get_sensitive())
        self.assertFalse(w.reset_button.is_sensitive())
        self.assertFalse(w.review_button.get_sensitive())
        w.confirm.set_active(True)
        w.update_actions()
        self.assertFalse(w.apply_button.get_sensitive())
        with (patch.object(self.editor, "prepare") as lighting_review,
            patch.object(w.assignment_page.editor, "prepare") as assignment_review):
            w.prepare_review()
            lighting_review.assert_not_called()
            assignment_review.assert_not_called()

        w.keyboard_connected = True
        w.update_actions()
        self.assertTrue(w.lighting_footer.get_sensitive())
        self.assertEqual(self.editor.export_draft(), draft)
        self.assertEqual(w.assignment_page.editor.draft, assignments_draft)

    def test_one_review_contains_both_drafts_and_cancel_preserves_them(self):
        self.present_window()
        w = self.window
        p = w.assignment_page
        p.editor.service.baseline = (bytes(640),)
        p.editor.service.identity = dict(self.service.identity)
        self.stage_color("#123456", w)
        p.editor.set(Action("disabled"), 39)
        for source in list(w.key_buttons)[:25]:
            p.editor.set(Action("disabled"), source)

        p.render()
        w.update_actions()
        w.review_button.emit("clicked")
        self.wait_idle()
        self.assertTrue(w.review_dialog.get_visible())
        self.wait_until(w.review_dialog.get_mapped, "GTK review dialog was not mapped")
        adjustment = w.review_scroll.get_vadjustment()
        self.wait_until(lambda: adjustment.get_page_size() > 0, "GTK review content was not allocated")
        self.assertGreater(adjustment.get_upper(), adjustment.get_page_size())
        _, confirm = w.confirm.compute_bounds(w.review_dialog)
        self.assertLessEqual(confirm.get_y() + confirm.get_height(), w.review_dialog.get_height())
        self.assertFalse(hasattr(p, "review_dialog"))
        self.assertIn("Lighting", w.review_label.get_text())
        self.assertIn("Assignments", w.review_label.get_text())
        self.assertIn("W:", w.review_label.get_text())
        self.assertFalse(w.apply_button.get_sensitive())
        w.cancel_button.emit("clicked")
        self.assertFalse(w.review_dialog.get_visible())
        self.assertTrue(self.editor.has_changes())
        self.assertTrue(p.editor.has_changes())

    def test_lighting_redraw_refreshes_visible_inspector_once(self):
        w = self.window
        w.assignment_page.select(39)
        with (patch.object(w.inspector, "render", wraps=w.inspector.render) as render,
            patch.object(self.editor, "current_keyboard_preview", wraps=self.editor.current_keyboard_preview) as copy):
            w.render_draft()
            render.assert_called_once()
            copy.assert_not_called()
            w.assignment_page.select(90)
            copy.assert_not_called()
            self.service.baseline = lighting.candidate(self.service.baseline,
                dict(style="solid", brightness=16, color="#123456"))
            self.editor.adopt(self.service.state())
            w.render_draft()
            copy.assert_called_once()
            self.assertIn("#123456", w.inspector.lighting_status.get_text())

    def test_assignment_tooltips_skip_unchanged_keys_and_follow_draft_and_capture_changes(self):
        w = self.window
        p = self.assignment_workspace(captured=True)
        changed, untouched = w.key_buttons[39], w.key_buttons[56]
        with (patch.object(changed, "set_tooltip_text", wraps=changed.set_tooltip_text) as updated,
            patch.object(untouched, "set_tooltip_text", wraps=untouched.set_tooltip_text) as unchanged):
            w.render_draft()
            p.select(39)
            updated.assert_not_called()
            unchanged.assert_not_called()
            self.assertTrue(changed.has_css_class("selected-key"))
            self.assertFalse(untouched.has_css_class("selected-key"))
            p.editor.set(Action("disabled"), 39)
            p.render()
            updated.assert_called_once()
            self.assertIn("Disable key", changed.get_tooltip_text())
            p.render()
            updated.assert_called_once()
            p.editor.set(None, 39)
            p.render()
            self.assertEqual(updated.call_count, 2)
            self.assertIn("Factory default", changed.get_tooltip_text())
            table = bytearray(p.editor.service.baseline[0])
            table[156:160] = Action("disabled").record()
            table[638:] = b"\xaa\x55"
            p.editor.service.baseline = (bytes(table),)
            p.render()
            self.assertEqual(updated.call_count, 3)
            self.assertIn("Disabled", changed.get_tooltip_text())
            unchanged.assert_not_called()

    def test_inspector_surfaces_assignment_errors_and_tracks_busy_controls(self):
        w = self.window
        p = w.assignment_page
        w.assignment_page.select(39)
        with patch.object(p.editor, "change_kind", side_effect=ValueError("injected assignment error")):
            p.kind.set_selected(5)

        self.assertTrue(w.inspector.feedback.get_visible())
        self.assertIn("injected assignment error", w.inspector.feedback.get_text())
        self.assertTrue(w.inspector.feedback.has_css_class("error"))
        self.assertEqual(p.kind.get_selected(), 0)
        w.busy = True
        w.update_actions()
        self.assertFalse(w.inspector.source.get_sensitive())
        self.assertFalse(w.inspector.paint.get_sensitive())
        self.assertFalse(p.settings.get_sensitive())
        w.busy = False
        w.update_actions()
        self.assertTrue(w.inspector.source.get_sensitive())
        self.assertTrue(w.inspector.paint.get_sensitive())
        self.assertTrue(p.settings.get_sensitive())

    def test_assignment_status_consistently_warns_about_inactive_tables(self):
        w = self.window
        p = w.assignment_page
        table = bytearray(640)
        table[39 * 4 : 40 * 4] = Action("disabled").record()
        p.editor.service.baseline = (bytes(table),)
        p.render()
        w.assignment_page.select(39)
        self.assertIn("Inactive", w.inspector.assignment_status.get_text())
        self.assertIn("Inactive", w.key_buttons[39].get_tooltip_text())
        self.assertNotIn("Disabled", w.inspector.assignment_status.get_text())

    def test_inspector_reverts_domains_independently_without_device_access(self):
        w = self.window
        p = w.assignment_page
        w.select_color("#123456")
        w.key_buttons[39].set_active(True)
        p.editor.set(Action("disabled"), 39)
        p.render()
        w.assignment_page.select(39)
        with patch.object(lighting.reader, "open_keyboard") as opened:
            w.inspector.revert_lighting.emit("clicked")
            self.assertFalse(self.editor.has_key_change(39))
            self.assertEqual(p.editor.actions[39], Action("disabled"))
            w.inspector.revert_assignment.emit("clicked")
            self.assertNotIn(39, p.editor.actions)
            opened.assert_not_called()

    def test_mode_round_trip_restores_brightness_and_hidden_blanks_keep_reset_available(self):
        w = self.window
        w.brightness.set_value(8)
        w.mode.set_selected(5)
        self.assertEqual(self.editor.draft["brightness"], 16)
        w.mode.set_selected(0)
        self.assertEqual(w.brightness.get_value(), 8)
        self.assertTrue(w.brightness.get_visible())
        w.clear_button.emit("clicked")
        w.mode.set_selected(5)
        self.assertTrue(w.reset_button.get_sensitive())
        self.assertTrue(w.preview_pill.has_css_class("pending"))
        w.mode.set_selected(0)
        self.assertEqual(self.editor.unpainted, lighting.PHYSICAL)
        self.assertFalse(w.review_button.get_sensitive())

    def test_keyboard_preview_colors_are_isolated_between_windows(self):
        def key_names(window):
            return {button.get_name() for button in window.key_buttons.values()}

        settings = {**self.editor.draft, "color": "#FF0000"}
        capture = lighting.candidate(self.service.baseline, settings)
        other = self.create_window(LightingEditor(capture=capture))
        first_names = key_names(self.window)
        other_names = key_names(other)
        self.assertTrue(first_names.isdisjoint(other_names))
        for window, names in ((self.window, first_names), (other, other_names)):
            selectors = set(re.findall(r"#([\w-]+)\s*\{", window.key_css.to_string()))
            self.assertEqual(selectors, names)

    def test_direct_paint_and_native_custom_color_match_presets(self):
        w = self.window
        before = self.service.baseline
        w.select_color("#123456")
        self.assertFalse(self.editor.has_pending_edits())
        w.key_buttons[39].set_active(True)
        self.assertEqual(self.editor.draft["slots"][39]["color"], "#123456")
        self.assertEqual(self.service.baseline, before)
        dialog = Mock()
        dialog.choose_rgba_finish.return_value = self.gui.rgba("#00FFFF")
        dialog.choose_rgba.side_effect = lambda parent, initial, cancellable, callback: callback(dialog, None)
        with patch.object(self.gui.Gtk, "ColorDialog", return_value=dialog):
            w.paint_tools["brush"].custom_button.emit("clicked")

        self.assertTrue(w.paint_tools["brush"].swatches["#00FFFF"].has_css_class("active"))
        self.assertEqual(w.paint_tools["brush"].custom_button.get_label(), "Custom…")
        self.assertEqual(self.editor.draft["slots"][39]["color"], "#123456")

    def test_review_confirmation_and_edit_invalidation(self):
        w = self.window
        self.stage_color("#FF00FF", w)
        w.review_button.emit("clicked")
        self.wait_idle()
        self.assertTrue(w.review_box.get_visible())
        self.assertFalse(w.apply_button.get_sensitive())
        with patch.object(lighting, "execute") as execute:
            w.apply_changes()
            execute.assert_not_called()
            w.confirm.set_active(True)
            self.assertTrue(w.apply_button.get_sensitive())
            w.brightness.set_value(8)
            self.assertIsNone(self.editor.review)
            self.assertFalse(w.apply_button.get_sensitive())
            w.apply_changes()
            execute.assert_not_called()

    def test_per_key_stages_colors_then_resets_or_applies_them_together(self):
        w = self.window
        before = self.service.baseline
        original_slots = self.editor.state["slots"]
        with (patch.object(lighting, "execute", return_value={"lock_indicators": 5}) as execute,
            patch.object(self.service, "read") as read, patch.object(lighting.reader, "open_keyboard") as opened):
            for index, color in ((39, "#123456"), (56, "#ABCDEF")):
                w.select_color(color)
                w.key_buttons[index].set_active(True)

            w.review_button.emit("clicked")
            self.wait_idle()
            w.confirm.set_active(True)
            w.reset_button.emit("clicked")
            self.assertEqual(w.mode.get_selected(), 0)
            self.assertEqual(self.editor.draft["slots"], original_slots)
            self.assertFalse(w.review_box.get_visible())
            self.assertFalse(w.review_button.get_sensitive())
            self.assertFalse(w.reset_button.get_sensitive())
            execute.assert_not_called()
            self.assertEqual(self.service.baseline, before)
            for index, color in ((39, "#123456"), (56, "#ABCDEF"), (57, "#000000")):
                w.select_color(color)
                w.key_buttons[index].set_active(True)

            w.review_button.emit("clicked")
            self.wait_idle()
            w.confirm.set_active(True)
            w.apply_button.emit("clicked")
            self.wait_idle()
            execute.assert_called_once()
            after = execute.call_args.args[1]
            self.assertEqual(after[0x200 + 39 * 4 : 0x204 + 39 * 4], bytes.fromhex("80123456"))
            self.assertEqual(after[0x200 + 56 * 4 : 0x204 + 56 * 4], bytes.fromhex("80ABCDEF"))
            self.assertEqual(after[0x200 + 57 * 4], 0)
            self.assertFalse(self.editor.has_changes())
            read.assert_not_called()
            opened.assert_not_called()

    def test_native_apply_uses_existing_backend_once(self):
        w = self.window
        self.stage_color("#FF00FF", w)
        w.review_button.emit("clicked")
        self.wait_idle()
        w.confirm.set_active(True)
        with patch.object(lighting, "execute", return_value={"lock_indicators": 5}) as execute:
            w.apply_button.emit("clicked")
            self.wait_idle()
            execute.assert_called_once()

        self.assertEqual(self.editor.draft["color"], "#FF00FF")
        self.assertIn("applied and verified", w.status.get_text())

    def test_window_stays_responsive_and_cannot_close_during_device_work(self):
        w = self.window
        p = self.assignment_workspace(captured=True)
        p.editor.set(Action("disabled"), 39)
        w.clear_button.emit("clicked")
        entered, release = threading.Event(), threading.Event()

        def read():
            entered.set()
            release.wait(3)
            return p.editor.service.baseline

        with patch.object(p.editor.service, "read", side_effect=read):
            try:
                w.read_button.emit("clicked")
                deadline = time.monotonic() + 1
                while not entered.is_set() and time.monotonic() < deadline:
                    self.pump()
                    time.sleep(0.005)

                self.assertTrue(entered.is_set())
                self.pump()
                self.assertTrue(w.busy)
                self.assertEqual(w.draft_label.get_text(), "Reading keyboard state…")
                self.assertFalse(w.form.get_sensitive())
                self.assertFalse(p.save_action.get_enabled())
                self.assertTrue(w.on_close())
            finally:
                release.set()

            self.wait_idle()

        self.assertTrue(w.form.get_sensitive())

    def test_native_file_actions_round_trip_draft_without_applying(self):
        w = self.window
        self.stage_color("#123456", w)
        path = Path(self.temporary.name) / "lighting.json"
        dialog = Mock()
        dialog.save_finish.return_value = self.gui.Gio.File.new_for_path(str(path))
        dialog.save.side_effect = lambda parent, cancellable, callback: callback(dialog, None)
        with patch.object(w, "file_dialog", return_value=dialog):
            w.lighting_profile_page.save_action.activate(None)
            self.wait_idle()

        self.assertTrue(path.is_file())
        self.stage_color("#FFFFFF", w)
        dialog.open_finish.return_value = self.gui.Gio.File.new_for_path(str(path))
        dialog.open.side_effect = lambda parent, cancellable, callback: callback(dialog, None)
        discard = Mock()
        discard.choose.side_effect = lambda parent, cancellable, callback: callback(discard, None)
        discard.choose_finish.return_value = 1
        with (patch.object(w, "file_dialog", return_value=dialog),
            patch.object(self.gui.Gtk, "AlertDialog", return_value=discard),
            patch.object(lighting, "execute") as execute):
            w.lighting_profile_page.profile_actions["Open"].activate(None)
            self.wait_idle()
            execute.assert_not_called()

        self.assertEqual(self.editor.draft["color"], "#123456")
        self.assertEqual(self.editor.brush_color, "#FFFFFF")
        self.assertIsNone(self.editor.review)

    def test_presets_clear_fill_off_last_color_and_reset(self):
        w = self.window
        original = self.editor.export_draft()
        w.clear_button.emit("clicked")
        self.assertEqual(self.editor.unpainted, lighting.PHYSICAL)
        self.assertFalse(w.review_button.get_sensitive())
        self.assertFalse(w.lighting_profile_page.save_action.get_enabled())
        self.assertTrue(w.reset_button.get_sensitive())
        w.paint_tools["brush"].swatches["#00FFFF"].emit("clicked")
        w.key_buttons[39].set_active(True)
        w.paint_tools["brush"].swatches["#000000"].emit("clicked")
        w.key_buttons[56].set_active(True)
        self.assertEqual(self.editor.last_used_color, "#00FFFF")
        w.paint_tools["brush"].swatches["#A48BFF"].emit("clicked")
        w.fill_button.emit("clicked")
        self.assertEqual(self.editor.draft["slots"][39]["color"], "#00FFFF")
        self.assertFalse(self.editor.draft["slots"][56]["enabled"])
        self.assertEqual(self.editor.draft["slots"][57]["color"], "#A48BFF")
        self.assertTrue(w.review_button.get_sensitive())
        w.paint_tools["brush"].last_button.emit("clicked")
        self.assertEqual(self.editor.brush_color, "#A48BFF")
        w.reset_button.emit("clicked")
        self.assertEqual(self.editor.export_draft(), original)
        self.assertFalse(w.review_button.get_sensitive())

    def test_animation_key_click_does_not_stage_or_switch_workspaces(self):
        w = self.window
        w.mode.set_selected(3)
        before = self.editor.export_draft()
        w.key_buttons[39].set_active(True)
        self.assertEqual(self.editor.export_draft(), before)
        self.assertFalse(hasattr(w, "assignment_switch"))
        self.assertFalse(w.key_buttons[39].get_active())

    def test_refresh_keeps_pending_edits_and_their_original_baseline(self):
        w = self.window
        self.stage_color("#A48BFF", w)
        draft = self.editor.export_draft()
        baseline = self.service.baseline

        def refresh():
            service = w.assignment_page.editor.service
            service.baseline = (bytes(640),)
            service.identity = dict(self.service.identity)
            return service.baseline

        with (patch.object(w.lighting_profile_page, "confirm_discard") as dialog,
            patch.object(w.assignment_page.editor, "read", side_effect=refresh) as read,
            patch.object(self.service, "read") as lighting_read):
            w.read_button.emit("clicked")
            self.wait_idle()
            read.assert_called_once()
            lighting_read.assert_not_called()
            dialog.assert_not_called()

        self.assertEqual(self.editor.export_draft(), draft)
        self.assertEqual(self.service.baseline, baseline)
        self.assertTrue(self.editor.has_pending_edits())

    def test_open_draft_keeps_pending_edits_until_discard_is_chosen(self):
        w = self.window
        self.stage_color("#123456", w)
        draft = self.editor.export_draft()
        dialog = Mock()
        dialog.choose.side_effect = lambda parent, cancellable, callback: callback(dialog, None)
        dialog.choose_finish.return_value = 0
        with patch.object(self.gui.Gtk, "AlertDialog", return_value=dialog), patch.object(w, "file_dialog") as opened:
            w.lighting_profile_page.open_profile()
            opened.assert_not_called()
            self.assertEqual(self.editor.export_draft(), draft)

        dialog.choose_finish.return_value = 1
        with patch.object(self.gui.Gtk, "AlertDialog", return_value=dialog), patch.object(w, "file_dialog") as opened:
            w.lighting_profile_page.open_profile()
            opened.assert_called_once()

    def test_refresh_protects_visible_and_hidden_blank_drafts_without_prompting(self):
        w = self.window
        w.clear_button.emit("clicked")
        for mode in (0, 5):
            with self.subTest(mode=mode):
                w.mode.set_selected(mode)
                draft = self.editor.draft.copy()
                with (patch.object(w.lighting_profile_page, "confirm_discard") as dialog,
                    patch.object(w.assignment_page.editor, "read", wraps=w.assignment_page.editor.read) as read,
                    patch.object(self.service, "read") as lighting_read):
                    w.read_button.emit("clicked")
                    self.wait_idle()
                    read.assert_called_once()
                    lighting_read.assert_not_called()
                    dialog.assert_not_called()

                self.assertEqual(self.editor.draft, draft)
                self.assertEqual(self.editor.unpainted, lighting.PHYSICAL)
                self.assertTrue(self.editor.has_pending_edits())

    def test_brush_and_animation_speed_do_not_rebuild_preview_css(self):
        w = self.window
        with patch.object(w.key_css, "load_from_string", wraps=w.key_css.load_from_string) as restyle:
            w.select_color("#A48BFF")
            restyle.assert_not_called()
            w.key_buttons[39].set_active(True)
            self.assertEqual(restyle.call_count, 1)

        w.mode.set_selected(1)
        with patch.object(w.key_css, "load_from_string", wraps=w.key_css.load_from_string) as restyle:
            w.speed.set_value(13)
            restyle.assert_not_called()

    def test_failed_draft_import_cleans_up_worker_and_preserves_editor(self):
        w = self.window
        path = Path(self.temporary.name) / "invalid.json"
        path.write_text("not JSON")
        dialog = Mock()
        dialog.open_finish.return_value = self.gui.Gio.File.new_for_path(str(path))
        dialog.open.side_effect = lambda parent, cancellable, callback: callback(dialog, None)
        with patch.object(w, "file_dialog", return_value=dialog):
            w.lighting_profile_page.profile_actions["Open"].activate(None)
            self.assertTrue(w.busy)
            self.wait_idle()

        self.assertIsNone(w.worker)
        self.assertEqual(self.editor.draft["color"], "#00FEFF")
        self.assertTrue(w.status.has_css_class("error"))
        self.assertTrue(w.form.get_sensitive())

    def test_closed_window_cannot_start_a_queued_operation(self):
        w = self.window
        w.on_close()
        with patch.object(self.service, "read") as read:
            w.read_keyboard()
            w.run_operation(self.editor.read, lambda _: None)
            read.assert_not_called()

        self.assertFalse(w.busy)

    def test_close_keeps_edits_until_discard_is_explicitly_chosen(self):
        self.present_window()
        w = self.window
        self.stage_color("#123456", w)
        dialog = Mock()
        with patch.object(w, "close_discard_dialog", return_value=dialog) as make_dialog:
            self.assertTrue(w.on_close())
            self.assertFalse(w._closed)
            self.assertTrue(w.on_close())  # Repeated close requests share one prompt.
            make_dialog.assert_called_once()
            callback = dialog.choose.call_args.args[2]
            dialog.choose_finish.return_value = 0
            callback(dialog, None)
            self.assertFalse(w._closed)
            self.assertEqual(self.editor.draft["slots"][39]["color"], "#123456")
            self.assertFalse(w._close_dialog_pending)
            self.assertTrue(w.on_close())
            callback = dialog.choose.call_args.args[2]
            dialog.choose_finish.return_value = 1
            with patch.object(lighting, "execute") as execute:
                callback(dialog, None)
                self.pump()
                self.assertTrue(w._closed)
                execute.assert_not_called()

    def test_close_dialog_dismissal_preserves_edits(self):
        w = self.window
        self.stage_color("#123456", w)
        dialog = Mock()
        dialog.choose_finish.side_effect = self.gui.GLib.Error("dismissed")
        with patch.object(w, "close_discard_dialog", return_value=dialog):
            self.assertTrue(w.on_close())
            dialog.choose.call_args.args[2](dialog, None)

        self.assertFalse(w._closed)
        self.assertFalse(w._close_dialog_pending)
        self.assertTrue(self.editor.has_changes())

    def test_reset_restores_captured_animation_without_pending_changes(self):
        w = self.window
        self.service.baseline = lighting.candidate(self.service.baseline,
            dict(style="animation", mode=11, brightness=16, speed=12, generated=True, direction=0))
        self.editor.adopt(self.service.state())
        w.render_state()
        w.mode.set_selected(0)
        w.select_color("#123456")
        w.key_buttons[39].set_active(True)
        w.reset_button.emit("clicked")
        self.assertEqual(w.mode.get_selected(), 5)
        self.assertFalse(w.paint_actions.get_visible())
        self.assertTrue(w.speed.get_visible())
        self.assertIn("Rolling", w.preview_pill.get_tooltip_text())
        self.assertFalse(w.review_button.get_sensitive())
        self.assertFalse(w.reset_button.get_sensitive())

    def test_uncertain_apply_displays_warning_and_evidence(self):
        w = self.window
        self.stage_color("#FF00FF", w)
        w.review_button.emit("clicked")
        self.wait_idle()
        w.confirm.set_active(True)
        error = lighting.UncertainWriteError("/tmp/write-evidence", OSError("Input/output error"))
        with patch.object(lighting, "execute", side_effect=error) as execute:
            w.apply_button.emit("clicked")
            self.wait_idle()
            execute.assert_called_once()

        self.assertIn("uncertain", w.status.get_text())
        self.assertIn("Do not retry", w.status.get_text())
        self.assertIn("/tmp/write-evidence", w.status.get_text())
        self.assertTrue(w.status.has_css_class("error"))
        self.assertIsNone(self.editor.review)
        self.assertIsNone(self.service.baseline)
        self.assertFalse(w.apply_button.get_sensitive())

    def open_profile_menu(self, page):
        popover = next(
            popover
            for popover in self.window.menu_popovers()
            if isinstance(popover, self.gui.Gtk.PopoverMenu) and popover.get_menu_model() == self.window.file_menu
        )
        popover.popup()
        self.wait_until(popover.get_mapped, "File menu was not mapped")
        page.submenu_action.change_state(self.gui.GLib.Variant("b", True))
        return popover

    def profile_dialog(self, path):
        dialog = Mock()
        file = self.gui.Gio.File.new_for_path(str(path))
        dialog.save_finish.return_value = file
        dialog.open_finish.return_value = file
        dialog.save.side_effect = lambda parent, cancellable, callback: callback(dialog, None)
        dialog.open.side_effect = lambda parent, cancellable, callback: callback(dialog, None)
        return dialog

    def assignment_workspace(self, *, captured=False):
        w = self.window
        w.update_actions()
        self.wait_idle()
        page = w.assignment_page
        if captured:
            page.editor.service.baseline = (bytes(640),)
            page.editor.service.identity = {"hidraw": "/dev/not-opened", "usb_path": "1-2"}
            page.render()

        return page

    def test_assignment_controls_edit_offline_with_intrinsic_and_extra_modifiers(self):
        p = self.assignment_workspace()
        self.assertEqual(len(p.key_buttons), 115)
        self.assertEqual(self.window.lighting_profile_page.save_action.get_name(), "lighting-save")
        self.assertFalse(self.window.review_button.get_sensitive())
        with patch.object(lighting.reader, "open_keyboard") as opened, patch.object(assignments, "execute") as execute:
            p.kind.set_selected(2)  # Keyboard / shortcut; first output is Left Ctrl.
            self.assertTrue(p.modifiers[1].get_active())
            self.assertFalse(p.modifiers[1].get_sensitive())
            index = next(i for i, target in enumerate(CATALOG["keyboard"]) if target["id"] == 6)
            p.target.set_selected(index)
            p.modifiers[8].set_active(True)
            self.assertEqual(p.editor.selected_action.record(), bytes((2, 8, 6, 0)))
            p.key_buttons[90].emit("clicked")
            p.kind.set_selected(5)  # Disable key.
            self.assertEqual(p.editor.selected_action, Action("disabled"))
            p.key_buttons[39].emit("clicked")
            self.assertEqual(p.editor.selected_action.record(), bytes((2, 8, 6, 0)))
            p.kind.set_selected(0)  # Keep captured assignment, remove planned change.
            self.assertNotIn(39, p.editor.actions)
            self.assertIn(90, p.editor.actions)
            opened.assert_not_called()
            execute.assert_not_called()

    def test_assignment_review_edit_invalidation_and_single_batch_apply(self):
        p = self.assignment_workspace(captured=True)
        p.kind.set_selected(5)
        p.select(90)
        p.kind.set_selected(3)  # Mouse.
        self.window.prepare_review()
        self.wait_idle()
        self.assertTrue(self.window.review_box.get_visible())
        self.window.review_dialog.present.assert_called_once()
        self.assertFalse(self.window.get_mapped())
        self.assertFalse(self.window.review_dialog.get_mapped())
        self.assertFalse(self.window.apply_button.get_sensitive())
        self.window.confirm.set_active(True)
        p.editor.set(Action("keyboard", 4), 90)
        p.invalidate_review()
        self.assertFalse(self.window.review_box.get_visible())
        self.assertIsNone(p.editor.review)
        self.window.prepare_review()
        self.wait_idle()
        after = assignments.candidate(p.editor.service.baseline[0], p.editor.draft)
        verified = (after, *p.editor.service.baseline[1:])
        with patch.object(assignments, "execute",
            return_value=assignments.AssignmentWriteResult(verified, lighting.reader.LockIndicators(5))) as execute:
            self.window.apply_changes()  # No confirmation yet.
            execute.assert_not_called()
            self.window.confirm.set_active(True)
            self.window.apply_button.emit("clicked")
            self.wait_idle()
            execute.assert_called_once()
            self.assertEqual(execute.call_args.args[1], after)
            self.window.apply_changes()
            execute.assert_called_once()

        self.assertEqual(self.window.status.get_text(), "Assignments applied and verified.")
        self.assertFalse(p.editor.has_changes())
        self.assertFalse(self.window.review_button.get_sensitive())

    def test_assignment_read_preserves_staged_changes_and_locks_both_pages_while_busy(self):
        p = self.assignment_workspace()
        p.kind.set_selected(5)
        entered, release = threading.Event(), threading.Event()

        def read():
            entered.set()
            release.wait(3)
            p.editor.service.baseline = (bytes(640),)
            return p.editor.service.baseline

        with (patch.object(p.editor.service, "read", side_effect=read),
            patch.object(self.service, "read", return_value=self.service.state()) as lighting_read):
            try:
                self.window.read_button.emit("clicked")
                deadline = time.monotonic() + 1
                while not entered.is_set() and time.monotonic() < deadline:
                    self.pump()
                    time.sleep(0.005)

                self.assertTrue(entered.is_set())
                self.pump()
                self.assertFalse(p.settings.get_sensitive())
                self.assertFalse(self.window.form.get_sensitive())
                self.assertTrue(self.window.on_close())
            finally:
                release.set()

            self.wait_idle()

        self.assertEqual(p.editor.actions[39], Action("disabled"))
        lighting_read.assert_called_once()
        self.assertTrue(p.settings.get_sensitive())
        self.assertTrue(self.window.review_button.get_sensitive())

    def test_uncertain_assignment_upload_blocks_both_workflows_and_survives_refresh(self):
        p = self.assignment_workspace(captured=True)
        p.kind.set_selected(5)
        self.window.prepare_review()
        self.wait_idle()
        self.window.confirm.set_active(True)
        error = assignments.AssignmentWriteError("/tmp/assignment-evidence", OSError("injected"), uncertain=True)
        with patch.object(assignments, "execute", side_effect=error):
            self.window.apply_changes()
            self.wait_idle()

        self.assertIn("Do not retry", self.window.status.get_text())
        self.assertTrue(self.window.inspection_banner.get_visible())
        self.assertFalse(self.window.review_button.get_sensitive())
        self.assertTrue(self.window.inspection_records_button.get_visible())
        self.assertEqual(self.service.guard.evidence, "/tmp/assignment-evidence")
        self.window.update_actions()
        self.stage_color("#FF00FF", self.window)
        self.assertFalse(self.window.review_button.get_sensitive())
        self.assertTrue(self.window.inspection_banner.get_visible())
        self.assertIs(p.editor.service.guard, self.service.guard)

    def test_both_profile_menus_save_updates_and_new_asks_for_a_separate_file(self):
        w = self.window
        for p in (w.lighting_profile_page, w.assignment_page):
            with (self.subTest(menu=p.title), patch.object(lighting, "execute") as lighting_write,
                patch.object(assignments, "execute") as assignment_write):
                first = Path(self.temporary.name) / (p.title + " First.json")
                dialog = self.profile_dialog(first)
                with patch.object(w, "file_dialog", return_value=dialog):
                    p.save_action.activate(None)
                    self.wait_idle()

                saved = json.loads(first.read_text())
                self.assertEqual(p.current_file.get_path(), str(first))
                if p is w.assignment_page:
                    p.kind.set_selected(5)
                else:
                    self.stage_color("#123456", w)

                changed = p.editor.draft
                with patch.object(w, "file_dialog") as chooser:
                    p.save_action.activate(None)
                    self.wait_idle()
                    chooser.assert_not_called()

                updated = json.loads(first.read_text())
                self.assertEqual(updated["id"], saved["id"])
                self.assertEqual(updated[p.payload], changed)
                p.profile_actions["New"].activate(None)
                self.assertIsNone(p.current_file)
                if p is w.lighting_profile_page:
                    self.assertEqual(w.editor.unpainted, lighting.PHYSICAL)
                    self.assertFalse(p.save_action.get_enabled())
                    w.fill_button.emit("clicked")
                else:
                    self.assertEqual(p.editor.actions, {})

                second = first.with_name(p.title + " Second.json")
                with patch.object(w, "file_dialog", return_value=self.profile_dialog(second)):
                    p.save_action.activate(None)
                    self.wait_idle()

                self.assertNotEqual(json.loads(second.read_text())["id"], saved["id"])
                self.assertEqual(json.loads(first.read_text()), updated)
                with patch.object(w, "file_dialog", return_value=self.profile_dialog(first)):
                    p.profile_actions["Open"].activate(None)
                    self.wait_idle()

                self.assertEqual(p.editor.draft, changed)
                self.assertEqual(p.current_file.get_path(), str(first))
                lighting_write.assert_not_called()
                assignment_write.assert_not_called()

    def test_both_profile_menus_preserve_edits_when_new_or_open_is_canceled(self):
        w = self.window
        for p in (w.lighting_profile_page, w.assignment_page):
            with self.subTest(menu=p.title):
                path = Path(self.temporary.name) / (p.title + ".json")
                with patch.object(w, "file_dialog", return_value=self.profile_dialog(path)):
                    p.save_profile()
                    self.wait_idle()

                if p is w.assignment_page:
                    p.kind.set_selected(5)
                else:
                    self.stage_color("#123456", w)

                draft = p.editor.draft
                file = p.current_file
                dialog = Mock()
                dialog.choose.side_effect = lambda parent, cancellable, callback: callback(dialog, None)
                dialog.choose_finish.return_value = 0
                with (patch.object(self.gui.Gtk, "AlertDialog", return_value=dialog),
                    patch.object(w, "file_dialog") as chooser):
                    for action in ("New", "Open"):
                        p.profile_actions[action].activate(None)
                        self.assertEqual(p.editor.draft, draft)
                        self.assertIs(p.current_file, file)

                    chooser.assert_not_called()

    def test_hidden_lighting_draft_is_protected_after_saving_an_animation_profile(self):
        p = self.window.lighting_profile_page
        self.editor.select_mode(11)
        path = Path(self.temporary.name) / "Animation.json"
        with patch.object(self.window, "file_dialog", return_value=self.profile_dialog(path)):
            p.save_profile()
            self.wait_idle()

        self.editor.select_mode(128)
        self.editor.clear_paint()
        self.editor.select_mode(11)
        p.save_profile()
        self.wait_idle()
        dialog = Mock()
        dialog.choose.side_effect = lambda parent, cancellable, callback: callback(dialog, None)
        dialog.choose_finish.return_value = 0
        with patch.object(self.gui.Gtk, "AlertDialog", return_value=dialog):
            p.open_profile()
            self.assertEqual(self.editor.unpainted, lighting.PHYSICAL)
            dialog.choose.assert_called_once()

    def test_profile_shortcuts_use_the_open_assignment_menu_and_block_during_work(self):
        w = self.window
        p = self.assignment_workspace()
        p.kind.set_selected(5)
        path = Path(self.temporary.name) / "Assignments.json"
        p.submenu_action.change_state(self.gui.GLib.Variant("b", True))
        self.assertIs(w.active_profile_page, p)
        with patch.object(w, "file_dialog", return_value=self.profile_dialog(path)):
            self.assertTrue(w.activate_profile_shortcut(saving=True))
            self.wait_idle()
            self.assertTrue(path.is_file())
            self.assertIsNone(w.lighting_profile_page.current_file)
            p.editor.clear()
            p.editor.saved(p.editor.draft)
            self.assertTrue(w.activate_profile_shortcut(saving=False))
            self.wait_idle()
            self.assertEqual(p.editor.actions[39], Action("disabled"))
            w.busy = True
            try:
                self.assertFalse(w.activate_profile_shortcut(saving=True))
            finally:
                w.busy = False

    def test_lighting_profiles_can_be_saved_with_a_capture_after_disconnection(self):
        w = self.window
        p = w.lighting_profile_page
        path = Path(self.temporary.name) / "Offline capture.json"
        w.keyboard_connected = False
        w.update_actions()
        self.assertTrue(p.save_action.get_enabled())
        with (patch.object(lighting, "execute") as execute,
            patch.object(w, "file_dialog", return_value=self.profile_dialog(path))):
            p.save_action.activate(None)
            self.wait_idle()
            execute.assert_not_called()

        self.assertEqual(json.loads(path.read_text())[p.payload], p.editor.draft)
        self.assertTrue(p.profile_actions["Open"].get_enabled())

    def test_large_profile_files_saved_from_the_menu_can_be_opened_again(self):
        p = self.window.lighting_profile_page
        self.editor.draft["notes"] = "x" * 123000
        path = Path(self.temporary.name) / "Large.json"
        with patch.object(self.window, "file_dialog", return_value=self.profile_dialog(path)):
            p.save_profile()
            self.wait_idle()

        self.assertEqual(decode_profile(path.read_text())["settings"], p.editor.draft)
        self.assertLessEqual(path.stat().st_size, p.file_limit)

    def test_assignment_profile_files_round_trip_without_writing_device(self):
        p = self.assignment_workspace()
        p.kind.set_selected(5)
        path = Path(self.temporary.name) / "assignments.json"
        with (patch.object(self.window, "file_dialog", return_value=self.profile_dialog(path)),
            patch.object(assignments, "execute") as execute):
            self.assertTrue(p.save_action.get_enabled())
            p.save_action.activate(None)
            self.wait_idle()
            saved = json.loads(path.read_text())
            self.assertEqual(saved["format"], "c989-profile-v2")
            self.assertEqual(saved["keyAssignments"], p.editor.draft)
            p.editor.clear()
            p.editor.saved(p.editor.draft)
            p.profile_actions["Open"].activate(None)
            self.wait_idle()
            self.assertEqual(p.editor.actions[39], Action("disabled"))
            execute.assert_not_called()

    def test_profile_menus_have_only_file_actions_and_no_name_field(self):
        for p in (self.window.lighting_profile_page, self.window.assignment_page):
            with self.subTest(menu=p.title):
                self.assertEqual(list(p.profile_actions), ["New", "Open", "Save", "Save As…", "Delete current"])
                self.assertFalse(hasattr(p, "profile_name"))
                path = Path(self.temporary.name) / (p.title + ".json")
                dialog = self.profile_dialog(path)
                with patch.object(self.window, "file_dialog", return_value=dialog):
                    p.save_profile()
                    self.wait_idle()

                folder = dialog.set_initial_folder.call_args.args[0]
                self.assertEqual(Path(folder.get_path()), Path(self.temporary.name) / ".config/KeyboardLights/Profiles")
                self.assertTrue(Path(folder.get_path()).is_dir())
                self.assertEqual(json.loads(path.read_text())["name"], path.stem)
                self.assertEqual(p.current_file.get_basename(), path.name)

    def test_save_as_switches_files_and_leaves_original_unchanged(self):
        w = self.window
        for p in (w.lighting_profile_page, w.assignment_page):
            with self.subTest(menu=p.title):
                original = Path(self.temporary.name) / (p.title + ".json")
                copy = original.with_name(p.title + " Copy.json")
                with patch.object(w, "file_dialog", return_value=self.profile_dialog(original)):
                    p.save_profile()
                    self.wait_idle()

                before = original.read_bytes()
                if p is w.assignment_page:
                    p.editor.set(Action("disabled"), 39)
                else:
                    self.stage_color("#123456", w)

                draft = p.editor.draft
                chooser = self.profile_dialog(copy)
                with patch.object(w, "file_dialog", return_value=chooser):
                    p.profile_actions["Save As…"].activate(None)
                    self.wait_idle()

                value = json.loads(copy.read_text())
                self.assertEqual(value[p.payload], draft)
                self.assertEqual(value["name"], copy.stem)
                self.assertNotEqual(value["id"], json.loads(before)["id"])
                self.assertEqual(original.read_bytes(), before)
                self.assertEqual(p.current_file.get_path(), str(copy))
                self.assertFalse(p.unsaved)
                chooser.set_initial_name.assert_called_once_with(original.name)
                with patch.object(w, "file_dialog") as dialog:
                    p.save_profile()
                    self.wait_idle()
                    dialog.assert_not_called()

                self.assertEqual(original.read_bytes(), before)
                self.assertEqual(json.loads(copy.read_text())["id"], value["id"])

    def test_save_as_through_a_symlink_preserves_the_original_profile(self):
        w = self.window
        for page in (w.lighting_profile_page, w.assignment_page):
            with self.subTest(menu=page.title):
                original = Path(self.temporary.name) / (page.title + ".json")
                with patch.object(w, "file_dialog", return_value=self.profile_dialog(original)):
                    page.save_profile()
                    self.wait_idle()

                before = original.read_bytes()
                alias = original.with_name(page.title + " Copy.json")
                alias.symlink_to(original)
                if page is w.assignment_page:
                    page.editor.set(Action("disabled"), 39)
                else:
                    self.stage_color("#123456", w)

                draft = page.editor.draft
                with patch.object(w, "file_dialog", return_value=self.profile_dialog(alias)):
                    page.save_profile_as()
                    self.wait_idle()

                self.assertEqual(original.read_bytes(), before)
                self.assertFalse(alias.is_symlink())
                self.assertEqual(json.loads(alias.read_text())[page.payload], draft)
                self.assertNotEqual(json.loads(alias.read_text())["id"], json.loads(before)["id"])
                self.assertEqual(page.current_file.get_path(), str(alias))

    def test_profiles_reject_fifo_and_directory_for_open_and_save(self):
        w = self.window
        for page in (w.lighting_profile_page, w.assignment_page):
            with self.subTest(menu=page.title):
                if page is w.assignment_page:
                    page.editor.set(Action("disabled"), 39)
                else:
                    self.stage_color("#123456", w)

                draft = page.editor.draft
                fifo = Path(self.temporary.name) / (page.title + " Pipe.json")
                os.mkfifo(fifo)
                directory = fifo.with_name(page.title + " Directory.json")
                directory.mkdir()
                for path in (fifo, directory):
                    with (self.subTest(path=path),
                        patch.object(w, "file_dialog", return_value=self.profile_dialog(path))):
                        page.save_profile()
                        self.wait_idle()
                        self.assertIsNone(page.current_file)
                        self.assertEqual(page.editor.draft, draft)
                        self.assertTrue(page.unsaved)
                        self.assertIn("regular file", w.status.get_text())
                        with patch.object(page, "confirm_discard", side_effect=lambda _, action: action()):
                            page.open_profile()
                            self.wait_idle()

                        self.assertIsNone(page.current_file)
                        self.assertEqual(page.editor.draft, draft)
                        self.assertTrue(page.unsaved)
                        self.assertIn("regular file", w.status.get_text())

                self.assertTrue(stat.S_ISFIFO(fifo.stat().st_mode))
                self.assertTrue(directory.is_dir())

    def test_save_keeps_opened_symlink_and_updates_its_regular_target(self):
        w = self.window
        for page in (w.lighting_profile_page, w.assignment_page):
            with self.subTest(menu=page.title):
                original = Path(self.temporary.name) / (page.title + ".json")
                with patch.object(w, "file_dialog", return_value=self.profile_dialog(original)):
                    page.save_profile()
                    self.wait_idle()

                identifier = json.loads(original.read_text())["id"]
                link = original.with_name(page.title + " Link.json")
                link.symlink_to(original)
                with patch.object(w, "file_dialog", return_value=self.profile_dialog(link)):
                    page.open_profile()
                    self.wait_idle()

                self.assertEqual(page.current_file.get_path(), str(link))
                if page is w.assignment_page:
                    page.editor.set(Action("disabled"), 39)
                else:
                    self.stage_color("#123456", w)

                draft = page.editor.draft
                page.save_profile()
                self.wait_idle()
                self.assertTrue(link.is_symlink())
                value = json.loads(original.read_text())
                self.assertEqual(value[page.payload], draft)
                self.assertEqual(value["id"], identifier)
                self.assertFalse(page.unsaved)

    def test_direct_save_rejects_a_profile_replaced_by_a_fifo(self):
        w = self.window
        for page in (w.lighting_profile_page, w.assignment_page):
            with self.subTest(menu=page.title):
                path = Path(self.temporary.name) / (page.title + ".json")
                with patch.object(w, "file_dialog", return_value=self.profile_dialog(path)):
                    page.save_profile()
                    self.wait_idle()

                file = page.current_file
                if page is w.assignment_page:
                    page.editor.set(Action("disabled"), 39)
                else:
                    self.stage_color("#123456", w)

                draft = page.editor.draft
                path.unlink()
                os.mkfifo(path)
                page.save_profile()
                self.wait_idle()
                self.assertTrue(stat.S_ISFIFO(path.stat().st_mode))
                self.assertIs(page.current_file, file)
                self.assertEqual(page.editor.draft, draft)
                self.assertTrue(page.unsaved)
                self.assertIn("regular file", w.status.get_text())

    def test_canceled_or_failed_save_as_keeps_current_file_and_draft(self):
        w = self.window
        for p in (w.lighting_profile_page, w.assignment_page):
            with self.subTest(menu=p.title):
                original = Path(self.temporary.name) / (p.title + ".json")
                with patch.object(w, "file_dialog", return_value=self.profile_dialog(original)):
                    p.save_profile()
                    self.wait_idle()

                file, etag, value = p.current_file, p._etag, p._profile
                if p is w.assignment_page:
                    p.editor.set(Action("disabled"), 39)
                else:
                    self.stage_color("#123456", w)

                draft = p.editor.draft
                missing = original.parent / "Missing" / original.name
                chooser = self.profile_dialog(missing)
                dismissed = self.gui.GLib.Error.new_literal(self.gui.Gtk.dialog_error_quark(), "Dismissed",
                    self.gui.Gtk.DialogError.DISMISSED)
                chooser.save_finish.side_effect = dismissed
                with patch.object(w, "file_dialog", return_value=chooser):
                    p.save_profile_as()
                    self.assertIs(p.current_file, file)
                    self.assertEqual(p._etag, etag)
                    self.assertEqual(p._profile, value)
                    chooser.save_finish.side_effect = None
                    p.save_profile_as()
                    self.wait_idle()

                self.assertIs(p.current_file, file)
                self.assertEqual(p._etag, etag)
                self.assertEqual(p._profile, value)
                self.assertEqual(p.editor.draft, draft)
                self.assertTrue(p.unsaved)
                self.assertFalse(missing.exists())

    def test_save_as_same_file_keeps_identity_and_checks_external_changes(self):
        w = self.window
        for p in (w.lighting_profile_page, w.assignment_page):
            with self.subTest(menu=p.title):
                path = Path(self.temporary.name) / (p.title + ".json")
                with patch.object(w, "file_dialog", return_value=self.profile_dialog(path)):
                    p.save_profile()
                    self.wait_idle()
                    identifier = json.loads(path.read_text())["id"]
                    p.save_profile_as()
                    self.wait_idle()
                    self.assertEqual(json.loads(path.read_text())["id"], identifier)
                    changed = json.loads(path.read_text())
                    changed["name"] = "Changed elsewhere"
                    path.write_text(json.dumps(changed))
                    p.save_profile_as()
                    self.wait_idle()

                self.assertEqual(json.loads(path.read_text()), changed)
                self.assertIn("changed on disk", w.status.get_text())

    def test_delete_current_requires_confirmation_and_preserves_the_draft(self):
        self.present_window()
        w = self.window
        for p in (w.lighting_profile_page, w.assignment_page):
            with self.subTest(menu=p.title):
                self.assertFalse(p.profile_actions["Delete current"].get_enabled())
                with patch.object(self.gui.Gtk, "AlertDialog") as prompt:
                    p.delete_current()
                    prompt.assert_not_called()

                path = Path(self.temporary.name) / (p.title + ".json")
                with patch.object(w, "file_dialog", return_value=self.profile_dialog(path)):
                    p.save_profile()
                    self.wait_idle()

                self.assertTrue(p.profile_actions["Delete current"].get_enabled())
                self.assertFalse(p.unsaved)
                draft, file = p.editor.draft, p.current_file
                dialog = Mock()
                dialog.choose_finish.return_value = 0

                def confirm(*_):
                    self.assertFalse(popover.get_visible())
                    self.assertFalse(popover.get_mapped())

                dialog.choose.side_effect = confirm
                popover = self.open_profile_menu(p)
                with patch.object(self.gui.Gtk, "AlertDialog", return_value=dialog):
                    p.profile_actions["Delete current"].activate(None)
                    self.assertTrue(path.exists())
                    callback = dialog.choose.call_args.args[2]
                    callback(dialog, None)
                    self.assertTrue(path.exists())
                    self.assertIs(p.current_file, file)
                    self.assertFalse(p.unsaved)
                    dialog.choose_finish.return_value = 1
                    p.profile_actions["Delete current"].activate(None)
                    dialog.choose.call_args.args[2](dialog, None)
                    self.wait_idle()

                self.assertFalse(path.exists())
                self.assertIsNone(p.current_file)
                self.assertFalse(p.profile_actions["Delete current"].get_enabled())
                self.assertEqual(p.editor.draft, draft)
                self.assertTrue(p.unsaved)
                with patch.object(w, "close_discard_dialog") as close:
                    self.assertTrue(w.on_close())
                    close.assert_called_once()

                w._close_dialog_pending = False
                replacement = path.with_name("Restored " + path.name)
                with patch.object(w, "file_dialog", return_value=self.profile_dialog(replacement)) as chooser:
                    p.save_profile()
                    self.wait_idle()
                    chooser.assert_called_once()

                self.assertEqual(json.loads(replacement.read_text())[p.payload], draft)
                self.assertFalse(p.unsaved)

    def test_delete_current_preserves_changed_or_unwritable_files(self):
        w = self.window
        for p in (w.lighting_profile_page, w.assignment_page):
            with self.subTest(menu=p.title):
                path = Path(self.temporary.name) / (p.title + ".json")
                with patch.object(w, "file_dialog", return_value=self.profile_dialog(path)):
                    p.save_profile()
                    self.wait_idle()

                file, draft = p.current_file, p.editor.draft
                dialog = Mock()
                dialog.choose_finish.return_value = 1
                dialog.choose.side_effect = lambda parent, cancellable, callback: callback(dialog, None)
                with (patch.object(self.gui.Gtk, "AlertDialog", return_value=dialog),
                    patch.object(self.gui.Gio.File, "delete", side_effect=PermissionError("cannot delete"))):
                    p.delete_current()
                    self.wait_idle()

                self.assertTrue(path.exists())
                self.assertIs(p.current_file, file)
                self.assertEqual(p.editor.draft, draft)
                self.assertIn("cannot delete", w.status.get_text())
                external = json.loads(path.read_text())
                external["name"] = "Changed elsewhere"
                path.write_text(json.dumps(external))
                with (patch.object(self.gui.Gtk, "AlertDialog", return_value=dialog),
                    patch.object(self.gui.Gio.File, "delete") as delete):
                    p.delete_current()
                    self.wait_idle()
                    delete.assert_not_called()

                self.assertEqual(json.loads(path.read_text()), external)
                self.assertIs(p.current_file, file)
                self.assertEqual(p.editor.draft, draft)
                self.assertIn("changed on disk", w.status.get_text())

    def test_closed_window_ignores_pending_profile_delete_confirmation(self):
        w = self.window
        p = w.lighting_profile_page
        path = Path(self.temporary.name) / "Profile.json"
        with patch.object(w, "file_dialog", return_value=self.profile_dialog(path)):
            p.save_profile()
            self.wait_idle()

        dialog = Mock()
        dialog.choose_finish.return_value = 1
        with patch.object(self.gui.Gtk, "AlertDialog", return_value=dialog):
            p.delete_current()
            self.close_window(w)
            dialog.choose.call_args.args[2](dialog, None)

        self.assertTrue(path.exists())
        self.assertIsNotNone(p.current_file)

    def test_canceled_file_choosers_preserve_draft_and_current_file(self):
        w = self.window
        for p in (w.lighting_profile_page, w.assignment_page):
            with self.subTest(menu=p.title):
                path = Path(self.temporary.name) / (p.title + ".json")
                draft = p.editor.draft
                dialog = self.profile_dialog(path)
                dismissed = self.gui.GLib.Error.new_literal(self.gui.Gtk.dialog_error_quark(), "Dismissed",
                    self.gui.Gtk.DialogError.DISMISSED)
                dialog.save_finish.side_effect = dismissed
                dialog.open_finish.side_effect = dismissed
                with patch.object(w, "file_dialog", return_value=dialog):
                    p.save_profile()
                    p.open_profile()

                self.assertIsNone(p.current_file)
                self.assertEqual(p.editor.draft, draft)
                self.assertFalse(p._dialog_pending)
                self.assertFalse(path.exists())
                self.assertFalse(w.busy)

    def test_profile_menus_close_before_open_and_save_file_dialogs(self):
        self.present_window()
        w = self.window
        for p in (w.lighting_profile_page, w.assignment_page):
            for action in ("Open", "Save", "Save As…"):
                with self.subTest(menu=p.title, action=action):
                    popover = self.open_profile_menu(p)
                    dialog = Mock()

                    def opened(*_):
                        self.assertFalse(popover.get_visible())
                        self.assertFalse(popover.get_mapped())

                    dialog.open.side_effect = opened
                    dialog.save.side_effect = opened
                    dismissed = self.gui.GLib.Error.new_literal(self.gui.Gtk.dialog_error_quark(), "Dismissed",
                        self.gui.Gtk.DialogError.DISMISSED)
                    dialog.open_finish.side_effect = dismissed
                    dialog.save_finish.side_effect = dismissed
                    with patch.object(w, "file_dialog", return_value=dialog):
                        p.profile_actions[action].activate(None)

                    choose = dialog.open if action == "Open" else dialog.save
                    choose.assert_called_once()
                    choose.call_args.args[2](dialog, None)
                    self.assertFalse(popover.get_visible())

    def test_profile_menus_close_before_unsaved_changes_confirmation(self):
        self.present_window()
        w = self.window
        self.stage_color("#123456", w)
        w.assignment_page.editor.set(Action("disabled"), 39)
        for p in (w.lighting_profile_page, w.assignment_page):
            with self.subTest(menu=p.title):
                popover = self.open_profile_menu(p)
                draft = p.editor.draft
                dialog = Mock()

                def confirm(*_):
                    self.assertFalse(popover.get_visible())
                    self.assertFalse(popover.get_mapped())

                dialog.choose.side_effect = confirm
                dialog.choose_finish.return_value = 0
                with patch.object(self.gui.Gtk, "AlertDialog", return_value=dialog):
                    p.profile_actions["Open"].activate(None)

                dialog.choose.assert_called_once()
                dialog.choose.call_args.args[2](dialog, None)
                self.assertEqual(p.editor.draft, draft)

    def test_profile_save_conflict_preserves_external_file_and_unsaved_draft(self):
        w = self.window
        for p in (w.lighting_profile_page, w.assignment_page):
            with self.subTest(menu=p.title):
                path = Path(self.temporary.name) / (p.title + ".json")
                with patch.object(w, "file_dialog", return_value=self.profile_dialog(path)):
                    p.save_profile()
                    self.wait_idle()

                value = json.loads(path.read_text())
                value["name"] = "Changed in another app"
                path.write_text(json.dumps(value))
                if p is w.assignment_page:
                    p.kind.set_selected(5)
                else:
                    self.stage_color("#123456", w)

                draft = p.editor.draft
                p.save_profile()
                self.wait_idle()
                self.assertEqual(json.loads(path.read_text()), value)
                self.assertEqual(p.editor.draft, draft)
                self.assertTrue(p.unsaved)
                self.assertIn("changed on disk", w.status.get_text())

    def test_failed_profile_save_keeps_draft_and_can_retry(self):
        w = self.window
        for p in (w.lighting_profile_page, w.assignment_page):
            with self.subTest(menu=p.title):
                path = Path(self.temporary.name) / p.title / "Missing directory" / "Profile.json"
                if p is w.assignment_page:
                    p.kind.set_selected(5)
                else:
                    self.stage_color("#123456", w)

                draft = p.editor.draft
                with patch.object(w, "file_dialog", return_value=self.profile_dialog(path)):
                    p.save_profile()
                    self.wait_idle()
                    self.assertIsNone(p.current_file)
                    self.assertEqual(p.editor.draft, draft)
                    self.assertTrue(p.unsaved)
                    self.assertTrue(w.status.has_css_class("error"))
                    path.parent.mkdir(parents=True)
                    p.save_profile()
                    self.wait_idle()

                self.assertEqual(json.loads(path.read_text())[p.payload], draft)
                self.assertFalse(p.unsaved)

    def test_closed_window_ignores_pending_profile_file_selection(self):
        w = self.window
        p = w.lighting_profile_page
        path = Path(self.temporary.name) / "Profile.json"
        dialog = self.profile_dialog(path)
        dialog.save.side_effect = None
        with patch.object(w, "file_dialog", return_value=dialog):
            p.save_profile()
            p.save_profile()
            dialog.save.assert_called_once()
            self.close_window(w)
            callback = dialog.save.call_args.args[2]
            callback(dialog, None)

        self.assertFalse(path.exists())
        self.assertIsNone(p.current_file)

    def test_editing_during_profile_save_keeps_the_newer_draft_unsaved(self):
        p = self.assignment_workspace()
        p.kind.set_selected(5)
        draft = p.editor.draft
        path = Path(self.temporary.name) / "Snapshot.json"

        def save_and_edit(work, saved, **_):
            result = work()
            p.editor.set(Action("keyboard", 4), 90)
            saved(result)
            p.update_actions()

        with (patch.object(self.window, "file_dialog", return_value=self.profile_dialog(path)),
            patch.object(self.window, "run_operation", side_effect=save_and_edit)):
            p.save_profile()

        self.assertEqual(json.loads(path.read_text())[p.payload], draft)
        self.assertTrue(p.unsaved)
        self.assertIn(90, p.editor.actions)

    def test_assignment_new_and_window_close_preserve_draft_until_confirmed(self):
        p = self.assignment_workspace()
        p.kind.set_selected(5)
        dialog = Mock()
        dialog.choose.side_effect = lambda parent, cancellable, callback: callback(dialog, None)
        dialog.choose_finish.return_value = 0
        with patch.object(self.gui.Gtk, "AlertDialog", return_value=dialog):
            p.new_profile()
            self.assertIn(39, p.editor.actions)
            self.assertTrue(self.window.on_close())
            self.assertFalse(self.window._closed)
            dialog.choose_finish.return_value = 1
            p.new_profile()
            self.assertEqual(p.editor.actions, {})

    def test_offline_capture_disables_assignment_device_access_but_keeps_editor_available(self):
        w = self.create_window(LightingEditor(capture=baseline_image()))
        p = w.assignment_page
        w.update_actions()
        p.kind.set_selected(5)
        self.assertIsNone(p.editor.service)
        self.assertFalse(w.read_button.get_visible())
        self.assertFalse(w.review_button.get_sensitive())
        self.assertTrue(w.lighting_profile_page.save_action.get_enabled())
        self.assertEqual(p.editor.actions[39], Action("disabled"))

    def test_profile_file_save_can_recover_when_worker_start_fails(self):
        p = self.assignment_workspace()
        p.kind.set_selected(5)
        path = Path(self.temporary.name) / "My work.json"
        draft = p.editor.draft
        with (patch.object(self.window, "file_dialog", return_value=self.profile_dialog(path)),
            patch.object(self.gui.threading.Thread, "start", side_effect=RuntimeError("worker unavailable"))):
            p.save_profile()

        self.assertFalse(self.window.busy)
        self.assertEqual(p.editor.draft, draft)
        self.assertTrue(p.editor.unsaved)
        self.assertIsNone(p.current_file)
        with patch.object(self.window, "file_dialog", return_value=self.profile_dialog(path)):
            p.save_profile()
            self.wait_idle()

        self.assertEqual(json.loads(path.read_text())[p.payload], draft)
        self.assertFalse(p.editor.unsaved)

    def test_verified_assignment_with_evidence_failure_is_described_accurately(self):
        p = self.assignment_workspace(captured=True)
        p.kind.set_selected(5)
        self.window.prepare_review()
        self.wait_idle()
        self.window.confirm.set_active(True)
        error = assignments.AssignmentWriteError("/tmp/verified-evidence", OSError("disk full"), uncertain=True,
            verified=True)
        with patch.object(assignments, "execute", side_effect=error):
            self.window.apply_changes()
            self.wait_idle()

        self.assertIn("Key assignments were updated", self.window.status.get_text())
        self.assertNotIn("may be incomplete", self.window.status.get_text())
        self.assertTrue(self.window.inspection_banner.get_visible())
        self.assertFalse(self.window.review_button.get_sensitive())

    def test_draft_changed_during_review_is_not_presented_or_written(self):
        p = self.assignment_workspace(captured=True)
        p.kind.set_selected(5)
        plan = p.editor.service.plan

        def changed(draft):
            review = plan(draft)
            p.editor.set(Action("keyboard", 4), 90)
            return review

        with (patch.object(p.editor.service, "plan", side_effect=changed),
            patch.object(assignments, "execute") as execute):
            self.window.prepare_review()
            self.wait_idle()
            self.assertFalse(self.window.review_dialog.get_visible())
            self.window.review_dialog.present.assert_not_called()
            self.assertIsNone(p.editor.review)
            self.assertIn("changed during review", self.window.status.get_text())
            self.window.apply_changes()
            execute.assert_not_called()

    def test_planned_assignments_track_edits_and_per_key_revert_without_a_draft_list(self):
        p = self.assignment_workspace()
        p.editor.set(Action("disabled"), 39)
        p.editor.set(Action("keyboard", 4), 90)
        p.render()
        self.assertTrue(p.key_buttons[39].has_css_class("planned"))
        self.assertTrue(p.key_buttons[90].has_css_class("planned"))
        p.editor.set(Action("keyboard", 6, 1), 39)
        p.render()
        self.assertIn("Left Ctrl + C", self.window.inspector.assignment_status.get_text())
        p.editor.set(Action("media", 2), 17)
        p.render()
        p.select(39)
        self.window.inspector.revert_assignment.emit("clicked")
        self.assertNotIn(39, p.editor.actions)
        p.select(90)
        p.key_buttons[90].emit("clicked")
        self.assertEqual(p.editor.selected_source, 90)
        self.assertTrue(p.key_buttons[90].has_css_class("selected-key"))
        self.assertFalse(p.key_buttons[39].get_active())
        self.assertFalse(p.key_buttons[39].has_css_class("planned"))
        self.assertTrue(p.key_buttons[17].has_css_class("planned"))


if __name__ == "__main__":
    unittest.main()
