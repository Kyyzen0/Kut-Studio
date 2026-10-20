"""Rendu des paramètres d'effets animés par ``sendcmd`` (ADR-0008, étape 5) : flou, aberration chromatique, rayon de la lueur.

Sans FFmpeg : le fichier de commandes (valeurs = celles du modèle, une demi-image avant l'image visée, ``sigma`` ET ``sigmaV``),
l'échappement du chemin, le cycle de vie du fichier temporaire, le graphe d'un projet sans courbe (inchangé). Avec FFmpeg réel
(``needs_ffmpeg``) : l'image ``n`` de la sortie animée égale le rendu statique à la valeur de la courbe, un segment d'aperçu égale
le clip entier, la parité avec le moniteur GPU.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import replace
from fractions import Fraction
from pathlib import Path

import pytest

from core.animation import InterpolationType, Keyframe
from core.effect_sendcmd import (
    MAX_COMMAND_LINES,
    command_files,
    command_plan,
    context_for,
    escape_command_path,
    filter_name,
    frame_count,
    frame_rate,
    sendcmd_filter,
    write_command_file,
)
from core.effect_targets import ANIMATABLE_EFFECT_PARAMS, SENDCMD_EFFECT_PARAMS, effective_effect_parameters, evaluated_effects
from core.effects_model import EFFECT_PARAMETER_SPECS, EffectType, create_effect, fx_property_id
from core.export_engine import ExportEngine, _build_clip_effect_filters, _build_layer_filter
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import RenderLayer, build_render_plan

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg absent")

L, B, H_, E_ = InterpolationType.LINEAR, InterpolationType.BEZIER, InterpolationType.HOLD, InterpolationType.EASE_IN_OUT
VEHICLES = [(EffectType.BLUR, "intensity"), (EffectType.GLOW, "radius"), (EffectType.CHROMATIC_ABERRATION, "intensity")]


def kf(prop, time, value, interpolation=L, **slopes):
    return Keyframe(prop, time, value, interpolation, **slopes)


def clip_with(effects, animation) -> Clip:
    clip = Clip(id="c", asset_id="a", track_id="V1", timeline_start=0.0, source_in=0.0, source_out=5.0)
    clip.effects = list(effects)
    clip.animation = list(animation)
    return clip


def curve_for(effect, name):
    """Quatre images clés aux interpolations variées, qui touchent les deux bornes ; la Bézier dépasse la borne (écrêtée)."""
    spec = next(s for s in EFFECT_PARAMETER_SPECS[effect.type] if s.name == name)
    lo, hi = spec.minimum, spec.maximum
    span = hi - lo
    prop = fx_property_id(effect.id, name)
    slope = 6.0 * span
    return [kf(prop, 0.5, lo + 0.2 * span, B, out_slope=slope, in_slope=slope), kf(prop, 1.5, lo + 0.9 * span, E_),
            kf(prop, 2.5, lo + 0.1 * span, H_), kf(prop, 4.0, hi, L)]


def applied_values(text: str, fps: Fraction, count: int, start: float = 0.0):
    """Ce que ``sendcmd`` applique à chaque image : toutes les instructions dont l'instant est <= à celui de l'image, la dernière
    gagne. Rend ``[(image, {option: valeur})]``."""
    lines = []
    for raw in text.strip().splitlines():
        stamp, body = raw.split(" ", 1)
        assert body.endswith(";"), raw
        lines.append((float(stamp), body[:-1]))
    assert [t for t, _ in lines] == sorted(t for t, _ in lines)
    state: dict[str, float] = {}
    result = []
    cursor = 0
    for index in range(count):
        instant = start + float(Fraction(index) / fps)
        while cursor < len(lines) and lines[cursor][0] <= instant:
            for command in lines[cursor][1].split(", "):
                _target, option, value = command.split(" ")
                state[option] = float(value)
            cursor += 1
        result.append(dict(state))
    return result


def model_value(effect, name, clip, time):
    return effective_effect_parameters(clip, effect, time)[name]


def to_option(kind, value, scale):
    if kind is EffectType.CHROMATIC_ABERRATION:
        return {"rh": float(int(round(value * scale))), "bh": -float(int(round(value * scale)))}
    floor = 0.01 if kind is EffectType.GLOW else 0.0
    sigma = max(floor, value * scale)
    return {"sigma": sigma, "sigmaV": sigma}


# ---------------------------------------------------------------------------
# (a) Le fichier de commandes donne, à chaque image, la valeur du modèle
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scale", [1.0, 0.5])
@pytest.mark.parametrize("kind, name", VEHICLES)
def test_every_frame_receives_the_value_of_the_model(kind, name, scale):
    """La même évaluation que l'inspecteur et le moniteur (``effective_effect_parameters``), puis la conversion de la chaîne
    statique : ``x pixel_scale``, entier pour l'aberration, plancher de 0,01 pour le rayon."""
    effect = create_effect(kind, effect_id="e")
    animation = curve_for(effect, name)
    clip = clip_with([effect], animation)
    context = context_for(25, 0.0, 5.0)
    plan = command_plan(effect, animation, context, "t@x", scale)
    assert plan is not None
    count = frame_count(context)
    for index, state in enumerate(applied_values(plan.text, context.fps, count)):
        expected = to_option(kind, model_value(effect, name, clip, index / 25), scale)
        assert set(state) == set(expected)
        for option, value in expected.items():
            assert state[option] == pytest.approx(value, abs=6e-7), (kind, index, option)


