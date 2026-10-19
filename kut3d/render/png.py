"""PNG natif (zlib, sans Qt ni Pillow) : encodeur RGBA 8 bits et décodeur complet.

Le décodeur lit les 5 types de couleur (gris, RGB, palette, gris+alpha, RGBA), les profondeurs 1, 2, 4, 8 et 16
bits, les 5 filtres et l'entrelacement Adam7, ainsi que `tRNS`. Il vérifie les CRC, borne les dimensions AVANT
d'allouer et refuse les bombes de décompression (la taille décompressée attendue vient de l'en-tête). Le résultat
est toujours (H, W, 4) uint8 ; une profondeur de 16 bits est ramenée à 8 bits par `(v + 128) // 257`.
Limite connue : les filtres « moyenne » et « Paeth » sont décodés en Python pur (plus lents sur de grandes images).
"""

from __future__ import annotations

import struct
import zlib

import numpy as np

from kut3d.errors import ImageDecodeError
from kut3d.mathutil import Array
from kut3d.render.limits import (
    MAX_IMAGE_FILE_BYTES,
    MAX_TEXTURE_PIXELS,
    MAX_TEXTURE_SIDE,
)

SIGNATURE = b"\x89PNG\r\n\x1a\n"
_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
_DEPTHS = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
_ADAM7 = (
    (0, 0, 8, 8),
    (4, 0, 8, 8),
    (0, 4, 4, 8),
    (2, 0, 4, 4),
    (0, 2, 2, 4),
    (1, 0, 2, 2),
    (0, 1, 1, 2),
)


def _chunk(tag: bytes, data: bytes) -> bytes:
    body = tag + data
    return (
        struct.pack(">I", len(data))
        + body
        + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
    )


def encode_png(image: Array, level: int = 6) -> bytes:
    """Encode un tableau (H, W, 4) uint8 en PNG RGBA 8 bits (filtre 0)."""
    arr = np.asarray(image)
    if arr.dtype != np.uint8 or arr.ndim != 3 or arr.shape[2] != 4:
        raise ValueError("encode_png : un tableau (H, W, 4) uint8 est attendu.")
    h, w, _ = arr.shape
    if h < 1 or w < 1:
        raise ValueError("encode_png : image vide.")
    raw = np.zeros((h, 1 + w * 4), dtype=np.uint8)
    raw[:, 1:] = arr.reshape(h, w * 4)
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    return (
        SIGNATURE
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(raw.tobytes(), level))
        + _chunk(b"IEND", b"")
    )


def _fail(why: str) -> ImageDecodeError:
    return ImageDecodeError(f"PNG invalide : {why}.")


def _read_chunks(data: bytes) -> dict[bytes, list[bytes]]:
    pos = len(SIGNATURE)
    chunks: dict[bytes, list[bytes]] = {}
    first = True
    while True:
        if pos + 8 > len(data):
            raise _fail("fichier tronqué (pas de bloc IEND)")
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        tag = data[pos + 4 : pos + 8]
        end = pos + 8 + length
        if length > 0x7FFFFFFF or end + 4 > len(data):
            raise _fail("fichier tronqué (bloc incomplet)")
        body = data[pos + 8 : end]
        crc = struct.unpack(">I", data[end : end + 4])[0]
        if zlib.crc32(tag + body) & 0xFFFFFFFF != crc:
            raise _fail("somme de contrôle (CRC) incorrecte")
        if first and tag != b"IHDR":
            raise _fail("le premier bloc n'est pas IHDR")
        first = False
        if tag == b"IEND":
            return chunks
        chunks.setdefault(tag, []).append(body)
        pos = end + 4


def _row_bytes(width: int, depth: int, channels: int) -> int:
    return (width * channels * depth + 7) // 8


def _paeth_row(line: list[int], prev: list[int], bpp: int) -> list[int]:
    out = [0] * len(line)
    for i, x in enumerate(line):
        if i >= bpp:
            a, c = out[i - bpp], prev[i - bpp]
        else:
            a = c = 0
        b = prev[i]
        p = a + b - c
        pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
        pr = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
        out[i] = (x + pr) & 255
    return out


