"""Étalonnage animé à l'export (ADR-0008, étape 6) : ``color.*`` rendus par expression (``eq``, ``hue``) et par ``sendcmd``
(``colorbalance``, ``colorchannelmixer``, ``curves`` pour l'exposition et les roues).

Sans FFmpeg : la forme du fichier de commandes (valeurs du modèle, demi-image avant l'image visée, un fichier par étalonnage), la
chaîne d'un clip sans courbe (inchangée, octet pour octet), l'ordre des étages, les bornes. Avec FFmpeg réel (``needs_ffmpeg``) :
les tables de ``curves`` égalent celles de ``lutrgb`` niveau par niveau, et l'image ``n`` de la sortie animée égale le rendu
**statique** à la valeur de la courbe (méthode de ``tools/validate_sendcmd.py``).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from fractions import Fraction

import pytest

from core.animation import InterpolationType, Keyframe
from core.chroma_siting import siting_before_rgb
from core.color_grading import ColorGrade, Wheel
from core.color_wheels import exposure_filter, wheels_filter
from core.effect_sendcmd import command_files, context_for, frame_count
from core.export_engine import ExportEngine, _build_color_grade_filters
from core.grade_render import MAX_COMMAND_BYTES, animated_grade
from core.grade_tables import curves_points, exposure_table, transfer_table, wheel_tables
from core.grade_targets import (
    ANIMATABLE_GRADE_PARAMS,
    EXPRESSION_GRADE_PARAMS,
    SENDCMD_GRADE_PARAMS,
    WHEEL_IDS,
    effective_grade,
    grade_spec,
    scalar_property_id,
    wheel_property_id,
)
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import build_render_plan

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg absent")

L, B, H_, E_ = InterpolationType.LINEAR, InterpolationType.BEZIER, InterpolationType.HOLD, InterpolationType.EASE_IN_OUT


def kf(prop, time, value, interpolation=L, **slopes):
    return Keyframe(prop, time, value, interpolation, **slopes)


def clip_with(grade, animation) -> Clip:
    clip = Clip(id="c", asset_id="a", track_id="V1", timeline_start=0.0, source_in=0.0, source_out=5.0)
    clip.color_grade = grade
    clip.animation = list(animation)
    return clip


def curve_for(property_id):
    """Quatre images clés aux interpolations variées, qui évitent la valeur neutre aux instants des images testées."""
    spec = grade_spec(property_id)
    lo, hi = spec.minimum, spec.maximum
    span = hi - lo
    return [kf(property_id, 0.0, lo + 0.13 * span, B, out_slope=3.0 * span, in_slope=3.0 * span),
            kf(property_id, 1.0, lo + 0.83 * span, E_), kf(property_id, 1.6, lo + 0.31 * span, H_),
            kf(property_id, 2.2, lo + 0.71 * span, L)]


def gentle_curve(property_id):
    """Trois images clés autour de la valeur neutre (un quart de la plage) : tous les réglages ensemble gardent une image lisible."""
    spec = grade_spec(property_id)
    reach = 0.25 * (spec.maximum - spec.minimum) / 2.0
    return [kf(property_id, 0.0, spec.default - 0.9 * reach), kf(property_id, 1.1, spec.default + 0.7 * reach, E_),
            kf(property_id, 2.2, spec.default - 0.4 * reach)]


def chain_of(grade, animation, context, tag="v0cg"):
    written: list[str] = []
    with command_files(written):
        text = _build_color_grade_filters(grade, tag, animation=tuple(animation), sendcmd=context)
    return text, written


def cleanup(paths):
    for path in paths:
        os.remove(path)


def applied(text: str, fps: Fraction, count: int, start: float = 0.0):
    """Ce que ``sendcmd`` applique à chaque image : `[(image, {(cible, option): argument})]` (la dernière instruction gagne)."""
    lines = []
    for raw in text.strip().splitlines():
        stamp, body = raw.split(" ", 1)
        assert body.endswith(";"), raw[:80]
        lines.append((float(stamp), body[:-1]))
    assert [t for t, _ in lines] == sorted(t for t, _ in lines)
    state, result, cursor = {}, [], 0
    for index in range(count):
        instant = start + float(Fraction(index) / fps)
        while cursor < len(lines) and lines[cursor][0] <= instant:
            for command in re.split(r", (?=[a-z]+@)", lines[cursor][1]):
                target, option, argument = command.split(" ", 2)
                state[(target, option)] = argument
            cursor += 1
        result.append(dict(state))
    return result


# ---------------------------------------------------------------------------
# (a) Sans FFmpeg
# ---------------------------------------------------------------------------


def test_every_registered_property_has_a_render_route_and_only_one():
    assert set(EXPRESSION_GRADE_PARAMS) | set(SENDCMD_GRADE_PARAMS) == set(ANIMATABLE_GRADE_PARAMS)
    assert not set(EXPRESSION_GRADE_PARAMS) & set(SENDCMD_GRADE_PARAMS)
    assert len(ANIMATABLE_GRADE_PARAMS) == 7 + 16 == len(set(ANIMATABLE_GRADE_PARAMS))


def test_a_clip_without_a_color_curve_has_the_chain_it_had_before():
    """Octets identiques : ni ``sendcmd`` ni fichier, ni ``animation`` d'autre nature qui change la chaîne."""
    grade = ColorGrade(exposure=0.4, contrast=0.2, saturation=1.3, temperature=20.0, hue=12.0, shadows=0.1, highlights=-0.2,
                       lift=Wheel(0.1, 0.0, -0.1, 0.05))
    reference = _build_color_grade_filters(grade, "t")
    unrelated = (kf("position_x", 0.0, 1.0), kf("fx.e.intensity", 0.0, 3.0), kf("color.nothing", 0.0, 3.0),
                 kf("color.curves", 0.0, 3.0))
    written: list[str] = []
    with command_files(written):
        assert _build_color_grade_filters(grade, "t", animation=unrelated, sendcmd=context_for(30, 0.0, 2.0)) == reference
        assert _build_color_grade_filters(grade, "t", animation=(), sendcmd=context_for(30, 0.0, 2.0)) == reference
        assert _build_color_grade_filters(grade, "t", animation=None) == reference
    assert written == []