@pytest.mark.parametrize("kind, name", VEHICLES[:2])
def test_both_sigma_options_are_always_set_together(kind, name):
    effect = create_effect(kind, effect_id="e")
    plan = command_plan(effect, curve_for(effect, name), context_for(25, 0.0, 5.0), "gblur@x", 1.0)
    for raw in plan.text.strip().splitlines():
        assert re.fullmatch(r"\d+\.\d{9} gblur@x sigma ([\d.]+), gblur@x sigmaV \1;", raw), raw


def test_the_chromatic_shift_is_an_integer_with_opposite_red_and_blue():
    effect = create_effect(EffectType.CHROMATIC_ABERRATION, effect_id="e")
    plan = command_plan(effect, curve_for(effect, "intensity"), context_for(25, 0.0, 5.0), "rgbashift@x", 1.0)
    for raw in plan.text.strip().splitlines():
        match = re.fullmatch(r"\d+\.\d{9} rgbashift@x rh (-?\d+), rgbashift@x bh (-?\d+);", raw)
        assert match and int(match.group(1)) == -int(match.group(2)), raw
    assert plan.peak == 40.0 and plan.initial == 8.0


def test_commands_are_written_half_a_frame_before_the_frame():
    effect = create_effect(EffectType.BLUR, effect_id="e")
    animation = [kf("fx.e.intensity", 0.0, 0.0), kf("fx.e.intensity", 1.0, 10.0)]
    plan = command_plan(effect, animation, context_for(30, 0.0, 1.0), "gblur@x", 1.0)
    stamps = [float(line.split(" ")[0]) for line in plan.text.splitlines()]
    assert stamps[0] == 0.0
    assert stamps[1:4] == pytest.approx([0.5 / 30, 1.5 / 30, 2.5 / 30], abs=1e-9)


def test_a_hold_writes_one_instruction_per_change_not_per_frame():
    effect = create_effect(EffectType.BLUR, effect_id="e")
    animation = [kf("fx.e.intensity", 0.0, 3.0, H_), kf("fx.e.intensity", 2.0, 9.0, H_), kf("fx.e.intensity", 3.0, 9.0, H_)]
    plan = command_plan(effect, animation, context_for(30, 0.0, 4.0), "gblur@x", 1.0)
    assert plan.lines == 2                                   # 3 puis 9
    clip = clip_with([effect], animation)
    for index, state in enumerate(applied_values(plan.text, frame_rate(30), frame_count(context_for(30, 0.0, 4.0)))):
        assert state["sigma"] == model_value(effect, "intensity", clip, index / 30)


def test_values_are_bounded_like_the_model():
    """Une courbe qui dépasse les bornes (pente de Bézier démesurée, valeur hors plage écrite à la main) est écrêtée."""
    effect = create_effect(EffectType.BLUR, effect_id="e")
    animation = [kf("fx.e.intensity", 0.0, -20.0), kf("fx.e.intensity", 1.0, 400.0)]
    plan = command_plan(effect, animation, context_for(30, 0.0, 2.0), "gblur@x", 1.0)
    values = [state["sigma"] for state in applied_values(plan.text, frame_rate(30), 62)]
    assert min(values) == 0.0 and max(values) == 50.0 and plan.peak == 50.0
    glow = create_effect(EffectType.GLOW, effect_id="g")
    zero = command_plan(glow, [kf("fx.g.radius", 0.0, 0.0)], context_for(30, 0.0, 1.0), "gblur@x", 1.0)
    assert applied_values(zero.text, frame_rate(30), 3)[0]["sigma"] == 0.01          # plancher du bloom, comme en statique


def test_a_segment_starts_at_its_local_time_with_the_same_values():
    """Un segment d'aperçu commence à ``start`` (temps local du clip) : les instants et les valeurs sont ceux du clip entier."""
    effect = create_effect(EffectType.BLUR, effect_id="e")
    animation = curve_for(effect, "intensity")
    clip = clip_with([effect], animation)
    context = context_for(10, 2.55, 5.0)
    plan = command_plan(effect, animation, context, "gblur@x", 1.0)
    assert float(plan.text.splitlines()[0].split(" ")[0]) == pytest.approx(2.55 - 0.05)
    for index, state in enumerate(applied_values(plan.text, context.fps, frame_count(context), start=2.55)):
        assert state["sigma"] == pytest.approx(model_value(effect, "intensity", clip, 2.55 + index / 10), abs=6e-7)


def test_ntsc_rates_keep_their_exact_fraction():
    assert frame_rate(29.97) == Fraction(30000, 1001) and frame_rate(30) == Fraction(30)
    effect = create_effect(EffectType.BLUR, effect_id="e")
    animation = [kf("fx.e.intensity", 0.0, 0.0), kf("fx.e.intensity", 2.0, 20.0)]
    plan = command_plan(effect, animation, context_for(29.97, 0.0, 2.0), "gblur@x", 1.0)
    assert float(plan.text.splitlines()[1].split(" ")[0]) == pytest.approx(0.5 * 1001 / 30000, abs=1e-9)
    clip = clip_with([effect], animation)
    for index, state in enumerate(applied_values(plan.text, Fraction(30000, 1001), 60)):
        assert state["sigma"] == pytest.approx(model_value(effect, "intensity", clip, index * 1001 / 30000), abs=6e-7)


