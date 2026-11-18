"""Lock readback uses only HIDIOCGOUTPUT and never guesses missing state."""

import io
import json
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

from keyboardlights.device import capture as reader
from keyboardlights.device.lock_indicators import LockIndicators


class LockIndicatorTests(unittest.TestCase):
    def test_latest_reading_uses_time_independently_of_lock_value(self):
        older = LockIndicators(5, sampled_at=1)
        newer = LockIndicators(5, sampled_at=2)
        self.assertEqual(older, newer)
        self.assertIs(LockIndicators.latest(older, newer), newer)
        self.assertIs(LockIndicators.latest(newer, older), newer)
        self.assertIs(LockIndicators.latest(older, None), older)
        self.assertIs(LockIndicators.latest(None, newer), newer)
        self.assertIsNone(LockIndicators.latest(None, None))
        same_time = LockIndicators(2, sampled_at=2)
        self.assertIs(LockIndicators.latest(newer, same_time), same_time)

    def test_decodes_all_combinations_and_preserves_reserved_bits(self):
        for raw in range(256):
            state = LockIndicators(raw)
            self.assertEqual(state.raw_value, raw)
            self.assertEqual(state.num_lock, bool(raw & 1))
            self.assertEqual(state.caps_lock, bool(raw & 2))
            self.assertEqual(state.scroll_lock, bool(raw & 4))

        for raw in (-1, 256, True, None, "5"):
            with self.assertRaises(ValueError):
                LockIndicators(raw)

    def test_exact_output_request_and_logging(self):
        class Log(io.StringIO):
            def fileno(self):
                return 9

        log = Log()
        transport = reader.Transport(9, log)

        def ioctl(fd, request, buffer, mutate):
            self.assertEqual(fd, 9)
            self.assertEqual(request, 0xC002480C)
            self.assertEqual(buffer, bytearray(2))
            self.assertTrue(mutate)
            buffer[1] = 0x85
            return 2

        with (patch.object(reader.fcntl, "ioctl", side_effect=ioctl) as call, patch.object(reader.os, "fsync"),
            patch.object(reader.time, "sleep") as sleep):
            self.assertEqual(transport.read_lock_indicators(), LockIndicators(0x85))
            call.assert_called_once()
            sleep.assert_called_once_with(0.015)

        records = [json.loads(line) for line in log.getvalue().splitlines()]
        self.assertEqual([item["event"] for item in records], ["attempt", "completion"])
        self.assertTrue(all(item["direction"] == "in" and item["report_type"] == "output" for item in records))
        self.assertEqual(records[1]["buffer_hex"], "0085")

    def test_bad_framing_and_unsupported_ioctl_stop_without_retry(self):
        for count, report_id, error in [(0, 0, None), (1, 0, None), (3, 0, None), (2, 1, None),
            (2, 0, OSError("unsupported ioctl"))]:
            transport = reader.Transport(9, None)

            def ioctl(fd, request, buffer, mutate):
                if error is not None:
                    raise error

                buffer[0] = report_id
                return count

            with patch.object(reader.fcntl, "ioctl", side_effect=ioctl) as call:
                with self.assertRaises((OSError, ValueError)):
                    transport.read_lock_indicators()

                call.assert_called_once()

    def test_capture_exposes_lock_snapshot_without_reading_unused_channels(self):
        for channel in (0, 1):
            body = bytes(reader.BLOCKS[channel] * 64)

            def ioctl(fd, request, buffer, mutate):
                self.assertEqual(request, 0xC002480C)
                buffer[1] = 2
                return 2

            with (patch.object(reader, "discover", return_value={"hidraw": "/dev/mock"}),
                patch.object(reader, "open_keyboard", return_value=nullcontext(9)),
                patch.object(reader, "operation_lock", return_value=nullcontext()),
                patch.object(reader.Transport, "read", return_value=(body, body, 0, 0)) as read,
                patch.object(reader.fcntl, "ioctl", side_effect=ioctl), patch.object(reader.time, "sleep")):
                state = reader.capture_state(None, (channel,))

            self.assertEqual([call.args for call in read.call_args_list], [(channel,), (channel,)])
            self.assertEqual(state.images, {channel: body})
            self.assertEqual(state.lock_indicators, LockIndicators(2))
            self.assertEqual(state.metadata["output_report_reads"], 1)

    def test_log_failure_prevents_the_output_request(self):
        transport = reader.Transport(9, io.StringIO())
        with patch.object(reader.fcntl, "ioctl") as ioctl:
            with self.assertRaises(io.UnsupportedOperation):
                transport.read_lock_indicators()

            ioctl.assert_not_called()

    def test_failed_exchange_preserves_device_error_when_logging_also_fails(self):
        for method in ("read_lock_indicators", "transfer"):
            with self.subTest(method=method):
                transport = reader.Transport(9, None)
                original = PermissionError("Device access denied")
                with (patch.object(reader.fcntl, "ioctl", side_effect=original) as ioctl,
                    patch.object(transport, "record", side_effect=[None, OSError("Disk full")])):
                    with self.assertRaises(PermissionError) as caught:
                        getattr(transport, method)()

                self.assertIs(caught.exception, original)
                self.assertTrue(any("Disk full" in note for note in original.__notes__))
                ioctl.assert_called_once()

    def test_capture_preserves_lock_failure_when_final_diagnostic_save_also_fails(self):
        original = PermissionError("Cannot read lock indicators")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "capture"
            body = bytes(640)
            with (patch.object(reader, "discover", return_value={"hidraw": "/dev/mock"}),
                patch.object(reader, "open_keyboard", return_value=nullcontext(9)),
                patch.object(reader, "operation_lock", return_value=nullcontext()),
                patch.object(reader.Transport, "read", return_value=(body, body, 0, 0)),
                patch.object(reader.Transport, "read_lock_indicators", side_effect=original),
                patch.object(Path, "write_text", side_effect=OSError("Disk full"))):
                with self.assertRaises(PermissionError) as caught:
                    reader.capture_state(output, (0,))

            self.assertIs(caught.exception, original)
            self.assertTrue(any("Disk full" in note for note in original.__notes__))

    def test_final_diagnostic_save_failure_without_read_failure_is_not_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "capture"
            body = bytes(640)
            with (patch.object(reader, "discover", return_value={"hidraw": "/dev/mock"}),
                patch.object(reader, "open_keyboard", return_value=nullcontext(9)),
                patch.object(reader, "operation_lock", return_value=nullcontext()),
                patch.object(reader.Transport, "read", return_value=(body, body, 0, 0)),
                patch.object(reader.Transport, "read_lock_indicators", return_value=LockIndicators(5)),
                patch.object(Path, "write_text", side_effect=OSError("Disk full"))):
                with self.assertRaisesRegex(OSError, "Disk full"):
                    reader.capture_state(output, (0,))

    def test_unused_configuration_channels_are_rejected_before_discovery(self):
        with patch.object(reader, "discover") as discover:
            for channels in ((2,), (3,), (0, 2), (True,)):
                with self.assertRaises(ValueError):
                    reader.capture_state(None, channels)

            discover.assert_not_called()
