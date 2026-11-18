"""Release policy, payload completeness, and isolated installation regression tests."""

# Test methods use descriptive scenario names.
# pylint: disable=missing-function-docstring
import importlib.util
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("release", ROOT / "scripts/release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ReleaseTests(unittest.TestCase):
    """Exercise native payload assembly, versioning, and safe output publication."""

    def test_version_metadata_rejects_package_drift_and_stale_changelog(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / "VERSION").write_text("1.0.0\n")
            for package, changelog, valid in (("1.0.0", "## 1.0.0\n\n- Current release.\n", True),
                ("0.9.0", "## 1.0.0\n", False), ("1.0.0", "## 0.9.0\n\n## 1.0.0\n", False),
                ("1.0.0", "# Changelog\n", False)):
                with self.subTest(package=package, changelog=changelog):
                    (root / "pyproject.toml").write_text(f'[project]\nversion = "{package}"\n')
                    (root / "CHANGELOG.md").write_text(changelog)
                    with patch.object(release, "ROOT", root):
                        if valid:
                            self.assertEqual(release.validate_version_metadata(), "1.0.0")
                        else:
                            with self.assertRaises(ValueError):
                                release.validate_version_metadata()

    def test_version_and_tag_agreement(self):
        value = release.version()
        subprocess.run([sys.executable, ROOT / "scripts/release.py", "check-version", "--tag", "v" + value], check=True)
        result = subprocess.run([sys.executable, ROOT / "scripts/release.py", "check-version", "--tag", "v0.0.0"],
            capture_output=True, check=False)
        self.assertNotEqual(result.returncode, 0)
        with tempfile.TemporaryDirectory() as name:
            (Path(name) / "VERSION").write_text("../bad\n")
            with patch.object(release, "ROOT", Path(name)):
                with self.assertRaises(ValueError):
                    release.version()

    def test_output_cannot_replace_checkout_or_parent(self):
        for path in [ROOT, ROOT.parent, Path("/")]:
            with self.subTest(path=path), self.assertRaises(ValueError):
                release.output_directory(str(path))

        with tempfile.TemporaryDirectory() as name:
            link = Path(name) / "repo"
            link.symlink_to(ROOT, target_is_directory=True)
            with self.assertRaises(ValueError):
                release.output_directory(str(link))

    def test_distribution_refuses_missing_signing_before_build(self):
        with (patch.object(release.sys, "platform", "darwin"), patch.object(release, "require"),
            patch.dict(os.environ, {}, clear=True)):
            with self.assertRaisesRegex(ValueError, "Distribution requires"):
                release.macos_package(Namespace(distribution=True))

    def test_publish_replaces_only_the_requested_installer_and_writes_checksum(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            source = root / "staging/package.deb"
            source.parent.mkdir()
            source.write_bytes(b"new")
            target = root / "package.deb"
            target.write_bytes(b"old")
            unrelated = root / "user-file"
            unrelated.write_bytes(b"keep")
            release.publish(source, target)
            self.assertEqual(target.read_bytes(), b"new")
            self.assertEqual(unrelated.read_bytes(), b"keep")
            self.assertEqual(target.with_name(target.name + ".sha256").read_text(),
                release.hashlib.sha256(b"new").hexdigest() + "  package.deb\n")

    def test_staged_linux_payload_is_independent_of_checkout(self):
        with tempfile.TemporaryDirectory(prefix="keyboardlights-package-test-") as name:
            work = Path(name)
            library = work / "lighting_core.so"
            subprocess.run(["cc", "-std=c11", "-O2", "-fPIC", "-shared", ROOT / "core/Core.c", "-o", library],
                check=True)
            stage = work / "relocated package"
            release.stage_linux(stage, library, release.version(), "amd64", "Test <test@example.invalid>")
            app = stage / "usr/lib/keyboardlights"
            sources = ROOT / "apps/linux/keyboardlights"
            expected_modules = {str(path.relative_to(sources)) for path in sources.rglob("*.py")}
            self.assertEqual(set(release.LINUX_MODULES), expected_modules)
            for icon_name in ("pointer.svg", "brush.svg", "bucket.svg"):
                self.assertEqual((app / "keyboardlights/ui" / icon_name).read_bytes(),
                    (sources / "ui" / icon_name).read_bytes())

            self.assertFalse((app / "Sources").exists())
            self.assertFalse((app / ".build").exists())
            self.assertFalse(list(app.rglob("test_*.py")))
            icon = (ROOT / "assets/app-icon.svg").read_bytes()
            self.assertEqual((app / "keyboardlights/app-icon.svg").read_bytes(), icon)
            self.assertEqual((stage / f"usr/share/icons/hicolor/scalable/apps/{release.APP_ID}.svg").read_bytes(), icon)
            self.assertEqual((stage / "usr/share/doc/keyboardlights/copyright").read_bytes(),
                (ROOT / "LICENSE").read_bytes())
            self.assertIn("usr/share/doc/keyboardlights/copyright", (stage / "DEBIAN/md5sums").read_text())
            for path in [stage / "DEBIAN/postinst", stage / "DEBIAN/postrm", stage / "usr/bin/keyboardlights"]:
                self.assertEqual(path.stat().st_mode & 0o777, 0o755)

            self.assertIn("gir1.2-gtk-4.0 (>= 4.12)", (stage / "DEBIAN/control").read_text())

            # Isolated subprocess starts outside the checkout and loads only staged modules/data/core.
            code = """import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import keyboardlights.lighting.service as lighting
from keyboardlights.device.core import native_library
from keyboardlights.assignments.model import Action, draft_dict
from keyboardlights.assignments.service import candidate
from keyboardlights.ui.presentation import KEY_POSITIONS
from keyboardlights.lighting.changes import KeyboardChanges
assert KeyboardChanges is not None
assert set(KEY_POSITIONS) == lighting.PHYSICAL
assert len(lighting.KEYS) == 115
assert lighting.KEY_MAP.parent == Path(sys.argv[1]).resolve() / 'keyboardlights/data'
assert Path(native_library()._name).parent == Path(sys.argv[1]).resolve() / 'keyboardlights/device'
assert candidate(bytes(640), draft_dict({39: Action('disabled')}))[156:160] == bytes((5,3,0,0))
"""
            environment = {
                key: value
                for key, value in os.environ.items()
                if key not in ("KEYBOARDLIGHTS_DATA_DIR", "KEYBOARDLIGHTS_CORE_LIBRARY", "KEYBOARDLIGHTS_ICON_FILE")
            }
            subprocess.run([sys.executable, "-I", "-B", "-c", code, str(app)], cwd=work, env=environment, check=True)
            for path in stage.rglob("*"):
                if path.is_file():
                    self.assertNotIn(str(ROOT).encode(), path.read_bytes(), str(path))

    def test_macos_app_stages_the_shared_resource_bundle(self):
        with tempfile.TemporaryDirectory(prefix="keyboardlights-macos-stage-") as name:
            root = Path(name)
            binary = root / "built/KeyboardLights"
            binary.parent.mkdir()
            binary.write_bytes(b"test executable")
            resources = binary.parent / "KeyboardLights_KeyboardData.bundle/Contents/Resources/c989"
            resources.mkdir(parents=True)
            for filename in ("layout.json", "assignments.json", "lighting-colors.json"):
                (resources / filename).write_bytes((ROOT / "data/c989" / filename).read_bytes())

            app = root / "image/KeyboardLights.app"
            previous_umask = os.umask(0o077)
            try:
                with patch.object(release, "run") as run:
                    release.stage_macos_app(binary, app, {}, release.version())
            finally:
                os.umask(previous_umask)

            for path in [app, *app.rglob("*")]:
                expected_mode = 0o755 if path.is_dir() or path == app / "Contents/MacOS/KeyboardLights" else 0o644
                self.assertEqual(path.stat().st_mode & 0o777, expected_mode, str(path))

            info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
            self.assertEqual(info["CFBundleIconFile"], "AppIcon")
            self.assertEqual(info["CFBundleShortVersionString"], release.version())
            run.assert_any_call(
                ["swift", ROOT / "scripts/release/render-icon.swift", ROOT / "assets/app-icon.svg", root / "icon.png"],
                env={})
            run.assert_any_call(
                ["iconutil", "-c", "icns", root / "AppIcon.iconset", "-o", app / "Contents/Resources/AppIcon.icns"]
            )

            installed = app / "Contents/Resources/KeyboardLights_KeyboardData.bundle/Contents/Resources/c989"
            self.assertEqual((app / "Contents/Resources/LICENSE").read_bytes(), (ROOT / "LICENSE").read_bytes())
            for filename in ("layout.json", "assignments.json", "lighting-colors.json"):
                self.assertEqual((installed / filename).read_bytes(), (resources / filename).read_bytes())

    @unittest.skipUnless(sys.platform == "darwin", "requires the macOS Foundation framework")
    def test_relocated_macos_resources_never_use_checkout_fallback(self):
        with tempfile.TemporaryDirectory(prefix="keyboardlights-resources-") as name:
            work = Path(name)
            app = work / "built/ResourceProbe.app"
            executable = app / "Contents/MacOS/ResourceProbe"
            executable.parent.mkdir(parents=True)
            (app / "Contents/Info.plist").write_bytes(
                plistlib.dumps(
                    {"CFBundleExecutable": "ResourceProbe",
                        "CFBundleIdentifier": "io.github.keyboardlights.ResourceProbe", "CFBundlePackageType": "APPL"}
                )
            )
            source = work / "KeyboardData.swift"
            source.write_bytes((ROOT / "data/KeyboardData.swift").read_bytes())
            main = work / "main.swift"
            main.write_text("""import Foundation
extension Bundle {
    static var module: Bundle { fatalError("Checkout resource fallback must never be used") }
}
for name in ["layout.json", "assignments.json", "lighting-colors.json"] {
    print(name, try KeyboardResources.data(named: name).count)
}
""")
            env = release.macos_build_environment()
            subprocess.run(
                ["xcrun", "swiftc", "-module-cache-path", env["CLANG_MODULE_CACHE_PATH"], source, main, "-o", executable
                ],
                env=env,
                check=True,
                capture_output=True,
                timeout=120,
            )
            relocated = work / "relocated/ResourceProbe.app"
            relocated.parent.mkdir()
            app.rename(relocated)
            resources = relocated / "Contents/Resources/KeyboardLights_KeyboardData.bundle"
            for layout in ("c989", "Contents/Resources/c989"):
                with self.subTest(layout=layout):
                    target = resources / layout
                    target.mkdir(parents=True)
                    expected = []
                    for filename in ("layout.json", "assignments.json", "lighting-colors.json"):
                        data = (ROOT / "data/c989" / filename).read_bytes()
                        (target / filename).write_bytes(data)
                        expected.append(f"{filename} {len(data)}")

                    result = subprocess.run([relocated / "Contents/MacOS/ResourceProbe"], cwd=work, env=env, text=True,
                        capture_output=True, check=True, timeout=15)
                    self.assertEqual(result.stdout.splitlines(), expected)
                    shutil.rmtree(resources)

    def test_local_app_rebuild_replaces_stale_resources_after_signing(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            destination = root / ".build/macos/KeyboardLights.app"
            destination.mkdir(parents=True)
            (destination / "stale-resource").write_text("old app")

            def stage(_binary, app, _env, _version):
                app.mkdir(parents=True)
                (app / "new-resource").write_text("current app")

            with (patch.object(release, "ROOT", root), patch.object(release.sys, "platform", "darwin"),
                patch.object(release, "require"), patch.object(release, "version", return_value="1.0.0"),
                patch.object(release, "build_macos_binary", return_value=root / "KeyboardLights") as build,
                patch.object(release, "stage_macos_app", side_effect=stage), patch.object(release, "run"),
                patch.object(release, "validate_macos_app") as validate):
                self.assertEqual(release.build_macos_app(), destination)
                self.assertEqual(build.call_args.kwargs["configuration"], "debug")
                validate.assert_called_once()
                self.assertEqual(validate.call_args.args[1:], ([os.uname().machine], "1.0.0"))

            self.assertFalse((destination / "stale-resource").exists())
            self.assertEqual((destination / "new-resource").read_text(), "current app")

    def test_failed_app_replacement_restores_previous_bundle(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            destination = root / ".build/macos/KeyboardLights.app"
            destination.mkdir(parents=True)
            previous = destination / "previous-resource"
            previous.write_text("keep this app")
            rename = Path.rename

            def stage(_binary, app, _env, _version):
                app.mkdir(parents=True)
                (app / "new-resource").write_text("replacement")

            def failed_publish(source, target):
                if source.parent.name == "image" and target == destination:
                    raise OSError("injected replacement failure")

                return rename(source, target)

            with (patch.object(release, "ROOT", root), patch.object(release.sys, "platform", "darwin"),
                patch.object(release, "require"), patch.object(release, "version", return_value="1.0.0"),
                patch.object(release, "build_macos_binary", return_value=root / "KeyboardLights"),
                patch.object(release, "stage_macos_app", side_effect=stage), patch.object(release, "run"),
                patch.object(release, "validate_macos_app"),
                patch.object(Path, "rename", autospec=True, side_effect=failed_publish)):
                with self.assertRaisesRegex(OSError, "replacement failure"):
                    release.build_macos_app()

            self.assertEqual(previous.read_text(), "keep this app")
            self.assertFalse((destination / "new-resource").exists())

    def test_local_app_signing_failure_preserves_previous_build(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            destination = root / ".build/macos/KeyboardLights.app"
            destination.mkdir(parents=True)
            previous = destination / "previous-resource"
            previous.write_text("keep this app")
            with (patch.object(release, "ROOT", root), patch.object(release.sys, "platform", "darwin"),
                patch.object(release, "require"), patch.object(release, "version", return_value="1.0.0"),
                patch.object(release, "build_macos_binary", return_value=root / "KeyboardLights"),
                patch.object(release, "stage_macos_app"),
                patch.object(release, "run", side_effect=RuntimeError("signing failed"))):
                with self.assertRaisesRegex(RuntimeError, "signing failed"):
                    release.build_macos_app()

            self.assertEqual(previous.read_text(), "keep this app")

    def test_installers_are_ignored(self):
        # A source checkout can be tested before it has any commits or Git metadata.
        with tempfile.TemporaryDirectory(prefix="keyboardlights-ignore-") as name:
            directory = Path(name)
            (directory / ".gitignore").write_text((ROOT / ".gitignore").read_text())
            subprocess.run(["git", "init", "--quiet", str(directory)], check=True)
            for filename in ("dist/KeyboardLights.dmg", "release-preview.deb",
                "preview/KeyboardLights.app/Contents/MacOS/KeyboardLights"):
                result = subprocess.run(["git", "check-ignore", "--quiet", filename], cwd=directory, check=False)
                self.assertEqual(result.returncode, 0, filename)

    def test_device_rule_is_restricted_to_legacy_hardware_and_active_user(self):
        rule = (ROOT / "packaging/linux/70-keyboardlights.rules").read_text()
        self.assertIn('ATTRS{bcdDevice}=="0138"', rule)
        self.assertIn('TAG+="uaccess"', rule)
        self.assertNotIn("0666", rule)
        self.assertNotIn("RUN+=", rule)
        self.assertIn('SUBSYSTEM=="hidraw"', rule)


if __name__ == "__main__":
    unittest.main()
