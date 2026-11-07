#!/bin/bash
# FFmpeg LGPL arm64 pour le Mac App Store (ADR-0013, étape 2). Voir README.md et docs/ffmpeg-lgpl.md.
#
#   packaging/ffmpeg-lgpl/build.sh            # construit dans packaging/ffmpeg-lgpl/_build (ignoré par git)
#   KUT_FFMPEG_LGPL_DIR=/chemin packaging/ffmpeg-lgpl/build.sh
#
# Résultat : $KUT_FFMPEG_LGPL_DIR/prefix/bin/{ffmpeg,ffprobe} — à passer à KUT_STUDIO_FFMPEG_DIR (build.py l'embarque).
# Rien n'est installé hors de ce dossier, rien n'est lu dans Homebrew (PATH réduit, --disable-autodetect), aucune clé n'est utilisée.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=versions.env
source "$HERE/versions.env"

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  echo "Ce script construit pour macOS arm64 (décision D6) ; machine : $(uname -sm)." >&2
  exit 2
fi

ROOT="${KUT_FFMPEG_LGPL_DIR:-$HERE/_build}"
SRC="$ROOT/src"          # archives téléchargées (vérifiées) et sources extraites
TOOLS="$ROOT/tools"      # pkgconf, meson, ninja : outils de construction, non livrés
PREFIX="$ROOT/prefix"    # bibliothèques tierces statiques + ffmpeg/ffprobe
JOBS="${JOBS:-$(sysctl -n hw.ncpu)}"
mkdir -p "$SRC" "$TOOLS" "$PREFIX"

# Environnement hermétique : ni Homebrew ni MacPorts, ni /usr/local ne sont visibles.
export PATH="$TOOLS/bin:$TOOLS/venv/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export PKG_CONFIG_PATH="$PREFIX/lib/pkgconfig"
export PKG_CONFIG_LIBDIR="$PREFIX/lib/pkgconfig"
export MACOSX_DEPLOYMENT_TARGET="${MACOSX_DEPLOYMENT_TARGET:-14.0}"
export CFLAGS="-O2 -arch arm64 -mmacosx-version-min=$MACOSX_DEPLOYMENT_TARGET"
export CXXFLAGS="$CFLAGS"
export LDFLAGS="-arch arm64 -mmacosx-version-min=$MACOSX_DEPLOYMENT_TARGET"
# Date des sources fixée : constructions reproductibles (pas d'horodatage dans les binaires).
export SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-1761600000}"

log() { printf '\n==> %s\n' "$*"; }

fetch() { # nom
  local name="$1" url sha file
  url="$(eval echo "\${${name}_URL}")"; sha="$(eval echo "\${${name}_SHA256}")"
  file="$SRC/$(basename "$url")"
  if [[ ! -f "$file" ]]; then
    log "Téléchargement $url"
    curl --fail --location --silent --show-error --output "$file.part" "$url"
    mv "$file.part" "$file"
  fi
  local actual; actual="$(shasum -a 256 "$file" | cut -d' ' -f1)"
  if [[ "$actual" != "$sha" ]]; then
    echo "EMPREINTE INCORRECTE pour $file : attendu $sha, obtenu $actual" >&2
    rm -f "$file"; exit 3
  fi
  local dir="$SRC/$(echo "$name" | tr A-Z a-z)-$(eval echo "\${${name}_VERSION}")"
  rm -rf "$dir"; mkdir -p "$dir"
  tar -xf "$file" -C "$dir" --strip-components=1
  echo "$dir"
}

# 1. Outils de construction (non livrés).
if [[ ! -x "$TOOLS/bin/pkgconf" ]]; then
  d="$(fetch PKGCONF | tail -1)"
  log "pkgconf $PKGCONF_VERSION"
  (cd "$d" && ./configure --prefix="$TOOLS" --disable-shared --with-pkg-config-dir="$PREFIX/lib/pkgconfig" >/dev/null && make -j"$JOBS" >/dev/null && make install >/dev/null)
  ln -sf pkgconf "$TOOLS/bin/pkg-config"
