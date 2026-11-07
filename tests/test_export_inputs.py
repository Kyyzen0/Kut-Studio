"""Entrées FFmpeg d'un export : une entrée positionnée par clip des médias chauds, une composition par fenêtre pour les séquences
imbriquées (défaut D-002 de la recette). Ces tests regardent le graphe et la commande ; la parité de rendu image par image est
prouvée sur le vrai FFmpeg dans ``tests/test_export_inputs_real.py``.
"""

from __future__ import annotations

import dataclasses
import math

import pytest

import core.export_inputs as export_inputs
from core.export_engine import ExportEngine, _build_input_list, decoder_threads, input_arguments
from core.export_inputs import (
    INSTANCE_TAIL,
    MAX_PIPELINES,
    MIN_PIPELINES,
    SeekedPath,
    decide_inputs,
    max_pipelines,
    plan_inputs,
)
from core.project_model import Clip, MediaAsset, Project, Sequence, Track
from core.render_plan import build_render_plan
from core.scripting import Session, apply_script, parse_script
from core.sequences import insert_sequence_clip
from core.time_remapping import TimeRemapping

W, H, FPS = 320, 180, 25.0


def _project(pieces, *, audio=False, name="/m/a b.mp4", assets=None, width=W, height=H) -> Project:
    """Un média coupé en morceaux ``(début timeline, entrée source, sortie source)``."""
    media = assets or [MediaAsset("a", name, "A", 600.0, width, height, FPS, "video", has_audio=audio)]
    clips = [Clip(f"c{i}", "a", "V1", start, s_in, s_out) for i, (start, s_in, s_out) in enumerate(pieces)]
    return Project("p", width=width, height=height, fps=FPS, media_assets=media, tracks=[Track("V1", "V1", "video", clips=clips)])


def _graph(project: Project, **kwargs):
    plan = build_render_plan(project)
    graph, video, audio, inputs = ExportEngine._build_filter_complex(plan, W, H, FPS, None, **kwargs)
    return plan, graph, video, audio, inputs


def _legacy(monkeypatch) -> None:
    """Le graphe d'avant le correctif : aucune entrée positionnée, aucune instance."""
    monkeypatch.setattr(export_inputs, "max_pipelines", lambda *_size: 0)


# --- La commande -----------------------------------------------------------------------------------------------------------


def test_a_seeked_path_is_a_string_and_positions_its_input():
    path = SeekedPath("/media/my clip.mp4", 19, 112.5)
    assert path == "/media/my clip.mp4" and isinstance(path, str)
    arguments = input_arguments(path, threads=8)
    assert arguments[arguments.index("-ss"):] == ["-ss", "19", "-t", "112.500", "-i", "/media/my clip.mp4"]
    assert arguments[arguments.index("-threads") + 1] == "1"            # un seul fil de décodage par entrée positionnée
    assert arguments.index("-threads") < arguments.index("-ss") < arguments.index("-i")


def test_a_plain_path_is_unchanged_and_the_frame_lists_are_never_positioned():
    plain = input_arguments("/m/a.mp4", threads=4)
    assert "-ss" not in plain and plain[-4:] == ["-threads", "4", "-i", "/m/a.mp4"]
    concat = input_arguments("/cache/layers.ffconcat")
    assert "-ss" not in concat and "concat" in concat


def test_a_positioned_input_keeps_the_local_path_check():
    assert input_arguments(SeekedPath("C:/media/clip.mp4", 3, 5.0))[-1] == "C:/media/clip.mp4"
    with pytest.raises(ValueError):
        input_arguments(SeekedPath("http://example.com/clip.mp4", 3, 5.0))


def test_the_decoder_threads_are_shared_between_the_shared_inputs_only():
    shared = ["/m/a.mp4", "/m/b.mp4"]
    assert decoder_threads(shared) == decoder_threads([*shared, SeekedPath("/m/c.mp4", 1, 2.0), SeekedPath("/m/d.mp4", 3, 2.0)])


# --- Quels clips ont leur entrée ---------------------------------------------------------------------------------------


