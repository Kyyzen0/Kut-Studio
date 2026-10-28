#!/bin/sh
# Construit ProbeGui.app (PyInstaller + PySide6, bundle com.kutstudio.app, déclare .kut) dans le dossier donné.
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="${PYTHON:-python3}"
OUT="$1"; mkdir -p "$OUT"
PYINSTALLER_CONFIG_DIR="$OUT/cfg" "$PY" -m PyInstaller --noconfirm --clean --windowed --name ProbeGui --osx-bundle-identifier com.kutstudio.app \
  --paths "$HERE" --distpath "$OUT/dist" --workpath "$OUT/work" --specpath "$OUT/spec" "$HERE/probe_gui.py" >/dev/null 2>&1
"$PY" - "$OUT/dist/ProbeGui.app/Contents/Info.plist" <<'PY'
import plistlib, sys
p = sys.argv[1]
d = plistlib.load(open(p, "rb"))
d["CFBundleDocumentTypes"] = [{"CFBundleTypeName": "Projet", "CFBundleTypeExtensions": ["kut"], "CFBundleTypeRole": "Editor", "LSHandlerRank": "Owner"}]
plistlib.dump(d, open(p, "wb"))
PY
echo "$OUT/dist/ProbeGui.app"
