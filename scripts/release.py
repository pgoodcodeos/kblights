#!/usr/bin/env python3
"""Build versioned installers using only native platform tools and Python's stdlib."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import plistlib
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import tomllib
from contextlib import contextmanager
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_ID = "io.github.keyboardlights.KeyboardLights"
MACOS_RESOURCE_FILES = ("layout.json", "assignments.json", "lighting-colors.json")

# Explicit source payload; tests and checkout debris must never be shipped.
LINUX_MODULES = ("__init__.py", "__main__.py", "app.py", "assignments/__init__.py", "assignments/editor.py",
    "assignments/model.py", "assignments/service.py", "device/__init__.py", "device/activity.py", "device/capture.py",
    "device/lock_indicators.py", "device/core.py", "lighting/__init__.py", "lighting/changes.py", "lighting/editor.py",
    "lighting/service.py", "profiles/__init__.py", "profiles/io.py", "profiles/lighting.py", "profiles/store.py",
    "resources.py", "ui/__init__.py", "ui/assignments.py", "ui/key_inspector.py", "ui/lighting_profiles.py",
    "ui/presentation.py", "ui/paint_tools.py", "ui/profiles.py", "ui/widgets.py")


def version() -> str:
    """Read and validate the shared numeric release version."""
    value = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", value):
        raise ValueError("VERSION must contain a numeric X.Y.Z version (without a v prefix).")

    return value


def validate_version_metadata():
    """Require package metadata and the newest changelog entry to match VERSION."""
    value = version()
    with (ROOT / "pyproject.toml").open("rb") as stream:
        package_version = tomllib.load(stream).get("project", {}).get("version")

    if package_version != value:
        raise ValueError(f"pyproject.toml project.version must match VERSION: {value}")

    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    newest = re.search(r"^##[ \t]+(.+?)[ \t]*$", changelog, re.MULTILINE)
    if newest is None or newest.group(1) != value:
        raise ValueError(f"The newest CHANGELOG.md entry must match VERSION: {value}")

    return value


def file_sha256(path: Path) -> str:
    """Hash an installer without reading the complete image into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def installer_name(target, release_version, architecture, *, signed=False, notarized=False):
    """Keep package builders and GitHub artifact validation on one naming contract."""
    if target == "linux" and architecture in ("amd64", "arm64"):
        return f"keyboardlights_{release_version}_{architecture}.deb"

    if target == "macos" and architecture in ("arm64", "x86_64", "universal2"):
        suffix = "" if notarized else "-unnotarized" if signed else "-adhoc"
        return f"KeyboardLights-{release_version}-macos-{architecture}{suffix}.dmg"

    raise ValueError("Unsupported release platform or architecture.")


def run(arguments, *, cwd=ROOT, env=None, capture=False):
    """Run a platform tool without exposing credential arguments in exception text."""
    result = subprocess.run([str(item) for item in arguments], cwd=cwd, env=env, text=True,
        stdout=subprocess.PIPE if capture else None, check=False)
    if result.returncode:
        # Do not include argument values: signing commands can contain credentials.
        raise RuntimeError(f"{arguments[0]} failed with exit status {result.returncode}")

    return result.stdout.strip() if capture else None


def require(*commands):
    """Fail early when a platform build tool is unavailable."""
    missing = [command for command in commands if not shutil.which(command)]
    if missing:
        raise ValueError("Missing build tools: " + ", ".join(missing))


def output_directory(value: str) -> Path:
    """Resolve an output directory without allowing a repository ancestor."""
    path = Path(value).expanduser().resolve()
    if path == ROOT or path in ROOT.parents:
        raise ValueError("Output must not be the repository or one of its parent directories.")

    path.mkdir(parents=True, exist_ok=True)
    return path


def install_file(source: Path, target: Path, mode=0o644):
    """Copy a runtime file into a staged package with an explicit mode."""
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    target.chmod(mode)