def test_the_static_golden_chains_are_unchanged():
    """Chaînes relevées sur ``origin/main`` avant l'étape 6 : le rendu des fichiers existants ne change pas (donc pas de
    ``RENDER_ENGINE_VERSION``)."""
    assert _build_color_grade_filters(ColorGrade(contrast=0.2, saturation=1.3, hue=12.0, shadows=0.1)) == (
        "eq=contrast=1.2:saturation=1.3,hue=h=12.0,colorbalance=rs=0.1:gs=0.1:bs=0.1:rh=0.0:gh=0.0:bh=0.0")
    assert _build_color_grade_filters(ColorGrade(temperature=30.0)) == "colorchannelmixer=rr=1.09:gg=0.982332:bb=0.91"
    assert _build_color_grade_filters(ColorGrade(exposure=0.5)) == exposure_filter(0.5)
    assert _build_color_grade_filters(ColorGrade(
        gain=Wheel(0.2, 0.0, -0.1, 0.05), lift=Wheel(0, 0, 0, 0.1), highlights=-0.3, temperature=-20.0)) == (
        "colorbalance=rs=0.0:gs=0.0:bs=0.0:rh=-0.3:gh=-0.3:bh=-0.3,colorchannelmixer=rr=0.94:gg=1.011779:bb=1.06,"
        "lutrgb=r='clip(pow(max(0,1.225000*val/maxval+0.025000),1.000000)*maxval+0.5,0,maxval)':"
        "g='clip(pow(max(0,1.025000*val/maxval+0.025000),1.000000)*maxval+0.5,0,maxval)':"
        "b='clip(pow(max(0,0.925000*val/maxval+0.025000),1.000000)*maxval+0.5,0,maxval)'")
    assert _build_color_grade_filters(ColorGrade()) == ""


def test_an_animated_contrast_is_an_eq_expression_with_eval_frame_and_no_file():
    grade = ColorGrade(saturation=1.4, hue=10.0)
    text, files = chain_of(grade, [kf("color.contrast", 0.0, -0.5), kf("color.contrast", 2.0, 0.5)], context_for(30, 0.0, 3.0))
    assert files == []
    assert re.fullmatch(r"eq=contrast='1\+clip\(.*,-1,1\)':saturation=1\.4:eval=frame,hue=h=10\.0", text), text


def test_an_animated_saturation_and_hue_are_expressions():
    grade = ColorGrade(contrast=0.25)
    animation = [kf("color.saturation", 0.0, 0.2), kf("color.saturation", 2.0, 1.8), kf("color.hue", 0.0, -90.0),
                 kf("color.hue", 1.0, 90.0)]
    text, files = chain_of(grade, animation, None)
    assert files == []
    assert re.fullmatch(r"eq=contrast=1\.25:saturation='clip\(.*,0,2\)':eval=frame,hue=h='clip\(.*,-180,180\)'", text), text


def test_every_stage_driven_by_sendcmd_shares_one_plan_read_before_the_first_one():
    """Un seul plan de commandes pour tout l'étalonnage (plusieurs cibles dans la même ligne), lu par des ``sendcmd`` qui se suivent,
    posés devant le premier filtre qu'ils pilotent."""
    grade = ColorGrade(contrast=0.1)
    animation = [kf("color.exposure", 0.0, -1.0), kf("color.exposure", 2.0, 1.0), kf("color.shadows", 0.0, -0.4),
                 kf("color.shadows", 2.0, 0.4), kf("color.temperature", 0.0, -50.0), kf("color.temperature", 2.0, 50.0),
                 kf("color.gain.y", 0.0, -0.3), kf("color.gain.y", 2.0, 0.3)]
    text, files = chain_of(grade, animation, context_for(10, 0.0, 2.0))
    try:
        assert files and text.count("sendcmd=") == len(files) and text.startswith("sendcmd=f='")
        order = [text.index(name) for name in ("curves@v0cge=", "eq=contrast=1.1", "colorbalance@v0cgb=",
                                               "colorchannelmixer@v0cgt=", "curves@v0cgw=")]
        assert order == sorted(order), text[:400]
        assert text.rindex("sendcmd=") < order[0], "tous les sendcmd précèdent le premier filtre piloté"
        assert text.count("sendcmd=") == text[: order[0]].count("sendcmd=")
    finally:
        cleanup(files)


def test_a_long_plan_is_cut_into_windows_each_read_by_its_own_sendcmd():
    """FFmpeg 6.1 et 7.1 lisent un fichier de commandes avec ~1000x sa taille de mémoire (4,4 Go pour 3,7 Mo, mesuré) : le plan est coupé en
    fichiers d'au plus ``WINDOW_BYTES``, sans trou ni recouvrement, et la mémoire (rendue après chaque lecture) ne dépend plus de la durée."""
    from core.grade_render import WINDOW_BYTES

    animation = [kf("color.exposure", 0.0, -1.5), kf("color.exposure", 20.0, 1.5), kf("color.shadows", 0.0, -0.5),
                 kf("color.shadows", 20.0, 0.5)]
    context = context_for(30, 0.0, 20.0)
    text, files = chain_of(ColorGrade(), animation, context)
    try:
        assert len(files) > 10 and text.count("sendcmd=") == len(files)
        sizes = [os.path.getsize(path) for path in files]
        assert max(sizes) <= WINDOW_BYTES
        lines = [line for path in files for line in open(path, encoding="utf-8").read().splitlines()]
        stamps = [float(line.split(" ")[0]) for line in lines]
        assert stamps == sorted(stamps) and stamps[0] == 0.0 and len(set(stamps)) == len(stamps)
        states = applied("\n".join(lines), context.fps, frame_count(context))
        clip = clip_with(ColorGrade(), animation)
        for index in (0, 1, 299, 300, 599):
            expected = effective_grade(clip, index / 30)
            table = states[index][("curves@v0cge", "master")].strip("'").split(" ")
            assert table == curves_points(exposure_table(expected.exposure)).split(" "), index
            assert float(states[index][("colorbalance@v0cgb", "rs")]) == pytest.approx(expected.shadows, abs=6e-7)
    finally:
        cleanup(files)


def test_a_window_never_exceeds_the_budget_unless_a_single_line_does():
    from core.grade_render import _windows

    assert list(_windows(["a" * 10, "b" * 10, "c" * 10], 25)) == [["a" * 10, "b" * 10], ["c" * 10]]
    assert list(_windows(["a" * 100], 25)) == [["a" * 100]]
    assert list(_windows([], 25)) == []


def test_without_a_stream_context_the_sendcmd_stages_keep_their_base_value():
    """Empreintes de cache, calques sans flux connu : exposition, ombres, température et roues restent à leur valeur de base ;
    contraste, saturation et teinte suivent encore leur courbe (expression : pas besoin du flux)."""
    grade = ColorGrade(exposure=0.5, temperature=20.0)
    animation = [kf("color.exposure", 0.0, -1.0), kf("color.exposure", 2.0, 1.0), kf("color.temperature", 0.0, 0.0),
                 kf("color.temperature", 1.0, 80.0), kf("color.contrast", 0.0, -0.3), kf("color.contrast", 1.0, 0.3)]
    text, files = chain_of(grade, animation, None)
    assert files == [] and "sendcmd" not in text
    static = _build_color_grade_filters(grade)
    assert text.startswith(static.split(",")[0]) and "eval=frame" in text
    assert exposure_filter(0.5) in text and "colorchannelmixer=rr=1.06" in text