def test_a_cut_clip_gets_one_positioned_input_per_piece_and_no_shared_input():
    plan, graph, _v, _a, inputs = _graph(_project([(0.0, 0.0, 20.0), (20.0, 20.0, 130.0)]))
    assert [type(item) for item in inputs] == [SeekedPath, SeekedPath]
    assert [(item.seek_start, item.seek_duration) for item in inputs] == [(0, 21.0), (19, 112.0)]
    assert all(item == "/m/a b.mp4" for item in inputs)
    # La coupe exacte reste faite par ``trim``, décalée de la position de l'entrée (un nombre entier de secondes).
    assert "[0:v]trim=start=0.0:end=20.0," in graph
    assert "[1:v]trim=start=1.0:end=111.0," in graph


def test_the_input_is_positioned_one_second_before_the_piece_on_a_whole_second():
    _plan, graph, _v, _a, inputs = _graph(_project([(0.0, 0.0, 5.5), (5.5, 5.5, 9.0), (9.0, 100.25, 101.0)]))
    assert [item.seek_start for item in inputs] == [0, 4, 99]
    assert "trim=start=1.5:end=5.0," in graph           # 5.5 - 4 ; 9.0 - 4
    assert "trim=start=1.25:end=2.0," in graph          # 100.25 - 99 ; 101 - 99


def test_a_cold_montage_keeps_the_legacy_graph_character_for_character(monkeypatch):
    for pieces in (
        [(0.0, 0.0, 130.0)],                                   # un clip seul
        [(0.0, 5.0, 25.0), (20.0, 5.0, 25.0)],                 # deux morceaux pris au même endroit
        [(0.0, 0.0, 0.2), (0.2, 0.2, 0.4)],                    # deux morceaux d'une fraction de seconde
    ):
        project = _project(pieces)
        _plan, graph, _v, _a, inputs = _graph(project)
        assert not any(isinstance(item, SeekedPath) for item in inputs)
        with monkeypatch.context() as patched:
            _legacy(patched)
            _plan2, legacy, _v2, _a2, legacy_inputs = _graph(project)
        assert graph == legacy and inputs == legacy_inputs


def test_the_legacy_switch_restores_the_shared_input(monkeypatch):
    _legacy(monkeypatch)
    plan, graph, _v, _a, inputs = _graph(_project([(0.0, 0.0, 20.0), (20.0, 20.0, 130.0)]))
    assert inputs == ["/m/a b.mp4"] == _build_input_list(plan)[0]
    assert "[0:v]trim=start=0.0:end=20.0," in graph and "[0:v]trim=start=20.0:end=130.0," in graph


def test_the_sound_always_keeps_the_shared_input():
    """Le décodeur AAC tire son bruit de substitution d'un état qui dépend de tous les paquets déjà décodés : un décodage qui part
    du milieu du fichier diffère du décodage continu."""
    _plan, graph, _v, _a, inputs = _graph(_project([(0.0, 0.0, 20.0), (20.0, 20.0, 130.0)], audio=True))
    assert inputs[0] == "/m/a b.mp4" and not isinstance(inputs[0], SeekedPath)
    assert [isinstance(item, SeekedPath) for item in inputs] == [False, True, True]
    assert "[0:a]atrim=start=0.0:end=20.0," in graph and "[0:a]atrim=start=20.0:end=130.0," in graph


def test_a_retimed_layer_keeps_the_shared_input_and_its_neighbours_are_positioned():
    project = _project([(0.0, 0.0, 20.0), (20.0, 20.0, 60.0), (60.0, 60.0, 100.0), (100.0, 100.0, 130.0)])
    project.tracks[0].clips[1].time_remapping = TimeRemapping(speed=2.0)
    _plan, graph, _v, _a, inputs = _graph(project)
    assert [getattr(item, "seek_start", None) for item in inputs] == [None, 0, 59, 99]
    assert "[1:v]trim=start=0.0:end=20.0" in graph
    assert "[2:v]trim=start=1.0:end=41.0" in graph and "[3:v]trim=start=1.0:end=31.0" in graph
    assert "[0:v]" in graph                                  # le clip accéléré lit l'entrée partagée


