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

import uuid

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


def _ensure_track_editable(project: Project, track) -> None:
    """Refuse les opérations sur une piste verrouillée."""
    if getattr(track, "locked", False):
        raise ValueError(
            f"La piste '{track.id}' est verrouillée : modifications refusées."
        )


def move_clip(
    project: Project, clip_id: str, new_timeline_start: float
) -> Clip:
    """Déplace un clip à un nouvel instant sur la timeline.

    Seule la valeur ``timeline_start`` est modifiée ; ``source_in``,
    ``source_out`` et ``duration`` restent strictement inchangées.

    Raises:
        KeyError: si ``clip_id`` n'existe pas dans le projet.
        ValueError: si ``new_timeline_start`` est strictement négatif
            ou si la piste du clip est verrouillée.
    """
    if new_timeline_start < 0.0:
        raise ValueError(
            f"Impossible de déplacer le clip '{clip_id}' avant 0.0 seconde "
            f"(position demandée : {new_timeline_start})."
        )
    track, index = _find_track_for_clip(project, clip_id)
    _ensure_track_editable(project, track)
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
    _ensure_track_editable(project, track)
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
    old_source_in = clip.source_in
    old_source_out = clip.source_out
    clip.timeline_start = new_timeline_start
    clip.source_in = new_source_in
    apply_clip_transform_on_trim(
        clip,
        old_source_in=old_source_in,
        old_source_out=old_source_out,
        new_source_in=new_source_in,
        new_source_out=clip.source_out,
    )
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
    _ensure_track_editable(project, track)
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

    old_source_in = clip.source_in
    old_source_out = clip.source_out
    clip.source_out = new_source_out
    apply_clip_transform_on_trim(
        clip,
        old_source_in=old_source_in,
        old_source_out=old_source_out,
        new_source_in=clip.source_in,
        new_source_out=new_source_out,
    )
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
    _ensure_track_editable(project, track)
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
        ValueError: si la piste du clip est verrouillée.
    """
    track, index = _find_track_for_clip(project, clip_id)
    _ensure_track_editable(project, track)
    clip = track.clips[index]
    del track.clips[index]
    return clip


# ---------------------------------------------------------------------------
# Création
# ---------------------------------------------------------------------------


def add_clip_to_track(
    project: Project,
    asset_id: str,
    track_id: str,
    timeline_start: float,
) -> Clip:
    """Crée un nouveau ``Clip`` à partir d'un ``MediaAsset`` et l'ajoute à la piste.

    Le clip utilise la totalité du média : ``source_in = 0.0``,
    ``source_out = MediaAsset.duration``. Il est créé activé, labellisé
    avec ``MediaAsset.name`` et sans texte. Son identifiant est généré
    de manière réellement unique (UUID) afin de permettre plusieurs
    insertions successives du même média sans collision.

    Cohérence asset / piste :

    - asset ``video`` → piste de type ``video`` ;
    - asset ``audio`` → piste de type ``audio`` ;
    - les pistes de sous-titres (``subtitle``) acceptent uniquement
      les médias ``subtitle`` (cas historique).

    Aucun contrôle n'est effectué à ce stade :

    - les chevauchements avec d'autres clips de la piste sont autorisés ;
    - pas de snapping, ni de ripple edit, ni de transition ;
    - pas de gestion d'undo/redo.

    Args:
        project: projet cible, modifié en place.
        asset_id: identifiant du ``MediaAsset`` à utiliser comme source.
        track_id: identifiant de la ``Track`` qui accueillera le clip.
        timeline_start: position de début sur la timeline (en secondes).

    Returns:
        Le nouveau ``Clip`` créé et ajouté à la piste.

    Raises:
        KeyError: si ``asset_id`` ou ``track_id`` est introuvable dans
            ``project``. Le message d'erreur mentionne l'identifiant
            inconnu pour faciliter le diagnostic.
        ValueError: si ``timeline_start`` est strictement négatif ou
            si le type du média ne correspond pas au type de la piste
            cible.
    """
    if timeline_start < 0.0:
        raise ValueError(
            f"Impossible d'ajouter un clip à un temps négatif "
            f"(timeline_start={timeline_start})."
        )
    asset = _find_asset(project, asset_id)
    track = find_track(project, track_id)
    _validate_track_asset_compatibility(asset, track)
    _ensure_track_editable(project, track)

    new_clip = Clip(
        id=f"clip-{uuid.uuid4().hex[:12]}",
        asset_id=asset.id,
        track_id=track.id,
        timeline_start=timeline_start,
        source_in=0.0,
        source_out=asset.duration,
        enabled=True,
        label=asset.name,
        text="",
    )
    track.clips.append(new_clip)
    return new_clip


# ---------------------------------------------------------------------------
# Helpers privés
# ---------------------------------------------------------------------------


# Correspondance type de média → types de pistes acceptés.
_ALLOWED_TRACK_TYPES_FOR_MEDIA = {
    "video": frozenset({"video"}),
    "audio": frozenset({"audio"}),
    "subtitle": frozenset({"subtitle"}),
}


def _validate_track_asset_compatibility(asset: MediaAsset, track: Track) -> None:
    """Lève ``ValueError`` si le média et la piste sont incompatibles."""
    allowed_track_types = _ALLOWED_TRACK_TYPES_FOR_MEDIA.get(asset.media_type)
    if allowed_track_types is None or track.type not in allowed_track_types:
        raise ValueError(
            f"Impossible d'ajouter un média '{asset.media_type}' sur la piste "
            f"'{track.id}' de type '{track.type}'."
        )


# ---------------------------------------------------------------------------
# Sous-titres (tâche 11)
# ---------------------------------------------------------------------------


from .subtitle_io import SubtitleCue  # noqa: E402  (import local pour cycle)
from .visual_effects import (  # noqa: E402  (import local pour cycle)
    ANIMATABLE_PROPERTIES,
    ClipTransform,
    TransformKeyframe,
)


_SUBTITLE_LABEL_MAX = 40


def _build_subtitle_label(text: str) -> str:
    """Génère un libellé court à partir du texte d'un sous-titre."""
    one_line = text.strip().replace("\n", " ")
    if not one_line:
        return ""
    if len(one_line) <= _SUBTITLE_LABEL_MAX:
        return one_line
    return one_line[: _SUBTITLE_LABEL_MAX - 1].rstrip() + "…"


