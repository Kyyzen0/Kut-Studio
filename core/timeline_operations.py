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
from .time_remapping import (
    FreezeFrameMode,
    TimeRemapping,
    clamp_speed,
    create_freeze_frame,
    timeline_to_source_time,
    validate_time_remapping,
)
from .transitions import remove_transitions_for_clips


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


def _source_bounds(project: Project, clip: Clip) -> tuple[str, float]:
    """``(libellé, durée)`` de la source d'un clip : média ou séquence imbriquée.

    Une séquence imbriquée introuvable (supprimée) ne permet pas d'étendre
    le clip : la limite est alors son ``source_out`` actuel.

    Raises:
        KeyError: si le média d'un clip classique est introuvable.
    """
    if clip.sequence_id:
        sequence = project.get_sequence(clip.sequence_id)
        if sequence is None:
            return "la séquence (introuvable)", float(clip.source_out)
        return f"la séquence '{sequence.name}'", float(sequence.duration)
    asset = _find_asset(project, clip.asset_id)
    return f"du média '{asset.name}'", float(asset.duration)


def _clip_media_type(project: Project, clip: Clip) -> str:
    """Type de source pour la validation du remappage (séquence = vidéo)."""
    if clip.sequence_id:
        return "video"
    return _find_asset(project, clip.asset_id).media_type


def _validate_track_clip_compatibility(project: Project, clip: Clip, track: Track) -> None:
    """Le clip peut-il aller sur ``track`` ? (média ou séquence imbriquée)."""
    if clip.sequence_id:
        if track.type not in {"video", "audio"}:
            raise ValueError(
                f"Une séquence imbriquée ne peut aller que sur une piste vidéo ou "
                f"audio (piste '{track.id}' de type '{track.type}')."
            )
        return
    _validate_track_asset_compatibility(_find_asset(project, clip.asset_id), track)


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
    doit être supérieur ou égal à la valeur actuelle. La fin du clip sur
    la timeline ne bouge pas. Le point de source consommé est ``delta *
    vitesse`` : ``source_in`` avance, ou ``source_out`` recule si le clip
    est lu à l'envers ; un arrêt sur image raccourcit sa durée.

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
    delta = new_timeline_start - clip.timeline_start  # secondes de timeline
    if delta < 0.0:
        raise ValueError(
            f"Le trim gauche ne peut que reculer la position de début "
            f"vers l'avant : timeline_start actuel = {clip.timeline_start}, "
            f"demandé = {new_timeline_start}."
        )
    new_duration = clip.duration - delta
    if new_duration <= 1e-9:
        raise ValueError(
            f"Le trim gauche produirait une durée nulle ou négative pour "
            f"le clip '{clip_id}' (durée={new_duration})."
        )
    if clip.is_frozen:
        # Un arrêt sur image garde ses bornes source : seule sa durée change.
        _set_freeze_duration(clip, new_duration)
    elif clip.is_reversed:
        # Lu à l'envers, le début de la timeline montre la fin de la source.
        clip.source_out -= delta * clip.speed
    else:
        clip.source_in += delta * clip.speed
    clip.timeline_start = new_timeline_start
    apply_clip_transform_on_trim(clip, start_offset=delta, new_duration=new_duration)
    return clip


