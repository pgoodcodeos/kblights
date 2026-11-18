"""Adversarial release-management tests. All GitHub calls and signing operations are mocked."""

# Test methods use descriptive scenario names.
# pylint: disable=missing-function-docstring
import ast
import hashlib
import importlib.util
import json
import os
import plistlib
import shutil
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[2]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


manager = load_module("github_release", "scripts/release/github-release.py")
config = load_module("verify_config", "scripts/release/verify-config.py")
signing = load_module("ci_signing", "scripts/release/ci-signing.py")
mac_check = load_module("mac_check", "scripts/release/check-macos-image.py")
import release  # pylint: disable=wrong-import-position

COMMIT = "a" * 40
RUN_ID = "1234"


class FakeGitHub:
    """In-memory GitHub API double that records every potential mutation."""

    repository = "owner/keyboardlights"

    def __init__(self, *, tag_commit=COMMIT, annotated=False, status="ahead", existing=None, assets=None):
        self.tag_commit = tag_commit
        self.annotated = annotated
        self.status = status
        self.existing = existing
        self.assets = assets or []
        self.calls = []
        self.published_during_upload = False

    def request(self, method, path, *, data=None, file=None, missing_ok=False):  # pylint: disable=unused-argument,too-many-return-statements
        self.calls.append((method, path, file))
        if path.startswith("/git/ref/tags/"):
            return {
                "object": {"type": "tag" if self.annotated else "commit",
                    "sha": "c" * 40 if self.annotated else self.tag_commit}
            }

        if path.startswith("/git/tags/"):
            return {"object": {"type": "commit", "sha": self.tag_commit}}

        if path == "":
            return {"default_branch": "main"}

        if path.startswith("/compare/"):
            return {"status": self.status}

        if path.startswith("/releases/tags/"):
            return self.existing

        if path == "/releases" and method == "POST":
            self.existing = {**data, "id": 1, "immutable": False}
            return self.existing

        if path.startswith("/releases/1/assets?"):
            return self.assets

        if path == "/releases/1":
            return {**self.existing, "draft": not self.published_during_upload and self.existing["draft"]}

        if file is not None:
            asset = {"name": file.name, "state": "uploaded",
                "digest": "sha256:" + hashlib.sha256(file.read_bytes()).hexdigest()}
            self.assets.append(asset)
            return asset

        raise AssertionError((method, path))

    def mutations(self):
        return [call for call in self.calls if call[0] != "GET"]


def fixture(directory, *, signed=False):
    result = []
    for folder, target, architecture in [("linux-amd64", "linux", "amd64"), ("linux-arm64", "linux", "arm64"),
        ("macos", "macos", "universal2")]:
        folder = directory / folder
        folder.mkdir()
        name = (
            f"keyboardlights_{release.version()}_{architecture}.deb"
            if target == "linux"
            else f"KeyboardLights-{release.version()}-macos-universal2{'' if signed else '-adhoc'}.dmg"
        )
        path = folder / name
        path.write_bytes((target + architecture).encode())
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        (folder / (name + ".sha256")).write_text(f"{digest}  {name}\n")
        manifest = {"format": "keyboardlights-release-v1", "version": release.version(), "file": name, "sha256": digest,
            "platform": target, "architecture": architecture, "source_commit": COMMIT, "source_dirty": False,
            "run_id": RUN_ID, "notarized": signed if target == "macos" else False}
        (folder / (name + ".json")).write_text(json.dumps(manifest))
        result.append(folder / (name + ".json"))

    return result


