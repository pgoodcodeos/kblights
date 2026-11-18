"""Native GTK components shared by the lighting and assignment workspaces."""

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Pango

import keyboardlights.lighting.service as lighting
from keyboardlights.ui.presentation import KEY_LABELS, KEY_POSITIONS


class LockIndicatorRow(Gtk.Stack):
    def __init__(self):
        super().__init__(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, hhomogeneous=True, vhomogeneous=True)
        self.labels = {}
        self._states = None
        labels = Gtk.Box(spacing=16)
        for name in ("Caps Lock", "Num Lock", "Scroll Lock"):
            label = Gtk.Label(label=name, xalign=0, width_chars=11, css_classes=["lock-indicator"])
            labels.append(label)
            self.labels[name] = label

        self.add_named(labels, "states")
        self.unavailable_label = Gtk.Label(label="Lock states unavailable", css_classes=["lock-indicator"])
        self.add_named(self.unavailable_label, "unavailable")
        self.update(None)

    def update(self, indicators):
        states = (
            (indicators.caps_lock, indicators.num_lock, indicators.scroll_lock)
            if indicators is not None
            else (None, None, None)
        )
        if states == self._states:
            return

        if indicators is not None:
            previous = self._states or (None, None, None)
            for (name, label), enabled, old in zip(self.labels.items(), states, previous):
                if enabled == old:
                    continue

                state = "On" if enabled else "Off"
                if enabled:
                    label.add_css_class("on")
                else:
                    label.remove_css_class("on")

                label.set_tooltip_text(f"{name}: {state}. Last keyboard reading.")
                label.update_property([Gtk.AccessibleProperty.LABEL], [f"{name}: {state}"])

        self._states = states
        self.set_visible_child_name("unavailable" if indicators is None else "states")


class KeyboardGrid(Gtk.Grid):
    def __init__(self, callback, *, signal, css_classes=()):
        super().__init__(row_spacing=4, column_spacing=0, row_homogeneous=True, column_homogeneous=True,
            halign=Gtk.Align.FILL, hexpand=True, valign=Gtk.Align.CENTER, css_classes=["keyboard-stage"])
        self.buttons = {}
        name_prefix = f"keyboard-key-{id(self)}"
        button_classes = ["led-key", *css_classes]
        for key in lighting.KEYS:
            index = key["led_index"]
            name = KEY_LABELS.get(key["name"], key["name"].replace("Numpad ", ""))
            label = Gtk.Label(label=name, max_width_chars=4, width_chars=2, ellipsize=Pango.EllipsizeMode.END,
                justify=Gtk.Justification.CENTER)
            button = Gtk.ToggleButton(child=label, tooltip_text=key["name"], margin_end=1, css_classes=button_classes)
            button.set_size_request(25, 34)
            button.set_name(f"{name_prefix}-{index}")
            button.update_property([Gtk.AccessibleProperty.LABEL], [key["name"]])
            button.connect(signal, callback, index)
            self.attach(button, *KEY_POSITIONS[index])
            self.buttons[index] = button