def add_subtitle_clip(
    project: Project,
    text: str,
    timeline_start: float,
    duration: float,
    track_id: str = "S1",
) -> Clip:
    """Crée un nouveau ``Clip`` de sous-titre dans ``project``.

    Un ``MediaAsset`` technique (``media_type="subtitle"``) est créé
    automatiquement, puis associé au clip. Le clip est ajouté à la
    piste cible (par défaut ``S1``).

    Args:
        project: projet cible, modifié en place.
        text: texte du sous-titre (au moins un caractère non blanc).
        timeline_start: début sur la timeline (secondes, ``>= 0``).
        duration: durée du clip (secondes, ``> 0``).
        track_id: identifiant de la piste (doit être de type ``subtitle``).

    Returns:
        Le nouveau :class:`Clip` créé et ajouté à la piste.

    Raises:
        ValueError: si le texte est vide après strip, si la position
            est négative, si la durée n'est pas strictement positive,
            ou si la piste cible n'existe pas ou n'est pas de type
            ``subtitle``.
    """
    if not text or not text.strip():
        raise ValueError("Le texte d'un sous-titre ne peut pas être vide.")
    if timeline_start < 0.0:
        raise ValueError(
            f"Impossible d'ajouter un sous-titre à un temps négatif "
            f"(timeline_start={timeline_start})."
        )
    if duration <= 0.0:
        raise ValueError(
            f"La durée d'un sous-titre doit être strictement positive "
            f"(reçu : {duration})."
        )

    track = find_track(project, track_id)
    if track.type != "subtitle":
        raise ValueError(
            f"La piste '{track.id}' est de type '{track.type}', "
            "impossible d'y ajouter un sous-titre."
        )

    label = _build_subtitle_label(text)
    asset_id = f"asset-subtitle-{uuid.uuid4().hex[:12]}"
    asset = MediaAsset(
        id=asset_id,
        path="",
        name=label or "Sous-titre",
        duration=duration,
        width=0,
        height=0,
        fps=0.0,
        media_type="subtitle",
        has_audio=False,
    )
    project.media_assets.append(asset)

    clip = Clip(
        id=f"clip-{uuid.uuid4().hex[:12]}",
        asset_id=asset.id,
        track_id=track.id,
        timeline_start=timeline_start,
        source_in=0.0,
        source_out=duration,
        enabled=True,
        label=label,
        text=text.strip(),
    )
    track.clips.append(clip)
    return clip