class GitHubReleaseTests(unittest.TestCase):
    """Probe release trust boundaries without remote requests or signing secrets."""

    def prepare(self, api, directory, signed=False):
        manager.prepare_draft(api, tag="v" + release.version(), commit=COMMIT, run_id=RUN_ID, directory=directory,
            signed=signed)

    def test_manual_tag_and_pull_request_cannot_enable_signing(self):
        self.assertFalse(config.signing_enabled("workflow_dispatch", "refs/tags/v1.0.0", "true"))
        self.assertFalse(config.signing_enabled("pull_request", "refs/pull/1/merge", "true"))
        self.assertTrue(config.signing_enabled("push", "refs/tags/v1.0.0", "true"))
        with self.assertRaises(ValueError):
            config.signing_enabled("push", "refs/tags/v1.0.0", "TRUE")

    def test_moved_or_unmerged_tags_are_rejected(self):
        for api in (FakeGitHub(tag_commit="b" * 40), FakeGitHub(status="diverged"), FakeGitHub(status="behind")):
            with self.assertRaises(ValueError):
                manager.verify_source(api, "v" + release.version(), COMMIT)

            self.assertEqual(api.mutations(), [])

        manager.verify_source(FakeGitHub(annotated=True), "v" + release.version(), COMMIT)
        manager.verify_source(FakeGitHub(status="identical"), "v" + release.version(), COMMIT)

    def test_missing_architecture_or_mixed_builds_never_create_drafts(self):
        for key, value in [("source_commit", "b" * 40), ("source_dirty", True), ("source_dirty", 0),
            ("run_id", "other"), ("architecture", "wrong"), ("notarized", 0), ("version", "9.9.9")]:
            with self.subTest(key=key, value=value), tempfile.TemporaryDirectory() as name:
                root = Path(name)
                manifests = fixture(root)
                data = json.loads(manifests[0].read_text())
                data[key] = value
                manifests[0].write_text(json.dumps(data))
                api = FakeGitHub()
                with self.assertRaises(ValueError):
                    self.prepare(api, root)

                self.assertEqual(api.mutations(), [])

        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            fixture(root)
            (root / "linux-arm64").rename(root / "unexpected")
            with self.assertRaises(ValueError):
                manager.assemble(root, COMMIT, RUN_ID, False)

    def test_checksums_and_file_sets_cannot_inject_paths(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            fixture(root)
            checksum = next((root / "linux-amd64").glob("*.sha256"))
            checksum.write_text("0" * 64 + "  ../../outside\n")
            with self.assertRaises(ValueError):
                manager.assemble(root, COMMIT, RUN_ID, False)

        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            fixture(root)
            artifact = next((root / "linux-amd64").glob("*.deb"))
            artifact.unlink()
            artifact.symlink_to("/etc/passwd")
            with self.assertRaises(ValueError):
                manager.assemble(root, COMMIT, RUN_ID, False)

    def test_adhoc_and_developer_id_artifacts_cannot_be_confused(self):
        for signed in (True, False):
            with tempfile.TemporaryDirectory() as name:
                root = Path(name)
                fixture(root, signed=signed)
                api = FakeGitHub()
                with self.assertRaises(ValueError):
                    self.prepare(api, root, signed=not signed)

                self.assertEqual(api.mutations(), [])

    def test_partial_upload_resumes_without_replacing_assets(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            fixture(root)
            api = FakeGitHub()
            self.prepare(api, root)
            self.assertTrue(api.existing["draft"])
            self.assertFalse(api.existing["prerelease"])
            self.assertEqual(len(api.assets), 10)
            api.assets = api.assets[:4]
            api.calls.clear()
            self.prepare(api, root)
            self.assertEqual(len(api.assets), 10)
            self.assertEqual(len(api.mutations()), 6)
            api.calls.clear()
            self.prepare(api, root)
            self.assertEqual(api.mutations(), [])

    def test_release_status_is_independent_of_mac_signing(self):
        for signed in (False, True):
            with self.subTest(signed=signed), tempfile.TemporaryDirectory() as name:
                root = Path(name)
                fixture(root, signed=signed)
                api = FakeGitHub()
                self.prepare(api, root, signed=signed)
                self.assertTrue(api.existing["draft"])
                self.assertFalse(api.existing["prerelease"])
                self.assertEqual(api.existing["make_latest"], "false")
                self.assertEqual(api.existing["name"], "KeyboardLights v" + release.version())
                self.assertNotIn("preview", api.existing["body"].lower())
                self.assertEqual("ad-hoc signed and unnotarized" in api.existing["body"], not signed)
                self.assertEqual("Developer ID signed and notarized" in api.existing["body"], signed)

    def test_published_releases_changed_assets_and_unrelated_drafts_are_untouched(self):
        for alteration in ("published", "immutable", "marker", "hash", "extra"):
            with self.subTest(alteration=alteration), tempfile.TemporaryDirectory() as name:
                root = Path(name)
                fixture(root)
                api = FakeGitHub()
                self.prepare(api, root)
                if alteration == "published":
                    api.existing["draft"] = False
                elif alteration == "immutable":
                    api.existing["immutable"] = True
                elif alteration == "marker":
                    api.existing["body"] = "A hand-made draft"
                elif alteration == "hash":
                    api.assets[0]["digest"] = "sha256:" + "0" * 64
                else:
                    api.assets.append({"name": "unexpected.zip"})

                api.calls.clear()
                with self.assertRaises(ValueError):
                    self.prepare(api, root)

                self.assertEqual(api.mutations(), [])

    def test_notarization_requires_explicit_acceptance(self):
        for status in ("Invalid", "Rejected", "In Progress", None):
            with self.subTest(status=status), patch.object(release, "run", return_value=json.dumps({"status": status})):
                with self.assertRaises(ValueError):
                    release.notarize(Path("app.zip"), "profile")

        with patch.object(release, "run", return_value='{"status":"Accepted"}'):
            release.notarize(Path("app.zip"), "profile")

    def test_cleanup_attempts_deletion_even_if_keychain_restoration_fails(self):
        with tempfile.TemporaryDirectory() as name, patch.dict(os.environ, {"RUNNER_TEMP": name}):
            root = Path(name) / "keyboardlights-signing-test"
            root.mkdir()
            (root / "state.json").write_text(json.dumps({"original": ["login.keychain-db"]}))
            (root / "release.keychain-db").write_bytes(b"private key")
            with patch.object(signing, "run", side_effect=[RuntimeError("restore failed"), None]) as command:
                with self.assertRaises(RuntimeError):
                    signing.cleanup(root)

                self.assertEqual(command.call_count, 2)
                self.assertEqual(command.call_args_list[1].args[0][1], "delete-keychain")

            self.assertFalse(root.exists())

    def test_publishing_mid_upload_stops_all_asset_writes(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            fixture(root)
            api = FakeGitHub()
            api.published_during_upload = True
            with self.assertRaises(ValueError):
                self.prepare(api, root)

            self.assertFalse(any(call[2] is not None for call in api.calls))

    def test_api_auth_errors_are_not_mistaken_for_missing_releases(self):
        api = manager.GitHub("owner/repo", "private-test-token")
        for code in (403, 500):
            error = HTTPError("https://api.github.com", code, "failure", {}, None)
            with patch.object(manager, "urlopen", side_effect=error):
                with self.assertRaises(RuntimeError) as caught:
                    api.request("GET", "/releases/tags/v1", missing_ok=True)

                self.assertNotIn("private-test-token", str(caught.exception))

        error = HTTPError("https://api.github.com", 404, "missing", {}, None)
        with patch.object(manager, "urlopen", side_effect=error):
            self.assertIsNone(api.request("GET", "/releases/tags/v1", missing_ok=True))

    def test_tag_releases_require_a_real_maintainer_contact(self):
        for value in ("", "Someone", "Name <a@example.invalid>", "Name <a@example.com>", "Name <a@project.test>"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                release.validate_maintainer(value, require_real=True)

        release.validate_maintainer("Contributor <build@project.org>", require_real=True)

    def test_signing_reuses_the_tested_app_without_rebuilding(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            source = root / "preview.dmg"
            source.write_bytes(b"preview")
            calls = []

            def command(arguments, **_kwargs):
                calls.append(arguments)
                if arguments[:2] == ["hdiutil", "attach"]:
                    mount = Path(arguments[arguments.index("-mountpoint") + 1])
                    app = mount / "KeyboardLights.app/Contents"
                    (app / "MacOS").mkdir(parents=True)
                    (app / "MacOS/KeyboardLights").write_bytes(b"tested executable")
                    (app / "Info.plist").write_bytes(plistlib.dumps({"CFBundleShortVersionString": release.version()}))

                if arguments[:2] == ["lipo", "-archs"]:
                    return "arm64 x86_64"

                if arguments[0] == "ditto" and len(arguments) == 3:
                    shutil.copytree(arguments[1], arguments[2])

                if arguments[:2] == ["hdiutil", "create"]:
                    Path(arguments[-1]).write_bytes(b"signed image")

                return None

            with (
                patch.object(release.sys, "platform", "darwin"),
                patch.object(release, "run", side_effect=command),
                patch.object(release, "notarize") as notarize,
                patch.object(release, "validate_macos_app"),
                patch.object(release, "validate_macos_image"),
                patch.object(release, "require"),
                patch.object(release, "publish") as publish,
                patch.dict(
                    os.environ,
                    {"KEYBOARDLIGHTS_SIGN_IDENTITY": "Developer ID Application: Test (TEST)",
                        "KEYBOARDLIGHTS_NOTARY_PROFILE": "test-profile"},
                ),
            ):
                release.macos_package(
                    Namespace(distribution=True, from_image=str(source), output=str(root / "output"), arch="universal2")
                )

            self.assertFalse(any(call[0] == "swift" for call in calls))
            self.assertEqual(notarize.call_count, 2)
            self.assertTrue(publish.call_args.kwargs["notarized"])

    def test_metadata_failure_preserves_the_previous_installer(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            source = root / "staging/image.dmg"
            source.parent.mkdir()
            source.write_bytes(b"new")
            destination = root / "image.dmg"
            destination.write_bytes(b"previous installer")
            checksum = destination.with_name(destination.name + ".sha256")
            checksum.write_text("previous checksum")
            with patch.object(release, "run", side_effect=RuntimeError("git failed")):
                with self.assertRaises(RuntimeError):
                    release.publish(source, destination, target="macos", architecture="universal2")

            self.assertEqual(destination.read_bytes(), b"previous installer")
            self.assertEqual(checksum.read_text(), "previous checksum")

    def test_installer_manifest_records_commit_and_dirty_source(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            source = root / "staging/image.dmg"
            source.parent.mkdir()
            source.write_bytes(b"image")
            destination = root / "image.dmg"
            with (patch.object(release, "run", side_effect=[COMMIT, " M source"]),
                patch.dict(os.environ, {"GITHUB_RUN_ID": RUN_ID})):
                release.publish(source, destination, target="macos", architecture="universal2")

            manifest = json.loads(destination.with_name(destination.name + ".json").read_text())
            self.assertEqual(manifest["source_commit"], COMMIT)
            self.assertTrue(manifest["source_dirty"])
            self.assertEqual(manifest["run_id"], RUN_ID)

    def test_mac_checksum_file_rejects_empty_malformed_and_mismatched_names(self):
        with tempfile.TemporaryDirectory() as name:
            image = Path(name) / "preview.dmg"
            image.write_bytes(b"preview")
            checksum = image.with_name(image.name + ".sha256")
            for text in ["", "not a checksum", "0" * 64 + "  wrong.dmg\n"]:
                with self.subTest(text=text), patch.object(mac_check, "validate_macos_image") as validate:
                    checksum.write_text(text)
                    with self.assertRaisesRegex(RuntimeError, "Invalid checksum"):
                        mac_check.check(image)

                    validate.assert_not_called()

    def test_distribution_checks_cannot_be_disabled_with_python_optimization(self):
        for name in ("check-macos-image.py", "check-linux-install.py"):
            tree = ast.parse((ROOT / "scripts/release" / name).read_text())
            self.assertFalse(any(isinstance(node, ast.Assert) for node in ast.walk(tree)))

        with tempfile.TemporaryDirectory() as name:
            image = Path(name) / "invalid.dmg"
            image.write_bytes(b"bad image")
            image.with_name(image.name + ".sha256").write_text("0" * 64 + "  invalid.dmg\n")
            with patch.object(mac_check, "validate_macos_image") as validate:
                with self.assertRaises(RuntimeError):
                    mac_check.check(image)

                validate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
