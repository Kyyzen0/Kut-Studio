"""Le remplaçant LGPL du filtre ``eq`` (ADR-0013) : ``lut`` donne **les mêmes octets** qu'``eq`` (statique et animé).

Sans FFmpeg : la formule (celle d':func:`core.gpu_effects.eq_plane`, déjà éprouvée contre ``eq``), les bornes, le plan de commandes.
Avec FFmpeg réel (``needs_ffmpeg``) : les tables **exécutées** par ``lut`` égalent la formule entière niveau par niveau ; et, quand ce
FFmpeg contient encore ``eq`` (pas celui de la version App Store), les sorties des deux filtres sont identiques octet pour octet, y
compris quand les réglages changent à chaque image par ``sendcmd``.
"""

from __future__ import annotations

import random
import re
import shutil
import subprocess

import pytest

from core import eq_lut
from core.effect_sendcmd import context_for
from core.effects_model import EffectType, create_effect, fx_property_id
from core.gpu_effects import eq_plane
from tests.ffmpeg_filters import has_filter

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg absent")
needs_eq = pytest.mark.skipif(shutil.which("ffmpeg") is None or not has_filter("eq"), reason="ce FFmpeg est LGPL : pas de filtre eq")

BRIGHTNESS = [-1.0, -0.5, -0.3, -0.1, -0.01, 0.0, 0.01, 0.1, 0.3, 0.5, 1.0]
CONTRAST = [0.0, 0.3, 0.7, 1.0, 1.0009765625, 1.3, 1.99, 2.0]
SATURATION = [0.0, 0.3, 0.7, 1.0, 1.4, 2.0, 3.0]


def parse(expression: str) -> tuple[int, int]:
    """``(facteur, décalage)`` d'une expression ``clip(floor(val*F/4096)+O,0,255)``."""
    match = re.fullmatch(r"'clip\(floor\(val\*(-?\d+)/4096\)([+-]\d+),0,255\)'", expression)
    assert match is not None, expression
    return int(match.group(1)), int(match.group(2))


# ---------------------------------------------------------------------------
# Sans FFmpeg
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("brightness", BRIGHTNESS)
@pytest.mark.parametrize("contrast", CONTRAST)
def test_the_luma_expression_is_the_integer_formula_of_eq_plane(brightness, contrast):
    luma = eq_lut.eq_planes(brightness, contrast, 1.0)[0]
    factor, offset, active = eq_plane(contrast, brightness)
    if not active:
        assert luma == eq_lut.IDENTITY
        return
    assert parse(luma) == (round(factor * 4096), int(offset))


@pytest.mark.parametrize("saturation", SATURATION)
def test_both_chroma_planes_use_the_saturation_as_a_contrast_without_brightness(saturation):
    _luma, u, v = eq_lut.eq_planes(0.0, 1.0, saturation)
    assert u == v
    factor, offset, active = eq_plane(saturation, 0.0)
    assert (u == eq_lut.IDENTITY) is (not active)
    if active:
        assert parse(u) == (round(factor * 4096), int(offset))


def test_a_neutral_correction_changes_no_plane_and_never_falls_back_to_clipval():
    """``lut`` écrête à [16, 235] / [16, 240] par défaut (``clipval``) : un plan intact doit s'écrire ``val``."""
    assert eq_lut.eq_planes(0.0, 1.0, 1.0) == ("val", "val", "val")
    assert eq_lut.static_filters(0.0, 1.0, 1.0) == eq_lut.FORMAT_FILTER            # ``eq`` neutre imposait ses formats, rien d'autre
    luma_only = eq_lut.static_filters(0.2, 1.0, 1.0)
    assert ":u=val:v=val" in luma_only and "clipval" not in luma_only


def test_the_settings_are_bounded_and_rounded_like_eq():
    """``av_clipf`` : luminosité [-1, 1], saturation [0, 3], contraste [-1000, 1000] ; stockés en float32."""
    assert eq_lut.eq_planes(5.0, 1.0, 1.0) == eq_lut.eq_planes(1.0, 1.0, 1.0)
    assert eq_lut.eq_planes(-9.0, 1.0, 1.0) == eq_lut.eq_planes(-1.0, 1.0, 1.0)
    assert eq_lut.eq_planes(0.0, 1.0, 9.0) == eq_lut.eq_planes(0.0, 1.0, 3.0)
    assert eq_lut.eq_planes(0.0, 1.0, -2.0) == eq_lut.eq_planes(0.0, 1.0, 0.0)
    assert eq_lut.eq_planes(0.0, 5000.0, 1.0) == eq_lut.eq_planes(0.0, 1000.0, 1.0)
    # float32 : 0.99999999 vaut 1.0 pour eq, qui laisse alors le plan intact (en double, int(c·4096) donnerait 4095).
    assert eq_lut.eq_planes(0.0, 0.99999999, 1.0)[0] == eq_lut.IDENTITY
    assert eq_lut.eq_planes(0.0, 1.0, 0.99999999)[1] == eq_lut.IDENTITY


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), "x", None])
def test_a_non_finite_setting_is_neutral_and_never_reaches_the_graph(bad):
    planes = eq_lut.eq_planes(bad, bad, bad)
    assert planes == ("val", "val", "val")
    text = eq_lut.static_filters(bad, bad, bad)
    assert "nan" not in text.lower() and "inf" not in text.lower()


