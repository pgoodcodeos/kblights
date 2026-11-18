"""Bounded profile serialization and GIO reads for the UI's background worker."""

import json
import os
import stat

MAX_DRAFT_BYTES = 128 * 1024


def encode_profile(value, *, limit=MAX_DRAFT_BYTES):
    """Ensure exported files fit the importer's byte limit, including whitespace."""
    text = json.dumps(value, indent=2) + "\n"
    if len(text.encode()) > limit:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"

    if len(text.encode()) > limit:
        raise ValueError("The profile file is too large to export")

    return text


def read_draft_file(file, *, limit=MAX_DRAFT_BYTES, label="Lighting drafts"):
    """Read local or remote GFiles, including fragmented stream responses."""
    path = file.get_path() if hasattr(file, "get_path") else None
    if path is not None:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ValueError("Choose a regular file for the profile")

            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                data = stream.read(limit + 1)
        finally:
            os.close(descriptor)

        if len(data) > limit:
            raise ValueError(f"{label} must be {limit} bytes or smaller")

        return data.decode("utf-8")

    stream = file.read(None)
    chunks = []
    size = 0
    try:
        while size <= limit:
            chunk = stream.read_bytes(limit + 1 - size, None).get_data()
            if not chunk:
                break

            chunks.append(chunk)
            size += len(chunk)
    finally:
        stream.close(None)

    if size > limit:
        raise ValueError(f"{label} must be {limit} bytes or smaller")

    return b"".join(chunks).decode("utf-8")
