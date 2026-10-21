"""Moniteur et étalonnage animé (ADR-0008, étape 7) : le moniteur montre l'étalonnage que l'**export** applique à l'instant affiché.

Sans FFmpeg : la valeur donnée au moniteur (``MainWindow._monitor_color_grade(clip, temps local)``), le clip sans image clé qui garde le
même objet (donc la même LUT, jamais recuite), l'invalidation de la clé de LUT, l'équité du cache entre plusieurs clips animés.
Avec FFmpeg réel (``needs_ffmpeg``) : l'oracle numpy. L'étalonnage évalué à l'instant ``t`` est cuit en LUT (la chaîne de l'export), lue
par la référence du moniteur (``reference_frame``, chemin CPU), et comparée à l'image que l'export rend au même instant. Le viewer
QtMultimedia est noir en capture hors écran : on prouve par ce calcul, pas par une capture.
"""

from __future__ import annotations

import subprocess
import threading
from types import SimpleNamespace

import numpy as np
import pytest
from render_probe import needs_ffmpeg, render_frame

from core.animation import InterpolationType, Keyframe
from core.color_grading import ColorGrade
from core.color_nodes import as_graph
from core.gpu_composite import (
    CompositeFrame,
    CompositeLayer,
    VideoSource,
    reference_frame,
)
from core.gpu_effects import program_for
from core.gpu_grade import (
    GradeLutCache,
    atlas_array,
    bake_grade_lut,
    grade_is_active,
    lut_key,
)
from core.grade_targets import effective_grade, has_live_grade_animation
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import build_render_plan
from ui.main_window_mixins.color_page import ColorPageMixin

W, H = 320, 180
LINEAR = InterpolationType.LINEAR


def kf(prop, time, value):
    return Keyframe(prop, time, value, LINEAR)


def monitor_grade(clip, local_time=None):
    """``MainWindow._monitor_color_grade`` hors fenêtre : aucun panneau Couleur visible, donc ni sélection montrée ni avant / après."""
    stub = SimpleNamespace(_color_highlight=False, color_panel=None)
    return ColorPageMixin._monitor_color_grade(stub, clip, local_time)


def animated_clip(grade=None) -> Clip:
    clip = Clip(
        id="c",
        asset_id="a",
        track_id="V1",
        timeline_start=0.0,
        source_in=0.0,
        source_out=2.0,
    )
    clip.color_grade = grade
    clip.animation = [
        kf("color.exposure", 0.0, -0.5),
        kf("color.exposure", 2.0, 0.8),
        kf("color.contrast", 0.0, 0.0),
        kf("color.contrast", 2.0, 0.4),
        kf("color.temperature", 0.0, -30.0),
        kf("color.temperature", 2.0, 40.0),
        kf("color.shadows", 0.0, -0.2),
        kf("color.shadows", 2.0, 0.3),
        kf("color.hue", 0.0, 0.0),
        kf("color.hue", 2.0, 25.0),
        kf("color.lift.r", 0.0, 0.0),
        kf("color.lift.r", 2.0, 0.08),
    ]
    return clip


# --- la valeur donnée au moniteur -------------------------------------------------------------------------------------------


def test_the_monitor_gets_the_grade_of_the_export_at_the_displayed_instant():
    clip = animated_clip(ColorGrade(saturation=1.2))
    for t in (0.0, 0.4, 1.0, 1.6, 2.0):
        assert monitor_grade(clip, t) == effective_grade(clip, t)
    assert monitor_grade(clip, 0.4) != monitor_grade(clip, 1.6), (
        "le curseur déplacé change l'étalonnage montré"
    )
    assert monitor_grade(clip, 1.0).saturation == 1.2, (
        "ce qui n'est pas animé garde sa valeur de base"
    )
    assert monitor_grade(clip) is clip.color_grade, (
        "sans instant : le réglage de base, comme avant l'étape 7"
    )


