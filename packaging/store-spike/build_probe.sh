#!/bin/sh
# Construit Probe.app (PyInstaller, bundle com.kutstudio.app) dans le dossier donné, avec un exécutable auxiliaire.
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="${PYTHON:-python3}"
OUT="$1"; mkdir -p "$OUT"
PYINSTALLER_CONFIG_DIR="$OUT/cfg" "$PY" -m PyInstaller --noconfirm --clean --windowed --name Probe --osx-bundle-identifier com.kutstudio.app \
  --distpath "$OUT/dist" --workpath "$OUT/work" --specpath "$OUT/spec" "$HERE/probe.py" >/dev/null 2>&1
echo "$OUT/dist/Probe.app"
