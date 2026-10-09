"""Vérification du moniteur GPU sur le **vrai** GPU (Metal, Direct3D 11, OpenGL…).

Usage ::

    python -m tools.gpu.selfcheck --api metal      # JSON : écarts max par format et cas

Rend, avec le vrai ``GpuPreviewWidget`` (hors écran, sans fenêtre), des images
NV12 / YUV420P / P010 / YUV420P10 avec transforms, effets, modes de fusion et étalonnage (LUT),
et les compare à :func:`core.gpu_composite.reference_frame`. Exige une
plateforme Qt avec GPU : utilisé par ``tests/test_gpu_hardware.py`` (optionnel)
et par le banc ``tools/perf/gpu_bench.py``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def cases():
    from core.blend_modes import BlendMode
    from core.effects_model import ClipEffect, EffectType

    def effect(kind, **params):
        return ClipEffect("e" + kind.value, kind, True, params)

    rotation = (0.7 * 0.94, 0.7 * 0.34, -0.7 * 0.34, 0.7 * 0.94, 80.0, 10.0)
    return [
        ("plain", [], (1.0, 0.0, 0.0, 1.0, 0.0, 0.0), 1.0, BlendMode.NORMAL),
        ("rotation_effects", [effect(EffectType.COLOR_CORRECTION, brightness=0.05, contrast=1.3, saturation=1.5),
                              effect(EffectType.BLUR, intensity=2.0), effect(EffectType.VIGNETTE, intensity=0.6)],
         rotation, 0.8, BlendMode.SCREEN),
        ("sharpen_sepia", [effect(EffectType.SHARPEN, intensity=1.2), effect(EffectType.SEPIA)],
         (0.5, 0.0, 0.0, 0.5, 40.0, 30.0), 1.0, BlendMode.DIFFERENCE),
        ("black_white_overlay", [effect(EffectType.BLACK_AND_WHITE)], (1.0, 0.0, 0.0, 1.0, 12.0, -6.0), 0.6,
         BlendMode.OVERLAY),
        # Vidéo sociale : bloom (4 passes), aberration chromatique, heat haze, puis une vignette après chacun.
        ("glow_aberration", [effect(EffectType.GLOW, threshold=0.5, radius=4.0, intensity=1.2),
                             effect(EffectType.CHROMATIC_ABERRATION, intensity=4.0),
                             effect(EffectType.VIGNETTE, intensity=0.4)],
         (1.0, 0.0, 0.0, 1.0, 0.0, 0.0), 1.0, BlendMode.NORMAL),
        ("heat_haze", [effect(EffectType.HEAT_HAZE, amplitude=5.0, frequency=0.07, speed=0.0, top=0.2, span=0.5),
                       effect(EffectType.COLOR_CORRECTION, brightness=0.0, contrast=1.1, saturation=1.2)],
         (1.0, 0.0, 0.0, 1.0, 0.0, 0.0), 1.0, BlendMode.ADD),
        # Étalonnage : LUT 3D (atlas) lue après les effets ; la sépia laisse le calque en RVB (conversion vers YUV).
        ("grade", [], (1.0, 0.0, 0.0, 1.0, 0.0, 0.0), 1.0, BlendMode.NORMAL),
        ("grade_after_effects", [effect(EffectType.BLUR, intensity=1.5), effect(EffectType.SEPIA)],
         (0.8, 0.0, 0.0, 0.8, 20.0, 10.0), 0.9, BlendMode.NORMAL),
        # Comparaison avant / après (page Couleur) : les 40 % de gauche du calque sans étalonnage.
        ("grade_compare", [], (1.0, 0.0, 0.0, 1.0, 0.0, 0.0), 1.0, BlendMode.NORMAL),
    ]


def synthetic_lut(size: int | None = None):
    """Atlas d'étalonnage non linéaire (``(octets RVB, QImage, tableau 0..1)``) : le même réseau pour le GPU et la
    référence, quelle que soit sa forme ; une courbe sur ``c₀``, un mélange et une inversion révèlent toute erreur
    d'adressage des tranches."""
    import numpy as np
    from PySide6.QtGui import QImage

    from core.gpu_grade import LUT_SIZE, atlas_array

    n = size or LUT_SIZE
    c0, c2, c1 = np.meshgrid(np.linspace(0, 1, n), np.linspace(0, 1, n), np.linspace(0, 1, n), indexing="ij")
    rgb = np.stack((c0 ** 0.7, 0.5 * (c1 + c0 * c2), 1.0 - c2 * c1), axis=-1)
    atlas = np.round(rgb.reshape(n, n * n, 3) * 255).astype(np.uint8).tobytes()
    image = QImage(atlas, n * n, n, n * n * 3, QImage.Format.Format_RGB888).copy()
    return atlas, image, atlas_array(atlas, n)