def subtitle_cues_from_project(project: Project) -> list[SubtitleCue]:
    """Retourne les :class:`SubtitleCue` actifs du projet, triés.

    Seuls les clips activés des pistes de type ``subtitle`` sont
    retenus. Les pistes invisibles (``track.visible is False``) ne
    produisent aucun sous-titre. Les cues sont triés par
    ``(start, end)`` pour produire un SRT ordonné, prêt à être
    sauvegardé ou envoyé à FFmpeg.
    """
    cues: list[SubtitleCue] = []
    for track in project.tracks:
        if track.type != "subtitle":
            continue
        if not track.visible:
            continue
        for clip in track.clips:
            if not clip.enabled:
                continue
            text = clip.text.strip()
            if not text:
                continue
            cues.append(
                SubtitleCue(
                    start=float(clip.timeline_start),
                    end=float(clip.timeline_start + clip.duration),
                    text=text,
                )
            )
    return sorted(cues, key=lambda c: (c.start, c.end))


# ---------------------------------------------------------------------------
# Duplication, activation, ripple delete (tâche 12)
# ---------------------------------------------------------------------------


def duplicate_clip(
    project: Project,
    clip_id: str,
    timeline_start: float | None = None,
) -> Clip:
    """Duplique ``clip_id`` sur sa piste.

    Le clip dupliqué hérite de l'asset, des trims, de la durée, de
    l'état activé, du label et du texte du clip source. Son
    identifiant est nouveau (UUID).

    Si ``timeline_start`` est fourni, la copie est placée à cet
    endroit (doit être positif). Sinon, la copie est insérée
    immédiatement après la fin du clip source, sur la même piste.
    """
    source_track, source_index = _find_track_for_clip(project, clip_id)
    _ensure_track_editable(project, source_track)
    source_clip = source_track.clips[source_index]
    duration = source_clip.duration

    if timeline_start is None:
        new_start = float(source_clip.timeline_start + duration)
    else:
        if timeline_start < 0.0:
            raise ValueError(
                f"Impossible de dupliquer à un temps négatif "
                f"(timeline_start={timeline_start})."
            )
        new_start = float(timeline_start)

    duplicate = Clip(
        id=f"clip-{uuid.uuid4().hex[:12]}",
        asset_id=source_clip.asset_id,
        track_id=source_track.id,
        timeline_start=new_start,
        source_in=source_clip.source_in,
        source_out=source_clip.source_out,
        enabled=source_clip.enabled,
        label=source_clip.label,
        text=source_clip.text,
        transform=source_clip.transform,
        transform_keyframes=[
            TransformKeyframe(
                property_name=kf.property_name,
                time_seconds=kf.time_seconds,
                value=kf.value,
            )
            for kf in source_clip.transform_keyframes
        ],
    )
    source_track.clips.append(duplicate)
    return duplicate


def set_clip_enabled(
    project: Project,
    clip_id: str,
    enabled: bool,
) -> Clip:
    """Active ou désactive ``clip_id`` et retourne le clip modifié."""
    track, index = _find_track_for_clip(project, clip_id)
    clip = track.clips[index]
    clip.enabled = bool(enabled)
    return clip


def ripple_delete_clip(project: Project, clip_id: str) -> list[str]:
    """Supprime ``clip_id`` et ramène à gauche tous les clips suivants.

    Tous les clips (vidéo, audio, sous-titres) dont le début est
    strictement postérieur à la fin du clip supprimé sont déplacés
    vers la gauche de ``delta``, où ``delta`` est la durée du clip
    supprimé. Aucun clip ne se retrouve avec une position négative.

    Returns:
        Liste des identifiants effectivement déplacés.

    Les assets de la bibliothèque ne sont jamais supprimés.
    """
    track, index = _find_track_for_clip(project, clip_id)
    _ensure_track_editable(project, track)
    deleted_clip = track.clips.pop(index)
    delta = float(deleted_clip.duration)
    boundary = float(deleted_clip.timeline_start + deleted_clip.duration)
    moved: list[str] = []
    for other_track in project.tracks:
        for other in list(other_track.clips):
            if other is deleted_clip:
                continue
            if other.timeline_start >= boundary - 1e-9:
                other.timeline_start = max(0.0, other.timeline_start - delta)
                moved.append(other.id)
    return moved


# ---------------------------------------------------------------------------
# Snapping magnétique (tâche 12)
# ---------------------------------------------------------------------------


