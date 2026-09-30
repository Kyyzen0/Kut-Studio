"""Faux FFmpeg portable pour les tests d'intégration Qt.

Le script shell historique reste plus agréable à lire sous POSIX. Cette
variante est lancée via l'interpréteur Python sur Windows, où un ``.sh`` ne
peut pas être le programme d'un ``QProcess``.
"""

from __future__ import annotations

from pathlib import Path
import sys
import time


def main(arguments: list[str]) -> int:
    output_path = ""
    for argument in arguments:
        if argument.startswith("FAIL"):
            print("Erreur simulée pour les tests", file=sys.stderr, flush=True)
            return 1
        if not argument.startswith("-"):
            output_path = argument

    print(f"fake_ffmpeg starting, output: {output_path}", flush=True)
    for elapsed_us in range(0, 1_000_000, 50_000):
        print(f"out_time_us={elapsed_us}", flush=True)
        time.sleep(0.05)

    if output_path:
        destination = Path(output_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.touch()
        print(f"fake_ffmpeg wrote: {output_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
