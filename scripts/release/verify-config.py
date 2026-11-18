#!/usr/bin/env python3
"""Freeze signing policy once per run; manual/PR runs can only build previews."""

import os
from pathlib import Path


def signing_enabled(event, reference, setting):
    """Permit signing only for explicitly configured pushed version tags."""
    pushed_tag = event == "push" and reference.startswith("refs/tags/v")
    if pushed_tag and setting not in ("", "false", "true"):
        raise ValueError("MACOS_SIGNED_RELEASE must be true or false; refusing an ambiguous signing configuration.")

    return pushed_tag and setting == "true"


if __name__ == "__main__":
    enabled = signing_enabled(os.environ["GITHUB_EVENT_NAME"], os.environ["GITHUB_REF"],
        os.environ.get("SIGNING_SETTING", ""))
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        output.write(f"signed={str(enabled).lower()}\n")
