"""Banc du flux optique : il tourne, et ce qu'il mesure respecte les promesses de structure.

Aucun test ne compare un temps à un seuil serré (la machine d'intégration continue n'est pas celle qui a produit
``docs/perf/optical-flow.json``) : ce sont des **invariants** (la qualité réduit la grille d'analyse donc la place du cache, le
mélange est des ordres de grandeur moins cher que le flux, le flux préparé se relit sans recalcul, les modes interpolés
fabriquent les mêmes images) et des bornes très larges.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

from tools.perf import flow_bench

pytestmark = [
    pytest.mark.skipif(sys.platform == "win32", reason="le banc mesure la mémoire avec le module POSIX `resource`"),
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg indisponible"),
]


@pytest.fixture(scope="module")
def report():
    return flow_bench.run(sizes=((320, 180),), qualities=("draft", "balanced"), render_sizes=((320, 180),))


def test_the_report_has_the_four_families_of_measures(report):
    assert {"meta", "estimate", "synthesize", "render", "accuracy"} <= set(report)
    assert len(report["estimate"]) == 2 and len(report["synthesize"]) == 1 and len(report["render"]) == 4
    json.dumps(report)                                                                  # sérialisable tel quel


def test_a_lower_quality_analyzes_a_smaller_grid_and_stores_less(report):
    draft, balanced = report["estimate"]
    assert (draft["quality"], balanced["quality"]) == ("draft", "balanced")
    width = lambda item: int(item["analysis_grid"].split("x")[0])                       # noqa: E731
    assert width(draft) < width(balanced) <= 320
    assert draft["stored_kb"] < balanced["stored_kb"]
    assert draft["backend"] == balanced["backend"] == "numpy"
    assert draft["status"] == balanced["status"] == "none"                              # un vrai flux, pas un repli


def test_a_clean_pair_is_trusted(report):
    assert all(item["mean_confidence"] > 0.6 for item in report["estimate"])


def test_a_blend_is_orders_of_magnitude_cheaper_than_a_flow_synthesis(report):
    item = report["synthesize"][0]
    assert item["blend_s"] * 20 < item["flow_synthesis_s"] and item["fallback"] == "none"


def test_the_three_modes_render_the_same_number_of_frames_and_the_interpolated_ones_the_same_images(report):
    by_mode = {(item["mode"], item["quality"]): item for item in report["render"]}
    sampling = by_mode[("sampling", "-")] if ("sampling", "-") in by_mode else by_mode[("sampling", "balanced")]
    blending = next(item for item in report["render"] if item["mode"] == "blending")
    flows = [item for item in report["render"] if item["mode"] == "optical_flow"]
    assert {item["output_frames"] for item in report["render"]} == {240}
    assert sampling["synthesized"] == 0 and sampling["prepared_stream_mb"] == 0
    assert blending["synthesized"] == 177 and all(item["synthesized"] == 177 for item in flows)   # 3 images sur 4 à 25 %


def test_the_vectors_are_computed_once_per_pair_and_never_for_a_blend(report):
    blending = next(item for item in report["render"] if item["mode"] == "blending")
    flows = [item for item in report["render"] if item["mode"] == "optical_flow"]
    assert blending["pairs_computed"] == 0
    assert all(item["pairs_computed"] == 59 for item in flows)                          # 60 images source, 59 paires


def test_a_second_passage_reuses_the_prepared_stream_instead_of_recomputing(report):
    for item in report["render"]:
        if item["synthesized"]:
            assert item["warm_reused"] is True and item["warm_prepare_s"] * 5 < max(item["prepare_s"], 0.05)


def test_memory_stays_bounded_for_a_small_clip(report):
    assert all(item["peak_rss_mb"] < 1500 for item in report["render"])


def test_the_command_line_writes_the_json(tmp_path):
    out = tmp_path / "bench.json"
    assert flow_bench.main(["--quick", "--out", str(out)]) == 0
    written = json.loads(Path(out).read_text(encoding="utf-8"))
    assert written["render"] and written["estimate"]


def test_the_flow_beats_a_plain_blend_on_every_moving_scene_of_the_accuracy_suite(report):
    rows = {row["scene"]: row for row in report["accuracy"]}
    assert len(rows) == 10
    for name, row in rows.items():
        assert row["flow_error"] <= row["blend_error"], name
        assert row["flow_iou"] >= row["blend_iou"] - 1e-6, name
        assert row["flow_iou"] > 0.97, name                                              # l'objet est au bon endroit
    for name in ("translation +8 px", "deux objets opposés", "croisement", "diagonale (7, 5)"):
        assert rows[name]["flow_error"] * 4 < rows[name]["blend_error"], name         # nettement, pas à peine


def test_the_flow_images_of_a_render_are_not_mostly_fallbacks(report):
    """Régression : une mire presque immobile partait entièrement en repli (confiance 0,16 à 0,48) et le bilan affichait « 1,0 »."""
    for item in report["render"]:
        if item["mode"] == "optical_flow":
            assert item["degraded"] <= item["synthesized"] // 4, (item["quality"], item["fallbacks"])
            assert item["mean_confidence"] > 0.6, (item["quality"], item["mean_confidence"])


def _document(tmp_path, body="avant\n"):
    path = tmp_path / "doc.md"
    path.write_text(
        f"{body}<!-- BENCH:MEASURES -->\nancien\n<!-- /BENCH:MEASURES -->\nmilieu\n<!-- BENCH:ACCURACY -->\n<!-- /BENCH:ACCURACY -->\nfin\n",
        encoding="utf-8",
    )
    return path


def test_the_document_tables_are_rewritten_from_the_report_and_only_between_the_markers(report, tmp_path):
    path = _document(tmp_path)
    flow_bench.update_document(path, report)
    text = path.read_text(encoding="utf-8")
    assert text.startswith("avant\n") and text.endswith("fin\n") and "\nmilieu\n" in text and "ancien" not in text
    assert "| Image | Qualité |" in text and "320x180" in text and "| Scène |" in text
    assert all(row["scene"] in text for row in report["accuracy"])                       # une ligne par scène mesurée
    flow_bench.update_document(path, report)                                             # idempotent : le document ne dérive pas
    assert path.read_text(encoding="utf-8") == text


def test_the_document_is_updated_from_a_json_report_without_measuring(report, tmp_path):
    saved, path = tmp_path / "report.json", _document(tmp_path)
    saved.write_text(json.dumps(report), encoding="utf-8")
    assert flow_bench.main(["--from-json", str(saved), "--update-doc", str(path)]) == 0
    assert "| Scène |" in path.read_text(encoding="utf-8")
    with pytest.raises(SystemExit):
        flow_bench.main(["--from-json", str(saved)])                                     # sans document : rien à faire


def test_a_document_missing_its_markers_is_refused_not_silently_left_stale(report, tmp_path):
    path = tmp_path / "bare.md"
    path.write_text("pas de marqueurs\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="introuvable"):
        flow_bench.update_document(path, report)
    assert path.read_text(encoding="utf-8") == "pas de marqueurs\n"
