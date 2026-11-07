#!/bin/sh
# Lance les tests XCTest du pont (hôte applicatif vide + SKTestSession). Usage : run_tests.sh [dossier DerivedData]
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
export TEST_RUNNER_KUT_BRIDGE_DYLIB="${KUT_BRIDGE_DYLIB:-$HERE/../build/libkutstore.dylib}"
export TEST_RUNNER_KUT_STOREKIT_FILE="$HERE/../Kut-Studio.storekit"
exec xcodebuild test -project "$HERE/KutStoreHost.xcodeproj" -scheme KutStoreBridgeTests -destination 'platform=macOS' \
  -derivedDataPath "${1:-$HERE/../build/xcode}"
