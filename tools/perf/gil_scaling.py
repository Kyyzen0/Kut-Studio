"""Banc de l'exécution parallèle des travaux Qt : threads contre processus (ADR-0003, B1).

Usage ::

    python -m tools.perf.gil_scaling --out docs/perf/gil-scaling.json
    python -m tools.perf.gil_scaling --quick          # moins de travaux, une seule répétition

Question : pendant un appel Qt long, PySide6 garde-t-il le GIL ? Si oui, des threads Python ne gagnent rien sur le
rendu ni sur l'encodage, et le seul parallélisme disponible dans un processus Python est celui des processus.

Pour chaque travail, le banc mesure le temps par travail en série, puis le gain (série / parallèle) avec 4 et 8
threads, puis avec 4 et 8 processus. Un travail en Python pur sert de témoin : son gain avec les threads doit
rester voisin de 1, sinon la méthode est en cause.

Les travaux reprennent les formats de Kut-Studio (1080x1920 RVBA, texte à contour comme le rastériseur des motion
graphics, PNG). Ils sont synthétiques : le rastériseur réel demande un projet, voir ``tools/perf/engine_bench.py``.
Les temps absolus dépendent de la machine : comparer les colonnes, pas les machines.

``--quick`` ne sert qu'à vérifier que le banc tourne : avec quelques travaux seulement, le lancement des processus
domine et les gains ne sont pas représentatifs.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import platform
import statistics
import sys
import time
from collections.abc import Callable
from concurrent.futures import Executor, ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
WIDTH, HEIGHT = 1080, 1920
WORKERS = (4, 8)
_APPLICATION: object | None = None


def _init_worker() -> None:
    """Un ``QGuiApplication`` par processus : les polices et les ``QImage`` en ont besoin."""
    global _APPLICATION
    from PySide6.QtGui import QGuiApplication

    _APPLICATION = QGuiApplication.instance() or QGuiApplication([])


def scale_4k(index: int) -> int:
    """Réduction lissée d'une image 4K : un seul appel Qt long."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage

    image = QImage(3840, 2160, QImage.Format.Format_RGBA8888)
    image.fill(0xFF336699 + index)
    scaled = image.scaled(1920, 1080, Qt.AspectRatioMode.IgnoreAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
    return scaled.width()


def convert_4k(index: int) -> int:
    """Conversion de format d'une image 4K : un seul appel Qt long."""
    from PySide6.QtGui import QImage

    image = QImage(3840, 2160, QImage.Format.Format_RGBA8888)
    image.fill(0x80336699 + index)
    return image.convertToFormat(QImage.Format.Format_RGBA8888_Premultiplied).width()


def paint_2000_calls(index: int) -> int:
    """2 000 petits appels ``QPainter`` sur un cadre 1080x1920."""
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QColor, QImage, QPainter

    image = QImage(WIDTH, HEIGHT, QImage.Format.Format_RGBA8888_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    for step in range(2000):
        painter.setBrush(QColor(200, (step + index) % 255, 80, 120))
        painter.drawEllipse(QPointF(50 + (step * 7) % 980, 50 + (step * 13) % 1800), 30, 30)
    painter.end()
    return image.width()


def text_outline(index: int) -> int:
    """Texte à contour, comme une couche de titre rastérisée (``core/mograph_raster.py``)."""
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen

    image = QImage(WIDTH, HEIGHT, QImage.Format.Format_RGBA8888_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    font = QFont("Helvetica", 96)
    font.setBold(True)
    path = QPainterPath()
    path.addText(QPointF(60, 400), font, f"Kut-Studio {index % 9}")
    painter.strokePath(path, QPen(QColor(0, 0, 0), 14))
    painter.fillPath(path, QColor(255, 255, 255))
    painter.end()
    return image.width()


def png_save(index: int) -> int:
    """Encodage PNG d'un cadre 1080x1920 en mémoire (``QImage.save`` libère le GIL)."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from PySide6.QtGui import QImage

    image = QImage(WIDTH, HEIGHT, QImage.Format.Format_RGBA8888)
    image.fill(0x80336699 + index)
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG", 1)
    return data.size()


def python_control(index: int) -> int:
    """Témoin en Python pur : le GIL doit empêcher tout gain des threads."""
    total = 0
    for step in range(600_000):
        total += step * step % 7
    return total + index


JOBS: dict[str, tuple[str, Callable[[int], int], int]] = {
    "scale_4k": ("Réduction lissée d'une image 4K (un appel Qt long)", scale_4k, 24),
    "convert_4k": ("Conversion de format d'une image 4K (un appel Qt long)", convert_4k, 40),
    "paint_2000_calls": ("2 000 petits appels QPainter, 1080x1920", paint_2000_calls, 40),
    "text_outline": ("Texte à contour (rastériseur des motion graphics)", text_outline, 120),
    "png_save": ("Encodage PNG 1080x1920 (QImage.save)", png_save, 60),
    "python_control": ("Témoin : boucle Python pure", python_control, 60),
}


def _executor(kind: str, workers: int) -> Executor:
    if kind == "thread":
        return ThreadPoolExecutor(workers)
    return ProcessPoolExecutor(workers, mp_context=multiprocessing.get_context("spawn"),
                               initializer=_init_worker)


def _median_seconds(run: Callable[[], object], repeat: int) -> float:
    samples = []
    for _ in range(repeat):
        started = time.perf_counter()
        run()
        samples.append(time.perf_counter() - started)
    return statistics.median(samples)


def measure(job: Callable[[int], int], count: int, repeat: int) -> dict:
    """Temps série par travail, puis gain (série / parallèle) pour chaque configuration."""
    job(0)  # une exécution avant la mesure : polices, caches Qt, import des modules
    serial = _median_seconds(lambda: [job(index) for index in range(count)], repeat)
    speedup: dict[str, float] = {}
    for kind in ("thread", "process"):
        for workers in WORKERS:
            with _executor(kind, workers) as pool:
                list(pool.map(job, range(workers)))  # démarre les travailleurs (import de Qt, polices)
                parallel = _median_seconds(lambda: list(pool.map(job, range(count))), repeat)
            speedup[f"{kind}{workers}"] = round(serial / parallel, 2)
    return {"count": count, "serial_ms_per_job": round(serial / count * 1000, 2), "speedup": speedup}


def _machine() -> dict:
    from PySide6 import __version__ as pyside_version

    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpus": os.cpu_count(),
        "python": platform.python_version(),
        "gil_enabled": getattr(sys, "_is_gil_enabled", lambda: True)(),
        "pyside6": pyside_version,
    }


def run(*, quick: bool, repeat: int) -> dict:
    _init_worker()
    report: dict = {"meta": {**_machine(), "quick": quick, "repeat": repeat}, "jobs": {}}
    for key, (label, job, count) in JOBS.items():
        size = max(4, count // 6) if quick else count
        result = measure(job, size, repeat)
        report["jobs"][key] = {"label": label, **result}
        print(f"{label:<60} série {result['serial_ms_per_job']:8.2f} ms/travail | "
              + " | ".join(f"{name} x{gain:.2f}" for name, gain in result["speedup"].items()), flush=True)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gain des threads et des processus sur des travaux Qt (ADR-0003, B1).")
    parser.add_argument("--out", type=Path, help="fichier JSON de sortie (sinon, rien n'est écrit)")
    parser.add_argument("--quick", action="store_true", help="moins de travaux, une seule répétition")
    parser.add_argument("--repeat", type=int, default=3, help="répétitions mesurées (médiane retenue)")
    args = parser.parse_args(argv)
    report = run(quick=args.quick, repeat=1 if args.quick else args.repeat)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"écrit : {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
