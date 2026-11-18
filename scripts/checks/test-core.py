"""Run the portable C policy and transaction suites without device access."""

import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
with tempfile.TemporaryDirectory(prefix="keyboardlights-core-") as directory:
    for source in sorted((ROOT / "tests/core").glob("test_*.c")):
        binary = Path(directory) / source.stem
        subprocess.run(
            [shutil.which("clang") or "cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined",
                str(ROOT / "core/Core.c"), str(source), "-o", str(binary)],
            check=True,
        )
        subprocess.run([str(binary)], check=True)
