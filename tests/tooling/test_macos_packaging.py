"""Mac packaging failures are checked without running the app or using signing credentials."""

import importlib.util
import os
import plistlib
import tempfile
import unittest
from argparse import Namespace
from itertools import product
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("macos_release", ROOT / "scripts/release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class MacPackagingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="keyboardlights-mac-package-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.app = self.root / "KeyboardLights.app"
        contents = self.app / "Contents"
        self.binary = contents / "MacOS/KeyboardLights"
        release.install_file(ROOT / "VERSION", self.binary, 0o755)
        self.info = plistlib.loads((ROOT / "apps/macos/Resources/Info.plist").read_bytes())
        self.info.update(CFBundleVersion=release.version(), CFBundleShortVersionString=release.version())
        self.write_info()
        self.resources = contents / "Resources"
        release.install_file(ROOT / "LICENSE", self.resources / "LICENSE")
        release.install_file(ROOT / "assets/app-icon.svg", self.resources / "AppIcon.icns")
        self.data = self.resources / "KeyboardLights_KeyboardData.bundle/Contents/Resources/c989"
        for name in release.MACOS_RESOURCE_FILES:
            release.install_file(ROOT / "data/c989" / name, self.data / name)

        self.dependencies = "binary:\n\t/usr/lib/libSystem.B.dylib (compatibility version 1.0)\n"
        self.minimums = (
            "Load command 1\ncmd LC_BUILD_VERSION\nplatform 1\nminos 14.0\n"
            "Load command 2\ncmd LC_BUILD_VERSION\nplatform 1\nminos 14.0\n"
        )

    def write_info(self):
        (self.app / "Contents/Info.plist").write_bytes(plistlib.dumps(self.info))

    def command(self, arguments, **_kwargs):
        if arguments[:2] == ["lipo", "-archs"]:
            return "arm64 x86_64"

        if arguments[:4] == ["otool", "-arch", "all", "-L"]:
            return self.dependencies

        if arguments[:4] == ["otool", "-arch", "all", "-l"]:
            return self.minimums

        return None

    def validate(self):
        with patch.object(release, "run", side_effect=self.command):
            release.validate_macos_app(self.app, ["arm64", "x86_64"], release.version())

    def test_complete_bundle_accepts_both_resource_bundle_layouts(self):
        self.validate()
        flat = self.resources / "KeyboardLights_KeyboardData.bundle/c989"
        self.data.rename(flat)
        self.validate()

    def test_identity_version_icon_and_permission_metadata_are_required(self):
        for key in ["CFBundleIdentifier", "CFBundleExecutable", "CFBundleVersion", "CFBundleShortVersionString",
            "CFBundleIconFile", "LSMinimumSystemVersion", "NSInputMonitoringUsageDescription"]:
            with self.subTest(key=key):
                previous = self.info[key]
                self.info[key] = ""
                self.write_info()
                with self.assertRaises(ValueError):
                    self.validate()

                self.info[key] = previous
                self.write_info()

    def test_resource_data_missing_stale_or_extra_is_rejected(self):
        path = self.data / "layout.json"
        original = path.read_bytes()
        for replacement in [None, b"{}"]:
            with self.subTest(replacement=replacement):
                if replacement is None:
                    path.unlink()
                else:
                    path.write_bytes(replacement)

                with self.assertRaises(ValueError):
                    self.validate()

                path.write_bytes(original)

        (self.data / "checkout-debris").write_text("must not ship")
        with self.assertRaises(ValueError):
            self.validate()

    def test_missing_icon_license_or_executable_mode_is_rejected(self):
        for name in ["AppIcon.icns", "LICENSE"]:
            with self.subTest(name=name):
                path = self.resources / name
                data = path.read_bytes()
                path.write_bytes(b"")
                with self.assertRaises(ValueError):
                    self.validate()

                path.write_bytes(data)

        self.binary.chmod(0o644)
        with self.assertRaises(ValueError):
            self.validate()

    def test_symlink_extra_executable_and_nonregular_payloads_are_rejected(self):
        unwanted = self.resources / "linked-source"
        unwanted.symlink_to(ROOT / "VERSION")
        with self.assertRaises(ValueError):
            self.validate()

        unwanted.unlink()
        extra = self.binary.parent / "old-executable"
        extra.write_bytes(b"stale")
        with self.assertRaises(ValueError):
            self.validate()

        extra.unlink()
        if hasattr(os, "mkfifo"):
            os.mkfifo(unwanted)
            with self.assertRaises(ValueError):
                self.validate()

    def test_every_architecture_must_target_supported_macos_and_system_libraries(self):
        original = self.minimums
        for minimums in [original.replace("minos 14.0", "minos 15.0", 1),
            original.replace("platform 1", "platform 2", 1), ""]:
            with self.subTest(minimums=minimums):
                self.minimums = minimums
                with self.assertRaises(ValueError):
                    self.validate()

        self.minimums = original
        self.dependencies += "\t/opt/homebrew/lib/libunexpected.dylib (compatibility version 1.0)\n"
        with self.assertRaises(ValueError):
            self.validate()

    def test_validation_failure_detaches_preview_without_copying(self):
        destination = self.root / "work/image/KeyboardLights.app"
        destination.parent.parent.mkdir()
        with (patch.object(release, "run", side_effect=self.command) as run,
            patch.object(release, "validate_macos_app", side_effect=ValueError("broken preview"))):
            with self.assertRaisesRegex(ValueError, "broken preview"):
                release.copy_tested_macos_app(self.root / "preview.dmg", destination, ["arm64"], release.version())

            self.assertEqual(run.call_args_list[-1].args[0][:2], ["hdiutil", "detach"])
            self.assertFalse(any(call.args[0][0] == "ditto" for call in run.call_args_list))

    def test_final_image_validates_shortcut_instructions_and_both_notarization_tickets(self):
        (self.root / "Applications").symlink_to("/Applications")
        instructions = self.root / "Install KeyboardLights.txt"
        instructions.write_text(release.mac_install_text(True))
        with (patch.object(release, "mounted_macos_image") as mounted,
            patch.object(release, "run", side_effect=self.command) as run):
            mounted.return_value.__enter__.return_value = self.root
            release.validate_macos_image(self.root / "image.dmg", ["arm64", "x86_64"], release.version(), notarized=True
            )
            calls = [call.args[0] for call in run.call_args_list]
            tickets = [call[-1] for call in calls if call[:3] == ["xcrun", "stapler", "validate"]]
            self.assertEqual(tickets, [self.app, self.root / "image.dmg"])
            self.assertEqual(len([call for call in calls if call[0] == "spctl"]), 2)

        for missing in ["Applications", "Install KeyboardLights.txt"]:
            with self.subTest(missing=missing):
                path = self.root / missing
                backup = self.root / (missing + ".backup")
                path.rename(backup)
                with (patch.object(release, "mounted_macos_image") as mounted,
                    patch.object(release, "run", side_effect=self.command)):
                    mounted.return_value.__enter__.return_value = self.root
                    with self.assertRaises(ValueError):
                        release.validate_macos_image(self.root / "image.dmg", ["arm64", "x86_64"], release.version())

                    mounted.return_value.__exit__.assert_called_once()

                backup.rename(path)

    def test_partial_publication_restores_previous_installer_and_metadata(self):
        for had_previous, failed_suffix in product((False, True), (".sha256", ".json")):
            with (self.subTest(had_previous=had_previous, failed_suffix=failed_suffix),
                tempfile.TemporaryDirectory() as name):
                output = Path(name)
                source = output / "stage/image.dmg"
                source.parent.mkdir()
                source.write_bytes(b"new image")
                destination = output / "image.dmg"
                previous = {destination: b"previous image", output / "image.dmg.sha256": b"previous checksum",
                    output / "image.dmg.json": b"previous manifest"}
                if had_previous:
                    for path, data in previous.items():
                        path.write_bytes(data)

                replace = Path.replace

                def fail_metadata(path, target):
                    if path.parent == source.parent and target == output / ("image.dmg" + failed_suffix):
                        raise OSError("injected metadata replacement failure")

                    return replace(path, target)

                with (patch.object(Path, "replace", autospec=True, side_effect=fail_metadata),
                    patch.object(release, "run", side_effect=["a" * 40, ""])):
                    with self.assertRaisesRegex(OSError, "metadata replacement failure"):
                        release.publish(source, destination, target="macos", architecture="universal2")

                for path, data in previous.items():
                    if had_previous:
                        self.assertEqual(path.read_bytes(), data)
                    else:
                        self.assertFalse(path.exists())

                self.assertFalse(list(output.glob(".keyboardlights-recovery-*")))

    def test_failed_publication_rollback_retains_recovery_files(self):
        source = self.root / "stage/image.dmg"
        source.parent.mkdir()
        source.write_bytes(b"new image")
        destination = self.root / "image.dmg"
        destination.write_bytes(b"previous image")
        checksum = self.root / "image.dmg.sha256"
        checksum.write_text("previous checksum")
        replace = Path.replace

        def fail_metadata_and_rollback(path, target):
            if target == checksum or path.parent.name.startswith(".keyboardlights-recovery-"):
                raise OSError("injected disk failure")

            return replace(path, target)

        with patch.object(Path, "replace", autospec=True, side_effect=fail_metadata_and_rollback):
            with self.assertRaisesRegex(RuntimeError, "previous artifacts are preserved"):
                release.publish(source, destination)

        backup = list(self.root.glob(".keyboardlights-recovery-*"))
        self.assertEqual(len(backup), 1)
        self.assertEqual((backup[0] / "image.dmg").read_bytes(), b"previous image")
        self.assertEqual((backup[0] / "image.dmg.sha256").read_text(), "previous checksum")

    def test_failed_final_image_validation_never_publishes(self):
        args = Namespace(distribution=False, from_image=None, arch="universal2", output=str(self.root / "out"))
        with (patch.object(release.sys, "platform", "darwin"), patch.object(release, "require"),
            patch.object(release, "build_macos_binary", return_value=self.binary),
            patch.object(release, "validate_macos_app"),
            patch.object(release, "validate_macos_image", side_effect=ValueError("invalid final image")),
            patch.object(release, "run"), patch.object(release, "publish") as publish,
            patch.dict(os.environ, {"KEYBOARDLIGHTS_SIGN_IDENTITY": "-"})):
            # The staging double creates only the image directory needed for its Applications shortcut.
            def stage(_binary, app, _env, _version):
                app.parent.mkdir()

            with patch.object(release, "stage_macos_app", side_effect=stage):
                with self.assertRaisesRegex(ValueError, "invalid final image"):
                    release.macos_package(args)

            publish.assert_not_called()

    def test_failed_replacement_and_failed_restore_keep_recoverable_previous_app(self):
        destination = self.root / ".build/macos/KeyboardLights.app"
        destination.mkdir(parents=True)
        (destination / "old-resource").write_text("keep")
        rename = Path.rename

        def stage(_binary, app, _env, _version):
            app.mkdir(parents=True)
            (app / "new-resource").write_text("replacement")

        def failed_rename(source, target):
            if target == destination:
                raise OSError("injected publication and restore failure")

            return rename(source, target)

        with (patch.object(release, "ROOT", self.root), patch.object(release.sys, "platform", "darwin"),
            patch.object(release, "require"), patch.object(release, "version", return_value="1.0.0"),
            patch.object(release, "build_macos_binary", return_value=self.binary),
            patch.object(release, "stage_macos_app", side_effect=stage), patch.object(release, "run"),
            patch.object(release, "validate_macos_app"),
            patch.object(Path, "rename", autospec=True, side_effect=failed_rename)):
            with self.assertRaisesRegex(RuntimeError, "previous app is preserved"):
                release.build_macos_app()

        previous = list(destination.parent.glob("*-previous.app"))
        self.assertEqual(len(previous), 1)
        self.assertEqual((previous[0] / "old-resource").read_text(), "keep")
