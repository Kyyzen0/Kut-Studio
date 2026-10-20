"""Cibles d'animation des effets ``fx.<effet>.<paramètre>`` (ADR-0008, étape 2) : registre, valeur effective, format ``.kut``.

Ces tests prouvent le modèle (le rendu : ``tests/test_effect_expressions.py``, sans interface) (résolution par la droite, bornes lues dans
``EFFECT_PARAMETER_SPECS``, fonction pure ``effective_effect_parameters``, version 20 du fichier, tolérance à la lecture).
"""

from __future__ import annotations

import hashlib
import json

import pytest

from core import project_io
from core.animation import Keyframe
from core.animation_targets import animation_curves, get_target, targets_for_clip
from core.effect_targets import (
    ANIMATABLE_EFFECT_PARAMS,
    EXCLUDED_EFFECT_PARAMS,
    EXPRESSION_EFFECT_PARAMS,
    PENDING_EFFECT_PARAMS,
    SENDCMD_EFFECT_PARAMS,
    effective_effect_parameters,
)
from core.effects_model import (
    EFFECT_PARAMETER_SPECS,
    FX_PREFIX,
    EffectType,
    create_effect,
    fx_property_id,
    prune_effect_animation,
    split_fx_property,
    update_clip_effect_parameters,
)
from core.keyframe_editing import add_keyframe, is_animated, remove_keyframes, KeyframeRef, set_value_at, value_at
from core.project_io import (
    COLOR_MATRIX_VERSION,
    CURRENT_VERSION,
    EFFECT_ANIMATION_VERSION,
    PATH_MASK_VERSION,
    SUPPORTED_VERSIONS,
    load_project,
    project_payload,
    save_project,
)
from core.project_model import Clip, MediaAsset, Project, Track


def make_project(effects=(), animation=(), *, track_type: str = "video") -> Project:
    asset = MediaAsset(id="a", path="/tmp/a.mp4", name="a", duration=10.0, width=1920, height=1080, fps=30.0,
                       media_type="video")
    clip = Clip(id="c", asset_id="a", track_id="V1", timeline_start=0.0, source_in=0.0, source_out=10.0)
    clip.effects = list(effects)
    clip.animation = list(animation)
    return Project(name="P", media_assets=[asset], tracks=[Track(id="V1", name="V1", type=track_type, clips=[clip])])


def clip_of(project: Project) -> Clip:
    return project.tracks[0].clips[0]


# ---------------------------------------------------------------------------
# Garde : la liste, les noms de paramètres
# ---------------------------------------------------------------------------


def test_no_parameter_name_contains_a_dot():
    """Le découpage par la droite n'est sans ambiguïté que si aucun paramètre ne contient de point."""
    names = [spec.name for specs in EFFECT_PARAMETER_SPECS.values() for spec in specs]
    assert names and all("." not in name and name for name in names)


def test_the_registered_list_is_exactly_the_rendered_one():
    """Étapes 3 et 5 : seuls les paramètres qui ont un rendu (expression FFmpeg ou ``sendcmd``, et moniteur) sont
    enregistrés ; la liste est l'union des deux voies de rendu."""
    assert ANIMATABLE_EFFECT_PARAMS == {
        EffectType.COLOR_CORRECTION: ("brightness", "contrast", "saturation"),
        EffectType.VIGNETTE: ("intensity",),
        EffectType.BLUR: ("intensity",),
        EffectType.GLOW: ("radius",),
        EffectType.CHROMATIC_ABERRATION: ("intensity",),
    }
    assert EXPRESSION_EFFECT_PARAMS == {EffectType.COLOR_CORRECTION: ("brightness", "contrast", "saturation"),
                                        EffectType.VIGNETTE: ("intensity",)}
    assert SENDCMD_EFFECT_PARAMS == {EffectType.BLUR: ("intensity",), EffectType.GLOW: ("radius",),
                                     EffectType.CHROMATIC_ABERRATION: ("intensity",)}
    for kind, names in ANIMATABLE_EFFECT_PARAMS.items():
        assert set(names) == set(EXPRESSION_EFFECT_PARAMS.get(kind, ())) | set(SENDCMD_EFFECT_PARAMS.get(kind, ()))
        assert not set(EXPRESSION_EFFECT_PARAMS.get(kind, ())) & set(SENDCMD_EFFECT_PARAMS.get(kind, ())), kind
    declared = {kind: {spec.name for spec in specs} for kind, specs in EFFECT_PARAMETER_SPECS.items()}
    for kind, names in ANIMATABLE_EFFECT_PARAMS.items():
        assert set(names) <= declared[kind]


