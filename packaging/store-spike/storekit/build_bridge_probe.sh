#!/bin/sh
# Construit BridgeProbe.app (PyInstaller, bundle com.kutstudio.app) avec libkutstore.dylib embarquée. Usage : build_bridge_probe.sh DOSSIER
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="${PYTHON:-python3}"
OUT="$1"; mkdir -p "$OUT"
[ -f "$HERE/build/libkutstore.dylib" ] || "$HERE/build.sh" >/dev/null
PYINSTALLER_CONFIG_DIR="$OUT/cfg" "$PY" -m PyInstaller --noconfirm --clean --windowed --name BridgeProbe --osx-bundle-identifier com.kutstudio.app \
  --distpath "$OUT/dist" --workpath "$OUT/work" --specpath "$OUT/spec" --paths "$HERE" \
  --add-binary "$HERE/build/libkutstore.dylib:." "$HERE/bridge_probe.py" >/dev/null 2>&1
echo "$OUT/dist/BridgeProbe.app"