def trim_clip_right(
    project: Project, clip_id: str, new_timeline_end: float
) -> Clip:
    """Rogne ou étend la partie droite du clip.

    ``timeline_start`` ne change pas. La durée demandée est convertie en
    durée de source selon la vitesse : ``source_out`` bouge (``source_in``
    si le clip est lu à l'envers) ; un arrêt sur image change sa durée.
    La source ne peut pas dépasser la durée du ``MediaAsset`` référencé.

    Raises:
        KeyError: si ``clip_id`` ou le ``MediaAsset`` associé est introuvable.
        ValueError: si ``new_timeline_end`` produit une durée nulle ou
            négative, ou si la nouvelle ``source_out`` dépasse la durée
            du média sous-jacent.
    """
    track, index = _find_track_for_clip(project, clip_id)
    _ensure_track_editable(project, track)
    clip = track.clips[index]
    source_name, source_limit = _source_bounds(project, clip)

    if new_timeline_end <= clip.timeline_start:
        raise ValueError(
            f"Le trim droit produirait une durée nulle ou négative pour "
            f"le clip '{clip_id}' (timeline_start={clip.timeline_start}, "
            f"timeline_end demandé={new_timeline_end})."
        )
    new_duration = new_timeline_end - clip.timeline_start
    if clip.is_frozen:
        # Un arrêt sur image garde ses bornes source : seule sa durée change.
        _set_freeze_duration(clip, new_duration)
        apply_clip_transform_on_trim(clip, start_offset=0.0, new_duration=new_duration)
        return clip

    # Durée de timeline -> durée de source : la vitesse change le rapport entre les deux.
    new_source_span = new_duration * clip.speed
    if clip.is_reversed:
        # Lu à l'envers, la fin de la timeline montre le début de la source.
        new_source_in = clip.source_out - new_source_span
        if new_source_in < -1e-9:
            raise ValueError(
                f"Le trim droit dépasserait le début de {source_name} "
                f"(source_in={new_source_in})."
            )
        new_source_in = max(0.0, new_source_in)
        new_source_out = clip.source_out
    else:
        new_source_in = clip.source_in
        new_source_out = clip.source_in + new_source_span
        # Raccourcir reste toujours permis, même pour un clip imbriqué qui
        # déborde déjà d'une séquence source raccourcie.
        if new_source_out > max(source_limit, clip.source_out) + 1e-9:
            raise ValueError(
                f"Le trim droit dépasserait la durée de {source_name} "
                f"(source={source_limit}s, source_out={new_source_out})."
            )
    if new_source_out - new_source_in <= 1e-9:
        raise ValueError(
            f"Le trim droit produirait une durée nulle ou négative pour "
            f"le clip '{clip_id}' (source_in={new_source_in}, "
            f"source_out={new_source_out})."
        )
    clip.source_in = new_source_in
    clip.source_out = new_source_out
    apply_clip_transform_on_trim(clip, start_offset=0.0, new_duration=new_duration)
    return clip


def _set_freeze_duration(clip: Clip, duration: float) -> None:
    """Nouvelle durée de timeline d'un arrêt sur image (``TimeRemapping`` est immuable)."""
    from dataclasses import replace

    clip.time_remapping = replace(clip.time_remapping, freeze_duration=float(duration))


# ---------------------------------------------------------------------------
# Coupe
# ---------------------------------------------------------------------------


def _carried_properties(clip: Clip, *, copy: bool = False) -> dict:
    """Propriétés qu'une coupe ou une duplication doit conserver.

    Effets, effets audio, étalonnage, calque graphique, composition, style
    texte et référence de séquence imbriquée. ``copy`` duplique les listes
    modifiables pour que les deux clips restent indépendants.
    """
    from copy import deepcopy

    return {
        "effects": deepcopy(clip.effects) if copy else list(clip.effects),
        "audio_effects": deepcopy(clip.audio_effects) if copy else list(clip.audio_effects),
        "color_grade": clip.color_grade,
        "graphic": clip.graphic,
        "compositing": clip.compositing,
        "text_style": clip.text_style,
        "sequence_id": clip.sequence_id,
        # Tracking : données en temps source, valables pour les deux moitiés.
        "tracking": getattr(clip, "tracking", None),
    }


def _split_time_remapping(
    clip: Clip, cut_local_time: float
) -> tuple[TimeRemapping, TimeRemapping]:
    """Construit les remappages des deux moitiés d'une coupe.

    Un remappage de vitesse ou de lecture inverse peut être partagé car il
    est immuable. Un arrêt sur image, en revanche, porte sa durée : chaque
    moitié doit donc recevoir sa propre durée pour ne pas doubler le plan.
    """
    remapping = clip.time_remapping
    if remapping.freeze_mode != FreezeFrameMode.FREEZE:
        return remapping, remapping

    remaining_duration = clip.duration - cut_local_time
    return (
        TimeRemapping(
            speed=remapping.speed,
            reverse=remapping.reverse,
            freeze_mode=remapping.freeze_mode,
            freeze_source_time=remapping.freeze_source_time,
            freeze_duration=cut_local_time,
        ),
        TimeRemapping(
            speed=remapping.speed,
            reverse=remapping.reverse,
            freeze_mode=remapping.freeze_mode,
            freeze_source_time=remapping.freeze_source_time,
            freeze_duration=remaining_duration,
        ),
    )


def _split_transform_keyframes(
    clip: Clip, cut_local_time: float
) -> tuple[list[TransformKeyframe], list[TransformKeyframe]]:
    """Répartit les keyframes sans changer l'animation de part et d'autre de la coupe."""
    return split_transform_keyframes(
        clip.transform, clip.transform_keyframes, cut_local_time, clip.duration
    )