def write_file(path: Path, value: str, mode=0o644):
    """Write a staged text file with an explicit mode."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    path.chmod(mode)


def publish(source: Path, destination: Path, *, target=None, architecture=None, notarized=False):
    """Prepare companion metadata before replacing final installer files."""

    # Prepare metadata before replacing a previous installer: even a Git failure
    # must not destroy the last good artifact. Each final rename is atomic.
    digest = file_sha256(source)
    checksum = destination.with_name(destination.name + ".sha256")
    temporary_checksum = source.parent / checksum.name
    temporary_checksum.write_text(f"{digest}  {destination.name}\n", encoding="utf-8")
    replacements = [(source, destination), (temporary_checksum, checksum)]
    if target is not None:
        manifest = {"format": "keyboardlights-release-v1", "version": version(), "file": destination.name,
            "sha256": digest, "platform": target, "architecture": architecture, "notarized": notarized,
            "source_commit": run(["git", "rev-parse", "HEAD"], capture=True),
            "source_dirty": bool(run(["git", "status", "--porcelain", "--untracked-files=normal"], capture=True)),
            "run_id": os.environ.get("GITHUB_RUN_ID")}
        manifest_path = destination.with_name(destination.name + ".json")
        temporary = source.parent / manifest_path.name
        temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        replacements.append((temporary, manifest_path))

    if any(final.is_symlink() or (final.exists() and not final.is_file()) for _, final in replacements):
        raise ValueError("An output filename is occupied by a nonregular file; previous installers were kept.")

    # Installer and metadata are separate files. Keep recovery copies outside the
    # caller's temporary work tree until every replacement has succeeded.
    backup = Path(tempfile.mkdtemp(prefix=".keyboardlights-recovery-", dir=destination.parent))
    changed = []
    keep_backup = False
    try:
        for _, final in replacements:
            if final.exists():
                shutil.copy2(final, backup / final.name)

        try:
            for temporary, final in replacements:
                temporary.replace(final)
                changed.append(final)
        except BaseException:
            failures = []
            for final in reversed(changed):
                previous = backup / final.name
                try:
                    if previous.exists():
                        previous.replace(final)
                    else:
                        final.unlink()
                except OSError as error:
                    failures.append(error)

            if failures:
                keep_backup = True
                raise RuntimeError(
                    f"Publication rollback failed; previous artifacts are preserved at {backup}"
                ) from failures[0]

            raise
    finally:
        if not keep_backup:
            shutil.rmtree(backup)

    print(f"Created {destination}")
    print(f"Checksum {checksum}")


def stage_linux(stage: Path, library: Path, release_version: str, architecture: str, maintainer: str, *,
    native_dependencies="libc6", epoch=0):
    """Assemble a relocatable package payload; never copy a checkout or build tree."""
    app = stage / "usr/lib/keyboardlights"
    package = app / "keyboardlights"
    for relative in LINUX_MODULES:
        install_file(ROOT / "apps/linux/keyboardlights" / relative, package / relative)

    for name in ("style.css", "pointer.svg", "brush.svg", "bucket.svg"):
        install_file(ROOT / "apps/linux/keyboardlights/ui" / name, package / "ui" / name)

    for name in ("layout.json", "assignments.json", "lighting-colors.json"):
        install_file(ROOT / "data/c989" / name, package / "data" / name)

    install_file(library, package / "device/lighting_core.so")
    install_file(ROOT / "assets/app-icon.svg", package / "app-icon.svg")
    write_file(app / "launch.py", "from keyboardlights.app import main\nraise SystemExit(main())\n")
    install_file(ROOT / "VERSION", app / "VERSION")
    install_file(ROOT / "packaging/linux/keyboardlights", stage / "usr/bin/keyboardlights", 0o755)
    install_file(ROOT / f"packaging/linux/{APP_ID}.desktop", stage / f"usr/share/applications/{APP_ID}.desktop")
    install_file(ROOT / f"packaging/linux/{APP_ID}.metainfo.xml", stage / f"usr/share/metainfo/{APP_ID}.metainfo.xml")
    install_file(ROOT / "assets/app-icon.svg", stage / f"usr/share/icons/hicolor/scalable/apps/{APP_ID}.svg")
    install_file(ROOT / "packaging/linux/70-keyboardlights.rules",
        stage / "usr/lib/udev/rules.d/70-keyboardlights.rules")
    for name in ("postinst", "postrm"):
        install_file(ROOT / "packaging/linux" / name, stage / "DEBIAN" / name, 0o755)

    install_file(ROOT / "LICENSE", stage / "usr/share/doc/keyboardlights/copyright")
    sections = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8").split(f"## {release_version}\n", 1)
    if len(sections) != 2:
        raise ValueError(f"CHANGELOG.md has no entry for version {release_version}.")

    changes = sections[1].split("\n## ", 1)[0].strip()
    try:
        date = format_datetime(datetime.fromtimestamp(epoch, timezone.utc))
    except (ValueError, OverflowError, OSError) as error:
        raise ValueError("SOURCE_DATE_EPOCH is outside the supported date range.") from error

    changelog = (
        f"keyboardlights ({release_version}) unstable; urgency=medium\n\n"
        + "\n".join(
            textwrap.fill(line.removeprefix("- "), width=78, initial_indent="  * " if line.startswith("- ") else "    ",
                subsequent_indent="    ")
            for line in changes.splitlines()
        )
        + f"\n\n -- {maintainer}  {date}\n"
    )
    for path, content in (("usr/share/doc/keyboardlights/changelog.gz", changelog),
        ("usr/share/man/man1/keyboardlights.1.gz", (ROOT / "packaging/linux/keyboardlights.1").read_text())):
        target = stage / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(gzip.compress(content.encode("utf-8"), mtime=0))
        target.chmod(0o644)

    install_file(ROOT / "packaging/linux/README", stage / "usr/share/doc/keyboardlights/README")

    # Match dpkg-gencontrol: round each file up to KiB and count directories as 1 KiB.
    size = 2 + sum(1 if path.is_dir() else (path.stat().st_size + 1023) // 1024 for path in (stage / "usr").rglob("*"))
    write_file(
        stage / "DEBIAN/control",
        f"""Package: keyboardlights
