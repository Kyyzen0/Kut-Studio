"""Flou, aberration chromatique, rayon de la lueur animés (ADR-0008, étape 5) : modèle, format, cycle de vie, composition, script.

Le rendu est dans ``tests/test_effect_sendcmd.py`` ; l'interface dans ``tests/test_effect_sendcmd_ui.py``. Ici : ce qui ne change pas
(format du fichier) et ce qui doit suivre les nouveaux paramètres (cycle de vie des courbes, refus de la conversion en
composition, verbes de script, plafond d'images clés, pistes verrouillées).
"""

from __future__ import annotations

import copy
import json

import pytest

from core.animation import Keyframe
from core.composition_ops import CompositionError, convert_to_composition
from core.effects_model import (
    EffectType,
    add_effect_to_clip,
    create_effect,
    has_live_effect_animation,
    remove_effect_from_clip,
)
from core.project_io import (
    EFFECT_ANIMATION_VERSION,
    SENDCMD_ANIMATION_VERSION,
    load_project,
    project_payload,
    save_project,
)
from core.project_model import Clip, MediaAsset, Project, Track
from core.scripting import Session, apply_script, parse_script
from core.timeline_operations import cut_clip, duplicate_clip, find_clip

NEW = [
    ("bl", EffectType.BLUR, "intensity", 12.0),
    ("gl", EffectType.GLOW, "radius", 30.0),
    ("ca", EffectType.CHROMATIC_ABERRATION, "intensity", 9.0),
]


def project_with(
    effect_id, kind, name, value, *, locked=False, track_type="video"
) -> Project:
    asset = MediaAsset("a", "/tmp/a.mp4", "a", 20.0, 96, 54, 25.0, "video")
    clip = Clip("c", "a", "V1", 0.0, 0.0, 10.0)
    clip.effects = [create_effect(kind, effect_id=effect_id)]
    clip.animation = [
        Keyframe(f"fx.{effect_id}.{name}", 0.0, 0.0, id="k0"),
        Keyframe(f"fx.{effect_id}.{name}", 4.0, value, id="k1"),
    ]
    return Project(
        "p",
        width=96,
        height=54,
        fps=25.0,
        media_assets=[asset],
        tracks=[Track("V1", "V1", track_type, locked=locked, clips=[clip])],
    )


# --- (6) Format : 23 pour les courbes de sendcmd (la 0.8.0 publiée les ignorerait en silence), 20 pour les autres ------------


@pytest.mark.parametrize("effect_id, kind, name, value", NEW)
def test_a_sendcmd_curve_writes_version_23_and_roundtrips(
    tmp_path, effect_id, kind, name, value
):
    project = project_with(effect_id, kind, name, value)
    assert project_payload(project)["version"] == SENDCMD_ANIMATION_VERSION == 23
    path = tmp_path / "p.kut"
    save_project(project, str(path))
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 23
    reloaded = load_project(str(path))
    clip = reloaded.tracks[0].clips[0]
    assert [(k.property_name, k.time_seconds, k.value) for k in clip.animation] == [
        (f"fx.{effect_id}.{name}", 0.0, 0.0),
        (f"fx.{effect_id}.{name}", 4.0, value),
    ]
    again = tmp_path / "again.kut"
    save_project(reloaded, str(again))
    assert again.read_bytes() == path.read_bytes()


def test_published_effect_curves_stay_in_version_20_and_no_curve_stays_below():
    vignette = project_with("vg", EffectType.VIGNETTE, "intensity", 0.9)
    assert project_payload(vignette)["version"] == EFFECT_ANIMATION_VERSION == 20
    color = project_with("cc", EffectType.COLOR_CORRECTION, "contrast", 1.5)
    assert project_payload(color)["version"] == 20
    plain = project_with("bl", EffectType.BLUR, "intensity", 12.0)
    plain.tracks[0].clips[0].animation = []
    assert project_payload(plain)["version"] < 20
    plain.tracks[0].clips[0].animation = [Keyframe("graphic.width", 0.0, 3.0)]
    assert project_payload(plain)["version"] < 20


def test_the_parameter_name_alone_does_not_decide_and_orphans_do_not_count():
    """``intensity`` est aussi celui de la vignette ; une courbe dont l'effet n'existe plus est ignorée par tous les builds."""
    project = project_with("vg", EffectType.VIGNETTE, "intensity", 0.9)
    project.tracks[0].clips[0].animation.append(
        Keyframe("fx.nobody.intensity", 0.0, 1.0)
    )
    assert project_payload(project)["version"] == 20
    glow = project_with(
        "gl", EffectType.GLOW, "intensity", 2.0
    )  # gain de la lueur : pas un paramètre sendcmd
    assert project_payload(glow)["version"] == 20