def test_every_declared_parameter_is_registered_pending_or_excluded_exactly_once():
    """Aucun paramètre d'effet n'échappe au classement, aucun n'est dans deux listes."""
    for kind, specs in EFFECT_PARAMETER_SPECS.items():
        groups = [set(table.get(kind, ())) for table in (ANIMATABLE_EFFECT_PARAMS, PENDING_EFFECT_PARAMS,
                                                         EXCLUDED_EFFECT_PARAMS)]
        for spec in specs:
            assert sum(spec.name in group for group in groups) == 1, (kind, spec.name)
        assert set().union(*groups) <= {spec.name for spec in specs}
    assert PENDING_EFFECT_PARAMS == {
        EffectType.HEAT_HAZE: ("amplitude", "frequency", "speed", "top", "span"),    # geq : coût, voir docs/animation.md
    }
    assert EXCLUDED_EFFECT_PARAMS == {EffectType.SHARPEN: ("intensity",), EffectType.GLOW: ("threshold", "intensity")}


# ---------------------------------------------------------------------------
# Découpage par la droite
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name, expected", [
    ("fx.fx-123.intensity", ("fx-123", "intensity")),
    ("fx.my.blur.intensity", ("my.blur", "intensity")),
    ("fx.a.b.c.span", ("a.b.c", "span")),
    ("fx.intensity", None),               # pas d'identifiant d'effet
    ("fx..intensity", None),
    ("fx.blur.", None),
    ("position_x", None),
    ("mask.m1.feather", None),
])
def test_split_fx_property_cuts_from_the_right(name, expected):
    assert split_fx_property(name) == expected


def test_a_dotted_effect_id_resolves_to_its_own_effect():
    effect = create_effect(EffectType.VIGNETTE, effect_id="my.vig", params={"intensity": 0.7})
    clip = clip_of(make_project([effect]))
    pid = fx_property_id("my.vig", "intensity")
    assert pid == "fx.my.vig.intensity"
    target = get_target(pid, clip)
    assert target.id == pid and target.get_static(clip) == 0.7


# ---------------------------------------------------------------------------
# Résolution, bornes, liste
# ---------------------------------------------------------------------------


def test_each_registered_parameter_has_the_bounds_of_its_effect_type():
    clip = clip_of(make_project([
        create_effect(EffectType.COLOR_CORRECTION, effect_id="cc"),
        create_effect(EffectType.VIGNETTE, effect_id="v"),
    ]))
    bounds = {pid: (get_target(pid, clip).spec.minimum, get_target(pid, clip).spec.maximum)
              for pid in ("fx.cc.brightness", "fx.cc.contrast", "fx.cc.saturation", "fx.v.intensity")}
    assert bounds == {"fx.cc.brightness": (-1.0, 1.0), "fx.cc.contrast": (0.0, 2.0), "fx.cc.saturation": (0.0, 3.0),
                      "fx.v.intensity": (0.0, 1.0)}


def test_bounds_and_default_are_read_from_the_specs():
    clip = clip_of(make_project([create_effect(EffectType.COLOR_CORRECTION, effect_id="h")]))
    for spec in EFFECT_PARAMETER_SPECS[EffectType.COLOR_CORRECTION]:
        target = get_target(fx_property_id("h", spec.name), clip)
        assert (target.spec.minimum, target.spec.maximum, target.spec.default) == (spec.minimum, spec.maximum, spec.default)


def test_without_a_clip_an_fx_property_is_unknown():
    clip = clip_of(make_project([create_effect(EffectType.VIGNETTE, effect_id="b")]))
    assert get_target("fx.b.intensity", clip)
    with pytest.raises(KeyError):
        get_target("fx.b.intensity")


@pytest.mark.parametrize("pid", [
    "fx.absent.intensity",      # l'effet n'existe pas
    "fx.b.threshold",           # paramètre qui n'est pas celui de l'effet
    "fx.b",                     # mal formé
])
def test_missing_effect_or_parameter_is_unknown(pid):
    clip = clip_of(make_project([create_effect(EffectType.VIGNETTE, effect_id="b")]))
    with pytest.raises(KeyError):
        get_target(pid, clip)


