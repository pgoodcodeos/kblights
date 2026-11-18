#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
export KEYBOARDLIGHTS_ICON_FILE="$PWD/assets/app-icon.svg"
export PYTHONPATH="$PWD/apps/linux"
export KEYBOARDLIGHTS_DATA_DIR="$PWD/data/c989"
export KEYBOARDLIGHTS_CORE_LIBRARY="$PWD/.build/linux/lighting_core.so"
exec python3 -m keyboardlights "$@"
