"""Shared file actions for lighting and assignment profiles."""

from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk

from keyboardlights.assignments.model import MAX_PROFILE_BYTES, MAX_PROFILE_NAME_LENGTH, decode_profile, profile
from keyboardlights.profiles.io import encode_profile, read_draft_file
from keyboardlights.ui.presentation import error_message


class ProfilePage:
    payload = "keyAssignments"
    file_limit = MAX_PROFILE_BYTES
    make_profile = staticmethod(profile)
    decode_profile = staticmethod(decode_profile)

    def __init__(self, window, editor, title, action_prefix):
        self.window = window
        self.editor = editor
        self.title = title
        self.action_prefix = action_prefix
        self.profile_directory = Path(GLib.get_user_config_dir()) / "KeyboardLights/Profiles"
        self.current_file = None
        self._etag = None
        self._profile = None
        self._dialog_pending = False
        self._build_menu()

    @property
    def unsaved(self):
        return self.editor.unsaved

    @property
    def can_edit(self):
        return True

    @property
    def draft_available(self):
        return True

    @property
    def ready(self):
        return not self.window.busy and not self.window._closed and not self._dialog_pending

    def update_actions(self):
        ready = self.ready
        editable = ready and self.can_edit
        savable = ready and self.draft_available
        self.profile_actions["New"].set_enabled(editable)
        self.profile_actions["Open"].set_enabled(editable)
        self.save_action.set_enabled(savable)
        self.profile_actions["Save As…"].set_enabled(savable)
        self.profile_actions["Delete current"].set_enabled(ready and self.current_file is not None)

    def _build_menu(self):
        self.menu = Gio.Menu()
        self.profile_actions = {}
        for label, suffix, callback in [("New", "new", self.new_profile), ("Open", "open", self.open_profile),
            ("Save", "save", self.save_profile), ("Save As…", "save-as", self.save_profile_as),
            ("Delete current", "delete", self.delete_current)]:
            action = Gio.SimpleAction.new(self.action_prefix + "-" + suffix, None)

            def activate(*_, callback=callback):
                self.window.active_profile_page = self
                self.close_menu()
                callback()

            action.connect("activate", activate)
            self.window.add_action(action)
            self.profile_actions[label] = action
            self.menu.append(label, "win." + action.get_name())

        self.save_action = self.profile_actions["Save"]
        self.submenu_action = Gio.SimpleAction.new_stateful(self.action_prefix + "-menu", None, GLib.Variant("b", False)
        )

        def submenu_changed(action, state):
            action.set_state(state)
            if state.get_boolean():
                self.window.active_profile_page = self

        self.submenu_action.connect("change-state", submenu_changed)
        self.window.add_action(self.submenu_action)

    def message(self, text, error=False):
        if hasattr(self.window, "inspector") and self.window.inspector.get_visible():
            self.window.inspector.message(text, error)
        else:
            self.window.message(text, error)

    def close_menu(self):
        self.window.close_file_menu()

    def confirm_discard(self, message, action):
        if not self.ready:
            return

        dialog = Gtk.AlertDialog(message=message,
            detail="Save a profile first if you want to keep these planned changes.",
            buttons=["Keep editing", "Discard changes"], cancel_button=0, default_button=0, modal=True)
        self._confirm_action(dialog, action)

    def _confirm_action(self, dialog, action):
        self.close_menu()
        self._dialog_pending = True
        self.update_actions()

        def chosen(dialog, result):
            self._dialog_pending = False
            self.update_actions()
            try:
                if dialog.choose_finish(result) == 1 and self.ready:
                    action()
            except GLib.Error:
                pass

        dialog.choose(self.window, None, chosen)

    def new_profile(self, *_):
        if not self.ready or not self.can_edit:
            return

        def create():
            self.editor.clear()
            self.current_file = self._etag = self._profile = None
            self.invalidate_review()
            self.render()
            self.message("New profile started. Your keyboard has not changed.")

        if self.unsaved:
            self.confirm_discard("Start a new profile?", create)
        else:
            create()

    def _choose_file(self, saving, chosen):
        self.close_menu()
        try:
            self.profile_directory.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            self.message(error_message(error), True)
            return

        dialog = self.window.file_dialog("Save profile" if saving else "Open profile")
        dialog.set_initial_folder(Gio.File.new_for_path(str(self.profile_directory)))
        if saving:
            dialog.set_initial_name(
                self.current_file.get_basename()
                if self.current_file is not None
                else self.title.removesuffix("s") + ".json"
            )

        self._dialog_pending = True
        self.update_actions()

        def finished(dialog, result):
            self._dialog_pending = False
            self.update_actions()
            try:
                file = dialog.save_finish(result) if saving else dialog.open_finish(result)
            except GLib.Error as error:
                self.window._file_dialog_error(error)
                return

            if self.ready:
                chosen(file)

        (dialog.save if saving else dialog.open)(self.window, None, finished)

    def open_profile(self, *_):
        if not self.ready or not self.can_edit:
            return

        def open_dialog():
            self._choose_file(False, self._open_file)

        if self.unsaved:
            self.confirm_discard("Replace your current draft?", open_dialog)
        else:
            open_dialog()

    def _open_file(self, file):
        def work():
            info = file.query_info("standard::type,etag::value", Gio.FileQueryInfoFlags.NONE, None)
            if info.get_file_type() != Gio.FileType.REGULAR:
                raise ValueError("Choose a regular file for the profile")

            before = info.get_etag()
            value = self.decode_profile(read_draft_file(file, limit=self.file_limit, label=self.title))
            after = file.query_info("etag::value", Gio.FileQueryInfoFlags.NONE, None).get_etag()
            if before != after:
                raise ValueError("The profile changed while opening it. Open it again.")

            return value, after

        def loaded(result):
            value, etag = result
            self.editor.load(value[self.payload])
            self.current_file, self._etag, self._profile = file, etag, value
            self.editor.saved(self.editor.draft)
            self.invalidate_review()
            self.render()
            self.message("Profile opened. Review before applying.")

        self.window.run_operation(work, loaded, progress="Opening profile…")

    def save_profile(self, *_):
        if not self.ready or not self.draft_available:
            return

        if self.current_file is None:
            self._choose_file(True, self._save_file)
        else:
            self._save_file(self.current_file)

    def save_profile_as(self, *_):
        if self.ready and self.draft_available:
            self._choose_file(True, self._save_file)

    def _save_file(self, file):
        same_file = self.current_file is not None and file.equal(self.current_file)
        try:
            draft = self.editor.draft
            name = self._profile["name"] if same_file else Path(file.get_basename()).stem
            value = self.make_profile(name[:MAX_PROFILE_NAME_LENGTH], draft)
            if same_file:
                value["id"] = self._profile["id"]

            text = encode_profile(value, limit=self.file_limit)
        except ValueError as error:
            self.message(str(error), True)
            return

        etag = self._etag if same_file else None
        flags = Gio.FileCreateFlags.NONE if same_file else Gio.FileCreateFlags.REPLACE_DESTINATION

        def work():
            try:
                info = file.query_info("standard::type", Gio.FileQueryInfoFlags.NONE, None)
            except GLib.Error as error:
                if not error.matches(Gio.io_error_quark(), Gio.IOErrorEnum.NOT_FOUND):
                    raise
            else:
                if info.get_file_type() != Gio.FileType.REGULAR:
                    raise ValueError("Choose a regular file for the profile")

            try:
                return file.replace_contents(text.encode(), etag, False, flags, None)
            except GLib.Error as error:
                if error.matches(Gio.io_error_quark(), Gio.IOErrorEnum.WRONG_ETAG):
                    raise ValueError(
                        "The profile changed on disk. Open it again before saving. Your draft is unchanged."
                    ) from error

                raise

        def saved(result):
            self.current_file, self._etag, self._profile = file, result[1], value
            self.editor.saved(draft)
            self.message("Profile saved.")

        self.window.run_operation(work, saved, progress="Saving profile…")

    def delete_current(self, *_):
        if not self.ready or self.current_file is None:
            return

        file, etag = self.current_file, self._etag
        dialog = Gtk.AlertDialog(
            message=f"Delete “{file.get_basename()}”?",
            detail="This permanently deletes the profile file. Your current draft stays open and can be saved again.\n\n"
            + file.get_parse_name(),
            buttons=["Cancel", "Delete profile"],
            cancel_button=0,
            default_button=0,
            modal=True,
        )

        def work():
            current = file.query_info("etag::value", Gio.FileQueryInfoFlags.NONE, None).get_etag()
            if current != etag:
                raise ValueError("The profile changed on disk. Open it again before deleting. Your draft is unchanged.")

            file.delete(None)

        def deleted(_):
            self.current_file = self._etag = self._profile = None
            self.editor.forget_saved()
            self.message("Profile file deleted. Your draft is still open; Save will ask for a filename.")

        def delete_file():
            if self.current_file is file:
                self.window.run_operation(work, deleted, progress="Deleting profile…")

        self._confirm_action(dialog, delete_file)
