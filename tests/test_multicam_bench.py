"""Bancs Multicam : ils tournent, et ce qu'ils mesurent respecte les promesses de complexité.

Aucun test ne compare un temps à un seuil serré (la machine d'intégration continue n'est pas celle qui a produit
``docs/perf``) : ce sont des **invariants de structure** (une composition par angle *montré*, une bascule ajoute une seule
coupe, les angles cachés ne coûtent rien) et des bornes très larges, qui ne cèdent que si la complexité change d'ordre.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from core.multicam_ops import effective_angle_id, multicam_segment_at, switch_angle
from tools.perf import audio_sync_bench, multicam_bench

ROOT = Path(__file__).resolve().parent.parent
POSIX_ONLY = pytest.mark.skipif(
    sys.platform == "win32", reason="les bancs mesurent la mémoire avec le module POSIX `resource` (absent sous Windows)"
)
NEEDS_FFMPEG = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg indisponible")


# ---------------------------------------------------------------------------
# Plan de rendu : le coût suit les angles montrés et le nombre de coupes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("angles", "cuts"), [(4, 10), (4, 100), (16, 10), (16, 100)])
def test_only_the_shown_angles_are_composed(angles, cuts):
    """Chaque coupe montre un angle différent du précédent : il y a autant de compositions que d'angles montrés."""
    shown = min(angles, cuts + 1)
    report = multicam_bench.bench_plan(angles, cuts)
    assert report["segments"] == cuts + 1
    assert report["angles_rendered"] == report["compositions"] == report["input_files"] == shown


def test_hidden_angles_cost_nothing_in_the_export_graph():
    """16 ou 32 angles, mais les mêmes 11 montrés : le graphe FFmpeg est identique (les cachés ne sont pas lus)."""
    few, many = multicam_bench.bench_plan(16, 10), multicam_bench.bench_plan(32, 10)
    assert few["input_files"] == many["input_files"] == 11
    assert many["graph_kb"] == pytest.approx(few["graph_kb"], rel=0.02)


def test_graph_grows_linearly_with_the_number_of_cuts():
    """Dix fois plus de coupes : environ dix fois plus de graphe (9,6 mesuré), jamais le carré."""
    hundred, thousand = multicam_bench.bench_plan(4, 100), multicam_bench.bench_plan(4, 1000)
    assert thousand["graph_kb"] / hundred["graph_kb"] < 14.0
    assert thousand["angles_rendered"] == hundred["angles_rendered"] == 4      # 10× les coupes, pas un angle de plus


# ---------------------------------------------------------------------------
# Bascule : une opération locale
# ---------------------------------------------------------------------------


def _segments(project) -> int:
    return len(next(track for track in project.active_sequence.tracks if track.id == "V1").clips)


@pytest.mark.parametrize("cuts", [10, 1000])
def test_a_switch_adds_exactly_one_cut_whatever_the_project_length(cuts):
    project = multicam_bench.multicam_project(4, cuts)
    moment = cuts  # au milieu d'un segment (chacun dure 2 s)
    segment = multicam_segment_at(project, moment + 0.5)
    shown = effective_angle_id(project, segment)
    other = next(f"angle-{index}" for index in range(1, 5) if f"angle-{index}" != shown)
    before = _segments(project)
    result = switch_angle(project, moment + 0.5, other)
    assert result is not None and result.cut
    assert _segments(project) == before + 1


def test_switch_time_does_not_depend_on_the_number_of_cuts():
    """Borne très large (l'opération seule mesure 0,2 ms à 1 000 coupes) : elle ne casse qu'en cas de balayage complet."""
    assert multicam_bench.bench_switch(4, 1000)["switch_ms"] < 50.0


# ---------------------------------------------------------------------------
# Le banc lui-même : exécutable de bout en bout, sans fuite d'environnement
# ---------------------------------------------------------------------------


def test_the_window_bench_runs_end_to_end_in_its_own_process():
    """Régression : sans ``QApplication`` la fenêtre ne se construit pas, et fermer un projet modifié attend une réponse."""
    done = subprocess.run(
        [sys.executable, "-m", "tools.perf.multicam_bench", "--quick", "--ui"], cwd=ROOT, capture_output=True, text=True,
        timeout=180, check=False, env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
    )
    assert done.returncode == 0, done.stderr[-800:]
    report = json.loads(done.stdout)
    assert report["window"]["window_switch_ms"] > 0
    assert set(report["plan"]) == {"4x10", "4x100"} and set(report["switch"]) == {"4x10", "4x100"}