fi
if [[ ! -x "$TOOLS/venv/bin/meson" ]]; then
  log "meson $MESON_VERSION + ninja $NINJA_VERSION (venv local)"
  /usr/bin/python3 -m venv "$TOOLS/venv" 2>/dev/null || python3 -m venv "$TOOLS/venv"
  "$TOOLS/venv/bin/pip" install --quiet "meson==$MESON_VERSION" "ninja==$NINJA_VERSION"
fi

# 2. Bibliothèques tierces, statiques, arm64.
log "freetype $FREETYPE_VERSION"
d="$(fetch FREETYPE | tail -1)"
(cd "$d" && ./configure --prefix="$PREFIX" --disable-shared --enable-static \
   --with-harfbuzz=no --with-png=no --with-bzip2=no --with-brotli=no --with-zlib=yes >/dev/null \
 && make -j"$JOBS" >/dev/null && make install >/dev/null)

log "harfbuzz $HARFBUZZ_VERSION"
d="$(fetch HARFBUZZ | tail -1)"
(cd "$d" && meson setup _b --prefix="$PREFIX" --libdir=lib --default-library=static --buildtype=release \
   -Dfreetype=enabled -Dglib=disabled -Dgobject=disabled -Dcairo=disabled -Dicu=disabled -Dcoretext=disabled \
   -Dtests=disabled -Ddocs=disabled -Dbenchmark=disabled -Dintrospection=disabled -Dutilities=disabled >/dev/null \
 && ninja -C _b >/dev/null && ninja -C _b install >/dev/null)

log "fribidi $FRIBIDI_VERSION"
d="$(fetch FRIBIDI | tail -1)"
(cd "$d" && meson setup _b --prefix="$PREFIX" --libdir=lib --default-library=static --buildtype=release \
   -Dtests=false -Ddocs=false -Dbin=false >/dev/null \
 && ninja -C _b >/dev/null && ninja -C _b install >/dev/null)

log "dav1d $DAV1D_VERSION"
d="$(fetch DAV1D | tail -1)"
(cd "$d" && meson setup _b --prefix="$PREFIX" --libdir=lib --default-library=static --buildtype=release \
   -Denable_tools=false -Denable_tests=false -Denable_examples=false >/dev/null \
 && ninja -C _b >/dev/null && ninja -C _b install >/dev/null)

# libass : polices du système par CoreText (aucune dépendance à fontconfig).
log "libass $LIBASS_VERSION"
d="$(fetch LIBASS | tail -1)"
(cd "$d" && ./configure --prefix="$PREFIX" --disable-shared --enable-static --disable-fontconfig --enable-coretext \
   --disable-asm --disable-require-system-font-provider >/dev/null \
 && make -j"$JOBS" >/dev/null && make install >/dev/null)

# 3. FFmpeg. --disable-autodetect : aucune bibliothèque n'est employée sans être demandée ici (rien de Homebrew ne s'infiltre).
# Ni --enable-gpl, ni --enable-nonfree, ni --enable-version3 (reste LGPL 2.1 ou ultérieure), ni x264 / x265.
log "ffmpeg $FFMPEG_VERSION"
d="$(fetch FFMPEG | tail -1)"
mkdir -p "$d/_b"
(cd "$d/_b" && ../configure --prefix="$PREFIX" --arch=arm64 --cc="clang -arch arm64" \
   --enable-static --disable-shared --disable-autodetect \
   --disable-gpl --disable-nonfree --disable-doc --disable-debug --disable-ffplay \
   --enable-videotoolbox --enable-audiotoolbox --enable-libass --enable-libdav1d \
   --enable-zlib --enable-iconv \
   --extra-cflags="-I$PREFIX/include" --extra-ldflags="-L$PREFIX/lib" \
   --pkg-config-flags="--static" > "$ROOT/configure.log" 2>&1 \
 && make -j"$JOBS" > "$ROOT/make.log" 2>&1 && make install > "$ROOT/install.log" 2>&1) || {
   echo "Échec de FFmpeg : voir $ROOT/configure.log, make.log (et $d/_b/ffbuild/config.log)" >&2; exit 4; }

log "Terminé : $PREFIX/bin/ffmpeg"
"$PREFIX/bin/ffmpeg" -hide_banner -version | head -3
echo "Utilisation : export KUT_STUDIO_FFMPEG_DIR=$PREFIX/bin"
