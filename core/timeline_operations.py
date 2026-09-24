"""Opérations métier sur la timeline de Kut-Studio.

Ce module manipule directement les dataclasses ``Project``, ``Track`` et
``Clip`` définies dans ``core.project_model``. Aucune dépendance PySide6 :
ces opérations peuvent être appelées hors d'un contexte Qt (tests,
scripts, services batch).

Chaque opération :

- Modifie le projet fourni en place (les dataclasses ne sont pas figées).
- Retourne le ou les ``Clip`` effectivement modifiés pour faciliter le
  chaînage et l'écriture des tests.
- Lève ``KeyError`` ou ``ValueError`` avec un message clair en cas
  d'identifiant inconnu ou de règle métier violée.

Périmètre volontairement limité pour cette étape : pas de snapping,
pas de ripple edit, pas de gestion des collisions, pas d'undo/redo,
pas de transitions.
"""

from __future__ import annotations

from .project_model import Clip, MediaAsset, Project, Track


# ---------------------------------------------------------------------------
# Recherche
# ---------------------------------------------------------------------------


def find_track(project: Project, track_id: str) -> Track:
    """Retourne la ``Track`` dont l'identifiant correspond.

    Raises:
        KeyError: si aucune piste ne correspond dans ``project``.
    """
    for track in project.tracks:
        if track.id == track_id:
            return track
    raise KeyError(
        f"Piste '{track_id}' introuvable dans le projet '{project.name}'."
    )


def find_clip(project: Project, clip_id: str) -> Clip:
    """Retourne le ``Clip`` dont l'identifiant correspond (toutes pistes).

    Raises:
        KeyError: si aucun clip ne correspond dans ``project``.
    """
    for track in project.tracks:
        for clip in track.clips:
            if clip.id == clip_id:
                return clip
    raise KeyError(
        f"Clip '{clip_id}' introuvable dans le projet '{project.name}'."
    )


def _find_track_for_clip(project: Project, clip_id: str) -> tuple[Track, int]:
    """Retourne ``(track, index_du_clip)``, ou lève ``KeyError``."""
    for track in project.tracks:
        for index, clip in enumerate(track.clips):
            if clip.id == clip_id:
                return track, index
    raise KeyError(
        f"Clip '{clip_id}' introuvable dans le projet '{project.name}'."
    )


def _find_asset(project: Project, asset_id: str) -> MediaAsset:
    """Retourne le ``MediaAsset`` correspondant, ou lève ``KeyError``."""
    for asset in project.media_assets:
        if asset.id == asset_id:
            return asset
    raise KeyError(
        f"Média '{asset_id}' introuvable dans le projet '{project.name}'."
    )


# ---------------------------------------------------------------------------
# Déplacement
# ---------------------------------------------------------------------------


def move_clip(
    project: Project, clip_id: str, new_timeline_start: float
) -> Clip:
    """Déplace un clip à un nouvel instant sur la timeline.

    Seule la valeur ``timeline_start`` est modifiée ; ``source_in``,
    ``source_out`` et ``duration`` restent strictement inchangées.

    Raises:
        KeyError: si ``clip_id`` n'existe pas dans le projet.
        ValueError: si ``new_timeline_start`` est strictement négatif.
    """
    if new_timeline_start < 0.0:
        raise ValueError(
            f"Impossible de déplacer le clip '{clip_id}' avant 0.0 seconde "
            f"(position demandée : {new_timeline_start})."
        )
    track, index = _find_track_for_clip(project, clip_id)
    clip = track.clips[index]
    clip.timeline_start = new_timeline_start
    return clip


# ---------------------------------------------------------------------------
# Trims
# ---------------------------------------------------------------------------


def trim_clip_left(
    project: Project, clip_id: str, new_timeline_start: float
) -> Clip:
    """Rogne la partie gauche du clip en avançant son point d'entrée.

    Le clip ne peut que se raccourcir par la gauche : ``timeline_start``
    doit être supérieur ou égal à la valeur actuelle, et ``source_in``
    est augmenté de la même quantité (la durée diminue d'autant,
    ``source_out`` reste fixe).

    Raises:
        KeyError: si ``clip_id`` n'existe pas dans le projet.
        ValueError: si ``new_timeline_start`` est négatif, strictement
            inférieur à la position actuelle, ou atteint/dépasse la fin
            de timeline du clip (durée nulle ou négative).
    """
    if new_timeline_start < 0.0:
        raise ValueError(
            f"Impossible de rogner le clip '{clip_id}' avant 0.0 seconde."
        )
    track, index = _find_track_for_clip(project, clip_id)
    clip = track.clips[index]
    delta = new_timeline_start - clip.timeline_start
    if delta < 0.0:
        raise ValueError(
            f"Le trim gauche ne peut que reculer la position de début "
            f"vers l'avant : timeline_start actuel = {clip.timeline_start}, "
            f"demandé = {new_timeline_start}."
        )
    new_source_in = clip.source_in + delta
    if new_source_in >= clip.source_out:
        raise ValueError(
            f"Le trim gauche produirait une durée nulle ou négative pour "
            f"le clip '{clip_id}' (source_in={new_source_in}, "
            f"source_out={clip.source_out})."
        )
    clip.timeline_start = new_timeline_start
    clip.source_in = new_source_in
    return clip


