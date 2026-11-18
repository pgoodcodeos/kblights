# Installation and releases

## Installation

Download an installer and its `.sha256` file from
[GitHub Releases](https://github.com/pgoodcodeos/kblights/releases).
KeyboardLights supports the legacy Cloud Nine C989 with firmware 1.38; revised
hardware with firmware 1.82 is incompatible.

### macOS

Requires macOS 14 or later. The universal DMG supports Apple Silicon and Intel.
Open the DMG, drag KeyboardLights onto Applications, then open it from Applications.
See the [macOS guide](docs/macos.md) for lighting, key assignments, and profiles.

#### Keyboard access on first launch

Connect the keyboard. If access has not already been approved, KeyboardLights
guides you through **Input Monitoring**, the macOS permission it needs to read
keyboard settings and apply changes.

1. Choose **Open Input Monitoring** in the main KeyboardLights window. The app
   opens System Settings after requesting access.
2. Under **Privacy & Security → Input Monitoring**, turn on **KeyboardLights**.
   If it is missing, choose **Show app in Finder**, then use **+** in Input
   Monitoring to add that copy of KeyboardLights.app.
3. If macOS offers **Quit & Reopen**, choose it. Otherwise, return to KeyboardLights;
   setup continues automatically.

If **Keyboard not connected** appears, connect the keyboard and choose **Refresh**.
The installer cannot grant keyboard access for you.

Installers ending in `-adhoc.dmg` or `-unnotarized.dmg` are not notarized by
Apple, and macOS may block first launch. Check the release notes for signing status.
After verifying the download and attempting to open the installed app, open
**System Settings → Privacy & Security**, scroll to **Security**, and choose
**Open Anyway** for KeyboardLights. Confirm the prompt to open it. This approval
is separate from Input Monitoring permission.

#### Update or remove the app

Quit KeyboardLights before updating. Open the new DMG and copy KeyboardLights
into Applications, replacing the old copy, then launch it again. If keyboard
access needs approval again, follow the Input Monitoring steps above.

To remove the app, quit it and move KeyboardLights from Applications to Trash.
Saved profiles and diagnostic records remain in
`~/Library/Application Support/KeyboardLights`. App preferences are also kept.

### Linux

Use a Debian/Ubuntu system with Python 3.11+ and GTK 4.12+, such as Ubuntu 24.04.
The `.deb` installs the app's runtime dependencies for you.

1. Check your system's architecture with `dpkg --print-architecture`: choose
   `amd64` for x86-64 or `arm64` for ARM64.
2. Download the matching `.deb` and `.sha256` file, then
   [verify the download](#verify-a-download).
3. Open a terminal in the folder containing the downloaded package and install it:

    ```sh
    sudo apt install ./keyboardlights_1.0.0_amd64.deb
    ```

Use your downloaded filename. If apt cannot satisfy the GTK requirement, your
distribution needs a release that provides GTK 4.12 or later.

Reconnect the keyboard, then open **KeyboardLights** from the application menu
or run `keyboardlights`. Run it as your normal desktop user. Wait for
**C989 connected** and **Current keyboard state loaded** in the footer.
If needed, choose **Refresh** at the top right.

To upgrade, download the new `.deb` and use the same install command, then
relaunch the app and reconnect the keyboard. To remove it:

```sh
sudo apt remove keyboardlights
```

Removing or purging the package keeps your profiles and preferences. See the
[Linux guide](docs/linux.md) for usage, file locations, and troubleshooting.

### Verify a download

From the directory containing the installer and its checksum file, run:

```sh
# macOS
shasum -a 256 -c KeyboardLights-1.0.0-macos-universal2-adhoc.dmg.sha256

# Linux
sha256sum -c keyboardlights_1.0.0_amd64.deb.sha256
```

Use the matching filename for your download. Each release also includes
`SHA256SUMS` and JSON manifests with the source commit, architecture, checksum,
and signing status. Checksums detect changed files; they do not authenticate the publisher.

## Building installers

Run the [tests](CONTRIBUTING.md#tests) first. From the checkout root, follow the
steps for your platform. Installers, checksums, and JSON manifests go in `dist/`.
The Apache 2.0 license is included in both installer formats.

### Linux packages

Install the build and validation tools on Ubuntu 24.04 or a compatible system:

```sh
sudo apt install git python3 build-essential dpkg-dev desktop-file-utils appstream lintian
python3 scripts/release.py linux
```

Build natively on `amd64` or `arm64`; the package uses the build machine's
architecture. The packaging script compiles the native library for you. Packages
use `oscoder47 <maintainer@pgoodcode.com>` as the maintainer contact. Set
`KEYBOARDLIGHTS_MAINTAINER` to override it for a local build.

The builder hardens and strips the native library, calculates its runtime
dependencies, and validates desktop metadata, software-center metadata, and the
archive. Lintian errors or warnings stop the build before replacing an existing
installer. The package includes a manual page, license, and changelog. Tests,
screenshots, and checkout files stay out of the package.

Package timestamps default to the source commit time. Set `SOURCE_DATE_EPOCH`
to a nonnegative Unix timestamp to override it. The same source, toolchain,
maintainer, and timestamp produce identical `.deb` files across output directories
and umasks. The manifest records the source commit and whether the checkout has
uncommitted changes.

CI on amd64 and arm64 checks package installation, upgrade from a synthetic older
package version, reinstallation, removal, and purge. It opens the installed GUI
offline, checks its menus, palettes, About dialog, and launcher isolation, and
verifies that user profiles and state survive package operations. Hardware checks
remain a separate release step.

### macOS disk images

Mac releases currently use ad-hoc signing without Apple notarization. This needs
no Apple developer account or signing certificate. The app has an ad-hoc code
signature; installer filenames use the `-adhoc.dmg` suffix to distinguish
them from Developer ID releases.

With full Xcode selected, build the installer containing the ad-hoc-signed app:

```sh
KEYBOARDLIGHTS_SIGN_IDENTITY=- python3 scripts/release.py macos
```

The default DMG contains both Apple Silicon and Intel executables. For a local
single-architecture preview, add `--arch arm64` or `--arch x86_64`.

Before replacing an existing output, the builder checks the app's identity,
versions, resources, permissions, architectures, macOS 14 compatibility, signature,
and library dependencies. It also mounts the finished DMG read-only to check the
app, Applications shortcut, and instructions. These checks do not launch the app
or access a keyboard.

The builder uses standard bundle permissions, even with restrictive build settings.
If publishing the output fails, it restores the previous installer, checksum, and
manifest. If restoration also fails, the error gives the recovery folder's location.

To repeat the checks on a generated universal preview:

```sh
python3 scripts/release/check-macos-image.py dist/KeyboardLights-1.0.0-macos-universal2-adhoc.dmg
```

Use your build's filename and source checkout. For a single-architecture image,
add the matching `--arch` option. For a notarized release, add `--notarized` to
check both stapled tickets and Gatekeeper acceptance.

For a signed and notarized Mac installer, set `KEYBOARDLIGHTS_SIGN_IDENTITY` to a
Developer ID Application identity and `KEYBOARDLIGHTS_NOTARY_PROFILE` to an
existing keychain notary profile, then run:

```sh
python3 scripts/release.py macos --distribution
```

To sign a previously tested preview without rebuilding its app, add
`--from-image PATH_TO_PREVIEW.dmg`. Use the same source checkout and architecture;
the builder checks the preview's version and keyboard data before copying it.
The app and DMG each receive a notarization ticket, so the installed app keeps
its ticket after being copied out of the image.

## Preparing a release

Build and release scripts require Python 3.11 or later. Run
`python3 scripts/release.py check-version` to check that `VERSION`, the Python
package version, and the newest changelog entry agree.

Complete local verification before pushing or publishing:

1. Run the automated tests and check installation and permissions on both platforms.
2. On supported hardware, test automatic startup reads, permission setup,
   reconnection with Refresh, and reviewed lighting and assignment updates.
   Verify that unrelated settings and saved profiles are preserved.
3. Review the release notes and [changelog](CHANGELOG.md). Check that version
   metadata matches `VERSION`.
4. Confirm the public repository URL, maintainer contact, and copyright attribution.
   Mac releases use ad-hoc signing for now.

### GitHub configuration

CI uses `oscoder47 <maintainer@pgoodcode.com>` as the public maintainer contact.
The optional repository variable `RELEASE_MAINTAINER` overrides it; tagged builds
reject placeholder contacts. Configure the `github-release`
environment for release preparation.

Require the **Code style**, **Release policy**, **Linux (amd64)**,
**Linux (arm64)**, and **macOS (Universal)** checks in the default branch's
protection rules. Complete a successful workflow run before pushing a release tag.

For the current ad-hoc-signed releases, leave `MACOS_SIGNED_RELEASE` unset or set
it to `false`. No signing secrets or `macos-signing` environment are needed.
To enable Developer ID signing and notarization later, set the variable to `true`,
configure the `macos-signing` environment, and add these secrets there:

- `MACOS_CERTIFICATE_P12_BASE64`
- `MACOS_CERTIFICATE_PASSWORD`
- `MACOS_SIGNING_IDENTITY`
- `APPLE_ID`
- `APPLE_TEAM_ID`
- `APPLE_APP_PASSWORD`

Keep credential values out of source files. The workflow uses a temporary
keychain and runs cleanup when the signing job ends. Signing runs only for
pushed version tags, never pull requests or manual builds.

### Publish

Commit the reviewed source to the default branch and tag it `v` followed by the
value in `VERSION` (for example, `v1.0.0`). Pushing the tag starts the
[release workflow](.github/workflows/ci.yml), which tests macOS and both Linux
architectures, checks the artifacts, and prepares a draft
release. Pull requests and manual runs produce test artifacts without a release draft.

Review the draft's installers, checksums, manifests, notes, and signing status,
then publish it manually. The workflow never publishes a release automatically.