def _split_fades(clip: Clip, cut_local_time: float) -> tuple[tuple[float, float], tuple[float, float]]:
    """Conserve les fondus aux extrémités externes, sans créer de fondu interne."""
    left_duration = cut_local_time
    right_duration = clip.duration - cut_local_time
    return (
        (min(clip.fade_in, left_duration), 0.0),
        (0.0, min(clip.fade_out, right_duration)),
    )


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

    cut_local_time = cut_timeline_position - clip.timeline_start
    left_remapping, right_remapping = _split_time_remapping(clip, cut_local_time)
    left_keyframes, right_keyframes = _split_transform_keyframes(clip, cut_local_time)
    from .animation_targets import split_animation

    left_animation, right_animation = split_animation(clip.animation, cut_local_time)
    (left_fade_in, left_fade_out), (right_fade_in, right_fade_out) = _split_fades(
        clip, cut_local_time
    )

    if clip.time_remapping.freeze_mode == FreezeFrameMode.FREEZE:
        # Un freeze lit toujours la même image : ses bornes source restent
        # intactes, seule sa durée timeline est répartie.
        left_source_in, left_source_out = clip.source_in, clip.source_out
        right_source_in, right_source_out = clip.source_in, clip.source_out
    else:
        cut_source_time = timeline_to_source_time(
            cut_local_time,
            clip.source_in,
            clip.source_out,
            clip.time_remapping.speed,
            clip.time_remapping.reverse,
            clip.time_remapping.freeze_mode,
            clip.time_remapping.freeze_source_time,
        )
        if clip.time_remapping.reverse:
            left_source_in, left_source_out = cut_source_time, clip.source_out
            right_source_in, right_source_out = clip.source_in, cut_source_time
        else:
            left_source_in, left_source_out = clip.source_in, cut_source_time
            right_source_in, right_source_out = cut_source_time, clip.source_out

    left_clip = Clip(
        id=clip.id,
        asset_id=clip.asset_id,
        track_id=clip.track_id,
        timeline_start=clip.timeline_start,
        source_in=left_source_in,
        source_out=left_source_out,
        enabled=clip.enabled,
        label=clip.label,
        text=clip.text,
        transform=clip.transform,
        transform_keyframes=left_keyframes,
        gain_db=clip.gain_db,
        pan=clip.pan,
        fade_in=left_fade_in,
        fade_out=left_fade_out,
        time_remapping=left_remapping,
        animation=left_animation,
        **_carried_properties(clip),
    )
    right_clip = Clip(
        id=right_id,
        asset_id=clip.asset_id,
        track_id=clip.track_id,
        timeline_start=cut_timeline_position,
        source_in=right_source_in,
        source_out=right_source_out,
        enabled=clip.enabled,
        label=clip.label,
        text=clip.text,
        transform=clip.transform,
        transform_keyframes=right_keyframes,
        gain_db=clip.gain_db,
        pan=clip.pan,
        fade_in=right_fade_in,
        fade_out=right_fade_out,
        time_remapping=right_remapping,
        animation=right_animation,
        **_carried_properties(clip, copy=True),
    )

    track.clips[index : index + 1] = [left_clip, right_clip]
    remove_transitions_for_clips(project, {clip_id})
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
    remove_transitions_for_clips(project, {clip_id})
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
    TRANSFORM_PROPERTY_NAMES,
    ClipTransform,
    TransformKeyframe,
    copy_keyframe,
    evaluate_transform,
    retime_transform_keyframes,
    split_transform_keyframes,
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
        transform_keyframes=[copy_keyframe(kf) for kf in source_clip.transform_keyframes],
        time_remapping=source_clip.time_remapping,
        gain_db=source_clip.gain_db,
        pan=source_clip.pan,
        fade_in=source_clip.fade_in,
        fade_out=source_clip.fade_out,
        animation=list(source_clip.animation),
        # Calque graphique, effets, composition, style… : une copie complète.
        **_carried_properties(source_clip, copy=True),
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
    """Retourne un clip visuel (vidéo ou graphique).

    Le nom historique est conservé pour éviter de casser les imports
    externes, mais les calques ``graphics`` partagent désormais le même
    modèle de transform et d'images-clés que la vidéo.
    """
    track, index = _find_track_for_clip(project, clip_id)
    if track.type not in {"video", "graphics"}:
        raise ValueError(
            f"Le clip '{clip_id}' est sur une piste '{track.type}' ; "
            "les transformations visuelles ne s'appliquent qu'aux pistes "
            "vidéo ou graphiques."
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
    if property_name not in TRANSFORM_PROPERTY_NAMES:
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
    # Validation (propriété, bornes, temps) avant toute modification.
    TransformKeyframe(property_name=property_name, time_seconds=float(clip_local_time), value=float(value))
    # Un keyframe existant à cet instant garde son interpolation, ses
    # tangentes et son identifiant : seule sa valeur change.
    from .keyframe_editing import add_keyframe

    add_keyframe(project, clip_id, property_name, float(clip_local_time), float(value))
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
    start_offset: float,
    new_duration: float,
) -> list[TransformKeyframe]:
    """Filtre les keyframes devenues invalides après un trim.

    Les temps des keyframes sont locaux au clip, donc en secondes de
    **timeline** (jamais de source : la vitesse et le reverse n'y changent rien).

    - Trim gauche : le temps local 0 avance de ``start_offset`` ; les keyframes
      sont décalées d'autant pour que l'animation visible ne bouge pas.
    - Trim droit : les keyframes au-delà de ``new_duration`` sont supprimées.
    """
    if new_duration < 0.0:
        raise ValueError("La nouvelle durée du clip est invalide.")

    return retime_transform_keyframes(
        clip.transform_keyframes,
        start_offset=float(start_offset),
        new_duration=float(new_duration),
    )


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
    start_offset: float,
    new_duration: float,
) -> None:
    """Retime les keyframes (transform et animation) après un trim, en place.

    ``start_offset`` et ``new_duration`` sont en secondes de timeline.
    """
    clip.transform_keyframes = clip_keyframes_remain_valid_after_trim(
        clip, start_offset=start_offset, new_duration=new_duration
    )
    if clip.animation:
        from .animation_targets import retime_animation

        clip.animation = retime_animation(
            clip.animation,
            start_offset=float(start_offset),
            new_duration=float(new_duration),
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
    new_keyframes = [copy_keyframe(kf) for kf in source_clip.transform_keyframes]

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
        time_remapping=source_clip.time_remapping,
    )
    source_track.clips.append(duplicate)
    return duplicate


