# Architecture

KeyboardLights has a Swift macOS app, a Python/GTK Linux app, and a shared C
backend for validating and applying keyboard configuration.

| Directory | Purpose |
|---|---|
| `apps/macos/` | SwiftUI app, device transport, and profile storage |
| `apps/linux/keyboardlights/` | GTK app, device transport, and profile storage |
| `core/` | Configuration validation and device transaction logic |
| `data/c989/` | Shared keyboard layout and assignment catalog |
| `tests/` | Backend, app, and packaging tests |
| `scripts/` | Build, formatting, test, and release tools |

## Shared backend

Both apps compile `core/Core.c` and call the interface in `core/include/C989Native.h`.
It validates lighting and assignments, prepares configuration buffers, selects
checksum-matching read reports, and coordinates uploads.

The apps provide callbacks for sending, receiving, delays, and transaction logs.
Callbacks run synchronously; their context and buffers must remain valid until
the call returns. Send and journal callbacks return nonzero on success. Receive
returns the exact byte count or a negative error value. Swift and Python catch
callback errors and stop the transaction.

Writes use a single upload attempt, checksum verification, and one activation
report. Uploads and activation are never retried. Setup delays are 105 ms for
lighting and 70 ms for assignments. `C989WriteResult` records where a write
stopped and whether activation may have reached the keyboard.

## Keyboard display

Both apps use the `display` geometry in `data/c989/layout.json` for lighting and
assignment previews. Horizontal positions and widths use quarter-key units;
vertical positions and heights use key rows. This describes staggered typing
rows, the split, macro columns, navigation cluster, and number pad independently
of firmware matrix coordinates. Device indices still select the same LED slots
and assignment records. Geometry changes belong in this shared resource so both
platforms remain aligned.

The shared `lighting-colors.json` palette gives both apps identical presets and
exact RGB matching. Unpainted keys are editor-only markers, separate from Off;
review and export reject incomplete static drafts. Clear and Fill never modify
reserved slots or access the device. Profile formats and transaction policies
remain unchanged. The brush and bucket keep separate last-used non-Off colors in OS-specific
preferences, separate from portable drafts and diagnostic evidence. Mode switching preserves
the local static layout and brightness. Inactive paint edits and blank markers
still prevent silent replacement during refreshes.
Linux reads color history nonblocking and accepts only regular files.

## macOS

Build with `sh scripts/build-macos.sh`, then launch with `sh scripts/run-macos.sh`.

The Keyboard workspace has lighting controls above the physical keyboard and
editable key details below it. `AssignmentKeyboard.swift` draws the case, keycaps,
lighting edges, and selection markers. It uses `PhysicalKeyboardLayout.swift` and
the shared layout data; styling does not change key positions or device indices.
Press feedback keeps each key's hit target stationary. Accessibility descriptions
include selection, planned assignments, lighting colors, Off, and unpainted states.

`KeyboardAppSession.swift` coordinates startup and refreshes for each window.
It checks the connection and permission before loading settings. Services are
injectable so tests can use simulated devices without requesting keyboard access.
Lighting and assignments use independent captures of channels 1 and 0 respectively.
Pending edits keep their original baseline; assignment captures never replace the
lighting preview. Both captures include the separate lock indicator byte.

`KeyboardChangesWorkflow` combines lighting and assignment reviews and reserves
shared device activity while applying them. Each writer keeps its own preflight,
activation, and readback checks for its own channel. No unrelated channels are
recaptured after a write. Failure stops the sequence and reports partial completion.
`KeyboardTransport.swift` handles IOUSBHost and IOHID requests;
`NativeKeyboardBackend.swift` serializes operations and compares settings before
and after a write. Services run device operations on worker tasks.

Profiles are individual JSON files managed from **File → Lighting Profile** and
**File → Assignment Profile**. `KeyboardProfileCommands.swift` routes commands to
the focused window. `KeyboardProfileWorkflow.swift` owns that window's current
files, active profile type, and pending actions. File dialogs and confirmations
block overlapping profile commands. Confirmed actions recheck availability before
changing drafts or files.

`KeyboardProfileDocument.swift` coordinates Save and Trash operations against the
bytes last opened or saved. External changes block replacement or deletion.
Save As uses the in-memory draft even if the source is missing or damaged.
Combined files preserve the other saved section. A save advances another section's
file revision only when its stored settings are unchanged. Compatible Linux
lighting files retain their names and identifiers; unnamed older drafts remain
readable. Readers use bounded, nonblocking reads of regular files and reject
file symlinks.

`KeyboardProfileFiles.swift` discovers local and remembered files and reuses
unchanged decoded profiles. It migrates the old library once, keeps the original
for recovery, and does not recreate deleted profiles.

`KeyboardPermission.swift` requests Input Monitoring access before opening System
Settings and rechecks access when the app becomes active. Setup stays in the main
window, with a Finder shortcut for adding the running app manually.
`KeyboardTermination.swift` accepts idle quit requests immediately and waits for
an active operation or reserved batch to finish. The activity gate prevents new
work while quitting.

Drafts and captured settings stay in memory. `DeviceEvidence.swift` saves logs
only when diagnostic logging is enabled. With logging off, validation creates no
capture directories or files. With logging on, a logging failure stops the
operation. Uncertain updates block further writes whether or not logs were saved.

For build and installer validation, see [Mac packaging](../RELEASE.md#macos-disk-images).
For app usage, see the [macOS guide](macos.md).

## Linux

`device/core.py` loads `lighting_core.so` through ctypes. `device/capture.py`
handles reads, while the lighting and assignment services prepare and apply
changes. GUI services default to in-memory operation; `--diagnostic-logging`
opts into saving capture and transaction files. The standalone capture CLI requires
an explicit output directory and saves diagnostic records. Assignment buffers
are also checked against an independent Python encoder. Startup checks system
device metadata before opening the keyboard, then loads lighting and assignments.

The GTK workspace has lighting controls above one fixed keyboard and a two-column
key inspector below it. Connection status and the combined review action sit in
the footer; Refresh sits at the right of the menu bar. Brush and bucket palettes
keep separate colors and histories. The pointer selects keys without painting.

`ui/profiles.py` provides the File menu's Lighting Profile and Assignment Profile
actions using native file dialogs. New starts a draft; Open stages a file;
Save updates the current file or asks for a filename. Save As changes the current
file, and Delete current confirms deletion while keeping the draft. Reads have
size limits, and saves use atomic replacement with etag checks. External changes
block a save and leave the draft intact. Older lighting draft files remain readable.

`lighting/changes.py` combines reviews and runs the assignment and lighting writers
in order under one operation lock. Only that worker may reenter the lock for the
individual steps. Each writer verifies only its own channel. A failure stops the
sequence and reports completed work. Device updates are sequential, not atomic. Device work
runs in a background thread; verified settings return to the UI for display
and editing.

Build the shared library with `sh scripts/build-linux.sh`, then launch the app
with `sh scripts/run-linux.sh`. The Linux test runner builds the library automatically.
The [Linux guide](linux.md) covers dependencies, device access, and usage.

See [Contributing](../CONTRIBUTING.md#tests) for tests and
[release preparation](../RELEASE.md#preparing-a-release) for hardware checks.