def test_nothing_is_planned_without_a_live_registered_curve():
    context = context_for(30, 0.0, 2.0)
    blur = create_effect(EffectType.BLUR, effect_id="e")
    assert command_plan(blur, [], context, "x", 1.0) is None and command_plan(blur, None, context, "x", 1.0) is None
    assert command_plan(blur, [kf("fx.other.intensity", 0.0, 3.0)], context, "x", 1.0) is None
    assert command_plan(blur, [kf("position_x", 0.0, 3.0)], context, "x", 1.0) is None
    # Netteté, gain et seuil de la lueur : pas de courbe (unsharp / lutrgb), même si l'on en écrit une à la main.
    sharpen = create_effect(EffectType.SHARPEN, effect_id="s")
    assert command_plan(sharpen, [kf("fx.s.intensity", 0.0, 3.0)], context, "x", 1.0) is None
    glow = create_effect(EffectType.GLOW, effect_id="g")
    assert command_plan(glow, [kf("fx.g.intensity", 0.0, 2.0), kf("fx.g.threshold", 0.0, 0.2)], context, "x", 1.0) is None
    # Le rendu par expression (vignette, correction de couleur) ne passe pas par ici.
    vignette = create_effect(EffectType.VIGNETTE, effect_id="v")
    assert command_plan(vignette, [kf("fx.v.intensity", 0.0, 0.3)], context, "x", 1.0) is None


def test_the_frame_count_is_bounded_before_any_text_is_built():
    assert frame_count(context_for(30, 0.0, 10.0)) == 302
    huge = context_for(120, 0.0, MAX_COMMAND_LINES)           # des années de vidéo : refusé avant d'allouer
    effect = create_effect(EffectType.BLUR, effect_id="e")
    with pytest.raises(ValueError, match="trop longue") as raised:
        command_plan(effect, [kf("fx.e.intensity", 0.0, 3.0)], huge, "gblur@x", 1.0)
    assert len(str(raised.value)) < 120


def test_the_registry_routes_each_parameter_to_exactly_one_renderer():
    from core.effect_expressions import animated_effect_expressions

    for kind, names in SENDCMD_EFFECT_PARAMS.items():
        assert set(names) <= set(ANIMATABLE_EFFECT_PARAMS[kind])
        effect = create_effect(kind, effect_id="e")
        animation = [kf(fx_property_id("e", name), 0.0, 1.0) for name in names]
        assert animated_effect_expressions(effect, animation, "t") == {}, "pas d'expression pour un paramètre de sendcmd"
        assert command_plan(effect, animation, context_for(30, 0.0, 1.0), "x", 1.0) is not None


# ---------------------------------------------------------------------------
# (b) Chemin du fichier cité dans le graphe : échappement
# ---------------------------------------------------------------------------

PATHOLOGICAL = [
    r"C:\Users\a b\x'y.cmd",
    "C:/Temp/Jean D'Arc/sendcmd.cmd",
    "/tmp/a b/c,d/e;f/[g]/h=i/j%k.cmd",
    r"D:\a'b'c\d:e\f.cmd",
    "/tmp/'.cmd",
    "/tmp/é à ü/ふ.cmd",
]


@pytest.mark.parametrize("path", PATHOLOGICAL)
def test_a_path_is_escaped_for_the_filter_option(path):
    escaped = escape_command_path(path)
    assert "\\" not in escaped.replace("\\:", "").replace("'\\\\\\''", ""), "plus de séparateur Windows"
    assert "C:" not in escaped and "D:" not in escaped, "la lettre de lecteur n'est pas lue comme une option"
    text = sendcmd_filter(path)
    assert text.startswith("sendcmd=f='") and text.endswith("'")
    # Une apostrophe du chemin ferme la chaîne, s'échappe, puis la rouvre : jamais une apostrophe nue au milieu.
    inner = text[len("sendcmd=f='"):-1]
    assert re.sub(r"'\\\\\\''", "", inner).count("'") == 0


@pytest.mark.parametrize("path", ["", "a\nb.cmd", "a\x00b.cmd", "a\rb.cmd", "a\x1fb"])
def test_a_path_with_a_control_character_is_refused_without_echoing_it(path):
    with pytest.raises(ValueError) as raised:
        escape_command_path(path)
    assert "\n" not in str(raised.value) and len(str(raised.value)) < 80


@needs_ffmpeg
def test_real_ffmpeg_reads_a_command_file_at_a_pathological_path(tmp_path):
    """Un dossier avec espace, apostrophe, virgule, point-virgule, crochets et signe égal : FFmpeg lit bien le fichier cité."""
    directory = tmp_path / "Jean D'Arc, [v1]; a=b"
    directory.mkdir()
    cmd = directory / "x'y.cmd"
    cmd.write_text("0 gblur@t sigma 30, gblur@t sigmaV 30;\n", encoding="utf-8")
    base = ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=64x36:r=5:d=0.4", "-frames:v", "1"]
    graph = f"{sendcmd_filter(str(cmd))},gblur@t=sigma=0:sigmaV=0,format=rgb24"
    blurred = subprocess.run([*base, "-vf", graph, "-f", "rawvideo", "-"], capture_output=True)
    sharp = subprocess.run([*base, "-vf", "gblur=sigma=0:sigmaV=0,format=rgb24", "-f", "rawvideo", "-"], capture_output=True)
    assert blurred.returncode == 0, blurred.stderr.decode()[:300]
    assert blurred.stdout != sharp.stdout, "la commande doit avoir changé le flou"