def test_a_clip_without_a_grade_key_keeps_the_very_same_object_so_the_lut_is_never_rebaked(
    monkeypatch,
):
    """Pas de recuisson par image : sans image clé ``color.*``, ni objet neuf ni courbe construite (``GradeCurves`` n'est même pas
    appelé), donc la clé de LUT ne bouge pas d'une image à l'autre."""
    import core.grade_targets as targets

    def forbidden(*_args, **_kwargs):
        raise AssertionError(
            "GradeCurves ne doit pas être construit pour un clip sans image clé d'étalonnage"
        )

    monkeypatch.setattr(targets, "GradeCurves", forbidden)
    grade = ColorGrade(exposure=0.4)
    for value in (None, grade, grade.with_enabled(False), as_graph(grade)):
        clip = Clip(
            id="c",
            asset_id="a",
            track_id="V1",
            timeline_start=0.0,
            source_in=0.0,
            source_out=2.0,
        )
        clip.color_grade = value
        clip.animation = [
            kf("transform.position_x", 0.0, 0.0),
            kf("transform.position_x", 1.0, 1.0),
        ]
        assert not has_live_grade_animation(clip)
        for t in (0.0, 0.5, 1.0):
            assert monitor_grade(clip, t) is value


def test_a_static_grade_is_baked_once_however_many_frames_are_shown():
    bakes = []
    release = threading.Event()

    def bake(grade, **_options):
        bakes.append(grade)
        release.wait(5)
        return b"\0"

    cache = GradeLutCache(bake=bake)
    clip = Clip(
        id="c",
        asset_id="a",
        track_id="V1",
        timeline_start=0.0,
        source_in=0.0,
        source_out=2.0,
    )
    clip.color_grade = ColorGrade(exposure=0.4)
    for index in range(100):
        cache.lookup(monitor_grade(clip, index / 25), slot="s")
    release.set()
    assert _wait(lambda: cache.lookup(clip.color_grade, slot="s") is not None)
    for index in range(100):
        assert cache.lookup(monitor_grade(clip, index / 25), slot="s") is not None
    assert len(bakes) == 1


def test_the_lut_key_follows_the_instant_and_every_color_key_and_nothing_leaks_between_clips():
    clip = animated_clip()
    other = animated_clip()
    other.id = "d"
    key = lambda c, t: lut_key(monitor_grade(c, t), colorspace="bt709")  # noqa: E731
    assert key(clip, 0.4) != key(clip, 0.44), "un autre instant : une autre LUT"
    assert key(clip, 0.4) == key(other, 0.4), (
        "même courbe, même instant : même LUT (la clé suit le contenu, pas le clip)"
    )
    before = key(clip, 1.0)
    clip.animation[1] = kf(
        "color.exposure", 2.0, 0.0
    )  # une image clé de la courbe d'exposition est modifiée
    assert key(clip, 1.0) != before, "une clé ``color.*`` modifiée invalide la LUT"
    assert key(other, 1.0) == before, "l'autre clip garde la sienne"
    clip.animation[1] = kf("color.exposure", 2.0, 0.8)
    assert key(clip, 1.0) == before, "la valeur restaurée retrouve la LUT déjà cuite"
    clip.animation.append(kf("color.saturation", 0.5, 1.7))
    assert key(clip, 1.0) != before, "une courbe de plus change l'étalonnage montré"


def test_an_animated_grade_stays_bounded_and_finite():
    clip = animated_clip(ColorGrade(exposure=0.1))
    clip.animation += [
        kf("color.saturation", 0.0, float("nan")),
        kf("color.saturation", 1.0, float("inf")),
        kf("color.gain.g", 0.0, 9.0),
        kf("color.gain.g", 2.0, -9.0),
        kf("color.hue", 3.0, 1e308),
    ]
    for t in (-1.0, 0.0, 0.7, 2.0, 1e6, float(2**40)):
        grade = monitor_grade(clip, t)
        values = [
            grade.exposure,
            grade.contrast,
            grade.saturation,
            grade.temperature,
            grade.hue,
            grade.shadows,
            grade.highlights,
            *(
                getattr(getattr(grade, wheel), channel)
                for wheel in ("lift", "gamma", "gain", "offset")
                for channel in "rgby"
            ),
        ]
        assert all(np.isfinite(values)), t
        assert -1.0 <= grade.gain.g <= 1.0 and -180.0 <= grade.hue <= 180.0
        assert "nan" not in lut_key(grade) and grade_is_active(grade) in (True, False)


def test_a_disabled_or_graph_grade_is_not_animated_by_the_monitor():
    clip = animated_clip(ColorGrade(exposure=0.2, enabled=False))
    assert not monitor_grade(clip, 1.0).enabled and not grade_is_active(
        monitor_grade(clip, 1.0)
    )
    graph = as_graph(ColorGrade(exposure=0.2))
    clip = animated_clip(graph)
    assert monitor_grade(clip, 1.0) is graph, (
        "un graphe de nœuds ne s'anime pas (étape 6) : rendu tel quel"
    )


