"""Outils partagés par les tests GPU optionnels et le banc ``tools/perf/gpu_bench.py``.

Fabrique des ``QVideoFrame`` (NV12, P010, YUV420P…) à partir de plans numpy,
rend une :class:`~core.gpu_composite.CompositeFrame` avec le vrai
``GpuPreviewWidget`` (``grabFramebuffer`` : rendu hors écran, sans fenêtre) et
compare avec :func:`core.gpu_composite.reference_frame`.

Le rendu réel exige une plateforme Qt avec GPU (``cocoa``, ``windows``,
``xcb``/``wayland``) : la CI (``offscreen``) ne l'exécute jamais.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QSize
from PySide6.QtMultimedia import QVideoFrame, QVideoFrameFormat

PIXEL_FORMATS = {
    "nv12": QVideoFrameFormat.PixelFormat.Format_NV12,
    "p010": QVideoFrameFormat.PixelFormat.Format_P010,
    "yuv420p": QVideoFrameFormat.PixelFormat.Format_YUV420P,
    "yuv420p10": QVideoFrameFormat.PixelFormat.Format_YUV420P10,
    "bgra": QVideoFrameFormat.PixelFormat.Format_BGRA8888,
}


def test_pattern(
    width: int, height: int, seed: int = 3, chroma_block: int = 2
) -> np.ndarray:
    """Codes YUV 8 bits (H, W, 3) : dégradés, damier, bords nets.

    ``chroma_block`` : côté des blocs de chroma aléatoire (2 = pire cas pour le
    suréchantillonnage 4:2:0 ; 16 = proche d'une vraie image).
    """
    rng = np.random.default_rng(seed)
    ys, xs = np.mgrid[0:height, 0:width]
    y = 16 + (xs * 219 // max(1, width - 1))
    y = np.where(((xs // 16) + (ys // 16)) % 2 == 0, y, 235 - (y - 16))
    step = max(2, int(chroma_block))
    blocks_u = rng.integers(40, 216, size=(height // step + 1, width // step + 1))
    blocks_v = rng.integers(40, 216, size=(height // step + 1, width // step + 1))
    u = blocks_u[ys // step, xs // step]
    v = blocks_v[ys // step, xs // step]
    return np.stack([y, u, v], axis=-1).astype(np.uint8)


def smooth_pattern(width: int, height: int) -> np.ndarray:
    """Codes YUV 8 bits **lisses** (sinusoïdes, dégradés), proches d'une vraie image.

    Sert à comparer l'aperçu à l'export : sur des bords francs, le demi-pixel de
    rééchantillonnage du filtre ``rotate`` de l'export domine toute autre mesure.
    """
    ys, xs = np.mgrid[0:height, 0:width].astype(np.float64)
    y = (
        120
        + 70 * np.sin(2 * np.pi * xs / 97.0) * np.cos(2 * np.pi * ys / 61.0)
        + 30 * xs / width
    )
    u = 128 + 60 * np.sin(2 * np.pi * (xs + ys) / 131.0)
    v = 128 + 60 * np.cos(2 * np.pi * (xs - 2 * ys) / 151.0)
    return np.clip(np.stack([y, u, v], axis=-1), 16, 235).round().astype(np.uint8)


def make_frame(codes: np.ndarray, layout: str) -> QVideoFrame:
    """``QVideoFrame`` au format ``layout`` contenant ``codes`` (YUV 8 bits, chroma 2×2 constante)."""
    height, width = codes.shape[:2]
    fmt = QVideoFrameFormat(QSize(width, height), PIXEL_FORMATS[layout])
    frame = QVideoFrame(fmt)
    assert frame.map(QVideoFrame.MapMode.WriteOnly)
    try:
        y = codes[..., 0].astype(np.uint16)
        u = codes[::2, ::2, 1].astype(np.uint16)
        v = codes[::2, ::2, 2].astype(np.uint16)
        if layout == "nv12":
            _write(frame, 0, y.astype(np.uint8))
            _write(
                frame, 1, np.stack([u, v], -1).astype(np.uint8).reshape(u.shape[0], -1)
            )
        elif layout == "yuv420p":
            for index, plane in enumerate((y, u, v)):
                _write(frame, index, plane.astype(np.uint8))
        elif layout == "p010":
            _write(
                frame, 0, (y << 8).astype("<u2").view(np.uint8)
            )  # 8 bits → 10 bits alignés en haut
            _write(
                frame,
                1,
                (np.stack([u, v], -1) << 8)
                .astype("<u2")
                .reshape(u.shape[0], -1)
                .view(np.uint8),
            )
        elif layout == "yuv420p10":
            for index, plane in enumerate((y, u, v)):
                _write(frame, index, (plane << 2).astype("<u2").view(np.uint8))
        elif layout == "bgra":
            raise ValueError("utiliser make_rgb_frame")
    finally:
        frame.unmap()
    return frame


def _write(frame: QVideoFrame, plane: int, data: np.ndarray) -> None:
    stride = frame.bytesPerLine(plane)
    view = np.frombuffer(frame.bits(plane), dtype=np.uint8)
    rows, row_bytes = (
        data.shape[0],
        data.shape[1] * (data.itemsize if data.ndim == 2 else 1),
    )
    raw = data.reshape(rows, -1).view(np.uint8).reshape(rows, -1)
    target = view[: stride * rows].reshape(rows, stride)
    target[:, : raw.shape[1]] = raw
    del row_bytes


def reference_codes(codes: np.ndarray) -> np.ndarray:
    """Codes normalisés attendus par :func:`reference_frame`, pour la vidéo 4:2:0 que :func:`make_frame` fabrique.

    La chroma garde l'échantillon du coin haut gauche de chaque bloc 2×2 (celui que ``make_frame`` et le plan
    ``yuv420p`` des tests retiennent), répété sur le bloc. La référence relit le plan sous-échantillonné (un
    échantillon par bloc) et l'interpole selon :func:`core.gpu_composite.layer_chroma_grid` : co-sité haut gauche pour
    un calque que l'export ne rééchantillonne pas (:mod:`core.chroma_siting`). Pour la chroma que l'export obtient
    ainsi, voir :func:`sited_codes`.
    """
    height, width = codes.shape[:2]
    out = codes.astype(np.float64) / 255.0
    for channel in (1, 2):
        plane = codes[::2, ::2, channel].astype(np.float64) / 255.0
        out[..., channel] = np.repeat(np.repeat(plane, 2, axis=0), 2, axis=1)[
            :height, :width
        ]
    return out


def sited_codes(codes: np.ndarray) -> np.ndarray:
    """Codes normalisés à pleine résolution tels que l'export les obtient (:mod:`core.chroma_siting`).

    L'échantillon de chroma 4:2:0 (coin haut gauche du bloc, comme ``make_frame``) est posé sur le pixel pair ; le pixel
    impair en est la moyenne avec l'échantillon suivant (bords répétés), sur chaque axe.
    """
    out = codes.astype(np.float64) / 255.0
    for channel in (1, 2):
        plane = codes[::2, ::2, channel].astype(np.float64) / 255.0
        for axis in (0, 1):
            following = np.concatenate(
                [
                    np.take(plane, range(1, plane.shape[axis]), axis=axis),
                    np.take(plane, [-1], axis=axis),
                ],
                axis=axis,
            )
            shape = list(plane.shape)
            shape[axis] *= 2
            doubled = np.empty(shape)
            doubled[(slice(None),) * axis + (slice(0, None, 2),)] = plane
            doubled[(slice(None),) * axis + (slice(1, None, 2),)] = 0.5 * (
                plane + following
            )
            plane = doubled
        out[..., channel] = plane[: codes.shape[0], : codes.shape[1]]
    return out


def grain_pattern(width: int, height: int, seed: int = 5) -> np.ndarray:
    """Codes YUV 8 bits : luma **lisse**, chroma aléatoire à chaque échantillon 4:2:0 (grain chromatique).

    La chroma change d'un bloc 2×2 au suivant : recopier un échantillon par bloc ou l'interpoler
    donnent ici des images très différentes (≈ 25 niveaux en moyenne), alors que les mires à chroma constante par
    bloc ou lente (:func:`test_pattern`, :func:`smooth_pattern`) ne le voient pas. La luma reste lisse pour que la
    comparaison avec l'export ne mesure pas le rééchantillonnage géométrique de ses filtres.
    """
    rng = np.random.default_rng(seed)
    ys, xs = np.mgrid[0:height, 0:width].astype(np.float64)
    y = 120 + 60 * np.sin(2 * np.pi * xs / 89.0) * np.cos(2 * np.pi * ys / 67.0)
    chroma = rng.integers(40, 216, size=((height + 1) // 2, (width + 1) // 2, 2))
    uv = np.repeat(np.repeat(chroma, 2, axis=0), 2, axis=1)[:height, :width]
    return np.dstack((np.clip(y, 16, 235).round(), uv)).astype(np.uint8)


def render_gpu(
    frame_desc, video_frames: dict, mattes: dict | None = None, api: str = "metal"
):
    """Rend ``frame_desc`` avec le vrai widget ; ``None`` si le GPU est indisponible."""
    from PySide6.QtWidgets import QApplication

    from ui.gpu_preview import GpuPreviewWidget

    app = QApplication.instance() or QApplication([])
    width, height = frame_desc.render_size
    widget = GpuPreviewWidget(api=api)
    failures: list[tuple[str, str]] = []
    widget.failed.connect(lambda kind, detail: failures.append((kind, detail)))
    widget.setFixedColorBufferSize(QSize(width, height))
    dpr = (
        widget.devicePixelRatioF() or 1.0
    )  # le rectangle est en pixels logiques (écran Retina : 2)
    widget.set_canvas_rect((0.0, 0.0, width / dpr, height / dpr), (0.0, 0.0, 0.0))
    for source_id, frame in video_frames.items():
        widget.set_video_frame(source_id, frame)
    widget.set_composite(frame_desc, mattes or {})
    image = widget.grabFramebuffer()
    app.processEvents()
    widget.release_gpu()
    widget.deleteLater()
    if image.isNull() or failures:
        return None, failures
    from PySide6.QtGui import QImage

    image = image.convertToFormat(QImage.Format.Format_RGB888)
    data = np.frombuffer(image.constBits(), dtype=np.uint8).reshape(
        image.height(), image.bytesPerLine()
    )
    rgb = data[:, : image.width() * 3].reshape(image.height(), image.width(), 3)
    return rgb.astype(np.float64) / 255.0, failures