def test_a_disabled_or_graph_grade_writes_nothing():
    animation = [kf("color.exposure", 0.0, -1.0), kf("color.exposure", 2.0, 1.0)]
    assert chain_of(ColorGrade(enabled=False), animation, context_for(30, 0.0, 2.0)) == ("", [])
    from core.color_nodes import ColorNodeGraph, as_graph

    graph = as_graph(ColorGrade(exposure=0.3))
    assert isinstance(graph, ColorNodeGraph)
    text, files = chain_of(graph, animation, context_for(30, 0.0, 2.0))
    assert files == [] and "sendcmd" not in text


def test_every_frame_receives_the_value_of_the_model_for_the_scalar_stages():
    grade = ColorGrade(highlights=0.15)
    animation = [kf("color.shadows", 0.0, -0.6, B, out_slope=2.0), kf("color.shadows", 1.0, 0.7, E_), kf("color.shadows", 2.0, -0.2),
                 kf("color.temperature", 0.0, -80.0), kf("color.temperature", 1.5, 90.0, E_)]
    context = context_for(25, 0.0, 2.0)
    _text, files = chain_of(grade, animation, context)
    try:
        states = applied(open(files[0], encoding="utf-8").read(), context.fps, frame_count(context))
    finally:
        cleanup(files)
    clip = clip_with(grade, animation)
    from core.export_engine import temperature_gains

    for index, state in enumerate(states):
        expected = effective_grade(clip, index / 25)
        shadow = float(state[("colorbalance@v0cgb", "rs")])
        assert shadow == pytest.approx(expected.shadows, abs=6e-7)
        assert {state[("colorbalance@v0cgb", option)] for option in ("rs", "gs", "bs")} == {state[("colorbalance@v0cgb", "rs")]}
        for option, gain in zip(("rr", "gg", "bb"), temperature_gains(expected.temperature)):
            assert float(state[("colorchannelmixer@v0cgt", option)]) == pytest.approx(gain, abs=6e-7), (index, option)
        assert ("colorbalance@v0cgb", "rh") not in state, "les hautes lumières ne sont pas animées : aucune commande"


def test_commands_are_written_half_a_frame_before_the_frame_and_only_when_a_value_changes():
    animation = [kf("color.shadows", 0.0, 0.0), kf("color.shadows", 1.0, 0.5)]
    _text, files = chain_of(ColorGrade(), animation, context_for(30, 0.0, 1.0))
    try:
        stamps = [float(line.split(" ")[0]) for line in open(files[0], encoding="utf-8").read().splitlines()]
    finally:
        cleanup(files)
    assert stamps[0] == 0.0 and stamps[1:4] == pytest.approx([0.5 / 30, 1.5 / 30, 2.5 / 30], abs=1e-9)
    held = [kf("color.shadows", 0.0, 0.25, H_), kf("color.shadows", 1.0, -0.25, H_), kf("color.shadows", 2.0, -0.25, H_)]
    _text, files = chain_of(ColorGrade(), held, context_for(30, 0.0, 3.0))
    try:
        assert len(open(files[0], encoding="utf-8").read().splitlines()) == 2        # 0,25 puis -0,25
    finally:
        cleanup(files)


def test_a_segment_starts_at_its_local_time():
    animation = [kf("color.temperature", 0.0, -100.0), kf("color.temperature", 4.0, 100.0)]
    context = context_for(10, 2.55, 5.0)
    _text, files = chain_of(ColorGrade(), animation, context)
    try:
        text = open(files[0], encoding="utf-8").read()
    finally:
        cleanup(files)
    assert float(text.splitlines()[0].split(" ")[0]) == pytest.approx(2.55 - 0.05)
    clip = clip_with(ColorGrade(), animation)
    from core.export_engine import temperature_gains

    for index, state in enumerate(applied(text, context.fps, frame_count(context), start=2.55)):
        expected = temperature_gains(effective_grade(clip, 2.55 + index / 10).temperature)[0]
        assert float(state[("colorchannelmixer@v0cgt", "rr")]) == pytest.approx(expected, abs=6e-7)


def test_values_are_bounded_like_the_model():
    """Une courbe qui dépasse les bornes (pente de Bézier démesurée, valeur écrite à la main) est écrêtée, jamais écrite telle quelle."""
    animation = [kf("color.shadows", 0.0, -50.0), kf("color.shadows", 1.0, 400.0)]
    context = context_for(30, 0.0, 2.0)
    _text, files = chain_of(ColorGrade(), animation, context)
    try:
        values = [float(state[("colorbalance@v0cgb", "rs")]) for state in
                  applied(open(files[0], encoding="utf-8").read(), context.fps, frame_count(context))]
    finally:
        cleanup(files)
    assert min(values) == -1.0 and max(values) == 1.0
    text, _ = chain_of(ColorGrade(), [kf("color.contrast", 0.0, -9.0), kf("color.contrast", 1.0, 9.0)], None)
    assert ",-1,1)" in text


def test_a_non_finite_keyframe_never_reaches_the_graph():
    animation = [kf("color.contrast", 0.0, float("nan")), kf("color.contrast", 1.0, float("inf")), kf("color.hue", 0.0, float("-inf")),
                 kf("color.exposure", 0.0, float("nan"))]
    text, files = chain_of(ColorGrade(contrast=0.3), animation, context_for(30, 0.0, 2.0))
    assert files == [] and "nan" not in text.lower() and "inf" not in text.lower()
    assert text == _build_color_grade_filters(ColorGrade(contrast=0.3))