Version: {release_version}
Section: utils
Priority: optional
Architecture: {architecture}
Maintainer: {maintainer}
Installed-Size: {size}
Depends: python3 (>= 3.11), python3-gi, python3-gi-cairo, gir1.2-gtk-4.0 (>= 4.12), {native_dependencies}
Recommends: udev
Description: Configure legacy Cloud Nine C989 keyboards
 Native GTK lighting and key assignments for C989 firmware 1.38.
 Includes local profiles, reviewed updates, and verified device writes.
""",
    )
    checksums = []
    for path in sorted((stage / "usr").rglob("*")):
        if path.is_file():
            digest = hashlib.md5(path.read_bytes(), usedforsecurity=False).hexdigest()
            checksums.append(f"{digest}  {path.relative_to(stage)}\n")

    write_file(stage / "DEBIAN/md5sums", "".join(checksums))

    # Match Debian filesystem ownership modes regardless of the builder's umask.
    stage.chmod(0o755)
    for path in stage.rglob("*"):
        if path.is_dir():
            path.chmod(0o755)


def validate_maintainer(value, *, require_real=False):
    """Validate a package contact and optionally reject preview placeholders."""
    match = re.fullmatch(r"[^<>\r\n]+ <[^<>\s@]+@([^<>\s@]+\.[^<>\s@]+)>", value)
    if not match:
        raise ValueError("Maintainer must be a single-line 'Name <email@domain>' contact.")

    domain = match.group(1).lower()
    if require_real and (
        domain.endswith((".invalid", ".test", ".example", ".localhost"))
        or any(
            domain == reserved or domain.endswith("." + reserved)
            for reserved in ("example.com", "example.org", "example.net")
        )
    ):
        raise ValueError("Set RELEASE_MAINTAINER to a real project contact before tagging a GitHub release.")


def linux_package(args):
    """Build and inspect a native Debian package in an isolated staging directory."""
    if sys.platform != "linux":
        raise ValueError("Build the Linux package on Linux (Ubuntu 24.04+ is the release baseline).")

    require("git", "cc", "strip", "dpkg", "dpkg-deb", "dpkg-buildflags", "dpkg-shlibdeps", "desktop-file-validate",
        "appstreamcli", "lintian")
    architecture = run(["dpkg", "--print-architecture"], capture=True)
    if architecture not in ("amd64", "arm64"):
        raise ValueError("Supported Linux architectures: amd64 and arm64. Build natively on the target architecture.")

    validate_maintainer(args.maintainer)
    release_version = version()
    output = output_directory(args.output)
    name = installer_name("linux", release_version, architecture)
    env = os.environ.copy()
    epoch = env.get("SOURCE_DATE_EPOCH", run(["git", "log", "-1", "--format=%ct"], capture=True))
    if not re.fullmatch(r"[0-9]+", epoch):
        raise ValueError("SOURCE_DATE_EPOCH must be a nonnegative integer.")

    env["SOURCE_DATE_EPOCH"] = epoch
    env["DEB_BUILD_MAINT_OPTIONS"] = (env.get("DEB_BUILD_MAINT_OPTIONS", "") + " hardening=+all").strip()
    flags = {
        name: shlex.split(run(["dpkg-buildflags", "--get", name], env=env, capture=True))
        for name in ("CPPFLAGS", "CFLAGS", "LDFLAGS")
    }
    with tempfile.TemporaryDirectory(prefix=".keyboardlights-", dir=output) as directory:
        work = Path(directory)
        library = work / "lighting_core.so"
        run(
            ["cc", *flags["CPPFLAGS"], *flags["CFLAGS"], "-std=c11", "-Wall", "-Wextra", "-Werror", "-fPIC", "-shared",
                ROOT / "core/Core.c", "-o", library, *flags["LDFLAGS"]],
            env=env,
        )
        run(["strip", "--strip-unneeded", library])

        # Derive libc requirements from the actual binary instead of guessing a glibc version.
        write_file(
            work / "debian/control",
            (
                "Source: keyboardlights\nSection: utils\nPriority: optional\n"
                f"Maintainer: {args.maintainer}\n\nPackage: keyboardlights\n"
                "Architecture: any\nDescription: Keyboard configuration\n"
            ),
        )
        stage = work / "debian/keyboardlights"
        (stage / "DEBIAN").mkdir(parents=True)
        staged_library = stage / "usr/lib/keyboardlights/keyboardlights/device/lighting_core.so"
        install_file(library, staged_library)
        dependencies = run(["dpkg-shlibdeps", "-O", "-e" + str(staged_library.relative_to(work))], cwd=work,
            capture=True)
        native = next(
            (
                line.removeprefix("shlibs:Depends=")
                for line in dependencies.splitlines()
                if line.startswith("shlibs:Depends=")
            ),
            "",
        )
        if not native:
            raise ValueError("dpkg-shlibdeps did not report native library dependencies.")

        stage_linux(stage, library, release_version, architecture, args.maintainer, native_dependencies=native,
            epoch=int(epoch))
        run(["desktop-file-validate", stage / f"usr/share/applications/{APP_ID}.desktop"])
        run(["appstreamcli", "validate", "--no-net", stage / f"usr/share/metainfo/{APP_ID}.metainfo.xml"])
        run(["dpkg-deb", "--root-owner-group", "--build", stage, work / name], env=env)
        run(["dpkg-deb", "--info", work / name])
        run(["lintian", "--fail-on", "error,warning", work / name])
        publish(work / name, output / name, target="linux", architecture=architecture)


def macos_build_environment():
    """Select full Xcode and keep compiler caches within the ignored build tree."""
    env = os.environ.copy()
    if not env.get("DEVELOPER_DIR") and Path("/Applications/Xcode.app/Contents/Developer").is_dir():
        env["DEVELOPER_DIR"] = "/Applications/Xcode.app/Contents/Developer"

    env.setdefault("CLANG_MODULE_CACHE_PATH", str(ROOT / ".build/clang-module-cache"))
    env.setdefault("SWIFTPM_MODULECACHE_OVERRIDE", str(ROOT / ".build/swiftpm-module-cache"))
    return env


def build_macos_binary(env, architectures, *, configuration="release"):
    """Build the requested architectures and verify the resulting executable."""
    options = ["-c", configuration, "--product", "KeyboardLights"]
    for architecture in architectures:
        options.extend(["--arch", architecture])

    if env.get("KEYBOARDLIGHTS_SDK"):
        options.extend(["--sdk", env["KEYBOARDLIGHTS_SDK"]])

    run(["swift", "build", *options], env=env)
    binary = Path(run(["swift", "build", *options, "--show-bin-path"], env=env, capture=True)) / "KeyboardLights"
    actual_architectures = set(run(["lipo", "-archs", binary], capture=True).split())
    if actual_architectures != set(architectures):
        raise ValueError(f"Expected architectures {architectures}; got {sorted(actual_architectures)}")

    return binary


@contextmanager
def mounted_macos_image(image, mount):
    """Mount an image read-only and detach it even when validation or copying fails."""
    mount.mkdir()
    run(["hdiutil", "attach", "-quiet", "-nobrowse", "-readonly", "-mountpoint", mount, Path(image).resolve()])
    try:
        yield mount
    finally:
        run(["hdiutil", "detach", "-quiet", mount])


def validate_macos_app(app, architectures, release_version):
    """Reject incomplete, incompatible, or checkout-dependent app bundles without launching them."""
    if app.is_symlink() or not app.is_dir():
        raise ValueError("Expected a regular KeyboardLights app bundle.")

    for path in app.rglob("*"):
        if path.is_symlink() or not (path.is_dir() or stat.S_ISREG(path.stat().st_mode)):
            raise ValueError("The app bundle must contain only regular files and directories.")

    contents = app / "Contents"
    info = plistlib.loads((contents / "Info.plist").read_bytes())
    if not isinstance(info, dict):
        raise ValueError("The app Info.plist must contain a metadata dictionary.")

    expected = {"CFBundleIdentifier": "com.keyboardlights.c989", "CFBundleExecutable": "KeyboardLights",
        "CFBundlePackageType": "APPL", "CFBundleIconFile": "AppIcon", "LSMinimumSystemVersion": "14.0",
        "CFBundleShortVersionString": release_version, "CFBundleVersion": release_version}
    for key, value in expected.items():
        if info.get(key) != value:
            raise ValueError(f"Unexpected {key} in the app bundle; expected {value}.")

    if not info.get("NSInputMonitoringUsageDescription"):
        raise ValueError("The app bundle is missing its keyboard access explanation.")

    binary = contents / "MacOS/KeyboardLights"
    if sorted(path.name for path in binary.parent.iterdir()) != ["KeyboardLights"] or not os.access(binary, os.X_OK):
        raise ValueError("Expected exactly one executable: Contents/MacOS/KeyboardLights.")

    resources = contents / "Resources"
    for name in ("AppIcon.icns", "LICENSE"):
        if not (resources / name).is_file() or not (resources / name).stat().st_size:
            raise ValueError(f"The app bundle is missing {name}.")

    bundle = resources / "KeyboardLights_KeyboardData.bundle"
    layouts = [bundle / "c989", bundle / "Contents/Resources/c989"]
    layouts = [path for path in layouts if path.is_dir()]
    if len(layouts) != 1:
        raise ValueError("Expected one complete keyboard data resource directory.")

    if sorted(path.name for path in layouts[0].iterdir()) != sorted(MACOS_RESOURCE_FILES):
        raise ValueError("The keyboard data resource bundle has missing or unexpected files.")

    for name in MACOS_RESOURCE_FILES:
        expected_data = (ROOT / "data/c989" / name).read_bytes()
        packaged = layouts[0] / name
        if packaged.stat().st_size != len(expected_data) or packaged.read_bytes() != expected_data:
            raise ValueError(f"Bundled {name} does not match this checkout's keyboard data.")

    run(["codesign", "--verify", "--strict", app])
    actual = set(run(["lipo", "-archs", binary], capture=True).split())
    if actual != set(architectures):
        raise ValueError(f"Expected architectures {architectures}; got {sorted(actual)}")

    dependencies = run(["otool", "-arch", "all", "-L", binary], capture=True)
    for line in dependencies.splitlines():
        if not line.startswith(("\t", " ")):
            continue

        dependency = line.strip().split(" ", 1)[0]
        if not dependency.startswith(("/System/Library/", "/usr/lib/")) and not re.fullmatch(
            r"@rpath/libswift[A-Za-z0-9_]+\.dylib", dependency):
            raise ValueError(f"The app depends on a non-system library: {dependency}")

    commands = run(["otool", "-arch", "all", "-l", binary], capture=True)
    minimums = []
    for command in commands.split("Load command "):
        if re.search(r"cmd LC_BUILD_VERSION\b", command):
            if not re.search(r"platform 1\s", command):
                raise ValueError("The app executable targets a platform other than macOS.")

            minimum = re.search(r"minos ([0-9.]+)", command)
        elif re.search(r"cmd LC_VERSION_MIN_MACOSX\b", command):
            minimum = re.search(r"version ([0-9.]+)", command)
        else:
            continue

        if minimum is None:
            raise ValueError("The executable is missing a macOS deployment target.")

        minimums.append(tuple(int(part) for part in minimum[1].split(".")))

    if len(minimums) != len(actual) or any(value > (14, 0, 0) for value in minimums):
        raise ValueError("Every executable architecture must support the advertised macOS 14 minimum.")


def validate_macos_image(image, architectures, release_version, *, notarized=False):
    """Inspect the final disk image before publication, including its installed app payload."""
    run(["hdiutil", "verify", image])
    with tempfile.TemporaryDirectory(prefix="keyboardlights-dmg-check-") as name:
        with mounted_macos_image(image, Path(name) / "mounted") as mount:
            app = mount / "KeyboardLights.app"
            validate_macos_app(app, architectures, release_version)
            link = mount / "Applications"
            if not link.is_symlink() or link.readlink() != Path("/Applications"):
                raise ValueError("The disk image needs an Applications shortcut pointing to /Applications.")

            instructions = mount / "Install KeyboardLights.txt"
            if instructions.is_symlink() or not instructions.is_file() or not instructions.stat().st_size:
                raise ValueError("The disk image is missing its installation instructions.")

            if notarized:
                run(["xcrun", "stapler", "validate", app])
                run(["spctl", "--assess", "--type", "execute", app])

    if notarized:
        run(["codesign", "--verify", "--strict", image])
        run(["xcrun", "stapler", "validate", image])
        run(["spctl", "--assess", "--type", "open", "--context", "context:primary-signature", image])


def copy_tested_macos_app(image, app, architectures, release_version):
    """Copy a verified app from a read-only preview without running or rebuilding it."""
    with mounted_macos_image(image, app.parent.parent / "mounted") as mount:
        source_app = mount / "KeyboardLights.app"
        validate_macos_app(source_app, architectures, release_version)
        app.parent.mkdir()
        run(["ditto", source_app, app])


def stage_macos_app(binary, app, env, release_version):
    """Assemble the native app bundle and render its icon using AppKit."""
    contents = app / "Contents"
    work = app.parent.parent
    install_file(binary, contents / "MacOS/KeyboardLights", 0o755)
    info = plistlib.loads((ROOT / "apps/macos/Resources/Info.plist").read_bytes())
    info["CFBundleShortVersionString"] = release_version
    info["CFBundleVersion"] = release_version
    (contents / "Info.plist").write_bytes(plistlib.dumps(info))
    iconset = work / "AppIcon.iconset"
    iconset.mkdir()
    png = work / "icon.png"
    run(["swift", ROOT / "scripts/release/render-icon.swift", ROOT / "assets/app-icon.svg", png], env=env)
    for size in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            pixels = str(size * scale)
            target = iconset / f"icon_{size}x{size}{'@2x' if scale == 2 else ''}.png"
            run(["sips", "-z", pixels, pixels, png, "--out", target], capture=True)

    (contents / "Resources").mkdir()
    install_file(ROOT / "LICENSE", contents / "Resources/LICENSE")
    resource_bundles = list(binary.parent.glob("KeyboardLights_KeyboardData.bundle"))
    if len(resource_bundles) != 1:
        raise ValueError("The built keyboard data resource bundle is missing")

    shutil.copytree(resource_bundles[0], contents / "Resources" / resource_bundles[0].name)
    run(["iconutil", "-c", "icns", iconset, "-o", contents / "Resources/AppIcon.icns"])

    # A restrictive build umask must not produce an app only its builder can read.
    app.chmod(0o755)
    for path in app.rglob("*"):
        if path.is_symlink() or not (path.is_dir() or stat.S_ISREG(path.stat().st_mode)):
            raise ValueError("The staged app must contain only regular files and directories.")

        path.chmod(0o755 if path.is_dir() or path == contents / "MacOS/KeyboardLights" else 0o644)


def codesign_arguments(identity):
    """Use hardened runtime and timestamps for Developer ID, or local ad-hoc signing."""
    signing = ["codesign", "--force", "--sign", identity]
    if identity != "-":
        signing.extend(["--options", "runtime", "--timestamp"])
    else:
        signing.append("--timestamp=none")

    return signing


def build_macos_app():
    """Build a local app with the same icon, resources, and identity as the installer."""
    if sys.platform != "darwin":
        raise ValueError("Build the Mac app on macOS with full Xcode installed.")

    require("swift", "xcrun", "codesign", "iconutil", "sips", "lipo", "otool")
    release_version = version()
    destination = ROOT / ".build/macos/KeyboardLights.app"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink() or (destination.exists() and not destination.is_dir()):
        raise ValueError("The local app output is occupied by a file or symlink.")

    env = macos_build_environment()
    binary = build_macos_binary(env, [os.uname().machine], configuration="debug")
    with tempfile.TemporaryDirectory(prefix=".keyboardlights-", dir=destination.parent) as name:
        work = Path(name)
        app = work / "image/KeyboardLights.app"
        stage_macos_app(binary, app, env, release_version)
        run([*codesign_arguments("-"), app])
        validate_macos_app(app, [os.uname().machine], release_version)

        # Keep recovery outside the temporary work tree if restoring the old app also fails.
        previous = destination.parent / (work.name + "-previous.app")
        if destination.exists():
            destination.rename(previous)

        try:
            app.rename(destination)
        except OSError:
            if previous.exists():
                try:
                    previous.rename(destination)
                except OSError as error:
                    raise RuntimeError(
                        f"App replacement failed; the previous app is preserved at {previous}"
                    ) from error

            raise

        if previous.exists():
            shutil.rmtree(previous)

    print(f"Created {destination}")
    return destination


def macos_package(args):
    """Build or reuse an app, then sign and package its disk image."""
    if sys.platform != "darwin":
        raise ValueError("Build the Mac disk image on macOS with full Xcode installed.")

    require("git", "swift", "xcrun", "codesign", "hdiutil", "iconutil", "sips", "ditto", "lipo", "otool")
    identity = os.environ.get("KEYBOARDLIGHTS_SIGN_IDENTITY", "-")
    profile = os.environ.get("KEYBOARDLIGHTS_NOTARY_PROFILE", "")
    if args.distribution and (identity == "-" or not profile):
        raise ValueError("Distribution requires KEYBOARDLIGHTS_SIGN_IDENTITY and KEYBOARDLIGHTS_NOTARY_PROFILE.")

    if identity != "-" and not identity.startswith("Developer ID Application:"):
        raise ValueError("Use a Developer ID Application identity for distribution, or '-' for local builds.")

    env = macos_build_environment()
    release_version = version()
    output = output_directory(args.output)
    architectures = ["arm64", "x86_64"] if args.arch == "universal2" else [args.arch]
    if args.from_image and not args.distribution:
        raise ValueError("--from-image is only supported with --distribution.")

    binary = None if args.from_image else build_macos_binary(env, architectures)
    name = installer_name("macos", release_version, args.arch, signed=identity != "-", notarized=args.distribution)
    with tempfile.TemporaryDirectory(prefix=".keyboardlights-", dir=output) as work:
        work = Path(work)
        image_root = work / "image"
        app = image_root / "KeyboardLights.app"
        if args.from_image:
            copy_tested_macos_app(args.from_image, app, architectures, release_version)
        else:
            stage_macos_app(binary, app, env, release_version)

        signing = codesign_arguments(identity)
        run([*signing, app])
        validate_macos_app(app, architectures, release_version)
        if args.distribution:
            # Staple the app before creating the read-only image so dragging it to
            # Applications preserves its own offline-verifiable ticket.
            archive = work / "notarization.zip"
            run(["ditto", "-c", "-k", "--keepParent", app, archive])
            notarize_and_staple(archive, app, profile)

        (image_root / "Applications").symlink_to("/Applications")
        write_file(image_root / "Install KeyboardLights.txt", mac_install_text(bool(args.distribution)))
        image = work / name
        run(
            ["hdiutil", "create", "-quiet", "-volname", "KeyboardLights", "-srcfolder", image_root, "-format", "UDZO",
                "-imagekey", "zlib-level=9", image]
        )
        if args.distribution:
            run([*signing, image])
            notarize_and_staple(image, image, profile)

        validate_macos_image(image, architectures, release_version, notarized=bool(args.distribution))
        publish(image, output / name, target="macos", architecture=args.arch, notarized=bool(args.distribution))

    if args.distribution:
        signing_status = "Developer ID + notarized"
    elif identity == "-":
        signing_status = "Ad-hoc, not notarized by Apple"
    else:
        signing_status = "Developer ID, not notarized by Apple"

    print(f"App signing: {signing_status}")


def notarize(path, profile):
    """Require an explicit Accepted response from the Apple notarization service."""
    arguments = ["xcrun", "notarytool", "submit", path, "--keychain-profile", profile, "--wait", "--timeout", "30m"]
    if os.environ.get("KEYBOARDLIGHTS_KEYCHAIN"):
        arguments.extend(["--keychain", os.environ["KEYBOARDLIGHTS_KEYCHAIN"]])

    arguments.extend(["--output-format", "json"])
    result = json.loads(run(arguments, capture=True))
    if result.get("status") != "Accepted":
        raise ValueError(
            "Apple did not accept the notarization submission; no distribution artifact will be published."
        )


def notarize_and_staple(submission, target, profile):
    """Notarize a submitted archive and verify the ticket stapled to its target."""
    notarize(submission, profile)
    run(["xcrun", "stapler", "staple", target])
    run(["xcrun", "stapler", "validate", target])


def mac_install_text(notarized):
    """Describe installation, first-run access, and the image's signing status."""
    signing = (
        "This release is Developer ID signed and notarized.\n"
        if notarized
        else (
            "This build is not notarized by Apple. macOS may block first launch.\n"
            "After verifying the download and trying to open the installed app,\n"
            "open System Settings > Privacy & Security, scroll to Security,\n"
            "and choose Open Anyway for KeyboardLights. Confirm the prompt.\n"
            "This approval is separate from Input Monitoring permission.\n"
        )
    )
    return (
        "Install KeyboardLights\n\n"
        "Requires macOS 14 or later and a legacy C989 with firmware 1.38.\n"
        "Drag KeyboardLights.app onto Applications, then open it from Applications.\n\n"
        + signing
        + "\nAllow keyboard access\n\n"
        "Connect your keyboard and choose Open Input Monitoring in the app.\n"
        "In System Settings > Privacy & Security > Input Monitoring, turn on\n"
        "KeyboardLights. To add a missing entry, choose Show app in Finder in\n"
        "KeyboardLights, then use + in Input Monitoring to add that copy of the app.\n"
        "Choose Quit & Reopen if macOS asks. Otherwise, return to KeyboardLights.\n\n"
        "If Keyboard not connected appears, connect it and choose Refresh.\n\n"
        "Using the app\n\n"
        "Edit lighting and key assignments on the Keyboard page. Open and save\n"
        "profiles from File > Lighting Profile or File > Assignment Profile.\n"
        "Choose Review changes, check the edits, confirm, then choose Apply changes.\n"
        "Editing a draft or saving a profile does not change the keyboard.\n\n"
        "Diagnostics has optional troubleshooting logs. Logging is off by default.\n"
        "Updating or removing the app keeps your saved profiles and logs.\n"
    )


