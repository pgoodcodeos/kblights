"""Bounded local/remote stream handling without a display or device."""

import io
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from keyboardlights.lighting.editor import MAX_DRAFT_BYTES
from keyboardlights.profiles.io import read_draft_file


class FragmentedStream:
    def __init__(self, content, fail=False):
        self.data = io.BytesIO(content)
        self.closed = False
        self.fail = fail

    def read_bytes(self, count, _):
        if self.fail:
            raise OSError("interrupted draft read")

        chunk = self.data.read(min(count, 17))
        return SimpleNamespace(get_data=lambda: chunk)

    def close(self, _):
        self.closed = True


class DraftIOTests(unittest.TestCase):
    def test_fragmented_utf8_file_is_read_completely_and_closed(self):
        text = '{"notes": "' + "Color ☀ " * 20 + '"}'
        stream = FragmentedStream(text.encode())
        self.assertEqual(read_draft_file(SimpleNamespace(read=lambda _: stream)), text)
        self.assertTrue(stream.closed)

    def test_oversized_input_is_bounded_and_closed(self):
        stream = FragmentedStream(b"x" * (MAX_DRAFT_BYTES + 5000))
        with self.assertRaises(ValueError):
            read_draft_file(SimpleNamespace(read=lambda _: stream))

        self.assertEqual(stream.data.tell(), MAX_DRAFT_BYTES + 1)
        self.assertTrue(stream.closed)

    def test_failed_input_stream_is_closed(self):
        stream = FragmentedStream(b"", fail=True)
        with self.assertRaises(OSError):
            read_draft_file(SimpleNamespace(read=lambda _: stream))

        self.assertTrue(stream.closed)

    def test_local_special_files_are_rejected_without_opening_a_gio_stream(self):
        with tempfile.TemporaryDirectory() as directory:
            fifo = Path(directory) / "profile.json"
            os.mkfifo(fifo)

            def unexpected_stream(_):
                raise AssertionError("Local profiles must not open a potentially blocking GIO stream")

            for path in (fifo, Path(directory)):
                file = SimpleNamespace(get_path=lambda path=path: str(path), read=unexpected_stream)
                with self.subTest(path=path), self.assertRaisesRegex(ValueError, "regular file"):
                    read_draft_file(file)

    def test_local_regular_file_and_symlink_are_read_with_a_byte_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            path.write_text('{"name": "Desk ☀"}')
            link = path.with_name("link.json")
            link.symlink_to(path)
            for source in (path, link):
                file = SimpleNamespace(get_path=lambda source=source: str(source))
                with self.subTest(source=source):
                    self.assertEqual(read_draft_file(file), path.read_text())

            path.write_bytes(b"x" * (MAX_DRAFT_BYTES + 1))
            with self.assertRaises(ValueError):
                read_draft_file(SimpleNamespace(get_path=lambda: str(path)))


if __name__ == "__main__":
    unittest.main()
