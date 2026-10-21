"""Aperçu interactif d'un clip « scène 3D » dans le moniteur (lot B3) : image figée, aucun rendu en lecture, cache, échec.

``MographRenderer(scene3d_mode=...)`` : ``exact`` (export, segments fidèles : inchangé), ``interactive`` (à l'arrêt : image réduite
et approximative) et ``frozen`` (en lecture : aucun rendu 3D). Les rendus sont comptés en espionnant ``kut3d.render_frame``.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import numpy as np
import pytest

import kut3d
from core import scene3d, scene3d_interactive
from core.graphics import GraphicType, add_graphic_clip
from core.mograph_raster import MographRenderer, scene_for_plan
from core.render_plan import build_render_plan
from core.scene3d_card import CARD_TEXT, PLACEHOLDER_TEXT, scene3d_card
from tests.scene3d_preview_support import Spy
from tests.scene3d_render_samples import write_animated_scene, write_static_scene
from tests.scene3d_samples import base_project


@pytest.fixture(autouse=True)
def _fresh(qapp):
    scene3d.clear_signature_cache()
    scene3d_interactive.clear_preview_cache()
    scene3d_interactive.set_backend_provider(None)
    yield
    scene3d_interactive.set_backend_provider(None)
    scene3d_interactive.clear_preview_cache()
    scene3d.clear_signature_cache()


def _setup(scene: Path, *, size: tuple[int, int] = (64, 36), duration: float = 3.0):
    project = base_project()
    project.width, project.height = size
    clip = add_graphic_clip(
        project,
        GraphicType.SCENE3D,
        timeline_start=0.0,
        duration=duration,
        source_path=str(scene),
        clip_id="s3d",
    )
    plan = build_render_plan(project)
    return project, clip, scene_for_plan(plan)


def _render(scene, size, mode, t=0.0):
    renderer = MographRenderer(scene, size[0], size[1], fps=25.0, scene3d_mode=mode)
    return renderer.render(scene.top_level(), t)


def _opaque_pixels(image) -> int:
    return int(
        sum(
            1
            for y in range(image.height())
            for x in range(image.width())
            if image.pixelColor(x, y).alpha() > 0
        )
    )


def _center_rgb(image) -> tuple[int, int, int]:
    color = image.pixelColor(image.width() // 2, image.height() // 2)
    return color.red(), color.green(), color.blue()


# --- Tailles ----------------------------------------------------------------------------------------------------------------


def test_render_size_interactive_is_reduced_and_exact_is_full():
    assert scene3d.render_size((1920, 1080)) == (1920, 1080)
    interactive = scene3d.render_size((1920, 1080), scene3d.MODE_INTERACTIVE)
    assert max(interactive) <= scene3d.INTERACTIVE_SIDE and interactive[
        0
    ] / interactive[1] == pytest.approx(16 / 9, rel=0.02)
    assert scene3d.render_size((1920, 1080), scene3d.MODE_INTERACTIVE, 240) == (
        240,
        135,
    )
    assert (
        scene3d.render_size((1920, 1080), scene3d.MODE_INTERACTIVE, 99999)
        == interactive
    )  # jamais au-delà de 480
    assert scene3d.render_size((1920, 1080), scene3d.MODE_EXACT, 240) == (
        1920,
        1080,
    )  # sans effet en exact


def test_the_monitor_renders_a_small_image_and_the_export_renders_the_full_box(
    tmp_path, monkeypatch
):
    spy = Spy(monkeypatch)
    scene = write_static_scene(tmp_path)
    _p, _c, big = _setup(scene, size=(800, 450))
    image = _render(big, (800, 450), "interactive")
    assert (
        image.width() == 800 and _opaque_pixels(image) > 0
    )  # posée dans la boîte entière, image réduite
    assert [call["size"] for call in spy.calls] == [
        (240, 135)
    ]  # numpy, petit : le fournisseur n'est pas posé
    assert spy.calls[0]["backend"] == "numpy"
    spy.calls.clear()
    _p, _c, small = _setup(scene, size=(64, 36))
    _render(small, (64, 36), "exact")
    assert [call["size"] for call in spy.calls] == [(64, 36)] and spy.calls[0][
        "backend"
    ] == "numpy"


def test_an_unknown_preview_mode_draws_the_card_instead_of_raising(
    tmp_path, monkeypatch
):
    Spy(monkeypatch)
    _p, _c, scene = _setup(write_static_scene(tmp_path))
    image = _render(scene, (64, 36), "weird")
    assert _opaque_pixels(image) > 0


# --- Aucun rendu pendant la lecture ----------------------------------------------------------------------------------------


def test_no_3d_render_while_playing_and_a_card_without_any_frozen_image(
    tmp_path, monkeypatch
):
    spy = Spy(monkeypatch)
    _p, _c, scene = _setup(write_static_scene(tmp_path))
    image = _render(scene, (64, 36), "frozen")
    assert spy.calls == []  # JAMAIS de rendu 3D en lecture
    assert _opaque_pixels(image) > 0  # la carte « Scène 3D » (fond opaque)


def test_while_playing_the_last_frozen_image_of_the_file_is_shown(
    tmp_path, monkeypatch
):
    spy = Spy(monkeypatch)
    folder = tmp_path / "anim"
    folder.mkdir()
    _p, _c, scene = _setup(write_animated_scene(folder), size=(64, 36))
    stopped = _render(scene, (64, 36), "interactive", t=0.2)
    assert len(spy.calls) == 1
    playing = _render(
        scene, (64, 36), "frozen", t=1.7
    )  # un autre instant : l'image la plus récente
    assert len(spy.calls) == 1
    assert playing == stopped
    card = _render(scene, (64, 36), "frozen", t=2.5)
    assert len(spy.calls) == 1 and card == stopped


def test_the_card_texts_are_distinct_and_the_placeholder_is_short():
    assert PLACEHOLDER_TEXT != CARD_TEXT and CARD_TEXT.startswith(PLACEHOLDER_TEXT)


# --- Cache des images figées -----------------------------------------------------------------------------------------------


def test_a_scrub_back_to_a_known_instant_reuses_the_frozen_image(tmp_path, monkeypatch):
    spy = Spy(monkeypatch)
    folder = tmp_path / "anim"
    folder.mkdir()
    _p, _c, scene = _setup(write_animated_scene(folder))
    for t in (
        0.1,
        0.2,
        0.3,
    ):  # trois instants : le cache de scene_image (2 images) a oublié le premier
        _render(scene, (64, 36), "interactive", t=t)
    assert len(spy.calls) == 3
    _render(scene, (64, 36), "interactive", t=0.1)
    _render(scene, (64, 36), "interactive", t=0.2)
    assert len(spy.calls) == 3  # servis par les images figées (LRU)


def test_the_frozen_cache_is_bounded(tmp_path, monkeypatch):
    Spy(monkeypatch)
    folder = tmp_path / "anim"
    folder.mkdir()
    _p, _c, scene = _setup(write_animated_scene(folder))
    for index in range(scene3d_interactive.FROZEN_LIMIT + 6):
        _render(scene, (64, 36), "interactive", t=0.01 * (index + 1))
    assert len(scene3d_interactive._FROZEN) == scene3d_interactive.FROZEN_LIMIT


def test_texture_keys_never_mix_the_exact_the_interactive_and_the_frozen_image(
    tmp_path,
):
    _p, _c, scene = _setup(write_static_scene(tmp_path))
    keys = {
        mode: MographRenderer(scene, 64, 36, scene3d_mode=mode).content_key("s3d", 0.0)
        for mode in ("exact", "interactive", "frozen")
    }
    assert len(set(keys.values())) == 3
    layer_keys = {
        mode: MographRenderer(scene, 64, 36, scene3d_mode=mode).layer_key("s3d", 0.0)
        for mode in keys
    }
    assert len(set(layer_keys.values())) == 3
    assert (
        MographRenderer(scene, 64, 36).content_key("s3d", 0.0) == keys["exact"]
    )  # défaut = exact (export inchangé)


# --- Moteur --------------------------------------------------------------------------------------------------------------


def test_the_backend_comes_from_the_provider_and_never_feeds_the_exact_cache(
    tmp_path, monkeypatch
):
    spy = Spy(monkeypatch)
    _p, _c, scene = _setup(write_static_scene(tmp_path))
    scene3d_interactive.set_backend_provider(lambda: ("auto", None))
    interactive = _render(scene, (64, 36), "interactive")
    assert spy.calls[-1]["backend"] == "auto" and spy.calls[-1]["size"] == (64, 36)
    exact = _render(scene, (64, 36), "exact")
    assert [call["backend"] for call in spy.calls] == [
        "auto",
        "numpy",
    ]  # l'image « auto » n'a pas servi l'export
    assert interactive == exact  # (numpy partout ici : mêmes pixels)


def test_a_failing_gpu_engine_falls_back_to_numpy(tmp_path, monkeypatch):
    calls = []
    real = kut3d.render_frame

    def flaky(scene, t=0.0, size=(640, 360), *, backend="numpy", samples=1):
        calls.append(backend)
        if backend == "auto":
            raise RuntimeError("pilote")
        return real(scene, t=t, size=size, backend=backend, samples=samples)

    monkeypatch.setattr(kut3d, "render_frame", flaky)
    _p, _c, scene = _setup(write_static_scene(tmp_path))
    scene3d_interactive.set_backend_provider(lambda: ("auto", None))
    image = _render(scene, (64, 36), "interactive")
    assert calls == ["auto", "numpy"] and _center_rgb(image)[0] > 100


def test_a_backend_transition_refreshes_even_a_preview_smaller_than_the_interim_side(
    tmp_path, monkeypatch
):
    """Revue de la PR #227 : la clé de l'image figée ne contenait que la taille. Pour une boîte d'au plus 240 px, passer de
    ``("numpy", 240)`` à ``("auto", None)`` donnait la même clé : l'ancienne image numpy était resservie et le moteur GPU
    devenu disponible n'était jamais appelé."""
    spy = Spy(monkeypatch)
    _p, _c, scene = _setup(write_static_scene(tmp_path))
    scene3d_interactive.set_backend_provider(lambda: ("numpy", 240))
    _render(scene, (64, 36), "interactive")
    scene3d_interactive.set_backend_provider(lambda: ("auto", None))
    _render(scene, (64, 36), "interactive")
    assert [call["backend"] for call in spy.calls] == ["numpy", "auto"]
    _render(
        scene, (64, 36), "interactive"
    )  # même moteur, même image : servie par le cache
    assert [call["backend"] for call in spy.calls] == ["numpy", "auto"]