def test_a_huge_duration_is_refused_without_a_file_and_a_long_one_stops_at_the_size_bound():
    animation = [kf("color.exposure", 0.0, -1.0), kf("color.exposure", 2.0, 1.0)]
    huge = context_for(60, 0.0, 10.0 * 3600)                       # plus de 2 000 000 d'images : refusé avant tout fichier
    written: list[str] = []
    with command_files(written):
        with pytest.raises(ValueError, match="trop longue") as raised:
            _build_color_grade_filters(ColorGrade(), "t", animation=tuple(animation), sendcmd=huge)
    assert written == [] and len(str(raised.value)) < 120
    # Sous la borne de lignes mais au-dessus de celle de taille : le texte s'arrête à MAX_COMMAND_BYTES, le fichier partiel part avec les
    # temporaires de l'appelant (la liste), jamais plus de MAX_COMMAND_BYTES sur le disque, le message ne recopie rien.
    wheels = [kf("color.lift.r", 18.0 * i, -0.5 if i % 2 else 0.5) for i in range(101)]      # cent allers-retours : la table change presque à chaque image
    with command_files(written):
        with pytest.raises(ValueError, match="trop longue") as raised:
            _build_color_grade_filters(ColorGrade(), "t", animation=tuple(wheels), sendcmd=context_for(30, 0.0, 1800.0))
    try:
        assert written and sum(os.path.getsize(path) for path in written) <= MAX_COMMAND_BYTES + 192 * 1024
        assert len(str(raised.value)) < 120
    finally:
        cleanup(written)
    assert MAX_COMMAND_BYTES <= 64 * 1024 * 1024


def test_the_wheel_stage_uses_one_master_channel_only_when_the_three_channels_stay_equal():
    grade = ColorGrade()
    only_master = [kf("color.gain.y", 0.0, -0.2), kf("color.gain.y", 1.0, 0.2)]
    one, files = chain_of(grade, only_master, context_for(10, 0.0, 1.0))
    cleanup(files)
    assert "curves@v0cgw=master='" in one and "red=" not in one
    colored = [kf("color.gain.r", 0.0, -0.2), kf("color.gain.r", 1.0, 0.2)]
    three, files = chain_of(grade, colored, context_for(10, 0.0, 1.0))
    cleanup(files)
    assert "red='" in three and "green='" in three and "blue='" in three and "master=" not in three
    # Un palet de base déjà coloré rend les canaux inégaux dès que la molette bouge.
    tinted, files = chain_of(ColorGrade(gain=Wheel(0.2, 0.0, -0.1, 0.0)), only_master, context_for(10, 0.0, 1.0))
    cleanup(files)
    assert "red='" in tinted


def test_the_named_filters_keep_only_safe_characters():
    text, files = chain_of(ColorGrade(), [kf("color.shadows", 0.0, 0.1), kf("color.shadows", 1.0, 0.5)],
                           context_for(10, 0.0, 1.0), tag="n 0;[v],1cg")
    cleanup(files)
    assert "colorbalance@n_0__v__1cgb=" in text


# --- tables : le modèle de lutrgb, sans FFmpeg --------------------------------------------------------------------------


def test_the_neutral_tables_are_the_identity():
    assert exposure_table(0.0) == tuple(range(256))
    assert transfer_table(1.0, 0.0, 1.0) == tuple(range(256))
    assert wheel_tables(ColorGrade()) == (tuple(range(256)),) * 3


def test_the_tables_are_monotone_and_inside_the_range():
    for stops in (-2.0, -0.7, 0.3, 2.0):
        table = exposure_table(stops)
        assert len(table) == 256 and min(table) >= 0 and max(table) <= 255
        assert all(a <= b for a, b in zip(table, table[1:]))
    assert exposure_table(2.0)[255] == 255 and exposure_table(-2.0)[0] == 0


def test_the_curve_points_satisfy_the_curves_filter_rules():
    """``(int)(x·255)`` croît strictement, x et y sont dans [0, 1] : sinon ``curves`` refuse toute la commande."""
    for table in (tuple(range(256)), exposure_table(1.3), transfer_table(2.0, 0.1, 0.5)):
        pairs = [tuple(float(v) for v in item.split("/")) for item in curves_points(table).split(" ")]
        assert len(pairs) == 256
        bins = [int(x * 255) for x, _y in pairs]
        assert bins == list(range(256))
        assert all(0.0 <= x <= 1.0 and 0.0 <= y <= 1.0 for x, y in pairs)


# ---------------------------------------------------------------------------
# (b) FFmpeg réel : les tables de curves égalent celles de lutrgb
# ---------------------------------------------------------------------------


def _ramp(chain: str):
    np = pytest.importorskip("numpy")
    completed = subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=256x2:r=1:d=1", "-vf",
         f"format=rgba,geq=r='X':g='X':b='X':a=255,{chain}", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
        capture_output=True, check=True)
    return np.frombuffer(completed.stdout, np.uint8).reshape(2, 256, 4)[0, :, :3].astype(int)


@needs_ffmpeg
@pytest.mark.parametrize("stops", [-2.0, -1.3, -0.5, 0.0004, 0.37, 1.0, 1.77, 2.0])
def test_the_curves_table_of_an_exposure_equals_the_lutrgb_table_level_by_level(stops):
    np = pytest.importorskip("numpy")
    reference = _ramp(exposure_filter(stops))[:, 0]
    assert reference.tolist() == list(exposure_table(stops)), "le modèle de la table égale lutrgb"
    through_curves = _ramp(f"curves=master='{curves_points(exposure_table(stops))}'")
    assert int(np.abs(through_curves - reference[:, None]).max()) == 0


@needs_ffmpeg
@pytest.mark.parametrize("grade", [
    ColorGrade(lift=Wheel(0.0, 0.0, 0.0, 0.2)),
    ColorGrade(gamma=Wheel(0.3, -0.2, 0.0, -0.4)),
    ColorGrade(gain=Wheel(0.5, -0.25, -0.25, 0.1), offset=Wheel(0.0, 0.1, 0.0, 0.0)),
    ColorGrade(lift=Wheel(-1.0, 1.0, 0.5, -1.0), gamma=Wheel(1.0, 1.0, -1.0, 1.0), gain=Wheel(-1.0, -1.0, -1.0, -1.0)),
    ColorGrade(gamma=Wheel(0.0, 0.0, 0.0, 1.0), gain=Wheel(0.0, 0.0, 0.0, 1.0), offset=Wheel(0.0, 0.0, 0.0, 1.0)),
])
def test_the_curves_tables_of_the_wheels_equal_the_lutrgb_tables(grade):
    np = pytest.importorskip("numpy")
    reference = _ramp(wheels_filter(grade))
    tables = wheel_tables(grade)
    for channel in range(3):
        assert reference[:, channel].tolist() == list(tables[channel]), channel
    chain = ":".join(f"{name}='{curves_points(table)}'" for name, table in zip(("red", "green", "blue"), tables))
    assert int(np.abs(_ramp(f"curves={chain}") - reference).max()) == 0