def main():
    """Dispatch the requested native packaging operation."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check-version", help="Validate VERSION and an optional release tag")
    check.add_argument("--tag")
    check.add_argument("--require-maintainer", action="store_true")
    commands.add_parser("macos-app", help="Build a local Mac app with its icon and resources")
    mac = commands.add_parser("macos", help="Build a self-contained Mac DMG")
    mac.add_argument("output", nargs="?", default=str(ROOT / "dist"))
    mac.add_argument("--arch", choices=("arm64", "x86_64", "universal2"), default="universal2")
    mac.add_argument("--from-image", help="Sign a previously tested DMG without rebuilding its app")
    mac.add_argument("--distribution", action="store_true", help="Require Developer ID signing and notarization")
    linux = commands.add_parser("linux", help="Build a native Debian/Ubuntu package")
    linux.add_argument("output", nargs="?", default=str(ROOT / "dist"))
    linux.add_argument("--maintainer",
        default=os.environ.get("KEYBOARDLIGHTS_MAINTAINER", "oscoder47 <maintainer@pgoodcode.com>"))
    args = parser.parse_args()
    try:
        if args.command == "check-version":
            value = validate_version_metadata()
            if args.tag and args.tag != f"v{value}":
                raise ValueError(f"Release tag must match VERSION: v{value}")

            if args.require_maintainer:
                validate_maintainer(os.environ.get("KEYBOARDLIGHTS_MAINTAINER", ""), require_real=True)

            print(value)
        elif args.command == "macos-app":
            build_macos_app()
        elif args.command == "macos":
            macos_package(args)
        else:
            linux_package(args)
    except (ValueError, RuntimeError, OSError, KeyError) as error:
        parser.exit(1, f"Release build stopped: {error}\n")


if __name__ == "__main__":
    main()