def test_only_containers_with_a_reliable_keyframe_index_are_positioned():
    for name in ("/m/a.mp4", "/m/B.MOV", "/m/c.mkv", "/m/d.webm", "/m/e.m4v"):
        assert export_inputs.positionable(name), name
    for name in ("/m/a.ts", "/m/b.MTS", "/m/c.m2ts", "/m/d.avi", "/m/e.mpg", "/m/f.flv", "/m/g.h264", "/m/h", "/m/i.mp4.bak"):
        assert not export_inputs.positionable(name), name
    for name in ("/m/a b.mts", "/m/a b.avi"):
        plan, graph, _v, _a, inputs = _graph(_project([(0.0, 0.0, 20.0), (20.0, 20.0, 130.0)], name=name))
        assert inputs == [name] and "[0:v]trim=start=20.0:end=130.0," in graph          # l'entrée partagée d'avant
        assert decide_inputs(plan, frame=(W, H)).shared                              # et sa file reste estimée


def test_a_layer_with_a_hostile_window_is_never_positioned():
    plan = build_render_plan(_project([(0.0, 0.0, 20.0), (20.0, 20.0, 130.0)]))
    layer = plan.video_layers[1]
    for bad in (math.nan, math.inf, -5.0, 1.0e12):
        broken = dataclasses.replace(layer, source_in=bad)
        assert export_inputs._video_window(broken, 0.0) is None
    assert export_inputs._video_window(dataclasses.replace(layer, source_out=layer.source_in), 0.0) is None
    assert export_inputs._video_window(dataclasses.replace(layer, source_path=""), 0.0) is None
    assert export_inputs._video_window(dataclasses.replace(layer, source_path="/c/layers.ffconcat"), 0.0) is None
    assert export_inputs._video_window(layer, 0.0) is not None


def test_a_one_frame_clip_and_a_montage_without_clip_are_handled():
    plan, _graph_text, _v, _a, inputs = _graph(_project([(0.0, 0.0, 1 / 25), (1 / 25, 1 / 25, 2 / 25)]))
    assert inputs == ["/m/a b.mp4"]
    empty = build_render_plan(Project("p", width=W, height=H, fps=FPS, tracks=[Track("V1", "V1", "video")]))
    paths, mapping, seeks = plan_inputs(empty)
    assert paths == [] and mapping == {} and not seeks


def test_a_preview_segment_positions_each_clip_from_its_first_needed_instant():
    """Segment d'aperçu (``origin``) : un clip déjà commencé est lu à partir de ce qui reste ; un clip terminé avant l'origine
    garde l'entrée partagée (sa fenêtre est vide : le graphe d'avant la rend vide aussi)."""
    project = _project([(0.0, 0.0, 40.0), (40.0, 40.0, 80.0), (80.0, 80.0, 130.0)])
    _plan, graph, _v, _a, inputs = _graph(project, origin=50.0)
    assert [(getattr(item, "seek_start", None), getattr(item, "seek_duration", None)) for item in inputs] == [
        (None, None), (49, 32.0), (79, 52.0),
    ]
    assert "[0:v]trim=start=50.0:end=40.0," in graph          # fini avant l'origine : fenêtre vide, comme avant
    assert "[1:v]trim=start=1.0:end=31.0," in graph           # 40 + 10 déjà passés -> 50 ; 50 - 49 = 1
    assert "[2:v]trim=start=1.0:end=51.0," in graph


# --- Le plafond d'entrées -----------------------------------------------------------------------------------------------


def test_the_input_cap_follows_the_frame_size():
    assert max_pipelines(320, 180) == MAX_PIPELINES
    assert max_pipelines(1920, 1080) > max_pipelines(3840, 2160) > MIN_PIPELINES - 1
    assert max_pipelines(7680, 4320) == MIN_PIPELINES
    assert max_pipelines(0, 0) == MAX_PIPELINES                  # une taille nulle n'empêche pas de borner


