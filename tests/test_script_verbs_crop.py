"""Format déclaratif : recadrage non destructif (`set_crop`, `clear_crop`, ADR-0006 étape 2).

Le recadrage est un masque ; les bornes sont lues dans le modèle (`core.clip_crop`) et ne sont jamais ramenées en silence.
"""

from __future__ import annotations

import pytest

from core.clip_crop import CROP_MASK_ID, MAX_CROP_SIDE, Crop, crop_of
from core.compositing import Compositing, Mask, MaskMode
from core.project_model import Clip, MediaAsset, Project, Track
from core.scripting import ScriptInvalid, Session, apply_script, parse_script
from core.scripting.verbs import VERBS
from core.timeline_operations import find_clip
from ui import i18n


@pytest.fixture(autouse=True)
def _french(monkeypatch):
    monkeypatch.setenv("KUT_STUDIO_LANG", "fr")
    i18n.reset_for_tests()
    yield
    i18n.reset_for_tests()


def _project(*, locked: bool = False) -> Project:
    return Project(
        "Démo",
        width=1280,
        height=720,
        fps=30.0,
        media_assets=[
            MediaAsset(
                "m",
                "/x/intro.mp4",
                "intro",
                10.0,
                1280,
                720,
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
                locked=locked,
                clips=[Clip("a", "m", "V1", 0.0, 0.0, 5.0)],
            ),
            Track("A1", "A1", "audio"),
        ],
    )


def _apply(project: Project, *ops: dict):
    session = Session(project)
    return session, apply_script(
        session, parse_script({"kut_script": 1, "ops": list(ops)})
    )


def test_the_verbs_are_in_the_table():
    assert {"set_crop", "clear_crop"} <= set(VERBS)


def test_set_crop_keeps_absent_edges_and_reuses_one_mask():
    project = _project()
    project.tracks[0].clips[0].compositing = Compositing(
        masks=(Mask(width=0.3, name="Mien"),)
    )
    session, report = _apply(
        project,
        {"op": "set_crop", "clip": "a", "left": 0.1},
        {"op": "set_crop", "clip": "a", "top": 0.2, "right": 0.05},
    )
    assert report.ok, report.error
    clip = find_clip(session.project, "a")
    assert crop_of(clip) == Crop(left=0.1, top=0.2, right=0.05, bottom=0.0)
    ids = [mask.id for mask in clip.compositing.masks]
    assert ids.count(CROP_MASK_ID) == 1 and ids[-1] == CROP_MASK_ID
    assert (
        clip.compositing.masks[0].name == "Mien"
        and clip.compositing.masks[0].mode is MaskMode.ADD
    )


def test_set_crop_to_zero_everywhere_removes_the_mask_and_clear_crop_is_idempotent():
    session, report = _apply(
        _project(),
        {"op": "set_crop", "clip": "a", "left": 0.2},
        {"op": "set_crop", "clip": "a", "left": 0.0},
    )
    assert report.ok, report.error
    assert find_clip(session.project, "a").compositing.masks == ()
    _s, report = _apply(
        session.project,
        {"op": "set_crop", "clip": "a", "bottom": 0.3},
        {"op": "clear_crop", "clip": "a"},
        {"op": "clear_crop", "clip": "a"},
    )
    assert report.ok, report.error
    assert crop_of(find_clip(session.project, "a")) is None


def test_bounds_are_read_from_the_model_and_nothing_is_clamped():
    for name in ("left", "top", "right", "bottom"):
        for bad in (-0.01, MAX_CROP_SIDE + 0.01):
            with pytest.raises(ScriptInvalid):
                parse_script(
                    {
                        "kut_script": 1,
                        "ops": [{"op": "set_crop", "clip": "a", name: bad}],
                    }
                )
    for pair in (("left", "right"), ("top", "bottom")):
        session, report = _apply(
            _project(), {"op": "set_crop", "clip": "a", pair[0]: 0.5, pair[1]: 0.5}
        )
        assert not report.ok and "moins de" in report.error
        assert crop_of(find_clip(session.project, "a")) is None


def test_at_least_one_edge_is_required():
    _session, report = _apply(_project(), {"op": "set_crop", "clip": "a"})
    assert not report.ok and "au moins un bord" in report.error


def test_a_locked_track_and_an_audio_track_are_refused():
    session, report = _apply(
        _project(locked=True), {"op": "set_crop", "clip": "a", "left": 0.1}
    )
    assert not report.ok and "verrouillée" in report.error
    assert crop_of(find_clip(session.project, "a")) is None
    _s, report = _apply(_project(locked=True), {"op": "clear_crop", "clip": "a"})
    assert not report.ok and "verrouillée" in report.error
    project = _project()
    project.tracks[1].clips.append(Clip("s", "m", "A1", 0.0, 0.0, 1.0))
    _s, report = _apply(project, {"op": "set_crop", "clip": "s", "left": 0.1})
    assert not report.ok and "piste" in report.error


def test_an_animated_or_hand_edited_crop_is_not_overwritten_silently():
    project = _project()
    session, report = _apply(project, {"op": "set_crop", "clip": "a", "left": 0.2})
    assert report.ok, report.error
    clip = find_clip(session.project, "a")
    from dataclasses import replace

    rotated = replace(clip.compositing.mask_by_id(CROP_MASK_ID), rotation=10.0)
    clip.compositing = Compositing(masks=(rotated,))
    _s, report = _apply(session.project, {"op": "set_crop", "clip": "a", "top": 0.1})
    assert not report.ok and "quatre bords" in report.error
    _s, report = _apply(
        session.project,
        {
            "op": "set_crop",
            "clip": "a",
            "left": 0.1,
            "top": 0.1,
            "right": 0.1,
            "bottom": 0.1,
        },
    )
    assert report.ok, report.error
    assert crop_of(find_clip(session.project, "a")) == Crop(0.1, 0.1, 0.1, 0.1)


def test_a_locked_graphic_layer_is_refused_by_the_verbs_and_the_menu_command():
    from core.graphics import add_graphic_clip
    from core.mograph_layers import set_layer_locked

    project = _project()
    layer = add_graphic_clip(project, "solid", timeline_start=0, duration=2)
    set_layer_locked(project, layer.id, True)
    for op in (
        {"op": "set_crop", "clip": layer.id, "left": 0.1},
        {"op": "clear_crop", "clip": layer.id},
    ):
        _s, report = _apply(project, op)
        assert not report.ok and "verrouillé" in report.error
    assert crop_of(layer) is None