def test_a_huge_contrast_keeps_the_float_table_of_eq():
    """Au-delà de |contraste| ≥ 7,9, ``eq`` quitte le chemin entier (table flottante) : reproduite, jamais ramenée au chemin entier."""
    luma = eq_lut.eq_planes(0.0, 9.0, 1.0)[0]
    assert "256*" in luma and "floor(val*" not in luma
    assert "e" not in luma.replace("floor", "").replace("clip", "").replace("val", "")        # pas de notation scientifique


def test_the_filter_text_is_one_chain_with_the_formats_of_eq():
    text = eq_lut.static_filters(0.1, 1.3, 1.2)
    assert text.startswith("format=gray|yuv410p|yuv411p|yuv420p|yuva420p|yuv422p|yuva422p|yuv444p|yuva444p,lut=y=")
    assert text.count("lut=") == 1 and "eq=" not in text and "\n" not in text


def _effect(**params):
    return create_effect(EffectType.COLOR_CORRECTION, effect_id="cc", params={"brightness": 0.0, "contrast": 1.0, "saturation": 1.0, **params})


def _ramp(name, low, high, end=2.0):
    from core.animation import InterpolationType, Keyframe

    prop = fx_property_id("cc", name)
    return [Keyframe(prop, 0.0, low, InterpolationType.LINEAR), Keyframe(prop, end, high, InterpolationType.LINEAR)]


def test_a_stage_needs_a_stream_context_and_an_animated_setting():
    effect, animation = _effect(), _ramp("contrast", 0.8, 1.6)
    context = context_for(30, 0.0, 2.0)
    assert eq_lut.effect_stage(effect, animation, None, "v0fx0") is None                 # chaîne statique : valeurs de base
    assert eq_lut.effect_stage(effect, [], context, "v0fx0") is None
    assert eq_lut.effect_stage(effect, None, context, "v0fx0") is None
    other = [k for k in _ramp("contrast", 0.8, 1.6)]
    assert eq_lut.effect_stage(create_effect(EffectType.COLOR_CORRECTION, effect_id="other"), other, context, "v0fx0") is None
    text = eq_lut.effect_stage(effect, animation, context, "v0fx0")
    assert text is not None and text.startswith("sendcmd=f='") and ",lut@v0fx0=y=" in text and "eq=" not in text


def test_only_the_planes_that_change_are_sent_and_the_first_frame_is_the_static_option():
    effect = _effect(saturation=1.4)
    context = context_for(10, 0.0, 2.0)
    values = eq_lut.effect_values(effect, _ramp("contrast", 0.5, 1.5))
    assert values is not None
    plan = eq_lut.plan(values, context, "lut@t")
    assert plan.initial == eq_lut.eq_planes(0.0, 0.5, 1.4)
    assert plan.rows[0].startswith("0.000000000 lut@t y ") and ", lut@t u " in plan.rows[0]      # tout, une fois
    assert all(" u " not in row and " v " not in row for row in plan.rows[1:])                  # la chroma ne bouge plus
    assert all(" y " in row for row in plan.rows)
    stamps = [float(row.split(" ")[0]) for row in plan.rows]
    assert stamps == sorted(stamps) and stamps[0] == 0.0 and stamps[1] == pytest.approx(0.05)    # une demi-image avant l'image 1


def test_values_follow_the_curve_and_the_clamps_of_the_inspector():
    from core.animation import InterpolationType, Keyframe

    prop = fx_property_id("cc", "contrast")
    wild = [Keyframe(prop, 0.0, -50.0, InterpolationType.LINEAR), Keyframe(prop, 1.0, 50.0, InterpolationType.LINEAR)]
    values = eq_lut.effect_values(_effect(), wild)
    assert values is not None and (
        values(0.0)[1], values(0.5)[1], values(0.51)[1], values(1.0)[1]) == (0.0, 0.0, 1.0, 2.0)