def test_beyond_the_cap_the_heaviest_pieces_keep_their_input_and_the_rest_stays_shared():
    count = max_pipelines(W, H) + 25
    pieces = [(i * 2.0, i * 2.0, (i + 1) * 2.0) for i in range(count)]
    plan = build_render_plan(_project(pieces))
    decision = decide_inputs(plan, frame=(W, H))
    assert decision.pipelines == max_pipelines(W, H)
    shared = [window for window in decision.shared]
    assert len(shared) == 25
    paths, _mapping, seeks = plan_inputs(plan, frame=(W, H))
    assert sum(isinstance(item, SeekedPath) for item in paths) == 0        # allouées à la demande
    assert paths == ["/m/a b.mp4"]                                          # le reste lit l'entrée partagée
    assert len(seeks._windows) == max_pipelines(W, H)                       # noqa: SLF001


# --- Séquences imbriquées par instance ----------------------------------------------------------------------------------


def _multicam(total=60.0, switches=((10.0, "A1"), (20.0, "A2"), (30.0, "A0"), (40.0, "A1"), (50.0, "A2"))) -> Project:
    assets = [MediaAsset(f"cam{i}", f"/m/cam{i}.mp4", f"Cam{i}", total + 10.0, W, H, FPS, "video", has_audio=True) for i in range(3)]
    project = Project("p", width=W, height=H, fps=FPS, media_assets=assets, tracks=[Track("V1", "V1", "video"), Track("A1", "A1", "audio")])
    ops = [
        {"op": "create_multicam_source", "as": "plateau", "name": "Plateau",
         "angles": [{"asset": f"cam{i}", "name": f"A{i}", "offset": 0.0} for i in range(3)]},
        {"op": "insert_multicam_clip", "sequence": {"ref": "plateau"}, "track": "V1", "at": 0.0, "angle": "A0", "source_in": 0.0, "source_out": total},
        *({"op": "switch_multicam_angle", "at": at, "angle": angle} for at, angle in switches),
    ]
    report = apply_script(Session(project), parse_script({"kut_script": 1, "ops": ops}))
    assert report.ok, report.error
    return project


@pytest.fixture
def probed(monkeypatch):
    monkeypatch.setattr(export_inputs, "first_frame_time", lambda _path, at: float(at))


def test_a_multicam_montage_is_composed_by_window(probed):
    plan, graph, _video, _a, inputs = _graph(_multicam())
    assert len(plan.nested_sequences) == 3
    decision = decide_inputs(plan, frame=(W, H))
    assert sorted(len(instances) for instances in decision.splits.values()) == [2, 2, 2]
    # Une composition par fenêtre : un fond transparent qui part du début de la fenêtre et s'arrête avec elle (+ la marge).
    assert f"color=c=black@0:s=320x180:r=25:d={10 + INSTANCE_TAIL:g},format=rgba[n0w0_bg]" in graph
    assert f"color=c=black@0:s=320x180:r=25:d={40 + INSTANCE_TAIL:g},trim=start=30.0,format=rgba[n0w1_bg]" in graph
    assert f"color=c=black@0:s=320x180:r=25:d={60 + INSTANCE_TAIL:g},trim=start=50.0,format=rgba[n2w1_bg]" in graph
    # Aucun ``split`` ne partage plus la vidéo d'un angle.
    assert "]split=" not in graph
    # Chaque clip d'angle a son entrée positionnée (une seconde avant la fenêtre, 0,1 s gardée avant la première image voulue).
    positioned = [(item.seek_start, item.seek_duration) for item in inputs if isinstance(item, SeekedPath)]
    assert positioned == [(0, 11.5), (28, 13.5), (8, 13.5), (38, 13.5), (18, 13.5), (48, 13.5)]
    # Temps absolu : PTS moins la position du début du clip dans l'entrée (ici le point d'entrée, première image pile dessus).
    assert "[3:v]trim=start=0.0:end=70.0,setpts=PTS-0/TB+0.001" in graph
    assert "[4:v]trim=start=1.9:end=42.0,setpts=PTS+28/TB+0.001" in graph


