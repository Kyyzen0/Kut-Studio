"""Étalonnage dans le moniteur GPU : LUT 3D cuite par la chaîne de l'export, passe ``grade``, cache de cuisson.

Le rendu réel du GPU est vérifié par ``tools/gpu/selfcheck.py`` (cas ``grade``, tests matériels optionnels) ; ici,
sans GPU : la LUT cuite contre l'export réel (FFmpeg), le plan de passes, la référence numpy et le cache.
"""

from __future__ import annotations

import hashlib
import math
import subprocess
import threading

import numpy as np
import pytest
from render_probe import needs_ffmpeg, render_frame

from core.color_grading import ColorCurve, ColorCurves, ColorGrade, LUTResource
from core.gpu_composite import (
    AdjustmentLayer,
    CompositeFrame,
    CompositeLayer,
    VideoSource,
    plan_frame,
    reference_frame,
)
from core.gpu_effects import program_for
from core.gpu_frames import yuv_to_rgb_matrix
from core.gpu_grade import (
    CODE_STEP,
    DOMAIN_RGB,
    DOMAIN_YUV,
    LUT_SIZE,
    GradeBakeError,
    GradeLutCache,
    atlas_array,
    bake_command,
    bake_grade_lut,
    grade_is_active,
    lattice,
    lut_key,
    sample_atlas,
)
from core.project_model import Clip, MediaAsset, Project, Track

W, H = 320, 180


def _cube(path) -> LUTResource:
    """LUT ``.cube`` 17³ « teal & orange » : rouges relevés dans les hautes lumières, bleus dans les ombres."""
    n = 17
    lines = ['TITLE "probe"', f"LUT_3D_SIZE {n}"]
    for b in range(n):
        for g in range(n):
            for r in range(n):
                red, green, blue = r / (n - 1), g / (n - 1), b / (n - 1)
                light = 0.3 * red + 0.59 * green + 0.11 * blue
                lines.append(f"{min(1.0, red * 1.1 + 0.05 * (light > 0.5)):.6f} {green * 0.95:.6f} "
                             f"{min(1.0, blue * 0.9 + 0.1 * (light < 0.4)):.6f}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return LUTResource(path=path.name, title="probe", sha1=hashlib.sha1(path.read_bytes()).hexdigest(),
                       size=path.stat().st_size, source_path=str(path))


def _grade(tmp_path, *, balance: bool = True) -> ColorGrade:
    """Exposition, contraste, saturation, courbe en S, LUT ``.cube`` ; ``balance`` : température et ombres en plus
    (le filtre ``colorbalance`` de l'export)."""
    identity = ColorCurve.identity()
    s_curve = ColorCurve(points=tuple((x, x - 0.06 * math.sin(2 * math.pi * x)) for x, _y in identity.points))
    curves = ColorCurves(master=s_curve)
    return ColorGrade(exposure=0.3, contrast=0.2, saturation=1.3, temperature=25.0 if balance else 0.0,
                      shadows=0.1 if balance else 0.0, curves=curves, lut=_cube(tmp_path / "look.cube"))


# --- réseau, commande, clé ------------------------------------------------------------------------------------------


def test_the_lattice_nodes_are_whole_8_bit_codes_in_the_atlas_layout():
    assert CODE_STEP * (LUT_SIZE - 1) == 255
    n = LUT_SIZE
    planes = np.frombuffer(lattice(DOMAIN_YUV), np.uint8).reshape(3, n, n * n)
    y, u, v = planes
    assert set(np.unique(planes)) == set(range(0, 256, CODE_STEP))
    # pixel (x = c2·N + c1, y = c0)
    assert (y[7, 0], u[0, 3 * n + 5], v[0, 3 * n + 5]) == (7 * CODE_STEP, 5 * CODE_STEP, 3 * CODE_STEP)
    rgba = np.frombuffer(lattice(DOMAIN_RGB), np.uint8).reshape(n, n * n, 4)
    assert tuple(rgba[7, 3 * n + 5]) == (7 * CODE_STEP, 5 * CODE_STEP, 3 * CODE_STEP, 255)


