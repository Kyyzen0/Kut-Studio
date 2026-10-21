"""Format déclaratif : rythme, plans et sous-titres (lot 5 de l'extension 0.8.0) : `set_beat_grid`, `cut_clips_on_beats`, `cut_clip_at_scenes`,
`add_subtitle_lines`, `add_karaoke_lines`…

Aucun verbe n'analyse un média : les temps et les lignes viennent du script. Chaque verbe appelle la fonction du modèle que l'interface appelle
après son analyse : le test de parité fait le même appel directement sur une copie et compare les projets. Les refus (verrous, bornes de
ressources, ce que le modèle écarterait en silence) laissent le projet strictement inchangé.
"""

from __future__ import annotations

import copy
import time
from dataclasses import replace

import pytest

from core.auto_captions import CaptionLine, add_karaoke_lines, add_subtitle_lines
from core.beat_edit import cut_clips_on_beats, distribute_on_grid
from core.beat_grid import BeatGrid
from core.graphics import add_graphic_clip, update_graphic
from core.project_model import Clip, MediaAsset, Project, Track
from core.scene_detection import cut_clip_at_scenes
from core.scripting import (
    ScriptInvalid,
    Session,
    apply_script,
    created_ids,
    parse_script,
)
from core.scripting import auto_rules
from core.scripting.verbs import VERBS
from core.sequences import create_sequence
from core.timeline_operations import find_clip
from core.visual_effects import TransformKeyframe
from ui import i18n

NEW_VERBS = (
    "set_beat_grid",
    "remove_beat_grid",
    "cut_clips_on_beats",
    "distribute_on_grid",
    "cut_clip_at_scenes",
    "add_subtitle_lines",
    "add_karaoke_lines",
)


@pytest.fixture(autouse=True)
def _french(monkeypatch):
    monkeypatch.setenv("KUT_STUDIO_LANG", "fr")
    i18n.reset_for_tests()
    yield
    i18n.reset_for_tests()


def _project(
    *, grid: BeatGrid | None = BeatGrid(120.0), locked_v1: bool = False
) -> Project:
    project = Project(
        "Démo",
        width=320,
        height=180,
        fps=30.0,
        media_assets=[
            MediaAsset(
                "m",
                "/x/intro.mp4",
                "intro",
                60.0,
                320,
                180,
                30.0,
                "video",
                has_audio=True,
            )
        ],
        tracks=[
            Track(
                "V1",
                "V1",
                "video",
                locked=locked_v1,
                clips=[
                    Clip("a", "m", "V1", 0.0, 0.0, 5.0),
                    Clip("b", "m", "V1", 5.0, 0.0, 3.0),
                ],
            ),
            Track("V2", "V2", "video", clips=[Clip("c", "m", "V2", 10.0, 0.0, 2.0)]),
            Track(
                "V3",
                "V3",
                "video",
                locked=True,
                clips=[Clip("d", "m", "V3", 20.0, 0.0, 4.0)],
            ),
            Track("A1", "A1", "audio"),
        ],
    )
    project.active_sequence.beat_grid = grid
    return project


def _op(verb: str, /, **fields) -> dict:
    return {"op": verb, **fields}


def _apply(project: Project, *ops: dict):
    session = Session(project)
    return session, apply_script(
        session, parse_script({"kut_script": 1, "ops": list(ops)})
    )


def _snapshot(project: Project):
    return (copy.deepcopy(project.sequences), copy.deepcopy(project.media_assets))


def _refused(project: Project, *ops: dict):
    before = _snapshot(project)
    session, report = _apply(project, *ops)
    assert not report.ok
    assert _snapshot(session.project) == before
    return report


def _shape(project: Project):
    """Le projet sans les identifiants que le modèle génère au hasard (clips, médias techniques) : de quoi comparer deux créations."""
    from dataclasses import asdict

    def clip(c):
        data = {k: v for k, v in asdict(c).items() if k not in ("id", "asset_id")}
        for field in ("animation", "transform_keyframes"):
            data[field] = [
                {k: v for k, v in kf.items() if k != "id"} for kf in data[field]
            ]
        return data

    tracks = [(t.id, t.type, [clip(c) for c in t.clips]) for t in project.tracks]
    assets = sorted(
        (a.media_type, a.name, a.duration, a.width, a.height)
        for a in project.media_assets
    )
    return tracks, assets