@needs_ffmpeg
def test_a_random_sample_of_wheels_and_exposures_matches_lutrgb():
    import random

    np = pytest.importorskip("numpy")
    rng = random.Random(23)
    for _ in range(25):
        stops = rng.uniform(-2.0, 2.0)
        assert _ramp(exposure_filter(stops))[:, 0].tolist() == list(exposure_table(stops)), stops
        component = lambda: round(rng.uniform(-1.0, 1.0), 3) if rng.random() < 0.6 else 0.0  # noqa: E731
        grade = ColorGrade(lift=Wheel(*(component() for _ in range(4))), gamma=Wheel(*(component() for _ in range(4))),
                           gain=Wheel(*(component() for _ in range(4))), offset=Wheel(*(component() for _ in range(4))))
        if grade.wheels_are_neutral():
            continue
        reference = _ramp(wheels_filter(grade))
        assert all(reference[:, c].tolist() == list(wheel_tables(grade)[c]) for c in range(3)), grade
    assert np is not None


# ---------------------------------------------------------------------------
# (c) FFmpeg réel : l'image n animée = le rendu statique à la valeur de la courbe
# ---------------------------------------------------------------------------

RATE = 4.0
FRAMES = 9
SIZE = (320, 180)


def _source():
    from tests.gpu_harness import smooth_pattern

    codes = smooth_pattern(*SIZE)
    return codes[..., 0].tobytes() + codes[::2, ::2, 1].tobytes() + codes[::2, ::2, 2].tobytes()


def _run_frames(chain, raw, *, count, rate="4"):
    np = pytest.importorskip("numpy")
    width, height = SIZE
    completed = subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", f"{width}x{height}", "-framerate", rate,
         "-i", "-", "-vf", chain, "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
        input=raw * count, capture_output=True, check=True)
    return np.frombuffer(completed.stdout, np.uint8).reshape(count, height, width, 4)[..., :3].astype(int)


def _real_chain(grade, animation, context):
    text, files = chain_of(grade, animation, context)
    return siting_before_rgb((text + "," if text else "") + "format=rgba"), files


def _assert_parity(grade, animation, *, frames=(0, 1, 2, 4, 6, 8), rate=RATE, count=FRAMES, ffrate="4", tolerance=0):
    np = pytest.importorskip("numpy")
    raw = _source()
    chain, files = _real_chain(grade, animation, context_for(rate, 0.0, count / rate))
    try:
        animated = _run_frames(chain, raw, count=count, rate=ffrate)
    finally:
        cleanup(files)
    clip = clip_with(grade, animation)
    worst = 0
    for index in frames:
        fixed = effective_grade(clip, index / rate if rate != 29.97 else index * 1001 / 30000)
        static_chain, extra = _real_chain(fixed, (), None)
        assert extra == []
        static = _run_frames(static_chain, raw, count=1, rate=ffrate)[0]
        worst = max(worst, int(np.abs(animated[index] - static).max()))
    assert worst <= tolerance, worst
    assert int(np.abs(animated[-1] - animated[0]).max()) > 3, "le réglage doit varier"
    return worst


@needs_ffmpeg
@pytest.mark.parametrize("property_id", ANIMATABLE_GRADE_PARAMS)
def test_each_animated_frame_equals_the_static_export_at_the_evaluated_value(property_id):
    """Les 23 propriétés : image ``n`` animée = image ``n`` du rendu STATIQUE de l'étalonnage réglé sur la valeur de la courbe à
    l'instant de l'image (écart 0 : mêmes niveaux, mêmes arrondis)."""
    base = ColorGrade(contrast=0.15, saturation=1.2) if property_id not in EXPRESSION_GRADE_PARAMS else ColorGrade()
    _assert_parity(base, curve_for(property_id))


@needs_ffmpeg
def test_a_clip_that_was_never_graded_is_animated_from_the_neutral_grade():
    """``Clip.color_grade`` est ``None`` pour un clip jamais étalonné : les images clés partent du réglage neutre (le test de la couche
    du plan de rendu passe par ce cas)."""
    animation = curve_for("color.exposure") + curve_for("color.contrast")
    chain, files = _real_chain(None, animation, context_for(RATE, 0.0, FRAMES / RATE))
    cleanup(files)
    assert "curves@v0cge=" in chain and "eq=contrast='1+" in chain
    _assert_parity(None, animation)


@needs_ffmpeg
def test_a_plan_cut_into_several_windows_stays_exact_across_the_window_boundaries():
    """80 images d'exposition et de roue colorée (~0,6 Mo) : plusieurs fenêtres de 192 Ko, donc plusieurs ``sendcmd`` à la suite ; les
    images de part et d'autre de chaque frontière reçoivent leur valeur (écart 0 contre le rendu statique)."""
    from core.grade_render import WINDOW_BYTES

    animation = [kf("color.exposure", 0.0, -1.2), kf("color.exposure", 80 / 30, 1.2), kf("color.lift.r", 0.0, -0.3),
                 kf("color.lift.r", 80 / 30, 0.3)]
    chain, files = chain_of(ColorGrade(), animation, context_for(30.0, 0.0, 80 / 30))
    try:
        assert len(files) >= 3 and max(os.path.getsize(path) for path in files) <= WINDOW_BYTES
        edges = []
        for path in files[1:]:                                    # la première instruction de chaque fenêtre suivante
            first = float(open(path, encoding="utf-8").readline().split(" ")[0])
            edges.append(round(first * 30))
    finally:
        cleanup(files)
    frames = sorted({0, 79, *(index + delta for index in edges for delta in (-1, 0, 1, 2) if 0 <= index + delta < 80)})
    _assert_parity(ColorGrade(), animation, frames=tuple(frames), rate=30.0, count=80, ffrate="30")


@needs_ffmpeg
def test_all_stages_animated_together_still_match_the_static_render():
    """Les sept réglages et les quatre roues animés ensemble : un seul fichier, des cibles distinctes, chaque image égale le statique."""
    animation = []
    for property_id in ANIMATABLE_GRADE_PARAMS:
        animation.extend(gentle_curve(property_id))
    _assert_parity(ColorGrade(), animation)


@needs_ffmpeg
def test_ntsc_frames_receive_their_own_value():
    animation = [kf("color.exposure", 0.0, -1.2), kf("color.exposure", 0.3, 1.1), kf("color.temperature", 0.0, -60.0),
                 kf("color.temperature", 0.3, 70.0), kf("color.contrast", 0.0, -0.4), kf("color.contrast", 0.3, 0.45)]
    _assert_parity(ColorGrade(), animation, frames=(0, 1, 4, 9), rate=29.97, count=10, ffrate="30000/1001")