def test_the_bake_command_carries_the_colour_properties_of_the_media():
    command = bake_command("ffmpeg", "eq=gamma=2", domain=DOMAIN_YUV, colorspace="bt709", color_range="full")
    graph = command[command.index("-vf") + 1]
    assert graph == "setparams=colorspace=bt709:range=pc,eq=gamma=2,format=rgba"
    assert command[command.index("-pix_fmt") + 1] == "yuv444p"
    untagged = bake_command("ffmpeg", "eq=gamma=2", domain=DOMAIN_YUV)
    assert untagged[untagged.index("-vf") + 1].startswith("setparams=range=tv,")
    rgb = bake_command("ffmpeg", "eq=gamma=2", domain=DOMAIN_RGB)
    assert rgb[rgb.index("-vf") + 1] == "eq=gamma=2,format=rgba", "les conversions de l'export, choisies par FFmpeg"
    assert rgb[rgb.index("-pix_fmt") + 1] == "rgba" and rgb[-2:] == ["rgb24", "pipe:1"]


def test_the_key_follows_the_grade_the_lut_file_and_the_colour_space(tmp_path):
    grade = _grade(tmp_path)
    key = lut_key(grade)
    assert key == lut_key(grade.with_field("exposure", 0.3)), "même étalonnage, même fichier : même LUT"
    assert key != lut_key(grade.with_field("exposure", 0.4))
    assert key != lut_key(grade, colorspace="bt709") != lut_key(grade, domain=DOMAIN_RGB)
    path = tmp_path / "look.cube"
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")   # même chemin, autre contenu
    assert lut_key(grade) != key
    assert grade_is_active(grade) and not grade_is_active(None)
    assert not grade_is_active(ColorGrade()) and not grade_is_active(grade.with_enabled(False))


# --- la LUT contre l'export réel ------------------------------------------------------------------------------------


def _clip_project(media: str, grade) -> Project:
    project = Project("p", width=W, height=H, fps=25.0, media_assets=[MediaAsset("v", media, "v", 2.0, W, H, 25.0,
                                                                                  "video")],
                      tracks=[Track("V1", "V1", "video", clips=[Clip("c", "v", "V1", 0.0, 0.0, 2.0)])])
    project.tracks[0].clips[0].color_grade = grade
    return project


def _flat_chroma(yuv) -> np.ndarray:
    """Pixels dont la chroma ne change pas brusquement autour (l'export la sous-échantillonne, le GPU non)."""
    flat = np.ones(yuv.shape[:2], bool)
    for axis in (0, 1):
        jump = np.abs(np.diff(yuv[..., 1:], axis=axis)).max(-1) >= 6
        before, after = [(0, 0), (0, 0)], [(0, 0), (0, 0)]
        before[axis], after[axis] = (0, 1), (1, 0)
        flat &= ~np.pad(jump, before) & ~np.pad(jump, after)
    return flat


@needs_ffmpeg
@pytest.mark.parametrize("tags", [["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"], []],
                         ids=["bt709", "sans-etiquette"])
def test_the_baked_lut_is_what_the_export_renders(tmp_path, tags):
    media = tmp_path / "source.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=s={W}x{H}:r=25:d=2",
                    "-c:v", "libx264", "-crf", "4", "-pix_fmt", "yuv420p", *tags, str(media)], check=True)
    grade = _grade(tmp_path)
    exported = render_frame(_plan(media, grade), W, H, 0.4).astype(float) / 255.0
    plain = render_frame(_plan(media, None), W, H, 0.4).astype(float) / 255.0
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", "0.4", "-i", str(media), "-frames:v", "1", "-f", "rawvideo",
                          "-pix_fmt", "yuv444p", "-"], capture_output=True, check=True).stdout
    yuv = np.frombuffer(raw, np.uint8).reshape(3, H, W).transpose(1, 2, 0).astype(float)

    colorspace = "bt709" if tags else ""
    atlas = atlas_array(bake_grade_lut(grade, colorspace=colorspace, color_range="video"))
    shown = sample_atlas(atlas, yuv / 255.0)
    flat = _flat_chroma(yuv)
    error = np.abs(shown - exported)[flat] * 255
    assert flat.mean() > 0.6
    # Mire aux couleurs saturées : là où l'étalonnage écrête un canal (bord du gamut), l'interpolation entre deux
    # nœuds de la LUT adoucit le coude de quelques niveaux (3,3 à 4,0 au 99ᵉ centile). En moyenne, l'écart vient surtout
    # de la chroma 4:2:0, que l'export convertit en RVB à la manière de la plateforme et la référence en 4:4:4 :
    # mesuré 0,08 à 0,18 (FFmpeg 7.1 et 9, arm64), 0,32 (FFmpeg 6.1, x86, CI). Jusqu'au 2026-10-09, un demi-pixel de flou
    # sur tout plan fixe (``rotate``) lissait cet écart.
    assert error.mean() < 0.4 and np.percentile(error, 99) < 5.0, (error.mean(), np.percentile(error, 99))
    # ≈ 10 niveaux sur cette mire déjà saturée (111 quand ``colorbalance`` avait ``pl=1`` : presque tout était du gris).
    assert np.abs(plain - exported).mean() * 255 > 5, "l'étalonnage change vraiment l'image"


