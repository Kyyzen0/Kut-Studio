"""Fenêtres des nœuds d'étalonnage hors export : matte du moniteur GPU, couverture sous la pipette.

Même rastériseur et même animation que l'export (:func:`core.mograph_ffmpeg.video_window_label`) : un calque « rig »
du clip dont les masques sont les fenêtres du nœud, avec l'animation effective du clip (images-clés ``mask.<id>.*``,
dont celles que dérive une liaison de tracking). La fenêtre est en espace calque, comme un masque : le moniteur GPU
l'applique à la texture du calque, avant sa transformation, comme la matte des masques.
"""

from __future__ import annotations

from dataclasses import replace

from .compositing import Compositing


def _clip_and_track(project, clip_id: str):
    for index, track in enumerate(project.tracks):
        for clip in track.clips:
            if clip.id == clip_id:
                return clip, track, index
    raise KeyError(clip_id)


def window_scene(project, clip_id: str, windows):
    """La scène d'un seul calque : le clip, ses masques remplacés par ``windows``."""
    from .mograph_scene import GraphicsScene
    from .render_plan import _graphic_layer

    clip, track, index = _clip_and_track(project, clip_id)
    state = None
    if getattr(clip, "tracking", None) is not None:
        from .tracking_bindings import TrackingContext, effective_clip_state

        state = effective_clip_state(clip, TrackingContext(project))
    layer = _graphic_layer(clip, track, index, role="rig", state=state)
    return GraphicsScene((replace(layer, compositing=Compositing(masks=tuple(windows))),), project.width, project.height)


def window_matte_key(project, clip_id: str, windows, timeline_time: float, width: int, height: int) -> str:
    """Identité de la matte de :func:`window_matte` : les fenêtres évaluées à cet instant et la taille."""
    evaluated = window_scene(project, clip_id, windows).evaluate(clip_id, timeline_time).masks
    return f"window:{clip_id}:{width}x{height}:{evaluated!r}"


def window_matte(project, clip_id: str, windows, timeline_time: float, width: int, height: int):
    """``(clé, QImage)`` de la matte des fenêtres à ``timeline_time``, à la taille ``width × height`` (alpha : la
    couverture). La clé suit les fenêtres évaluées : une fenêtre immobile n'est rastérisée qu'une fois."""
    from .mograph_raster import render_layer_matte

    scene = window_scene(project, clip_id, windows)
    evaluated = scene.evaluate(clip_id, timeline_time).masks
    key = f"window:{clip_id}:{width}x{height}:{evaluated!r}"
    return key, render_layer_matte(scene, clip_id, timeline_time, width, height)


def window_coverage(project, clip_id: str, windows, timeline_time: float, point: tuple[float, float]) -> float:
    """Couverture (0..1) des fenêtres au pixel ``point`` du calque (pixels de la séquence)."""
    from PySide6.QtCore import QRect

    from .mograph_raster import render_matte

    scene = window_scene(project, clip_id, windows)
    evaluated = scene.evaluate(clip_id, timeline_time).masks
    x, y = int(point[0]), int(point[1])
    matte = render_matte(evaluated, (scene.width, scene.height), (1.0, 0.0, 0.0, 1.0, 0.0, 0.0), QRect(x, y, 1, 1))
    if matte is None:
        return 1.0
    return ((matte.pixel(0, 0) >> 24) & 0xFF) / 255.0


def window_ids(value) -> set[str]:
    """Identifiants des fenêtres des nœuds d'un étalonnage (graphe ou réglage simple)."""
    from .color_nodes import windows_of

    return {window.id for window in windows_of(value)}


def forget_windows(clip, ids) -> None:
    """Les fenêtres ``ids`` ont quitté l'étalonnage de ``clip`` : leurs images-clés ``mask.<id>.*`` et leurs liaisons
    de tracking partent avec elles (sinon : liaisons cassées dans le panneau Tracking, avertissements « masque
    absent » au rendu). Quel que soit le geste : supprimer la fenêtre, son nœud, coller un autre étalonnage."""
    gone = set(ids)
    if not gone:
        return
    clip.animation = [kf for kf in clip.animation
                      if not (kf.property_name.startswith("mask.") and kf.property_name.split(".")[1] in gone)]
    tracking = getattr(clip, "tracking", None)
    if tracking is not None and any(link.mask_id in gone for link in tracking.links):
        clip.tracking = replace(tracking, links=tuple(link for link in tracking.links if link.mask_id not in gone))