# ---------------------------------------------------------------------------
# (c) Fichier temporaire : écrit dans la liste de l'appelant, supprimé avec elle
# ---------------------------------------------------------------------------


def test_files_go_to_the_callers_list_and_are_unique(tmp_path):
    written: list[str] = []
    with command_files(written):
        first = write_command_file("0 a b c;\n")
        second = write_command_file("0 a b c;\n")
    assert written == [first, second] and first != second
    assert all(os.path.isfile(path) and Path(path).read_text(encoding="utf-8") == "0 a b c;\n" for path in written)
    for path in written:
        os.remove(path)


def test_without_an_owner_the_file_is_named_by_its_content_in_a_process_directory():
    one, two = write_command_file("0 a b c;\n"), write_command_file("0 a b c;\n")
    other = write_command_file("0 a b d;\n")
    assert one == two != other and os.path.isfile(one)
    assert os.path.basename(os.path.dirname(one)).startswith("kut-studio-sendcmd-")


def test_the_context_is_restored_after_the_construction():
    written: list[str] = []
    with command_files(written):
        pass
    leftover = write_command_file("0 x y z;\n")
    assert leftover not in written and written == []


# ---------------------------------------------------------------------------
# (d) Dans la chaîne d'un clip
# ---------------------------------------------------------------------------


def _chain(effects, animation, **kwargs):
    written: list[str] = []
    context = kwargs.pop("context", context_for(30, 0.0, 3.0))
    with command_files(written):
        text = _build_clip_effect_filters(tuple(effects), 1.0, "v0fx", animation=tuple(animation), sendcmd=context, **kwargs)
    return text, written


def test_an_animated_blur_is_a_named_gblur_after_its_sendcmd():
    blur = create_effect(EffectType.BLUR, effect_id="b")
    text, files = _chain([blur], [kf("fx.b.intensity", 0.0, 2.0), kf("fx.b.intensity", 2.0, 8.0)])
    assert len(files) == 1
    assert re.fullmatch(r"sendcmd=f='[^']+',gblur@v0fx0=sigma=2\.0", text), text


def test_an_animated_glow_names_its_gblur_and_keeps_the_static_gain_and_threshold():
    glow = create_effect(EffectType.GLOW, effect_id="g", params={"threshold": 0.5, "radius": 18.0, "intensity": 2.0})
    text, files = _chain([glow], [kf("fx.g.radius", 0.0, 3.0), kf("fx.g.radius", 2.0, 30.0)])
    static, _ = _chain([glow], [], context=None)
    assert len(files) == 1 and text.startswith("sendcmd=f='")
    assert "gblur@v0fx0=sigma=3.0[v0fx0g]" in text
    assert static.replace("gblur=sigma=18.0", "gblur@v0fx0=sigma=3.0") == text[text.index(",") + 1:]


def test_the_chromatic_aberration_keeps_its_round_trip_and_starts_at_the_first_frame_value():
    ca = create_effect(EffectType.CHROMATIC_ABERRATION, effect_id="c")
    text, files = _chain([ca], [kf("fx.c.intensity", 0.0, 4.0), kf("fx.c.intensity", 2.0, 10.0)])
    assert len(files) == 1
    assert text.endswith("format=rgba,rgbashift@v0fx0=rh=4:bh=-4:edge=smear,format=rgba,format=yuva444p")


def test_a_chromatic_curve_that_stays_at_zero_adds_nothing():
    ca = create_effect(EffectType.CHROMATIC_ABERRATION, effect_id="c")
    text, files = _chain([ca], [kf("fx.c.intensity", 0.0, 0.0), kf("fx.c.intensity", 2.0, 0.0)])
    assert text == "" and files == []                    # ni filtre (le statique à 4 n'est pas rendu non plus), ni fichier


def test_two_animated_effects_get_their_own_files_and_distinct_names():
    first = create_effect(EffectType.BLUR, effect_id="b1")
    second = create_effect(EffectType.BLUR, effect_id="b2")
    animation = [kf("fx.b1.intensity", 0.0, 1.0), kf("fx.b1.intensity", 1.0, 5.0),
                 kf("fx.b2.intensity", 0.0, 9.0), kf("fx.b2.intensity", 1.0, 2.0)]
    text, files = _chain([first, second], animation)
    assert len(files) == 2 and "gblur@v0fx0=" in text and "gblur@v0fx1=" in text
    assert len(set(re.findall(r"sendcmd=f='([^']+)'", text))) == 2


def test_a_disabled_effect_writes_nothing():
    blur = replace(create_effect(EffectType.BLUR, effect_id="b"), enabled=False)
    text, files = _chain([blur], [kf("fx.b.intensity", 0.0, 1.0), kf("fx.b.intensity", 1.0, 5.0)])
    assert text == "" and files == []


def test_without_a_stream_context_the_static_chain_is_unchanged():
    """Empreintes de cache et calques graphiques n'ont pas de contexte de flux : la chaîne reste celle d'avant, sans fichier."""
    blur = create_effect(EffectType.BLUR, effect_id="b", params={"intensity": 4.0})
    animation = (kf("fx.b.intensity", 0.0, 1.0), kf("fx.b.intensity", 1.0, 5.0))
    written: list[str] = []
    with command_files(written):
        assert _build_clip_effect_filters((blur,), animation=animation) == "gblur=sigma=4.0"
    assert written == []