def test_many_animated_clips_are_each_evaluated_at_their_own_local_time():
    clips = []
    for number in range(40):
        clip = animated_clip()
        clip.id, clip.timeline_start = f"c{number}", number * 0.5
        clips.append(clip)
    playhead = 7.3
    active = [
        c for c in clips if 0.0 <= playhead - c.timeline_start < 2.0
    ]  # les clips que le curseur traverse
    keys = {lut_key(monitor_grade(c, playhead - c.timeline_start)) for c in active}
    assert len(active) == 4 and len(keys) == 4, (
        "chaque clip a l'étalonnage de son propre instant local"
    )


# --- cache de cuisson : plusieurs clips animés ------------------------------------------------------------------------------


def _wait(condition, timeout=5.0):
    event = threading.Event()
    for _ in range(int(timeout / 0.01)):
        if condition():
            return True
        event.wait(0.01)
    return condition()


def test_two_animated_layers_both_get_fresh_luts_during_playback():
    """Chaque image demande une nouvelle LUT pour chaque clip animé, plus vite qu'une cuisson ne dure : sans équité, le dernier
    demandé était servi à chaque fois et l'autre restait figé. Avec la règle « le moins récemment servi d'abord », les deux avancent."""
    baked: list[tuple[str, float]] = []
    gate = threading.Semaphore(0)

    def bake(grade, **options):
        gate.acquire(timeout=5)
        baked.append((options["colorspace"], grade.exposure))
        return b"x"

    cache = GradeLutCache(bake=bake)
    for tick in range(1, 9):  # 8 images de lecture, deux clips (deux emplacements)
        cache.lookup(ColorGrade(exposure=tick / 10), colorspace="A", slot="A")
        cache.lookup(ColorGrade(exposure=-tick / 10), colorspace="B", slot="B")
        gate.release()  # une cuisson se termine par image
        assert _wait(lambda n=tick: len(baked) >= n)
    gate.release(2)
    assert _wait(lambda: len(baked) >= 9)
    served = [name for name, _ in baked]
    assert served.count("A") >= 3 and served.count("B") >= 3, served
    assert all(a != b for a, b in zip(served, served[1:], strict=False)), (
        "ils alternent : " + "".join(served)
    )


def test_one_slot_keeps_the_last_request_first_like_a_dragged_slider():
    calls: list[float] = []
    release = threading.Event()

    def bake(grade, **_options):
        calls.append(grade.exposure)
        release.wait(5)
        return b"x"

    cache = GradeLutCache(bake=bake)
    cache.lookup(ColorGrade(exposure=0.1))
    assert _wait(lambda: calls == [0.1])
    for value in (0.2, 0.3, 0.4):
        cache.lookup(ColorGrade(exposure=value))
    release.set()
    assert _wait(lambda: cache.lookup(ColorGrade(exposure=0.4)) is not None)
    assert calls == [0.1, 0.4]


def test_workers_bound_the_simultaneous_bakes_and_the_default_is_one():
    running, peak, lock = [0], [0], threading.Lock()
    release = threading.Event()

    def bake(_grade, **_options):
        with lock:
            running[0] += 1
            peak[0] = max(peak[0], running[0])
        release.wait(5)
        with lock:
            running[0] -= 1
        return b"x"

    for workers, expected in ((1, 1), (2, 2)):
        peak[0] = 0
        release.clear()
        cache = GradeLutCache(bake=bake, workers=workers)
        for number in range(5):
            cache.lookup(ColorGrade(exposure=number / 10), slot=f"s{number}")
        assert _wait(lambda: peak[0] >= expected)
        threading.Event().wait(0.1)
        assert peak[0] == expected
        release.set()
        assert _wait(lambda: cache.latest("s4") is not None)
    assert GradeLutCache()._workers == 1


def test_latest_is_the_newest_successful_bake_of_the_slot_only():
    release = threading.Event()
    release.set()
    cache = GradeLutCache(bake=lambda grade, **_o: bytes([int(grade.exposure * 100)]))
    assert cache.latest("a") is None
    first, second = ColorGrade(exposure=0.1), ColorGrade(exposure=0.2)
    cache.lookup(first, slot="a")
    assert _wait(lambda: cache.latest("a") is not None)
    assert (
        cache.latest("a") == (lut_key(first), bytes([10])) and cache.latest("b") is None
    )
    cache.lookup(second, slot="a")
    assert _wait(lambda: cache.latest("a") == (lut_key(second), bytes([20])))
    failing = GradeLutCache(
        bake=lambda *_a, **_o: (_ for _ in ()).throw(
            __import__("core.gpu_grade", fromlist=["x"]).GradeBakeError("x")
        )
    )
    failing.lookup(first, slot="a")
    assert _wait(lambda: failing.failed(first))
    assert failing.latest("a") is None, "un échec n'est jamais une « dernière LUT »"