def test_excluded_and_pending_parameters_are_not_animatable():
    """Un paramètre n'est enregistré que si son rendu existe : ni l'exclu (netteté, seuil et gain de la lueur), ni
    l'en-attente (brume de chaleur) n'ont de cible ; le flou, le rayon de la lueur et l'aberration chromatique en ont une
    depuis l'étape 5."""
    clip = clip_of(make_project([
        create_effect(EffectType.SHARPEN, effect_id="s"),
        create_effect(EffectType.GLOW, effect_id="g"),
        create_effect(EffectType.BLUR, effect_id="b"),
        create_effect(EffectType.CHROMATIC_ABERRATION, effect_id="ca"),
        create_effect(EffectType.HEAT_HAZE, effect_id="h"),
        create_effect(EffectType.COLOR_CORRECTION, effect_id="cc"),
    ]))
    for pid in ("fx.s.intensity", "fx.g.threshold", "fx.g.intensity",
                "fx.h.amplitude", "fx.h.frequency", "fx.h.speed", "fx.h.top", "fx.h.span"):
        with pytest.raises(KeyError):
            get_target(pid, clip)
    for pid, bounds in (("fx.cc.saturation", (0.0, 3.0)), ("fx.g.radius", (0.0, 100.0)), ("fx.b.intensity", (0.0, 50.0)),
                        ("fx.ca.intensity", (0.0, 40.0))):
        target = get_target(pid, clip)
        assert (target.spec.minimum, target.spec.maximum) == bounds


def test_fx_targets_only_exist_on_video_tracks():
    clip = clip_of(make_project([create_effect(EffectType.VIGNETTE, effect_id="b")]))
    target = get_target("fx.b.intensity", clip)
    assert target.applies_to("video") and not target.applies_to("graphics") and not target.applies_to("audio")


# Liste calculée sur une copie de origin/main (d0c319b), avec un clip qui porte un effet de chaque type : l'interface actuelle
# (Graph Editor, commandes d'animation) ne doit rien voir de nouveau avant l'étape 4.
MAIN_TARGETS = ["position_x", "position_y", "scale", "rotation", "opacity", "anchor_x", "anchor_y", "scale_x", "scale_y",
                "flip_h", "flip_v", "pan_x", "pan_y", "time.speed"]


def test_targets_for_clip_is_unchanged_by_default():
    clip = clip_of(make_project([create_effect(kind, effect_id=f"e-{kind.value}") for kind in EffectType]))
    assert [target.id for target in targets_for_clip(clip, "video")] == MAIN_TARGETS


def test_targets_for_clip_can_list_the_effect_parameters():
    clip = clip_of(make_project([create_effect(kind, effect_id=f"e-{kind.value}") for kind in EffectType]))
    ids = [t.id for t in targets_for_clip(clip, "video", include_effects=True)]
    assert ids[: len(MAIN_TARGETS)] == MAIN_TARGETS
    fx_ids = [pid for pid in ids if pid.startswith(FX_PREFIX)]
    expected = sum(len(names) for names in ANIMATABLE_EFFECT_PARAMS.values())
    assert len(fx_ids) == expected == len(set(fx_ids))
    assert "fx.e-vignette.intensity" in fx_ids and "fx.e-sharpen.intensity" not in fx_ids
    for kind, names in PENDING_EFFECT_PARAMS.items():         # en attente : jamais exposés tant qu'il n'y a pas de rendu
        for name in names:
            assert fx_property_id(f"e-{kind.value}", name) not in fx_ids


# ---------------------------------------------------------------------------
# Valeur effective (fonction pure)
# ---------------------------------------------------------------------------


def test_without_keyframes_the_effective_parameters_are_exactly_the_base_ones():
    effect = create_effect(EffectType.COLOR_CORRECTION, effect_id="cc", params={"brightness": 0.2, "contrast": 1.5})
    clip = clip_of(make_project([effect], [Keyframe("position_x", 0.0, 0.1)]))
    assert effective_effect_parameters(clip, effect, 1.0) == dict(effect.params)
    assert effective_effect_parameters(clip, effect, 1.0) is not effect.params


