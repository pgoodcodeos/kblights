"""Synthetic configuration fixtures and protocol replies; no hardware captures."""

import json
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def baseline_image():
    data = bytearray(1152)
    for index, tag in enumerate((1, 2, 3, 4, 5, 6, 7, 128)):
        data[index * 64] = tag
        data[index * 64 + 62 : index * 64 + 64] = b"\xaa\x55"

    # Uniform cyan UserDefine state used by editor reset/dirty-state tests.

    layout = json.loads((Path(__file__).resolve().parents[2] / "data/c989/layout.json").read_text())
    for slot in layout["slots"]:
        if slot["physical_key"]:
            offset = 0x200 + slot["led_index"] * 4
            data[offset : offset + 4] = bytes.fromhex("8000feff")

    data[0x440] = 0x80
    data[0x449] = 16
    data[0x47E:] = b"\xaa\x55"
    return bytes(data)


def reply(code, checksum=0):
    data = bytearray(64)
    data[:2] = bytes((4, code))
    data[3] = 1
    data[4:8] = checksum.to_bytes(4, "little")
    return bytes(data)
