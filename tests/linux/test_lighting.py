"""Offline parity fixtures and Linux write-adapter failure injection."""

import io
import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import keyboardlights.lighting.service as light
from support import FIXTURES, baseline_image, reply


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.before = baseline_image()

    def test_every_animation_matches_mac_canonical_fixture(self):
        matrix = json.loads((FIXTURES / "lighting/animation-records.json").read_text())
        for mode in matrix["modes"]:
            if mode["tag"] < 7:
                continue

            with self.subTest(mode=mode["name"]):
                after = light.candidate(
                    self.before,
                    dict(style="animation", mode=mode["tag"], brightness=16, speed=12, generated=True, color="#00FF00",
                        direction=0),
                )
                self.assertEqual(after[0x440:0x450], bytes.fromhex(mode["canonical_record"]))
                self.assertEqual(after[:0x440], self.before[:0x440])
                self.assertEqual(after[0x450:], self.before[0x450:])

    def test_persistent_solid_has_exact_expected_slots_and_selected_record(self):
        before = baseline_image()
        after = light.candidate(before, dict(style="solid", brightness=16, color="#FFFFFF"))
        expected = bytearray(before)
        for index in light.PHYSICAL:
            expected[0x200 + index * 4 : 0x204 + index * 4] = bytes.fromhex("80ffffff")

        expected[0x440] = 0x80
        expected[0x449] = 16
        self.assertEqual(after, bytes(expected))
        self.assertEqual(len(light.PHYSICAL), 115)

    def test_single_color_and_only_rolling_direction_match_mac_policy(self):
        for tag in range(7, 16):
            after = light.candidate(self.before,
                dict(style="animation", mode=tag, brightness=8, speed=2, generated=False, color="#123456", direction=1))
            self.assertEqual(after[0x441:0x444], bytes(3) if tag in (7, 9) else bytes.fromhex("123456"))
            self.assertEqual(after[0x44B], 1 if tag == 11 else 0)

    def test_per_key_uses_known_w_slot_and_preserves_reserved_slots(self):
        slots = [{"enabled": False, "color": "#000000"} for _ in range(144)]
        slots[39] = {"enabled": True, "color": "#0000FF"}
        slots[0] = {"enabled": True, "color": "#FFFFFF"}
        after = light.candidate(self.before, dict(style="perKey", brightness=16, slots=slots))
        self.assertEqual(after[0x29C:0x2A0], bytes.fromhex("800000ff"))
        self.assertEqual(after[0x200:0x204], self.before[0x200:0x204])
        for index in range(144):
            if index not in light.PHYSICAL:
                offset = 0x200 + index * 4
                self.assertEqual(after[offset : offset + 4], self.before[offset : offset + 4])

    def test_invalid_controls_shapes_and_mutation_ranges_rejected(self):
        valid = dict(style="animation", mode=8, brightness=16, speed=12, generated=True)
        for key, value in (("speed", 1), ("speed", 17), ("brightness", 0), ("brightness", True), ("mode", 1),
            ("direction", 3), ("generated", "false")):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                light.candidate(self.before, {**valid, key: value})

        for offset in (0, 62, 0x200, 0x450, 0x47F):
            modified = bytearray(self.before)
            modified[offset] ^= 1
            with self.assertRaises(ValueError):
                light.allowed_changes(self.before, modified)


class MockTransport:
    def __init__(self, body, fail_at=0, bad_checksum=False):
        self.body, self.fail_at, self.bad_checksum = body, fail_at, bad_checksum
        self.calls, self.sent, self.receives = 0, [], 0

    def _exchange(self, payload=None):
        self.calls += 1
        if payload is not None:
            self.sent.append(payload)

        if self.calls == self.fail_at:
            raise OSError("injected ioctl failure")

        if payload is None:
            self.receives += 1
            return reply(0x13) if self.receives == 1 else reply(2, sum(self.body) + self.bad_checksum)

        return payload


class NativeAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib = light.native_library()
        cls.body = light.candidate(baseline_image(), dict(style="solid", brightness=16, color="#FF00FF"))

    def run_upload(self, transport, journal=None):
        result = light.WriteResult()
        with tempfile.TemporaryFile(mode="w+") as log, patch.object(light.time, "sleep") as sleep:
            light.upload(transport, self.body, result, journal if journal is not None else log, self.lib)

        return result, sleep

    def test_exact_stream_one_f0_and_mac_timing(self):
        transport = MockTransport(self.body)
        result, sleep = self.run_upload(transport)
        self.assertEqual(b"".join(transport.sent), light.stream(self.body))
        self.assertEqual(transport.calls, 23)
        self.assertEqual(result.stage, 5)
        self.assertTrue(result.activation_host_completed)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [0.105] + [0.015] * 23)

    def test_every_ioctl_failure_stops_without_retry(self):
        for fail_at in range(1, 24):
            transport = MockTransport(self.body, fail_at=fail_at)
            with self.subTest(fail_at=fail_at), self.assertRaises(OSError):
                self.run_upload(transport)

            self.assertEqual(transport.calls, fail_at)
            self.assertLessEqual(transport.sent.count(light.reader.command(0xF0)), 1)

    def test_bad_checksum_and_journal_failure_prevent_f0(self):
        transport = MockTransport(self.body, bad_checksum=True)
        with self.assertRaises(ValueError):
            self.run_upload(transport)

        self.assertNotIn(light.reader.command(0xF0), transport.sent)
        transport = MockTransport(self.body)
        with self.assertRaises(io.UnsupportedOperation):
            self.run_upload(transport, io.StringIO())

        self.assertEqual(transport.calls, 0)

    def test_short_receive_stops_before_activation(self):
        transport = MockTransport(self.body)
        exchange = transport._exchange

        def short(payload=None):
            packet = exchange(payload)
            return packet[:-1] if payload is None else packet

        with patch.object(transport, "_exchange", side_effect=short):
            with self.assertRaisesRegex(ValueError, "Short native receive"):
                self.run_upload(transport)

        self.assertEqual(transport.calls, 2)
        self.assertNotIn(light.reader.command(0xF0), transport.sent)


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.service = light.LightingService(Path(self.temporary.name))
        self.service.baseline = baseline_image()
        self.service.identity = {"hidraw": "/dev/test"}

    def test_missing_postwrite_lock_snapshot_blocks_writes_without_publishing_baseline(self):
        before = self.service.baseline
        for result in (None, {}, [], {"result": "passed"}):
            with self.subTest(result=result):
                self.service.baseline = before
                self.service.identity = {"hidraw": "/dev/test"}
                self.service.guard = light.WriteGuard()
                review = self.service.plan(dict(style="solid", color="#FF00FF", brightness=16))
                with patch.object(light, "execute", return_value=result) as execute:
                    with self.assertRaises(light.UncertainWriteError):
                        self.service.apply(review["token"])

                execute.assert_called_once()
                self.assertIsNone(self.service.baseline)
                self.assertIsNone(self.service.lock_indicators)
                self.assertTrue(self.service.guard.requires_inspection)
                with self.assertRaises(ValueError):
                    self.service.plan(dict(style="solid", color="#FF00FF", brightness=16))

    def test_successful_write_retains_lock_snapshot_without_logging(self):
        review = self.service.plan(dict(style="solid", color="#FF00FF", brightness=16))
        with patch.object(light, "execute", return_value={"lock_indicators": 0x85}):
            self.service.apply(review["token"])

        self.assertEqual(self.service.lock_indicators, light.reader.LockIndicators(0x85))
        self.assertIsNotNone(self.service.baseline)
        self.assertFalse(self.service.guard.requires_inspection)

    def test_malformed_postwrite_lock_data_blocks_writes_before_publishing_baseline(self):
        before = self.service.baseline
        for raw in (-1, 256, True, None, "2"):
            self.service.baseline = before
            self.service.identity = {"hidraw": "/dev/test"}
            self.service.guard = light.WriteGuard()
            review = self.service.plan(dict(style="solid", color="#FF00FF", brightness=16))
            with patch.object(light, "execute", return_value={"lock_indicators": raw}):
                with self.assertRaises(light.VerifiedWriteEvidenceError):
                    self.service.apply(review["token"])

            self.assertIsNone(self.service.baseline)
            self.assertIsNone(self.service.lock_indicators)
            self.assertTrue(self.service.guard.requires_inspection)

    def test_review_has_no_device_access_and_is_single_use_after_failure(self):
        with patch.object(light.reader, "open_keyboard") as opened:
            review = self.service.plan(dict(style="solid", color="#FF00FF", brightness=16))
            opened.assert_not_called()

        self.assertEqual(list(Path(self.temporary.name).iterdir()), [])
        with patch.object(light, "execute", side_effect=OSError("injected")) as execute:
            with self.assertRaises(OSError):
                self.service.apply(review["token"])

            with self.assertRaises(ValueError):
                self.service.apply(review["token"])

            self.assertEqual(execute.call_count, 1)

        self.assertIsNone(self.service.baseline)

    def test_exact_preflight_mismatch_prevents_upload(self):
        @contextmanager
        def opened(_):
            yield 9

        before = self.service.baseline
        after = light.candidate(before, dict(style="solid", color="#FF00FF", brightness=16))
        with (patch.object(light.reader, "open_keyboard", opened),
            patch.object(light.reader.Transport, "read", return_value=(after, after + bytes(64), sum(after), 0)),
            patch.object(light, "upload") as upload):
            with self.assertRaises(ValueError):
                light.execute(before, after, self.service.identity, Path(self.temporary.name) / "write")

            upload.assert_not_called()

    def test_review_token_is_bound_and_not_replayed(self):
        review = self.service.plan(dict(style="solid", color="#FF00FF", brightness=16))
        with patch.object(light, "execute") as execute:
            with self.assertRaises(ValueError):
                self.service.apply("wrong-token")

            execute.assert_not_called()

            with self.assertRaises(ValueError):
                self.service.apply(review["token"])

            execute.assert_not_called()

    def test_review_settings_are_a_snapshot(self):
        settings = dict(style="solid", color="#FF00FF", brightness=16)
        review = self.service.plan(settings)
        settings["color"] = "#00FF00"
        self.assertEqual(review["settings"]["color"], "#FF00FF")
        expected = light.candidate(self.service.baseline, review["settings"])
        self.assertEqual(light.digest(expected), review["candidateSha256"])

    def test_logging_defaults_off_and_output_directories_are_opt_in(self):
        directory = Path(self.temporary.name) / "optional-logs"
        service = light.LightingService(directory)
        self.assertFalse(service.logging)
        self.assertIsNone(service.folder("capture"))
        self.assertFalse(directory.exists())
        service.logging = True
        output = service.folder("capture")
        self.assertEqual(output.parent, directory)
        self.assertTrue(directory.is_dir())

    def test_capture_uses_returned_bytes_without_reading_evidence(self):
        image = self.service.baseline
        captured = light.reader.CapturedState(metadata={"identity": self.service.identity}, images={1: image})
        with (patch.object(light.reader, "capture_state", return_value=captured),
            patch.object(Path, "read_bytes", side_effect=AssertionError("Evidence is not an IPC channel"))):
            self.service.read()

        self.assertEqual(self.service.baseline, image)

    def test_review_freezes_mutable_baseline_and_identity(self):
        before = self.service.baseline
        mutable = bytearray(before)
        identity = self.service.identity
        self.service.baseline = mutable
        self.service.plan(dict(style="solid", color="#FF00FF", brightness=16))
        mutable[0] ^= 1
        identity["hidraw"] = "/dev/different"
        self.assertEqual(self.service.pending[1], before)
        self.assertEqual(self.service.pending[3]["hidraw"], "/dev/test")

    def test_verified_evidence_failure_blocks_further_writes(self):
        review = self.service.plan(dict(style="solid", color="#FF00FF", brightness=16))
        with patch.object(light, "execute",
            side_effect=light.VerifiedWriteEvidenceError("/tmp/evidence", OSError("disk full"))):
            with self.assertRaisesRegex(light.VerifiedWriteEvidenceError, "applied and verified"):
                self.service.apply(review["token"])

        self.assertEqual(self.service.guard.evidence, "/tmp/evidence")
        with self.assertRaises(ValueError):
            self.service.plan(dict(style="solid", color="#00FF00", brightness=16))


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.output = Path(self.temporary.name) / "new-parent" / "write"
        self.before = baseline_image()
        self.after = light.candidate(self.before, dict(style="solid", color="#FF00FF", brightness=16))
        self.transport = MockTransport(self.after)
        self.opened = self.enterContext(patch.object(light.reader, "open_keyboard"))
        self.opened.return_value.__enter__.return_value = 9
        self.read = self.enterContext(
            patch.object(
                light.reader.Transport,
                "read",
                side_effect=[(self.before, self.before, sum(self.before), 0),
                    (self.after, self.after, sum(self.after), 0)],
            )
        )
        self.lock_read = self.enterContext(
            patch.object(light.reader.Transport, "read_lock_indicators", return_value=light.reader.LockIndicators(5))
        )
        self.enterContext(patch.object(light.reader.Transport, "unlock"))
        self.enterContext(patch.object(light.reader.Transport, "_exchange", side_effect=self.transport._exchange))
        self.enterContext(patch.object(light.time, "sleep"))

    def test_lock_read_failure_stops_before_upload(self):
        self.lock_read.side_effect = OSError("lock indicators unavailable")
        with self.assertRaises(OSError) as caught:
            self.execute()

        self.assertIn("lock indicators unavailable", str(caught.exception))
        self.assertEqual(self.transport.sent, [])
        self.lock_read.assert_called_once()

    def execute(self):
        return light.execute(self.before, self.after, {"hidraw": "/dev/not-opened"}, self.output)

    def test_disabled_logging_verifies_write_without_creating_files(self):
        with (patch.object(light, "durable", side_effect=AssertionError("No diagnostic writes")),
            patch.object(light, "make_directory", side_effect=AssertionError("No diagnostic directories"))):
            status = light.execute(self.before, self.after, {"hidraw": "/dev/not-opened"}, None)

        self.assertTrue(status["lighting_preflight"])
        self.assertTrue(status["lighting_postflight"])
        self.assertEqual(self.transport.sent.count(light.reader.command(0xF0)), 1)
        self.assertFalse(self.output.parent.exists())

    def test_uncertain_write_without_logs_has_no_fictional_evidence_path(self):
        self.transport.fail_at = 23
        with self.assertRaises(light.UncertainWriteError) as caught:
            light.execute(self.before, self.after, {"hidraw": "/dev/not-opened"}, None)

        self.assertIsNone(caught.exception.evidence)
        self.assertNotIn("None", str(caught.exception))
        self.assertNotIn("saved evidence", str(caught.exception))
        self.assertFalse(self.output.parent.exists())

    def test_f0_io_failure_is_uncertain_and_preserves_original_cause(self):
        self.transport.fail_at = 23  # The activation send, after checksum verification.
        with self.assertRaises(light.UncertainWriteError) as caught:
            self.execute()

        self.assertIsInstance(caught.exception.__cause__, OSError)
        self.assertIn("Do not retry", str(caught.exception))
        self.assertIn(str(self.output), str(caught.exception))
        status = json.loads((self.output / "result.json").read_text())
        self.assertTrue(status["activation_attempted"])
        self.assertFalse(status["lighting_postflight"])
        self.assertEqual(self.transport.sent.count(light.reader.command(0xF0)), 1)

    def test_postflight_failure_is_also_uncertain(self):
        self.read.side_effect = [(self.before, self.before, sum(self.before), 0), OSError("postflight disconnected")]
        with self.assertRaisesRegex(light.UncertainWriteError, "postflight disconnected"):
            self.execute()

        status = json.loads((self.output / "result.json").read_text())
        self.assertTrue(status["activation_host_completed"])
        self.assertFalse(status["lighting_postflight"])

    def test_result_save_failure_does_not_mask_uncertain_activation(self):
        self.transport.fail_at = 23
        save_json = light.save_json

        def fail_result(path, value):
            if path.name == "result.json":
                raise OSError("disk full")

            save_json(path, value)

        with patch.object(light, "save_json", side_effect=fail_result):
            with self.assertRaises(light.UncertainWriteError) as caught:
                self.execute()

        self.assertIn("injected ioctl failure", str(caught.exception))
        self.assertIn("disk full", str(caught.exception.__cause__.__notes__))
        self.assertIn(str(self.output), str(caught.exception))

    def test_evidence_failure_after_verified_write_reports_successful_activation(self):
        save_json = light.save_json

        def fail_result(path, value):
            if path.name == "result.json":
                raise OSError("disk full")

            save_json(path, value)

        with patch.object(light, "save_json", side_effect=fail_result):
            with self.assertRaisesRegex(RuntimeError, "applied and verified") as caught:
                self.execute()

        self.assertNotIsInstance(caught.exception, light.UncertainWriteError)
        self.assertEqual(self.transport.sent.count(light.reader.command(0xF0)), 1)

    def test_preflight_failure_is_not_reported_as_activation_uncertainty(self):
        self.read.side_effect = OSError("preflight disconnected")
        with self.assertRaisesRegex(OSError, "preflight disconnected"):
            self.execute()

        self.assertEqual(self.transport.sent, [])

    def test_directory_entries_are_synced_before_any_device_access(self):
        sync = light.sync_directory
        synced = []

        def record(path):
            synced.append((path, set(entry.name for entry in path.iterdir())))
            sync(path)

        def opened(_):
            self.assertTrue(
                any(
                    path == self.output
                    and {"journal.jsonl", "exchanges.jsonl", "candidate-channel-1.bin", "expected-channel-1.bin"}
                    <= names
                    for path, names in synced
                )
            )
            self.assertIn((self.output.parent, {"write"}), synced)
            self.assertIn((self.output.parent.parent, {"new-parent"}), synced)
            return self.opened.return_value

        self.opened.side_effect = opened
        with patch.object(light, "sync_directory", side_effect=record):
            self.assertEqual(self.execute()["result"], "passed")

    def test_journal_directory_sync_failure_stops_before_device_open(self):
        sync = light.sync_directory

        def fail_journal_directory(path):
            if path == self.output and (path / "journal.jsonl").exists():
                raise OSError("directory sync failed")

            sync(path)

        with patch.object(light, "sync_directory", side_effect=fail_journal_directory):
            with self.assertRaisesRegex(OSError, "directory sync failed"):
                self.execute()

        self.opened.assert_not_called()
        self.assertEqual(self.transport.sent, [])


if __name__ == "__main__":
    unittest.main()