def test_effective_parameters_follow_the_curve_and_are_bounded():
    effect = create_effect(EffectType.COLOR_CORRECTION, effect_id="b", params={"contrast": 1.2})
    animation = [Keyframe("fx.b.contrast", 0.0, 0.0), Keyframe("fx.b.contrast", 4.0, 6.0)]   # 6 > borne 2
    clip = clip_of(make_project([effect], animation))
    assert effective_effect_parameters(clip, effect, 0.0)["contrast"] == pytest.approx(0.0)
    assert effective_effect_parameters(clip, effect, 1.0)["contrast"] == pytest.approx(1.5)
    assert effective_effect_parameters(clip, effect, 4.0)["contrast"] == 2.0         # bornée comme l'inspecteur
    assert effect.params["contrast"] == 1.2                                           # la base n'a pas bougé


def test_effective_parameters_ignore_other_effects_and_non_animatable_keyframes():
    vignette = create_effect(EffectType.VIGNETTE, effect_id="b", params={"intensity": 0.3})
    sharpen = create_effect(EffectType.SHARPEN, effect_id="s", params={"intensity": 1.0})
    blur = create_effect(EffectType.BLUR, effect_id="u", params={"intensity": 2.0})
    glow = create_effect(EffectType.GLOW, effect_id="g", params={"threshold": 0.6, "radius": 18.0, "intensity": 1.0})
    animation = [Keyframe("fx.b.intensity", 0.0, 0.9), Keyframe("fx.s.intensity", 0.0, 4.0),
                 Keyframe("fx.u.intensity", 0.0, 9.0), Keyframe("fx.g.radius", 0.0, 30.0),
                 Keyframe("fx.g.intensity", 0.0, 3.0), Keyframe("fx.g.threshold", 0.0, 0.2)]
    clip = clip_of(make_project([vignette, sharpen, blur, glow], animation))
    assert effective_effect_parameters(clip, vignette, 0.5) == {"intensity": 0.9}
    assert effective_effect_parameters(clip, sharpen, 0.5) == {"intensity": 1.0}     # exclu : courbe ignorée
    assert effective_effect_parameters(clip, blur, 0.5) == {"intensity": 9.0}        # étape 5 : flou animé
    # Rayon animé ; seuil et gain (lutrgb, trop coûteux) : courbes ignorées.
    assert effective_effect_parameters(clip, glow, 0.5) == {"threshold": 0.6, "radius": 30.0, "intensity": 1.0}


# ---------------------------------------------------------------------------
# Édition par les fonctions génériques (sans interface)
# ---------------------------------------------------------------------------


def test_generic_editing_works_on_an_fx_target():
    project = make_project([create_effect(EffectType.VIGNETTE, effect_id="v", params={"intensity": 0.5})])
    clip = clip_of(project)
    assert not is_animated(clip, "fx.v.intensity")
    set_value_at(project, "c", "fx.v.intensity", 0.0, 0.2)               # non animée : valeur statique de l'effet
    assert clip.effects[0].params["intensity"] == 0.2
    first = add_keyframe(project, "c", "fx.v.intensity", 0.0)
    add_keyframe(project, "c", "fx.v.intensity", 2.0, 0.9)
    assert is_animated(clip, "fx.v.intensity") and value_at(clip, "fx.v.intensity", 1.0) == pytest.approx(0.55)
    set_value_at(project, "c", "fx.v.intensity", 0.0, 7.0)               # animée : image clé, bornée à 1
    assert value_at(clip, "fx.v.intensity", 0.0) == 1.0
    remove_keyframes(project, [KeyframeRef("c", "fx.v.intensity", k.id) for k in clip.animation], local_time=0.0)
    assert not clip.animation and clip.effects[0].params["intensity"] == 1.0 and first.id


def test_editing_a_parameter_keeps_the_keyframes_of_live_effects():
    project = make_project([create_effect(EffectType.VIGNETTE, effect_id="b")])
    add_keyframe(project, "c", "fx.b.intensity", 0.0, 0.5)
    update_clip_effect_parameters(project, "c", "b", {"intensity": 0.9})
    assert [k.property_name for k in clip_of(project).animation] == ["fx.b.intensity"]


def test_animation_curves_resolve_with_the_clip_and_tolerate_orphans():
    clip = clip_of(make_project([create_effect(EffectType.VIGNETTE, effect_id="b")],
                                [Keyframe("fx.b.intensity", 0.0, 1.0), Keyframe("fx.gone.intensity", 0.0, 1.0)]))
    assert set(animation_curves(clip)) == {"fx.b.intensity", "fx.gone.intensity"}


