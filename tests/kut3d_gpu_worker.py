"""Worker GPU des tests de phase 2b : `python tests/kut3d_gpu_worker.py SORTIE.npz [cas ...]`.

Lancé dans un SOUS-PROCESSUS par `tests/test_kut3d_gpu_render.py` avec une plateforme Qt native (la suite de tests force
`offscreen`, qui n'a pas de GPU). Rend chaque cas avec `render_frame(backend="qrhi")`, écrit les images, les temps et
l'API graphique dans un `.npz`, puis libère le moteur : le code de sortie 0 prouve aussi un démontage propre.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import kut3d  # noqa: E402
from kut3d import render_frame  # noqa: E402
from tests.kut3d_gpu_cases import CASES  # noqa: E402


def main(argv: list[str]) -> int:
    out = Path(argv[1])
    names = argv[2:] or list(CASES)
    arrays: dict[str, np.ndarray] = {}
    times: dict[str, float] = {}
    for name in names:
        case = CASES[name]
        scene = case.build()
        render_frame(
            scene, case.t, case.size, backend="qrhi", samples=case.samples
        )  # chauffe
        start = time.perf_counter()
        frame = render_frame(
            scene, case.t, case.size, backend="qrhi", samples=case.samples
        )
        times[name] = time.perf_counter() - start
        arrays[f"{name}/color"], arrays[f"{name}/alpha"], arrays[f"{name}/depth"] = (
            frame.color,
            frame.alpha,
            frame.depth,
        )
    from kut3d.gpu import renderer

    first = CASES["checker"]
    sc = first.build()
    a = render_frame(sc, first.t, first.size, backend="qrhi")
    other = CASES["pbr_lit"]
    render_frame(other.build(), other.t, other.size, backend="qrhi")
    b = render_frame(
        sc, first.t, first.size, backend="qrhi"
    )  # même scène après une autre : aucun état résiduel
    try:
        render_frame(first.build(), 0.0, (8192, 100), backend="qrhi", samples=4)
        oversize = ""
    except kut3d.RenderBackendUnavailable as error:
        oversize = str(error)
    info = {
        "backend": renderer.get_renderer().backend_name,
        "times": times,
        "windows": _visible_windows(),
        "repeat_identical": bool(
            a.color.tobytes() == b.color.tobytes()
            and a.depth.tobytes() == b.depth.tobytes()
        ),
        "oversize": oversize,
    }
    arrays["info"] = np.frombuffer(json.dumps(info).encode(), dtype=np.uint8)
    np.savez(out, **arrays)
    kut3d.gpu.shutdown()
    return 0


def _visible_windows() -> list[str]:
    from PySide6.QtGui import QGuiApplication

    return [str(w.title()) for w in QGuiApplication.allWindows() if w.isVisible()]


if __name__ == "__main__":
    sys.exit(main(sys.argv))