def test_a_too_long_animation_is_refused_without_user_data_in_the_message(monkeypatch):
    effect = _effect()
    values = eq_lut.effect_values(effect, _ramp("contrast", 0.1, 1.9, end=60.0))
    assert values is not None
    monkeypatch.setattr(eq_lut, "MAX_EFFECT_COMMANDS", 10)
    with pytest.raises(ValueError, match="trop longue") as raised:
        eq_lut.plan(values, context_for(30, 0.0, 60.0), "lut@t")
    assert len(str(raised.value)) < 160
    monkeypatch.setattr(eq_lut, "MAX_EFFECT_COMMANDS", 40_000)
    monkeypatch.setattr(eq_lut, "MAX_COMMAND_BYTES", 500)
    with pytest.raises(ValueError, match="trop longue"):
        eq_lut.plan(values, context_for(30, 0.0, 60.0), "lut@t")


def test_the_instance_name_keeps_safe_characters_only():
    assert eq_lut.instance_name("v0fx0") == "lut@v0fx0"
    assert eq_lut.instance_name("v0 fx,0'\"[]") == "lut@v0_fx_0____"
    assert eq_lut.instance_name("") == "lut@eq"


# ---------------------------------------------------------------------------
# FFmpeg réel
# ---------------------------------------------------------------------------


def _run(vf: str, raw: bytes, pix_fmt: str, width: int, height: int, out_pix_fmt: str | None = None) -> bytes:
    command = ["ffmpeg", "-v", "error", "-f", "rawvideo", "-pix_fmt", pix_fmt, "-s", f"{width}x{height}", "-i", "-", "-vf", vf]
    command += ["-pix_fmt", out_pix_fmt] if out_pix_fmt else []
    return subprocess.run([*command, "-f", "rawvideo", "-"], input=raw, capture_output=True, check=True).stdout


def _ramps() -> tuple[bytes, int, int]:
    """Une image 4:4:4 dont chaque plan est une rampe 0..255 (pour lire la table de chaque plan)."""
    row = bytes(range(256))
    return row * 2 * 3, 256, 2


@needs_ffmpeg
@pytest.mark.parametrize("brightness, contrast, saturation", [(0.2, 1.3, 0.7), (-0.4, 0.6, 2.0), (0.0, 1.0, 1.5), (0.1, 1.0, 1.0),
                                                              (-1.0, 0.0, 0.0), (1.0, 2.0, 3.0), (0.5, 1.0009765625, 1.0)])
def test_lut_executes_the_integer_tables_of_eq_level_by_level(brightness, contrast, saturation):
    """Sans ``eq`` : les trois plans d'une rampe sortent de ``lut`` tels que la formule entière les donne (y compris hors [16, 235])."""
    np = pytest.importorskip("numpy")
    raw, width, height = _ramps()
    out = np.frombuffer(_run(eq_lut.static_filters(brightness, contrast, saturation), raw, "yuv444p", width, height), np.uint8)
    ramp = np.arange(256)
    for index, (c, b) in enumerate(((contrast, brightness), (saturation, 0.0), (saturation, 0.0))):
        factor, offset, active = eq_plane(c, b)
        expected = np.clip(np.floor(ramp * factor) + offset, 0, 255) if active else ramp
        plane = out[index * 512:index * 512 + 256]
        assert np.array_equal(plane, expected), (index, brightness, contrast, saturation)


@needs_ffmpeg
def test_a_plane_left_alone_is_not_clipped_to_the_broadcast_range():
    """Fait échouer l'oubli de ``val`` : avec ``clipval`` (défaut de ``lut``), une saturation seule écrêterait la luma à [16, 235]."""
    np = pytest.importorskip("numpy")
    raw, width, height = _ramps()
    out = np.frombuffer(_run(eq_lut.static_filters(0.0, 1.0, 1.4), raw, "yuv444p", width, height), np.uint8)
    assert np.array_equal(out[:256], np.arange(256)), "la luma ne doit pas bouger"
    out = np.frombuffer(_run(eq_lut.static_filters(0.2, 1.0, 1.0), raw, "yuv444p", width, height), np.uint8)
    assert np.array_equal(out[256 * 2:256 * 3], np.arange(256)) and np.array_equal(out[512 * 2:512 * 2 + 256], np.arange(256))


