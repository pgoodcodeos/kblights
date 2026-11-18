#!/usr/bin/env python3
"""Mount and inspect an installer without launching the app or accessing a keyboard."""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# The CLI helper must add its parent directory before importing shared release code.
from release import file_sha256, validate_macos_image, version  # pylint: disable=wrong-import-position


def check(image, *, architecture="universal2", notarized=False):
    """Check the published checksum and run the same validation used before publication."""
    image = Path(image).resolve()
    checksum = image.with_name(image.name + ".sha256").read_text(encoding="utf-8")
    fields = re.fullmatch(r"([0-9a-fA-F]{64})  ([^\r\n]+)\n?", checksum)
    if fields is None or fields[2] != image.name:
        raise RuntimeError("Invalid checksum file or installer filename mismatch")

    if file_sha256(image) != fields[1].lower():
        raise RuntimeError("Checksum mismatch")

    architectures = ["arm64", "x86_64"] if architecture == "universal2" else [architecture]
    validate_macos_image(image, architectures, version(), notarized=notarized)
    print(
        "PASS: DMG checksum, installation layout, metadata, resources, architectures, signature, and system libraries"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--arch", choices=("arm64", "x86_64", "universal2"), default="universal2")
    parser.add_argument("--notarized", action="store_true", help="Also validate both stapled tickets and Gatekeeper")
    arguments = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("Run this check on macOS.")

    check(arguments.image, architecture=arguments.arch, notarized=arguments.notarized)
