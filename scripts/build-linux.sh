#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
mkdir -p .build/linux
keyboardlights_core_tmp=$(mktemp .build/linux/lighting_core.XXXXXX.so)
trap 'rm -f -- "$keyboardlights_core_tmp"' EXIT
cc -std=c11 -O2 -Wall -Wextra -Werror -fPIC -shared core/Core.c -o "$keyboardlights_core_tmp"
mv -f -- "$keyboardlights_core_tmp" .build/linux/lighting_core.so
trap - EXIT
