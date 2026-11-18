#!/usr/bin/env python3
"""Validate commit-bound build artifacts and safely resume a GitHub draft release."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Parent-path setup is required for these directly executable helpers.
from release import file_sha256, installer_name, version  # pylint: disable=wrong-import-position


class GitHub:
    """Minimal GitHub REST adapter with sanitized credential-safe failures."""

    def __init__(self, repository, token):
        """Bind API requests to a validated repository and authorization token."""
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) or not token:
            raise ValueError("GH_REPO and GH_TOKEN must identify the GitHub repository and an authorized token.")

        self.repository = repository
        self.token = token

    def request(self, method, path, *, data=None, file=None, missing_ok=False):
        """Read repository metadata or upload an asset to the fixed GitHub endpoints."""
        if file is not None:
            url = f"https://uploads.github.com/repos/{self.repository}/{path}"
            payload = Path(file).read_bytes()
            content_type = "application/octet-stream"
        else:
            url = f"https://api.github.com/repos/{self.repository}{path}"
            payload = json.dumps(data).encode() if data is not None else None
            content_type = "application/json"

        request = Request(
            url,
            method=method,
            data=payload,
            headers={"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28", "Content-Type": content_type,
                "User-Agent": "KeyboardLights-release"},
        )
        try:
            with urlopen(request, timeout=120) as response:
                return json.load(response)
        except HTTPError as error:
            if missing_ok and error.code == 404:
                return None

            raise RuntimeError(f"GitHub API request failed ({error.code}); no release was published.") from None
        except URLError:
            raise RuntimeError(
                "GitHub API request failed; check connectivity and rerun. No release was published."
            ) from None


def verify_source(api, tag, commit):
    """Require a matching remote tag and default-branch ancestry for this commit."""
    if tag != "v" + version() or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("The release tag, VERSION, and full source commit must agree.")

    target = api.request("GET", "/git/ref/tags/" + quote(tag, safe=""))["object"]
    seen = set()
    while target["type"] == "tag":
        if target["sha"] in seen or len(seen) >= 10:
            raise ValueError("Invalid or excessively nested annotated tag.")

        seen.add(target["sha"])
        target = api.request("GET", "/git/tags/" + target["sha"])["object"]

    if target["type"] != "commit" or target["sha"] != commit:
        raise ValueError("The remote tag moved or does not point to the tested commit. No release changes are allowed.")

    default = api.request("GET", "")["default_branch"]
    comparison = api.request("GET", "/compare/" + commit + "..." + quote(default, safe=""))
    if comparison["status"] not in ("ahead", "identical"):
        raise ValueError("The release commit is not on the repository default branch. Merge it before tagging.")


def verify_bundle(directory, *, target, architecture, commit, run_id, signed=False):
    """Check the exact files and provenance required for one platform artifact."""
    directory = Path(directory)
    filename = installer_name(target, version(), architecture, signed=signed, notarized=signed)
    expected = {filename, filename + ".sha256", filename + ".json"}
    if directory.is_symlink() or {path.name for path in directory.iterdir()} != expected:
        raise ValueError(f"Incomplete or unexpected artifact set in {directory.name}.")

    if any(path.is_symlink() or not path.is_file() for path in directory.iterdir()):
        raise ValueError("Release artifacts must be regular files, not links or directories.")

    artifact = directory / filename
    digest = file_sha256(artifact)
    checksum = (directory / (filename + ".sha256")).read_text(encoding="utf-8")
    if checksum != f"{digest}  {filename}\n":
        raise ValueError(f"Invalid checksum for {filename}.")

    manifest = json.loads((directory / (filename + ".json")).read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError(f"Malformed artifact manifest: {filename}.")

    required = {"format": "keyboardlights-release-v1", "version": version(), "file": filename, "sha256": digest,
        "platform": target, "architecture": architecture, "source_commit": commit, "run_id": str(run_id)}
    if any(manifest.get(key) != value for key, value in required.items()):
        raise ValueError(f"Artifact version, commit, workflow run, or platform mismatch: {filename}.")

    if manifest.get("source_dirty") is not False or manifest.get("notarized") is not bool(signed):
        raise ValueError(f"Uncommitted source or incorrect signing state in {filename}.")

    return [artifact, directory / (filename + ".sha256"), directory / (filename + ".json")]


def assemble(directory, commit, run_id, signed):
    """Require all three tested installers before writing the aggregate checksum."""
    directory = Path(directory)
    expected = {"linux-amd64", "linux-arm64", "macos"}
    if {path.name for path in directory.iterdir()} - {"SHA256SUMS"} != expected:
        raise ValueError("A release requires exactly Linux amd64, Linux arm64, and the selected Mac artifact.")

    files = []
    for architecture in ("amd64", "arm64"):
        files.extend(
            verify_bundle(directory / ("linux-" + architecture), target="linux", architecture=architecture,
                commit=commit, run_id=run_id)
        )

    files.extend(
        verify_bundle(directory / "macos", target="macos", architecture="universal2", commit=commit, run_id=run_id,
            signed=signed)
    )
    checksum = directory / "SHA256SUMS"
    if checksum.is_symlink():
        raise ValueError("SHA256SUMS must not be a symlink.")

    checksum.write_text("".join(path.read_text(encoding="utf-8") for path in files if path.name.endswith(".sha256")),
        encoding="utf-8")
    return files + [checksum]


def release_assets(api, identifier):
    """Read the complete remote asset list, including pagination."""
    assets = []
    for page in range(1, 21):
        batch = api.request("GET", f"/releases/{identifier}/assets?per_page=100&page={page}")
        assets.extend(batch)
        if len(batch) < 100:
            return assets

    raise ValueError("Unexpectedly large asset set; inspect this release manually.")


def matching_draft_assets(api, draft, hashes):
    """Preflight all existing assets before attempting any upload or resumption."""
    assets = release_assets(api, draft["id"])
    names = [asset["name"] for asset in assets]
    if len(set(names)) != len(names):
        raise ValueError("Duplicate release asset names; inspect the draft manually.")

    # Preflight every existing asset before uploading anything. Never overwrite an asset.

    for asset in assets:
        if (
            asset["name"] not in hashes
            or asset.get("state") != "uploaded"
            or asset.get("digest") != hashes[asset["name"]]
        ):
            raise ValueError(
                "The draft has unexpected, incomplete, or changed assets. Inspect it manually; no asset was replaced."
            )

    return names


def create_draft(api, tag, commit, *, run_id, marker, signed):
    """Create only a draft release, with source provenance and explicit signing status."""
    signing_note = (
        "\nThe Mac app is Developer ID signed and notarized.\n"
        if signed
        else "\nThe Mac app is ad-hoc signed and unnotarized.\n"
    )
    body = (
        f"{marker}\nBuilt from commit `{commit}` in workflow run `{run_id}`.\n"
        + signing_note
        + "\nInstall the Linux package matching your architecture with `sudo apt install ./FILE.deb`, "
        "then reconnect the keyboard. "
        "On Mac, open the DMG and drag KeyboardLights into Applications.\n"
        f"\n[Installation and release guide](https://github.com/{api.repository}/blob/{commit}/RELEASE.md)\n"
        "\nRequires legacy C989 firmware 1.38. Check SHA256SUMS before installing.\n"
    )
    return api.request(
        "POST",
        "/releases",
        data={"tag_name": tag, "target_commitish": commit, "name": "KeyboardLights " + tag, "body": body, "draft": True,
            "prerelease": False, "make_latest": "false"},
    )


def upload_missing_assets(api, identifier, files, hashes):
    """Recheck draft state before each upload and require the server's matching digest."""
    for path in files:
        current = api.request("GET", f"/releases/{identifier}")
        if current.get("draft") is not True or current.get("immutable") is True:
            raise ValueError("The release was published while preparing assets; no further uploads are allowed.")

        uploaded = api.request("POST", f"releases/{identifier}/assets?name=" + quote(path.name, safe=""), file=path)
        if uploaded.get("digest") != hashes[path.name] or uploaded.get("state") != "uploaded":
            raise ValueError(
                "Uploaded asset verification failed. The release remains a draft; inspect before retrying."
            )