def _plan(media, grade):
    from core.render_plan import build_render_plan

    return build_render_plan(_clip_project(str(media), grade))


@needs_ffmpeg
def test_a_grade_on_an_effects_layer_is_baked_in_rgb_like_the_export_applies_it(tmp_path):
    """Calque d'effets étalonné : l'export étalonne la composition RVB en dessous ; la LUT est cuite en RVB.

    Étalonnage complet, ``colorbalance`` compris : depuis qu'il n'a plus ``pl=1`` (qui grisait un pixel dont un canal
    touchait 0 ou 255, une cassure que la LUT ne pouvait qu'adoucir), la fonction est continue.
    """
    from core.graphics import add_graphic_clip
    from core.render_plan import build_render_plan

    media = tmp_path / "flat.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=s={W}x{H}:r=25:d=2",
                    "-vf", "gblur=sigma=6", "-c:v", "libx264", "-crf", "2", "-pix_fmt", "yuv444p", str(media)],
                   check=True)
    project = _clip_project(str(media), None)
    plain = render_frame(build_render_plan(project), W, H, 0.4).astype(float) / 255.0
    adjustment = add_graphic_clip(project, "adjustment", timeline_start=0.0, duration=2.0)
    grade = _grade(tmp_path)
    adjustment.color_grade = grade
    exported = render_frame(build_render_plan(project), W, H, 0.4).astype(float) / 255.0
    shown = sample_atlas(atlas_array(bake_grade_lut(grade, domain=DOMAIN_RGB)), plain)
    # La sortie de l'export repasse en 4:2:0 après la composition : on compare là où la chroma est régulière.
    m = np.linalg.inv(np.array(yuv_to_rgb_matrix())[:3, :3])
    flat = _flat_chroma((plain @ m.T) * 255)
    error = np.abs(shown - exported)[flat] * 255
    assert flat.mean() > 0.6
    assert error.mean() < 0.5 and np.percentile(error, 99) < 3.0, (error.mean(), np.percentile(error, 99))
    assert np.abs(plain - exported).mean() * 255 > 10, "l'étalonnage change vraiment l'image"


def test_a_failed_bake_is_an_error_not_a_crash(tmp_path):
    grade = _grade(tmp_path)
    with pytest.raises(GradeBakeError):
        bake_grade_lut(grade, ffmpeg=str(tmp_path / "pas-de-ffmpeg"))
    with pytest.raises(GradeBakeError):
        bake_grade_lut(ColorGrade(enabled=False))


# --- passes et référence --------------------------------------------------------------------------------------------


def _frame(*, grade_lut: str = "", effects=(), adjustments=()) -> CompositeFrame:
    layer = CompositeLayer("v", (1.0, 0.0, 0.0, 1.0, 0.0, 0.0), (0, 0, 64, 36), program=program_for(list(effects)),
                           grade_lut=grade_lut)
    return CompositeFrame(64, 36, 1.0, (layer,), (VideoSource("v", "yuv420p", 64, 36),), adjustments=adjustments)