def test_a_numpy_failure_does_not_suppress_the_retry_with_the_gpu_engine(
    tmp_path, monkeypatch
):
    """Même collision pour la mémoire des échecs : un échec numpy ne doit pas interdire l'essai du moteur GPU."""
    real = kut3d.render_frame
    calls: list[str] = []

    def flaky(scene, t=0.0, size=(640, 360), *, backend="numpy", samples=1):
        calls.append(backend)
        if backend == "numpy":
            raise RuntimeError("numpy en échec")
        return real(scene, t=t, size=size, backend="numpy", samples=samples)

    monkeypatch.setattr(kut3d, "render_frame", flaky)
    _p, _c, scene = _setup(write_static_scene(tmp_path))
    scene3d_interactive.set_backend_provider(lambda: ("numpy", 240))
    _render(
        scene, (64, 36), "interactive"
    )  # échec : la carte est dessinée, jamais d'exception
    assert calls == ["numpy"]
    scene3d_interactive.set_backend_provider(lambda: ("auto", None))
    image = _render(scene, (64, 36), "interactive")
    assert calls == ["numpy", "auto"], calls  # l'essai GPU a bien eu lieu
    assert _center_rgb(image)[0] > 100  # une vraie image, pas la carte


def test_a_provider_that_raises_gives_numpy_small():
    def broken():
        raise RuntimeError("x")

    scene3d_interactive.set_backend_provider(broken)
    assert scene3d_interactive.preview_backend() == (
        "numpy",
        scene3d_interactive.INTERIM_SIDE,
    )