def run(api: str, width: int = 320, height: int = 180) -> dict:
    import numpy as np
    from PySide6.QtWidgets import QApplication

    from core.gpu_composite import AdjustmentLayer, CompositeFrame, CompositeLayer, VideoSource, reference_frame
    from core.gpu_effects import program_for
    from tests.gpu_harness import make_frame, reference_codes, render_gpu, test_pattern

    QApplication.instance() or QApplication([])
    codes = test_pattern(width, height)
    results: dict = {"api": api, "cases": {}}
    for layout in ("nv12", "yuv420p", "p010", "yuv420p10"):
        for name, effects, matrix, opacity, blend in cases():
            graded = name.startswith("grade")
            layer = CompositeLayer("v", matrix, (0, 0, width, height), opacity, blend, program_for(effects),
                                   (abs(matrix[0]) or 1.0, abs(matrix[3]) or 1.0),
                                   grade_lut="lut:test" if graded else "",
                                   grade_split=0.4 if name == "grade_compare" else 0.0)
            adjustments = ()
            mattes, images, luts = {}, {}, {}
            if graded:
                _bytes, images["lut:test"], luts["lut:test"] = synthetic_lut()
            if name == "plain":  # + un calque d'effets sur la moitié droite
                from core.effects_model import ClipEffect, EffectType

                adjustments = (AdjustmentLayer(program_for([ClipEffect("s", EffectType.SEPIA, True, {})]), "cov"),)
                mattes["cov"], images["cov"] = _half_matte(width, height)
            if name == "grade":  # + un calque d'effets étalonné (LUT en RVB) sur la moitié droite
                adjustments = (AdjustmentLayer(program_for(()), "cov", grade_lut="lut:test"),)
                mattes["cov"], images["cov"] = _half_matte(width, height)
            frame = CompositeFrame(width, height, 1.0, (layer,), (VideoSource("v", layout, width, height),),
                                   (0.1, 0.2, 0.3), adjustments)
            got, failures = render_gpu(frame, {"v": make_frame(codes, layout)}, images, api=api)
            key = f"{layout}/{name}"
            if got is None:
                results["cases"][key] = {"error": str(failures)}
                continue
            reference = reference_frame(frame, {"v": reference_codes(codes)}, mattes, luts)
            diff = np.abs(got - reference) * 255
            results["cases"][key] = {"mean": float(diff.mean()), "max": float(diff.max())}
    results["cases"]["rgba/light_add"] = _light_case(api, width, height, codes)
    return results


def _light_case(api: str, width: int, height: int, codes) -> dict:
    """Calque graphique RGBA (lumière) composé en Addition sur la vidéo, son alpha en matte (moniteur GPU)."""
    import numpy as np
    from PySide6.QtGui import QColor, QImage, QRadialGradient, QPainter
    from PySide6.QtCore import QPointF

    from core.blend_modes import BlendMode
    from core.gpu_composite import CompositeFrame, CompositeLayer, VideoSource, reference_frame
    from core.gpu_effects import program_for
    from tests.gpu_harness import make_frame, reference_codes, render_gpu

    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(0, 0, 0, 0))
    painter = QPainter(image)
    gradient = QRadialGradient(QPointF(width * 0.4, height * 0.5), height * 0.6)
    gradient.setColorAt(0.0, QColor(255, 176, 64, 230))
    gradient.setColorAt(1.0, QColor(255, 176, 64, 0))
    painter.fillRect(image.rect(), gradient)
    painter.end()
    straight = image.convertToFormat(QImage.Format.Format_RGBA8888)
    data = np.frombuffer(straight.constBits(), np.uint8).reshape(height, straight.bytesPerLine())
    rgba = data[:, : width * 4].reshape(height, width, 4) / 255.0
    identity = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    frame = CompositeFrame(width, height, 1.0, (
        CompositeLayer("v", identity, (0, 0, width, height), program=program_for(())),
        CompositeLayer("light", identity, (0, 0, width, height), blend=BlendMode.ADD, program=program_for(()),
                       matte="light"),
    ), (VideoSource("v", "nv12", width, height), VideoSource("light", "rgba", width, height)))
    from PySide6.QtMultimedia import QVideoFrame

    got, failures = render_gpu(frame, {"v": make_frame(codes, "nv12"), "light": QVideoFrame(straight)},
                               {"light": straight}, api=api)
    if got is None:
        return {"error": str(failures)}
    reference = reference_frame(frame, {"v": reference_codes(codes), "light": rgba[..., :3]}, {"light": rgba[..., 3]})
    diff = np.abs(got - reference) * 255
    return {"mean": float(diff.mean()), "max": float(diff.max())}


def _half_matte(width: int, height: int):
    """Couverture : moitié droite opaque (tableau pour la référence, ``QImage`` pour le GPU)."""
    import numpy as np
    from PySide6.QtGui import QColor, QImage

    array = np.zeros((height, width))
    array[:, width // 2:] = 1.0
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(0, 0, 0, 0))
    for y in range(height):
        for x in range(width // 2, width):
            image.setPixelColor(x, y, QColor(255, 255, 255, 255))
    return array, image


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--api", default="metal")
    args = parser.parse_args(argv)
    print(json.dumps(run(args.api)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
