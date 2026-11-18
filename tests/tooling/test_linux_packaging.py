"""Inspect real Debian archives and exercise maintainer scripts without system changes."""

import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ("cc", "strip", "dpkg", "dpkg-deb", "dpkg-buildflags", "dpkg-shlibdeps", "desktop-file-validate",
    "appstreamcli", "lintian", "readelf")
SPEC = importlib.util.spec_from_file_location("linux_release", ROOT / "scripts/release.py")
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


class LinuxPackagingTests(unittest.TestCase):
    """Check archive reproducibility, permissions, native hardening, and lifecycle hooks."""

    @unittest.skipUnless(sys.platform == "linux" and all(shutil.which(tool) for tool in TOOLS),
        "requires Linux and Debian package build/validation tools")
    def test_real_packages_are_reproducible_with_clean_owned_payloads(self):
        with tempfile.TemporaryDirectory(prefix="keyboardlights-deb-test-") as name:
            work = Path(name)
            environment = os.environ.copy()
            environment.update(SOURCE_DATE_EPOCH="1700000000", KEYBOARDLIGHTS_MAINTAINER="Test <test@example.invalid>")
            packages = []
            for mask in (0o022, 0o077):
                output = work / str(mask)
                result = subprocess.run([sys.executable, ROOT / "scripts/release.py", "linux", output], cwd=work,
                    env=environment, umask=mask, capture_output=True, text=True, check=False)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                package = next(output.glob("*.deb"))
                digest = hashlib.sha256(package.read_bytes()).hexdigest()
                self.assertEqual(package.with_name(package.name + ".sha256").read_text(), f"{digest}  {package.name}\n")
                self.assertEqual(json.loads(package.with_name(package.name + ".json").read_text())["sha256"], digest)
                packages.append(package)

            self.assertEqual(packages[0].read_bytes(), packages[1].read_bytes(), "Build depends on umask or directory")
            data = subprocess.run(["dpkg-deb", "--fsys-tarfile", packages[0]], check=True, capture_output=True).stdout
            with tarfile.open(fileobj=io.BytesIO(data)) as archive:
                members = archive.getmembers()
                for member in members:
                    with self.subTest(path=member.name):
                        self.assertEqual((member.uid, member.gid), (0, 0))
                        self.assertTrue(member.isdir() or member.isfile())
                        self.assertNotIn("__pycache__", member.name)
                        self.assertEqual(member.mtime, 1700000000)
                        expected_mode = 0o755 if member.isdir() or member.name == "./usr/bin/keyboardlights" else 0o644
                        self.assertEqual(member.mode, expected_mode)

                files = {
                    member.name.removeprefix("./"): archive.extractfile(member).read()
                    for member in members
                    if member.isfile()
                }

            installed_size = sum(1 if member.isdir() else (member.size + 1023) // 1024 for member in members)
            control_size = subprocess.run(["dpkg-deb", "--field", packages[0], "Installed-Size"], check=True,
                capture_output=True, text=True).stdout
            self.assertEqual(int(control_size), installed_size)
            md5sums = subprocess.run(["dpkg-deb", "--info", packages[0], "md5sums"], check=True, capture_output=True,
                text=True).stdout
            recorded = dict(line.split("  ", 1)[::-1] for line in md5sums.splitlines())
            self.assertEqual(set(recorded), set(files))
            for path, content in files.items():
                self.assertEqual(recorded[path], hashlib.md5(content, usedforsecurity=False).hexdigest(), path)

            library = work / "lighting_core.so"
            library.write_bytes(files["usr/lib/keyboardlights/keyboardlights/device/lighting_core.so"])
            dynamic = subprocess.run(["readelf", "-dW", library], check=True, capture_output=True, text=True).stdout
            self.assertIn("BIND_NOW", dynamic)
            self.assertNotIn("RPATH", dynamic)
            self.assertNotIn("RUNPATH", dynamic)
            segments = subprocess.run(["readelf", "-lW", library], check=True, capture_output=True, text=True).stdout
            self.assertIn("GNU_RELRO", segments)
            self.assertRegex(segments, r"GNU_STACK[^\n]+ RW ")
            sections = subprocess.run(["readelf", "-SW", library], check=True, capture_output=True, text=True).stdout
            self.assertNotIn(".symtab", sections)

    def test_failed_dependency_or_archive_validation_keeps_previous_artifacts(self):
        for failure in ("dependencies", "lintian"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as name:
                output = Path(name)
                filename = f"keyboardlights_{release.version()}_amd64.deb"
                for suffix in ("", ".sha256", ".json"):
                    (output / (filename + suffix)).write_bytes(b"keep previous artifact")

                def run(arguments, **_):
                    command = str(arguments[0])
                    if command == "dpkg":
                        return "amd64"

                    if command == "git":
                        return "1700000000"

                    if command == "dpkg-buildflags":
                        return ""

                    if command == "cc":
                        Path(arguments[arguments.index("-o") + 1]).write_bytes(b"test library")
                    elif command == "dpkg-shlibdeps":
                        return "" if failure == "dependencies" else "shlibs:Depends=libc6 (>= 2.4)"
                    elif command == "dpkg-deb" and "--build" in arguments:
                        Path(arguments[-1]).write_bytes(b"new archive")
                    elif command == "lintian":
                        raise RuntimeError("lintian failed")

                    return None

                with (patch.object(release.sys, "platform", "linux"), patch.object(release, "require"),
                    patch.object(release, "run", side_effect=run), patch.object(release, "publish") as publish):
                    with self.assertRaises((ValueError, RuntimeError)):
                        release.linux_package(Namespace(output=name, maintainer="Test <test@example.invalid>"))

                    publish.assert_not_called()

                for suffix in ("", ".sha256", ".json"):
                    self.assertEqual((output / (filename + suffix)).read_bytes(), b"keep previous artifact")

    @unittest.skipUnless(sys.platform == "linux", "requires a POSIX shell")
    def test_maintainer_scripts_only_reload_rules_and_preserve_user_files(self):
        with tempfile.TemporaryDirectory(prefix="keyboardlights-hooks-test-") as name:
            work = Path(name)
            binary = work / "bin"
            binary.mkdir()
            log = work / "udev.log"
            udev = binary / "udevadm"
            udev.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$HOOK_LOG"\nexit "$HOOK_EXIT"\n')
            udev.chmod(0o755)
            profile = work / ".config/KeyboardLights/Profiles/keep.json"
            profile.parent.mkdir(parents=True)
            profile.write_text('{"keep": true}\n')
            state = work / ".local/state/KeyboardLights/keep.txt"
            state.parent.mkdir(parents=True)
            state.write_text("keep state\n")
            environment = {"PATH": str(binary), "HOME": name, "XDG_CONFIG_HOME": str(work / ".config"),
                "XDG_STATE_HOME": str(work / ".local/state"), "HOOK_LOG": str(log), "HOOK_EXIT": "0"}
            for script, actions in (
                ("postinst", ("configure", "abort-upgrade", "abort-remove", "abort-deconfigure")),
                ("postrm",
                    ("remove", "purge", "upgrade", "failed-upgrade", "abort-install", "abort-upgrade", "disappear")),
            ):
                for action in actions:
                    for exit_code in ("0", "1"):
                        with self.subTest(script=script, action=action, exit_code=exit_code):
                            log.write_text("")
                            environment["HOOK_EXIT"] = exit_code
                            for _ in range(2):
                                subprocess.run(["/bin/sh", ROOT / "packaging/linux" / script, action], env=environment,
                                    check=True)

                            reloads = script == "postinst" and action == "configure" or action in ("remove", "purge")
                            self.assertEqual(log.read_text().splitlines(),
                                ["control --reload-rules"] * (2 if reloads else 0))
                            self.assertEqual(profile.read_text(), '{"keep": true}\n')
                            self.assertEqual(state.read_text(), "keep state\n")

            udev.unlink()
            for script, action in (("postinst", "configure"), ("postrm", "remove"), ("postrm", "purge")):
                subprocess.run(["/bin/sh", ROOT / "packaging/linux" / script, action], env=environment, check=True)


if __name__ == "__main__":
    unittest.main()