def test_the_instance_name_keeps_only_safe_characters():
    assert filter_name(EffectType.BLUR, "n0_v1fx0") == "gblur@n0_v1fx0"
    assert filter_name(EffectType.CHROMATIC_ABERRATION, "v 1;fx,[0]") == "rgbashift@v_1_fx__0_"
    assert filter_name(EffectType.GLOW, "") == "gblur@fx"


# ---------------------------------------------------------------------------
# (e) Le graphe complet : projet sans courbe inchangé, projet animé complet
# ---------------------------------------------------------------------------

W, H, FPS = 96, 54, 10


def _project(tmp_path: Path, effects, animation, *, source_fps=10, source_in=0.5, timeline_start=1.0, speed=None, length=6.0,
             fps=FPS):
    from core.timeline_operations import set_clip_speed

    source = tmp_path / f"src{source_fps}.mp4"
    if not source.exists():
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        f"testsrc2=s={W}x{H}:r={source_fps}:d={length}", "-pix_fmt", "yuv420p", str(source)], check=True)
    project = Project(name="sendcmd", width=W, height=H, fps=float(fps))
    project.media_assets.append(MediaAsset("a", str(source), "src", length, W, H, float(source_fps), "video"))
    clip = Clip("v", "a", "V1", timeline_start, source_in, source_in + 5.0)
    clip.effects = list(effects)
    clip.animation = list(animation)
    project.tracks.append(Track("V1", "V1", "video", clips=[clip]))
    if speed is not None:
        set_clip_speed(project, "v", speed)
    return project


def _fake_project(effects, animation) -> Project:
    asset = MediaAsset("a", "/tmp/a.mp4", "a", 10.0, W, H, 25.0, "video")
    clip = Clip("c", "a", "V1", 0.0, 0.0, 4.0)
    clip.effects = list(effects)
    clip.animation = list(animation)
    return Project("p", width=W, height=H, fps=25.0, media_assets=[asset], tracks=[Track("V1", "V1", "video", clips=[clip])])


def _all_effects():
    return [create_effect(kind, effect_id=f"e-{kind.value}") for kind in EffectType]


def test_a_project_without_a_curve_has_the_same_graph_and_writes_nothing():
    """Octets identiques : aucune courbe ``fx.*`` donc ni ``sendcmd`` ni fichier, quel que soit le reste de l'animation."""
    effects = _all_effects()
    reference = ExportEngine._build_filter_complex(build_render_plan(_fake_project(effects, [])), W, H, 25, None)
    unrelated = [kf("position_x", 0.0, 1.0), kf("fx.e-sharpen.intensity", 0.0, 3.0), kf("fx.e-glow.threshold", 0.0, 0.2),
                 kf("fx.e-glow.intensity", 0.0, 3.0), kf("fx.nothing.radius", 0.0, 3.0)]
    written: list[str] = []
    other = ExportEngine._build_filter_complex(build_render_plan(_fake_project(effects, unrelated)), W, H, 25, None,
                                               temporary_files=written)
    assert other[0] == reference[0] and "sendcmd" not in reference[0] and written == []


@pytest.mark.parametrize("kind, name", VEHICLES)
def test_an_animated_project_writes_one_file_into_the_callers_list_and_cites_it(kind, name):
    effects = [create_effect(kind, effect_id="e")]
    animation = curve_for(effects[0], name)
    written: list[str] = []
    graph, *_ = ExportEngine._build_filter_complex(build_render_plan(_fake_project(effects, animation)), W, H, 25, None,
                                                   temporary_files=written)
    try:
        assert len(written) == 1 and os.path.isfile(written[0])
        assert escape_command_path(written[0]) in graph and graph.count("sendcmd=") == 1
        assert re.search(r"(gblur|rgbashift)@v0fx0=", graph)
    finally:
        for path in written:
            os.remove(path)


def test_the_fingerprint_is_stable_across_builds_and_follows_the_curve():
    import core.filter_graph as filter_graph

    effects = [create_effect(EffectType.BLUR, effect_id="e")]
    animation = curve_for(effects[0], "intensity")

    def print_of(frames):
        return filter_graph.fingerprint_plan(build_render_plan(_fake_project(effects, frames)), width=W, height=H, fps=25)

    first = print_of(animation)
    assert first == print_of(animation), "le fichier temporaire (nom aléatoire) n'entre pas dans l'empreinte"
    assert len({first, print_of(animation[:-1]), print_of([])}) == 3
    assert filter_graph.RENDER_ENGINE_VERSION == 13, "pas de rendu d'un projet existant qui change : la version ne bouge pas"


def _request(project, output):
    from core.export_engine import ExportFormat, ExportPreset, ExportRequest

    return ExportRequest(render_plan=build_render_plan(project), output_path=str(output), format=ExportFormat.MP4_H264,
                         preset=ExportPreset(name="test", resolution=(W, H), crf=28, audio_bitrate="64k"), fps=25.0)