def test_the_sound_of_a_multicam_montage_keeps_its_shared_mix(probed):
    _plan, graph, _v, _a, _inputs = _graph(_multicam())
    assert "asplit=2" in graph                              # le mixage d'un angle repris est partagé, comme avant
    assert "n0w0_a" not in graph


def test_a_nested_sequence_with_something_else_than_plain_clips_is_not_split(probed):
    project = _multicam()
    plan = build_render_plan(project)
    entry = plan.nested_sequences[0]
    for change in (
        {"graphics_layers": ("x",)},
        {"transitions": ("x",)},
        {"subtitle_cues": ("x",)},
    ):
        altered = dataclasses.replace(entry.plan, **change)
        assert export_inputs._instance_bases(altered) is None
    assert export_inputs._instance_bases(entry.plan) is not None


def test_a_nested_media_that_cannot_be_probed_is_not_split(monkeypatch):
    monkeypatch.setattr(export_inputs, "first_frame_time", lambda _path, _at: None)
    plan = build_render_plan(_multicam())
    assert decide_inputs(plan, frame=(W, H)).splits == {}
    _plan, graph, _v, _a, inputs = _graph(_multicam())
    assert "n0w0_" not in graph and not any(isinstance(item, SeekedPath) for item in inputs[:3])


def test_a_retimed_reader_keeps_the_shared_render(probed):
    project = _multicam()
    first = project.tracks[0].clips[0]
    first.time_remapping = TimeRemapping(speed=2.0)
    plan = build_render_plan(project)
    decision = decide_inputs(plan, frame=(W, H))
    key = next(layer.nested_key for layer in plan.video_layers if layer.clip_id == first.id)
    assert key not in decision.splits


def test_the_number_of_instances_is_capped(probed):
    many = tuple((float(second), f"A{second % 3}") for second in range(1, 59))
    plan = build_render_plan(_multicam(switches=many))
    decision = decide_inputs(plan, frame=(7680, 4320))
    assert sum(len(instances) for instances in decision.splits.values()) <= max_pipelines(7680, 4320) // 2


def test_two_readers_of_the_same_window_share_one_composition(probed):
    project = _multicam(total=30.0, switches=((10.0, "A1"),))
    plan = build_render_plan(project)
    layer = plan.video_layers[0]
    twin = dataclasses.replace(layer, clip_id="twin", timeline_start=40.0, timeline_end=50.0)
    plan = dataclasses.replace(plan, video_layers=(*plan.video_layers, twin))
    graph, _v, _a, _inputs = ExportEngine._build_filter_complex(plan, W, H, FPS, None)
    assert graph.count("split=2") >= 1


# --- Séquence imbriquée générique (pas un multicam) ------------------------------------------------------------------------


def test_a_generic_nested_sequence_read_twice_far_apart_is_composed_per_window(probed):
    asset = MediaAsset("a", "/m/a.mp4", "A", 600.0, W, H, FPS, "video", has_audio=False)
    inner = Sequence("inner", "Inner", W, H, FPS, tracks=[Track("V1", "V1", "video", clips=[Clip("r", "a", "V1", 0.0, 0.0, 300.0)])])
    main = Sequence("main", "Master", W, H, FPS, tracks=[Track("V1", "V1", "video")])
    project = Project("p", media_assets=[asset], sequences=[main, inner], active_sequence_id="main")
    insert_sequence_clip(project, "inner", "V1", 0.0, source_in=0.0, source_out=20.0)
    insert_sequence_clip(project, "inner", "V1", 20.0, source_in=200.0, source_out=220.0)
    plan = build_render_plan(project)
    decision = decide_inputs(plan, frame=(W, H))
    assert [len(instances) for instances in decision.splits.values()] == [2]
    graph, _v, _a, inputs = ExportEngine._build_filter_complex(plan, W, H, FPS, None)
    assert [(item.seek_start, item.seek_duration) for item in inputs] == [(0, 21.5), (198, 23.5)]
    assert "color=c=black@0:s=320x180:r=25:d=220.5,trim=start=200.0,format=rgba[n0w1_bg]" in graph
    assert "[1:v]trim=start=1.9:end=102.0,setpts=PTS+198/TB+0.001" in graph


