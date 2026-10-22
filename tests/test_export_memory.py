"""Mémoire d'un export (défaut D-002 de la recette) : l'estimation de ce que FFmpeg retient, le refus avant de le lancer, et la
mesure sur le vrai FFmpeg que la mémoire ne dépend plus de ce qui sépare deux morceaux d'un même média.

Deux structures coexistent dans ces tests.

* **La structure d'aujourd'hui** (``core/export_inputs.py``) : chaque clip d'un média chaud a sa propre entrée positionnée, chaque
  instance d'une séquence imbriquée est composée pour sa fenêtre seule. L'estimation est celle des entrées ouvertes ; la file de
  vidéo n'existe plus (``test_a_cut_clip_no_longer_...``).
* **Les flux qui restent partagés** : au-delà du plafond d'entrées, dans un conteneur qui ne se positionne pas, ou (fixture
  ``legacy``) quand le plafond est ramené à 0, soit exactement le graphe d'avant le correctif. La file est alors celle que la
  recette a mesurée (loi des fenêtres, ``core/export_memory.py``) ; ces tests gardent la formule et le budget de mémoire.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

import core.export_engine as engine_module
import core.export_inputs as export_inputs
import core.export_memory as export_memory
import core.export_runner as runner_module
from core.export_engine import ExportEngine, ExportFormat, ExportPreset, ExportRequest, build_export_command, check_export_request
from core.export_inputs import MIN_PIPELINES, PIPELINE_FRAMES, max_pipelines
from core.export_memory import MEMORY_SHARE, UNKNOWN_MEMORY_BYTES, check_export_memory, estimate_export_queue
from core.export_runner import ExportStatus, run_export
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import build_render_plan
from core.scripting import Session, apply_script, parse_script
from core.visual_effects import ClipTransform, TransformKeyframe

FRAME = 1920 * 1080 * 4
GIB = 1024**3


@pytest.fixture
def legacy(monkeypatch):
    """Le graphe d'avant le correctif : aucune entrée positionnée, aucune instance (tout reste sur l'entrée partagée)."""
    monkeypatch.setattr(export_inputs, "max_pipelines", lambda *_size: 0)


@pytest.fixture
def probed(monkeypatch):
    """Les médias des tests n'existent pas : la sonde de première image rend le point d'entrée (un média dont la première image
    tombe pile dessus), ce que fait ``ffprobe`` sur un vrai fichier."""
    monkeypatch.setattr(export_inputs, "first_frame_time", lambda _path, at: float(at))


def _project(pieces, *, size=(1920, 1080), fps=25.0, transform=None, extra_assets=()) -> Project:
    """Un média de 130 s découpé en ``pieces`` : [(début sur la timeline, entrée source, sortie source), ...]."""
    assets = [MediaAsset("a", "/x/a.mp4", "A", 130.0, size[0], size[1], fps, "video", has_audio=False), *extra_assets]
    clips = [Clip(f"c{i}", "a", "V1", start, s_in, s_out) for i, (start, s_in, s_out) in enumerate(pieces)]
    if transform is not None:
        for clip in clips:
            clip.transform = transform
    return Project("p", width=size[0], height=size[1], fps=fps, media_assets=assets, tracks=[Track("V1", "V1", "video", clips=clips)])


def _request(project: Project, output: str = "/out/x.mp4", size=(1920, 1080), fps=25.0) -> ExportRequest:
    return ExportRequest(
        render_plan=build_render_plan(project), output_path=output, format=ExportFormat.MP4_H264,
        preset=ExportPreset(name="t", resolution=size, crf=30, audio_bitrate="64k"), fps=fps,
    )


def _cut(at: float) -> list[tuple[float, float, float]]:
    """Un clip de 130 s coupé en deux morceaux bout à bout, à ``at`` secondes."""
    return [(0.0, 0.0, at), (at, at, 130.0)]


def _pieces(count: int, length: float = 2.0) -> list[tuple[float, float, float]]:
    """``count`` morceaux bout à bout de ``length`` s, qui se suivent dans la source."""
    return [(i * length, i * length, (i + 1) * length) for i in range(count)]


# --- L'estimation : la structure d'aujourd'hui -------------------------------------------------------------------------------


def test_a_single_clip_retains_nothing():
    assert estimate_export_queue(_request(_project([(0.0, 0.0, 130.0)]))) == export_memory.QueueEstimate()


def test_a_cut_clip_no_longer_queues_the_media_before_the_second_piece():
    """Mesuré avant le correctif : une coupe à 20 s retenait 4 Go, à 60 s plus de 5,8 Go. Chaque morceau a maintenant son entrée :
    l'estimation est celle de deux entrées ouvertes, quelle que soit la position de la coupe."""
    near = estimate_export_queue(_request(_project(_cut(20.0))))
    far = estimate_export_queue(_request(_project(_cut(60.0))))
    assert near == far
    assert near.pipelines == 2
    assert near.families == 0
    assert near.bytes == 2 * PIPELINE_FRAMES * FRAME


