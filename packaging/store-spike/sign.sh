#!/bin/sh
# Spike ADR-0013 étape 1 : signe un bundle Kut-Studio.app binaire par binaire, bac à sable + runtime renforcé.
# Usage : sign.sh BUNDLE.app ENTITLEMENTS_PRINCIPAL.plist [IDENTITE]
# Aucun --deep. Ordre : bibliothèques / modules, exécutables auxiliaires (inherit), exécutable principal, puis le bundle.
set -eu
APP="$1"; MAIN_ENT="$2"; MAIN="${KUT_MAIN_EXE:-Kut-Studio}"
ID="${3:-Apple Development: audryk.chesse@outlook.fr (9Q736F8949)}"
HERE="$(cd "$(dirname "$0")" && pwd)"
SIGN="codesign --force --timestamp=none --options runtime --sign"

# 1. Tous les Mach-O non exécutables-principaux (dylib, .so, frameworks Qt, ffmpeg...). Les liens symboliques sont ignorés.
find "$APP/Contents" -type f ! -path "*/_CodeSignature/*" -print0 | while IFS= read -r -d '' f; do
  if file -b "$f" | grep -q "Mach-O"; then
    case "$f" in
      "$APP/Contents/MacOS/$MAIN") continue ;;
      */MacOS/probe-helper|*/bin/ffmpeg|*/bin/ffprobe) $SIGN "$ID" --entitlements "$HERE/entitlements/inherit.plist" "$f" ;;
      *) $SIGN "$ID" "$f" ;;
    esac
  fi
done
# 2. Exécutable principal puis bundle (le bundle porte les droits complets).
$SIGN "$ID" --entitlements "$MAIN_ENT" "$APP/Contents/MacOS/$MAIN"
$SIGN "$ID" --entitlements "$MAIN_ENT" "$APP"
codesign --verify --strict --verbose=2 "$APP"