# ---------------------------------------------------------------------------
# Remappage temporel (tâche 18)
# ---------------------------------------------------------------------------

def set_clip_speed(
    project: Project, clip_id: str, speed: float
) -> Clip:
    """Modifie la vitesse de lecture d'un clip.
    
    Args:
        project: Le projet à modifier.
        clip_id: L'identifiant du clip à modifier.
        speed: La nouvelle vitesse (sera clampée entre MIN_SPEED et MAX_SPEED).
    
    Returns:
        Le clip modifié.
    
    Raises:
        KeyError: Si le clip n'est pas trouvé.
        ValueError: Si la vitesse est invalide.
    """
    track, index = _find_track_for_clip(project, clip_id)
    clip = track.clips[index]
    
    # Valider la vitesse
    clamped_speed = clamp_speed(speed)
    
    # Valider le time remapping
    new_time_remapping = TimeRemapping(
        speed=clamped_speed,
        reverse=clip.time_remapping.reverse,
        freeze_mode=clip.time_remapping.freeze_mode,
        freeze_source_time=clip.time_remapping.freeze_source_time,
        freeze_duration=clip.time_remapping.freeze_duration,
    )
    
    errors = validate_time_remapping(
        speed=new_time_remapping.speed,
        reverse=new_time_remapping.reverse,
        freeze_mode=new_time_remapping.freeze_mode,
        freeze_source_time=new_time_remapping.freeze_source_time,
        freeze_duration=new_time_remapping.freeze_duration,
        source_in=clip.source_in,
        source_out=clip.source_out,
        media_type=_clip_media_type(project, clip),
    )
    if errors:
        raise ValueError(f"Vitesse invalide: {'; '.join(errors)}")
    
    clip.time_remapping = new_time_remapping
    return clip


def set_clip_reverse(
    project: Project, clip_id: str, reverse: bool
) -> Clip:
    """Modifie le mode reverse d'un clip.
    
    Args:
        project: Le projet à modifier.
        clip_id: L'identifiant du clip à modifier.
        reverse: Le nouvel état reverse.
    
    Returns:
        Le clip modifié.
    
    Raises:
        KeyError: Si le clip n'est pas trouvé.
        ValueError: Si le reverse est invalide (ex: durée trop longue).
    """
    track, index = _find_track_for_clip(project, clip_id)
    clip = track.clips[index]
    media_type = _clip_media_type(project, clip)
    
    # Valider le reverse
    new_time_remapping = TimeRemapping(
        speed=clip.time_remapping.speed,
        reverse=reverse,
        freeze_mode=clip.time_remapping.freeze_mode,
        freeze_source_time=clip.time_remapping.freeze_source_time,
        freeze_duration=clip.time_remapping.freeze_duration,
    )
    
    errors = validate_time_remapping(
        speed=new_time_remapping.speed,
        reverse=new_time_remapping.reverse,
        freeze_mode=new_time_remapping.freeze_mode,
        freeze_source_time=new_time_remapping.freeze_source_time,
        freeze_duration=new_time_remapping.freeze_duration,
        source_in=clip.source_in,
        source_out=clip.source_out,
        media_type=media_type,
    )
    if errors:
        raise ValueError(f"Reverse invalide: {'; '.join(errors)}")
    
    clip.time_remapping = new_time_remapping
    return clip