def _parity(op: dict, direct, factory=_project, shape=_snapshot) -> Project:
    scripted = factory()
    session, report = _apply(scripted, op)
    assert report.ok, report.error
    reference = factory()
    direct(reference)
    assert shape(session.project) == shape(reference)
    return session.project


def _crowd(
    project: Project, count: int, *, sequence: bool = False, graphics: bool = False
) -> None:
    """Remplit le projet de ``count`` clips (dans une AUTRE séquence si ``sequence``, que ``Project.tracks`` ne montre pas)."""
    clips = [Clip(f"x{n}", "m", "X1", 100.0 + n, 0.0, 0.5) for n in range(count)]
    if graphics:
        from core.graphics import GraphicOverlay, GraphicType

        clips = [
            replace(clip, graphic=GraphicOverlay(type=GraphicType.TEXT, text="x"))
            for clip in clips
        ]
    track = Track("X1", "X1", "graphics" if graphics else "video", clips=clips)
    if sequence:
        other = create_sequence(project, "autre")
        other.tracks.append(track)
    else:
        project.tracks.append(track)


# --- Forme -----------------------------------------------------------------------------------------------------------------


def test_the_verbs_are_in_the_table_and_in_the_schema():
    from core.scripting.schema import json_schema

    assert set(NEW_VERBS) <= set(VERBS)
    for name in NEW_VERBS:
        assert (
            VERBS[name].creates is None
            and VERBS[name].doc
            and not VERBS[name].needs_context
        )
    titles = {
        item["title"] for item in json_schema()["properties"]["ops"]["items"]["oneOf"]
    }
    assert set(NEW_VERBS) <= titles


# --- Grille rythmique ------------------------------------------------------------------------------------------------------


def test_set_beat_grid_matches_the_model_and_replaces_the_old_one():
    project = _parity(
        _op("set_beat_grid", bpm=90, offset=0.25, beats_per_bar=3),
        lambda p: setattr(p.active_sequence, "beat_grid", BeatGrid(90.0, 0.25, 3)),
    )
    assert project.active_sequence.beat_grid == BeatGrid(90.0, 0.25, 3)
    session, report = _apply(_project(), _op("set_beat_grid", bpm=100))
    assert report.ok and session.project.active_sequence.beat_grid == BeatGrid(
        100.0, 0.0, 4
    )
    assert session.undo() and session.project.active_sequence.beat_grid == BeatGrid(
        120.0
    )


@pytest.mark.parametrize(
    "operation",
    [
        _op("set_beat_grid", bpm=10),
        _op("set_beat_grid", bpm=301),
        _op("set_beat_grid", bpm=float("nan")),
        _op("set_beat_grid", bpm=120, beats_per_bar=0),
        _op("set_beat_grid", bpm=120, beats_per_bar=17),
        _op("set_beat_grid", bpm=120, offset=1e12),
        _op("set_beat_grid", bpm=120, beats_per_bar=2.5),
        _op("set_beat_grid"),
    ],
)
def test_set_beat_grid_refuses_what_the_model_would_refuse_at_parse_time(operation):
    with pytest.raises(ScriptInvalid):
        parse_script({"kut_script": 1, "ops": [operation]})


def test_remove_beat_grid_removes_it_and_refuses_when_there_is_none():
    session, report = _apply(_project(), _op("remove_beat_grid"))
    assert report.ok and session.project.active_sequence.beat_grid is None
    assert session.undo() and session.project.active_sequence.beat_grid == BeatGrid(
        120.0
    )
    _refused(_project(grid=None), _op("remove_beat_grid"))


def test_the_grid_is_the_active_sequences_not_another_ones():
    project = _project(grid=None)
    other = create_sequence(project, "autre")
    other.beat_grid = BeatGrid(60.0)
    _refused(project, _op("cut_clips_on_beats", clips=["a"]))


