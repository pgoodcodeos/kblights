"""Offline Linux protocol, failure-boundary, and capture-viewer tests."""

import io
import json
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

import keyboardlights.device.capture as reader


def reply(code, checksum=0):
    packet = bytearray(reader.command(code))
    packet[3] = 1
    packet[4:8] = checksum.to_bytes(4, "little")
    return bytes(packet)


class ScriptedTransport(reader.Transport):
    def __init__(self, replies):
        self.replies = iter(replies)
        self.sent = []

    def transfer(self, payload=None):
        if payload is None:
            return next(self.replies)

        self.sent.append(payload)
        if payload not in reader.ALLOWED:
            raise AssertionError("Unsafe packet")

        return payload


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(reader.Transport, "read_lock_indicators", return_value=reader.LockIndicators(5)))

    def test_missing_or_incompatible_core_stops_before_capture_side_effects(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "capture"
            with (patch.object(reader, "native_library", side_effect=ValueError("incompatible core")),
                patch.object(reader, "discover") as discover, patch.object(reader, "operation_lock") as lock,
                patch.object(reader, "open_keyboard") as opened):
                with self.assertRaisesRegex(ValueError, "incompatible core"):
                    reader.capture_state(output, (1,))

                discover.assert_not_called()
                lock.assert_not_called()
                opened.assert_not_called()
                self.assertFalse(output.exists())

    def test_capture_returns_paired_verified_bytes_directly(self):
        body = bytes([7]) + bytes(1151)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "capture"
            with (patch.object(reader, "discover", return_value={"hidraw": "/dev/mock"}),
                patch.object(reader, "open_keyboard", return_value=nullcontext(9)),
                patch.object(reader.Transport, "read", return_value=(body, body + bytes(64), 7, 0)) as read,
                patch.object(Path, "read_bytes", side_effect=AssertionError("No evidence reload"))):
                state = reader.capture_state(output, (1,))

            self.assertEqual(read.call_count, 2)
            self.assertEqual(state.images, {1: body})
            self.assertTrue(state.metadata["paired_reads_match"])
            self.assertEqual(json.loads((output / "result.json").read_text()), state.metadata)

    def test_capture_without_logging_keeps_paired_verified_data_in_memory(self):
        body = bytes([7]) + bytes(1151)
        with (patch.object(reader, "discover", return_value={"hidraw": "/dev/mock"}),
            patch.object(reader, "open_keyboard", return_value=nullcontext(9)),
            patch.object(reader, "operation_lock", return_value=nullcontext()),
            patch.object(reader.Transport, "read", return_value=(body, body + bytes(64), 7, 0)) as read,
            patch.object(Path, "mkdir", side_effect=AssertionError("No diagnostic directories")),
            patch.object(Path, "open", side_effect=AssertionError("No diagnostic files"))):
            state = reader.capture_state(None, (1,))

        self.assertEqual(read.call_count, 2)
        self.assertEqual(state.images, {1: body})
        self.assertTrue(state.metadata["paired_reads_match"])

    def test_capture_never_returns_a_mismatched_pair(self):
        body = bytes(1152)
        other = bytes([1]) + body[1:]
        with tempfile.TemporaryDirectory() as directory:
            with (patch.object(reader, "discover", return_value={"hidraw": "/dev/mock"}),
                patch.object(reader, "open_keyboard", return_value=nullcontext(9)),
                patch.object(reader.Transport, "read", side_effect=[(body, body, 0, 0), (other, other, 1, 0)])):
                with self.assertRaisesRegex(ValueError, "captures differ"):
                    reader.capture_state(Path(directory) / "capture", (1,))

    def test_exact_full_channel_reads(self):
        for channel, blocks in enumerate(reader.BLOCKS):
            body = bytes((i * 17 + i // 64) % 256 for i in range(blocks * 64))
            reports = body + body[-64:]
            transport = ScriptedTransport(
                [reply(0xAB), reply(2, 0x54711688), *(reports[i : i + 64] for i in range(0, len(reports), 64)),
                    reply(2, sum(body))]
            )
            self.assertEqual(transport.read(channel), (body, reports, sum(body), 0))
            self.assertEqual(
                transport.sent,
                [reader.command(0xAB), bytes(64), reader.command(2),
                    reader.command(reader.READ_COMMANDS[channel], blocks), reader.command(2)],
            )

    def test_bad_handshake_stops_before_channel_setup(self):
        for replies in ([bytes(64)], [reply(0xAB), reply(2, 1)]):
            transport = ScriptedTransport(replies)
            with self.assertRaises(ValueError):
                transport.read(1)

            self.assertFalse(any(packet[1] in reader.READ_COMMANDS for packet in transport.sent))

    def test_expected_read_uses_no_extra_report_and_resolves_checksum_collision(self):
        blocks = reader.BLOCKS[1]
        body = bytes([7]) + bytes(blocks * 64 - 1)
        transport = ScriptedTransport(
            [reply(0xAB), reply(2, 0x54711688), *(body[i : i + 64] for i in range(0, len(body), 64)), reply(2, 7)]
        )
        self.assertEqual(transport.read(1, body), (body, body, 7, 0))
        reports = bytes([7]) + bytes(127) + bytes([7]) + bytes(63)
        self.assertEqual(reader.select_window(reports, 2, 7, reports[:128]), (reports[:128], 0))

    def test_window_checksum_and_ambiguity(self):
        body = bytes([7]) + bytes(127)
        self.assertEqual(reader.select_window(bytes([0xA5]) * 64 + body, 2, 7), (body, 1))
        self.assertEqual(reader.select_window(bytes(192), 2, 0), (bytes(128), 0))
        for reports, checksum in ((bytes(192), 1), (bytes(191), 0),
            (bytes([7]) + bytes(127) + bytes([7]) + bytes(63), 7)):
            with self.assertRaises(ValueError):
                reader.select_window(reports, 2, checksum)

    def test_feature_framing_and_short_report_rejected_without_retry(self):
        class Log(io.StringIO):
            def fileno(self):
                return 123

        for count in (64, 65):
            log = Log()
            transport = reader.Transport(9, log)
            with (patch.object(reader.fcntl, "ioctl", return_value=count) as ioctl, patch.object(reader.os, "fsync"),
                patch.object(reader.time, "sleep")):
                if count == 64:
                    with self.assertRaises(ValueError):
                        transport.transfer(reader.command(2))
                else:
                    transport.transfer(reader.command(2))

                self.assertEqual(ioctl.call_count, 1)
                arguments = ioctl.call_args.args
                self.assertEqual(arguments[1], 0xC0414806)
                self.assertEqual(bytes(arguments[2]), b"\0" + reader.command(2))

            self.assertEqual(json.loads(log.getvalue().splitlines()[-1])["success"], count == 65)

    def test_write_commands_rejected_before_ioctl_or_logging(self):
        transport = reader.Transport(9, io.StringIO())
        with patch.object(reader.fcntl, "ioctl") as ioctl:
            for code in (0x11, 0x13, 0x15, 0x17, 0xF0, 0xF1, 0xE0):
                with self.assertRaises(ValueError):
                    transport.transfer(reader.command(code))

            ioctl.assert_not_called()

    def test_lock_blocks_before_discovery_and_output_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "capture"
            with reader.operation_lock(), patch.object(reader, "discover") as discover:
                with self.assertRaises(BlockingIOError):
                    reader.capture(output, (1,))

                discover.assert_not_called()
                self.assertFalse(output.exists())

    def test_existing_evidence_is_preserved_before_device_open(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            marker = output / "result.json"
            marker.write_text("preserve evidence")
            with patch.object(reader, "discover", return_value={"hidraw": "/dev/does-not-exist"}):
                with self.assertRaises(FileExistsError):
                    reader.capture(output, (1,))

            self.assertEqual(marker.read_text(), "preserve evidence")

    def test_missing_keyboard_probe_does_not_open_device_or_load_native_core(self):
        with tempfile.TemporaryDirectory() as name:
            with patch.object(reader, "open_keyboard") as opened, patch.object(reader, "native_library") as native:
                self.assertFalse(reader.is_connected(Path(name)))
                opened.assert_not_called()
                native.assert_not_called()

    def test_probe_does_not_disguise_unsupported_or_unreadable_devices_as_absent(self):
        with patch.object(reader, "discover", side_effect=PermissionError("Cannot inspect keyboard")):
            with self.assertRaises(PermissionError):
                reader.is_connected()

    def test_discovery_rejects_wrong_revision_descriptor_and_multiple_devices(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sysfs = root / "hidraw"

            def device(name, usb_name):
                usb = root / usb_name
                hid = usb / "interface" / "hid"
                hid.mkdir(parents=True)
                (usb / "idVendor").write_text("0c45")
                (usb / "bcdDevice").write_text("0138")
                (hid / "uevent").write_text("HID_ID=0003:00000C45:00008006\n")
                (hid / "report_descriptor").write_bytes(reader.DESCRIPTOR)
                entry = sysfs / name
                entry.mkdir(parents=True)
                (entry / "device").symlink_to(hid)
                return usb, hid

            usb, hid = device("hidraw2", "1-2")
            self.assertEqual(reader.discover(sysfs)["hidraw"], "/dev/hidraw2")
            (usb / "bcdDevice").write_text("0182")
            with self.assertRaises(ValueError):
                reader.discover(sysfs)

            (usb / "bcdDevice").write_text("0138")
            (hid / "report_descriptor").write_bytes(bytes(64))
            with self.assertRaises(ValueError):
                reader.discover(sysfs)

            (hid / "report_descriptor").write_bytes(reader.DESCRIPTOR)
            device("hidraw3", "1-3")
            with self.assertRaises(ValueError):
                reader.discover(sysfs)

    def test_bad_read_arguments_stop_before_session_handshake(self):
        for channel, expected in ((-1, None), (4, None), (True, None), (1.0, None), (1, bytes(64))):
            transport = ScriptedTransport([])
            with self.subTest(channel=channel), self.assertRaises(ValueError):
                transport.read(channel, expected)

            self.assertEqual(transport.sent, [])

    def test_malformed_feature_payload_stops_before_ioctl(self):
        transport = reader.Transport(9, io.StringIO())
        with patch.object(reader.fcntl, "ioctl") as ioctl:
            for size in (0, 63, 65):
                with self.assertRaises(ValueError):
                    transport._exchange(bytes(size))

            ioctl.assert_not_called()


if __name__ == "__main__":
    unittest.main()
