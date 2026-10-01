"""Faux FFmpeg portable pour les tests d'intégration Qt.

Le script shell historique reste plus agréable à lire sous POSIX. Cette
variante est lancée via l'interpréteur Python sur Windows, où un ``.sh`` ne
peut pas être le programme d'un ``QProcess``.

Comportement réglable par variables d'environnement (héritées par le
``QProcess``) :

- ``FAKE_FFMPEG_SECONDS`` : durée simulée du rendu (défaut 1,0 s) ;
- ``FAKE_FFMPEG_FAIL`` : si défini, quitte en erreur avec ce message ;
- ``FAKE_FFMPEG_FAIL_ON`` : sous-chaîne ; échoue si elle figure dans le
  chemin de sortie (permet de faire échouer un seul job d'une file) ;
- ``FAKE_FFMPEG_PID_FILE`` : y écrit son PID (vérification d'orphelins) ;
- ``FAKE_FFMPEG_FAIL_ENCODER`` : sous-chaîne ; échoue **au lancement** (aucune image
  encodée) si un argument la contient, comme un encodeur matériel qui ne s'initialise
  pas (``videotoolbox``…) ;
- ``FAKE_FFMPEG_FAIL_ENCODER_LATE`` : idem mais après quelques images de progression ;
- ``FAKE_FFMPEG_ARGS_FILE`` : ajoute une ligne JSON avec les arguments de chaque lancement.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def main(arguments: list[str]) -> int:
    args_file = os.environ.get("FAKE_FFMPEG_ARGS_FILE")
    if args_file:
        with open(args_file, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(arguments) + "\n")
    fail_encoder = os.environ.get("FAKE_FFMPEG_FAIL_ENCODER")
    if fail_encoder and any(fail_encoder in argument for argument in arguments):
        print(f"Error initializing the encoder {fail_encoder}", file=sys.stderr, flush=True)
        return 1
    late_encoder = os.environ.get("FAKE_FFMPEG_FAIL_ENCODER_LATE")
    late_failure = bool(late_encoder and any(late_encoder in argument for argument in arguments))
    output_path = ""
    for argument in arguments:
        if argument.startswith("FAIL"):
            print("Erreur simulée pour les tests", file=sys.stderr, flush=True)
            return 1
        if not argument.startswith("-"):
            output_path = argument

    pid_file = os.environ.get("FAKE_FFMPEG_PID_FILE")
    if pid_file:
        Path(pid_file).write_text(str(os.getpid()), encoding="utf-8")

    failure = os.environ.get("FAKE_FFMPEG_FAIL")
    fail_on = os.environ.get("FAKE_FFMPEG_FAIL_ON")
    if failure or (fail_on and fail_on in output_path):
        print(failure or "Erreur simulée pour les tests", file=sys.stderr, flush=True)
        return 1

    total = float(os.environ.get("FAKE_FFMPEG_SECONDS", "1.0"))
    print(f"fake_ffmpeg starting, output: {output_path}", flush=True)
    steps = max(1, int(total / 0.05))
    for step in range(steps):
        elapsed_us = int(step / steps * 1_000_000)
        # Le vrai FFmpeg écrit les deux clés, avec la même valeur en microsecondes.
        print(f"out_time_us={elapsed_us}", flush=True)
        print(f"out_time_ms={elapsed_us}", flush=True)
        time.sleep(0.05)
        if late_failure and step >= 3:
            print("Encoder stopped unexpectedly", file=sys.stderr, flush=True)
            return 1

    if output_path:
        destination = Path(output_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.touch()
        print(f"fake_ffmpeg wrote: {output_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
