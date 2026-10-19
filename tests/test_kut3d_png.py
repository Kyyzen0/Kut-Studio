"""Décodeur et encodeur PNG natifs, décodage d'images (cache, repli Qt), bornes et fichiers corrompus."""

from __future__ import annotations

import os
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import numpy as np
import pytest

from kut3d import ImageDecodeError, RenderError
from kut3d.model import TextureRef
from kut3d.render import images
from kut3d.render.png import SIGNATURE, decode_png, encode_png

ROOT = Path(__file__).resolve().parent.parent
CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
ADAM7 = (
    (0, 0, 8, 8),
    (4, 0, 8, 8),
    (0, 4, 4, 8),
    (2, 0, 4, 4),
    (0, 2, 2, 4),
    (1, 0, 2, 2),
    (0, 1, 1, 2),
)


def chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    return a if pa <= pb and pa <= pc else (b if pb <= pc else c)


def apply_filter(ftype: int, line: bytes, prev: bytes, bpp: int) -> bytes:
    """Filtre direct d'une ligne (l'inverse de ce que fait le décodeur), écrit à la main d'après la spec."""
    out = bytearray(len(line))
    for i, x in enumerate(line):
        a = line[i - bpp] if i >= bpp else 0
        b = prev[i]
        c = prev[i - bpp] if i >= bpp else 0
        pred = (0, a, b, (a + b) // 2, _paeth(a, b, c))[ftype]
        out[i] = (x - pred) & 255
    return bytes(out)


def pack_rows(values: np.ndarray, depth: int) -> list[bytes]:
    """(h, w, ch) valeurs entières -> lignes d'octets selon la profondeur de bits."""
    rows = []
    for y in range(values.shape[0]):
        flat = values[y].reshape(-1)
        if depth == 8:
            rows.append(bytes(flat.astype(np.uint8).tolist()))
        elif depth == 16:
            rows.append(flat.astype(">u2").tobytes())
        else:
            bits = "".join(format(int(v), f"0{depth}b") for v in flat)
            bits += "0" * (-len(bits) % 8)
            rows.append(bytes(int(bits[i : i + 8], 2) for i in range(0, len(bits), 8)))
    return rows


def make_png(
    values: np.ndarray, ctype: int, depth: int, *, filters: tuple[int, ...] = (0,), interlace: bool = False,
    plte: bytes | None = None, trns: bytes | None = None,
) -> bytes:  # fmt: skip
    h, w, ch = values.shape
    assert ch == CHANNELS[ctype]
    bpp = max(1, ch * depth // 8)
    raw = bytearray()
    passes = ADAM7 if interlace else ((0, 0, 1, 1),)
    row_no = 0
    for xs, ys, dx, dy in passes:
        sub = values[ys::dy, xs::dx]
        if sub.shape[0] == 0 or sub.shape[1] == 0:
            continue
        prev = bytes(len(pack_rows(sub[:1], depth)[0]))
        for line in pack_rows(sub, depth):
            ftype = filters[row_no % len(filters)]
            raw += bytes([ftype]) + apply_filter(ftype, line, prev, bpp)
            prev = line
            row_no += 1
    out = SIGNATURE + chunk(
        b"IHDR",
        struct.pack(">IIBBBBB", w, h, depth, ctype, 0, 0, 1 if interlace else 0),
    )
    if plte is not None:
        out += chunk(b"PLTE", plte)
    if trns is not None:
        out += chunk(b"tRNS", trns)
    return out + chunk(b"IDAT", zlib.compress(bytes(raw))) + chunk(b"IEND", b"")


def _random(h: int, w: int, ch: int, maxval: int, seed: int = 1) -> np.ndarray:
    return np.random.default_rng(seed).integers(
        0, maxval + 1, size=(h, w, ch), dtype=np.int64
    )


@pytest.mark.parametrize("ftype", [0, 1, 2, 3, 4])
@pytest.mark.parametrize("depth", [8, 16])
def test_rgba_every_filter_and_both_depths(ftype: int, depth: int) -> None:
    vals = _random(9, 7, 4, 255 if depth == 8 else 65535, seed=ftype)
    png = make_png(vals, 6, depth, filters=(ftype,))
    out = decode_png(png)
    expected = vals if depth == 8 else (vals + 128) // 257
    assert out.shape == (9, 7, 4) and out.dtype == np.uint8
    assert np.array_equal(out, expected.astype(np.uint8))


def test_mixed_filters_on_one_image() -> None:
    vals = _random(20, 11, 3, 255)
    out = decode_png(make_png(vals, 2, 8, filters=(0, 1, 2, 3, 4, 4, 3, 2, 1)))
    assert np.array_equal(out[..., :3], vals.astype(np.uint8))
    assert (out[..., 3] == 255).all()


def test_grayscale_and_gray_alpha() -> None:
    g = _random(6, 5, 1, 255)
    out = decode_png(make_png(g, 0, 8, filters=(4,)))
    assert np.array_equal(out[..., 0], g[..., 0].astype(np.uint8))
    assert np.array_equal(out[..., 1], out[..., 0]) and (out[..., 3] == 255).all()
    ga = _random(6, 5, 2, 255)
    out = decode_png(make_png(ga, 4, 8, filters=(1,)))
    assert np.array_equal(out[..., 0], ga[..., 0].astype(np.uint8))
    assert np.array_equal(out[..., 3], ga[..., 1].astype(np.uint8))
    g16 = _random(3, 3, 1, 65535)
    out = decode_png(make_png(g16, 0, 16))
    assert np.array_equal(out[..., 0], ((g16[..., 0] + 128) // 257).astype(np.uint8))


@pytest.mark.parametrize("depth", [1, 2, 4])
def test_low_bit_depth_gray_is_scaled_to_8_bits(depth: int) -> None:
    g = _random(5, 11, 1, (1 << depth) - 1)
    out = decode_png(make_png(g, 0, depth))
    assert np.array_equal(
        out[..., 0], (g[..., 0] * 255 // ((1 << depth) - 1)).astype(np.uint8)
    )


@pytest.mark.parametrize("depth", [1, 2, 4, 8])
def test_palette_with_transparency(depth: int) -> None:
    n = min(1 << depth, 5)
    pal = np.array(
        [[10, 20, 30], [200, 100, 50], [1, 2, 3], [255, 0, 255], [9, 9, 9]], np.uint8
    )[:n]
    idx = _random(6, 9, 1, n - 1)
    png = make_png(idx, 3, depth, plte=pal.tobytes(), trns=bytes([0, 128]))
    out = decode_png(png)
    assert np.array_equal(out[..., :3], pal[idx[..., 0]])
    alpha = np.full(n, 255, np.uint8)
    alpha[:2] = (0, 128)
    assert np.array_equal(out[..., 3], alpha[idx[..., 0]])


def test_gray_and_rgb_color_key_transparency() -> None:
    g = np.array([[[0], [7]], [[7], [1]]])
    out = decode_png(make_png(g, 0, 8, trns=struct.pack(">H", 7)))
    assert out[..., 3].tolist() == [[255, 0], [0, 255]]
    rgb = np.array([[[1, 2, 3], [4, 5, 6]]])
    out = decode_png(make_png(rgb, 2, 8, trns=struct.pack(">HHH", 4, 5, 6)))
    assert out[..., 3].tolist() == [[255, 0]]


@pytest.mark.parametrize("size", [(1, 1), (3, 5), (8, 8), (13, 9), (4, 17)])
@pytest.mark.parametrize("ctype, depth", [(6, 8), (2, 8), (0, 4), (6, 16)])
def test_adam7_interlaced_equals_non_interlaced(
    size: tuple[int, int], ctype: int, depth: int
) -> None:
    h, w = size
    vals = _random(h, w, CHANNELS[ctype], (1 << depth) - 1, seed=h * 31 + w)
    plain = decode_png(make_png(vals, ctype, depth, filters=(0, 1, 2, 3, 4)))
    inter = decode_png(
        make_png(vals, ctype, depth, filters=(4, 3, 2, 1, 0), interlace=True)
    )
    assert np.array_equal(plain, inter)


def test_encoder_round_trip_and_validation() -> None:
    img = _random(5, 6, 4, 255).astype(np.uint8)
    assert np.array_equal(decode_png(encode_png(img)), img)
    with pytest.raises(ValueError):
        encode_png(img.astype(np.float32))
    with pytest.raises(ValueError):
        encode_png(img[..., :3])


# --- fichiers invalides ---------------------------------------------------------------------------------------


def _good() -> bytes:
    return encode_png(_random(8, 8, 4, 255).astype(np.uint8))


def test_truncated_and_corrupt_files_raise_a_clean_error() -> None:
    good = _good()
    for cut in (4, 20, 40, len(good) - 13, len(good) - 1):
        with pytest.raises(ImageDecodeError):
            decode_png(good[:cut])
    corrupt = bytearray(good)
    corrupt[40] ^= 0xFF  # un octet du bloc IDAT : le CRC ne correspond plus
    with pytest.raises(ImageDecodeError, match="CRC"):
        decode_png(bytes(corrupt))
    with pytest.raises(ImageDecodeError, match="signature"):
        decode_png(b"GIF89a" + good[6:])
    assert issubclass(ImageDecodeError, RenderError) and issubclass(
        ImageDecodeError, ValueError
    )


def test_idat_with_wrong_size_or_bad_stream_is_refused() -> None:
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 8, 6, 0, 0, 0))
    short = (
        SIGNATURE
        + ihdr
        + chunk(b"IDAT", zlib.compress(b"\x00" * 10))
        + chunk(b"IEND", b"")
    )
    with pytest.raises(ImageDecodeError, match="tronqu"):
        decode_png(short)
    garbage = SIGNATURE + ihdr + chunk(b"IDAT", b"pas du zlib") + chunk(b"IEND", b"")
    with pytest.raises(ImageDecodeError, match="corrompu"):
        decode_png(garbage)
    no_data = SIGNATURE + ihdr + chunk(b"IEND", b"")
    with pytest.raises(ImageDecodeError, match="IDAT"):
        decode_png(no_data)
    bad_filter = (
        SIGNATURE
        + ihdr
        + chunk(b"IDAT", zlib.compress((b"\x09" + b"\x00" * 16) * 4))
        + chunk(b"IEND", b"")
    )
    with pytest.raises(ImageDecodeError, match="filtre"):
        decode_png(bad_filter)


def test_decompression_bomb_is_refused_without_inflating_it() -> None:
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
    bomb = zlib.compress(
        bytes(200_000_000), 9
    )  # 200 Mo de zéros pour quelques centaines d'octets
    assert len(bomb) < 400_000
    png = SIGNATURE + ihdr + chunk(b"IDAT", bomb) + chunk(b"IEND", b"")
    with pytest.raises(ImageDecodeError, match="plus grandes"):
        decode_png(png)


def test_huge_dimensions_are_refused_before_any_allocation() -> None:
    for w, h in ((100_000, 100_000), (16385, 1), (16384, 16384)):
        ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
        with pytest.raises(ImageDecodeError, match="trop grande") as err:
            decode_png(SIGNATURE + ihdr + chunk(b"IDAT", b"x") + chunk(b"IEND", b""))
        assert len(str(err.value)) < 300
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 0, 5, 8, 6, 0, 0, 0))
    with pytest.raises(ImageDecodeError, match="nulles"):
        decode_png(SIGNATURE + ihdr + chunk(b"IEND", b""))


def test_unsupported_header_values_are_refused() -> None:
    for ctype, depth, comp, inter in (
        (1, 8, 0, 0),
        (2, 4, 0, 0),
        (6, 8, 1, 0),
        (6, 8, 0, 2),
    ):
        ihdr = chunk(
            b"IHDR", struct.pack(">IIBBBBB", 2, 2, depth, ctype, comp, 0, inter)
        )
        with pytest.raises(ImageDecodeError):
            decode_png(
                SIGNATURE
                + ihdr
                + chunk(b"IDAT", zlib.compress(b""))
                + chunk(b"IEND", b"")
            )


def test_palette_errors() -> None:
    idx = np.array([[[0], [3]]])
    with pytest.raises(ImageDecodeError, match="palette"):
        decode_png(make_png(idx, 3, 8))
    with pytest.raises(ImageDecodeError, match="hors limites"):
        decode_png(make_png(idx, 3, 8, plte=bytes([1, 2, 3, 4, 5, 6])))


def test_oversized_file_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("kut3d.render.png.MAX_IMAGE_FILE_BYTES", 10)
    with pytest.raises(ImageDecodeError, match="volumineux"):
        decode_png(_good())


# --- décodage d'images, cache, repli Qt -------------------------------------------------------------------------


def test_load_texture_image_from_data_and_path_with_cache(tmp_path: Path) -> None:
    images.clear_texture_cache()
    img = _random(4, 4, 4, 255).astype(np.uint8)
    ref = TextureRef("a", data=encode_png(img))
    first = images.load_texture_image(ref)
    assert np.array_equal(first, img)
    assert (
        images.load_texture_image(TextureRef("autre nom", data=encode_png(img)))
        is first
    )  # même contenu
    with pytest.raises(ValueError):
        first[0, 0, 0] = 1  # lecture seule
    path = tmp_path / "t.png"
    path.write_bytes(encode_png(img))
    by_path = images.load_texture_image(TextureRef("p", path=path))
    assert images.load_texture_image(TextureRef("p", path=path)) is by_path
    other = (255 - img).astype(np.uint8)
    path.write_bytes(
        encode_png(other)
    )  # fichier modifié : rechargement (rechargement à chaud)
    assert np.array_equal(images.load_texture_image(TextureRef("p", path=path)), other)


def test_rewritten_file_with_same_size_and_same_mtime_is_reloaded(
    tmp_path: Path,
) -> None:
    """Fenêtre d'horloge grossière (Windows), `cp -p`, rsync : taille ET date identiques, contenu différent."""
    images.clear_texture_cache()
    rng = np.random.default_rng(3)
    first = rng.integers(0, 256, (16, 16, 4), dtype=np.uint8)
    second = rng.integers(0, 256, (16, 16, 4), dtype=np.uint8)
    path = tmp_path / "same.png"
    path.write_bytes(encode_png(first))
    before = path.stat()
    assert np.array_equal(images.load_texture_image(TextureRef("p", path=path)), first)
    data = encode_png(second)
    assert len(data) == before.st_size  # bruit incompressible : même taille
    path.write_bytes(data)
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    after = path.stat()
    assert (after.st_size, after.st_mtime_ns) == (before.st_size, before.st_mtime_ns)
    assert np.array_equal(images.load_texture_image(TextureRef("p", path=path)), second)


def test_large_file_fingerprint_sees_a_change_in_the_tail(tmp_path: Path) -> None:
    images.clear_texture_cache()
    path = tmp_path / "big.bin"
    raw = bytearray(b"\x00" * (4 * images.FINGERPRINT_SPAN))
    path.write_bytes(bytes(raw))
    base = images._file_fingerprint(path, len(raw))
    raw[-1] = 1
    path.write_bytes(bytes(raw))
    assert images._file_fingerprint(path, len(raw)) != base


def test_file_vanishing_between_stat_and_read_is_a_clean_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    images.clear_texture_cache()
    path = tmp_path / "gone.png"
    path.write_bytes(encode_png(np.zeros((2, 2, 4), np.uint8)))
    images.load_texture_image(TextureRef("p", path=path))
    path.unlink()
    with pytest.raises(ImageDecodeError, match="illisible"):
        images.load_texture_image(TextureRef("p", path=path))  # disparu avant `stat`
    path.write_bytes(encode_png(np.zeros((2, 2, 4), np.uint8)))

    def boom(self: Path, *args: object, **kwargs: object) -> object:
        raise PermissionError("verrouillé")

    monkeypatch.setattr(Path, "open", boom)  # `stat` passe, la lecture échoue
    with pytest.raises(ImageDecodeError, match="illisible"):
        images.load_texture_image(TextureRef("p", path=path))


def test_cache_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    images.clear_texture_cache()
    monkeypatch.setattr(images, "TEXTURE_CACHE_ENTRIES", 3)
    for i in range(10):
        images.load_texture_image(
            TextureRef("x", data=encode_png(np.full((2, 2, 4), i, np.uint8)))
        )
    assert len(images._CACHE) == 3
    monkeypatch.setattr(images, "TEXTURE_CACHE_BYTES", 40)
    images.clear_texture_cache()
    images.load_texture_image(
        TextureRef("x", data=encode_png(np.zeros((8, 8, 4), np.uint8)))
    )
    assert len(images._CACHE) == 0  # une image plus grosse que le cache n'y entre pas


def test_missing_and_empty_textures_raise_clean_errors(tmp_path: Path) -> None:
    with pytest.raises(ImageDecodeError, match="illisible"):
        images.load_texture_image(TextureRef("absente", path=tmp_path / "nope.png"))
    with pytest.raises(ImageDecodeError, match="sans chemin"):
        images.load_texture_image(TextureRef("vide"))
    big = tmp_path / "big.png"
    big.write_bytes(b"x" * 100)
    import kut3d.render.images as mod

    old = mod.MAX_IMAGE_FILE_BYTES
    mod.MAX_IMAGE_FILE_BYTES = 10
    try:
        with pytest.raises(ImageDecodeError, match="volumineuse"):
            images.load_texture_image(TextureRef("big", path=big))
    finally:
        mod.MAX_IMAGE_FILE_BYTES = old


def test_non_png_without_qt_gives_a_clear_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "PySide6", None)  # force ImportError
    monkeypatch.setitem(sys.modules, "PySide6.QtGui", None)
    monkeypatch.setitem(sys.modules, "PySide6.QtCore", None)
    with pytest.raises(ImageDecodeError, match="PySide6"):
        images.decode_image(b"\xff\xd8\xff\xe0 pas vraiment un jpeg")