def snap_timeline_position(
    project: Project,
    proposed_position: float,
    threshold_seconds: float,
    excluded_clip_id: str | None = None,
    playhead_seconds: float | None = None,
) -> float:
    """Accroche ``proposed_position`` au candidat le plus proche.

    Candidats considérés :

    - ``0.0`` (début de timeline) ;
    - ``playhead_seconds`` si fourni ;
    - début et fin de chaque clip, sauf ceux du clip ``excluded_clip_id``.

    Returns:
        ``proposed_position`` s'il n'existe aucun candidat dans le seuil
        ``threshold_seconds`` ; sinon la valeur du candidat le plus
        proche.
    """
    if proposed_position < 0.0:
        proposed_position = 0.0
    if threshold_seconds <= 0.0:
        return proposed_position

    candidates: list[float] = [0.0]
    if playhead_seconds is not None and playhead_seconds >= 0.0:
        candidates.append(float(playhead_seconds))

    for track in project.tracks:
        for clip in track.clips:
            if excluded_clip_id is not None and clip.id == excluded_clip_id:
                continue
            start = float(clip.timeline_start)
            end = float(clip.timeline_start + clip.duration)
            candidates.append(start)
            candidates.append(end)

    best = proposed_position
    best_distance = threshold_seconds
    for candidate in candidates:
        distance = abs(candidate - proposed_position)
        if distance <= best_distance:
            best = candidate
            best_distance = distance
    return best


# ---------------------------------------------------------------------------
# Transformations visuelles et images-clés (tâche 13)
# ---------------------------------------------------------------------------


def _require_video_clip(project: Project, clip_id: str) -> Clip:
    """Retourne le clip après avoir vérifié qu'il appartient à une piste vidéo."""
    track, index = _find_track_for_clip(project, clip_id)
    if track.type != "video":
        raise ValueError(
            f"Le clip '{clip_id}' est sur une piste '{track.type}' ; "
            "les transformations visuelles ne s'appliquent qu'aux pistes vidéo."
        )
    return track.clips[index]


def set_clip_transform(
    project: Project,
    clip_id: str,
    transform: ClipTransform,
) -> Clip:
    """Remplace la transform de base d'un clip vidéo par ``transform``.

    Les keyframes existantes sont conservées intactes.
    """
    clip = _require_video_clip(project, clip_id)
    clip.transform = transform
    return clip


def set_transform_keyframe(
    project: Project,
    clip_id: str,
    property_name: str,
    clip_local_time: float,
    value: float,
) -> Clip:
    """Ajoute (ou remplace) une image-clé pour ``property_name`` à ``clip_local_time``.

    Les images-clés sont conservées triées par
    ``(property_name, time_seconds)``. Si une image-clé existe déjà
    pour le même couple, sa valeur est écrasée.
    """
    clip = _require_video_clip(project, clip_id)
    if property_name not in ANIMATABLE_PROPERTIES:
        raise ValueError(f"Propriété inconnue : {property_name!r}.")
    duration = clip.duration
    if duration <= 0.0:
        raise ValueError(
            "Le clip doit avoir une durée strictement positive pour "
            "héberger des images-clés."
        )
    if clip_local_time < 0.0:
        raise ValueError(
            f"time_seconds doit être positif ou nul (reçu : {clip_local_time})."
        )
    if clip_local_time > duration:
        raise ValueError(
            f"L'image-clé ({clip_local_time}s) dépasse la durée du clip "
            f"({duration}s)."
        )
    new_kf = TransformKeyframe(
        property_name=property_name,
        time_seconds=float(clip_local_time),
        value=float(value),
    )
    # Filtre les keyframes existantes : remplace si même couple.
    kept = [
        kf
        for kf in clip.transform_keyframes
        if not (
            kf.property_name == new_kf.property_name
            and abs(kf.time_seconds - new_kf.time_seconds) < 1e-9
        )
    ]
    kept.append(new_kf)
    kept.sort(key=lambda kf: (kf.property_name, kf.time_seconds))
    clip.transform_keyframes = kept
    return clip


def remove_transform_keyframe(
    project: Project,
    clip_id: str,
    property_name: str,
    clip_local_time: float,
) -> Clip:
    """Retire l'image-clé ``(property_name, clip_local_time)`` si elle existe."""
    clip = _require_video_clip(project, clip_id)
    clip.transform_keyframes = [
        kf
        for kf in clip.transform_keyframes
        if not (
            kf.property_name == property_name
            and abs(kf.time_seconds - clip_local_time) < 1e-9
        )
    ]
    return clip


