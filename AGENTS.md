# kblights development instructions

Follow [docs/code-style.md](docs/code-style.md). Use four spaces for indentation
in all code and configuration files. In particular, use packed
comma-separated lists, a space before opening block braces, no space before
call/control-flow parentheses, and a blank line after a completed block or
unbraced control-flow statement unless a closing delimiter or continuation follows.
Binary operators keep their surrounding spaces even before an opening brace or
parenthesis (` = {`, ` = (`). Use idiomatic Python control flow without
unnecessary parentheses.
Attach C/C++ pointer asterisks to the type (`int* value`); function-pointer
declarators keep their grouping (`int (*callback)(void)`).

Use `python3 scripts/format.py` to format and `python3 scripts/format.py --check`
to check; do not run a stock formatter alone and leave its output as final style.
Do not change behavior during a formatting-only task. Keep protocol bytes,
delays, failure boundaries, profiles, and resource handling intact.

Verification:

- `swift test -c release` (select a compatible Xcode toolchain if required).
- `python3 scripts/checks/test-core.py`.
- `python3 scripts/checks/test-linux.py` (Python 3.11+).
- `python3 -m unittest discover -s tests/tooling -v`.

Verify transport and protocol changes on supported hardware separately from
offline tests. GTK tests require Linux and `KEYBOARDLIGHTS_GTK_TEST=1`; never
represent skipped tests as passed.
Do not access physical hardware for formatting or offline tests. Do not commit
or publish without the user's instruction. Preserve user edits and editor state.
