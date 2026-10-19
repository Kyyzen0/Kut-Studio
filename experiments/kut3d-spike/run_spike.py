"""Exécute chaque voie de rendu dans un sous-processus (timeout 30 s) et affiche un tableau récapitulatif.

Usage : ``python experiments/kut3d-spike/run_spike.py`` (écrit ``out/cube_<voie>.png``, 512x512).
Mode interne : ``--worker <voie>`` (lancé par le parent, une plateforme Qt par processus).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = Path(os.environ.get("KUT3D_SPIKE_OUT", str(HERE / "out")))
SIZE = 512
ANGLE = 35.0
TIMEOUT_S = 30
# (voie, plateformes Qt à essayer ; "" = numpy, sans Qt). Ordre : première réussite = PNG de la voie.
ROUTES: list[tuple[str, list[str]]] = [
    ("numpy", [""]),
    ("gl_offscreen", ["offscreen", "minimal", "cocoa"]),
    ("qrhi", ["offscreen", "minimal", "cocoa"]),
    ("moderngl", [""]),
]


def worker(route: str) -> int:
    """Rend 2 images (chauffe puis mesure), écrit le PNG et imprime une ligne JSON."""
    sys.path.insert(0, str(HERE))
    from pngio import write_png

    app = None
    if route == "numpy":
        from render_numpy import NumpyRenderer

        make = NumpyRenderer
    elif route == "gl_offscreen":
        from PySide6.QtGui import QGuiApplication

        app = QGuiApplication(sys.argv[:1])
        from render_gl_offscreen import GlRenderer

        make = GlRenderer
    elif route == "qrhi":
        from PySide6.QtWidgets import QApplication

        app = QApplication(sys.argv[:1])
        from render_qrhi import QrhiRenderer

        make = QrhiRenderer
    else:
        print(json.dumps({"ok": False, "error": f"voie inconnue : {route}"}))
        return 2
    renderer = make(SIZE, SIZE)
    renderer.render(ANGLE)  # chauffe : compilation, caches, premier envoi GPU
    t0 = time.perf_counter()
    image = renderer.render(ANGLE)
    ms = (time.perf_counter() - t0) * 1000
    write_png(OUT / f"cube_{route}.png", image)
    windows = 0
    if app is not None:
        from PySide6.QtGui import QGuiApplication

        windows = sum(1 for w in QGuiApplication.allWindows() if w.isVisible())
    print(
        json.dumps(
            {
                "ok": True,
                "ms": round(ms, 2),
                "backend": getattr(renderer, "backend", ""),
                "visible_windows": windows,
            }
        )
    )
    if hasattr(renderer, "close"):
        renderer.close()
    return 0


def run_route(route: str, platform: str) -> dict:
    """Lance le worker en sous-processus ; ne lève jamais (un crash GPU devient un résultat)."""
    env = dict(os.environ)
    if platform:
        env["QT_QPA_PLATFORM"] = platform
    cmd = [sys.executable, str(Path(__file__).resolve()), "--worker", route]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=TIMEOUT_S, env=env, cwd=HERE
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timeout > {TIMEOUT_S} s"}
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("{")]
    if proc.returncode == 0 and lines:
        return json.loads(lines[-1])
    tail = (proc.stderr.strip().splitlines() or ["(aucune sortie d'erreur)"])[-1]
    return {"ok": False, "error": f"code {proc.returncode} : {tail[:200]}"}


def main() -> int:
    rows = []
    for route, platforms in ROUTES:
        if route == "moderngl" and importlib.util.find_spec("moderngl") is None:
            rows.append(
                (
                    route,
                    "-",
                    {
                        "ok": False,
                        "error": "module moderngl absent (non installé, volontairement)",
                    },
                )
            )
            continue
        if route == "moderngl":
            rows.append(
                (
                    route,
                    "-",
                    {
                        "ok": False,
                        "error": "moderngl présent mais voie non implémentée dans ce spike",
                    },
                )
            )
            continue
        for platform in platforms:
            res = run_route(route, platform)
            rows.append((route, platform or "-", res))
    print(
        f"{'voie':<14}{'plateforme Qt':<15}{'résultat':<9}{'ms/image':<10}{'fenêtres':<9}détail"
    )
    for route, platform, res in rows:
        if res["ok"]:
            print(
                f"{route:<14}{platform:<15}{'OK':<9}{res['ms']:<10}{res['visible_windows']:<9}{res['backend']}"
            )
        else:
            print(
                f"{route:<14}{platform:<15}{'ÉCHEC':<9}{'-':<10}{'-':<9}{res['error']}"
            )
    ok = (OUT / "cube_numpy.png").exists()
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--worker")
    args = ap.parse_args()
    sys.exit(worker(args.worker) if args.worker else main())
