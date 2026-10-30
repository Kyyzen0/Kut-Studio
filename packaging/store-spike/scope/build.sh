#!/bin/sh
# Construit libkutscope.dylib (arm64, macOS 13+) dans build/. Usage : build.sh [dossier de sortie]
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="${1:-$HERE/build}"; mkdir -p "$OUT"
swiftc -emit-library -O -module-name KutScopeBridge -target arm64-apple-macos13.0 -swift-version 5 \
  -Xlinker -install_name -Xlinker @rpath/libkutscope.dylib "$HERE/KutScopeBridge.swift" -o "$OUT/libkutscope.dylib"
echo "$OUT/libkutscope.dylib"
