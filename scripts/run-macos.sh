#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
app="$PWD/.build/macos/KeyboardLights.app"
if [ ! -x "$app/Contents/MacOS/KeyboardLights" ]; then
    echo "Build KeyboardLights first: sh scripts/build-macos.sh" >&2
    exit 1
fi

exec open -n -W "$app" --args "$@"