def test_the_estimate_grows_with_the_number_of_open_inputs_not_with_the_media_between_them():
    """Dix morceaux de 6 s ou deux de 30 s : le coût est celui des entrées ouvertes."""
    ten = estimate_export_queue(_request(_project(_pieces(10, 6.0))))
    two = estimate_export_queue(_request(_project([(0.0, 0.0, 54.0), (54.0, 54.0, 60.0)])))
    assert ten.pipelines == 10 and two.pipelines == 2
    assert ten.bytes == 5 * two.bytes


def test_pieces_of_different_files_retain_nothing():
    other = MediaAsset("b", "/x/b.mp4", "B", 130.0, 1920, 1080, 25.0, "video", has_audio=False)
    project = _project([(0.0, 0.0, 60.0)], extra_assets=(other,))
    project.tracks[0].clips.append(Clip("c9", "b", "V1", 60.0, 60.0, 120.0))
    assert estimate_export_queue(_request(project)).bytes == 0


def test_a_piece_taken_from_the_same_place_retains_nothing():
    """Deux morceaux qui commencent au même instant source n'ont rien entre eux à garder."""
    assert estimate_export_queue(_request(_project([(0.0, 5.0, 25.0), (20.0, 5.0, 25.0)]))).bytes == 0


def test_the_open_inputs_follow_the_output_size():
    half_frame = estimate_export_queue(_request(_project(_cut(20.0)), size=(960, 540)))
    assert half_frame.bytes == 2 * PIPELINE_FRAMES * 960 * 540 * 4


def test_the_sound_retains_nothing_and_is_not_estimated():
    """Mesuré : un média de 20 min coupé à 5, 10 et 15 min garde 61 Mo quelle que soit la coupe (``amix`` lit au fil de l'eau)."""
    project = _project(_cut(60.0))
    project.media_assets[0].has_audio = True
    assert estimate_export_queue(_request(project)).bytes == 2 * PIPELINE_FRAMES * FRAME


def test_a_retimed_layer_is_left_out_of_the_estimate():
    from core.time_remapping import TimeRemapping

    project = _project(_cut(20.0))
    project.tracks[0].clips[0].time_remapping = TimeRemapping(speed=2.0)
    assert estimate_export_queue(_request(project)).bytes == 0


def test_the_flows_beyond_the_input_cap_stay_in_the_estimate_with_the_actual_geometry():
    """Au-delà du plafond d'entrées (8 en 8K), les morceaux qui restent sur l'entrée partagée gardent la file d'avant : la loi
    des fenêtres, à la taille du cadre."""
    size = (7680, 4320)
    cap = max_pipelines(*size)
    assert cap == MIN_PIPELINES
    queue = estimate_export_queue(_request(_project(_pieces(cap + 4), size=size), size=size))
    frame = 7680 * 4320 * 4
    assert queue.families == 1 and queue.pipelines == cap
    # Les quatre morceaux qui n'ont pas leur entrée sont ceux qui retiennent le plus (le plafond choisit les plus lourds,
    # les plus tardifs d'abord) ; sur l'entrée partagée, tous sauf le dernier retiennent leur fenêtre entière (2 s à 25 i/s).
    assert queue.frames == pytest.approx(3 * 2 * 25)
    assert queue.bytes == pytest.approx(3 * 2 * 25 * frame + cap * PIPELINE_FRAMES * frame)


# --- Les flux qui restent partagés : la loi des fenêtres (graphe d'avant) ---------------------------------------------------


def test_a_cut_clip_retains_everything_before_the_second_piece_in_full_rgba_frames(legacy):
    """Mesuré : une coupe à 20 s retient 20 s × 25 i/s d'images RGBA 1080p (≈ 4,1 Go), à 60 s trois fois plus."""
    near = estimate_export_queue(_request(_project(_cut(20.0))))
    far = estimate_export_queue(_request(_project(_cut(60.0))))
    assert near.bytes == 20 * 25 * FRAME
    assert far.bytes == 60 * 25 * FRAME
    assert near.families == far.families == 1


def test_the_shared_queue_does_not_grow_with_the_number_of_pieces_only_with_the_media_before_the_last_one(legacy):
    """Dix morceaux de 6 s ou deux de 30 s couvrant les mêmes 60 s : la même file (tout sauf le dernier morceau)."""
    ten = [(i * 6.0, i * 6.0, (i + 1) * 6.0) for i in range(10)]
    two = [(0.0, 0.0, 54.0), (54.0, 54.0, 60.0)]
    assert estimate_export_queue(_request(_project(ten))).bytes == estimate_export_queue(_request(_project(two))).bytes


def test_the_shared_queue_follows_the_output_size(legacy):
    half_frame = estimate_export_queue(_request(_project(_cut(20.0)), size=(960, 540)))
    assert half_frame.bytes == 20 * 25 * 960 * 540 * 4