# --- Couper sur les temps --------------------------------------------------------------------------------------------------


def test_cut_clips_on_beats_matches_the_model():
    _parity(
        _op("cut_clips_on_beats", clips=["a", "b"]),
        lambda p: cut_clips_on_beats(p, ["a", "b"], BeatGrid(120.0)),
    )
    _parity(
        _op("cut_clips_on_beats", clips=["a"], every=2),
        lambda p: cut_clips_on_beats(p, ["a"], BeatGrid(120.0), every=2),
    )


def test_cut_on_beats_leaves_a_clip_no_beat_crosses_and_keeps_animation_coherent():
    def animated() -> Project:
        project = _project(grid=BeatGrid(30.0, offset=0.1))  # un temps toutes les 2 s
        find_clip(project, "c").transform_keyframes = [
            TransformKeyframe("position_x", 0.0, 0.0),
            TransformKeyframe("position_x", 1.5, 1.0),
        ]
        return project

    project = _parity(
        _op("cut_clips_on_beats", clips=["a", "c"]),
        lambda p: cut_clips_on_beats(p, ["a", "c"], BeatGrid(30.0, 0.1)),
        animated,
    )
    assert [clip.id for clip in project.tracks[1].clips] == [
        "c",
        "c-split-2",
    ]  # 10 s → 12 s : le temps 10,1 s coupe le clip


def test_cut_on_beats_errors_are_clean_and_leave_nothing():
    _refused(
        _project(grid=None), _op("cut_clips_on_beats", clips=["a"])
    )  # pas de grille
    _refused(
        _project(grid=BeatGrid(20.0, offset=0.0)),
        _op("cut_clips_on_beats", clips=["c"]),
    )  # un temps toutes les 3 s : le clip 10→12 n'en croise pas
    _refused(_project(), _op("cut_clips_on_beats", clips=["inconnu"]))
    _refused(_project(), _op("cut_clips_on_beats", clips=["a", "a"]))  # cité deux fois


def test_cut_on_beats_checks_every_clips_locks_before_the_first_cut():
    report = _refused(
        _project(), _op("cut_clips_on_beats", clips=["a", "d"])
    )  # d : piste V3 verrouillée
    assert "verrouill" in report.error
    locked_layer = _project()
    track = Track("G1", "G1", "graphics")
    locked_layer.tracks.append(track)
    layer = add_graphic_clip(
        locked_layer, "text", timeline_start=0.0, duration=4.0, track=track
    )
    update_graphic(layer, "locked", True)
    _refused(locked_layer, _op("cut_clips_on_beats", clips=["a", layer.id]))
    # un clip sur une piste libre mais déverrouillé reste coupable
    session, report = _apply(_project(), _op("cut_clips_on_beats", clips=["a"]))
    assert report.ok and len(session.project.tracks[0].clips) == 11


def test_cut_on_beats_bounds_the_number_of_pieces_before_cutting():
    huge = _project(grid=BeatGrid(300.0))
    find_clip(huge, "a").source_out = 1_000_000.0
    start = time.perf_counter()
    report = _refused(huge, _op("cut_clips_on_beats", clips=["a"]))
    assert (
        time.perf_counter() - start < 1.0 and "limite" in report.error
    )  # refusé sans calculer 5 millions de temps
    wide = _project(grid=BeatGrid(120.0))
    find_clip(wide, "a").source_out = 300.0  # 600 temps sur 300 s
    _refused(wide, _op("cut_clips_on_beats", clips=["a"]))
    ok = _project(grid=BeatGrid(120.0))
    find_clip(ok, "a").source_out = 200.0  # 399 coupes : sous la limite
    session, report = _apply(ok, _op("cut_clips_on_beats", clips=["a"]))
    assert report.ok and len(session.project.tracks[0].clips) == 401