# ---------------------------------------------------------------------------
# Format .kut
# ---------------------------------------------------------------------------


def reference_project() -> Project:
    """Même projet que celui de ``/private/tmp/.../fxbase_probe.py`` calculé sur origin/main (d0c319b)."""
    project = make_project([create_effect(kind, effect_id=f"e-{kind.value}") for kind in EffectType],
                           [Keyframe("graphic.width", 0.0, 100.0, id="k1"), Keyframe("graphic.width", 1.0, 200.0, id="k2")])
    project.name = "Ref"
    return project


# Empreinte du fichier ``.kut`` de ``reference_project()`` écrit par le code de ``origin/main`` (bd3478c, sans aucune
# modification), fins de ligne ramenées à ``\n`` : sonde ``save_project`` sur un worktree de ``origin/main``, puis
# SHA-256 des octets après ``replace(b"\r\n", b"\n")``. Sous Windows ``_atomic_write_json`` ouvre le fichier en mode texte
# (``os.fdopen(fd, "w")``) : chaque ``\n`` devient ``\r\n``, d'où l'empreinte brute 02c65d54bb1a… observée en CI. Le chemin du
# média, lui, est écrit tel quel sur toutes les plateformes (``asset.path``).
REFERENCE_SHA256 = "4f8103e55b395f0b04ef056146de2026a698e240251ac834b33a677bdc6c59c0"
WINDOWS_RAW_SHA256 = "02c65d54bb1ad41046d315c0654201f25cefc123633a16498c2d209ed92b0bd2"


def normalized_digest(data: bytes) -> str:
    """SHA-256 des octets du fichier, fins de ligne de plateforme (CRLF) ramenées à LF ; tout le reste est comparé tel quel."""
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def test_a_file_without_fx_keyframes_is_written_byte_for_byte_as_before(tmp_path):
    path = tmp_path / "ref.kut"
    save_project(reference_project(), path)
    assert json.loads(path.read_text())["version"] == CURRENT_VERSION == 17
    assert normalized_digest(path.read_bytes()) == REFERENCE_SHA256     # empreinte calculée sur origin/main


def test_crlf_line_endings_of_windows_do_not_change_the_normalized_digest(tmp_path):
    path = tmp_path / "ref.kut"
    save_project(reference_project(), path)
    lf = path.read_bytes().replace(b"\r\n", b"\n")
    crlf = lf.replace(b"\n", b"\r\n")                                     # ce que le mode texte écrit sous Windows
    assert hashlib.sha256(crlf).hexdigest() == WINDOWS_RAW_SHA256           # c'est bien l'empreinte vue sur windows-latest
    assert normalized_digest(crlf) == normalized_digest(lf) == REFERENCE_SHA256


@pytest.mark.parametrize("media_path", ["/tmp/a.mp4", "C:\\tmp\\a.mp4", "D:/tmp/a.mp4", "\\\\serveur\\partage\\a.mp4"])
def test_the_media_path_is_written_verbatim_whatever_the_platform_spelling(tmp_path, media_path):
    project = reference_project()
    project.media_assets[0].path = media_path
    path = tmp_path / "ref.kut"
    save_project(project, path)
    data = path.read_bytes()
    assert json.loads(data)["project"]["media_assets"][0]["path"] == media_path
    spelled = json.dumps(media_path, ensure_ascii=False).encode()
    assert data.count(spelled) == 1
    # Hors du chemin, les octets sont ceux de la référence : une autre graphie ne déplace rien d'autre.
    assert normalized_digest(data.replace(spelled, b'"/tmp/a.mp4"')) == REFERENCE_SHA256


def test_version_constants():
    assert (CURRENT_VERSION, PATH_MASK_VERSION, COLOR_MATRIX_VERSION, EFFECT_ANIMATION_VERSION) == (17, 18, 19, 20)
    assert {17, 18, 19, 20} <= SUPPORTED_VERSIONS


def test_a_blur_keyframe_writes_version_23_not_20():
    """Le flou est rendu par ``sendcmd`` (étape 5) : la 0.8.0 publiée l'ignorerait en silence, donc la 23 (``SENDCMD_ANIMATION_VERSION``)."""
    project = reference_project()
    clip_of(project).animation.append(Keyframe("fx.e-blur.intensity", 0.5, 1.0, id="kx"))
    assert project_payload(project)["version"] == 23