@pytest.mark.parametrize(
    "transform, keyframes",
    [
        (ClipTransform(scale=0.5), ()),
        (ClipTransform(scale=3.0), ()),
        (ClipTransform(scale=1.0, scale_x=2.0), ()),
        (ClipTransform(rotation=45.0), ()),
        (ClipTransform(fill=True), ()),
        (ClipTransform(), (TransformKeyframe("scale", 0.0, 1.0), TransformKeyframe("scale", 10.0, 3.0))),
    ],
    ids=["scale-0.5", "scale-3", "scale-x-2", "rotation-45", "fill", "animated-1-to-3"],
)
def test_the_shared_queue_is_made_of_conformed_frames_whatever_the_clip_transform(legacy, transform, keyframes):
    """Mesuré (FFmpeg 6.1, 7.1, 9.0, voir ``core/export_memory.py``) : la file se forme avant ``scale`` / ``rotate`` du clip, à la
    taille du cadre de la composition. Ni une échelle < 1 (qui la réduirait), ni un agrandissement (qui la gonflerait), ni une
    rotation ne la changent : le test de mesure sur le vrai FFmpeg ci-dessous le prouve pour chacun."""
    project = _project(_cut(20.0))
    for clip in project.tracks[0].clips:
        clip.transform = transform
        clip.transform_keyframes = list(keyframes)
    assert estimate_export_queue(_request(project)).bytes == 20 * 25 * FRAME


def _multicam(switches: list[tuple[float, str]], total: float) -> Project:
    assets = [MediaAsset(f"cam{i}", f"/x/cam{i}.mp4", f"Cam{i}", total + 10.0, 1920, 1080, 25.0, "video", has_audio=True) for i in range(3)]
    project = Project("p", width=1920, height=1080, fps=25.0, media_assets=assets, tracks=[Track("V1", "V1", "video"), Track("A1", "A1", "audio")])
    ops = [
        {"op": "create_multicam_source", "as": "plateau", "name": "Plateau",
         "angles": [{"asset": f"cam{i}", "name": f"A{i}", "offset": 0.0} for i in range(3)]},
        {"op": "insert_multicam_clip", "sequence": {"ref": "plateau"}, "track": "V1", "at": 0.0, "angle": "A0", "source_in": 0.0, "source_out": total},
        *({"op": "switch_multicam_angle", "at": at, "angle": angle} for at, angle in switches),
    ]
    report = apply_script(Session(project), parse_script({"kut_script": 1, "ops": ops}))
    assert report.ok, report.error
    return project


def test_a_multicam_montage_is_composed_by_window_and_retains_no_video_queue(probed):
    """Trois angles, un changement toutes les 10 s sur 60 s : chaque instance est composée pour sa fenêtre."""
    switches = [(10.0, "A1"), (20.0, "A2"), (30.0, "A0"), (40.0, "A1"), (50.0, "A2")]
    queue = estimate_export_queue(_request(_multicam(switches, 60.0)))
    assert queue.families == 0                       # aucune file de vidéo partagée
    assert queue.pipelines >= 2 * 6                  # deux par instance (6 segments)


def test_a_multicam_media_that_cannot_be_probed_keeps_the_shared_render_and_its_queue():
    """Sans sonde (ffprobe absent, fichier illisible) l'instance n'est pas créée : le rendu partagé garde sa file, et le refus
    protège la machine."""
    switches = [(10.0, "A1"), (20.0, "A2"), (30.0, "A0"), (40.0, "A1"), (50.0, "A2")]
    queue = estimate_export_queue(_request(_multicam(switches, 60.0)))
    # A0 : segments 0-10 et 30-40 (le dernier à 30 s) -> 10 s ; A1 : 10-20 et 40-50 -> 10 s ; A2 : 20-30 et 50-60 -> 10 s.
    assert queue.bytes == 30 * 25 * FRAME
    assert queue.families == 3


def _layer(path: str, source_in: float, source_out: float, start: float, end: float):
    from core.render_plan import RenderLayer

    return RenderLayer(
        clip_id=f"{path}-{source_in}", asset_id="a", track_id="V1", track_index=0, source_path=path,
        source_in=source_in, source_out=source_out, timeline_start=start, timeline_end=end,
    )


def _two_plan_request(root_path: str, nested_path: str, nested_in: float) -> ExportRequest:
    """Un clip à la racine qui lit le début d'un média, un clip dans une séquence imbriquée qui lit ``nested_in`` plus loin."""
    from core.render_plan import NestedSequencePlan, RenderPlan

    inner = RenderPlan(1920, 1080, 25.0, 10.0, video_layers=(_layer(nested_path, nested_in, nested_in + 10.0, 0.0, 10.0),))
    root = RenderPlan(
        1920, 1080, 25.0, 10.0, video_layers=(_layer(root_path, 0.0, 10.0, 0.0, 10.0),),
        nested_sequences=(NestedSequencePlan(key="seq", sequence_id="seq", name="seq", plan=inner),),
    )
    return ExportRequest(
        render_plan=root, output_path="/out/x.mp4", format=ExportFormat.MP4_H264,
        preset=ExportPreset(name="t", resolution=(1920, 1080), crf=30, audio_bitrate="64k"), fps=25.0,
    )


def test_a_file_shared_by_the_root_and_a_nested_sequence_is_one_family():
    """Deux plans à un seul clip chacun (aucune file plan par plan) : la racine lit le début du média, la séquence imbriquée
    100 s plus loin, sur la même entrée FFmpeg (``_build_input_list``). Famille calculée sur toute la hiérarchie : les deux clips
    sont chauds et ont chacun leur entrée (deux entrées ouvertes) ; plan par plan, rien n'aurait été positionné."""
    queue = estimate_export_queue(_two_plan_request("/x/a.mp4", "/x/a.mp4", 100.0))
    assert queue.pipelines == 2 and queue.bytes == 2 * PIPELINE_FRAMES * FRAME


