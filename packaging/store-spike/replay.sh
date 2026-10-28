#!/bin/sh
# Rejoue les mesures automatisables du spike bac à sable (ADR-0013 étape 1). macOS, Apple Silicon, Xcode, certificat
# « Apple Development » dans le trousseau, venv du dépôt (PyInstaller + PySide6), FFmpeg Homebrew.
# Usage : packaging/store-spike/replay.sh        (variables : SPIKE_DIR, PYTHON, IDENTITY)
# N'écrit jamais dans la vraie configuration : l'application sandboxée a son propre conteneur (~/Library/Containers/com.kutstudio.app).
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
SPIKE_DIR="${SPIKE_DIR:-/private/tmp/kut-store-spike}"
PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
export PYTHON
IDENTITY="${IDENTITY:-Apple Development: audryk.chesse@outlook.fr (9Q736F8949)}"
APP="$SPIKE_DIR/Kut-Studio.app"; PROBE="$SPIKE_DIR/Probe.app"; OUT="$SPIKE_DIR/out"
CONTAINER="$HOME/Library/Containers/com.kutstudio.app/Data"
mkdir -p "$SPIKE_DIR" "$OUT"

echo "== 0. Coquille Xcode (crée l'App ID / le conteneur ; signature automatique, équipe 3SZ5D643QC)"
(cd "$HERE/shell" && xcodebuild -project KutShell.xcodeproj -scheme KutShell -configuration Debug \
  -derivedDataPath "$SPIKE_DIR/shell-dd" -allowProvisioningUpdates build | tail -1)

echo "== 1. Construction de Kut-Studio.app (build.py inchangé) et de Probe.app"
(cd "$ROOT" && "$PYTHON" build.py >"$SPIKE_DIR/build.log" 2>&1)
rm -rf "$APP"; cp -R "$ROOT/dist/Kut-Studio.app" "$APP"
"$PYTHON" "$HERE/bundle_ffmpeg.py" "$APP/Contents/Frameworks/bin"          # FFmpeg Homebrew autonome (jouet, GPL)
"$PYTHON" "$HERE/make_testdata.py" "$APP/Contents/Resources/spikedata"    # projet + média lisibles dans le bundle
"$HERE/build_probe.sh" "$SPIKE_DIR/probe-build" >/dev/null
rm -rf "$PROBE"; cp -R "$SPIKE_DIR/probe-build/dist/Probe.app" "$PROBE"
cp "$PROBE/Contents/MacOS/Probe" "$PROBE/Contents/MacOS/probe-helper"
"$PYTHON" "$HERE/bundle_ffmpeg.py" "$PROBE/Contents/Frameworks/bin"

echo "== 2. Signature binaire par binaire (runtime renforcé + app-sandbox)"
for ENT in main main-no-ume; do
  "$HERE/sign.sh" "$APP" "$HERE/entitlements/$ENT.plist" "$IDENTITY" >/dev/null
  KUT_MAIN_EXE=Probe "$HERE/sign.sh" "$PROBE" "$HERE/entitlements/$ENT.plist" "$IDENTITY" >/dev/null

  echo "== 3. Mesures avec entitlements/$ENT.plist"
  : >"$OUT/probe-$ENT.out"
  open -W -n --stdout "$OUT/probe-$ENT.out" --stderr "$OUT/probe-$ENT.err" \
    --env KUT_PROBE_HELPER="$PROBE/Contents/MacOS/probe-helper" "$PROBE"
  cat "$OUT/probe-$ENT.out"
  : >"$OUT/export-$ENT.out"
  open -W -n --stdout "$OUT/export-$ENT.out" --stderr "$OUT/export-$ENT.err" --env KUT_STUDIO_UPDATE_CHECK=off \
    "$APP" --args export "$APP/Contents/Resources/spikedata/demo.kut" -o "$CONTAINER/tmp/out-$ENT.mp4" --overwrite --json
  echo "export CLI ($ENT) :"; cat "$OUT/export-$ENT.out"
  : >"$OUT/smoke-$ENT.err"
  open -W -n --stdout "$OUT/smoke-$ENT.out" --stderr "$OUT/smoke-$ENT.err" --env KUT_STUDIO_UPDATE_CHECK=off \
    --env KUT_STUDIO_LOG_DIR=/dev/null/x --env KUT_STUDIO_CACHE_DIR="$CONTAINER/Library/Caches/replay-$ENT" \
    "$APP" --args --smoke-test
  echo "smoke test ($ENT) :"; grep -E "gpu|Smoke|Traceback|rror" "$OUT/smoke-$ENT.err" || true
done
echo "== 4. Refus du bac à sable (journal)"
/usr/bin/log show --last 15m --style compact --predicate 'eventMessage CONTAINS "Sandbox:" AND (eventMessage CONTAINS "Kut-Studio" OR eventMessage CONTAINS "Probe")' \
  | grep "deny(" | sed 's/^.*Sandbox: //' | sort | uniq -c | sort -rn | head -30
echo "Reste à la main (boîtes de dialogue, voix off) : voir README.md, section « À faire à la main »."