def test_the_registry_decides_the_version(monkeypatch):
    from core import effect_targets

    project = project_with("vg", EffectType.VIGNETTE, "intensity", 0.9)
    assert project_payload(project)["version"] == 20
    monkeypatch.setitem(
        effect_targets.SENDCMD_EFFECT_PARAMS, EffectType.VIGNETTE, ("intensity",)
    )
    assert project_payload(project)["version"] == 23


def test_a_3d_layer_and_a_blur_curve_together_write_23_and_a_3d_layer_alone_stays_22(
    tmp_path,
):
    from tests.scene3d_samples import project_with_scene

    project, clip, _scene = project_with_scene(tmp_path)
    assert project_payload(project)["version"] == 22
    clip_with_blur = project.tracks[0].clips[0]
    clip_with_blur.effects = [create_effect(EffectType.BLUR, effect_id="bl")]
    clip_with_blur.animation = [
        Keyframe("fx.bl.intensity", 0.0, 1.0),
        Keyframe("fx.bl.intensity", 1.0, 9.0),
    ]
    assert project_payload(project)["version"] == 23


def test_a_build_that_does_not_know_version_23_refuses_the_file(tmp_path, monkeypatch):
    from core import project_io

    path = tmp_path / "p.kut"
    save_project(project_with("bl", EffectType.BLUR, "intensity", 12.0), str(path))
    monkeypatch.setattr(
        project_io,
        "SUPPORTED_VERSIONS",
        project_io.SUPPORTED_VERSIONS - {SENDCMD_ANIMATION_VERSION},
    )
    with pytest.raises(ValueError, match="Versions acceptées"):
        load_project(str(path))
    other = tmp_path / "v.kut"
    save_project(project_with("vg", EffectType.VIGNETTE, "intensity", 0.9), str(other))
    assert load_project(str(other)).tracks[0].clips[0].animation, (
        "un fichier en 20 reste lisible par ce lecteur"
    )


def test_a_project_without_an_fx_curve_stays_below_version_20():
    project = project_with("bl", EffectType.BLUR, "intensity", 12.0)
    project.tracks[0].clips[0].animation = [Keyframe("graphic.width", 0.0, 3.0)]
    assert project_payload(project)["version"] < 20


# --- (7) Cycle de vie : un effet supprimé emporte ses courbes, annuler les rend ---------------------------------------------


@pytest.mark.parametrize("effect_id, kind, name, value", NEW)
def test_removing_the_effect_removes_its_curve_in_the_same_history_step_and_undo_brings_both_back(
    effect_id, kind, name, value
):
    project = project_with(effect_id, kind, name, value)
    session = Session(project)
    assert session.apply(
        [lambda p: remove_effect_from_clip(p, "c", effect_id)],
        label="Supprimer l'effet",
    ).ok
    clip = project.tracks[0].clips[0]
    assert not clip.effects and not clip.animation
    assert session.undo()
    clip = project.tracks[0].clips[0]
    assert [e.id for e in clip.effects] == [effect_id] and [
        k.value for k in clip.animation
    ] == [0.0, value]
    assert has_live_effect_animation(clip)


@pytest.mark.parametrize("effect_id, kind, name, value", NEW)
def test_cutting_and_duplicating_keep_the_curve_of_the_new_parameters(
    effect_id, kind, name, value
):
    project = project_with(effect_id, kind, name, value)
    cut_clip(project, "c", 2.0)
    first, second = project.tracks[0].clips
    prop = f"fx.{effect_id}.{name}"
    assert any(k.property_name == prop for k in first.animation) and any(
        k.property_name == prop for k in second.animation
    )
    copy_id = duplicate_clip(project, first.id)
    copied = find_clip(project, copy_id) if isinstance(copy_id, str) else copy_id
    assert any(k.property_name == prop for k in getattr(copied, "animation", ()))


# --- (8) Composition : un clip aux courbes vivantes est refusé ------------------------------------------------------------


@pytest.mark.parametrize("effect_id, kind, name, value", NEW)
def test_converting_a_clip_with_a_live_new_curve_is_refused_and_changes_nothing(
    effect_id, kind, name, value
):
    project = project_with(effect_id, kind, name, value)
    before = copy.deepcopy(project)
    with pytest.raises(CompositionError, match="effets animés"):
        convert_to_composition(project, ["c"])
    assert project == before


def test_an_orphan_new_curve_does_not_block_the_conversion():
    project = project_with("bl", EffectType.BLUR, "intensity", 12.0)
    project.tracks[0].clips[
        0
    ].effects = []  # l'effet n'existe plus : courbe orpheline, tolérée
    assert not has_live_effect_animation(project.tracks[0].clips[0])
    convert_to_composition(project, ["c"])