def reset_clip_transform(
    project: Project,
    clip_id: str,
) -> Clip:
    """Restaure la transform identité et supprime toutes les keyframes du clip."""
    clip = _require_video_clip(project, clip_id)
    clip.transform = ClipTransform()
    clip.transform_keyframes = []
    return clip


def clip_keyframes_remain_valid_after_trim(
    clip: Clip,
    *,
    old_source_in: float,
    old_source_out: float,
    new_source_in: float,
    new_source_out: float,
) -> list[TransformKeyframe]:
    """Filtre les keyframes devenues invalides après un trim.

    - Trim gauche : ``source_in`` augmente, donc le temps local 0
      correspond à un point de source plus avancé. Les keyframes
      situées au-delà du nouveau ``source_in`` doivent être décalées
      de ``new_source_in - old_source_in`` pour rester cohérentes.
    - Trim droit : ``source_out`` diminue, donc la durée effective
      du clip diminue. Les keyframes situées au-delà de la nouvelle
      durée sont supprimées.
    """
    if new_source_in > new_source_out:
        raise ValueError("Les nouvelles bornes de source sont invalides.")

    delta_left = float(new_source_in - old_source_in)
    new_duration = float(new_source_out - new_source_in)
    new_kfs: list[TransformKeyframe] = []
    for kf in clip.transform_keyframes:
        # Conversion temps local : on conserve la position dans le
        # nouveau clip, en supprimant ce qui dépasse la durée.
        new_local = float(kf.time_seconds) + delta_left
        if new_local < 0.0:
            continue
        if new_local > new_duration + 1e-6:
            continue
        new_kfs.append(
            TransformKeyframe(
                property_name=kf.property_name,
                time_seconds=new_local,
                value=float(kf.value),
            )
        )
    new_kfs.sort(key=lambda kf: (kf.property_name, kf.time_seconds))
    return new_kfs


def apply_clip_transform_on_move(
    clip: Clip,
    timeline_delta: float,
) -> None:
    """Déplace les keyframes d'un clip en même temps que le clip lui-même.

    Les temps des keyframes sont relatifs au clip et ne sont pas
    affectés par un simple déplacement timeline. Cette fonction est
    un no-op conservé pour la lisibilité des autres opérations.
    """
    _ = timeline_delta  # noqa: F841
    return None  # Les keyframes sont locales, rien à faire.


def apply_clip_transform_on_trim(
    clip: Clip,
    *,
    old_source_in: float,
    old_source_out: float,
    new_source_in: float,
    new_source_out: float,
) -> None:
    """Filtre les keyframes devenues invalides suite à un trim, en place."""
    clip.transform_keyframes = clip_keyframes_remain_valid_after_trim(
        clip,
        old_source_in=old_source_in,
        old_source_out=old_source_out,
        new_source_in=new_source_in,
        new_source_out=new_source_out,
    )


def duplicate_clip_preserving_transform(
    project: Project,
    clip_id: str,
) -> Clip:
    """Duplique un clip en propageant ``transform`` et ``transform_keyframes``.

    Variante de :func:`duplicate_clip` qui copie également la
    transform et les keyframes. Les clips audio / sous-titres sont
    copiés avec un transform identité (compatibilité).
    """
    source_track, source_index = _find_track_for_clip(project, clip_id)
    source_clip = source_track.clips[source_index]
    duration = source_clip.duration

    # Clonage profond des keyframes (les TransformKeyframe sont frozen,
    # donc une shallow copy suffit).
    new_keyframes = [
        TransformKeyframe(
            property_name=kf.property_name,
            time_seconds=kf.time_seconds,
            value=kf.value,
        )
        for kf in source_clip.transform_keyframes
    ]

    duplicate = Clip(
        id=f"clip-{uuid.uuid4().hex[:12]}",
        asset_id=source_clip.asset_id,
        track_id=source_track.id,
        timeline_start=source_clip.timeline_start + duration,
        source_in=source_clip.source_in,
        source_out=source_clip.source_out,
        enabled=source_clip.enabled,
        label=source_clip.label,
        text=source_clip.text,
        transform=source_clip.transform,
        transform_keyframes=new_keyframes,
    )
    source_track.clips.append(duplicate)
    return duplicate
