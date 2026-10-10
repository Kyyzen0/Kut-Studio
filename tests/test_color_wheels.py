"""Roues lift / gamma / gain / offset : la formule « Grade », appliquée au niveau près par le vrai FFmpeg.

La référence numpy est écrite ici, à partir de la formule de :mod:`core.color_wheels`, sans réutiliser son code : un
test qui appellerait ``channel_transfer`` pour calculer l'attendu validerait une erreur de formule.
"""

from __future__ import annotations

import json
import subprocess

import numpy as np
import pytest
from render_probe import lavfi_video, needs_ffmpeg, render_frame

from core.color_grading import (
    ColorGrade,
    ColorGradingRangeError,
    Wheel,
    _dict_to_grade,
    _grade_to_dict,
)
from core.color_wheels import PUCK_REACH, puck_from_wheel, wheel_from_puck, wheels_filter
from core.export_engine import _build_color_grade_filters
from core.gpu_grade import DOMAIN_RGB, atlas_array, bake_grade_lut
from core.project_io import load_project, save_project
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import build_render_plan

W, H = 64, 36


def _expected(grade: ColorGrade, levels: np.ndarray) -> np.ndarray:
    """Formule « Grade » écrite à la main : ``levels`` (… × 3, 0..255) → niveaux de sortie arrondis."""
    return np.clip(np.floor(_exact(grade, levels) + 0.5), 0, 255)


def _exact(grade: ColorGrade, levels: np.ndarray) -> np.ndarray:
    """La formule avant arrondi, en niveaux 0..255 (non bornée en haut)."""
    out = np.empty(levels.shape, dtype=float)
    for index in range(3):
        def part(wheel: Wheel) -> float:
            return (wheel.r, wheel.g, wheel.b)[index] + wheel.y
        lift, gain = 0.25 * part(grade.lift), max(0.0, 1.0 + part(grade.gain))
        gamma, offset = 2.0 ** part(grade.gamma), 0.25 * part(grade.offset)
        x = levels[..., index] / 255.0
        out[..., index] = np.power(np.maximum(0.0, (gain - lift) * x + lift + offset), 1.0 / gamma) * 255.0
    return out


GRADES = {
    "lift": ColorGrade(lift=Wheel(0.3, -0.15, -0.15, 0.2)),
    "gamma-extremes": ColorGrade(gamma=Wheel(1.0, -1.0, 0.0, 0.0)),
    "gain-coupe-un-canal": ColorGrade(gain=Wheel(-1.0, 0.4, 0.0, -0.2)),
    "offset-negatif": ColorGrade(offset=Wheel(0.0, 0.0, 0.5, -0.4)),
    "les-quatre": ColorGrade(lift=Wheel(y=-0.2), gamma=Wheel(0.1, -0.05, -0.05, 0.3), gain=Wheel(-0.1, 0.0, 0.1, 0.25),
                             offset=Wheel(0.05, 0.0, -0.05, 0.0)),
}


# --- modèle ---------------------------------------------------------------------------------------------------------


def test_neutral_wheels_are_the_identity_and_add_nothing_to_the_chain():
    grade = ColorGrade()
    assert grade.is_identity() and grade.wheels_are_neutral()
    assert wheels_filter(grade) == "" and "lutrgb" not in _build_color_grade_filters(grade)
    moved = grade.with_wheel("gamma", Wheel(y=0.1))
    assert not moved.is_identity() and moved.gamma.y == 0.1 and grade.gamma.is_neutral()


def test_a_wheel_outside_its_bounds_or_an_unknown_wheel_is_refused():
    with pytest.raises(ColorGradingRangeError):
        Wheel(r=1.5)
    with pytest.raises(ValueError):
        ColorGrade().with_wheel("saturation", Wheel())


def test_the_wheels_come_after_colorbalance_and_before_the_curves():
    from core.color_grading import ColorCurve, ColorCurves

    bent = ColorCurve(points=((0.0, 0.0), (0.5, 0.6), (1.0, 1.0)))
    grade = ColorGrade(exposure=0.2, hue=5.0, shadows=0.1, temperature=20.0, curves=ColorCurves(master=bent),
                       gain=Wheel(y=0.2))
    chain = _build_color_grade_filters(grade)
    order = ["eq=", "hue=", "colorbalance=", "colorchannelmixer=", "lutrgb=", "curves="]
    assert [chain.index(stage) for stage in order] == sorted(chain.index(stage) for stage in order)


# --- formule, au niveau près ----------------------------------------------------------------------------------------