def _average_row(line: list[int], prev: list[int], bpp: int) -> list[int]:
    out = [0] * len(line)
    for i, x in enumerate(line):
        a = out[i - bpp] if i >= bpp else 0
        out[i] = (x + ((a + prev[i]) >> 1)) & 255
    return out


def _unfilter(raw: bytes, pos: int, height: int, row_bytes: int, bpp: int) -> Array:
    """Défiltre `height` lignes (1 octet de filtre + `row_bytes`) lues à partir de `pos` : (height, row_bytes)."""
    out = np.empty((height, row_bytes), dtype=np.uint8)
    prev = np.zeros(row_bytes, dtype=np.uint8)
    for y in range(height):
        base = pos + y * (row_bytes + 1)
        ftype = raw[base]
        line = np.frombuffer(raw, dtype=np.uint8, count=row_bytes, offset=base + 1)
        if ftype == 0:
            cur = line.copy()
        elif ftype == 1:
            cur = np.cumsum(line.reshape(-1, bpp), axis=0, dtype=np.uint8).reshape(-1)
        elif ftype == 2:
            cur = line + prev
        elif ftype == 3:
            cur = np.array(_average_row(line.tolist(), prev.tolist(), bpp), np.uint8)
        elif ftype == 4:
            cur = np.array(_paeth_row(line.tolist(), prev.tolist(), bpp), np.uint8)
        else:
            raise _fail(f"type de filtre {ftype} inconnu")
        out[y] = cur
        prev = out[y]
    return out


def _samples(rows: Array, width: int, depth: int, channels: int) -> Array:
    """Lignes défiltrées -> valeurs (h, width, channels) uint16 (brutes, non mises à l'échelle)."""
    h = rows.shape[0]
    if depth == 8:
        return rows[:, : width * channels].reshape(h, width, channels).astype(np.uint16)
    if depth == 16:
        be = rows[:, : width * channels * 2].copy().view(">u2")
        return be.reshape(h, width, channels).astype(np.uint16)
    bits = np.unpackbits(rows, axis=1)[:, : width * depth].reshape(h, width, depth)
    weights = (1 << np.arange(depth - 1, -1, -1)).astype(np.uint16)
    return (bits.astype(np.uint16) @ weights).reshape(h, width, 1)


def _expected_size(width: int, height: int, depth: int, ch: int, adam7: bool) -> int:
    if not adam7:
        return height * (1 + _row_bytes(width, depth, ch))
    total = 0
    for xs, ys, dx, dy in _ADAM7:
        pw = (width - xs + dx - 1) // dx if width > xs else 0
        ph = (height - ys + dy - 1) // dy if height > ys else 0
        if pw and ph:
            total += ph * (1 + _row_bytes(pw, depth, ch))
    return total


