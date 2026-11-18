"""Bounded, atomic profile storage with concurrent-edit protection."""

import fcntl
import json
import os
import secrets
import stat
import threading
from pathlib import Path

import keyboardlights.lighting.service as lighting
from keyboardlights.assignments.model import MAX_PROFILE_BYTES, parse_profile

MAX_PROFILES = 128
MAX_LIBRARY_BYTES = MAX_PROFILES * MAX_PROFILE_BYTES


class ProfileConflict(ValueError):
    def __init__(self):
        super().__init__(
            "Saved profiles changed in another window. Reload profiles before saving again. Your draft is unchanged."
        )


_UNLOADED = object()


class ProfileStore:
    """Bounded, atomic local storage shared by both profile libraries."""

    def __init__(self, directory, filename, parser):
        self.path = Path(directory) / filename
        self.parse_profile = parser
        self._revision = _UNLOADED
        self._mutex = threading.Lock()

    def _read(self):
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
        except FileNotFoundError:
            return None

        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("The profile library must be a regular file")

            if info.st_size > MAX_LIBRARY_BYTES:
                raise ValueError("The profile library is too large")

            with os.fdopen(fd, "rb", closefd=False) as file:
                data = file.read(MAX_LIBRARY_BYTES + 1)
        finally:
            os.close(fd)

        if len(data) > MAX_LIBRARY_BYTES:
            raise ValueError("The profile library is too large")

        return data

    def load(self):
        with self._mutex:
            data = self._read()
            values = json.loads(data) if data is not None else []
            if not isinstance(values, list) or len(values) > MAX_PROFILES:
                raise ValueError("Invalid profile library")

            profiles = [self.parse_profile(value) for value in values]
            if len({value["id"] for value in profiles}) != len(profiles):
                raise ValueError("Duplicate profiles")

            self._revision = lighting.digest(data) if data is not None else None
            return profiles

    def save(self, profiles):
        if len(profiles) > MAX_PROFILES:
            raise ValueError(f"Keep at most {MAX_PROFILES} profiles")

        profiles = [self.parse_profile(value) for value in profiles]
        if len({value["id"] for value in profiles}) != len(profiles):
            raise ValueError("Duplicate profiles")

        body = (json.dumps(profiles, indent=2) + "\n").encode()
        if len(body) > MAX_LIBRARY_BYTES:
            raise ValueError("The profile library is too large")

        lighting.make_directory(self.path.parent, exist_ok=True)
        with self._mutex:
            lock = os.open(self.path.with_suffix(".lock"),
                os.O_RDWR | os.O_CREAT | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            try:
                if not stat.S_ISREG(os.fstat(lock).st_mode):
                    raise ValueError("The profile lock must be a regular file")

                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as error:
                    raise ProfileConflict() from error

                current = self._read()
                revision = lighting.digest(current) if current is not None else None
                expected = None if self._revision is _UNLOADED else self._revision
                if revision != expected:
                    raise ProfileConflict()

                temporary = self.path.with_name(f".{self.path.stem}-{secrets.token_hex(8)}.json")
                try:
                    lighting.durable(temporary, body)
                    os.replace(temporary, self.path)
                    lighting.sync_directory(self.path.parent)
                    self._revision = lighting.digest(body)
                finally:
                    temporary.unlink(missing_ok=True)
            finally:
                os.close(lock)


class AssignmentProfiles(ProfileStore):
    def __init__(self, directory):
        super().__init__(directory, "assignment-profiles.json", parse_profile)
