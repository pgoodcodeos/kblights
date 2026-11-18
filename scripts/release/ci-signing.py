#!/usr/bin/env python3
"""Set up or remove an isolated signing keychain on an ephemeral GitHub Mac runner."""

import base64
import json
import os
import secrets
import shlex
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# The executable helper shares only the parent module's subprocess boundary.
from release import run  # pylint: disable=wrong-import-position


def cleanup(directory):
    """Attempt keychain restoration and deletion independently before removing state."""
    root = Path(directory).resolve()
    runner = Path(os.environ["RUNNER_TEMP"]).resolve()
    if root.parent != runner or not root.name.startswith("keyboardlights-signing-"):
        raise ValueError("Refusing an unexpected signing-state directory")

    state = json.loads((root / "state.json").read_text(encoding="utf-8"))
    failures = []
    try:
        run(["security", "list-keychains", "-d", "user", "-s", *state["original"]], capture=True)
    except (RuntimeError, OSError) as error:
        failures.append(error)

    keychain = root / "release.keychain-db"
    try:
        if keychain.exists():
            run(["security", "delete-keychain", keychain], capture=True)
    except (RuntimeError, OSError) as error:
        failures.append(error)
    finally:
        shutil.rmtree(root)

    if failures:
        raise RuntimeError(
            "Signing cleanup failed; temporary files were removed, but the runner keychain list may need inspection."
        ) from failures[0]


def setup():
    """Import signing material into an isolated runner keychain and register cleanup."""
    required = ["MACOS_CERTIFICATE_P12_BASE64", "MACOS_CERTIFICATE_PASSWORD", "MACOS_SIGNING_IDENTITY", "APPLE_ID",
        "APPLE_TEAM_ID", "APPLE_APP_PASSWORD"]
    if any(not os.environ.get(name) for name in required):
        raise ValueError("Signed builds require all six signing/notarization secrets listed in RELEASE.md")

    if "\n" in os.environ["MACOS_SIGNING_IDENTITY"] or "\r" in os.environ["MACOS_SIGNING_IDENTITY"]:
        raise ValueError("Signing identity must be a single line")

    root = Path(tempfile.mkdtemp(prefix="keyboardlights-signing-", dir=os.environ["RUNNER_TEMP"]))
    original = shlex.split(run(["security", "list-keychains", "-d", "user"], capture=True))
    (root / "state.json").write_text(json.dumps({"original": original}), encoding="utf-8")
    keychain = root / "release.keychain-db"
    password = secrets.token_urlsafe(32)
    with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as environment:
        environment.write(f"KEYBOARDLIGHTS_SIGNING_STATE={root}\n")

    try:
        certificate = root / "certificate.p12"
        certificate.write_bytes(base64.b64decode(os.environ["MACOS_CERTIFICATE_P12_BASE64"], validate=True))
        certificate.chmod(0o600)
        run(["security", "create-keychain", "-p", password, keychain], capture=True)
        run(["security", "set-keychain-settings", "-lut", "7200", keychain], capture=True)
        run(["security", "unlock-keychain", "-p", password, keychain], capture=True)
        run(
            ["security", "import", certificate, "-P", os.environ["MACOS_CERTIFICATE_PASSWORD"], "-k", keychain, "-T",
                "/usr/bin/codesign", "-T", "/usr/bin/security"],
            capture=True,
        )
        certificate.unlink()
        run(
            ["security", "set-key-partition-list", "-S", "apple-tool:,apple:,codesign:", "-s", "-k", password, keychain
            ],
            capture=True,
        )
        run(["security", "list-keychains", "-d", "user", "-s", keychain, *original], capture=True)
        run(
            ["xcrun", "notarytool", "store-credentials", "keyboardlights-release", "--keychain", keychain, "--apple-id",
                os.environ["APPLE_ID"], "--team-id", os.environ["APPLE_TEAM_ID"], "--password",
                os.environ["APPLE_APP_PASSWORD"]],
            capture=True,
        )
        with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as environment:
            environment.write(f"KEYBOARDLIGHTS_KEYCHAIN={keychain}\n")
            environment.write("KEYBOARDLIGHTS_NOTARY_PROFILE=keyboardlights-release\n")
            environment.write(f"KEYBOARDLIGHTS_SIGN_IDENTITY={os.environ['MACOS_SIGNING_IDENTITY']}\n")
    except BaseException:
        try:
            cleanup(root)
        except Exception:  # pylint: disable=broad-exception-caught
            # Cleanup must never replace the original setup/cancellation failure.
            print("Additional signing cleanup failure; the original setup error follows.", file=sys.stderr)

        raise


if __name__ == "__main__":
    if os.environ.get("GITHUB_ACTIONS") != "true" or sys.platform != "darwin":
        raise SystemExit("This helper is only for ephemeral GitHub Actions macOS runners.")

    if sys.argv[1:] == ["setup"]:
        setup()
    elif sys.argv[1:] == ["cleanup"]:
        signing_state = os.environ.get("KEYBOARDLIGHTS_SIGNING_STATE")
        if signing_state and Path(signing_state).exists():
            cleanup(signing_state)
    else:
        raise SystemExit("Usage: ci-signing.py setup|cleanup")
