# Code style

Follow these conventions unless language syntax requires otherwise.

- Use four spaces for indentation in all code and configuration files. Use
  spaces rather than tabs, except where the file format requires tabs.
- Keep comma-separated arguments, parameters, conditions, and collection entries
  together on a line; wrap only when needed for readability or the line limit.
  Prefer packed continuation lines over one item per line. Use 120 columns as the
  default limit when a project does not specify one; do not split string literals
  or otherwise change behavior just to meet it.
- Put a space before an opening code-block brace, following standard formatter
  spacing: `if(ready) {`, `struct Device {`, `map { ... }`.
  Binary operators retain their surrounding spaces, including before a brace
  or parenthesis: `callback = { ... }`, `value = (left + right)`.
  Preserve other language-required whitespace, such as `@MainActor () -> Bool`.
- No space between a callable/control-flow keyword and its opening parenthesis:
  `process(item)`, `if(ready)`, `for(...)`, `while(...)`. Do not add parentheses
  merely to apply this rule in a language that does not require them.
- Keep Python control flow idiomatic: `if ready:`, `for item in items:`, and
  `with path.open() as stream:`. Keep the spaces Python syntax requires.
- Leave a blank line after a completed code block or control-flow statement,
  including unbraced `if`/`for`/`while` bodies, when another statement follows.
  Do not add a blank line before an enclosing closing brace, parenthesis, bracket,
  or continuation of the same expression. Keep `else`/`elif`, `catch`/`except`,
  `finally`, and `do`/`while` clauses attached to their compound statement.
- Leave a blank line before a whole-line comment or comment block that follows
  code, so the comment clearly belongs to what follows. Keep consecutive
  comment lines together, and do not put a blank line between a comment and
  the code it explains. No extra blank is needed at the start of a file
  or code block. Preserve syntax-required adjacency and line continuations.
- For C/C++ includes: matching/public interface header first, other project
  headers second, third-party headers third, then standard/system headers. Leave
  one blank line between groups and alphabetize within groups.
- Alphabetize Swift imports. For Python use standard library, third-party, then
  project import groups, separated by blank lines and sorted within each group.
- Attach C/C++ pointer asterisks to the type: `int* value`, not `int *value`.
  Function-pointer declarators retain their required grouping, such as `int (*callback)(void)`.
- Apply style changes without changing behavior. Preserve comments, literal
  contents, protocol bytes/timing, and user edits. Avoid generated/vendor files.

## Formatting this repository

Install Ruff 0.14.4 in a development environment. C formatting requires
clang-format 21.1.8; Swift formatting uses the selected Xcode's swift-format
(Xcode 16.4 in CI).

```sh
python3 scripts/format.py
python3 scripts/format.py --check
```

Pass files or directories to format only that part of the checkout. Only the
formatters needed by those files are required, so Linux-only Python changes do
not need Swift tools:

```sh
python3 scripts/format.py apps/linux tests/linux
python3 scripts/format.py --check apps/linux tests/linux
```

Directory scans skip symbolic links so formatting cannot follow them outside
the checkout.

The script runs the language formatters, then applies the repository's list and
blank-line rules. Use it instead of running a language formatter alone.
It checks that the whitespace pass preserves source tokens or the Python syntax
tree and skips generated files, editor files, and build output.

CSS uses the same blank-line rules. JSON, YAML, and shell files keep their
existing layout; EditorConfig defines indentation and line endings.