# --- (5) Mode script ------------------------------------------------------------------------------------------------------


def _script_project(*, locked=False) -> Project:
    asset = MediaAsset("m", "/x/intro.mp4", "intro", 20.0, 320, 180, 30.0, "video")
    project = Project(
        "Démo",
        width=320,
        height=180,
        fps=30.0,
        media_assets=[asset],
        tracks=[
            Track("V1", "V1", "video", clips=[Clip("a", "m", "V1", 0.0, 0.0, 10.0)])
        ],
    )
    for kind, effect_id in (
        (EffectType.BLUR, "bl"),
        (EffectType.GLOW, "gl"),
        (EffectType.CHROMATIC_ABERRATION, "ca"),
    ):
        add_effect_to_clip(project, "a", kind, effect_id=effect_id)
    project.tracks[0].locked = locked
    return project


def _apply(project, *ops):
    session = Session(project)
    return session, apply_script(
        session, parse_script({"kut_script": 1, "ops": list(ops)})
    )


def _op(**fields):
    return {"op": "add_effect_keyframe", "clip": "a", **fields}


@pytest.mark.parametrize(
    "effect_id, parameter, value",
    [("bl", "intensity", 20.0), ("gl", "radius", 60.0), ("ca", "intensity", 12.0)],
)
def test_add_effect_keyframe_accepts_the_new_parameters_like_the_model(
    effect_id, parameter, value
):
    from core.keyframe_editing import add_keyframe

    project = _script_project()
    session, report = _apply(
        project, _op(effect=effect_id, parameter=parameter, at=1.0, value=value)
    )
    assert report.ok, report.error
    reference = _script_project()
    add_keyframe(reference, "a", f"fx.{effect_id}.{parameter}", 1.0, value)
    mine = [
        (k.property_name, k.time_seconds, k.value)
        for k in find_clip(session.project, "a").animation
    ]
    assert mine == [
        (k.property_name, k.time_seconds, k.value)
        for k in find_clip(reference, "a").animation
    ]


@pytest.mark.parametrize(
    "effect_id, parameter, too_much",
    [("bl", "intensity", 50.5), ("gl", "radius", 100.5), ("ca", "intensity", 40.5)],
)
def test_add_effect_keyframe_refuses_a_value_the_model_would_clamp(
    effect_id, parameter, too_much
):
    project = _script_project()
    before = copy.deepcopy(project.tracks)
    session, report = _apply(
        project, _op(effect=effect_id, parameter=parameter, at=1.0, value=too_much)
    )
    assert (
        not report.ok
        and "hors bornes" in report.error
        and session.project.tracks == before
    )


@pytest.mark.parametrize(
    "effect_id, parameter", [("bl", "intensity"), ("gl", "radius"), ("ca", "intensity")]
)
def test_a_locked_track_refuses_the_new_parameters(effect_id, parameter):
    project = _script_project(locked=True)
    before = copy.deepcopy(project.tracks)
    session, report = _apply(
        project, _op(effect=effect_id, parameter=parameter, at=1.0, value=2.0)
    )
    assert not report.ok and session.project.tracks == before


def test_the_keyframe_ceiling_applies_to_the_new_parameters(monkeypatch):
    import core.scripting.resource_limits as limits

    monkeypatch.setattr(limits, "MAX_KEYFRAMES_PER_CLIP", 3)
    project = _script_project()
    ops = [
        _op(effect="bl", parameter="intensity", at=float(i), value=float(i))
        for i in range(1, 5)
    ]
    session, report = _apply(project, *ops)
    assert not report.ok and "images clés" in report.error
    assert not find_clip(session.project, "a").animation, (
        "le script est refusé en entier"
    )
    session, report = _apply(_script_project(), *ops[:3])
    assert report.ok and len(find_clip(session.project, "a").animation) == 3


def test_sharpen_and_the_glow_gain_and_threshold_stay_refused_by_the_script():
    project = _script_project()
    add_effect_to_clip(project, "a", EffectType.SHARPEN, effect_id="sh")
    for effect_id, parameter in (("sh", "intensity"), ("gl", "intensity")):
        before = copy.deepcopy(project.tracks)
        session, report = _apply(
            project, _op(effect=effect_id, parameter=parameter, at=1.0, value=1.0)
        )
        assert (
            not report.ok
            and "pas animable" in report.error
            and session.project.tracks == before
        )


def test_the_script_documentation_lists_the_new_parameters():
    from pathlib import Path

    text = (Path(__file__).resolve().parent.parent / "docs" / "scripting.md").read_text(
        encoding="utf-8"
    )
    assert (
        "flou" in text[text.index("add_effect_keyframe") :]
        and "rayon" in text[text.index("add_effect_keyframe") :]
    )