def trim_clip_right(
    project: Project, clip_id: str, new_timeline_end: float
) -> Clip:
    """Rogne ou étend la partie droite du clip.

    Seule la valeur ``source_out`` est modifiée ; ``timeline_start`` et
    ``source_in`` restent inchangés. La nouvelle position de fin ne doit
    pas dépasser la durée du ``MediaAsset`` référencé par le clip.

    Raises:
        KeyError: si ``clip_id`` ou le ``MediaAsset`` associé est introuvable.
        ValueError: si ``new_timeline_end`` produit une durée nulle ou
            négative, ou si la nouvelle ``source_out`` dépasse la durée
            du média sous-jacent.
    """
    track, index = _find_track_for_clip(project, clip_id)
    clip = track.clips[index]
    asset = _find_asset(project, clip.asset_id)

    current_timeline_end = clip.timeline_start + clip.duration
    delta = new_timeline_end - current_timeline_end
    new_source_out = clip.source_out + delta

    if new_timeline_end <= clip.timeline_start:
        raise ValueError(
            f"Le trim droit produirait une durée nulle ou négative pour "
            f"le clip '{clip_id}' (timeline_start={clip.timeline_start}, "
            f"timeline_end demandé={new_timeline_end})."
        )
    if new_source_out <= clip.source_in:
        raise ValueError(
            f"Le trim droit produirait une durée nulle ou négative pour "
            f"le clip '{clip_id}' (source_in={clip.source_in}, "
            f"source_out={new_source_out})."
        )
    if new_source_out > asset.duration:
        raise ValueError(
            f"Le trim droit dépasserait la durée du média '{asset.name}' "
            f"(asset={asset.duration}s, source_out={new_source_out})."
        )

    clip.source_out = new_source_out
    return clip


# ---------------------------------------------------------------------------
# Coupe
# ---------------------------------------------------------------------------


def cut_clip(
    project: Project, clip_id: str, cut_timeline_position: float
) -> tuple[Clip, Clip]:
    """Coupe un clip en deux à la position de timeline indiquée.

    Le clip de gauche conserve l'identifiant d'origine ; le clip de
    droite reçoit l'identifiant ``"{clip_id}-split-2"``. Les deux clips
    restent sur la même piste et la somme de leurs durées est égale à
    la durée d'origine du clip.

    Raises:
        KeyError: si ``clip_id`` n'existe pas.
        ValueError: si la position n'est pas strictement à l'intérieur
            du clip, ou si l'identifiant généré est déjà utilisé.
    """
    track, index = _find_track_for_clip(project, clip_id)
    clip = track.clips[index]

    timeline_end = clip.timeline_start + clip.duration
    if (
        cut_timeline_position <= clip.timeline_start
        or cut_timeline_position >= timeline_end
    ):
        raise ValueError(
            f"La position de coupe ({cut_timeline_position}) doit être "
            f"strictement à l'intérieur du clip '{clip_id}' "
            f"[{clip.timeline_start}, {timeline_end}]."
        )

    right_id = f"{clip_id}-split-2"
    for existing_track in project.tracks:
        for existing_clip in existing_track.clips:
            if existing_clip.id == right_id:
                raise ValueError(
                    f"Impossible de couper : l'identifiant '{right_id}' "
                    f"existe déjà dans le projet."
                )

    delta_in_source = cut_timeline_position - clip.timeline_start
    left_source_out = clip.source_in + delta_in_source

    left_clip = Clip(
        id=clip.id,
        asset_id=clip.asset_id,
        track_id=clip.track_id,
        timeline_start=clip.timeline_start,
        source_in=clip.source_in,
        source_out=left_source_out,
        enabled=clip.enabled,
        label=clip.label,
        text=clip.text,
    )
    right_clip = Clip(
        id=right_id,
        asset_id=clip.asset_id,
        track_id=clip.track_id,
        timeline_start=cut_timeline_position,
        source_in=left_source_out,
        source_out=clip.source_out,
        enabled=clip.enabled,
        label=clip.label,
        text=clip.text,
    )

    track.clips[index : index + 1] = [left_clip, right_clip]
    return left_clip, right_clip


# ---------------------------------------------------------------------------
# Suppression
# ---------------------------------------------------------------------------


def delete_clip(project: Project, clip_id: str) -> Clip:
    """Retire le clip de sa piste et le retourne.

    Raises:
        KeyError: si ``clip_id`` n'existe pas.
    """
    track, index = _find_track_for_clip(project, clip_id)
    clip = track.clips[index]
    del track.clips[index]
    return clip