def test_a_file_shared_by_the_root_and_a_nested_sequence_queues_as_one_family_when_it_stays_shared(legacy):
    """Sous le graphe d'avant, la racine met 10 s d'images en file : la famille est celle du fichier, racine et séquence confondues."""
    request = _two_plan_request("/x/a.mp4", "/x/a.mp4", 100.0)
    queue = estimate_export_queue(request)
    assert queue.bytes == 10 * 25 * FRAME
    assert queue.families == 1
    assert export_memory.estimate_plan_queue(request.render_plan.nested_sequences[0].plan, 1920, 1080, 25.0).bytes == 0


def test_distinct_files_in_the_root_and_a_nested_sequence_retain_nothing():
    assert estimate_export_queue(_two_plan_request("/x/a.mp4", "/x/b.mp4", 100.0)).bytes == 0


def test_the_nested_frames_are_counted_at_the_size_of_their_own_composition(legacy):
    """Le clip de la séquence imbriquée attend à la taille de **sa** composition (ici 960 × 540), pas à celle de la racine."""
    from dataclasses import replace

    request = _two_plan_request("/x/a.mp4", "/x/a.mp4", 100.0)
    root = request.render_plan
    entry = root.nested_sequences[0]
    # Le clip imbriqué commence plus tôt que celui de la racine : c'est lui qui attend, à sa propre taille.
    inner = replace(entry.plan, width=960, height=540, video_layers=(_layer("/x/a.mp4", 0.0, 10.0, 0.0, 10.0),))
    outer = replace(root, video_layers=(_layer("/x/a.mp4", 100.0, 110.0, 0.0, 10.0),),
                    nested_sequences=(replace(entry, plan=inner),))
    request = replace(request, render_plan=outer)
    assert estimate_export_queue(request).bytes == 10 * 25 * 960 * 540 * 4


def test_a_container_that_cannot_be_positioned_keeps_its_shared_queue():
    """MPEG-TS décode des images corrompues quand on le positionne (FFmpeg 6.1 et 7.1) : il garde l'entrée partagée, et sa file reste
    estimée."""
    project = _project(_cut(20.0))
    project.media_assets[0].path = "/x/a.mts"
    queue = estimate_export_queue(_request(project))
    assert queue.bytes == 20 * 25 * FRAME and queue.families == 1 and queue.pipelines == 0


# --- Le refus, avant FFmpeg ----------------------------------------------------------------------------------------------


def test_an_export_that_cannot_fit_in_memory_is_refused_in_french_with_the_estimate(legacy):
    request = _request(_project(_cut(60.0)))      # 60 s × 25 i/s × 8,3 Mo ≈ 11,6 Go
    with pytest.raises(ValueError) as refused:
        check_export_memory(request, total_bytes=8 * GIB, available_bytes=6 * GIB)
    message = str(refused.value)
    assert "trop complexe pour être exporté d'un seul tenant" in message
    assert "mémoire estimée 12 Go" in message
    assert "mémoire disponible sur cette machine ≈ 6 Go sur 8 Go" in message
    assert "/x/a.mp4" not in message


def test_the_montage_that_was_refused_is_accepted_now():
    """Le geste « couper un long clip en deux » ne dépend plus de la durée qui sépare les morceaux : deux entrées ouvertes."""
    check_export_memory(_request(_project(_cut(60.0))), total_bytes=8 * GIB, available_bytes=6 * GIB)


def test_the_same_montage_is_accepted_on_a_machine_with_enough_memory(legacy):
    request = _request(_project(_cut(60.0)))
    check_export_memory(request, total_bytes=64 * GIB, available_bytes=48 * GIB)


def test_the_limit_is_a_share_of_the_available_memory(legacy):
    request = _request(_project(_cut(20.0)))       # 20 s × 25 × FRAME
    queue = 20 * 25 * FRAME
    available = int(queue / MEMORY_SHARE)
    check_export_memory(request, total_bytes=available, available_bytes=available + 1)
    with pytest.raises(ValueError):
        check_export_memory(request, total_bytes=available, available_bytes=available - 1)


def test_the_decision_uses_the_available_memory_not_the_installed_one(monkeypatch, legacy):
    """24 Go installés dont 9 Go disponibles : une file de ≈ 14 Go ne tient pas (elle passait sous 85 % des 24 Go installés)."""
    request = _request(_project(_cut(72.0)))
    assert 13 * GIB < estimate_export_queue(request).bytes < 15 * GIB
    monkeypatch.setattr(export_memory, "memory_snapshot", lambda: (24 * GIB, 9 * GIB))
    with pytest.raises(ValueError, match="mémoire disponible sur cette machine ≈ 9 Go sur 24 Go"):
        check_export_memory(request)
    monkeypatch.setattr(export_memory, "memory_snapshot", lambda: (24 * GIB, 20 * GIB))
    check_export_memory(request)


def test_a_momentarily_busy_machine_does_not_refuse_what_passed_a_minute_ago(monkeypatch, legacy):
    """La référence ne descend jamais sous un quart de la mémoire installée : l'instantané bouge d'un lancement à l'autre."""
    request = _request(_project(_cut(20.0)))          # ≈ 3,9 Go
    monkeypatch.setattr(export_memory, "memory_snapshot", lambda: (24 * GIB, 1 * GIB))
    check_export_memory(request)
    assert export_memory.memory_budget(24 * GIB, 1 * GIB)[0] == int(24 * GIB * export_memory.MEMORY_FLOOR * MEMORY_SHARE)