def test_a_non_numpy_engine_is_refused_in_exact_mode(tmp_path):
    graphic = add_graphic_clip(
        base_project(),
        GraphicType.SCENE3D,
        timeline_start=0,
        duration=1,
        source_path=str(write_static_scene(tmp_path)),
    ).graphic
    with pytest.raises(ValueError):
        scene3d.scene_image(graphic, mode=scene3d.MODE_EXACT, backend="auto")


# --- Échec ---------------------------------------------------------------------------------------------------------------


def test_a_failed_render_draws_the_card_logs_once_and_never_raises(
    tmp_path, monkeypatch, caplog
):
    spy = Spy(monkeypatch, fail=kut3d.RenderError("mémoire insuffisante"))
    _p, _c, scene = _setup(write_static_scene(tmp_path))
    with caplog.at_level(logging.WARNING, logger="kut_studio.scene3d"):
        first = _render(scene, (64, 36), "interactive")
        second = _render(scene, (64, 36), "interactive")
    assert _opaque_pixels(first) > 0 and first == second  # la carte, deux fois
    assert len(spy.calls) == 1  # pas de nouvel essai pour la même image
    warnings = [
        record for record in caplog.records if record.levelno == logging.WARNING
    ]
    assert len(warnings) == 1 and "mémoire insuffisante" in warnings[0].getMessage()


