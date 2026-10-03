"""Mesures de la synchronisation audio : N sources × durée, à froid puis avec le cache, temps et mémoire.

Usage::

    python -m tools.perf.audio_sync_bench --out docs/perf/audio-sync.json
    python -m tools.perf.audio_sync_bench --quick                       # petit jeu, sert aux tests
    python -m tools.perf.audio_sync_bench --sources 2,4 --minutes 5,30

Pour ne pas fabriquer des dizaines de fichiers géants, **un seul** média sonore est généré par durée (parole synthétique, WAV
16 bits) et chaque source en lit une **fenêtre différente** (``SyncSource.start`` / ``duration``) : chacune est donc décodée,
réduite en enveloppe et corrélée comme un fichier à part. Les décalages attendus sont connus (le début de chaque fenêtre) :
le banc vérifie aussi que la mesure est juste, pas seulement rapide.

Chaque scénario tourne dans un sous-processus (mémoire isolée) : temps à froid, temps avec le cache d'enveloppes, pic de
mémoire du processus et du plus gros FFmpeg. Les temps dépendent de la machine ; les tests vérifient les invariants (la
mémoire ne dépend pas de la durée, le cache accélère, les décalages sont justes).
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
for _entry in (ROOT, ROOT / "tests"):          # ``audio_scenes`` (générateur de parole synthétique) est un assistant de tests
    if str(_entry) not in sys.path:
        sys.path.insert(0, str(_entry))

SAMPLE_RATE = 16000
BLOCK_SECONDS = 60


def make_media(path: Path, seconds: float, seed: int = 1) -> Path:
    """Parole synthétique de ``seconds`` secondes, écrite par blocs d'une minute (jamais toute la durée en mémoire)."""
    import numpy as np

    from audio_scenes import speech

    rng = np.random.default_rng(seed)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        remaining = seconds
        while remaining > 0:
            length = min(BLOCK_SECONDS, remaining)
            block = np.clip(speech(rng, length), -1.0, 1.0)
            handle.writeframes((block * 32767).astype("<i2").tobytes())
            remaining -= length
    return path


def _worker(payload: str) -> None:
    """Sous-processus : une analyse à froid, une avec le cache ; mesures en JSON sur la sortie standard."""
    import resource

    from core.audio_sync import SyncSource, analyze_sync
    from core.audio_sync_cache import AudioSyncCache

    args = json.loads(payload)
    seconds, count, path = float(args["seconds"]), int(args["sources"]), args["path"]
    window = seconds * 0.7
    starts = [round(i * (seconds - window) / max(1, count - 1), 3) if count > 1 else 0.0 for i in range(count)]
    sources = [SyncSource(f"s{i}", path, start=start, duration=window) for i, start in enumerate(starts)]
    cache = AudioSyncCache(Path(args["cache"]))
    began = time.perf_counter()
    cold = analyze_sync(sources, reference="s0", cache=cache)
    cold_seconds = time.perf_counter() - began
    began = time.perf_counter()
    warm = analyze_sync(sources, reference="s0", cache=cache)
    warm_seconds = time.perf_counter() - began
    # source i commence à starts[i] dans le média : sur la timeline commune, décalage(i) = starts[i] − starts[0] + (valeur d'ancrage)
    expected = {f"s{i}": starts[i] for i in range(count)}
    placed = {item.key: item.offset for item in cold.sources}
    base = placed.get("s0")
    errors = [
        abs((placed[key] - base) - (expected[key] - expected["s0"]))
        for key in expected if placed.get(key) is not None and base is not None and key != "s0"
    ]
    unit = 1 if sys.platform == "darwin" else 1024
    print(json.dumps({
        "sources": count,
        "seconds_each": round(window, 1),
        "cold_s": round(cold_seconds, 2),
        "cached_s": round(warm_seconds, 2),
        "cache_speedup": round(cold_seconds / warm_seconds, 1) if warm_seconds > 0 else None,
        "worst_offset_error_ms": round(max(errors) * 1000, 2) if errors else None,
        "measured_ok": sum(1 for item in cold.sources if item.status.value in {"excellent", "good", "none"}),
        "cached_matches": [item.offset for item in cold.sources] == [item.offset for item in warm.sources],
        "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * unit / 1e6, 1),
        "rss_largest_ffmpeg_mb": round(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss * unit / 1e6, 1),
    }))


def bench(minutes: float, sources: int, folder: Path) -> dict:
    path = folder / f"speech-{minutes:g}min.wav"
    if not path.exists():
        make_media(path, minutes * 60)
    cache = folder / f"cache-{minutes:g}-{sources}"
    payload = json.dumps({"seconds": minutes * 60, "sources": sources, "path": str(path), "cache": str(cache)})
    done = subprocess.run(
        [sys.executable, "-m", "tools.perf.audio_sync_bench", "--worker", payload], cwd=ROOT, capture_output=True,
        text=True, timeout=3600, check=False, env={**os.environ, "PYTHONPATH": f"{ROOT}:{ROOT / 'tests'}"},
    )
    if done.returncode != 0:
        raise RuntimeError(done.stderr[-800:])
    result = json.loads(done.stdout.strip().splitlines()[-1])
    result["media_mb"] = round(path.stat().st_size / 1e6, 1)
    return result


def run(*, sources=(2, 4, 8, 16), minutes=(5, 30, 120)) -> dict:
    report: dict = {
        "meta": {"python": platform.python_version(), "machine": platform.machine(), "system": platform.system()},
        "scenarios": {},
    }
    with tempfile.TemporaryDirectory(prefix="kut-audio-sync-bench-") as workdir:
        for length in minutes:
            for count in sources:
                report["scenarios"][f"{count} sources × {length:g} min"] = bench(length, count, Path(workdir))
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path)
    parser.add_argument("--quick", action="store_true", help="petit jeu de mesures (tests)")
    parser.add_argument("--sources", default="2,4,8,16")
    parser.add_argument("--minutes", default="5,30,120")
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        _worker(args.worker)
        return 0
    if args.quick:
        report = run(sources=(2, 3), minutes=(0.75,))
    else:
        report = run(sources=tuple(int(x) for x in args.sources.split(",")), minutes=tuple(float(x) for x in args.minutes.split(",")))
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