def test_cut_on_beats_counts_the_whole_project_not_the_active_sequence():
    project = _project()
    _crowd(
        project, auto_rules.MAX_CLIPS_IN_PROJECT - 10, sequence=True
    )  # dans une autre séquence
    assert auto_rules.total_clips(project) > auto_rules.MAX_CLIPS_IN_PROJECT - 10
    report = _refused(
        project, _op("cut_clips_on_beats", clips=["a"])
    )  # 9 coupes, il reste de la place pour 5 seulement
    assert "limite" in report.error
    lone = _project()
    _crowd(lone, 4_000)
    session, report = _apply(lone, _op("cut_clips_on_beats", clips=["a"]))
    assert report.ok


def test_cut_on_beats_is_one_undo_step_and_clips_are_cited_by_alias():
    project = _project()
    session, report = _apply(
        project,
        _op("add_marker", at=1.0, name="x"),
        _op("cut_clips_on_beats", clips=["a", "b"]),
    )
    assert report.ok and len(session.project.tracks[0].clips) > 4
    assert session.undo() and [clip.id for clip in session.project.tracks[0].clips] == [
        "a",
        "b",
    ]
    session, report = _apply(
        _project(),
        _op("duplicate_clip", clip="c", at=20.0, **{"as": "copie"}),
        _op("cut_clips_on_beats", clips=[{"ref": "copie"}]),
    )
    assert report.ok and any(
        clip.id.startswith("copie") for clip in session.project.tracks[1].clips
    )


def test_huge_inputs_are_bounded_at_parse():
    with pytest.raises(ScriptInvalid):
        parse_script(
            {
                "kut_script": 1,
                "ops": [_op("cut_clips_on_beats", clips=[f"c{n}" for n in range(201)])],
            }
        )
    with pytest.raises(ScriptInvalid):
        parse_script({"kut_script": 1, "ops": [_op("cut_clips_on_beats", clips=[])]})
    with pytest.raises(ScriptInvalid):
        parse_script(
            {"kut_script": 1, "ops": [_op("cut_clips_on_beats", clips=["a"], every=0)]}
        )
    with pytest.raises(ScriptInvalid):
        parse_script(
            {
                "kut_script": 1,
                "ops": [_op("cut_clip_at_scenes", clip="a", times=[1.0] * 501)],
            }
        )
    with pytest.raises(ScriptInvalid):
        parse_script(
            {
                "kut_script": 1,
                "ops": [
                    _op(
                        "add_subtitle_lines",
                        lines=[{"start": 0, "end": 1, "text": "x"}] * 501,
                    )
                ],
            }
        )
    with pytest.raises(ScriptInvalid):
        parse_script(
            {
                "kut_script": 1,
                "ops": [
                    _op(
                        "add_subtitle_lines",
                        lines=[{"start": 0, "end": 1, "text": "x" * (5 * 1024 * 1024)}],
                    )
                ],
            }
        )


# --- Répartir sur la grille ------------------------------------------------------------------------------------------------


def test_distribute_on_grid_matches_the_model():
    _parity(
        _op("distribute_on_grid", clips=["a", "b"], beats_each=2),
        lambda p: distribute_on_grid(p, ["a", "b"], BeatGrid(120.0), beats_each=2),
    )
    _parity(
        _op("distribute_on_grid", clips=["a", "b"], beats_each=4, start=1.0),
        lambda p: distribute_on_grid(
            p, ["a", "b"], BeatGrid(120.0), beats_each=4, start=1.0
        ),
    )


def test_distribute_on_grid_refusals():
    _refused(_project(grid=None), _op("distribute_on_grid", clips=["a"]))
    _refused(
        _project(), _op("distribute_on_grid", clips=["a", "d"])
    )  # piste verrouillée
    _refused(_project(locked_v1=True), _op("distribute_on_grid", clips=["a", "b"]))
    _refused(_project(), _op("distribute_on_grid", clips=["a", "a"]))
    # un clip fixe sur la piste est recouvert : refusé par le modèle, rien ne bouge
    crowded = _project()
    crowded.tracks[0].clips.append(Clip("fixe", "m", "V1", 0.5, 0.0, 0.5))
    _refused(crowded, _op("distribute_on_grid", clips=["b"], start=0.0))