def decode_png(data: bytes) -> Array:
    """Décode un PNG en (H, W, 4) uint8 RGBA (alpha droit). Lève `ImageDecodeError` si le fichier est invalide."""
    if len(data) > MAX_IMAGE_FILE_BYTES:
        raise _fail(f"fichier trop volumineux ({len(data)} octets)")
    if data[: len(SIGNATURE)] != SIGNATURE:
        raise _fail("signature absente")
    chunks = _read_chunks(data)
    if b"IHDR" not in chunks or len(chunks[b"IHDR"][0]) != 13:
        raise _fail("en-tête IHDR absent ou incorrect")
    width, height, depth, ctype, comp, filt, interlace = struct.unpack(
        ">IIBBBBB", chunks[b"IHDR"][0]
    )
    if ctype not in _CHANNELS or depth not in _DEPTHS[ctype]:
        raise _fail(f"type de couleur {ctype} / profondeur {depth} non pris en charge")
    if comp != 0 or filt != 0 or interlace not in (0, 1):
        raise _fail("méthode de compression, de filtre ou d'entrelacement inconnue")
    if width < 1 or height < 1:
        raise _fail("dimensions nulles")
    if (
        width > MAX_TEXTURE_SIDE
        or height > MAX_TEXTURE_SIDE
        or width * height > MAX_TEXTURE_PIXELS
    ):
        raise _fail(
            f"image trop grande ({width}x{height}, maximum {MAX_TEXTURE_SIDE} par côté "
            f"et {MAX_TEXTURE_PIXELS} pixels)"
        )
    ch = _CHANNELS[ctype]
    idat = b"".join(chunks.get(b"IDAT", []))
    if not idat:
        raise _fail("aucune donnée d'image (IDAT)")
    expected = _expected_size(width, height, depth, ch, interlace == 1)
    inflater = zlib.decompressobj()
    try:
        raw = inflater.decompress(idat, expected + 1)
    except zlib.error:
        raise _fail("données compressées corrompues") from None
    if len(raw) > expected or inflater.unconsumed_tail:
        raise _fail("données décompressées plus grandes que prévu (bombe ?)")
    if len(raw) < expected:
        raise _fail("données d'image tronquées")
    bpp = max(1, ch * depth // 8)
    if interlace == 0:
        rows = _unfilter(raw, 0, height, _row_bytes(width, depth, ch), bpp)
        values = _samples(rows, width, depth, ch)
    else:
        values = np.zeros((height, width, ch), dtype=np.uint16)
        pos = 0
        for xs, ys, dx, dy in _ADAM7:
            pw = (width - xs + dx - 1) // dx if width > xs else 0
            ph = (height - ys + dy - 1) // dy if height > ys else 0
            if not (pw and ph):
                continue
            rb = _row_bytes(pw, depth, ch)
            sub = _unfilter(raw, pos, ph, rb, bpp)
            values[ys::dy, xs::dx] = _samples(sub, pw, depth, ch)
            pos += ph * (1 + rb)
    return _to_rgba(values, depth, ctype, chunks)


def _to_rgba(
    values: Array, depth: int, ctype: int, chunks: dict[bytes, list[bytes]]
) -> Array:
    h, w, _ = values.shape
    trns = chunks.get(b"tRNS", [b""])[0]
    out = np.empty((h, w, 4), dtype=np.uint8)
    out[..., 3] = 255

    def to8(v: Array) -> Array:
        if depth == 16:
            return ((v.astype(np.uint32) + 128) // 257).astype(np.uint8)
        if depth < 8:
            return (v.astype(np.uint32) * 255 // ((1 << depth) - 1)).astype(np.uint8)
        return v.astype(np.uint8)

    if ctype == 3:
        plte = chunks.get(b"PLTE", [b""])[0]
        if not plte or len(plte) % 3 or len(plte) > 768:
            raise _fail("palette absente ou incorrecte")
        pal = np.zeros((256, 4), dtype=np.uint8)
        n = len(plte) // 3
        pal[:n, :3] = np.frombuffer(plte, dtype=np.uint8).reshape(n, 3)
        pal[:, 3] = 255
        for i, a in enumerate(trns[:256]):
            pal[i, 3] = a
        idx = values[..., 0]
        if int(idx.max()) >= n:
            raise _fail("indice de palette hors limites")
        return pal[idx]
    if ctype == 0:
        g = to8(values[..., 0])
        out[..., :3] = g[..., None]
        if len(trns) >= 2:
            key = struct.unpack(">H", trns[:2])[0]
            out[..., 3] = np.where(values[..., 0] == key, 0, 255)
    elif ctype == 2:
        out[..., :3] = to8(values)
        if len(trns) >= 6:
            key = np.array(struct.unpack(">HHH", trns[:6]), dtype=np.uint16)
            out[..., 3] = np.where((values == key).all(axis=2), 0, 255)
    elif ctype == 4:
        g = to8(values[..., 0])
        out[..., :3] = g[..., None]
        out[..., 3] = to8(values[..., 1])
    else:
        out[...] = to8(values)
    return out