@needs_ffmpeg
def test_thirty_fps_frames_receive_their_own_value_not_the_previous_one():
    """À 30 i/s l'instant d'une image (``n/30``) n'est pas un nombre exact de microsecondes : une commande écrite à l'instant exact part
    une image trop tard une fois sur trois (``docs/ffmpeg-sendcmd.md``). Écrite une demi-image avant, chaque image reçoit sa valeur."""
    animation = [kf("color.exposure", 0.0, -1.5), kf("color.exposure", 0.3, 1.5), kf("color.shadows", 0.0, -0.8),
                 kf("color.shadows", 0.3, 0.8), kf("color.temperature", 0.0, -90.0), kf("color.temperature", 0.3, 90.0)]
    _assert_parity(ColorGrade(), animation, frames=tuple(range(10)), rate=30.0, count=10, ffrate="30")


@needs_ffmpeg
def test_an_exposure_that_passes_through_zero_matches_the_static_chain_which_omits_the_filter():
    """À 0 EV la chaîne statique n'émet pas de filtre ; la chaîne animée garde ``curves`` (table identité) : même image."""
    animation = [kf("color.exposure", 0.0, -1.0), kf("color.exposure", 2.0, 1.0)]       # 0 exactement à t = 1 s (image 4)
    _assert_parity(ColorGrade(), animation, frames=(0, 3, 4, 5, 8))


@needs_ffmpeg
def test_neutral_yuv_stages_are_exact_when_nothing_before_them_is_rgb():
    """Contraste 0, saturation 1 et teinte 0 à ``t = 1 s`` : la chaîne fixe omet ``eq`` et ``hue``, l'animée les garde ; dans le flux YUV
    d'origine ils sont l'identité exacte (mesuré : écart 0)."""
    animation = [kf("color.contrast", 0.0, -0.5), kf("color.contrast", 2.0, 0.5), kf("color.saturation", 0.0, 0.0),
                 kf("color.saturation", 2.0, 2.0), kf("color.hue", 0.0, -60.0), kf("color.hue", 2.0, 60.0)]
    _assert_parity(ColorGrade(), animation, frames=(4,), tolerance=0)


@needs_ffmpeg
@pytest.mark.parametrize("animation", [
    [kf("color.contrast", 0.0, -0.5), kf("color.contrast", 2.0, 0.5)],
    [kf("color.saturation", 0.0, 0.0), kf("color.saturation", 2.0, 2.0)],
    [kf("color.hue", 0.0, -60.0), kf("color.hue", 2.0, 60.0)],
], ids=["contraste", "saturation", "teinte"])
def test_a_neutral_yuv_stage_after_an_rgb_filter_costs_at_most_two_levels_on_that_frame(animation):
    """Limite connue (ADR-0008, étape 6) : si un filtre RVB précède (ici l'exposition fixe), l'``eq`` ou le ``hue`` gardé à l'image où
    sa valeur est exactement neutre ajoute l'aller-retour RVB / YUV que la chaîne fixe n'a pas : mesuré 2 niveaux au plus, sur cette
    seule image (toutes les autres sont exactes : ``test_each_animated_frame_equals_the_static_export_at_the_evaluated_value``)."""
    _assert_parity(ColorGrade(exposure=0.5), animation, frames=(4,), tolerance=2)


def test_the_siting_conversion_goes_before_the_first_named_rgb_filter():
    """Une chaîne dont le premier filtre RVB est nommé (``curves@…``, ``colorbalance@…``) reçoit la conversion de chroma co-sitée
    devant lui, comme son homologue fixe : sinon FFmpeg insérait sa propre conversion (recopie de la chroma)."""
    from core.chroma_siting import CHROMA_SITING_TO_RGBA

    for animation, first in (
            ([kf("color.exposure", 0.0, -1.0), kf("color.exposure", 2.0, 1.0)], "curves@v0cge="),
            ([kf("color.shadows", 0.0, -1.0), kf("color.shadows", 2.0, 1.0)], "colorbalance@v0cgb="),
            ([kf("color.temperature", 0.0, -9.0), kf("color.temperature", 2.0, 9.0)], "colorchannelmixer@v0cgt="),
            ([kf("color.gain.y", 0.0, -0.1), kf("color.gain.y", 2.0, 0.1)], "curves@v0cgw=")):
        chain, files = _real_chain(ColorGrade(), animation, context_for(RATE, 0.0, 2.0))
        cleanup(files)
        assert chain.count(CHROMA_SITING_TO_RGBA) == 1 and chain.index(CHROMA_SITING_TO_RGBA) < chain.index(first), chain[:300]
    static = _real_chain(ColorGrade(exposure=0.5), (), None)[0]
    assert static.count(CHROMA_SITING_TO_RGBA) == 1


@needs_ffmpeg
def test_the_monitor_chain_is_the_static_chain_of_the_base_grade():
    """Le moniteur (étape 7) n'est pas touché : sans paramètre d'animation, ``gpu_grade.grade_filters`` rend la chaîne statique."""
    from core.gpu_grade import grade_filters

    grade = ColorGrade(exposure=0.3, contrast=0.1)
    assert grade_filters(grade) == _build_color_grade_filters(grade)


# ---------------------------------------------------------------------------
# (d) Le graphe complet d'un clip
# ---------------------------------------------------------------------------

W, H = 96, 54


def _fake_project(grade, animation) -> Project:
    asset = MediaAsset("a", "/tmp/a.mp4", "a", 10.0, W, H, 25.0, "video")
    clip = Clip("c", "a", "V1", 0.0, 0.0, 4.0)
    clip.color_grade = grade
    clip.animation = list(animation)
    return Project("p", width=W, height=H, fps=25.0, media_assets=[asset], tracks=[Track("V1", "V1", "video", clips=[clip])])


def test_a_project_without_a_color_curve_has_the_same_graph_and_writes_nothing():
    grade = ColorGrade(exposure=0.4, saturation=1.2, lift=Wheel(0.1, 0.0, 0.0, 0.0))
    unrelated = [kf("position_x", 0.0, 1.0), kf("fx.nothing.radius", 0.0, 3.0), kf("color.curves", 0.0, 0.2)]
    reference = ExportEngine._build_filter_complex(build_render_plan(_fake_project(grade, [])), W, H, 25, None)
    written: list[str] = []
    other = ExportEngine._build_filter_complex(build_render_plan(_fake_project(grade, unrelated)), W, H, 25, None,
                                               temporary_files=written)
    assert other[0] == reference[0] and "sendcmd" not in reference[0] and written == []