# --- Comptage et géométrie des entrées des séquences imbriquées -------------------------------------------------------------


def _generic_nested(layers: int, size: tuple[int, int], windows=((0.0, 20.0), (200.0, 220.0)), parent=(W, H)) -> Project:
    """Une séquence imbriquée de ``size`` qui superpose ``layers`` clips d'un même fichier (points d'entrée distincts), lue par
    deux fenêtres éloignées dans un projet de ``parent``."""
    asset = MediaAsset("a", "/m/a.mp4", "A", 900.0, size[0], size[1], FPS, "video", has_audio=False)
    clips = [Track(f"T{i}", f"T{i}", "video", clips=[Clip(f"r{i}", "a", f"T{i}", 0.0, i * 0.5, 300.0)]) for i in range(layers)]
    inner = Sequence("inner", "Inner", size[0], size[1], FPS, tracks=clips)
    main = Sequence("main", "Master", parent[0], parent[1], FPS, tracks=[Track("V1", "V1", "video")])
    project = Project("p", media_assets=[asset], sequences=[main, inner], active_sequence_id="main")
    at = 0.0
    for start, end in windows:
        insert_sequence_clip(project, "inner", "V1", at, source_in=start, source_out=end)
        at += end - start
    return project


def test_every_input_of_a_split_sequence_is_counted_before_accepting_the_split(probed):
    """20 calques x 2 fenêtres en 8K : le plafond (8) ne laisse pas scinder ; la commande n'ouvre alors aucune entrée par instance."""
    plan = build_render_plan(_generic_nested(20, (7680, 4320), parent=(7680, 4320)))
    decision = decide_inputs(plan, frame=(7680, 4320))
    assert decision.splits == {}
    graph, _v, _a, inputs = ExportEngine._build_filter_complex(plan, 7680, 4320, FPS, None)
    assert sum(isinstance(item, SeekedPath) for item in inputs) == decision.pipelines


def test_the_pipelines_announced_are_the_inputs_the_command_opens(probed):
    """Trois calques x deux fenêtres : trois entrées distinctes par fenêtre + un compositeur par fenêtre ; l'égalité porte sur la
    commande générée (les mêmes fichier, position et durée ne s'ouvrent qu'une fois)."""
    plan = build_render_plan(_generic_nested(3, (W, H)))
    decision = decide_inputs(plan, frame=(W, H))
    instances = sum(len(v) for v in decision.splits.values())
    assert instances == 2
    _g, _v, _a, inputs = ExportEngine._build_filter_complex(plan, W, H, FPS, None)
    assert decision.pipelines == sum(isinstance(item, SeekedPath) for item in inputs) + instances == 8
    assert sum(number for _owner, number in decision.resident) == decision.pipelines


def test_the_estimate_counts_each_pipeline_at_the_size_of_its_own_composition(probed):
    from core.export_memory import estimate_plan_queue
    from core.export_inputs import PIPELINE_FRAMES

    # 4K imbriqué dans un projet 1080p : 2 instances x (1 compositeur + 1 entrée) = 4 entrées, chacune à la taille 4K.
    big = estimate_plan_queue(build_render_plan(_generic_nested(1, (3840, 2160), parent=(1920, 1080))), 1920, 1080, FPS)
    assert big.pipelines == 4 and big.bytes == 4 * PIPELINE_FRAMES * 3840 * 2160 * 4
    # Plus PETITE que le parent : 480x270 dans 1080p.
    small = estimate_plan_queue(build_render_plan(_generic_nested(1, (480, 270), parent=(1920, 1080))), 1920, 1080, FPS)
    assert small.pipelines == 4 and small.bytes == 4 * PIPELINE_FRAMES * 480 * 270 * 4