# --- l'oracle : moniteur (référence CPU) contre export -----------------------------------------------------------------------


def _media(tmp_path) -> str:
    path = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=s={W}x{H}:r=25:d=2",
            "-c:v",
            "libx264",
            "-crf",
            "4",
            "-pix_fmt",
            "yuv420p",
            "-colorspace",
            "bt709",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            str(path),
        ],
        check=True,
    )
    return str(path)


def _project(media: str, clip: Clip) -> Project:
    clip.asset_id, clip.track_id = "v", "V1"
    return Project(
        "p",
        width=W,
        height=H,
        fps=25.0,
        media_assets=[MediaAsset("v", media, "v", 2.0, W, H, 25.0, "video")],
        tracks=[Track("V1", "V1", "video", clips=[clip])],
    )


def _flat_chroma(yuv: np.ndarray) -> np.ndarray:
    flat = np.ones(yuv.shape[:2], bool)
    for axis in (0, 1):
        jump = np.abs(np.diff(yuv[..., 1:], axis=axis)).max(-1) >= 6
        before, after = [(0, 0), (0, 0)], [(0, 0), (0, 0)]
        before[axis], after[axis] = (0, 1), (1, 0)
        flat &= ~np.pad(jump, before) & ~np.pad(jump, after)
    return flat


def _monitor_image(grade, yuv: np.ndarray) -> np.ndarray:
    """L'image du moniteur (référence CPU de ``plan_frame``) : LUT cuite par la chaîne de l'export, lue par la passe ``grade`` ;
    ``grade`` inactif (``None``, neutre) : le calque passe sans étalonnage, comme le moniteur."""
    active = grade_is_active(grade)
    atlas = (
        atlas_array(bake_grade_lut(grade, colorspace="bt709", color_range="video"))
        if active
        else None
    )
    layer = CompositeLayer(
        "v",
        (1.0, 0.0, 0.0, 1.0, 0.0, 0.0),
        (0, 0, W, H),
        program=program_for(()),
        grade_lut="lut:k" if active else "",
    )
    frame = CompositeFrame(
        W, H, 1.0, (layer,), (VideoSource("v", "yuv420p", W, H, "bt709", "video"),)
    )
    return np.asarray(
        reference_frame(
            frame, {"v": yuv / 255.0}, luts={"lut:k": atlas} if active else None
        ),
        float,
    )


@needs_ffmpeg
@pytest.mark.parametrize("index", [10, 25, 40], ids=["0.4s", "1.0s", "1.6s"])
def test_the_monitor_image_at_an_instant_matches_the_export_of_the_same_instant(
    tmp_path, index
):
    """Six réglages animés ensemble (expression : contraste, teinte ; ``sendcmd`` : exposition, température, ombres, roue), à l'arrêt.
    Tolérance : l'écart **déjà présent** entre le moniteur et l'export pour un étalonnage statique de cette force (mesuré 0,46 à 0,74
    niveau en moyenne, 2,7 à 2,9 au 99ᵉ centile sur cette mire ; ``test_the_baked_lut_is_what_the_export_renders`` : 0,1 à 0,4 pour un
    étalonnage plus léger) : la chroma 4:2:0, que l'export convertit en RVB à la manière de la plateforme et la référence en 4:4:4, et
    l'interpolation de la LUT au bord du gamut. L'animation n'y ajoute rien : l'image animée de l'export égale celle d'un étalonnage
    statique aux mêmes valeurs (écart mesuré 0). Un étalonnage faux (la valeur de base au lieu de celle de l'instant) s'écarte de plusieurs
    fois cette tolérance."""
    media = _media(tmp_path)
    t = index / 25
    clip = animated_clip()
    project = _project(media, clip)
    exported = render_frame(build_render_plan(project), W, H, t).astype(float) / 255.0
    raw = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            media,
            "-vf",
            f"select=eq(n\\,{index})",
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "yuv444p",
            "-",
        ],
        capture_output=True,
        check=True,
    ).stdout
    yuv = np.frombuffer(raw, np.uint8).reshape(3, H, W).transpose(1, 2, 0).astype(float)
    flat = _flat_chroma(yuv)

    fixed = Clip(
        id="c",
        asset_id="v",
        track_id="V1",
        timeline_start=0.0,
        source_in=0.0,
        source_out=2.0,
    )
    fixed.color_grade = effective_grade(clip, t)
    static = (
        render_frame(build_render_plan(_project(media, fixed)), W, H, t).astype(float)
        / 255.0
    )
    assert np.abs(static - exported).max() * 255 <= 1.0, (
        "l'export animé rend les valeurs de effective_grade à cet instant"
    )

    shown = _monitor_image(monitor_grade(clip, t), yuv)
    error = np.abs(shown - exported)[flat] * 255
    assert flat.mean() > 0.6
    assert error.mean() < 1.0 and np.percentile(error, 99) < 6.0, (
        error.mean(),
        np.percentile(error, 99),
    )

    base = _monitor_image(
        monitor_grade(clip), yuv
    )  # le réglage de base (neutre ici) : l'avant-étape 7
    wrong = np.abs(base - exported)[flat] * 255
    assert wrong.mean() > 3 * error.mean(), (
        "l'oracle distingue l'étalonnage de l'instant du réglage de base"
    )
    other = _monitor_image(monitor_grade(clip, t + 0.4), yuv)  # le mauvais instant
    assert np.abs(other - exported)[flat].mean() * 255 > 1.5 * error.mean()


