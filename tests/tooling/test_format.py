"""Formatting must preserve syntax, literals, and required language spacing."""

import ast
import importlib.util
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("project_format",
    Path(__file__).resolve().parents[2] / "scripts/format.py")
style = importlib.util.module_from_spec(spec)
spec.loader.exec_module(style)


class FormatSafetyTests(unittest.TestCase):
    def test_directory_scan_prunes_excluded_build_and_tool_trees(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "app.py").write_text("value = 1\n")
            for name in (".build", ".tools", ".venv"):
                directory = root / name / "nested"
                directory.mkdir(parents=True)
                (directory / "App.swift").write_text("let value = true\n")

            visited = []
            walk = style.os.walk

            def tracked_walk(*args, **kwargs):
                for directory, children, filenames in walk(*args, **kwargs):
                    visited.append(Path(directory))
                    yield directory, children, filenames

            with (patch.object(style, "ROOT", root), patch("sys.argv", ["format.py", "--check", str(root)]),
                patch.object(style.os, "walk", side_effect=tracked_walk),
                patch.object(style, "executable", return_value="ruff") as executable,
                patch.object(style, "run", side_effect=lambda command, text: text), redirect_stdout(io.StringIO())):
                self.assertEqual(style.main(), 0)
                executable.assert_called_once_with("ruff", "RUFF")

            self.assertEqual(visited, [root])

    def test_directory_formatting_does_not_follow_file_symlinks_outside_the_checkout(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()
            root = directory / "checkout"
            root.mkdir()
            source = "if ready:\n    work()\nfinish()\n"
            outside = directory / "outside.py"
            outside.write_text(source)
            (root / "linked.py").symlink_to(outside)
            inside = root / "inside.py"
            inside.write_text(source)
            with (patch.object(style, "ROOT", root), patch("sys.argv", ["format.py", str(root)]),
                patch.object(style, "executable", return_value="ruff"),
                patch.object(style, "run", side_effect=lambda command, text: text), redirect_stdout(io.StringIO())):
                self.assertEqual(style.main(), 0)

            self.assertEqual(outside.read_text(), source)
            self.assertEqual(inside.read_text(), "if ready:\n    work()\n\nfinish()\n")

    def test_selected_python_paths_do_not_require_swift_or_c_tools(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            directory = root / "linux"
            directory.mkdir()
            python = directory / "app.py"
            swift = root / "App.swift"
            source = "if ready:\n    work()\nfinish()\n"
            python.write_text(source)
            swift.write_text("let untouched = true\n")
            with (patch.object(style, "ROOT", root),
                patch.object(style, "executable", return_value="ruff") as executable,
                patch.object(style, "run", side_effect=lambda command, text: text), redirect_stdout(io.StringIO())):
                with patch("sys.argv", ["format.py", "--check", str(directory)]):
                    self.assertEqual(style.main(), 1)

                self.assertEqual(python.read_text(), source)
                with patch("sys.argv", ["format.py", str(python), str(python)]):
                    self.assertEqual(style.main(), 0)

                self.assertEqual(python.read_text(), "if ready:\n    work()\n\nfinish()\n")
                with patch("sys.argv", ["format.py", "--check", str(python)]):
                    self.assertEqual(style.main(), 0)

                self.assertTrue(all(call.args == ("ruff", "RUFF") for call in executable.call_args_list))

            self.assertEqual(swift.read_text(), "let untouched = true\n")

    def test_missing_formatter_stops_before_changing_any_selected_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            python = root / "app.py"
            source = "if ready:\n    work()\nfinish()\n"
            python.write_text(source)
            (root / "App.swift").write_text("let value = true\n")
            with (patch.object(style, "ROOT", root), patch("sys.argv", ["format.py"]),
                patch.object(style, "executable", side_effect=["ruff", RuntimeError("Missing swift-format")]),
                patch.object(style, "run") as run):
                with self.assertRaisesRegex(RuntimeError, "Missing swift-format"):
                    style.main()

                run.assert_not_called()

            self.assertEqual(python.read_text(), source)

    def test_c_literal_and_operator_spacing_are_preserved(self):
        source = 'int values[] = {1, 2};\nif(ready) {\n  use("if (x) {keep spaces}");\n}\nfinish();\n'
        result = style.blank_after_blocks(source)
        self.assertIn("int values[] = {1, 2};", result)
        self.assertIn("if(ready) {", result)
        self.assertIn('"if (x) {keep spaces}"', result)
        self.assertIn("}\n\nfinish();", result)
        self.assertEqual(style.blank_after_blocks(result), result)

    def test_swift_attributes_interpolation_and_closure_operators_are_preserved(self):
        literal = r'"\(format("if (x) {keep spaces}"))"'
        source = (
            "let text = "
            + literal
            + "\nlet callback: @MainActor () -> Bool = { true }\nlet next = optional ?? { work() }\n"
        )
        result = style.blank_after_blocks(source, swift=True)
        self.assertIn(literal, result)
        self.assertIn("@MainActor () -> Bool = {", result)
        self.assertIn("optional ?? {", result)
        self.assertIn("URL? {", style.blank_after_blocks("var evidence: URL? { nil }\n", swift=True))
        self.assertEqual(style.blank_after_blocks(result, swift=True), result)

    def test_python_packing_preserves_values_and_singleton_tuples(self):
        source = 'items = [\n    "a  b",\n    "c",\n]\nsingle = (\n    1,\n)\nif ready:\n    run(items)\nfinish()\n'
        result = style.python_style(source)
        self.assertEqual(ast.dump(ast.parse(source)), ast.dump(ast.parse(result)))
        self.assertIn('["a  b", "c"]', result)
        self.assertIn("(1,)", result)
        self.assertIn("if ready:", result)
        self.assertIn("run(items)\n\nfinish()", result)
        self.assertEqual(style.python_style(result), result)

    def test_multiline_literal_contents_and_comments_are_not_compacted(self):
        source = 'items = [\n    """one\n    two""",\n    # A comment attached to the next element.\n    "three",\n]\n'
        result = style.python_style(source)
        self.assertEqual(result, source.replace("    # A comment", "\n    # A comment"))
        self.assertEqual(style.python_style(result), result)

    def test_python_comment_blocks_are_separated_without_changing_literals(self):
        source = (
            '# Header\nvalue = "# literal"\n# Explain the next statement.\n# Keep this block together.\n'
            "next_value = 2\nif ready:\n    # First statement.\n    work()\n"
            'text = """literal\n# This is literal content.\nend"""\n'
        )
        result = style.python_style(source)
        self.assertIn('value = "# literal"\n\n# Explain', result)
        self.assertIn("statement.\n# Keep", result)
        self.assertIn("if ready:\n    # First", result)
        self.assertIn('"""literal\n# This is literal content.\nend"""', result)
        self.assertEqual(style.python_style(result), result)

    def test_brace_language_comment_blocks_are_separated(self):
        source = (
            "// Header\nvoid work() {\n    // First statement.\n    run();\n"
            "    /* Explain the next statement.\n     * Keep this block together. */\n"
            "    finish();\n    // Another explanation.\n    done();\n}\n"
        )
        for options in ({"c": True}, {"swift": True}, {}):
            result = style.blank_after_blocks(source, **options)
            self.assertIn("work() {\n    // First", result)
            self.assertIn("run();\n\n    /* Explain", result)
            self.assertIn("finish();\n\n    // Another", result)
            self.assertEqual(style.blank_after_blocks(result, **options), result)

    def test_comments_preserve_explicit_line_continuations(self):
        source = "#define WORK() \\\n    /* Keep the macro intact. */ \\\n    work()\n"
        self.assertEqual(style.blank_after_blocks(source, c=True), source)

    def test_python_dedent_keeps_comments_attached_to_following_statement(self):
        source = (
            "def save(values):\n    if busy:\n        return\n"
            "    # Capture a snapshot.\n    # Include nested data.\n    values = copy(values)\n"
        )
        expected = source.replace("        return\n", "        return\n\n")
        result = style.python_style(source)
        self.assertEqual(result, expected)
        self.assertEqual(style.python_style(result), result)

    def test_unbraced_if_and_for_are_separated_from_following_statements(self):
        source = (
            "if(!ready)\n    return 0;\n"
            "for(size_t i = 0; i < size; ++i)\n    checksum += body[i];\n"
            "uint8_t setup[64] = {4, 0x13};\n"
        )
        result = style.blank_after_blocks(source, c=True)
        self.assertIn("return 0;\n\nfor(", result)
        self.assertIn("checksum += body[i];\n\nuint8_t", result)
        self.assertNotIn("i = 0;\n", result)
        self.assertEqual(style.blank_after_blocks(result, c=True), result)

    def test_unbraced_nested_else_and_do_while_stay_attached(self):
        source = (
            "if(outer)\n    if(inner)\n        work();\n    else\n        other();\n"
            "finish();\ndo\n    if(ready)\n        work();\nwhile(again);\nfinish();\n"
        )
        result = style.blank_after_blocks(source, c=True)
        self.assertIn("work();\n    else", result)
        self.assertIn("other();\n\nfinish();", result)
        self.assertIn("work();\nwhile(again);", result)
        self.assertIn("while(again);\n\nfinish();", result)

    def test_independent_while_gets_a_blank_but_enclosing_brace_does_not(self):
        source = "if(ready)\n    work();\nwhile(again)\n    advance();\n}\n"
        result = style.blank_after_blocks(source, c=True)
        self.assertIn("work();\n\nwhile(again)", result)
        self.assertIn("advance();\n}", result)
        self.assertNotIn("advance();\n\n}", result)

    def test_unbraced_formatting_preserves_preprocessor_and_literal_contents(self):
        source = (
            "#define GUARD(x) \\\n    if(x) \\\n        work();\n"
            'if(ready)\n    use("if(x); for(;;); { literal }");\n'
            "// Describe the next statement.\nfinish();\n"
        )
        result = style.blank_after_blocks(source, c=True)
        self.assertIn("#define GUARD(x) \\\n    if(x) \\\n        work();\n", result)
        self.assertIn('"if(x); for(;;); { literal }"', result)
        self.assertIn(");\n\n// Describe", result)
