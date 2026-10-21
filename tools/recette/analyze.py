"""Analyses du moteur sur les médias générés, lancées dans un processus à part : ``python -m tools.recette.analyze KIND DOSSIER JSON``.

* ``tempo`` : ``core.beat_detection.detect_tempo`` sur la musique ; la vérité est le tempo qui a servi à la fabriquer.
* ``sync`` : ``core.audio_sync.analyze_sync`` sur les angles d'un plateau multicam ; la vérité est le décalage fabriqué.

Ce sont des mesures de **justesse** (tempo à 1 % près, décalage à une image près), pas de vitesse : la durée est celle du
processus, relevée par l'appelant. Écrit un objet JSON sur la sortie standard ; code 0 même si la mesure est mauvaise (le
verdict est dans ``ok``), code 1 si l'analyse n'a pas pu tourner.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

TEMPO_TOLERANCE = 0.01
"""Écart relatif du tempo toléré (1 %)."""
SYNC_TOLERANCE_MS = 40.0
"""Écart de décalage toléré : une image à 25 i/s."""


def analyze_tempo(folder: Path, params: dict[str, Any]) -> dict[str, Any]:
    from core.beat_detection import detect_tempo

    estimate = detect_tempo(str(folder / params["media"]))
    expected = float(params["expected_bpm"])
    beat = 60.0 / expected
    phase = estimate.first_beat % beat
    phase = min(phase, beat - phase)
    error = abs(estimate.bpm - expected) / expected
    return {"ok": error <= TEMPO_TOLERANCE, "bpm": estimate.bpm, "expected_bpm": expected, "relative_error": round(error, 5),
            "first_beat": estimate.first_beat, "phase_error_s": round(phase, 4), "confidence": estimate.confidence}


def analyze_sync(folder: Path, params: dict[str, Any]) -> dict[str, Any]:
    from core.audio_sync import SyncSource, analyze_sync as run

    angles = params["angles"]
    sources = [SyncSource(angle["key"], str(folder / angle["media"])) for angle in angles]
    result = run(sources, reference=params.get("reference"))
    found = result.by_key()
    expected = {angle["key"]: float(angle["expected_offset"]) for angle in angles}
    smallest = min(expected.values())
    rows: list[dict[str, Any]] = []
    worst = 0.0
    usable = True
    for angle in angles:
        item = found[angle["key"]]
        wanted = expected[angle["key"]] - smallest
        if item.offset is None:
            usable = False
            rows.append({"key": angle["key"], "expected": wanted, "found": None, "status": item.status.value, "confidence": item.confidence})
            continue
        error_ms = abs(item.offset - wanted) * 1000.0
        worst = max(worst, error_ms)
        rows.append({"key": angle["key"], "expected": round(wanted, 4), "found": round(item.offset, 4), "error_ms": round(error_ms, 2),
                     "status": item.status.value, "confidence": round(item.confidence, 3)})
    return {"ok": usable and worst <= SYNC_TOLERANCE_MS, "max_error_ms": round(worst, 2), "angles": rows,
            "reference": result.reference, "engine_seconds": round(result.elapsed, 2)}


def main(argv: list[str]) -> int:
    if len(argv) != 4 or argv[1] not in ("tempo", "sync"):
        print(json.dumps({"ok": False, "message": "usage : analyze (tempo|sync) DOSSIER JSON"}))
        return 1
    folder, params = Path(argv[2]), json.loads(argv[3])
    try:
        data = analyze_tempo(folder, params) if argv[1] == "tempo" else analyze_sync(folder, params)
    except Exception as error:                      # noqa: BLE001 - l'analyse ne doit pas faire croire à un plantage du banc
        print(json.dumps({"ok": False, "message": f"{type(error).__name__}: {str(error)[:200]}"}))
        return 1
    print(json.dumps(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