@needs_ffmpeg
def test_the_float_table_of_a_huge_contrast_is_executed_as_eq_computes_it():
    np = pytest.importorskip("numpy")
    raw, width, height = _ramps()
    out = np.frombuffer(_run(eq_lut.static_filters(0.1, 9.0, 1.0), raw, "yuv444p", width, height), np.uint8)[:256]
    brightness = float(np.float32(0.1))
    expected = np.clip(np.floor(256.0 * (float(np.float32(9.0)) * (np.arange(256) / 255.0 - 0.5) + 0.5 + brightness)), 0, 255)
    assert np.array_equal(out, expected)


@needs_ffmpeg
@pytest.mark.parametrize("pix_fmt", ["yuv420p10le", "yuv422p10le", "yuv444p16le", "rgba", "rgb24", "gbrp", "gray", "gray10le", "yuva420p",
                                     "yuvj420p", "nv12", "yuv440p"])
def test_lut_only_ever_sees_the_8_bit_formats_eq_accepts(pix_fmt):
    """``eq`` n'accepte que le YUV planaire 8 bits (et le gris) : FFmpeg lui convertit le reste. ``lut`` accepterait le 10 bits tel quel (sa
    table 8 bits y serait fausse) : le ``format=`` qui précède impose la même négociation. ``showinfo`` lit le format réellement négocié."""
    width, height = 32, 16
    raw = bytes(width * height * 8)
    chain = eq_lut.static_filters(0.2, 1.3, 0.7) + ",showinfo"
    completed = subprocess.run(
        ["ffmpeg", "-hide_banner", "-v", "info", "-f", "rawvideo", "-pix_fmt", pix_fmt, "-s", f"{width}x{height}", "-i", "-", "-vf", chain,
         "-frames:v", "1", "-f", "null", "-"], input=raw, capture_output=True, check=True)
    negotiated = re.findall(r"fmt:(\w+)", completed.stderr.decode())
    assert negotiated and negotiated[-1] in eq_lut.EQ_FORMATS.split("|"), (pix_fmt, negotiated)
    bare = subprocess.run(
        ["ffmpeg", "-hide_banner", "-v", "info", "-f", "rawvideo", "-pix_fmt", pix_fmt, "-s", f"{width}x{height}", "-i", "-", "-vf",
         "lut=y=val,showinfo", "-frames:v", "1", "-f", "null", "-"], input=raw, capture_output=True, check=True)
    if pix_fmt in ("yuv420p10le", "yuv422p10le", "yuv444p16le", "gray10le"):
        assert re.findall(r"fmt:(\w+)", bare.stderr.decode())[-1] == pix_fmt, \
            "sans le format imposé, lut garde le 10 bits d'entrée (la preuve que la contrainte sert)"