def test_jpeg_fallback_through_qt() -> None:
    pytest.importorskip("PySide6.QtGui")
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from PySide6.QtGui import QColor, QImage

    qimg = QImage(16, 8, QImage.Format.Format_RGB32)
    qimg.fill(QColor(200, 40, 40))
    payload = QByteArray()
    buf = QBuffer(payload)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    assert qimg.save(buf, "JPG", 100)
    out = images.decode_image(bytes(payload.data()))
    assert out.shape == (8, 16, 4) and out.dtype == np.uint8
    assert (
        abs(int(out[4, 8, 0]) - 200) < 12
        and abs(int(out[4, 8, 1]) - 40) < 12
        and out[4, 8, 3] == 255
    )
    with pytest.raises(ImageDecodeError, match="illisible"):
        images.decode_image(b"\xff\xd8\xff\xe0 tronque")


def test_png_decoding_and_render_import_never_load_qt() -> None:
    code = (
        "import sys, numpy as np, kut3d\n"
        "from kut3d.render import images, png, render_frame\n"
        "from kut3d.model import TextureRef\n"
        "images.load_texture_image(TextureRef('t', data=png.encode_png(np.zeros((2, 2, 4), np.uint8))))\n"
        "assert not [m for m in sys.modules if m.startswith('PySide6')], 'Qt chargé'\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
