"""Apply the repository style without rewriting comments or literal contents."""

import argparse
import ast
import io
import os
import re
import shutil
import subprocess
import tokenize
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {".build", ".git", ".tools", ".venv", "__pycache__", "build", "dist"}
SUFFIXES = {".c", ".h", ".swift", ".py", ".css"}
LIMIT = 120


class Token(NamedTuple):
    start: int
    end: int
    value: str
    kind: str


def quoted_end(source, start, swift):
    match = re.match(r'(#+)?("""|"|\')', source[start:])
    hashes, quote = match.group(1) or "", match.group(2)
    position = start + len(match.group())
    closing = quote + hashes
    escape = "\\" + hashes
    while position < len(source):
        if source.startswith(closing, position):
            return position + len(closing)

        if source.startswith(escape, position):
            position += len(escape)
            if swift and source.startswith("(", position):
                position = interpolation_end(source, position + 1)
            else:
                position += 1
        else:
            position += 1

    raise ValueError("Unterminated literal")


def interpolation_end(source, position):
    depth = 1
    while position < len(source):
        match = re.match(r'#+"|"', source[position:])
        if match:
            position = quoted_end(source, position, True)
        elif source[position] == "(":
            depth += 1
            position += 1
        elif source[position] == ")":
            depth -= 1
            position += 1
            if not depth:
                return position
        else:
            position += 1

    raise ValueError("Unterminated string interpolation")


def brace_tokens(source, swift=False):
    tokens = []
    position = 0
    while position < len(source):
        start = position
        if source[position].isspace():
            position += 1
            continue

        if source.startswith("//", position):
            position = source.find("\n", position)
            if position < 0:
                position = len(source)

            kind = "comment"
        elif source.startswith("/*", position):
            position += 2
            depth = 1
            while depth and position < len(source):
                if swift and source.startswith("/*", position):
                    depth += 1
                    position += 2
                elif source.startswith("*/", position):
                    depth -= 1
                    position += 2
                else:
                    position += 1

            if depth:
                raise ValueError("Unterminated comment")

            kind = "comment"
        elif source[position] in "\"'" or (swift and re.match(r'#+"', source[position:])):
            position = quoted_end(source, position, swift)
            kind = "literal"
        elif source[position].isalnum() or source[position] == "_":
            position += 1
            while position < len(source) and (source[position].isalnum() or source[position] == "_"):
                position += 1

            kind = "word"
        else:
            position += 1
            kind = "punctuation"

        tokens.append(Token(start, position, source[start:position], kind))

    return tokens


def c_statement_ends(code, source):
    """Locate complete C control statements, including nested unbraced bodies."""
    pairs, stack = {}, []
    for index, token in enumerate(code):
        if token.value in ("(", "[", "{"):
            stack.append(index)
        elif token.value in (")", "]", "}") and stack:
            opening = stack.pop()
            if (code[opening].value, token.value) in (("(", ")"), ("[", "]"), ("{", "}")):
                pairs[opening] = index

    completed, continuations, cached = set(), set(), {}

    def statement_end(start):
        if start >= len(code):
            return None

        if start in cached:
            return cached[start]

        word = code[start].value
        end = None
        if word == "{":
            if start in pairs:
                end = pairs[start] + 1
        elif word in ("if", "for", "while", "switch"):
            header = start + 1
            if header in pairs and code[header].value == "(":
                end = statement_end(pairs[header] + 1)
                if word == "if" and end is not None and end < len(code) and code[end].value == "else":
                    end = statement_end(end + 1)
        elif word == "do":
            tail = statement_end(start + 1)
            if tail is not None and tail < len(code) and code[tail].value == "while":
                header = tail + 1
                if header in pairs:
                    semicolon = pairs[header] + 1
                    if semicolon < len(code) and code[semicolon].value == ";":
                        continuations.add(tail)
                        end = semicolon + 1
        else:
            position = start
            while position < len(code):
                value = code[position].value
                if value == ";":
                    end = position + 1
                    break

                if value in ("}", "#"):
                    break

                if value in ("(", "[", "{"):
                    if position not in pairs:
                        break

                    position = pairs[position]

                position += 1

        cached[start] = end
        return end

    # Ignore preprocessor directives, including backslash-continued macro bodies.

    directives, offset, continued = [], 0, False
    for line in source.splitlines(keepends=True):
        if continued or line.lstrip().startswith("#"):
            directives.append((offset, offset + len(line)))
            continued = line.rstrip().endswith("\\")

        offset += len(line)

    for index, token in enumerate(code):
        if token.value not in ("if", "for", "while", "switch", "do"):
            continue

        if any(start <= token.start < end for start, end in directives):
            continue

        end = statement_end(index)
        if end is not None:
            completed.add(end - 1)

    return completed, continuations


