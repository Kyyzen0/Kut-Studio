"""Décodage des textures (`TextureRef.path` ou `.data`) en tableaux RGBA 8 bits, avec cache.

Le PNG est lu en natif (`kut3d.render.png`). Les autres formats (JPEG, WebP, BMP...) passent par `PySide6.QtGui`
en REPLI : l'import de Qt est paresseux et n'a lieu que dans `_decode_with_qt`, seul endroit du paquet `kut3d`
autorisé à toucher Qt (liste blanche du test d'architecture). Sans Qt, un format non PNG donne une erreur claire.
Les dimensions sont bornées avant d'allouer ; le cache est borné en nombre d'entrées et en octets.
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from pathlib import Path
from typing import Any

import numpy as np

from kut3d.errors import ImageDecodeError, short
from kut3d.mathutil import Array
from kut3d.model import TextureRef
from kut3d.render.limits import (
    MAX_IMAGE_FILE_BYTES,
    MAX_TEXTURE_PIXELS,
    MAX_TEXTURE_SIDE,
    TEXTURE_CACHE_BYTES,
    TEXTURE_CACHE_ENTRIES,
)
from kut3d.render.png import SIGNATURE, decode_png

_CACHE: OrderedDict[tuple[Any, ...], Array] = OrderedDict()
_cache_bytes = 0


def clear_texture_cache() -> None:
    """Vide le cache des textures décodées."""
    global _cache_bytes
    _CACHE.clear()
    _cache_bytes = 0


def _cache_put(key: tuple[Any, ...], image: Array) -> None:
    global _cache_bytes
    if image.nbytes > TEXTURE_CACHE_BYTES:
        return
    _CACHE[key] = image
    _cache_bytes += image.nbytes
    while len(_CACHE) > TEXTURE_CACHE_ENTRIES or _cache_bytes > TEXTURE_CACHE_BYTES:
        _, old = _CACHE.popitem(last=False)
        _cache_bytes -= old.nbytes


def _check_size(width: int, height: int) -> None:
    if (
        width < 1
        or height < 1
        or width > MAX_TEXTURE_SIDE
        or height > MAX_TEXTURE_SIDE
        or width * height > MAX_TEXTURE_PIXELS
    ):
        raise ImageDecodeError(
            f"texture trop grande ou vide ({width}x{height}, maximum {MAX_TEXTURE_SIDE} par côté)."
        )


def _decode_with_qt(data: bytes) -> Array:
    """Repli pour les formats non PNG. Import paresseux de Qt, seul dans tout `kut3d`."""
    try:
        from PySide6.QtCore import QBuffer, QByteArray, QIODevice
        from PySide6.QtGui import QImage, QImageReader
    except ImportError:
        raise ImageDecodeError(
            "format d'image non pris en charge sans Qt (seul le PNG est lu en natif) : installer PySide6."
        ) from None
    payload = QByteArray(data)
    buf = QBuffer(payload)
    buf.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buf)
    size = reader.size()
    if size.isValid():
        _check_size(size.width(), size.height())
    image = reader.read()
    if image.isNull():
        raise ImageDecodeError("image illisible (format inconnu ou fichier corrompu).")
    _check_size(image.width(), image.height())
    image = image.convertToFormat(QImage.Format.Format_RGBA8888)
    w, h, bpl = image.width(), image.height(), image.bytesPerLine()
    flat = np.frombuffer(image.constBits(), dtype=np.uint8, count=h * bpl)
    return (
        flat.reshape(h, bpl)[:, : w * 4].copy().reshape(h, w, 4)
    )  # copie : la mémoire est celle de Qt


def decode_image(data: bytes) -> Array:
    """Décode des octets d'image en (H, W, 4) uint8 : PNG en natif, le reste par Qt s'il est présent."""
    if len(data) > MAX_IMAGE_FILE_BYTES:
        raise ImageDecodeError(f"image trop volumineuse ({len(data)} octets).")
    if data[: len(SIGNATURE)] == SIGNATURE:
        return decode_png(data)
    return _decode_with_qt(data)


FINGERPRINT_SPAN = 8192  # octets lus au début et à la fin d'un fichier pour l'empreinte


def _file_fingerprint(path: Path, size: int) -> str:
    """Empreinte blake2b (16 octets) du début et de la fin du fichier, ou de tout s'il tient dans deux fenêtres.

    La date (`st_mtime_ns`) ne suffit pas : la résolution d'horloge de certains systèmes de fichiers (~15 ms sous
    Windows) et les outils qui préservent les dates (`cp -p`, rsync, archives) laissent passer deux contenus de même
    taille. Pour un PNG, la queue contient le trailer Adler-32 du flux zlib puis le CRC de IDAT : ils changent dès
    qu'un pixel change. Limite connue : une modification interne d'un autre format (JPEG via Qt), de même taille et de
    même date, qui épargne le début et la fin passe inaperçue.
    """
    digest = hashlib.blake2b(digest_size=16)
    with path.open("rb") as handle:
        digest.update(handle.read(FINGERPRINT_SPAN))
        if size > 2 * FINGERPRINT_SPAN:
            handle.seek(size - FINGERPRINT_SPAN)
            digest.update(handle.read(FINGERPRINT_SPAN))
        elif size > FINGERPRINT_SPAN:
            digest.update(handle.read(FINGERPRINT_SPAN))
    return digest.hexdigest()


def load_texture_image(ref: TextureRef) -> Array:
    """Image RGBA 8 bits (lecture seule) d'une texture, mise en cache par chemin (+ date, taille, empreinte) ou contenu."""
    path: Path | None
    if ref.data is not None:
        key: tuple[Any, ...] = (
            "data",
            len(ref.data),
            hashlib.blake2b(ref.data, digest_size=16).hexdigest(),
        )
        path = None
    elif ref.path is not None:
        path = ref.path
        try:
            st = path.stat()
        except OSError:
            raise ImageDecodeError(f"texture illisible : {short(path.name)}.") from None
        if st.st_size > MAX_IMAGE_FILE_BYTES:
            raise ImageDecodeError(
                f"texture trop volumineuse : {short(path.name)} ({st.st_size} octets)."
            )
        try:
            fingerprint = _file_fingerprint(path, st.st_size)
            resolved = str(path.resolve())
        except OSError:  # disparu ou devenu illisible entre `stat` et la lecture
            raise ImageDecodeError(f"texture illisible : {short(path.name)}.") from None
        key = ("path", resolved, st.st_mtime_ns, st.st_size, fingerprint)
    else:
        raise ImageDecodeError(f"texture {short(ref.name)} sans chemin ni données.")
    cached = _CACHE.get(key)
    if cached is not None:
        _CACHE.move_to_end(key)
        return cached
    if ref.data is not None:
        raw = ref.data
    else:
        assert path is not None
        try:
            raw = path.read_bytes()
        except OSError:
            raise ImageDecodeError(f"texture illisible : {short(ref.name)}.") from None
    image = decode_image(raw)
    image.setflags(write=False)
    _cache_put(key, image)
    return image