def test_the_grade_pass_sits_between_the_effects_and_the_composite():
    assert [step.shader for step in plan_frame(_frame()).passes] == ["clear", "prep", "composite"]
    plan = plan_frame(_frame(grade_lut="lut:k"))
    assert [step.shader for step in plan.passes] == ["clear", "prep", "grade", "composite"]
    prep, grade, composite = plan.passes[1:]
    assert grade.inputs == (prep.target, "matte:lut:k") and composite.inputs[0] == grade.target != prep.target
    assert grade.target in plan.textures
    adjusted = plan_frame(_frame(adjustments=(AdjustmentLayer(program_for(()), "cov", grade_lut="lut:a"),)))
    assert [step.shader for step in adjusted.passes][-3:] == ["prep", "grade", "composite"], \
        "un calque d'effets sans effet mais étalonné a sa passe"
    assert [step.shader for step in plan_frame(_frame(adjustments=(AdjustmentLayer(program_for(())),))).passes] \
        == ["clear", "prep", "composite"]


def test_the_reference_reads_the_atlas_at_the_lattice_addresses():
    """Une LUT qui fait la conversion YUV → RVB elle-même (linéaire : la trilinéaire est exacte) : même image que
    sans étalonnage. Une erreur d'adressage (tranche, ligne, colonne) la ferait diverger."""
    n = LUT_SIZE
    codes = np.arange(n) * CODE_STEP / 255.0
    c0, c2, c1 = np.meshgrid(codes, codes, codes, indexing="ij")
    m = np.array(yuv_to_rgb_matrix())
    rgb = np.stack((c0, c1, c2), axis=-1) @ m[:3, :3].T + m[:3, 3]
    atlas = rgb.reshape(n, n * n, 3)                                    # non borné : pas d'écrêtage dans la LUT
    rng = np.random.default_rng(3)
    codes_yuv = np.stack((rng.uniform(16, 235, (36, 64)), rng.uniform(16, 240, (36, 64)),
                          rng.uniform(16, 240, (36, 64))), axis=-1) / 255.0
    plain = reference_frame(_frame(), {"v": codes_yuv})
    graded = reference_frame(_frame(grade_lut="lut:k"), {"v": codes_yuv}, luts={"lut:k": atlas})
    assert np.abs(np.clip(graded, 0, 1) - plain).max() < 1e-6
    with_sepia = reference_frame(_frame(grade_lut="lut:k", effects=[_sepia()]), {"v": codes_yuv},
                                 luts={"lut:k": atlas})
    assert np.abs(np.clip(with_sepia, 0, 1) - reference_frame(_frame(effects=[_sepia()]), {"v": codes_yuv})).max() \
        < 2e-3, "après la sépia (calque en RVB), la passe repasse en YUV pour lire la LUT"


def test_before_after_leaves_the_left_part_of_the_layer_ungraded():
    """Comparaison avant / après (page Couleur) : à gauche de la part demandée, la couleur d'origine en RVB ; à droite,
    l'étalonnage. Une LUT qui inverse les couleurs rend la frontière évidente."""
    from dataclasses import replace

    n = LUT_SIZE
    codes = np.arange(n) * CODE_STEP / 255.0
    c0, c2, c1 = np.meshgrid(codes, codes, codes, indexing="ij")
    m = np.array(yuv_to_rgb_matrix())
    inverted = 1.0 - np.clip(np.stack((c0, c1, c2), axis=-1) @ m[:3, :3].T + m[:3, 3], 0, 1)
    atlas = inverted.reshape(n, n * n, 3)
    yuv = np.dstack((np.full((36, 64), 0.6), np.full((36, 64), 0.4), np.full((36, 64), 0.55)))
    plain = reference_frame(_frame(), {"v": yuv})
    whole = reference_frame(_frame(grade_lut="lut:k"), {"v": yuv}, luts={"lut:k": atlas})
    frame = _frame(grade_lut="lut:k")
    split = replace(frame, layers=(replace(frame.layers[0], grade_split=0.25),))
    compared = reference_frame(split, {"v": yuv}, luts={"lut:k": atlas})
    assert np.abs(compared[:, :15] - plain[:, :15]).max() < 1e-6, "avant : sans étalonnage"
    assert np.abs(compared[:, 17:] - whole[:, 17:]).max() < 1e-6, "après : étalonné"
    grade = plan_frame(split).passes[2]
    assert grade.shader == "grade" and grade != plan_frame(frame).passes[2], "la part passe au shader (misc.z)"