def test_distribute_on_grid_refuses_a_result_beyond_the_time_limit():
    project = _project(grid=BeatGrid(20.0))
    _refused(
        project,
        _op("distribute_on_grid", clips=["a", "b"], start=1_000_000.0, beats_each=64),
    )


# --- Plans -----------------------------------------------------------------------------------------------------------------


def test_cut_clip_at_scenes_matches_the_model():
    _parity(
        _op("cut_clip_at_scenes", clip="a", times=[1.0, 2.5, 4.0]),
        lambda p: cut_clip_at_scenes(p, "a", [1.0, 2.5, 4.0]),
    )
    _parity(
        _op("cut_clip_at_scenes", clip="a", times=[4.0, 1.0]),
        lambda p: cut_clip_at_scenes(p, "a", [4.0, 1.0]),
    )  # dans le désordre


def test_cut_clip_at_scenes_follows_the_source_window():
    def trimmed() -> Project:
        project = _project()
        clip = find_clip(project, "a")
        clip.source_in, clip.source_out = 10.0, 15.0
        return project

    _parity(
        _op("cut_clip_at_scenes", clip="a", times=[12.0]),
        lambda p: cut_clip_at_scenes(p, "a", [12.0]),
        trimmed,
    )
    _refused(
        trimmed(), _op("cut_clip_at_scenes", clip="a", times=[1.0])
    )  # hors de la fenêtre : le modèle l'écarterait


def test_cut_clip_at_scenes_refuses_what_the_model_drops_silently():
    _refused(
        _project(), _op("cut_clip_at_scenes", clip="a", times=[0.05])
    )  # trop près du bord
    _refused(
        _project(), _op("cut_clip_at_scenes", clip="a", times=[1.0, 1.05])
    )  # deux instants trop proches
    _refused(
        _project(), _op("cut_clip_at_scenes", clip="a", times=[1.0, 1.0])
    )  # doublon
    _refused(
        _project(), _op("cut_clip_at_scenes", clip="a", times=[2.0, 99.0])
    )  # un instant utile, un hors du clip
    report = _refused(
        _project(), _op("cut_clip_at_scenes", clip="a", times=[1.0, 99.0])
    )
    assert "1 instant" in report.error


def test_cut_clip_at_scenes_refuses_locks_kinds_and_non_linear_clips():
    _refused(
        _project(), _op("cut_clip_at_scenes", clip="d", times=[1.0])
    )  # piste verrouillée
    _refused(_project(), _op("cut_clip_at_scenes", clip="inconnu", times=[1.0]))
    audio = _project()
    audio.tracks[3].clips.append(Clip("au", "m", "A1", 0.0, 0.0, 5.0))
    _refused(
        audio, _op("cut_clip_at_scenes", clip="au", times=[1.0])
    )  # une piste audio n'a pas de plans
    for changes in ({"speed": 2.0}, {"reverse": True}):
        slowed = _project()
        clip = find_clip(slowed, "a")
        clip.time_remapping = replace(clip.time_remapping, **changes)
        _refused(slowed, _op("cut_clip_at_scenes", clip="a", times=[1.0]))


def test_a_non_linear_clip_is_refused_for_its_own_reason_before_the_times_are_looked_at():
    slowed = _project()
    clip = find_clip(slowed, "a")
    clip.time_remapping = replace(clip.time_remapping, speed=2.0)
    report = _refused(
        slowed, _op("cut_clip_at_scenes", clip="a", times=[99.0])
    )  # l'instant serait aussi refusé : la raison du clip passe d'abord
    assert "retimé" in report.error


