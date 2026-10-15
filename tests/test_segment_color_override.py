"""Un segment d'aperçu fidèle ne reçoit jamais la matrice du média : il est déjà converti en sortie (BT.709, plage limitée).

Défaut corrigé : la surcharge ``main`` laissée par le dernier affichage direct du clip (BT.601 forcé) était appliquée par-dessus
le segment, soit une seconde conversion (aperçu 232,0,0 là où l'export donne 254,0,0). Voir aussi ``test_color_matrix_parity.py``.
"""

from __future__ import annotations

import shutil
from types import SimpleNamespace

import numpy as np
import pytest

from core.color_matrix import asset_effective_matrix
from core.gpu_composite import CompositeFrame, CompositeLayer, VideoSource, reference_frame
from core.project_model import MediaAsset
from ui.gpu_preview import GpuPreviewWidget, PendingFrame   # importés avant que fake_gpu ne remplace la classe
from test_color_matrix_ui import _window_with_clip  # noqa: F401
from test_gpu_preview_ui import fake_gpu  # noqa: F401 - fixture partagée


def _window(qtbot, monkeypatch, fake_gpu, *, matrix_choice: str, size=(1920, 1080)):
    window = _window_with_clip(qtbot, monkeypatch)
    asset = window.project.media_assets[0]
    asset.color_matrix = matrix_choice
    asset.width, asset.height = size
    # Aucun vrai lecteur sur un média fictif (journalise depuis un fil de Qt Multimedia, segfault xdist).
    panel = window.preview_panel
    monkeypatch.setattr(panel.player, "setSource", lambda *_a, **_k: None)
    monkeypatch.setattr(panel.player, "setPosition", lambda *_a, **_k: None)
    monkeypatch.setattr(panel.player, "play", lambda *_a, **_k: None)
    monkeypatch.setattr(panel.player, "pause", lambda *_a, **_k: None)
    monkeypatch.setattr(window, "preview_source_for", lambda path, **_k: path)
    assert panel.enable_gpu("metal")
    window._timeline_index = None            # le clip a été ajouté après la construction de l'index
    window.playhead_seconds = 0.5
    window.is_playing = False
    return window, panel, fake_gpu.instances[-1]


def _show(window, segment: bool, monkeypatch):
    monkeypatch.setattr(window, "_cached_preview_at",
                        lambda _t: ("/cache/seg.mp4", 0.0) if segment else None)
    window._sync_preview_core()


@pytest.mark.parametrize("choice, expected", [("bt601", "bt601"), ("auto", "bt709")])
def test_the_main_override_exists_only_while_the_clip_is_shown_directly(qtbot, monkeypatch, fake_gpu, choice, expected):
    window, panel, view = _window(qtbot, monkeypatch, fake_gpu, matrix_choice=choice)
    for segment in (False, True, False, True):
        _show(window, segment, monkeypatch)
        if segment:
            assert view.colors["main"] == ("bt709", "bt709") and panel._gpu_main_color == ("bt709", "bt709"), segment
        else:
            assert view.colors["main"][0] == expected and panel._gpu_main_color[0] == expected


def test_a_sd_clip_labelled_601_in_auto_is_not_converted_twice_either(qtbot, monkeypatch, fake_gpu):
    window, panel, view = _window(qtbot, monkeypatch, fake_gpu, matrix_choice="auto", size=(640, 360))
    asset = window.project.media_assets[0]
    asset.color_space = "bt601"
    assert asset_effective_matrix(asset, probe=False) == "bt601"
    _show(window, False, monkeypatch)
    assert view.colors["main"][0] == "bt601"
    _show(window, True, monkeypatch)
    assert view.colors["main"] == ("bt709", "bt709")
    _show(window, False, monkeypatch)
    assert view.colors["main"][0] == "bt601"


