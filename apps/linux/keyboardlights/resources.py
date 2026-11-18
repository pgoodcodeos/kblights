"""Explicit development roots or self-contained installed package resources."""

import os
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent


def data_file(name):
    root = (
        Path(os.environ["KEYBOARDLIGHTS_DATA_DIR"])
        if "KEYBOARDLIGHTS_DATA_DIR" in os.environ
        else PACKAGE_ROOT / "data"
    )
    path = root / name
    if not path.is_file():
        raise ValueError(f"Required keyboard resource is missing: {path}")

    return path


def core_library():
    path = (
        Path(os.environ["KEYBOARDLIGHTS_CORE_LIBRARY"])
        if "KEYBOARDLIGHTS_CORE_LIBRARY" in os.environ
        else PACKAGE_ROOT / "device/lighting_core.so"
    )
    if not path.is_file():
        raise ValueError("Build the keyboard core first: sh scripts/build-linux.sh")

    return path


def icon_file():
    path = (
        Path(os.environ["KEYBOARDLIGHTS_ICON_FILE"])
        if "KEYBOARDLIGHTS_ICON_FILE" in os.environ
        else PACKAGE_ROOT / "app-icon.svg"
    )
    if not path.is_file():
        raise ValueError(f"Required application icon is missing: {path}")

    return path
