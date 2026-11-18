# Contributing

## Report a problem

Use the [bug report template](https://github.com/pgoodcodeos/kblights/issues/new?template=bug_report.md)
to include your operating system, app version, keyboard model and firmware, and
steps to reproduce the problem. Describe what you expected and what happened.
If something used to work, include the last working version.

Check diagnostic files for personal paths, device identifiers, and profile
content before sharing them. Never post credentials in an issue.

For suspected security vulnerabilities, follow the [private reporting instructions](SECURITY.md).

## Make a change

Keep changes focused. Explain the problem, your solution, and how you tested it.
Include the origin and license of any third-party code or assets.

Build instructions are in the [README](README.md). The [architecture guide](docs/architecture.md)
explains how the apps and shared backend fit together.

### Formatting

Follow the [code style](docs/code-style.md). Install Ruff 0.14.4 and clang-format 21.1.8;
Swift formatting uses Xcode's swift-format. Run:

```sh
python3 scripts/format.py
python3 scripts/format.py --check
```

### Tests

Run the checks relevant to your change:

```sh
swift test -c release
python3 scripts/checks/test-core.py
python3 scripts/checks/test-linux.py
python3 -m unittest discover -s tests/tooling -v
```

The Swift suite includes a home-screen check in a temporary app window and startup
tests with simulated connections and permissions. It does not request keyboard
access or communicate with a keyboard. To save keyboard preview images while
running the UI tests, set `KEYBOARDLIGHTS_PREVIEW_DIR` to an output folder outside
the checkout. The test captures the keycaps and their scroll view at two sizes.

Swift tests require a compatible Xcode installation. If necessary, select it for
one command:

```sh
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer swift test -c release
```

Linux tests require Python 3.11+ and a C compiler. The test runner builds the
shared library. The default command skips GTK tests. To include them on a Linux
desktop, install the [app dependencies](docs/linux.md#build-from-source) and run:

```sh
KEYBOARDLIGHTS_GTK_TEST=1 python3 scripts/checks/test-linux.py
```

For a machine without a display, install `xvfb` and `dbus-x11`, then run:

```sh
dbus-run-session -- xvfb-run -a env KEYBOARDLIGHTS_GTK_TEST=1 python3 scripts/checks/test-linux.py
```

The tooling suite checks Mac bundle contents, architecture and deployment-target
validation, installer publication, and recovery after failures. On macOS it also
tests resource loading from a relocated app. Signing-service calls use test doubles;
a passing suite does not mean a release has been notarized. To build and inspect a
DMG containing an ad-hoc-signed app, follow the [Mac packaging steps](RELEASE.md#macos-disk-images).

The tooling suite also builds real Debian packages when the
[Linux packaging tools](RELEASE.md#linux-packages) are installed. It checks
reproducibility, file permissions, checksums, native hardening, and installer
failure handling. Without those tools, the archive test is skipped. Report
skipped tests separately from passed tests.

GTK tests share one application while giving each test fresh windows and
editor state. Most windows remain hidden; presentation, close, and layout checks
explicitly show theirs. New widget tests should use `create_window` for cleanup
and call `present_window` only when they need a displayed window.
Unhandled GTK callback exceptions fail the test, and cleanup waits for each
window's pending work. The GTK fixture blocks physical keyboard access.
Automated tests use simulated devices and must not access physical keyboards.

Preserve device protocol bytes, timing, failure handling, and user data unless
changing them is the purpose of the work. Keep build output, installers,
credentials, and raw device captures out of source control.

## Releases and licensing

CI checks formatting, tests macOS in release configuration, and builds and checks
installers on macOS and both Linux architectures. Mac jobs select Xcode 16.4;
Python jobs use Python 3.12, including Ubuntu's system Python for its GTK bindings.
Use Python 3.11 or later for local build, release, and test scripts.

See [RELEASE.md](RELEASE.md#preparing-a-release) for packaging and hardware checks.
Contributions use the project's [Apache License, Version 2.0](LICENSE).