@pytest.mark.parametrize("size", [(480, 270), (960, 540), (1280, 720)])
@pytest.mark.parametrize("choice", ["auto", "bt601", "bt709"])
def test_a_segment_is_read_in_bt709_whatever_qt_labels_and_whatever_its_reduced_size(qtbot, monkeypatch, fake_gpu, choice, size):
    """Qt répond souvent ``ColorSpace_Undefined`` (étiquette vide) : la matrice ne doit alors pas être déduite de la taille."""
    from core.gpu_frames import resolve_color
    window, panel, view = _window(qtbot, monkeypatch, fake_gpu, matrix_choice=choice)
    _show(window, False, monkeypatch)                                              # le clip d'abord en direct
    _show(window, True, monkeypatch)
    entry = view.colors["main"]
    real = SimpleNamespace(_color_overrides={"main": entry}, _latest={"main": PendingFrame(object(), 1)}, _pending={},
                           update=lambda: None)
    undefined = VideoSource("main", "yuv420p", size[0], size[1], "", "video")      # ce que lit le décodeur de Qt
    colored = GpuPreviewWidget._colored(real, "main", undefined)
    assert resolve_color(colored.colorspace, colored.color_range, size[0], size[1], colored.color_choice)[0] == "bt709"


def test_a_composition_cache_piece_is_read_in_bt709_too():
    from core.gpu_frames import resolve_color
    from ui.main_window_mixins.hardware_preview import HardwarePreviewMixin

    matrix, choice = HardwarePreviewMixin._monitor_color_matrix(SimpleNamespace(), SimpleNamespace(rendered=True), None)
    assert resolve_color(matrix, "", 480, 270, choice)[0] == "bt709"


# --- Parité numérique avec l'export (FFmpeg réel) ------------------------------------------------------------------------

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="FFmpeg absent")


@needs_ffmpeg
@pytest.mark.parametrize("size", [(480, 270), (960, 540), (1280, 720)])
@pytest.mark.parametrize("choice", ["auto", "bt601", "bt709"])
def test_a_real_faithful_segment_read_with_its_own_label_equals_the_export(tmp_path, choice, size):
    """Le segment est le résultat de l'export (OUTPUT_COLOR_STAGE) : le moniteur le lit sans surcharge, étiquette Qt bt709."""
    import subprocess

    from test_color_matrix_parity import FPS, _asset, _codes, _encode, _export

    w, h = size
    entry = (_encode(tmp_path / "hd.mkv", size), size)
    asset = _asset(entry, choice)
    exported = _export(asset)                                       # l'image de l'export = ce que contient le segment
    segment = tmp_path / "segment.mkv"
    from core.export_engine import OUTPUT_COLOR_STAGE

    rgb = np.clip(np.rint(exported), 0, 255).astype(np.uint8)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(FPS),
                    "-i", "-", "-frames:v", "1", "-vf", f"format=rgb24,{OUTPUT_COLOR_STAGE}", "-c:v", "ffv1", str(segment)],
                   input=rgb.tobytes(), check=True, timeout=60)
    seg_asset = MediaAsset("s", str(segment), "s", 1.0, w, h, float(FPS), "video", color_space="bt709")
    codes = _codes(seg_asset)

    def monitor(matrix: str, choice_: str = "auto") -> np.ndarray:
        source = VideoSource("main", "yuv444p", w, h, colorspace=matrix, color_choice=choice_)
        layer = CompositeLayer(source="main", matrix=(1.0, 0.0, 0.0, 1.0, 0.0, 0.0), fit=(0.0, 0.0, float(w), float(h)))
        return reference_frame(CompositeFrame(w, h, 1.0, (layer,), (source,)), {"main": codes}) * 255.0

    from test_color_matrix_parity import _bars
    from ui.main_window_mixins.faithful_preview import SEGMENT_COLOR

    assert float(np.abs(_bars(monitor(*SEGMENT_COLOR)) - _bars(exported)).max()) <= 1                   # sans surcharge : égal à l'export
    wrong = monitor("bt601", "bt601")                                                         # le défaut : seconde conversion
    assert float(np.abs(_bars(wrong) - _bars(exported)).max()) > 15
    if h < 720:                                                                       # étiquette Qt indéfinie : la taille décide
        undefined = monitor("")
        assert float(np.abs(_bars(undefined) - _bars(exported)).max()) > 15