def test_before_after_follows_the_viewer_line_for_a_moved_layer():
    """Calque décalé de 20 px vers la droite, partage à la moitié du cadre (x = 32) : le trait du viewer, pas la
    moitié du calque (qui tomberait en x = 52)."""
    from dataclasses import replace

    n = LUT_SIZE
    codes = np.arange(n) * CODE_STEP / 255.0
    c0, c2, c1 = np.meshgrid(codes, codes, codes, indexing="ij")
    m = np.array(yuv_to_rgb_matrix())
    atlas = (1.0 - np.clip(np.stack((c0, c1, c2), axis=-1) @ m[:3, :3].T + m[:3, 3], 0, 1)).reshape(n, n * n, 3)
    yuv = np.dstack((np.full((36, 64), 0.6), np.full((36, 64), 0.4), np.full((36, 64), 0.55)))
    base = _frame(grade_lut="lut:k")
    moved = replace(base, layers=(replace(base.layers[0], matrix=(1.0, 0.0, 0.0, 1.0, 20.0, 0.0)),))
    plain = reference_frame(replace(moved, layers=(replace(moved.layers[0], grade_lut=""),)), {"v": yuv})
    whole = reference_frame(moved, {"v": yuv}, luts={"lut:k": atlas})
    split = reference_frame(replace(moved, layers=(replace(moved.layers[0], grade_split=0.5),)), {"v": yuv},
                            luts={"lut:k": atlas})
    assert np.abs(split[:, 22:31] - plain[:, 22:31]).max() < 1e-6, "avant le trait : sans étalonnage"
    assert np.abs(split[:, 33:62] - whole[:, 33:62]).max() < 1e-6, "après le trait : étalonné"


def _sepia():
    from core.effects_model import ClipEffect, EffectType

    return ClipEffect("s", EffectType.SEPIA, True, {})


# --- cache de cuisson -----------------------------------------------------------------------------------------------


class _FakeBaker:
    def __init__(self, *, fail: bool = False):
        self.calls: list[float] = []
        self.release = threading.Event()
        self.fail = fail

    def __call__(self, grade, **_options):
        self.calls.append(grade.exposure)
        self.release.wait(5)
        if self.fail:
            raise GradeBakeError("LUT illisible")
        return bytes([int(grade.exposure * 100)])


def _wait(condition, timeout=5.0):
    event = threading.Event()
    for _ in range(int(timeout / 0.01)):
        if condition():
            return True
        event.wait(0.01)
    return condition()


def test_the_cache_bakes_off_the_caller_thread_and_only_the_last_waiting_request():
    baker, ready = _FakeBaker(), threading.Event()
    cache = GradeLutCache(on_ready=ready.set, bake=baker)
    grades = [ColorGrade(exposure=value) for value in (0.1, 0.2, 0.3, 0.4)]
    assert cache.lookup(grades[0]) is None, "pas encore cuite : le moniteur passe sans, sans attendre"
    assert _wait(lambda: baker.calls == [0.1])
    for grade in grades[1:]:                                    # un curseur qu'on glisse pendant la cuisson
        assert cache.lookup(grade) is None
    baker.release.set()
    assert _wait(lambda: cache.lookup(grades[3]) is not None)
    assert baker.calls == [0.1, 0.4], "les valeurs intermédiaires ne sont jamais cuites"
    assert ready.is_set()
    key, atlas = cache.lookup(grades[3])
    assert key == lut_key(grades[3]) and atlas == bytes([40])


def test_a_failed_bake_is_remembered_and_never_retried_on_every_frame():
    baker = _FakeBaker(fail=True)
    baker.release.set()
    cache = GradeLutCache(bake=baker)
    grade = ColorGrade(exposure=0.5)
    assert cache.lookup(grade) is None
    assert _wait(lambda: cache.failed(grade))
    for _ in range(5):
        assert cache.lookup(grade) is None
    assert baker.calls == [0.5]