@needs_ffmpeg
@pytest.mark.parametrize(
    "base", [None, ColorGrade(saturation=1.2)], ids=["sans-etalonnage", "avec-base"]
)
def test_a_before_after_comparison_keeps_the_animated_grade_on_the_compared_side(
    tmp_path, base
):
    """Segment fidèle avec l'avant / après actif (``Compare`` posé par ``apply_grade_overrides``) : la partie étalonnée suit
    ``effective_grade(t)`` à chaque instant (comme l'export), la partie gauche reste sans étalonnage. Sans la transmission des images
    clés, la partie étalonnée restait figée sur le réglage de base."""
    from core.color_render import Compare
    from core.preview_segments import segment_plan

    media = _media(tmp_path)
    clip = animated_clip(base)
    project = _project(media, clip)
    split = 0.5
    shown = Compare(monitor_grade(clip), split)
    plain = Clip(
        id="c",
        asset_id="v",
        track_id="V1",
        timeline_start=0.0,
        source_in=0.0,
        source_out=2.0,
    )
    plain.color_grade = None
    ungraded = _project(media, plain)
    half = W // 2
    gaps = []
    for t in (0.4, 1.0, 1.6):
        compared = render_frame(
            segment_plan(project, 0.0, 2.0, grade_overrides={"c": shown}), W, H, t
        ).astype(float)
        exported = render_frame(build_render_plan(project), W, H, t).astype(float)
        original = render_frame(build_render_plan(ungraded), W, H, t).astype(float)
        right = slice(half + 8, None)
        assert np.abs(compared[:, right] - exported[:, right]).mean() < 1.0, t
        assert np.abs(compared[:, : half - 8] - original[:, : half - 8]).mean() < 1.0, t
        static = Clip(
            id="c",
            asset_id="v",
            track_id="V1",
            timeline_start=0.0,
            source_in=0.0,
            source_out=2.0,
        )
        static.color_grade = base
        frozen = render_frame(
            build_render_plan(_project(media, static)), W, H, t
        ).astype(float)
        gaps.append(np.abs(frozen[:, right] - exported[:, right]).mean())
    assert min(gaps) > 3.0, (
        "le réglage de base seul est loin de l'étalonnage animé : le test distingue l'animation"
    )


def test_a_before_after_comparison_without_animation_is_unchanged():
    from core.color_render import Compare
    from core.export_engine import _build_color_grade_filters

    grade = ColorGrade(exposure=0.3, saturation=1.2)
    assert _build_color_grade_filters(
        Compare(grade, 0.5), tag="t"
    ) == _build_color_grade_filters(
        Compare(grade, 0.5), tag="t", animation=[], sendcmd=None
    )
    assert _build_color_grade_filters(
        Compare(grade, 0.5), tag="t", animation=[kf("transform.position_x", 0.0, 0.0)]
    ) == _build_color_grade_filters(Compare(grade, 0.5), tag="t")