def test_a_missing_measure_falls_back_to_half_the_installed_memory(monkeypatch, legacy):
    request = _request(_project(_cut(72.0)))          # ≈ 13,9 Go
    monkeypatch.setattr(export_memory, "memory_snapshot", lambda: (24 * GIB, None))
    with pytest.raises(ValueError, match="disponible non mesurée"):
        check_export_memory(request)        # 85 % de 12 Go = 10,2 Go
    monkeypatch.setattr(export_memory, "memory_snapshot", lambda: (48 * GIB, None))
    check_export_memory(request)            # 85 % de 24 Go = 20,4 Go


def test_an_unknown_memory_size_only_refuses_gigantic_queues(monkeypatch, legacy):
    monkeypatch.setattr(export_memory, "memory_snapshot", lambda: (None, None))
    check_export_memory(_request(_project(_cut(60.0))))
    assert 60 * 25 * FRAME < UNKNOWN_MEMORY_BYTES * MEMORY_SHARE < 130 * 25 * FRAME
    huge = Project("p", width=1920, height=1080, fps=25.0,
                   media_assets=[MediaAsset("a", "/x/a.mp4", "A", 3600.0, 1920, 1080, 25.0, "video", has_audio=False)],
                   tracks=[Track("V1", "V1", "video", clips=[Clip("c0", "a", "V1", 0.0, 0.0, 1500.0), Clip("c1", "a", "V1", 1500.0, 1500.0, 1600.0)])])
    with pytest.raises(ValueError, match="trop complexe"):
        check_export_memory(_request(huge))


def _beyond_the_cap(size=(7680, 4320), count=40) -> ExportRequest:
    return _request(_project(_pieces(count, 10.0), size=size), size=size)


def test_a_montage_with_too_many_open_inputs_is_still_refused(monkeypatch):
    """Au-delà du plafond d'entrées, les morceaux restent sur l'entrée partagée : la file d'avant, qui ne tient pas."""
    monkeypatch.setattr(export_memory, "memory_snapshot", lambda: (8 * GIB, 6 * GIB))
    with pytest.raises(ValueError, match="trop complexe"):
        check_export_memory(_beyond_the_cap())
    check_export_memory(_beyond_the_cap(), total_bytes=4096 * GIB, available_bytes=2048 * GIB)


def test_the_d002_montage_is_no_longer_refused_on_a_24_gb_machine(probed):
    """Le montage de la recette : 20 min de multicam à 3 angles et ≈ 30 changements d'angle. Avant le correctif : plus de 200 Go
    de file de vidéo ; maintenant des instances par fenêtre."""
    switches = [(40.0 * i, f"A{i % 3}") for i in range(1, 30)]
    request = _request(_multicam(switches, 1200.0))
    queue = estimate_export_queue(request)
    assert queue.families == 0
    assert queue.bytes < 12 * GIB
    check_export_memory(request, total_bytes=24 * GIB, available_bytes=20 * GIB)


def test_the_d002_montage_is_still_refused_when_its_media_cannot_be_probed():
    switches = [(40.0 * i, f"A{i % 3}") for i in range(1, 30)]
    request = _request(_multicam(switches, 1200.0))
    assert estimate_export_queue(request).bytes > 150 * GIB
    with pytest.raises(ValueError, match="mémoire estimée"):
        check_export_memory(request, total_bytes=24 * GIB, available_bytes=20 * GIB)


def test_every_export_entry_point_applies_the_refusal(monkeypatch, tmp_path):
    """Script (``core.cli`` -> ``run_export``), interface (``ExportEngine.start``) et ``check_export_request`` : le même refus."""
    monkeypatch.setattr(export_memory, "memory_snapshot", lambda: (8 * GIB, 6 * GIB))
    size = (7680, 4320)
    request = _request(_project(_pieces(40, 10.0), size=size), output=str(tmp_path / "out.mp4"), size=size)

    with pytest.raises(ValueError, match="trop complexe"):
        check_export_request(request)

    launched: list[object] = []
    monkeypatch.setattr(runner_module, "_run_ffmpeg", lambda *a, **k: launched.append(a))
    result = run_export(request)
    assert result.status is ExportStatus.FAILED
    assert "trop complexe" in result.message
    assert not Path(request.output_path).exists()

    engine = ExportEngine()
    failures: list[str] = []
    engine.failed.connect(failures.append)
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(engine_module, "_ffmpeg_path", "/usr/bin/ffmpeg")
    engine.start(request)
    assert len(failures) == 1 and "trop complexe" in failures[0]
    assert launched == []


def test_ordinary_montages_are_never_refused(monkeypatch, probed):
    """Pas de faux positif : un clip seul, des clips de médias différents, des coupes courantes, un multicam court."""
    monkeypatch.setattr(export_memory, "memory_snapshot", lambda: (8 * GIB, 6 * GIB))
    check_export_memory(_request(_project([(0.0, 0.0, 130.0)])))
    check_export_memory(_request(_project(_cut(2.0))))
    check_export_memory(_request(_project(_cut(60.0))))               # le geste « couper un long clip en deux »
    check_export_memory(_request(_project(_pieces(30))))              # 30 morceaux bout à bout
    check_export_memory(_request(_multicam([(10.0, "A1"), (20.0, "A2")], 30.0)))