def blank_before_comments(source, comments, *, python=False):
    """Separate whole-line comment blocks without touching literals or continuations."""
    lines = source.splitlines(keepends=True)
    offsets, position = {}, 0
    for row, line in enumerate(lines):
        offsets[position] = row
        position += len(line)

    comment_rows, starts = set(), []
    for start, end in comments:
        line_start = source.rfind("\n", 0, start) + 1
        line_end = source.find("\n", end)
        if line_end < 0:
            line_end = len(source)

        if source[line_start:start].strip() or source[end:line_end].strip():
            continue

        row = offsets[line_start]
        starts.append(row)
        comment_rows.update(range(row, row + source[start:end].count("\n") + 1))

    insertions = set()
    for row in starts:
        if not row or row - 1 in comment_rows or not lines[row - 1].strip():
            continue

        previous = lines[row - 1].rstrip()
        indentation = len(lines[row]) - len(lines[row].lstrip())
        previous_indent = len(previous) - len(previous.lstrip())
        if previous.endswith(("\\", "{", "(", "[")) or (python and previous.endswith(":")):
            continue

        if indentation > previous_indent:
            continue

        insertions.add(row)

    for row in sorted(insertions, reverse=True):
        lines.insert(row, "\n")

    return "".join(lines)


def blank_after_blocks(source, swift=False, c=False):
    before = brace_tokens(source, swift)
    tokens = before
    code = [token for token in tokens if token.kind != "comment"]
    completed, continuations = c_statement_ends(code, source) if c else (set(), set())
    insertions = set()
    for index, token in enumerate(code):
        if (token.value != "}" and index not in completed) or index + 1 == len(code):
            continue

        following = index + 1
        if code[following].value == ";":
            following += 1

        if following == len(code):
            continue

        next_token = code[following]
        line_end = source.find("\n", token.end)
        if line_end < 0 or next_token.start <= line_end:
            continue

        if next_token.value in ("}", ")", "]", ",", ";", ".", "else", "catch"):
            continue

        if following in continuations or (not c and next_token.value == "while"):
            continue

        next_line = source[line_end + 1 :].split("\n", 1)[0]
        if next_line.lstrip().startswith("#endif") or "\n\n" in source[token.end : next_token.start]:
            continue

        if not next_line.strip():
            continue

        insertions.add(line_end + 1)

    for position in sorted(insertions, reverse=True):
        source = source[:position] + "\n" + source[position:]

    comments = [(token.start, token.end) for token in brace_tokens(source, swift) if token.kind == "comment"]
    source = blank_before_comments(source, comments)
    after = brace_tokens(source, swift)
    if [(t.kind, t.value) for t in before] != [(t.kind, t.value) for t in after]:
        raise ValueError("Whitespace pass changed source tokens")

    return source


def python_tokens(source):
    offsets = [0]
    for line in source.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))

    tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))

    def offset(position):
        row, column = position
        return offsets[min(row - 1, len(offsets) - 1)] + column

    return tokens, offset


def packed_python(source):
    # Reparse after edits so offsets always refer to current source.
    for _ in range(2000):
        tokens, offset = python_tokens(source)
        stack, groups = [], []
        for index, token in enumerate(tokens):
            if token.type == tokenize.OP and token.string in ("(", "[", "{"):
                stack.append(index)
            elif token.type == tokenize.OP and token.string in (")", "]", "}") and stack:
                groups.append((stack.pop(), index))

        changed = False
        for begin, end in groups:
            group = tokens[begin : end + 1]
            if group[0].start[0] == group[-1].end[0]:
                continue

            if any(
                token.type == tokenize.COMMENT or (token.type == tokenize.STRING and "\n" in token.string)
                for token in group
            ):
                continue

            depth, separators = 0, []
            for token in group:
                if token.type == tokenize.OP:
                    if token.string in ("(", "[", "{"):
                        depth += 1
                    elif token.string in (")", "]", "}"):
                        depth -= 1
                    elif token.string == "," and depth == 1:
                        separators.append(offset(token.end))

            if not separators:
                continue

            start, finish = offset(group[0].start), offset(group[-1].end)

            # Collapse only gaps between tokens, never whitespace inside a literal.
            def flatten(left, right):
                value, previous = "", left
                for token in group:
                    if token.type in (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT):
                        continue

                    token_start, token_end = offset(token.start), offset(token.end)
                    if token_start < left or token_end > right:
                        continue

                    gap = source[previous:token_start]
                    value += (" " if "\n" in gap else gap) + token.string
                    previous = token_end

                return value.strip()

            boundaries = [offset(group[0].end)] + separators + [offset(group[-1].start)]
            items = [flatten(left, right) for left, right in zip(boundaries, boundaries[1:])]
            items = [item for item in items if item]
            if items and (group[0].string != "(" or len(items) > 1):
                items[-1] = items[-1].removesuffix(",")

            line_start = source.rfind("\n", 0, start) + 1
            prefix = source[line_start:start]
            indent = " " * (len(prefix) - len(prefix.lstrip()) + 4)
            suffix = source[finish:].split("\n", 1)[0]
            flat = group[0].string + " ".join(items) + group[-1].string
            if len(prefix + flat + suffix) <= LIMIT:
                replacement = flat
            else:
                if any(len(indent + item) > LIMIT for item in items):
                    continue

                lines = [prefix + group[0].string]
                for item in items:
                    separator = "" if lines[-1].endswith(group[0].string) else " "
                    if len(lines[-1] + separator + item) <= LIMIT:
                        lines[-1] += separator + item
                    else:
                        lines.append(indent + item)

                if len(lines[-1] + group[-1].string + suffix) <= LIMIT:
                    lines[-1] += group[-1].string
                else:
                    lines.append(" " * (len(prefix) - len(prefix.lstrip())) + group[-1].string)

                replacement = "\n".join(lines)[len(prefix) :]

            if replacement != source[start:finish]:
                source = source[:start] + replacement + source[finish:]
                changed = True
                break

        if not changed:
            return source

    raise ValueError("List formatting did not converge")


