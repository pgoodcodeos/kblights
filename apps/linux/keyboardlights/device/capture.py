#!/usr/bin/env python3
"""Linux hidraw validation of the legacy C989 read protocol. No write commands.

Captures each requested channel twice, with a fresh volatile handshake for each
read. Stops on the first API, framing, checksum, or paired-read failure; no retries.
Does not detach the kernel keyboard driver or consume input reports.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import struct
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from keyboardlights.device.core import native_library, read_window
from keyboardlights.device.lock_indicators import LockIndicators

BLOCKS = (10, 18)
READ_COMMANDS = (0x10, 0x12)

# Exact descriptor observed on the Mac-tested legacy keyboard, interface 0.
DESCRIPTOR = bytes.fromhex(
    "05010906a101050719e029e715002501750195088102950175088101950575010508"
    "19012905910295017503910195067508150026ff00050719002a65008100050c0900"
    "1580257f95407508b102c0"
)


def command(code: int, blocks: int = 0) -> bytes:
    packet = bytearray(64)
    packet[:2] = bytes((4, code))
    packet[8:10] = blocks.to_bytes(2, "little")
    return bytes(packet)


ALLOWED = {bytes(64), command(0xAB), command(2)} | {command(code, blocks) for code, blocks in zip(READ_COMMANDS, BLOCKS)
}


class KeyboardNotConnectedError(ValueError):
    """No matching USB keyboard is present in system metadata."""


def is_connected(sysfs: Path = Path("/sys/class/hidraw")) -> bool:
    """Check system metadata without opening a device or sending reports."""
    try:
        discover(sysfs)
    except KeyboardNotConnectedError:
        return False

    return True


def discover(sysfs: Path = Path("/sys/class/hidraw")) -> dict:
    candidates = []
    keyboards = set()
    for entry in sorted(sysfs.glob("hidraw*")):
        device = (entry / "device").resolve()
        props = dict(line.split("=", 1) for line in (device / "uevent").read_text().splitlines() if "=" in line)
        if props.get("HID_ID") != "0003:00000C45:00008006":
            continue

        usb = next((p for p in device.parents if (p / "idVendor").is_file()), None)
        if usb is None:
            raise ValueError("Cannot establish keyboard USB identity")

        keyboards.add(usb)
        if (usb / "bcdDevice").read_text().strip() != "0138":
            raise ValueError("Refusing a C989 other than legacy firmware 1.38")

        if (device / "report_descriptor").read_bytes() != DESCRIPTOR:
            continue

        candidates.append(
            {"hidraw": f"/dev/{entry.name}", "usb_path": usb.name, "vendor_id": "0C45", "product_id": "8006",
                "device_revision": "0138", "descriptor_hex": DESCRIPTOR.hex()}
        )

    if not keyboards:
        raise KeyboardNotConnectedError("Keyboard not connected")

    if len(keyboards) != 1 or len(candidates) != 1:
        raise ValueError(
            f"Expected one legacy keyboard and configuration interface; found {len(keyboards)}/{len(candidates)}"
        )

    return candidates[0]


def select_window(reports: bytes, blocks: int, checksum: int, expected: bytes | None = None) -> tuple[bytes, int]:
    if type(blocks) is not int or blocks <= 0:
        raise ValueError("Read window must contain at least one block")

    size = blocks * 64
    offset = read_window(reports, size, checksum, expected)
    return reports[offset : offset + size], offset // 64


@contextmanager
def diagnostic_log(output: Path | None, name: str):
    if output is None:
        yield None
    else:
        with (output / name).open("x") as log:
            yield log


class Transport:
    def __init__(self, fd: int, log):
        self.fd, self.log, self.sequence = fd, log, 0
        self.lock_reads = 0

    def record(self, **entry):
        if self.log is None:
            return

        self.log.write(json.dumps({"sequence": self.sequence, "monotonic_ns": time.monotonic_ns(), **entry}) + "\n")
        self.log.flush()
        os.fsync(self.log.fileno())

    def record_failure(self, error, **entry):
        try:
            self.record(event="completion", success=False, error=str(error), **entry)
        except Exception as log_error:
            error.add_note(f"Could not save failed HID exchange: {log_error}")

    def transfer(self, payload: bytes | None = None) -> bytes:
        if payload is not None and payload not in ALLOWED:
            raise ValueError("Refusing packet outside the fixed read-only allowlist")

        result = self._exchange(payload)
        time.sleep(0.015)
        return result

    def _exchange(self, payload: bytes | None = None) -> bytes:
        """One framed ioctl. Callers own packet policy and transfer timing."""
        if payload is not None and len(payload) != 64:
            raise ValueError("Feature payload must contain exactly 64 bytes")

        self.sequence += 1
        incoming = payload is None
        buffer = bytearray(b"\0" + (bytes(64) if incoming else payload))

        # Linux HIDIOCGFEATURE/HIDIOCSFEATURE; report ID zero precedes payload.
        request = (3 << 30) | (65 << 16) | (ord("H") << 8) | (7 if incoming else 6)
        self.record(event="attempt", direction="in" if incoming else "out", buffer_hex=buffer.hex())
        try:
            count = fcntl.ioctl(self.fd, request, buffer, True)
            if count != 65 or buffer[0] != 0:
                raise ValueError(f"Expected report ID zero and 65 API bytes; got {count} bytes, ID {buffer[0]}")
        except (OSError, ValueError) as error:
            self.record_failure(error, buffer_hex=buffer.hex())
            raise

        self.record(event="completion", success=True, api_bytes=count, buffer_hex=buffer.hex())
        return bytes(buffer[1:])

    def read_lock_indicators(self) -> LockIndicators:
        """GET_REPORT(Output), report ID zero, one payload byte; never set LEDs."""
        self.sequence += 1
        self.lock_reads += 1
        buffer = bytearray(2)

        # HIDIOCGOUTPUT(2): Linux includes the zero report ID before the payload.
        request = (3 << 30) | (2 << 16) | (ord("H") << 8) | 0x0C
        self.record(event="attempt", direction="in", report_type="output", report_id=0, requested_length=1,
            ioctl=request, buffer_hex=buffer.hex())
        try:
            count = fcntl.ioctl(self.fd, request, buffer, True)
            if count != 2 or len(buffer) != 2 or buffer[0] != 0:
                raise ValueError("Expected report ID zero and exactly one lock indicator byte")
        except (OSError, ValueError) as error:
            self.record_failure(error, direction="in", report_type="output", buffer_hex=buffer.hex())
            raise

        self.record(event="completion", direction="in", report_type="output", success=True, api_bytes=count,
            buffer_hex=buffer.hex())
        time.sleep(0.015)
        return LockIndicators(buffer[1])

    def query(self, code: int) -> bytes:
        self.transfer(command(code))
        reply = self.transfer()
        if reply[:2] != bytes((4, code)) or reply[3] != 1:
            raise ValueError(f"Rejected command {code:02X}: {reply.hex()}")

        return reply

    def checksum(self) -> int:
        return int.from_bytes(self.query(2)[4:8], "little")

    def unlock(self):
        self.query(0xAB)
        self.transfer(bytes(64))
        response = self.checksum()
        if response != 0x54711688:
            raise ValueError(f"Volatile handshake checksum {response:08X} != 54711688")

    def read(self, channel: int, expected: bytes | None = None) -> tuple[bytes, bytes, int, int]:
        if type(channel) is not int or channel not in range(len(BLOCKS)):
            raise ValueError("Invalid read channel")

        blocks = BLOCKS[channel]
        if expected is not None and len(expected) != blocks * 64:
            raise ValueError("Expected image has the wrong size for this channel")

        self.unlock()
        self.transfer(command(READ_COMMANDS[channel], blocks))
        reports = b"".join(self.transfer() for _ in range(blocks if expected is not None else blocks + 1))
        if expected is not None and reports != expected:
            reports += self.transfer()

        checksum = self.checksum()
        body, skipped = select_window(reports, blocks, checksum, expected)
        return body, reports, checksum, skipped


@contextmanager
def open_keyboard(identity: dict):
    if discover() != identity:
        raise ValueError("Keyboard identity changed since capture")

    fd = os.open(identity["hidraw"], os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = bytearray(8)
        fcntl.ioctl(fd, (2 << 30) | (8 << 16) | (ord("H") << 8) | 3, info, True)
        if struct.unpack("IHH", info) != (3, 0x0C45, 0x8006) or discover() != identity:
            raise ValueError("Opened HID device identity does not match capture")

        yield fd
    finally:
        os.close(fd)


_operation_thread = threading.local()


@contextmanager
def operation_lock():
    if getattr(_operation_thread, "batch", False) and getattr(_operation_thread, "depth", 0):
        _operation_thread.depth += 1
        try:
            yield
        finally:
            _operation_thread.depth -= 1

        return

    path = f"/tmp/c989-baseline-trial-{os.getuid()}.lock"
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _operation_thread.depth = 1
        yield
    finally:
        _operation_thread.depth = 0
        os.close(fd)


@contextmanager
def operation_batch():
    if getattr(_operation_thread, "batch", False):
        raise RuntimeError("A keyboard batch is already active on this worker")

    with operation_lock():
        _operation_thread.batch = True
        try:
            yield
        finally:
            _operation_thread.batch = False


@dataclass(frozen=True)
class CapturedState:
    metadata: dict
    images: dict[int, bytes]
    lock_indicators: LockIndicators | None = None


def capture_state(output: Path | None, channels: tuple[int, ...]) -> CapturedState:
    if not channels or any(type(channel) is not int or channel not in range(len(BLOCKS)) for channel in channels):
        raise ValueError("Invalid read channels")

    native_library()  # Resolve the complete ABI before discovery, output, or HID traffic.
    with operation_lock():
        identity = discover()
        if output is not None:
            output.mkdir(parents=True, exist_ok=False)

        result = {"format": "c989-linux-read-validation-v1", "identity": identity, "transport": "hidraw-feature",
            "retries": 0, "configuration_writes": 0, "paired_reads_match": False, "channels": []}
        images = {}
        failure = None
        try:
            with diagnostic_log(output, "exchanges.jsonl") as log, open_keyboard(identity) as fd:
                transport = Transport(fd, log)
                for channel in channels:
                    first = None
                    for copy in ("first", "second"):
                        body, reports, checksum, skipped = transport.read(channel)
                        name = f"channel-{channel}-{copy}"
                        if output is not None:
                            (output / f"{name}.bin").write_bytes(body)
                            (output / f"{name}-reports.bin").write_bytes(reports)

                        result["channels"].append(
                            {"channel": channel, "copy": copy, "bytes": len(body), "checksum": f"{checksum:08X}",
                                "checksum_verified": True, "sha256": hashlib.sha256(body).hexdigest(),
                                "skipped_reports": skipped, "file": f"{name}.bin"}
                        )
                        if first is None:
                            first = body
                        elif body != first:
                            raise ValueError(f"Paired channel-{channel} captures differ")

                        images[channel] = bytes(body)

                lock_indicators = transport.read_lock_indicators()
                result["lock_indicators"] = lock_indicators.raw_value
                if output is not None:
                    (output / "lock-indicators.bin").write_bytes(bytes((lock_indicators.raw_value,)))

                result["feature_exchanges"] = transport.sequence - transport.lock_reads
                result["output_report_reads"] = transport.lock_reads
                result["paired_reads_match"] = True
        except BaseException as error:
            failure = error
            result["error"] = str(error)
            raise
        finally:
            if output is not None:
                try:
                    (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
                except Exception as error:
                    if failure is None:
                        raise

                    failure.add_note(f"Could not save final capture evidence: {error}")

        return CapturedState(metadata=result, images=images, lock_indicators=lock_indicators)


def capture(output: Path, channels: tuple[int, ...]) -> dict:
    """Diagnostic CLI compatibility; applications consume capture_state directly."""
    result = capture_state(output, channels).metadata
    for entry in result["channels"]:
        print(f"Channel {entry['channel']} {entry['copy']}: {entry['bytes']} bytes, checksum {entry['checksum']}")

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="new evidence directory (must not exist)")
    parser.add_argument("--channel", type=int, choices=range(len(BLOCKS)),
        help="default: assignment and lighting channels")
    args = parser.parse_args()
    try:
        capture(args.output, (args.channel,) if args.channel is not None else tuple(range(len(BLOCKS))))
    except (OSError, ValueError) as error:
        print(f"Read validation stopped: {error}", file=sys.stderr)
        return 1

    print("PASS: paired checksum-verified reads. No configuration writes sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
