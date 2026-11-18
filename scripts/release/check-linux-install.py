#!/usr/bin/env python3
"""Smoke-test the installed Linux payload; the preview cannot access hardware."""

import os
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, "/usr/lib/keyboardlights")

# These imports deliberately target the installed payload, not the checkout.
import keyboardlights.app as gui  # pylint: disable=wrong-import-position
import keyboardlights.lighting.service as lighting  # pylint: disable=wrong-import-position
from keyboardlights.assignments.model import Action, python_candidate  # pylint: disable=wrong-import-position
from keyboardlights.device.core import native_library  # pylint: disable=wrong-import-position


def ensure(condition, message):
    """Keep installed-payload checks active with Python optimization enabled."""
    if not condition:
        raise RuntimeError(message)


ensure(lighting.KEY_MAP == Path("/usr/lib/keyboardlights/keyboardlights/data/layout.json"),
    "The installed key map was not loaded")
ensure(len(lighting.KEYS) == 115, "The installed key map is incomplete")

# ctypes exposes the loaded library filename only through its diagnostic _name field.
ensure(
    Path(native_library()._name) == Path("/usr/lib/keyboardlights/keyboardlights/device/lighting_core.so"),  # pylint: disable=protected-access
    "The installed native core was not loaded",
)
ensure(
    len(python_candidate(bytes(640), {"assignments": [{"source": 39, "action": {"kind": "disabled", "modifiers": 0}}]}))
    == 640,
    "Assignment candidate size mismatch",
)
ensure(Action("keyboard", 6, 8).record() == bytes((2, 8, 6, 0)), "Assignment record mismatch")
image = bytearray(1152)
for index, tag in enumerate((1, 2, 3, 4, 5, 6, 7, 0x80)):
    image[index * 64] = tag
    image[index * 64 + 62 : index * 64 + 64] = b"\xaa\x55"

image[0x440] = 1
image[0x449] = 16
image[0x47E:0x480] = b"\xaa\x55"
lighting.validate(image)
with tempfile.TemporaryDirectory(prefix="keyboardlights-installed-preview-") as name:
    app = gui.KeyboardLightsApplication(Path(name), capture=bytes(image), auto_read=False)
    failures = []

    def callback_failed(kind, error, trace):
        """Fail the smoke test on exceptions that GTK would otherwise only print."""
        failures.append(RuntimeError("".join(traceback.format_exception(kind, error, trace))))
        app.quit()

    def finish():
        """Always quit the GTK loop and preserve any callback validation failure."""
        try:
            window = app.window
            ensure(window is not None and window.get_mapped(), "Installed GTK window did not open")
            ensure(len(window.key_buttons) == 115, "Installed keyboard preview is incomplete")
            ensure(window.editor.draft is not None, "Installed lighting editor has no preview")
            for tool in window.paint_tools.values():
                ensure(len(tool.swatches) > 0, "Installed paint palette is empty")
                tool.popup()
                tool.popdown()

            ensure(window.lighting_profile_page.menu.get_n_items() == 5, "Lighting profile menu is incomplete")
            ensure(window.assignment_page.menu.get_n_items() == 5, "Assignment profile menu is incomplete")
            window.show_about()
            version = Path("/usr/lib/keyboardlights/VERSION").read_text().strip()
            ensure(window.about_dialog.get_version() == version, "Installed About version is incorrect")
            ensure(window.about_dialog.get_logo() is not None, "Installed About icon is missing")
            window.about_dialog.close()
            window.close()
        except Exception as error:
            failures.append(error)
        finally:
            app.quit()

        return False

    with (
        patch.object(sys, "excepthook", callback_failed),
        patch.object(lighting.reader, "open_keyboard",
            side_effect=AssertionError("Install checks must not access physical keyboards")) as hardware,
    ):
        gui.GLib.timeout_add(1000, finish)
        ensure(app.run(["keyboardlights-install-check"]) == 0, "Installed GTK app exited unsuccessfully")
        hardware.assert_not_called()

    if failures:
        raise failures[0]

    # A checkout, virtualenv, or hostile working directory must not override the package.
    directory = Path(name)
    for module in ("keyboardlights.py", "sitecustomize.py"):
        (directory / module).write_text("raise RuntimeError('loaded from working directory')\n")

    environment = os.environ.copy()
    environment.update(PYTHONPATH=name, PYTHONHOME=name, KEYBOARDLIGHTS_DATA_DIR=name, KEYBOARDLIGHTS_CORE_LIBRARY=name,
        KEYBOARDLIGHTS_ICON_FILE=name)
    subprocess.run(["/usr/bin/keyboardlights", "--help"], cwd=name, env=environment, check=True, timeout=15)

ensure(not list(Path("/usr/lib/keyboardlights").rglob("__pycache__")), "Installed app created bytecode files")

state = Path(os.environ["XDG_STATE_HOME"]) / "KeyboardLights"
state.mkdir(parents=True, exist_ok=True)
(state / "install-check.txt").write_text("preserve user state\n")
config = Path(os.environ["XDG_CONFIG_HOME"]) / "KeyboardLights/Profiles"
config.mkdir(parents=True, exist_ok=True)
(config / "install-check.json").write_text('{"install-check": "preserve user profiles"}\n')
print("PASS: installed data/core, keyboard, palettes, profile menus, About, isolated launcher, and no bytecode")
