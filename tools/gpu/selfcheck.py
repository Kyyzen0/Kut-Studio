"""Vérification du moniteur GPU sur le **vrai** GPU (Metal, Direct3D 11, OpenGL…).

Usage ::

    python -m tools.gpu.selfcheck --api metal      # JSON : écarts max par format et cas

Rend, avec le vrai ``GpuPreviewWidget`` (hors écran, sans fenêtre), des images
NV12 / YUV420P / P010 / YUV420P10 avec transforms, effets et modes de fusion,
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
    ]


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
            layer = CompositeLayer("v", matrix, (0, 0, width, height), opacity, blend, program_for(effects),
                                   (abs(matrix[0]) or 1.0, abs(matrix[3]) or 1.0))
            adjustments = ()
            mattes, images = {}, {}
            if name == "plain":  # + un calque d'effets sur la moitié droite
                from core.effects_model import ClipEffect, EffectType

                adjustments = (AdjustmentLayer(program_for([ClipEffect("s", EffectType.SEPIA, True, {})]), "cov"),)
                mattes["cov"], images["cov"] = _half_matte(width, height)
            frame = CompositeFrame(width, height, 1.0, (layer,), (VideoSource("v", layout, width, height),),
                                   (0.1, 0.2, 0.3), adjustments)
            got, failures = render_gpu(frame, {"v": make_frame(codes, layout)}, images, api=api)
            key = f"{layout}/{name}"
            if got is None:
                results["cases"][key] = {"error": str(failures)}
                continue
            reference = reference_frame(frame, {"v": reference_codes(codes)}, mattes)
            diff = np.abs(got - reference) * 255
            results["cases"][key] = {"mean": float(diff.mean()), "max": float(diff.max())}
    return results


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