def test_an_animated_project_writes_its_files_into_the_callers_list_and_cites_them():
    from core.effect_sendcmd import escape_command_path

    animation = [kf("color.exposure", 0.0, -1.0), kf("color.exposure", 2.0, 1.0), kf("color.contrast", 0.0, -0.3),
                 kf("color.contrast", 2.0, 0.3)]
    written: list[str] = []
    graph, *_ = ExportEngine._build_filter_complex(build_render_plan(_fake_project(ColorGrade(), animation)), W, H, 25, None,
                                                   temporary_files=written)
    try:
        assert written and all(os.path.isfile(path) for path in written)
        assert all(escape_command_path(path) in graph for path in written) and graph.count("sendcmd=") == len(written)
        assert "curves@v0cge=" in graph and "eq=contrast='1+" in graph
    finally:
        cleanup(written)


def test_the_fingerprint_follows_the_curve_and_is_stable():
    import core.filter_graph as filter_graph

    animation = [kf("color.exposure", 0.0, -1.0), kf("color.exposure", 2.0, 1.0)]

    def print_of(frames):
        return filter_graph.fingerprint_plan(build_render_plan(_fake_project(ColorGrade(), frames)), width=W, height=H, fps=25)

    first = print_of(animation)
    assert first == print_of(list(animation)), "le fichier temporaire (nom aléatoire) n'entre pas dans l'empreinte"
    assert len({first, print_of(animation[:1]), print_of([])}) == 3
    assert filter_graph.RENDER_ENGINE_VERSION == 13, "aucun fichier existant ne change de rendu : la version ne bouge pas"


def test_animated_grade_returns_none_when_nothing_is_animated():
    assert animated_grade(ColorGrade(), [], None, "t", str) is None
    assert animated_grade(ColorGrade(), [kf("fx.e.intensity", 0.0, 1.0)], None, "t", str) is None
    assert animated_grade(ColorGrade(), [kf("color.exposure", 0.0, 1.0)], None, "t", str) is not None
    assert animated_grade(None, [kf("color.exposure", 0.0, 1.0)], None, "t", str) is not None, "jamais étalonné : part du neutre"


def test_scalar_and_wheel_ids_are_wired_to_the_model_names():
    assert scalar_property_id("exposure") == "color.exposure" and wheel_property_id("lift", "r") == "color.lift.r"
    assert len(WHEEL_IDS) == 16


# ---------------------------------------------------------------------------
# (e) Base de temps : clip entier contre segment d'aperçu qui démarre en milieu de clip (FFmpeg réel)
# ---------------------------------------------------------------------------

FPS = 10


def _project(tmp_path, grade, animation, *, source_fps=10, source_in=0.5, timeline_start=1.0, speed=None, length=6.0):
    from core.timeline_operations import set_clip_speed

    source = tmp_path / f"src{source_fps}.mp4"
    if not source.exists():
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        f"testsrc2=s={W}x{H}:r={source_fps}:d={length}", "-pix_fmt", "yuv420p", str(source)], check=True)
    project = Project(name="grade", width=W, height=H, fps=float(FPS))
    project.media_assets.append(MediaAsset("a", str(source), "src", length, W, H, float(source_fps), "video"))
    clip = Clip("v", "a", "V1", timeline_start, source_in, source_in + 5.0)
    clip.color_grade = grade
    clip.animation = list(animation)
    project.tracks.append(Track("V1", "V1", "video", clips=[clip]))
    if speed is not None:
        set_clip_speed(project, "v", speed)
    return project