def test_cut_clip_at_scenes_bounds_resources_and_is_atomic_end_to_end():
    crowded = _project()
    _crowd(crowded, auto_rules.MAX_CLIPS_IN_PROJECT - 1, sequence=True)
    _refused(crowded, _op("cut_clip_at_scenes", clip="a", times=[1.0, 2.0]))
    session, report = _apply(
        _project(),
        _op("cut_clip_at_scenes", clip="a", times=[1.0, 3.0]),
        _op("trim_clip_end", clip="a", at=0.5),
    )
    assert report.ok and [clip.id for clip in session.project.tracks[0].clips][:2] == [
        "a",
        "a-split-2",
    ]
    assert session.undo() and [clip.id for clip in session.project.tracks[0].clips] == [
        "a",
        "b",
    ]


# --- Sous-titres -----------------------------------------------------------------------------------------------------------


_LINES = [
    {"start": 0.5, "end": 1.5, "text": "un deux trois"},
    {"start": 1.5, "end": 3.0, "text": "quatre"},
]


def test_add_subtitle_lines_matches_the_model():
    expected = [
        CaptionLine(0.5, 1.5, "un deux trois", ()),
        CaptionLine(1.5, 3.0, "quatre", ()),
    ]
    project = _parity(
        _op("add_subtitle_lines", lines=_LINES),
        lambda p: add_subtitle_lines(p, expected),
        shape=_shape,
    )
    track = next(track for track in project.tracks if track.type == "subtitle")
    assert [
        (clip.timeline_start, clip.duration, clip.text) for clip in track.clips
    ] == [(0.5, 1.0, "un deux trois"), (1.5, 1.5, "quatre")]


def test_subtitle_lines_go_on_a_free_unlocked_track_and_never_over_a_clip():
    def busy() -> Project:
        project = _project()
        project.tracks.append(Track("S1", "S1", "subtitle", locked=True))
        project.tracks.append(Track("S2", "S2", "subtitle"))
        project.tracks[-1].clips.append(
            Clip("old", "m", "S2", 1.0, 0.0, 1.0, text="déjà là")
        )
        return project

    expected = [
        CaptionLine(0.5, 1.5, "un deux trois", ()),
        CaptionLine(1.5, 3.0, "quatre", ()),
    ]
    project = _parity(
        _op("add_subtitle_lines", lines=_LINES),
        lambda p: add_subtitle_lines(p, expected),
        busy,
        _shape,
    )
    created = [
        track
        for track in project.tracks
        if track.type == "subtitle"
        and any(clip.text == "quatre" for clip in track.clips)
    ]
    assert len(created) == 1 and created[0].id not in (
        "S1",
        "S2",
    )  # ni la piste verrouillée, ni celle qui est occupée


@pytest.mark.parametrize(
    "lines",
    [
        [{"start": 2.0, "end": 1.0, "text": "à l'envers"}],
        [{"start": 1.0, "end": 1.0, "text": "durée nulle"}],
        [
            {"start": 0.0, "end": 2.0, "text": "a"},
            {"start": 1.0, "end": 3.0, "text": "recouvre"},
        ],
        [
            {"start": 3.0, "end": 4.0, "text": "b"},
            {"start": 0.0, "end": 1.0, "text": "désordre"},
        ],
    ],
)
def test_subtitle_lines_must_be_ordered_and_positive(lines):
    _refused(_project(), _op("add_subtitle_lines", lines=lines))
    _refused(_project(), _op("add_karaoke_lines", lines=lines))


def test_subtitle_lines_have_a_minimum_duration():
    report = _refused(
        _project(),
        _op(
            "add_subtitle_lines",
            lines=[{"start": 1.0, "end": 1.01, "text": "trop bref"}],
        ),
    )
    assert "au moins" in report.error
    session, report = _apply(
        _project(),
        _op(
            "add_subtitle_lines",
            lines=[{"start": 1.0, "end": 1.04, "text": "une image"}],
        ),
    )
    assert report.ok


def test_the_list_bounds_hold_even_when_the_decoder_is_bypassed():
    from core.scripting.auto_verbs import _caption_lines

    with pytest.raises(ValueError, match="limite"):
        auto_rules.editable_clips(_project(), ["a"] * (auto_rules.MAX_LISTED_CLIPS + 1))
    with pytest.raises(ValueError, match="limite"):
        _caption_lines(
            [
                {"start": float(n), "end": n + 0.5, "text": "x"}
                for n in range(auto_rules.MAX_NEW_CLIPS_PER_OPERATION + 1)
            ],
            karaoke=False,
        )


