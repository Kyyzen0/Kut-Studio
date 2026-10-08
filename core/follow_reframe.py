"""Recadrage suivi : un plan horizontal recadré en vertical dont la fenêtre suit un point de tracking.

Le cadrage « remplir » (``ClipTransform.fill``) agrandit le média jusqu'à couvrir le cadre ; ``pan_x`` / ``pan_y``
(−1…1) placent la fenêtre visible dans l'excédent, au pixel près comme le ``crop`` de l'export
(:func:`core.tracking_motion.pan_offset` : bord gauche de la fenêtre = excédent × (1 + pan) / 2). Pour que le point
suivi tombe au centre du cadre, la fenêtre commence à ``X − cadre / 2``, d'où, à chaque image de la séquence :

    pan = 2 · (X − cadre / 2) / excédent − 1

où ``X`` est la position du point dans le média agrandi. La trajectoire du point est lissée d'abord (une caméra qui
suit le sujet, pas une qui sursaute à chaque pas), puis le pan est borné à [−1, 1] : la fenêtre ne sort jamais du
média, aucun bord noir n'apparaît, même quand le sujet longe le bord de l'image.

Le résultat est écrit en images-clés ordinaires de ``pan_x`` (et de ``pan_y`` quand le média dépasse aussi en
hauteur) : l'aperçu, le moniteur et l'export les lisent comme tout pan animé, et l'utilisateur peut les retoucher.
Corriger le tracker ensuite ne met pas le recadrage à jour : on relance la commande.
"""

from __future__ import annotations

from collections.abc import Sequence

from .animation import InterpolationType
from .project_model import Clip
from .tracking_bindings import frame_times, simplify
from .tracking_model import Smoothing, TrackData, Tracker
from .tracking_motion import cover_size, gaussian_smooth, source_time
from .visual_effects import TransformKeyframe

FOLLOW_SIGMA = Smoothing.SIGMA[Smoothing.MEDIUM]
"""Lissage de la trajectoire suivie (écart-type en images) : celui de la stabilisation « moyenne »."""

TOLERANCE_PIXELS = 0.25
"""Écart toléré (pixels du cadre) entre le pan échantillonné et ses images-clés simplifiées : invisible."""


class ReframeRefused(ValueError):
    """Recadrage suivi impossible ; ``reason`` (``no_margin`` ou ``no_positions``) permet à l'interface de traduire."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def follow_pan_keyframes(
    clip: Clip,
    trackers: Sequence[Tracker],
    *,
    media_size: tuple[int, int],
    canvas_size: tuple[int, int],
    fps: float,
    sigma: float = FOLLOW_SIGMA,
) -> list[TransformKeyframe]:
    """Images-clés (temps local du clip) de ``pan_x`` / ``pan_y`` qui gardent le sujet au centre du cadre.

    Le sujet est la moyenne des ``trackers`` analysés, à l'instant source que le clip montre (vitesse, inversion et
    courbes comprises). Seuls les axes où le média agrandi dépasse du cadre sont animés.

    Raises:
        ReframeRefused: aucun axe ne dépasse (``no_margin`` : le média a déjà la forme du cadre), ou aucun tracker n'a
            de position (``no_positions``).
    """
    width, height = max(1, int(media_size[0])), max(1, int(media_size[1]))
    canvas_w, canvas_h = max(1, int(canvas_size[0])), max(1, int(canvas_size[1]))
    cover_w, cover_h = cover_size(width, height, canvas_w, canvas_h)
    axes = [
        (name, axis, excess, cover / media, frame)
        for name, axis, excess, cover, media, frame in (
            ("pan_x", 0, cover_w - canvas_w, cover_w, width, canvas_w),
            ("pan_y", 1, cover_h - canvas_h, cover_h, height, canvas_h),
        )
        if excess > 0
    ]
    if not axes:
        raise ReframeRefused("no_margin", "Ce plan a déjà la forme du cadre : aucune marge où déplacer la fenêtre.")
    datas = [tracker.data for tracker in trackers if tracker.data.valid_range() is not None]
    if not datas:
        raise ReframeRefused("no_positions", "Le tracker n'a encore aucune position : analysez-le d'abord.")
    times = frame_times(clip, fps)
    subject = [_subject_at(clip, datas, t, (width, height)) for t in times]
    frames: list[TransformKeyframe] = []
    for name, axis, excess, scale, frame in axes:
        centre = gaussian_smooth([point[axis] * scale for point in subject], sigma)
        pans = [max(-1.0, min(1.0, 2.0 * (x - frame / 2.0) / excess - 1.0)) for x in centre]
        for index in simplify(times, pans, 2.0 * TOLERANCE_PIXELS / excess):
            frames.append(TransformKeyframe(name, times[index], pans[index], InterpolationType.LINEAR))
    return frames


def apply_follow_reframe(
    clip: Clip,
    trackers: Sequence[Tracker],
    *,
    media_size: tuple[int, int],
    canvas_size: tuple[int, int],
    fps: float,
    sigma: float = FOLLOW_SIGMA,
) -> list[TransformKeyframe]:
    """Passe le clip en cadrage « remplir » et remplace l'animation de son pan par le suivi du sujet.

    Les autres propriétés animées sont gardées. Rien n'est modifié si le calcul est refusé (:class:`ReframeRefused`)."""
    frames = follow_pan_keyframes(
        clip, trackers, media_size=media_size, canvas_size=canvas_size, fps=fps, sigma=sigma,
    )
    animated = {frame.property_name for frame in frames}
    kept = [frame for frame in clip.transform_keyframes if frame.property_name not in animated]
    clip.transform = clip.transform.with_property("fill", True)
    clip.transform_keyframes = sorted([*kept, *frames], key=lambda frame: (frame.property_name, frame.time_seconds))
    return frames


def _subject_at(clip: Clip, datas: Sequence[TrackData], local_time: float, media_size: tuple[int, int]):
    """Position moyenne des trackers (pixels du média actuel) à l'instant source montré à ``local_time``."""
    seconds = source_time(clip, local_time)
    xs, ys = [], []
    for data in datas:
        position = data.position_at_index(data.index_at_time(seconds))
        if position is None:
            continue
        sx, sy = data.scaled_to(*media_size)             # mesuré sur un média d'une autre résolution (relink)
        xs.append(position[0] * sx)
        ys.append(position[1] * sy)
    return (sum(xs) / len(xs), sum(ys) / len(ys))


__all__ = ["FOLLOW_SIGMA", "ReframeRefused", "TOLERANCE_PIXELS", "apply_follow_reframe", "follow_pan_keyframes"]