def _render_frames(plan, tmp_path, name, *, start, duration):
    np = pytest.importorskip("numpy")
    from core.filter_graph import build_preview_command

    owned: list[str] = []
    command = build_preview_command(plan, width=W, height=H, fps=FPS, quality="high", start=start, duration=duration,
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
@pytest.mark.parametrize("animation", [
    [kf("color.exposure", 0.0, -1.2), kf("color.exposure", 4.0, 1.2)],
    [kf("color.contrast", 0.0, -0.6), kf("color.contrast", 4.0, 0.6)],
    [kf("color.temperature", 0.0, -80.0), kf("color.temperature", 4.0, 80.0), kf("color.gain.y", 0.0, -0.3), kf("color.gain.y", 4.0, 0.3)],
], ids=["exposition", "contraste", "temperature+roue"])
def test_a_segment_starting_mid_clip_renders_the_same_animated_frames_as_the_whole_clip(tmp_path, scenario, animation):
    """Images clés en temps local du clip : un segment qui démarre en milieu de clip (``origin``) rend les mêmes images que le clip
    entier, et chacune égale le rendu STATIQUE à la valeur de la courbe à son instant local (au plus 2 niveaux : le montage recode)."""
    np = pytest.importorskip("numpy")
    options, start, duration = SCENARIOS[scenario]
    plan = build_render_plan(_project(tmp_path, ColorGrade(), animation, **options))
    full = _render_frames(plan, tmp_path, "full", start=0.0, duration=6.0)
    segment = _render_frames(plan, tmp_path, "segment", start=start, duration=duration)
    first = round(start * FPS)
    assert len(segment) >= round(duration * FPS)
    for index, frame in enumerate(segment):
        assert int(np.abs(frame - full[first + index]).max()) <= 1, (scenario, index)
    clip = clip_with(ColorGrade(), animation)
    for index in (0, len(segment) // 2, len(segment) - 1):
        local = start + index / FPS - 1.0
        fixed = effective_grade(clip, local)
        static_plan = build_render_plan(_project(tmp_path, fixed, [], **options))
        static = _render_frames(static_plan, tmp_path, "static", start=0.0, duration=6.0)[first + index]
        assert int(np.abs(segment[index] - static).max()) <= 2, (scenario, index, local)
    assert int(np.abs(full[first + len(segment) - 1] - full[first]).max()) > 2, "le réglage doit varier"


# ---------------------------------------------------------------------------
# (f) Suivi : l'état dérivé reprend l'animation du clip, les images clés d'étalonnage la traversent
# ---------------------------------------------------------------------------

from tests.test_tracking import _cache_dir, _clip, _tracked_project, media  # noqa: E402,F401  (fixtures du module de suivi)


@needs_ffmpeg
def test_color_keyframes_cross_a_stabilized_clip_into_the_render_plan(media, tmp_path):
    """``tracking_bindings._compute_state`` part de ``list(clip.animation)`` et ne remplace que les courbes ``mask.*`` dérivées : une
    image clé d'étalonnage traverse la stabilisation (mode recadrage compris, qui réécrit les courbes du masque) jusqu'à la couche
    du plan de rendu, et le graphe cite bien le fichier de commandes."""
    from core import tracking_ops as ops
    from core.tracking_bindings import TrackingContext, effective_clip_state
    from core.tracking_model import BorderMode, Smoothing, StabilizationMode

    rigid = media["rigid_points"]
    project, ids, _result = _tracked_project(media, "rigid", points=rigid(0))
    ops.set_stabilization(project, "v", tracker_ids=tuple(ids), mode=StabilizationMode.POSITION_ROTATION,
                          smoothing=Smoothing.HIGH, borders=BorderMode.CROP)
    clip = _clip(project)
    clip.animation = [kf("color.exposure", 0.0, -1.0), kf("color.exposure", 1.0, 1.0), kf("color.lift.y", 0.0, -0.2),
                      kf("color.lift.y", 1.0, 0.2)]
    state = effective_clip_state(clip, TrackingContext(project))
    assert any(k.property_name.startswith("mask.trackcrop.") for k in state.animation), "la stabilisation a bien réécrit des courbes"
    assert {k.property_name for k in state.animation if k.property_name.startswith("color.")} == {"color.exposure", "color.lift.y"}
    layer = build_render_plan(project).video_layers[0]
    assert {k.property_name for k in layer.animation if k.property_name.startswith("color.")} == {"color.exposure", "color.lift.y"}
    written: list[str] = []
    graph, *_ = ExportEngine._build_filter_complex(build_render_plan(project), 320, 180, 30, None, temporary_files=written)
    try:
        assert written and "curves@" in graph
    finally:
        cleanup(written)


# ---------------------------------------------------------------------------
# (g) Séquence imbriquée : le clip imbriqué et les clips de la séquence interne ont chacun leur étalonnage animé
# ---------------------------------------------------------------------------


def test_a_nested_sequence_clip_and_the_clips_inside_it_are_each_graded_by_their_own_curve():
    from core.project_model import Sequence
    from core.sequences import insert_sequence_clip

    media = MediaAsset("m", "/tmp/m.mp4", "m", 10.0, W, H, 25.0, "video", False)
    inner = Clip("l", "m", "V1", 0.0, 0.0, 4.0)
    inner.animation = [kf("color.exposure", 0.0, -1.0), kf("color.exposure", 3.0, 1.0)]
    leaf = Sequence("leaf", "leaf", W, H, 10.0, tracks=[Track("V1", "V1", "video", clips=[inner]), Track("V2", "V2", "video")])
    main = Sequence("main", "main", W, H, 10.0, tracks=[Track("V1", "V1", "video"), Track("V2", "V2", "video")])
    project = Project("p", media_assets=[media], sequences=[main, leaf], active_sequence_id="main")
    outer = insert_sequence_clip(project, "leaf", "V2", 0.0, parent_sequence_id="main")
    outer.animation = [kf("color.contrast", 0.0, -0.4), kf("color.contrast", 3.0, 0.4)]
    plan = build_render_plan(project)
    layer = next(item for item in plan.video_layers if item.clip_id == outer.id)
    assert layer.nested_key and {k.property_name for k in layer.animation} == {"color.contrast"}
    graph, *_ = ExportEngine._build_filter_complex(plan, W, H, 25, None, temporary_files=[])
    assert "eq=contrast='1+" in graph, "le clip imbriqué suit sa propre courbe (expression : pas de fichier)"
    # La séquence interne, rendue à part, anime l'exposition de son clip.
    written: list[str] = []
    inner_graph, *_ = ExportEngine._build_filter_complex(build_render_plan(project, sequence_id="leaf"), W, H, 25, None,
                                                         temporary_files=written)
    try:
        assert "curves@" in inner_graph and written
    finally:
        cleanup(written)


# ---------------------------------------------------------------------------
# (h) Écriture du fichier de commandes au fil de l'eau
# ---------------------------------------------------------------------------


def test_the_command_file_is_written_line_by_line_into_the_callers_list():
    from core.effect_sendcmd import write_command_stream

    written: list[str] = []
    with command_files(written):
        path = write_command_stream(line for line in ("0.000000000 a b c;", "0.033333333 a b d;"))
    try:
        assert written == [path] and open(path, encoding="utf-8").read() == "0.000000000 a b c;\n0.033333333 a b d;\n"
    finally:
        cleanup(written)


def test_a_stream_that_fails_leaves_its_partial_file_to_the_caller_and_no_orphan_elsewhere():
    from core.effect_sendcmd import write_command_stream

    def lines():
        yield "0 a b c;"
        raise ValueError("trop long")

    written: list[str] = []
    with command_files(written):
        with pytest.raises(ValueError, match="trop long"):
            write_command_stream(lines())
    assert len(written) == 1 and os.path.isfile(written[0])         # supprimé avec les autres temporaires de l'export
    cleanup(written)
    directory = os.path.dirname(write_command_stream(iter(["0 x y z;"])))       # hors construction : dossier de processus
    with pytest.raises(ValueError, match="trop long"):
        write_command_stream(lines())
    assert not [name for name in os.listdir(directory) if name.endswith(".part")]


def test_outside_a_build_the_stream_is_named_by_its_content():
    from core.effect_sendcmd import write_command_stream

    one, two = write_command_stream(iter(["0 a b c;"])), write_command_stream(iter(["0 a b c;"]))
    other = write_command_stream(iter(["0 a b d;"]))
    assert one == two != other and open(one, encoding="utf-8").read() == "0 a b c;\n"


def test_a_rotated_scaled_layer_keeps_the_command_file_after_the_geometry_and_before_the_timeline_offset():
    """Calque tourné et à l'échelle animée : le fichier de commandes est lu après ``rotate`` (la géométrie ne change pas le temps du flux)
    et avant le décalage de la timeline (``setpts=PTS+…`` ne doit pas décaler les instants de ``sendcmd``)."""
    from dataclasses import replace

    from core.export_engine import _build_layer_filter
    from core.render_plan import RenderLayer
    from core.visual_effects import ClipTransform, TransformKeyframe

    layer = RenderLayer(
        clip_id="c", asset_id="a", track_id="V1", track_index=0, source_path="/tmp/s.mp4", source_in=0.0, source_out=3.0,
        timeline_start=1.0, timeline_end=4.0, animation=(kf("color.exposure", 0.0, -1.0), kf("color.exposure", 2.0, 1.0)))
    layer = replace(layer, transform=ClipTransform(rotation=20.0), transform_keyframes=(
        TransformKeyframe("scale", 0.0, 1.0), TransformKeyframe("scale", 1.0, 1.4)))
    written: list[str] = []
    with command_files(written):
        chain = _build_layer_filter(0, layer, 0, 640, 360, 30)
    try:
        assert written
        assert chain.index("rotate=") < chain.index("sendcmd=") < chain.index("curves@v0cge=") < chain.index("setpts=PTS+")
    finally:
        cleanup(written)