def python_style(source):
    before = ast.dump(ast.parse(source), include_attributes=False)
    source = packed_python(source)
    tokens, _ = python_tokens(source)
    lines = source.splitlines(keepends=True)
    comment_rows = {token.start[0] - 1: token.start[1] for token in tokens if token.type == tokenize.COMMENT}
    rows = set()
    for token in tokens:
        if token.type != tokenize.DEDENT or token.start[0] > len(lines):
            continue

        row = token.start[0] - 1
        if not row or not lines[row - 1].strip():
            continue

        if re.match(r"(?:elif|else|except|finally|case)\b", lines[row].lstrip()):
            continue

        # Keep leading comments attached to the statement after a completed block.
        indentation = len(lines[row]) - len(lines[row].lstrip())
        while row and comment_rows.get(row - 1) == indentation:
            row -= 1

        if not row or not lines[row - 1].strip():
            continue

        rows.add(row)

    for row in sorted(rows, reverse=True):
        lines.insert(row, "\n")

    source = "".join(lines)
    tokens, offset = python_tokens(source)
    comments = [(offset(token.start), offset(token.end)) for token in tokens if token.type == tokenize.COMMENT]
    source = blank_before_comments(source, comments, python=True)
    if ast.dump(ast.parse(source), include_attributes=False) != before:
        raise ValueError("Whitespace pass changed Python syntax tree")

    return source


def executable(name, override):
    path = os.environ.get(override) or shutil.which(name)
    if not path and shutil.which("xcrun"):
        result = subprocess.run(["xcrun", "--find", name], capture_output=True, text=True)
        if result.returncode == 0:
            path = result.stdout.strip()

    if not path:
        raise RuntimeError(f"Missing {name}; see docs/code-style.md")

    return path


def run(command, source):
    result = subprocess.run(command, input=source, text=True, capture_output=True, cwd=ROOT)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"Formatter failed: {command[0]}")

    return result.stdout


def source_paths(targets):
    paths = set()
    for target in targets:
        if any(part in EXCLUDED for part in target.relative_to(ROOT).parts):
            continue

        if target.is_dir():
            for directory, children, filenames in os.walk(target, followlinks=False):
                children[:] = [name for name in children if name not in EXCLUDED]
                paths.update(Path(directory) / name for name in filenames if Path(name).suffix in SUFFIXES)
        else:
            paths.add(target)

    return sorted(path for path in paths if path.suffix in SUFFIXES and not path.is_symlink() and path.is_file())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("paths", nargs="*", type=Path, help="files or directories to format (default: entire checkout)")
    args = parser.parse_args()
    targets = [path.resolve() for path in args.paths] or [ROOT]
    for target in targets:
        if not target.is_relative_to(ROOT) or not target.exists():
            parser.error(f"Expected an existing file or directory inside the checkout: {target}")

    tool_names = {".c": ("clang-format", "CLANG_FORMAT"), ".swift": ("swift-format", "SWIFT_FORMAT"),
        ".py": ("ruff", "RUFF")}
    paths = source_paths(targets)
    suffixes = {".c" if path.suffix == ".h" else path.suffix for path in paths}
    tools = {suffix: executable(*tool_names[suffix]) for suffix in sorted(suffixes & tool_names.keys())}
    changed, count = [], 0
    for path in paths:
        original = path.read_text()
        if path.suffix in (".c", ".h"):
            base = run([tools[".c"], f"--assume-filename={path}"], original)
            formatted = blank_after_blocks(base, c=True)
        elif path.suffix == ".swift":
            base = run([tools[".swift"], "format", "--configuration", str(ROOT / ".swift-format"), "-"], original)
            formatted = blank_after_blocks(base, swift=True)
        elif path.suffix == ".py":
            base = run([tools[".py"], "check", "--select", "I", "--fix", "--stdin-filename", str(path), "-"], original)
            base = run([tools[".py"], "format", "--stdin-filename", str(path), "-"], base)
            formatted = python_style(base)
        else:
            formatted = blank_after_blocks(original)

        count += 1
        if formatted != original:
            changed.append(str(path.relative_to(ROOT)))
            if not args.check:
                if path.read_text() != original:
                    raise RuntimeError(f"File changed while formatting: {path}")

                path.write_text(formatted)

    if args.check and changed:
        print("Style changes required:\n" + "\n".join(changed))
        return 1

    print(f"{'Checked' if args.check else 'Formatted'} {count} source files; {len(changed)} changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