def prepare_draft(api, *, tag, commit, run_id, directory, signed):
    """Create or resume a matching draft without replacing existing assets."""
    verify_source(api, tag, commit)
    files = assemble(directory, commit, run_id, signed)
    hashes = {path.name: "sha256:" + file_sha256(path) for path in files}
    marker = f"<!-- keyboardlights-release:{commit}:{run_id} -->"
    existing = api.request("GET", "/releases/tags/" + quote(tag, safe=""), missing_ok=True)
    if existing is not None:
        if existing.get("draft") is not True or existing.get("immutable") is True:
            raise ValueError("An existing published or immutable release will never be changed.")

        if existing.get("target_commitish") != commit or marker not in (existing.get("body") or ""):
            raise ValueError("An unrelated draft already uses this tag; inspect it manually. Nothing was changed.")

        names = matching_draft_assets(api, existing, hashes)
        missing = [path for path in files if path.name not in names]
        identifier = existing["id"]
    else:
        created = create_draft(api, tag, commit, run_id=run_id, marker=marker, signed=signed)
        identifier = created["id"]
        missing = files

    verify_source(api, tag, commit)
    upload_missing_assets(api, identifier, missing, hashes)
    verify_source(api, tag, commit)
    print(f"Draft release ready for manual review: {tag}. No release was published or existing asset replaced.")


def main():
    """Dispatch release validation or controlled draft preparation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("verify-source", "verify-mac-preview", "prepare-draft"))
    parser.add_argument("--tag", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--signed", action="store_true")
    args = parser.parse_args()
    try:
        api = GitHub(os.environ.get("GH_REPO", ""), os.environ.get("GH_TOKEN", ""))
        if args.command == "verify-source":
            verify_source(api, args.tag, args.commit)
        elif args.command == "verify-mac-preview":
            verify_source(api, args.tag, args.commit)
            verify_bundle(args.directory, target="macos", architecture="universal2", commit=args.commit,
                run_id=os.environ["GITHUB_RUN_ID"])
        else:
            prepare_draft(api, tag=args.tag, commit=args.commit, run_id=os.environ["GITHUB_RUN_ID"],
                directory=args.directory, signed=args.signed)
    except (ValueError, RuntimeError, OSError, KeyError, TypeError) as error:
        parser.exit(1, f"Release management stopped: {error}\n")


if __name__ == "__main__":
    main()
