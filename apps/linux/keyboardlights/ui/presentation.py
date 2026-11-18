"""Pure presentation helpers; no GTK or device access."""

import colorsys
import json
import re

import keyboardlights.lighting.service as lighting
from keyboardlights.resources import data_file

# Display percentages follow the already validated firmware PWM lookup.
BRIGHTNESS_PWM = (0, 0, 24, 48, 64, 80, 96, 112, 128, 144, 160, 176, 192, 208, 224, 240, 255)
KEY_LABELS = {"Left Shift": "⇧", "Right Shift": "⇧", "Left Ctrl": "Ctrl", "Right Ctrl": "Ctrl", "Left Alt": "Alt",
    "Right Alt": "Alt", "Left GUI": "Win", "Application": "Menu", "Left Space": "Spc", "Right Space": "Spc",
    "Caps Lock": "Caps", "Backspace": "Bksp", "Enter": "↵", "Numpad Enter": "↵", "Numpad Decimal": ".",
    "Num Lock": "Num", "Scroll Lock": "ScrL", "Print": "Prt", "Print Screen": "Prt", "Page Up": "Pg↑",
    "Page Down": "Pg↓", "Home": "Hm", "Left": "←", "Down": "↓", "Up": "↑", "Right": "→", "Pause": "Paus",
    "Insert": "Ins", "Delete": "Del"}
COLOR_PRESETS = tuple(
    (value["name"], value["hex"]) for value in json.loads(data_file("lighting-colors.json").read_text())
)


COLOR_NAMES = {color: name for name, color in COLOR_PRESETS}

# Display geometry is shared with macOS; firmware indices and matrix coordinates stay unchanged.
KEY_POSITIONS = {
    key["led_index"]: tuple(key["display"][field] for field in ("left", "top", "width", "height"))
    for key in lighting.KEYS
}


def _generated_color(key):
    channels = colorsys.hsv_to_rgb((key["matrix_column"] / 20 + key["matrix_row"] / 12) % 1, 0.65, 1)
    return "#" + "".join(f"{round(channel * 255):02X}" for channel in channels)


# This palette only illustrates generated colors; it does not emulate motion.

GENERATED_COLORS = tuple(_generated_color(key) for key in lighting.KEYS)


def error_message(error):
    if isinstance(error, PermissionError) and re.fullmatch(r"/dev/hidraw[0-9]+", str(error.filename)):
        return (
            "Keyboard access was denied. Unplug and reconnect the keyboard, then choose Refresh. "
            "If access is still denied, install the device access rule from the Linux setup instructions. "
            "Run KeyboardLights as your normal desktop user."
        )

    return str(error)


def brightness_percent(code):
    return round(BRIGHTNESS_PWM[code] / 255 * 100)


def preview_colors(draft):
    if draft["style"] == "perKey":
        slots = draft["slots"]
        return tuple(
            slots[key["led_index"]]["color"].upper() if slots[key["led_index"]]["enabled"] else "#000000"
            for key in lighting.KEYS
        )

    if draft["style"] == "animation" and not lighting.uses_custom_color(draft):
        return GENERATED_COLORS

    return (draft["color"].upper(),) * len(lighting.KEYS)


def review_summary(draft, current_slots):
    style = lighting.MODE_BY_TAG[draft["mode"]]["name"] if draft["style"] == "animation" else "Solid color"
    details = [style]
    if draft["style"] != "perKey" and lighting.uses_custom_color(draft):
        details[0] += " · " + draft["color"]

    if draft["style"] == "perKey":
        changed = []
        for key in lighting.KEYS:
            index = key["led_index"]
            before, after = current_slots[index], draft["slots"][index]
            if before["enabled"] != after["enabled"] or lighting.rgb(before["color"]) != lighting.rgb(after["color"]):
                changed.append(key["name"])

        if changed:
            details.append(f"{len(changed)} key" + ("s" if len(changed) != 1 else "") + " changed")
            if len(changed) <= 8:
                details.append(", ".join(changed))

    details.append(f"Brightness: {brightness_percent(draft['brightness'])}%")
    if draft["style"] == "animation":
        mode = lighting.MODE_BY_TAG[draft["mode"]]
        if not lighting.uses_custom_color(draft):
            details.append("Colors: Full color")

        speed = "Fast" if draft["speed"] >= 12 else "Medium" if draft["speed"] >= 7 else "Slow"
        details.append(f"Speed: {speed}")
        if mode["direction"]:
            details.append("Direction: " + ("Left" if draft["direction"] else "Right"))

    return "\n".join(details)


def key_lighting_description(draft, source, *, unpainted=()):
    if draft is None:
        return "Not loaded"

    if draft["style"] == "animation":
        mode = lighting.MODE_BY_TAG[draft["mode"]]["name"]
        color = draft["color"] if lighting.uses_custom_color(draft) else "Full color"
        return (
            f"{mode} · {color} · speed {draft['speed']} · brightness {brightness_percent(draft['brightness'])}%"
            " · animation on all keys"
        )

    if source in unpainted:
        return "Unpainted"

    slot = draft["slots"][source]
    color = draft["color"] if draft["style"] == "solid" else slot["color"] if slot["enabled"] else "#000000"
    return f"{'Off' if color == '#000000' else color} · brightness {brightness_percent(draft['brightness'])}%"