def test_build_export_command_hands_the_command_file_to_its_caller(tmp_path):
    from core.export_engine import build_export_command

    media = tmp_path / "a.mp4"
    media.write_bytes(b"")
    effects = [create_effect(EffectType.BLUR, effect_id="e")]
    project = _fake_project(effects, curve_for(effects[0], "intensity"))
    project.media_assets[0].path = str(media)
    own: list[str] = []
    command = build_export_command(_request(project, tmp_path / "out.mp4"), temporary_files=own)
    try:
        assert len(command.temporary_files) == 1 and own == list(command.temporary_files)
        assert any(str(Path(own[0]).as_posix()) in argument or escape_command_path(own[0]) in argument
                   for argument in command.arguments)
    finally:
        for path in command.temporary_files:
            os.remove(path)


def test_a_failure_after_the_file_is_written_leaves_no_temporary_file(tmp_path, monkeypatch):
    import core.export_engine as engine

    media = tmp_path / "a.mp4"
    media.write_bytes(b"")
    effects = [create_effect(EffectType.BLUR, effect_id="e")]
    project = _fake_project(effects, curve_for(effects[0], "intensity"))
    project.media_assets[0].path = str(media)
    seen: list[str] = []
    real = engine.ExportEngine._build_filter_complex

    def spy(*args, **kwargs):
        result = real(*args, **kwargs)
        seen.extend(kwargs["temporary_files"])
        return result

    monkeypatch.setattr(engine.ExportEngine, "_build_filter_complex", staticmethod(spy))
    monkeypatch.setattr(engine, "filter_graph_arguments", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    own: list[str] = []
    with pytest.raises(RuntimeError, match="boom"):
        engine.build_export_command(_request(project, tmp_path / "out.mp4"), temporary_files=own)
    assert len(seen) == 1 and not os.path.exists(seen[0]) and own == []


def test_the_preview_command_collects_the_command_files_in_the_callers_list(tmp_path):
    from core.filter_graph import build_preview_command

    effects = [create_effect(EffectType.CHROMATIC_ABERRATION, effect_id="e")]
    plan = build_render_plan(_fake_project(effects, curve_for(effects[0], "intensity")))
    own: list[str] = []
    build_preview_command(plan, width=W, height=H, fps=25, quality="high", start=1.0, duration=1.0,
                          output_path=str(tmp_path / "p.mp4"), temporary_files=own)
    try:
        assert len(own) == 1 and os.path.isfile(own[0])
        # Le segment commence à 1 s : la première instruction est une demi-image avant la première image du segment.
        assert float(Path(own[0]).read_text(encoding="utf-8").split(" ")[0]) == pytest.approx(1.0 - 0.5 / 25, abs=1e-6)
    finally:
        for path in own:
            os.remove(path)


# ---------------------------------------------------------------------------
# (f) FFmpeg réel : l'image n de la sortie animée = le rendu statique à la valeur de la courbe
# ---------------------------------------------------------------------------

RATE = 4.0
FRAMES = 9
SIZE = (320, 180)


def _run_frames(chain, raw, *, count, rate="4"):
    np = pytest.importorskip("numpy")
    width, height = SIZE
    completed = subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", f"{width}x{height}", "-framerate", rate,
         "-i", "-", "-vf", chain, "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
        input=raw * count, capture_output=True, check=True)
    return np.frombuffer(completed.stdout, np.uint8).reshape(count, height, width, 4)[..., :3]


def _source():
    from tests.gpu_harness import smooth_pattern

    codes = smooth_pattern(*SIZE)
    return codes, codes[..., 0].tobytes() + codes[::2, ::2, 1].tobytes() + codes[::2, ::2, 2].tobytes()


def _real_chain(effects, animation, fps_context):
    from core.chroma_siting import siting_before_rgb

    written: list[str] = []
    with command_files(written):
        text = _build_clip_effect_filters(tuple(effects), 1.0, "v0fx", animation=tuple(animation), sendcmd=fps_context)
    return siting_before_rgb((text + "," if text else "") + "format=rgba"), written


@needs_ffmpeg
@pytest.mark.parametrize("kind, name", VEHICLES)
def test_each_animated_frame_equals_the_static_export_at_the_evaluated_value(kind, name):
    """Méthode de ``tools/validate_sendcmd.py`` : image ``n`` animée = image ``n`` du rendu STATIQUE du même effet réglé sur la
    valeur de la courbe à l'instant de l'image (écart max 0 mesuré sur FFmpeg 6.1.1, 7.1.5 et 9.0.2)."""
    np = pytest.importorskip("numpy")
    params = {"threshold": 0.5, "intensity": 1.5} if kind is EffectType.GLOW else {}
    effect = create_effect(kind, effect_id="e", params=params)
    spec = next(s for s in EFFECT_PARAMETER_SPECS[kind] if s.name == name)
    top = 14.0 if kind is EffectType.CHROMATIC_ABERRATION else 20.0
    prop = fx_property_id("e", name)
    animation = [kf(prop, 0.0, max(spec.minimum, 2.0), B, out_slope=10.0), kf(prop, 1.0, top, E_), kf(prop, 2.0, 3.0, L)]
    _codes, raw = _source()
    chain, files = _real_chain([effect], animation, context_for(RATE, 0.0, FRAMES / RATE))
    try:
        animated = _run_frames(chain, raw, count=FRAMES)
    finally:
        for path in files:
            os.remove(path)
    clip = clip_with([effect], animation)
    for index in range(FRAMES):
        fixed = evaluated_effects(tuple(clip.effects), tuple(clip.animation), index / RATE)
        static_chain, _ = _real_chain(fixed, (), None)
        static = _run_frames(static_chain, raw, count=1)[0]
        assert int(np.abs(animated[index].astype(int) - static.astype(int)).max()) == 0, (kind, index)
    assert int(np.abs(animated[-1].astype(int) - animated[0].astype(int)).max()) > 4, "l'effet doit varier"