# --- La loi, sur le vrai FFmpeg ----------------------------------------------------------------------------------------
#
# Surcoût d'une coupe, 480×270, au-dessus du même montage sans coupe, graphe d'AVANT le correctif (fixture ``legacy``) :
#   FFmpeg 9.0 et 7.1 : 191 / 377 / 563 Mo pour une coupe à 15 / 30 / 45 s, soit l'estimation (185 / 371 / 556 Mo) à 3 % près ;
#   FFmpeg 6.1        : 430 / 535 / 640 Mo, soit une pente de 7 Mo/s au lieu de 12,4 (57 % de l'estimation) et ~245 Mo fixes
#                       en plus (la seconde chaîne de filtres), quelle que soit la transformation du clip.
# Les tests comparent des PENTES (différence entre deux coupes : le décalage fixe disparaît). Avec le correctif, la pente tombe à
# zéro : chaque morceau a sa propre entrée, la mémoire ne dépend plus de ce qui sépare les morceaux.


needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg absent")
needs_wait4 = pytest.mark.skipif(not hasattr(os, "wait4"), reason="mesure du pic de mémoire d'un processus fils (os.wait4) indisponible")
SOURCE_SECONDS = 60
W, H, FPS = 480, 270, 25
NEAR, FAR = 15.0, 45.0
REPEATS = 3


@pytest.fixture(scope="module")
def media(tmp_path_factory) -> str:
    if shutil.which("ffmpeg") is None:
        pytest.skip("FFmpeg absent")
    path = tmp_path_factory.mktemp("memory") / "source.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s={W}x{H}:r={FPS}:d={SOURCE_SECONDS},format=yuv420p",
         "-c:v", "libx264", "-crf", "35", "-preset", "ultrafast", str(path)], check=True, timeout=120)
    return str(path)


_WAIT4_HELPER = (
    "import os, signal, subprocess, sys\n"
    "p = subprocess.Popen(sys.argv[1:], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)\n"
    "def stop(*_):\n"
    "    try:\n"
    "        os.killpg(p.pid, signal.SIGKILL)\n"
    "    except OSError:\n"
    "        pass\n"
    "    os._exit(143)\n"
    "signal.signal(signal.SIGTERM, stop)\n"
    "try:\n"
    "    _pid, status, usage = os.wait4(p.pid, 0)\n"
    "except BaseException:\n"
    "    stop()\n"
    "print(os.waitstatus_to_exitcode(status), usage.ru_maxrss)\n"
)


def _peak_rss_mb(arguments: list[str], timeout: float = 600.0) -> float:
    """Pic de mémoire résidente de FFmpeg, lu par le système à la fin du processus (``ru_maxrss`` : exact, sans échantillonnage).

    FFmpeg est lancé **par un petit interprète intermédiaire**, pas par le processus de test : sous Linux, ``ru_maxrss`` d'un
    fils compte aussi le pic de mémoire du processus qui l'a lancé (le noyau reprend le maximum de l'ancienne image à
    ``exec``). Lancé depuis la suite complète (plusieurs Go déjà résidents), FFmpeg se voyait donc attribuer un plancher de
    plusieurs centaines de Mo, qui écrasait la pente mesurée (160 Mo au lieu de 371 sous FFmpeg 7.1 en CI, 350 Mo isolé).

    FFmpeg a son propre groupe de processus : sur délai dépassé, interruption ou ``SIGTERM``, l'intermédiaire tue ce groupe
    avant de sortir (jamais de FFmpeg orphelin)."""
    wrapper = subprocess.Popen(
        [sys.executable, "-I", "-c", _WAIT4_HELPER, *arguments], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        out, err = wrapper.communicate(timeout=timeout)
    except BaseException:
        wrapper.terminate()                                          # SIGTERM : l'intermédiaire tue le groupe de FFmpeg
        try:
            wrapper.wait(timeout=10)
        except subprocess.TimeoutExpired:
            wrapper.kill()
            wrapper.wait()
        raise
    assert wrapper.returncode == 0, err
    code, maxrss = out.split()
    assert int(code) == 0
    return int(maxrss) / (1024.0 * 1024.0 if sys.platform == "darwin" else 1024.0)     # macOS : octets ; Linux : Ko


@needs_wait4
def test_an_interrupted_measure_leaves_no_ffmpeg_behind(tmp_path):
    """Un délai dépassé tue aussi le processus lancé par l'intermédiaire, pas seulement l'intermédiaire."""
    marker = tmp_path / "pid"
    with pytest.raises(subprocess.TimeoutExpired):
        _peak_rss_mb(["sh", "-c", f"echo $$ > {marker}; exec sleep 60"], timeout=2.0)
    pid = int(marker.read_text())
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        try:                                                         # mort mais non récolté (conteneur sans init) : zombie
            if Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] == "Z":
                return
        except OSError:
            pass
        time.sleep(0.1)
    os.kill(pid, 9)
    pytest.fail("le processus mesuré est resté vivant après l'interruption")