def test_the_window_bench_restores_the_environment_it_isolates():
    """Les mesures de flux qui suivent partent du même processus : elles ne doivent pas hériter de ``…=off``.

    Dans un processus à part : la fenêtre du banc pointe sur des dossiers jetables, qui ne doivent jamais fuir vers les
    autres tests (réglages mémorisés, caches).
    """
    script = (
        "import json, os, sys\n"
        "sys.path.insert(0, '.')\n"
        "from tools.perf import multicam_bench as bench\n"
        "read = lambda: {name: os.environ.get(name) for name in bench._ISOLATION}\n"
        "before = read()\n"
        "bench.bench_window(4, 4)\n"
        "print(json.dumps({'before': before, 'after': read()}))\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True, timeout=180, check=False,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen", "KUT_STUDIO_HARDWARE_DECODING": "on"},
    )
    assert done.returncode == 0, done.stderr[-800:]
    seen = json.loads(done.stdout.strip().splitlines()[-1])
    assert seen["before"]["KUT_STUDIO_HARDWARE_DECODING"] == "on"          # une valeur de l'utilisateur est bien retrouvée
    assert seen["after"] == seen["before"]


# ---------------------------------------------------------------------------
# Synchronisation audio : la mesure est juste, le cache accélère sans rien changer
# ---------------------------------------------------------------------------


@POSIX_ONLY
@NEEDS_FFMPEG
def test_audio_sync_bench_quick_run_is_accurate_and_cache_is_faithful():
    report = audio_sync_bench.run(sources=(2, 3), minutes=(0.75,))
    assert len(report["scenarios"]) == 2
    for name, scenario in report["scenarios"].items():
        assert scenario["measured_ok"] == scenario["sources"], name       # aucune source « incertaine » sur de la parole nette
        assert scenario["worst_offset_error_ms"] is not None and scenario["worst_offset_error_ms"] < 5.0, name
        assert scenario["cached_matches"] is True, name                  # le cache ne change aucun décalage
        assert scenario["cached_s"] < scenario["cold_s"], name          # et il évite bien le décodage
        assert scenario["peak_rss_mb"] < 1000.0, name


def test_published_audio_sync_numbers_keep_the_promised_shape():
    """``docs/perf/audio-sync.json`` : décalages justes, cache fidèle, et une mémoire très en deçà du son décodé.

    Le pic de mémoire croît doucement avec la durée (la corrélation d'enveloppes se fait par FFT sur toute la durée) mais
    presque pas avec le nombre de sources ; 16 sources de 2 h décodées d'un bloc occuperaient plusieurs Go.
    """
    scenarios = json.loads((ROOT / "docs" / "perf" / "audio-sync.json").read_text(encoding="utf-8"))["scenarios"]
    assert len(scenarios) == 12                                           # 2/4/8/16 sources × 5 / 30 / 120 min
    for name, scenario in scenarios.items():
        assert scenario["worst_offset_error_ms"] < 5.0, name
        assert scenario["cached_matches"] is True and scenario["measured_ok"] == scenario["sources"], name
        assert scenario["peak_rss_mb"] < 512.0, name
    two, sixteen = scenarios["2 sources × 120 min"], scenarios["16 sources × 120 min"]
    assert sixteen["peak_rss_mb"] < 1.5 * two["peak_rss_mb"]              # 8× les sources, moins de 1,5× la mémoire


def test_published_multicam_numbers_keep_the_promised_shape():
    """``docs/perf/multicam.json`` : une composition par angle montré, 1 000 coupes restent des dizaines de millisecondes."""
    report = json.loads((ROOT / "docs" / "perf" / "multicam.json").read_text(encoding="utf-8"))
    for name, plan in report["plan"].items():
        angles, cuts = (int(part) for part in name.split("x"))
        assert plan["compositions"] == plan["angles_rendered"] == min(angles, cuts + 1), name
    assert report["plan"]["16x1000"]["render_plan_ms"] < 500.0
    assert report["switch"]["16x1000"]["switch_ms"] < 50.0
    assert set(report["feeds"]) == {"4x1080p", "8x1080p", "4x4K", "8x4K+proxy"}