@needs_ffmpeg
def test_ntsc_frames_receive_their_own_value():
    """29,97 i/s : une instruction par image à ``(n - 0,5) x 1001 / 30000`` ; chaque image égale le rendu statique."""
    np = pytest.importorskip("numpy")
    effect = create_effect(EffectType.BLUR, effect_id="e")
    animation = [kf("fx.e.intensity", 0.0, 0.0), kf("fx.e.intensity", 0.3, 16.0)]
    _codes, raw = _source()
    count = 10
    chain, files = _real_chain([effect], animation, context_for(29.97, 0.0, 0.4))
    try:
        animated = _run_frames(chain, raw, count=count, rate="30000/1001")
    finally:
        for path in files:
            os.remove(path)
    clip = clip_with([effect], animation)
    for index in (0, 1, 4, 9):
        fixed = evaluated_effects(tuple(clip.effects), tuple(clip.animation), index * 1001 / 30000)
        static = _run_frames(_real_chain(fixed, (), None)[0], raw, count=1, rate="30000/1001")[0]
        assert int(np.abs(animated[index].astype(int) - static.astype(int)).max()) == 0, index


@needs_ffmpeg
@pytest.mark.parametrize("name, effects_and_curve", [
    ("flou", (EffectType.BLUR, "intensity", 0.0, 14.0)),
    ("aberration", (EffectType.CHROMATIC_ABERRATION, "intensity", 2.0, 12.0)),
    ("rayon", (EffectType.GLOW, "radius", 1.0, 20.0)),
])
def test_the_monitor_reference_matches_the_animated_export_like_a_static_effect(name, effects_and_curve):
    """Parité export / moniteur : à chaque image échantillonnée, l'écart entre l'export animé et la référence du moniteur
    (paramètres évalués à cet instant, ``program_for``) est celui d'un effet STATIQUE réglé sur la même valeur (l'écart de
    formule propre à l'effet, connu : ``test_gpu_pipeline``), pas davantage ; et il est bien plus grand si le moniteur
    gardait la valeur de la première image (la mesure voit la différence)."""
    np = pytest.importorskip("numpy")
    from core.gpu_effects import program_for, reference_layer
    from core.gpu_frames import yuv_to_rgb_matrix
    from tests.gpu_harness import sited_codes

    kind, param, low, high = effects_and_curve
    params = {"threshold": 0.5, "intensity": 1.2} if kind is EffectType.GLOW else {}
    effect = create_effect(kind, effect_id="e", params=params)
    animation = [kf(fx_property_id("e", param), 0.0, low, L), kf(fx_property_id("e", param), 2.0, high, L)]
    codes, raw = _source()
    chain, files = _real_chain([effect], animation, context_for(RATE, 0.0, FRAMES / RATE))
    try:
        frames = _run_frames(chain, raw, count=FRAMES)
    finally:
        for path in files:
            os.remove(path)
    clip = clip_with([effect], animation)
    reference = sited_codes(codes)

    def error(effects, time, frame):
        expected = reference_layer(reference, program_for(effects, time=time), yuv_to_rgb=yuv_to_rgb_matrix())
        diff = np.abs(expected - frame / 255.0) * 255
        return float(diff.mean()), float(np.percentile(diff, 99))

    stale = 0.0
    for index in range(FRAMES):
        time = index / RATE
        fixed = evaluated_effects(tuple(clip.effects), tuple(clip.animation), time)
        static_frame = _run_frames(_real_chain(fixed, (), None)[0], raw, count=1)[0]
        mean, p99 = error(fixed, time, frames[index])
        static_mean, static_p99 = error(fixed, time, static_frame)
        assert mean <= static_mean + 0.05 and p99 <= static_p99 + 0.5, (name, index, mean, p99, static_mean, static_p99)
        stale = max(stale, error(evaluated_effects(tuple(clip.effects), tuple(clip.animation), 0.0), 0.0, frames[index])[0] - mean)
    assert stale > 0.15, "le moniteur à la mauvaise valeur doit s'écarter nettement de l'export"


# --- base de temps : clip entier contre segment d'aperçu qui démarre en milieu de clip ---------------------------------


