"""Compact paint tools with independent color palettes and remembered colors."""

from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from keyboardlights.ui.presentation import COLOR_NAMES, COLOR_PRESETS

ROOT = Path(__file__).resolve().parent


class PaintTool(Gtk.MenuButton):
    def __init__(self, window, tool):
        title = "Paint brush" if tool == "brush" else "Paint bucket"
        super().__init__(tooltip_text=title, valign=Gtk.Align.CENTER, css_classes=["paint-tool"])
        self.window, self.tool = window, tool
        self._display_state = None
        self.update_property([Gtk.AccessibleProperty.LABEL], [title])
        face = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        face.append(Gtk.Image.new_from_file(str(ROOT / (tool + ".svg"))))
        self.chip = Gtk.Box(css_classes=["paint-chip"])
        self.chip.set_size_request(18, 3)
        self.chip.set_name(f"paint-chip-{id(self)}")
        face.append(self.chip)
        self.set_child(face)
        self.set_always_show_arrow(True)
        self.palette = Gtk.Popover()
        self.set_popover(self.palette)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=12, margin_bottom=12,
            margin_start=12, margin_end=12)
        self.palette.set_child(box)
        box.append(Gtk.Label(label=title, xalign=0, css_classes=["field-label"]))
        last = Gtk.Box(spacing=10)
        last.append(Gtk.Label(label="Last", xalign=0, hexpand=True))
        self.last_button = Gtk.Button(css_classes=["color-swatch"])
        self.last_button.set_size_request(24, 24)
        self.last_button.set_name(f"last-{tool}-{id(self)}")
        self.last_button.connect("clicked", lambda *_: window.select_color(self.last_color, tool=tool))
        last.append(self.last_button)
        box.append(last)
        grid = Gtk.Grid(row_spacing=8, column_spacing=8)
        box.append(grid)
        self.swatches = {}
        self._swatch_rules = []
        for index, (name, color) in enumerate(COLOR_PRESETS):
            button = Gtk.Button(label="╱" if name == "Off" else None, tooltip_text=f"{name} · {color}",
                halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, css_classes=["color-swatch"])
            button.set_size_request(24, 24)
            button.set_name(f"{tool}-swatch-{id(self)}-{name}")
            button.update_property([Gtk.AccessibleProperty.LABEL], [name])
            button.connect("clicked", lambda _, color=color: window.select_color(color, tool=tool))
            grid.attach(button, index % 3, index // 3, 1, 1)
            self.swatches[color] = button
            self._swatch_rules.append(f"#{button.get_name()} {{ background: {color}; }}")

        self.custom_button = Gtk.Button(label="Custom…")
        self.custom_button.connect("clicked", lambda *_: window.choose_color(tool=tool))
        box.append(self.custom_button)
        self.connect("notify::active", self.opened)

    @property
    def color(self):
        editor = self.window.editor
        return editor.brush_color if self.tool == "brush" else editor.bucket_color

    @property
    def last_color(self):
        editor = self.window.editor
        return editor.last_used_color if self.tool == "brush" else editor.last_bucket_color

    def opened(self, *_):
        if self.get_active():
            self.window.select_paint_tool(self.tool)

    def update(self):
        editor = self.window.editor
        available = editor.workspace_style != "animation"
        sensitive = available and editor.draft is not None and not self.window.busy and not self.window._closed
        selected = available and editor.paint_tool == self.tool
        color, last = self.color, self.last_color
        state = (available, sensitive, selected, color, last)
        if state == self._display_state:
            return

        self.set_visible(available)
        self.set_sensitive(sensitive)
        if selected:
            self.add_css_class("active")
        else:
            self.remove_css_class("active")

        name = COLOR_NAMES.get(color, color)
        action = "Paint one key" if self.tool == "brush" else "Fill neighboring keys of the same color"
        self.set_tooltip_text(f"{action} · {name}")
        self.last_button.set_tooltip_text("Last-used color · " + last)
        self.last_button.update_property([Gtk.AccessibleProperty.LABEL], ["Last-used " + self.tool + " color"])
        for value, button in self.swatches.items():
            if value == color:
                button.add_css_class("active")
            else:
                button.remove_css_class("active")

        self.custom_button.set_label("Custom…" if color in self.swatches else "Custom ✓")
        self._display_state = state

    def color_styles(self):
        return [*self._swatch_rules, f"#{self.chip.get_name()} {{ background: {self.color}; }}",
            f"#{self.last_button.get_name()} {{ background: {self.last_color}; }}"]