@needs_wait4
@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="plancher hérité de ru_maxrss : propre à Linux")
def test_the_peak_measure_ignores_the_memory_of_the_test_process():
    """Le pic d'un fils ne doit pas être plancher par la mémoire du processus de test (la suite complète en a plusieurs Go)."""
    ballast = bytearray(400 * 1024 * 1024)
    ballast[::4096] = b"\x01" * len(ballast[::4096])                          # pages réellement résidentes
    try:
        assert _peak_rss_mb([sys.executable, "-I", "-c", "pass"]) < 200.0
    finally:
        del ballast


def _measure(path: str, at: float | None, tmp_path: Path, tag: str, transform: ClipTransform | None = None) -> tuple[float, int]:
    """(pic de mémoire de FFmpeg en Mo — médiane de ``REPEATS`` lancements —, file estimée en octets) d'un média coupé à ``at``."""
    asset = MediaAsset("a", path, "m", float(SOURCE_SECONDS), W, H, float(FPS), "video", has_audio=False)
    pieces = [(0.0, 0.0, float(SOURCE_SECONDS))] if at is None else [(0.0, 0.0, at), (at, at, float(SOURCE_SECONDS))]
    clips = [Clip(f"c{i}", "a", "V1", start, s_in, s_out) for i, (start, s_in, s_out) in enumerate(pieces)]
    for clip in clips:
        if transform is not None:
            clip.transform = transform
    project = Project("p", width=W, height=H, fps=float(FPS), media_assets=[asset], tracks=[Track("V1", "V1", "video", clips=clips)])
    request = ExportRequest(
        render_plan=build_render_plan(project), output_path=str(tmp_path / f"{tag}.mp4"), format=ExportFormat.MP4_H264,
        preset=ExportPreset(name="t", resolution=(W, H), crf=35, audio_bitrate="64k"), fps=float(FPS),
    )
    temporary: list[str] = []
    command = build_export_command(request, temporary_files=temporary)
    try:
        peaks = sorted(_peak_rss_mb(list(command.arguments)) for _ in range(REPEATS))
    finally:
        for name in temporary:
            Path(name).unlink(missing_ok=True)
    return peaks[len(peaks) // 2], estimate_export_queue(request).bytes


def _slope(path: str, tmp_path: Path, tag: str, transform: ClipTransform | None = None) -> tuple[float, float]:
    """(pente mesurée, pente estimée) en Mo : surcoût d'une coupe à ``FAR`` moins celui d'une coupe à ``NEAR``, **du même montage**
    (même transformation, donc mêmes filtres) : le décalage fixe d'une seconde chaîne de filtres s'annule."""
    near, near_estimate = _measure(path, NEAR, tmp_path, f"{tag}n", transform)
    far, far_estimate = _measure(path, FAR, tmp_path, f"{tag}f", transform)
    return far - near, (far_estimate - near_estimate) / (1024.0 * 1024.0)


@pytest.fixture(scope="module")
def legacy_slope(media, tmp_path_factory) -> tuple[float, float]:
    """La pente du graphe d'avant le correctif, mesurée ici (cette machine, cette version de FFmpeg)."""
    previous = export_inputs.max_pipelines
    export_inputs.max_pipelines = lambda *_size: 0
    try:
        return _slope(media, tmp_path_factory.mktemp("legacy"), "leg")
    finally:
        export_inputs.max_pipelines = previous


@needs_ffmpeg
@needs_wait4
def test_ffmpeg_memory_of_the_legacy_graph_grows_with_the_media_before_the_last_piece_and_the_estimate_bounds_it(legacy_slope):
    """Le graphe d'avant : une coupe plus loin retient plus d'images (la pente mesurée est positive), et l'estimation ne la
    sous-estime jamais : exacte sous FFmpeg ≥ 7 (±25 %), 1,75 fois trop haute sous FFmpeg 6.1 (mesuré)."""
    measured, estimated = legacy_slope
    assert estimated == pytest.approx(556.0 - 185.0, rel=0.02)           # 30 s de plus × 25 i/s × 480 × 270 × 4 octets
    assert measured > 0.0, "une coupe plus loin doit retenir plus d'images (FFmpeg a changé de comportement ?)"
    assert estimated >= 0.9 * measured                                   # jamais de sous-estimation
    assert estimated <= 2.2 * measured                                   # et une borne utile, pas un majorant sans valeur


@needs_ffmpeg
@needs_wait4
def test_ffmpeg_memory_no_longer_depends_on_the_media_before_the_last_piece(media, tmp_path, legacy_slope):
    """Avec le correctif, la pente (surcoût d'une coupe à 45 s moins celui d'une coupe à 15 s) tombe à une petite fraction de celle
    du graphe d'avant, mesurée au même endroit : chaque morceau a sa propre entrée. Rapport entre deux mesures, jamais un seuil
    absolu ; sans le correctif ce rapport vaut 1."""
    measured, estimated = _slope(media, tmp_path, "new")
    reference, _ = legacy_slope
    assert reference > 0.0
    assert measured <= 0.25 * reference, f"la mémoire suit encore la durée avant la coupe : pente {measured:.0f} Mo contre {reference:.0f} Mo"
    assert estimated == 0.0                                              # l'estimation ne dépend pas non plus de la coupe


@needs_ffmpeg
@needs_wait4
@pytest.mark.parametrize(
    "transform", [ClipTransform(scale=0.5), ClipTransform(scale=3.0), ClipTransform(rotation=45.0)], ids=["scale-0.5", "scale-3", "rotation-45"],
)
def test_the_measured_legacy_queue_does_not_depend_on_the_clip_transform(media, tmp_path, legacy_slope, monkeypatch, transform):
    """Sur les flux qui restent partagés, la pente d'un clip transformé est celle du clip sans transformation : la file est faite
    d'images conformées au cadre, pas de la taille après échelle ou rotation (3 fois plus grande ici : 9 fois plus d'octets)."""
    monkeypatch.setattr(export_inputs, "max_pipelines", lambda *_size: 0)
    moved, estimated = _slope(media, tmp_path, "tr", transform)
    measured, _ = legacy_slope
    assert 0.8 <= moved / measured <= 1.25
    assert estimated >= 0.9 * moved


@needs_ffmpeg
@needs_wait4
def test_ffmpeg_memory_of_a_multicam_montage_no_longer_depends_on_its_duration(media, tmp_path):
    """Trois angles, six segments : un montage de 24 s ou de 56 s retient la même mémoire (avant : proportionnelle à la durée)."""
    assets = [MediaAsset(f"cam{i}", media, f"Cam{i}", float(SOURCE_SECONDS), W, H, float(FPS), "video", has_audio=False) for i in range(3)]

    def peak(total: float, tag: str) -> float:
        project = Project("p", width=W, height=H, fps=float(FPS), media_assets=assets, tracks=[Track("V1", "V1", "video")])
        step = total / 6.0
        ops = [
            {"op": "create_multicam_source", "as": "plateau", "name": "Plateau",
             "angles": [{"asset": f"cam{i}", "name": f"A{i}", "offset": 0.0} for i in range(3)]},
            {"op": "insert_multicam_clip", "sequence": {"ref": "plateau"}, "track": "V1", "at": 0.0, "angle": "A0", "source_in": 0.0, "source_out": total},
            *({"op": "switch_multicam_angle", "at": step * k, "angle": f"A{k % 3}"} for k in range(1, 6)),
        ]
        report = apply_script(Session(project), parse_script({"kut_script": 1, "ops": ops}))
        assert report.ok, report.error
        request = ExportRequest(
            render_plan=build_render_plan(project), output_path=str(tmp_path / f"{tag}.mp4"), format=ExportFormat.MP4_H264,
            preset=ExportPreset(name="t", resolution=(W, H), crf=35, audio_bitrate="64k"), fps=float(FPS),
        )
        temporary: list[str] = []
        command = build_export_command(request, temporary_files=temporary)
        try:
            return sorted(_peak_rss_mb(list(command.arguments)) for _ in range(REPEATS))[REPEATS // 2]
        finally:
            for name in temporary:
                Path(name).unlink(missing_ok=True)

    short, long = peak(24.0, "s"), peak(56.0, "l")
    assert long <= short * 1.4, f"la mémoire suit encore la durée du multicam : {short:.0f} Mo -> {long:.0f} Mo"


@needs_ffmpeg
@needs_wait4
def test_ffmpeg_memory_of_the_sound_does_not_depend_on_the_cut(tmp_path):
    """Le son garde l'entrée partagée (le décodeur AAC ne se positionne pas sans changer quelques échantillons) : sa file serait de
    0,37 Mo par seconde de média retenue. Mesuré : ``amix`` lit au fil de l'eau, la mémoire ne suit pas la coupe."""
    seconds = 600
    path = tmp_path / "long.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s=64x36:r=5:d={seconds},format=yuv420p", "-f", "lavfi", "-i",
         f"anoisesrc=d={seconds}:c=pink:r=48000:a=0.3:seed=3", "-c:v", "libx264", "-crf", "40", "-preset", "ultrafast", "-c:a", "aac",
         "-b:a", "48k", "-shortest", str(path)], check=True, timeout=240)

    def peak(at: float, tag: str) -> float:
        asset = MediaAsset("a", str(path), "long", float(seconds), 64, 36, 5.0, "video", has_audio=True)
        clips = [Clip("c0", "a", "V1", 0.0, 0.0, at), Clip("c1", "a", "V1", at, at, float(seconds))]
        project = Project("p", width=64, height=36, fps=25.0, media_assets=[asset], tracks=[Track("V1", "V1", "video", clips=clips)])
        request = ExportRequest(
            render_plan=build_render_plan(project), output_path=str(tmp_path / f"{tag}.mp4"), format=ExportFormat.MP4_H264,
            preset=ExportPreset(name="t", resolution=(64, 36), crf=40, audio_bitrate="48k"), fps=25.0,
        )
        temporary: list[str] = []
        command = build_export_command(request, temporary_files=temporary)
        try:
            return sorted(_peak_rss_mb(list(command.arguments)) for _ in range(REPEATS))[REPEATS // 2]
        finally:
            for name in temporary:
                Path(name).unlink(missing_ok=True)

    near, far = peak(100.0, "n"), peak(500.0, "f")
    assert far <= near * 1.4, f"la mémoire du son suit la coupe : {near:.0f} Mo -> {far:.0f} Mo"