def test_subtitle_lines_refuse_bad_fields_at_parse_time():
    for line in (
        {"start": 0, "end": 1},
        {"start": 0, "end": 1, "text": ""},
        {"start": 0, "end": 1, "text": "a\nb"},
        {"start": -1, "end": 1, "text": "a"},
        {"start": 0, "end": 1, "text": "a", "word_starts": [0]},
        {"start": 0, "end": float("inf"), "text": "a"},
        {"start": 0, "end": 1, "text": " a "},
    ):
        with pytest.raises(ScriptInvalid):
            parse_script(
                {"kut_script": 1, "ops": [_op("add_subtitle_lines", lines=[line])]}
            )


def test_subtitle_lines_count_against_the_whole_project():
    crowded = _project()
    _crowd(crowded, auto_rules.MAX_CLIPS_IN_PROJECT - 1, sequence=True)
    _refused(crowded, _op("add_subtitle_lines", lines=_LINES))
    session, report = _apply(_project(), _op("add_subtitle_lines", lines=_LINES))
    assert (
        report.ok
        and session.undo()
        and not [t for t in session.project.tracks if t.type == "subtitle"]
    )


# --- Karaoké ---------------------------------------------------------------------------------------------------------------


_KARAOKE = [
    {"start": 0.0, "end": 2.0, "text": "un deux trois", "word_starts": [0.0, 0.5, 1.2]},
    {"start": 2.0, "end": 3.0, "text": "quatre"},
]


def test_add_karaoke_lines_matches_the_model_with_word_times():
    expected = [
        CaptionLine(0.0, 2.0, "un deux trois", (0.0, 0.5, 1.2)),
        CaptionLine(2.0, 3.0, "quatre", ()),
    ]
    project = _parity(
        _op("add_karaoke_lines", lines=_KARAOKE),
        lambda p: add_karaoke_lines(p, expected),
        shape=_shape,
    )
    layers = next(track for track in project.tracks if track.type == "graphics").clips
    assert (
        layers[0].graphic.word_times == (0.0, 0.5, 1.2)
        and layers[1].graphic.word_times == ()
    )


@pytest.mark.parametrize(
    "starts", [[0.0, 0.5], [0.0, 0.5, 1.0, 1.5], [0.0, 1.5, 1.0], [0.0, 0.5, 2.5]]
)
def test_karaoke_word_starts_must_match_the_words_and_stay_in_the_line(starts):
    _refused(
        _project(),
        _op(
            "add_karaoke_lines",
            lines=[
                {
                    "start": 0.0,
                    "end": 2.0,
                    "text": "un deux trois",
                    "word_starts": starts,
                }
            ],
        ),
    )


def test_karaoke_lines_count_as_clips_across_all_sequences():
    crowded = _project()
    _crowd(
        crowded, auto_rules.MAX_CLIPS_IN_PROJECT - 7, sequence=True, graphics=True
    )  # des calques d'une autre séquence (+ 4 clips : 3 places)
    many = [
        {"start": float(n), "end": n + 0.5, "text": "mot"} for n in range(6)
    ]  # 6 lignes pour 3 places
    report = _refused(crowded, _op("add_karaoke_lines", lines=many))
    assert "limite" in report.error
    session, report = _apply(crowded, _op("add_karaoke_lines", lines=_KARAOKE))
    assert report.ok


def test_karaoke_lines_are_one_step_and_creation_is_not_reported_as_one_object():
    session, report = _apply(_project(), _op("add_karaoke_lines", lines=_KARAOKE))
    assert (
        report.ok
        and created_ids(
            parse_script(
                {"kut_script": 1, "ops": [_op("add_karaoke_lines", lines=_KARAOKE)]}
            ),
            report,
        )
        == []
    )
    assert session.undo() and not [
        t for t in session.project.tracks if t.type == "graphics"
    ]
