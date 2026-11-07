#!/bin/bash
# Prépare le contenu du dépôt public « offre de sources » (ADR-0013, D2) : sources d'amont vérifiées, scripts, licences, notices, empreintes.
# NE PUBLIE RIEN : il écrit un dossier et une archive que le mainteneur publie lui-même (dépôt public dédié, sans le code de Kut-Studio).
#
#   packaging/ffmpeg-lgpl/make-source-offer.sh [dossier-de-sortie]       # défaut : packaging/ffmpeg-lgpl/_build/source-offer
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
# shellcheck source=versions.env
source "$HERE/versions.env"
OUT="${1:-$HERE/_build/source-offer}"
CACHE="${KUT_FFMPEG_LGPL_DIR:-$HERE/_build}/src"
rm -rf "$OUT"; mkdir -p "$OUT/sources" "$OUT/build" "$OUT/licenses" "$CACHE"

for name in FFMPEG LIBASS FREETYPE HARFBUZZ FRIBIDI DAV1D; do
  url="$(eval echo "\${${name}_URL}")"; sha="$(eval echo "\${${name}_SHA256}")"
  file="$(basename "$url")"
  [[ -f "$CACHE/$file" ]] || curl --fail --location --silent --show-error --output "$CACHE/$file" "$url"
  actual="$(shasum -a 256 "$CACHE/$file" | cut -d' ' -f1)"
  [[ "$actual" == "$sha" ]] || { echo "EMPREINTE INCORRECTE pour $file ($actual)" >&2; exit 3; }
  cp "$CACHE/$file" "$OUT/sources/$file"
done
(cd "$OUT/sources" && shasum -a 256 -- * > SHA256SUMS)

cp "$HERE/build.sh" "$HERE/versions.env" "$OUT/build/"
cp "$HERE"/licenses/* "$OUT/licenses/"
cp "$HERE/source-offer/README.md" "$OUT/README.md"
cp "$REPO/THIRD_PARTY_NOTICES.md" "$OUT/NOTICE.md"
(cd "$OUT" && tar -czf "$OUT.tar.gz" .)
echo "Prêt : $OUT (et $OUT.tar.gz) — à publier par le mainteneur ; rien n'a été envoyé."