@needs_eq
@pytest.mark.parametrize("pix_fmt", ["yuv420p", "yuv420p10le", "yuv422p10le", "rgba", "rgb24", "gray", "yuva420p", "yuvj420p", "nv12", "yuv444p"])
@pytest.mark.parametrize("settings", [(0.2, 1.3, 0.7), (0.0, 1.0, 0.5), (-0.3, 0.8, 1.0), (0.0, 1.4, 1.8), (0.0, 1.0, 1.0)])
def test_every_input_format_gives_the_bytes_of_eq(pix_fmt, settings):
    np = pytest.importorskip("numpy")
    width, height = 64, 48
    rng = np.random.default_rng(11)
    per_pixel_x2 = {"yuv420p": 3, "yuv420p10le": 6, "yuv422p10le": 8, "rgba": 8, "rgb24": 6, "gray": 2, "yuva420p": 5, "yuvj420p": 3,
                    "nv12": 3, "yuv444p": 6}[pix_fmt]                  # octets par pixel, doublés (4:2:0 : 1,5 -> 3)
    raw = rng.integers(0, 256, width * height * per_pixel_x2 // 2, dtype=np.uint8).tobytes()
    b, c, s = settings
    old = _run(f"eq=brightness={b}:contrast={c}:saturation={s},format=rgba", raw, pix_fmt, width, height)
    new = _run(f"{eq_lut.static_filters(b, c, s)},format=rgba", raw, pix_fmt, width, height)
    assert old == new


@needs_eq
def test_a_random_sweep_of_settings_gives_the_bytes_of_eq_even_when_they_change_every_frame(tmp_path):
    """Les réglages changent à chaque image (``sendcmd``) : la sortie de ``lut`` égale celle d'``eq`` image par image, sur les trois plans."""
    np = pytest.importorskip("numpy")
    rng = random.Random(7)
    width, height = 512, 4
    frame = (np.tile(np.arange(width) % 256, (height, 1)).astype(np.uint8).tobytes()
             + np.tile(np.arange(256), (height // 2, 1)).astype(np.uint8).tobytes()
             + np.tile(255 - np.arange(256), (height // 2, 1)).astype(np.uint8).tobytes())
    count = 240
    settings = [(round(rng.uniform(-1, 1), 3), round(rng.uniform(0, 2), 3), round(rng.uniform(0, 3), 3)) for _ in range(count)]
    settings[:4] = [(0.0, 1.0, 1.0), (0.2, 1.0, 1.0), (0.0, 1.3, 1.0), (0.0, 1.0, 0.5)]
    eq_lines, lut_lines = [], []
    for index, (b, c, s) in enumerate(settings):
        instant = max(0.0, (index - 0.5) / 30)
        eq_lines.append(f"{instant:.6f} eq brightness {b}, eq contrast {c}, eq saturation {s};")
        lut_lines.append(f"{instant:.6f} " + ", ".join(eq_lut.commands("lut", eq_lut.eq_planes(b, c, s), None)) + ";")
    (tmp_path / "eq.cmd").write_text("\n".join(eq_lines) + "\n")
    (tmp_path / "lut.cmd").write_text("\n".join(lut_lines) + "\n")
    first = settings[0]
    graph = (f"[0:v]split[a][b];[a]sendcmd=f={tmp_path}/eq.cmd,eq=eval=frame:brightness={first[0]}:contrast={first[1]}:saturation={first[2]}[o1];"
             f"[b]sendcmd=f={tmp_path}/lut.cmd,{eq_lut.FORMAT_FILTER},lut={eq_lut.lut_options(eq_lut.eq_planes(*first))}[o2]")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", f"{width}x{height}", "-r", "30", "-i", "-",
                    "-filter_complex", graph, "-map", "[o1]", "-f", "rawvideo", str(tmp_path / "1.yuv"),
                    "-map", "[o2]", "-f", "rawvideo", str(tmp_path / "2.yuv")], input=frame * count, check=True)
    expected, got = (np.fromfile(tmp_path / name, np.uint8) for name in ("1.yuv", "2.yuv"))
    assert len(expected) == len(got) == len(frame) * count
    assert np.array_equal(expected, got)
    assert not np.array_equal(got[:len(frame)], got[-len(frame):]), "les réglages doivent réellement changer l'image"


@needs_ffmpeg
def test_the_animated_stage_of_an_effect_renders_the_static_frames(tmp_path):
    """Bout en bout, sans ``eq`` : l'image ``n`` du rendu animé (``lut`` + ``sendcmd``) égale le rendu fixe réglé sur la valeur de la courbe."""
    np = pytest.importorskip("numpy")
    from core.effect_sendcmd import command_files
    from core.export_engine import _build_clip_effect_filters

    effect = _effect(saturation=1.2)
    animation = _ramp("brightness", -0.3, 0.4, 2.0) + _ramp("contrast", 0.6, 1.8, 2.0)
    width, height, rate, count = 64, 48, 10, 21
    rng = np.random.default_rng(5)
    raw = rng.integers(0, 256, width * height * 3 // 2, dtype=np.uint8).tobytes()
    written: list[str] = []
    with command_files(written):
        animated = _build_clip_effect_filters((effect,), animation=tuple(animation), sendcmd=context_for(rate, 0.0, 2.0), label="v0fx")
    frames = subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", f"{width}x{height}", "-framerate", str(rate), "-i", "-",
         "-vf", animated, "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"], input=raw * count, capture_output=True, check=True).stdout
    size = width * height * 3 // 2
    from core.effect_targets import evaluated_effects

    assert len(frames) == size * count and written
    for index in (0, 3, 10, 17, 20):
        fixed = evaluated_effects((effect,), animation, index / rate)
        static = _build_clip_effect_filters(fixed)
        expected = subprocess.run(
            ["ffmpeg", "-v", "error", "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", f"{width}x{height}", "-i", "-", "-vf", static,
             "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"], input=raw, capture_output=True, check=True).stdout
        worst = int(np.abs(np.frombuffer(frames[index * size:(index + 1) * size], np.uint8).astype(int)
                           - np.frombuffer(expected, np.uint8).astype(int)).max())
        assert worst <= 1, (index, worst)


def test_eq_is_not_a_filter_of_the_export_any_more():
    effect = _effect(brightness=0.3, contrast=1.2, saturation=0.8)
    from core.export_engine import _build_clip_effect_filters

    assert "eq=" not in _build_clip_effect_filters((effect,))
