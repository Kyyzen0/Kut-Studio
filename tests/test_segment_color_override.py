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
            assert view.colors["main"] == ("", "auto") and panel._gpu_main_color == ("", "auto"), segment
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
    assert view.colors["main"] == ("", "auto")
    _show(window, False, monkeypatch)
    assert view.colors["main"][0] == "bt601"


def test_the_real_view_keeps_the_label_of_a_segment_once_the_override_is_removed():
    from ui.gpu_preview import GpuPreviewWidget, PendingFrame

    fake = SimpleNamespace(_color_overrides={}, _latest={"main": PendingFrame(object(), 1)}, _pending={}, update=lambda: None)
    source = VideoSource("main", "yuv420p", 1920, 1080, "", "video")
    GpuPreviewWidget.set_source_color(fake, "main", "bt601", "bt601")             # affichage direct, BT.601 forcé
    assert GpuPreviewWidget._colored(fake, "main", source).colorspace == "bt601"
    fake._pending.clear()
    GpuPreviewWidget.set_source_color(fake, "main", "")                            # puis le segment
    assert GpuPreviewWidget._colored(fake, "main", source) is source               # ni colorspace ni color_choice remplacés
    assert "main" in fake._pending                                                 # l'image affichée est relue


# --- Parité numérique avec l'export (FFmpeg réel) ------------------------------------------------------------------------

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="FFmpeg absent")


@needs_ffmpeg
@pytest.mark.parametrize("choice", ["auto", "bt601", "bt709"])
def test_a_real_faithful_segment_read_with_its_own_label_equals_the_export(tmp_path, choice):
    """Le segment est le résultat de l'export (OUTPUT_COLOR_STAGE) : le moniteur le lit sans surcharge, étiquette Qt bt709."""
    import subprocess

    from test_color_matrix_parity import FPS, _asset, _codes, _encode, _export

    entry = (_encode(tmp_path / "hd.mkv", (1280, 720)), (1280, 720))
    asset = _asset(entry, choice)
    exported = _export(asset)                                       # l'image de l'export = ce que contient le segment
    segment = tmp_path / "segment.mkv"
    from core.export_engine import OUTPUT_COLOR_STAGE

    rgb = np.clip(np.rint(exported), 0, 255).astype(np.uint8)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "1280x720", "-r", str(FPS),
                    "-i", "-", "-frames:v", "1", "-vf", f"format=rgb24,{OUTPUT_COLOR_STAGE}", "-c:v", "ffv1", str(segment)],
                   input=rgb.tobytes(), check=True, timeout=60)
    seg_asset = MediaAsset("s", str(segment), "s", 1.0, 1280, 720, float(FPS), "video", color_space="bt709")
    codes = _codes(seg_asset)

    def monitor(matrix: str, choice_: str = "auto") -> np.ndarray:
        source = VideoSource("main", "yuv444p", 1280, 720, colorspace=matrix, color_choice=choice_)
        layer = CompositeLayer(source="main", matrix=(1.0, 0.0, 0.0, 1.0, 0.0, 0.0), fit=(0.0, 0.0, 1280.0, 720.0))
        return reference_frame(CompositeFrame(1280, 720, 1.0, (layer,), (source,)), {"main": codes}) * 255.0

    from test_color_matrix_parity import _bars

    assert float(np.abs(_bars(monitor("")) - _bars(exported)).max()) <= 1                   # sans surcharge : égal à l'export
    wrong = monitor("bt601", "bt601")                                                         # le défaut : seconde conversion
    assert float(np.abs(_bars(wrong) - _bars(exported)).max()) > 15
