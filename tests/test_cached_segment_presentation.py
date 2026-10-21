"""Afficher un segment déjà composé remet à zéro le compositing du moniteur GPU.

Régression : le segment contient déjà transform, effets, fusion, masques et calques d'effets. Le moniteur
remettait la transformation et les effets à zéro, mais gardait la fusion, la matte et les ajustements du clip
précédemment affiché : ils étaient appliqués une seconde fois par-dessus le segment.
"""

from __future__ import annotations

from test_scopes import _window

from core.blend_modes import BlendMode


def test_a_cached_segment_clears_the_gpu_compositing_of_the_previous_clip(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    panel = window.preview_panel
    panel.set_layer_compositing(BlendMode.MULTIPLY, ("matte-key", object()))
    panel.set_adjustments([("adjustment-key", (), None, 1.0)])
    assert panel._gpu_blend is BlendMode.MULTIPLY and panel._gpu_adjustments

    monkeypatch.setattr(window, "_cached_preview_at", lambda _time: ("/tmp/segment.mp4", 0.0))
    monkeypatch.setattr(panel, "preview_at", lambda *_args, **_kwargs: None)       # pas de vraie lecture
    assert window._present_cached_preview_at(1.0) is True

    assert panel._gpu_blend is None
    assert panel._gpu_matte is None
    assert panel._gpu_adjustments == ()
