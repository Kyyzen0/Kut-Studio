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
    return flow_bench.run(
        sizes=((320, 180),), qualities=("draft", "balanced"), render_sizes=((320, 180),),
        extreme_scenes=("translation +8 px",), extreme_ratios=(0.25, 0.05),
    )


def test_the_report_has_the_five_families_of_measures(report):
    assert {"meta", "estimate", "synthesize", "render", "accuracy", "extreme"} <= set(report)
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
        f"{body}<!-- BENCH:MEASURES -->\nancien\n<!-- /BENCH:MEASURES -->\nmilieu\n<!-- BENCH:ACCURACY -->\n<!-- /BENCH:ACCURACY -->\n"
        "<!-- BENCH:EXTREME -->\n<!-- /BENCH:EXTREME -->\n<!-- BENCH:COST -->\n<!-- /BENCH:COST -->\nfin\n",
        encoding="utf-8",
    )
    return path


def test_the_document_tables_are_rewritten_from_the_report_and_only_between_the_markers(report, tmp_path):
    path = _document(tmp_path)
    flow_bench.update_document(path, report)
    text = path.read_text(encoding="utf-8")
    assert text.startswith("avant\n") and text.endswith("fin\n") and "\nmilieu\n" in text and "ancien" not in text
    assert "| Image | Qualité |" in text and "320x180" in text and "| Scène |" in text
    assert "| Ralenti |" in text and "5 %" in text and "Total, 1 s de source en 320x180" in text   # ralenti extrême et coût estimé
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


# ---------------------------------------------------------------------------
# Position de l'objet et ralenti extrême
# ---------------------------------------------------------------------------


def _scene_pair():
    from flow_scenes import Body, Scene, linear

    scene = Scene(320, 180, [Body(60, linear(100, 90, 8, 0))])
    return scene, scene.render(4.0)


def test_the_best_offset_finds_a_known_shift_to_a_fraction_of_a_pixel():
    import numpy as np

    scene, truth = _scene_pair()
    shifted = np.roll(truth, 3, axis=1)
    dx, dy = flow_bench.best_offset(shifted, truth)
    assert dx == pytest.approx(3.0, abs=0.05) and dy == pytest.approx(0.0, abs=0.05)
    half = scene.render(4.0 + 0.375)
    dx, dy = flow_bench.best_offset(half, truth)                                        # 0,375 × 8 px de mouvement réel
    assert dx == pytest.approx(3.0, abs=0.25)
    assert flow_bench.best_offset(np.zeros_like(truth), np.zeros_like(truth)) is None   # pas d'objet : pas de position


def test_a_blend_places_the_object_off_while_the_flow_places_it_on_the_truth():
    import numpy as np

    from core.optical_flow import FlowParams, NumpyBackend, OpticalFlowEngine, analysis_plane, blend_frames
    from core.time_remapping import FlowQuality
    from flow_scenes import rgb

    scene, _ = _scene_pair()
    first, second = rgb(scene.render(4.0)), rgb(scene.render(5.0))
    pair = NumpyBackend().analyze(analysis_plane(first, 1), analysis_plane(second, 1), FlowParams(scale=1, levels=5, iterations=3,
                                                                                                  window=5, smoothing=4))
    engine = OpticalFlowEngine(FlowQuality.BALANCED)
    truth = scene.render(4.25)
    made = engine.interpolator.interpolate(first, second, pair, 0.25).pixels[:, :, 0]
    mixed = blend_frames(first, second, 0.25)[:, :, 0]
    flow_place, blend_place = (np.hypot(*flow_bench.best_offset(image, truth)) for image in (made, mixed))
    assert flow_place < 0.3 and blend_place > 1.0, (flow_place, blend_place)           # 2 px de mouvement réel à t = ¼


def test_the_accuracy_suite_reports_a_position_for_single_object_scenes_only(report):
    rows = {row["scene"]: row for row in report["accuracy"]}
    assert rows["translation +8 px"]["flow_position"] < 0.5 < rows["translation +8 px"]["blend_position"]
    assert rows["croisement"]["flow_position"] is None and rows["deux objets opposés"]["blend_position"] is None


def test_at_extreme_slowdowns_the_flow_keeps_its_images_right_and_its_trajectory_smooth(report):
    rows = report["extreme"]
    assert [(row["ratio"], row["images_per_pair"]) for row in rows] == [(0.25, 3), (0.05, 19)]
    for row in rows:
        assert row["flow_error"] * 4 < row["blend_error"], row                            # nettement meilleur que le mélange
        assert row["flow_position_max"] < 0.5, row                                        # l'objet reste là où il doit être
        assert row["flow_jerk"] < 0.5 < row["blend_jerk"], row                            # trajectoire régulière contre sauts de fantôme
    slow, extreme = rows
    assert extreme["flow_error_max"] < 3.0 * max(slow["flow_error_max"], 1.0)             # 5 % : pas de moins bonnes images, seulement plus