def test_the_card_of_a_failed_render_is_the_unavailable_card(tmp_path, monkeypatch):
    Spy(monkeypatch, fail=RuntimeError("boom"))
    _p, clip, scene = _setup(write_static_scene(tmp_path))
    from PySide6.QtGui import QImage, QPainter

    from core.mograph_raster import draw_content

    expected = QImage(64, 36, QImage.Format_ARGB32_Premultiplied)
    expected.fill(0)
    painter = QPainter(expected)
    draw_content(painter, scene3d_card(clip.graphic, CARD_TEXT), (64, 36))
    painter.end()
    assert _render(scene, (64, 36), "interactive") == expected


# --- Recharger ---------------------------------------------------------------------------------------------------------------


def _edit_keeping_stat(path: Path, old: str, new: str) -> None:
    """Modifie le contenu en gardant la taille ET la date : le mémo des signatures (``stat``) n'y voit rien."""
    info = path.stat()
    path.write_text(path.read_text().replace(old, new))
    os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
    assert path.stat().st_size == info.st_size


def test_reload_forgets_the_memoised_signature_and_the_frozen_images(
    tmp_path, monkeypatch
):
    Spy(monkeypatch)
    scene_file = write_static_scene(tmp_path)
    _p, _c, scene = _setup(scene_file)
    before = _render(scene, (64, 36), "interactive")
    signature = scene3d.scene_signature(scene_file)
    _edit_keeping_stat(tmp_path / "cube.mtl", "Kd 0.9 0.1 0.2", "Kd 0.1 0.9 0.2")
    assert (
        scene3d.scene_signature(scene_file) == signature
    )  # le mémo ne voit pas le changement
    assert _render(scene, (64, 36), "interactive") == before  # l'image figée non plus
    scene3d.reload_scene(scene_file)
    assert scene3d.scene_signature(scene_file) != signature
    after = _render(scene, (64, 36), "interactive")
    r, g, _b = _center_rgb(after)
    assert after != before and g > r  # le cube est devenu vert


def test_forget_never_raises_on_a_refused_path():
    scene3d.forget("")
    scene3d.forget("pas-une-scene.txt")
    scene3d.reload_scene("x" * 10000)


def test_a_scene_that_turned_unreadable_shows_the_card_after_reload(
    tmp_path, monkeypatch
):
    Spy(monkeypatch)
    scene_file = write_static_scene(tmp_path)
    _p, _c, scene = _setup(scene_file)
    _render(scene, (64, 36), "interactive")
    scene_file.write_text("{ pas du json")
    scene3d.reload_scene(scene_file)
    image = _render(scene, (64, 36), "interactive")
    assert _opaque_pixels(image) > 0  # carte, sans exception


def test_the_exact_path_still_gives_the_same_pixels_as_before(tmp_path):
    """Sans ``scene3d_mode`` (export, segments fidèles) : le rendu est celui de ``draw_scene`` d'avant le lot B3."""
    _p, _c, scene = _setup(write_static_scene(tmp_path))
    default = MographRenderer(scene, 64, 36, fps=25.0).render(scene.top_level(), 0.0)
    explicit = _render(scene, (64, 36), "exact")
    assert default == explicit
    arr = np.array(
        [[default.pixelColor(x, y).red() for x in range(64)] for y in range(36)]
    )
    assert arr.max() > 100