@needs_ffmpeg
@pytest.mark.parametrize("name", list(GRADES))
def test_the_wheels_stage_is_the_formula_for_every_input_level(name):
    """Une rampe des 256 niveaux, chaque canal décalé des autres : la table de FFmpeg est la formule, au niveau près."""
    grade = GRADES[name]
    levels = np.arange(256)
    ramp = np.stack((levels, (levels + 85) % 256, (levels + 170) % 256), axis=-1).astype(np.uint8)
    done = subprocess.run(["ffmpeg", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "256x1", "-i", "-",
                           "-vf", wheels_filter(grade), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                          input=ramp.tobytes(), capture_output=True, check=True)
    graded = np.frombuffer(done.stdout, np.uint8).reshape(256, 3).astype(float)
    exact = _exact(grade, ramp.astype(float))
    # Un résultat tombé pile sur un demi-niveau (181,5) s'arrondit d'un côté ou de l'autre selon l'ordre des calculs
    # en double précision (181,49999999999997 ici, 181,5 dans FFmpeg) : les deux voisins y sont justes.
    tie = np.abs(exact - np.floor(exact) - 0.5) < 1e-9
    error = np.abs(graded - _expected(grade, ramp.astype(float)))
    assert error[~tie].max() == 0 and error[tie].max(initial=0) <= 1, error.max()


def test_lift_moves_the_blacks_gain_the_whites_and_gamma_neither():
    ends = np.array([[0.0, 0.0, 0.0], [255.0, 255.0, 255.0]])
    lifted = _expected(ColorGrade(lift=Wheel(y=0.4)), ends)
    assert lifted[0, 0] == round(0.1 * 255) and lifted[1, 0] == 255
    gained = _expected(ColorGrade(gain=Wheel(y=-0.4)), ends)
    assert gained[0, 0] == 0 and gained[1, 0] == round(0.6 * 255)
    assert np.array_equal(_expected(ColorGrade(gamma=Wheel(y=0.7)), ends), ends)


# --- rendu de l'export et moniteur GPU ------------------------------------------------------------------------------


def _rendered(tmp_path, color: str, grade: ColorGrade | None) -> np.ndarray:
    media = lavfi_video(tmp_path / f"{color}.mp4", f"color=c={color}", size=(W, H), seconds=1.0)
    project = Project("p", width=W, height=H, fps=25.0,
                      media_assets=[MediaAsset("v", str(media), "v", 1.0, W, H, 25.0, "video")],
                      tracks=[Track("V1", "V1", "video", clips=[Clip("c", "v", "V1", 0.0, 0.0, 1.0)])])
    project.tracks[0].clips[0].color_grade = grade
    frame = render_frame(build_render_plan(project), W, H, 0.3).astype(float)
    return frame[H // 2 - 4:H // 2 + 4, W // 2 - 4:W // 2 + 4].reshape(-1, 3).mean(axis=0)


@needs_ffmpeg
def test_an_exported_clip_takes_the_wheels(tmp_path):
    """Un gris moyen : gamma maître en haut l'éclaircit, le palet du gain vers le bleu le bleuit sans changer le niveau
    moyen (décalages de moyenne nulle)."""
    grey = _rendered(tmp_path, "0x707070", None)
    brighter = _rendered(tmp_path, "0x707070", ColorGrade(gamma=Wheel(y=0.5)))
    assert (brighter - grey).min() > 15, (grey, brighter)
    bluer = _rendered(tmp_path, "0x707070", ColorGrade(gain=wheel_from_puck(240.0, 1.0)))
    assert bluer[2] > grey[2] + 15 and bluer[0] < grey[0] - 5, (grey, bluer)
    assert abs(bluer.mean() - grey.mean()) < 3


@needs_ffmpeg
def test_the_monitor_lut_carries_the_wheels():
    """La LUT du moniteur est cuite par la chaîne de l'export : aux nœuds du réseau RVB, elle vaut la formule des roues
    au niveau près (plus d'``eq`` neutre ni de son aller-retour YUV devant les roues)."""
    from core.gpu_grade import CODE_STEP, LUT_SIZE

    grade = GRADES["les-quatre"]
    n, codes = LUT_SIZE, np.arange(LUT_SIZE) * CODE_STEP
    c0, c1, c2 = np.meshgrid(codes, codes, codes, indexing="ij")
    lattice = np.stack((c0, c1, c2), axis=-1).astype(float)
    baked = np.rint(atlas_array(bake_grade_lut(grade, domain=DOMAIN_RGB)) * 255.0)
    shown = baked.reshape(n, n, n, 3).transpose(0, 2, 1, 3)               # atlas[c₀, c₂·N + c₁]
    error = np.abs(shown - _exact(grade, lattice).clip(0, 255))
    assert error.max() <= 0.51 and error.mean() < 0.3, (error.mean(), error.max())


@needs_ffmpeg
def test_five_serial_nodes_drift_by_no_more_than_their_own_roundings():
    """Cinq nœuds de roues en série contre la formule composée en flottant : seul l'arrondi de chaque nœud reste.
    Jusqu'au 2026-10-10, chaque nœud passait aussi par un ``eq`` neutre (aller-retour YUV) : 1,16 niveau d'écart moyen,
    6 au plus ; mesuré ensuite 0,50 et 2."""
    from core.color_nodes import ColorNode, ColorNodeGraph

    grades = [ColorGrade(gain=Wheel(0.05, 0.0, -0.05, 0.1)), ColorGrade(gamma=Wheel(y=0.15)),
              ColorGrade(lift=Wheel(y=0.05)), ColorGrade(gain=Wheel(y=-0.08)),
              ColorGrade(gamma=Wheel(-0.03, 0.02, 0.01, -0.1))]
    image = np.random.default_rng(3).integers(0, 256, (32, 48, 3), dtype=np.uint8)
    ideal = image.astype(float)
    for grade in grades:
        ideal = _exact(grade, ideal).clip(0, 255)
    graph = ColorNodeGraph.serial(ColorNode(f"n{index + 1}", grade) for index, grade in enumerate(grades))
    done = subprocess.run(["ffmpeg", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "48x32", "-i", "-",
                           "-vf", _build_color_grade_filters(graph), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                          input=image.tobytes(), capture_output=True, check=True)
    error = np.abs(np.frombuffer(done.stdout, np.uint8).reshape(32, 48, 3) - ideal)
    assert error.mean() < 0.7 and error.max() <= 2.5, (error.mean(), error.max())
    assert "eq=" not in _build_color_grade_filters(graph)


# --- palet --------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("hue", [0.0, 60.0, 135.0, 240.0, 333.0])
def test_the_puck_moves_the_colour_not_the_level_and_reads_back(hue):
    wheel = wheel_from_puck(hue, 0.6, master=-0.25)
    assert abs(wheel.r + wheel.g + wheel.b) < 1e-9 and wheel.y == -0.25
    back_hue, back_radius = puck_from_wheel(wheel)
    assert back_radius == pytest.approx(0.6) and (back_hue - hue + 180.0) % 360.0 - 180.0 == pytest.approx(0.0)


def test_pushing_the_puck_towards_red_raises_red():
    wheel = wheel_from_puck(0.0, 1.0)
    assert wheel.r == pytest.approx(PUCK_REACH) and wheel.g == pytest.approx(wheel.b)
    assert wheel.g < 0
    assert puck_from_wheel(Wheel()) == (0.0, 0.0)


# --- fichiers -------------------------------------------------------------------------------------------------------


def test_moved_wheels_survive_a_save_and_neutral_ones_leave_the_file_as_before(tmp_path):
    grade = GRADES["les-quatre"]
    project = Project("p", width=W, height=H, fps=25.0,
                      media_assets=[MediaAsset("v", str(tmp_path / "v.mp4"), "v", 1.0, W, H, 25.0, "video")],
                      tracks=[Track("V1", "V1", "video", clips=[Clip("c", "v", "V1", 0.0, 0.0, 1.0),
                                                                 Clip("d", "v", "V1", 1.0, 0.0, 1.0)])])
    project.tracks[0].clips[0].color_grade = grade
    project.tracks[0].clips[1].color_grade = ColorGrade(exposure=0.5)
    target = tmp_path / "p.kut"
    save_project(project, str(target))
    clips = json.loads(target.read_text(encoding="utf-8"))["project"]["sequences"][0]["tracks"][0]["clips"]
    assert set(clips[0]["color_grade"]["wheels"]) == {"lift", "gamma", "gain", "offset"}
    assert "wheels" not in clips[1]["color_grade"], "un étalonnage sans roues s'écrit comme avant"
    loaded = load_project(str(target))
    assert loaded.tracks[0].clips[0].color_grade == grade
    assert loaded.tracks[0].clips[1].color_grade == ColorGrade(exposure=0.5)


def test_presets_keep_their_wheels_and_a_broken_wheel_is_dropped_alone():
    grade = ColorGrade(saturation=1.2, gain=Wheel(0.1, 0.0, -0.1, 0.05))
    assert _dict_to_grade(json.loads(json.dumps(_grade_to_dict(grade)))) == grade
    payload = _grade_to_dict(grade)
    payload["wheels"]["lift"] = [3.0, 0.0, 0.0, 0.0]
    payload["wheels"]["gamma"] = "abîmé"
    read = _dict_to_grade(payload)
    assert read.saturation == 1.2 and read.gain == grade.gain and read.lift.is_neutral() and read.gamma.is_neutral()
