"""Run the Linux model/protocol tests with explicit checkout resources."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
env = os.environ.copy()
env.update(KEYBOARDLIGHTS_ICON_FILE=str(ROOT / "assets/app-icon.svg"), PYTHONPATH=str(ROOT / "apps/linux"),
    KEYBOARDLIGHTS_DATA_DIR=str(ROOT / "data/c989"),
    KEYBOARDLIGHTS_CORE_LIBRARY=str(ROOT / ".build/linux/lighting_core.so"))
subprocess.run(["sh", str(ROOT / "scripts/build-linux.sh")], cwd=ROOT, check=True)
subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests/linux", "-v"], cwd=ROOT, env=env, check=True)
