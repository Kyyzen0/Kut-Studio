"""Un projet ouvert avec des images clés ``fx.*`` (effet existant et effet absent) : les lecteurs d'animation de l'interface
(Graph Editor, commandes d'animation, moniteur) fonctionnent sans les lister (ADR-0008, étape 2 : aucune interface pour elles)."""

from __future__ import annotations

from core.animation import Keyframe
from core.effects_model import EffectType, create_effect
from core.keyframe_editing import KeyframeRef
from core.timeline_operations import set_transform_keyframe
from tests.test_inspector_lock_guards import _add_track_clip, window  # noqa: F401 - fixture et aide communes


def _prepared(win):
    _track, clip = _add_track_clip(win, "video", "v")
    set_transform_keyframe(win.project, clip.id, "position_x", 0.5, 0.2)
    clip.effects = [create_effect(EffectType.BLUR, effect_id="b")]
    clip.animation = [Keyframe("fx.b.intensity", 0.0, 1.0, id="k1"), Keyframe("fx.b.intensity", 2.0, 9.0, id="k2"),
                      Keyframe("fx.gone.intensity", 1.0, 5.0, id="k3")]
    win.history.reset(win.project)
    win.timeline_panel.select_clip(clip.id)
    return clip


def test_the_graph_editor_and_the_animation_commands_ignore_the_effect_keyframes(window):
    clip = _prepared(window)
    editor = window.open_graph_editor("position_x")
    editor.set_property("position_x")
    editor.refresh()
    assert editor.target().id == "position_x"
    assert all(not pid.startswith("fx.") for pid in window._animation_properties(clip))     # rien de nouveau dans les listes
    assert all(not pid.startswith("fx.") for pid in window._command_properties(clip))
    assert window.select_all_keyframes() >= 1
    assert window.copy_animation() and window.paste_animation() >= 0
    window.set_keyframe_selection({KeyframeRef(clip.id, "position_x", clip.transform_keyframes[0].id)})
    editor.refresh()
    assert [kf.property_name for kf in clip.animation] == ["fx.b.intensity", "fx.b.intensity", "fx.gone.intensity"]


def test_the_viewer_helpers_and_the_alignment_check_read_the_registry_without_failing(window):
    from ui.main_window_mixins.frame_alignment import FrameAlignmentMixin
    from ui.viewer_overlay import ViewerOverlay

    _prepared(window)
    assert ViewerOverlay._reachable("position_x", 0.5)
    assert ViewerOverlay._reachable("fx.b.intensity", 999.0)       # inconnue sans clip : repli tolérant (except KeyError)
    assert FrameAlignmentMixin._out_of_range({"position_x": 0.5}) == []