@pytest.mark.parametrize("name", ["fx.e-vignette.intensity", "color.exposure", "fx.gone.intensity"])
def test_an_fx_or_color_keyframe_writes_version_20(name):
    project = reference_project()
    clip_of(project).animation.append(Keyframe(name, 0.5, 1.0, id="kx"))
    assert project_payload(project)["version"] == EFFECT_ANIMATION_VERSION == 20


def test_other_generic_keyframes_keep_the_old_version():
    project = reference_project()
    clip_of(project).animation.append(Keyframe("mask.m1.feather", 0.5, 0.2, id="kx"))
    clip_of(project).animation.append(Keyframe("time.speed", 0.5, 1.2, id="ky"))
    assert project_payload(project)["version"] == 17


def test_fx_animation_round_trips_in_version_20(tmp_path):
    effects = [create_effect(EffectType.VIGNETTE, effect_id="my.blur", params={"intensity": 0.4}),
               create_effect(EffectType.COLOR_CORRECTION, effect_id="cc")]
    animation = [Keyframe("fx.cc.contrast", 1.0, 1.4, id="k3"), Keyframe("fx.my.blur.intensity", 0.0, 1.0, id="k1"),
                 Keyframe("fx.my.blur.intensity", 2.0, 0.9, id="k2")]       # l'ordre que le chargeur rend (nom, temps)
    project = make_project(effects, animation)
    path = tmp_path / "fx.kut"
    save_project(project, path)
    assert json.loads(path.read_text())["version"] == 20
    loaded = load_project(path)
    again = tmp_path / "again.kut"
    save_project(loaded, again)
    assert again.read_bytes() == path.read_bytes()
    assert [(k.property_name, k.time_seconds, k.value, k.id) for k in clip_of(loaded).animation] == [
        ("fx.cc.contrast", 1.0, 1.4, "k3"), ("fx.my.blur.intensity", 0.0, 1.0, "k1"), ("fx.my.blur.intensity", 2.0, 0.9, "k2"),
    ]
    assert get_target("fx.my.blur.intensity", clip_of(loaded)).get_static(clip_of(loaded)) == 0.4


def test_a_version_20_file_is_refused_by_a_build_that_does_not_know_it(tmp_path, monkeypatch):
    project = make_project([create_effect(EffectType.VIGNETTE, effect_id="b")], [Keyframe("fx.b.intensity", 0.0, 1.0)])
    path = tmp_path / "fx.kut"
    save_project(project, path)
    monkeypatch.setattr(project_io, "SUPPORTED_VERSIONS", SUPPORTED_VERSIONS - {EFFECT_ANIMATION_VERSION})
    with pytest.raises(ValueError, match="Version de fichier Kut-Studio non supportée"):
        load_project(path)


def test_an_orphan_fx_keyframe_never_fails_the_loading_and_goes_at_the_next_edit(tmp_path):
    project = make_project([create_effect(EffectType.VIGNETTE, effect_id="b")],
                           [Keyframe("fx.b.intensity", 0.0, 1.0, id="k1"), Keyframe("fx.gone.intensity", 0.0, 1.0, id="k2")])
    path = tmp_path / "orphan.kut"
    save_project(project, path)
    loaded = load_project(path)
    clip = clip_of(loaded)
    assert {k.property_name for k in clip.animation} == {"fx.b.intensity", "fx.gone.intensity"}   # lus, ignorés
    with pytest.raises(KeyError):
        get_target("fx.gone.intensity", clip)
    assert effective_effect_parameters(clip, clip.effects[0], 0.0) == {"intensity": 1.0}
    update_clip_effect_parameters(loaded, "c", "b", {"intensity": 0.3})                         # prochaine édition du clip
    assert [k.property_name for k in clip.animation] == ["fx.b.intensity"]


def test_prune_is_idempotent_and_leaves_other_keyframes_alone():
    clip = clip_of(make_project([create_effect(EffectType.BLUR, effect_id="b")],
                                [Keyframe("fx.b.intensity", 0.0, 1.0), Keyframe("fx.gone.x", 0.0, 1.0),
                                 Keyframe("fx.broken", 0.0, 1.0), Keyframe("graphic.width", 0.0, 3.0)]))
    assert prune_effect_animation(clip) == 2
    assert prune_effect_animation(clip) == 0
    assert [k.property_name for k in clip.animation] == ["fx.b.intensity", "graphic.width"]