def _render_frames(plan, tmp_path, name, *, start, duration, fps=FPS):
    np = pytest.importorskip("numpy")
    from core.filter_graph import build_preview_command

    owned: list[str] = []
    command = build_preview_command(plan, width=W, height=H, fps=fps, quality="high", start=start, duration=duration,
                                    output_path=str(tmp_path / f"{name}.mp4"), temporary_files=owned)
    output = tmp_path / f"{name}.mkv"
    tail = min(command.index(option) for option in ("-ss", "-t") if option in command)
    lossless = [*command[: command.index("-c:v")], "-c:v", "ffv1", "-c:a", "pcm_s16le", *command[tail:-1], str(output)]
    try:
        completed = subprocess.run(lossless, capture_output=True, text=True, timeout=120)
    finally:
        for path in owned:
            os.remove(path)
    assert completed.returncode == 0, completed.stderr
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(output), "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"],
                         capture_output=True, check=True).stdout
    size = W * H * 3 // 2
    return [np.frombuffer(raw[i * size:(i + 1) * size], np.uint8).astype(int) for i in range(len(raw) // size)]


SCENARIOS = {
    "clip_decale": (dict(), 3.0, 1.0),
    "cadence_24_vers_10": (dict(source_fps=24), 3.0, 1.0),
    "clip_retime_x2": (dict(speed=2.0), 2.0, 0.5),
}


@needs_ffmpeg
@pytest.mark.parametrize("scenario", list(SCENARIOS))
@pytest.mark.parametrize("kind, name, low, high", [
    (EffectType.BLUR, "intensity", 0.0, 6.0),
    (EffectType.CHROMATIC_ABERRATION, "intensity", 1.0, 5.0),
    (EffectType.GLOW, "radius", 1.0, 8.0),
])
def test_a_segment_starting_mid_clip_renders_the_same_animated_frames_as_the_whole_clip(tmp_path, scenario, kind, name, low, high):
    """Les images clés sont en temps local du clip : un segment qui démarre en milieu de clip (``origin``) garde les horodatages
    locaux, le clip entier aussi, et le fichier de commandes part du temps local de la première image : mêmes images, et chacune
    égale le rendu STATIQUE à la valeur de la courbe à son instant local."""
    np = pytest.importorskip("numpy")
    options, start, duration = SCENARIOS[scenario]
    params = {"threshold": 0.5, "intensity": 1.2} if kind is EffectType.GLOW else {}
    effects = [create_effect(kind, effect_id="e", params=params)]
    animation = [kf(fx_property_id("e", name), 0.0, low), kf(fx_property_id("e", name), 4.0, high)]
    plan = build_render_plan(_project(tmp_path, effects, animation, **options))
    full = _render_frames(plan, tmp_path, "full", start=0.0, duration=6.0)
    segment = _render_frames(plan, tmp_path, "segment", start=start, duration=duration)
    first = round(start * FPS)
    assert len(segment) >= round(duration * FPS)
    for index, frame in enumerate(segment):
        assert int(np.abs(frame - full[first + index]).max()) <= 1, (scenario, kind, index)
    clip = clip_with(effects, animation)
    for index in (0, len(segment) // 2, len(segment) - 1):
        local = start + index / FPS - 1.0
        fixed = evaluated_effects(tuple(clip.effects), tuple(clip.animation), local)
        static_plan = build_render_plan(_project(tmp_path, list(fixed), [], **options))
        static = _render_frames(static_plan, tmp_path, "static", start=0.0, duration=6.0)[first + index]
        worst = int(np.abs(segment[index] - static).max())
        assert worst <= 2, (scenario, kind, index, local, worst)
    assert int(np.abs(full[first + len(segment) - 1] - full[first]).max()) > 2, "l'effet doit varier"


@needs_ffmpeg
def test_a_frozen_clip_animates_from_its_own_zero(tmp_path):
    """Arrêt sur image : le flux repart de 0 (pas de ``skip``) : le flou animé varie sur toute la durée du clip."""
    np = pytest.importorskip("numpy")
    from core.time_remapping import FreezeFrameMode, TimeRemapping

    effects = [create_effect(EffectType.BLUR, effect_id="e")]
    animation = [kf("fx.e.intensity", 0.0, 0.0), kf("fx.e.intensity", 2.0, 6.0)]
    project = _project(tmp_path, effects, animation)
    clip = project.tracks[0].clips[0]
    clip.time_remapping = TimeRemapping(freeze_mode=FreezeFrameMode.FREEZE, freeze_duration=3.0)
    plan = build_render_plan(project)
    frames = _render_frames(plan, tmp_path, "freeze", start=0.0, duration=4.0)
    first = round(1.0 * FPS)                                                # le clip commence à 1 s
    sharp, blurred = frames[first], frames[first + 22]
    assert int(np.abs(sharp - blurred).max()) > 10 and int(np.abs(frames[first + 22] - frames[first + 28]).max()) <= 1     # tenu à 6 après 2 s


# ---------------------------------------------------------------------------
# (g) Le rendu des couches du plan : calque tourné, échelle animée, clip verrouillé, pistes
# ---------------------------------------------------------------------------


def test_a_rotated_scaled_layer_keeps_the_command_file_before_the_effect():
    effects = (create_effect(EffectType.BLUR, effect_id="e"),)
    layer = RenderLayer(
        clip_id="c", asset_id="a", track_id="V1", track_index=0, source_path="/tmp/s.mp4", source_in=0.0, source_out=3.0,
        timeline_start=1.0, timeline_end=4.0, effects=effects,
        animation=(kf("fx.e.intensity", 0.0, 1.0), kf("fx.e.intensity", 2.0, 9.0)))
    from core.visual_effects import ClipTransform, TransformKeyframe

    layer = replace(layer, transform=ClipTransform(rotation=20.0), transform_keyframes=(
        TransformKeyframe("scale", 0.0, 1.0), TransformKeyframe("scale", 1.0, 1.4)))
    written: list[str] = []
    with command_files(written):
        chain = _build_layer_filter(0, layer, 0, 640, 360, 30)
    try:
        assert len(written) == 1
        assert chain.index("rotate=") < chain.index("sendcmd=") < chain.index("gblur@v0fx0=") < chain.index("setpts=PTS+")
    finally:
        for path in written:
            os.remove(path)