def set_clip_freeze_frame(
    project: Project, clip_id: str, freeze_source_time: float, freeze_duration: float
) -> Clip:
    """Active le mode arrêt sur image pour un clip.
    
    Args:
        project: Le projet à modifier.
        clip_id: L'identifiant du clip à modifier.
        freeze_source_time: L'instant source pour l'arrêt sur image.
        freeze_duration: La durée de l'arrêt sur image sur la timeline.
    
    Returns:
        Le clip modifié.
    
    Raises:
        KeyError: Si le clip n'est pas trouvé.
        ValueError: Si le freeze frame est invalide.
    """
    track, index = _find_track_for_clip(project, clip_id)
    clip = track.clips[index]
    media_type = _clip_media_type(project, clip)
    
    # Créer le time remapping pour le freeze frame
    new_time_remapping = create_freeze_frame(
        source_in=clip.source_in,
        source_out=clip.source_out,
        freeze_source_time=freeze_source_time,
        freeze_duration=freeze_duration,
    )
    
    # Valider
    errors = validate_time_remapping(
        speed=new_time_remapping.speed,
        reverse=new_time_remapping.reverse,
        freeze_mode=new_time_remapping.freeze_mode,
        freeze_source_time=new_time_remapping.freeze_source_time,
        freeze_duration=new_time_remapping.freeze_duration,
        source_in=clip.source_in,
        source_out=clip.source_out,
        media_type=media_type,
    )
    if errors:
        raise ValueError(f"Arrêt sur image invalide: {'; '.join(errors)}")
    
    clip.time_remapping = new_time_remapping
    return clip


def remove_clip_freeze_frame(
    project: Project, clip_id: str
) -> Clip:
    """Désactive le mode arrêt sur image pour un clip.
    
    Args:
        project: Le projet à modifier.
        clip_id: L'identifiant du clip à modifier.
    
    Returns:
        Le clip modifié.
    """
    track, index = _find_track_for_clip(project, clip_id)
    clip = track.clips[index]
    
    # Réinitialiser le time remapping
    clip.time_remapping = TimeRemapping.default()
    return clip


def set_clip_freeze_duration(
    project: Project, clip_id: str, freeze_duration: float
) -> Clip:
    """Modifie la durée d'un arrêt sur image.
    
    Args:
        project: Le projet à modifier.
        clip_id: L'identifiant du clip à modifier.
        freeze_duration: La nouvelle durée (doit être > 0).
    
    Returns:
        Le clip modifié.
    
    Raises:
        KeyError: Si le clip n'est pas trouvé.
        ValueError: Si la durée est invalide.
    """
    track, index = _find_track_for_clip(project, clip_id)
    clip = track.clips[index]
    media_type = _clip_media_type(project, clip)
    
    # Mettre à jour la durée
    new_time_remapping = TimeRemapping(
        speed=clip.time_remapping.speed,
        reverse=clip.time_remapping.reverse,
        freeze_mode=clip.time_remapping.freeze_mode,
        freeze_source_time=clip.time_remapping.freeze_source_time,
        freeze_duration=freeze_duration,
    )
    
    # Valider
    errors = validate_time_remapping(
        speed=new_time_remapping.speed,
        reverse=new_time_remapping.reverse,
        freeze_mode=new_time_remapping.freeze_mode,
        freeze_source_time=new_time_remapping.freeze_source_time,
        freeze_duration=new_time_remapping.freeze_duration,
        source_in=clip.source_in,
        source_out=clip.source_out,
        media_type=media_type,
    )
    if errors:
        raise ValueError(f"Durée d'arrêt sur image invalide: {'; '.join(errors)}")
    
    clip.time_remapping = new_time_remapping
    return clip


def reset_clip_time_remapping(
    project: Project, clip_id: str
) -> Clip:
    """Réinitialise le remappage temporel d'un clip aux valeurs par défaut.
    
    Args:
        project: Le projet à modifier.
        clip_id: L'identifiant du clip à modifier.
    
    Returns:
        Le clip modifié.
    """
    track, index = _find_track_for_clip(project, clip_id)
    clip = track.clips[index]
    
    clip.time_remapping = TimeRemapping.default()
    return clip
