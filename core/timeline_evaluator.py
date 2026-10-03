"""Évaluation pure de la timeline de Kut-Studio.

Ce module détermine, à partir des dataclasses métier ``Project``, ``Track``
et ``Clip`` définies dans :mod:`core.project_model`, quels clips sont
actifs à un instant donné et expose une vue immuable de chacun d'eux via
la dataclass :class:`ActiveClip`.

L'objectif est de fournir une couche de calcul :

- sans dépendance à ``PySide6`` (donc utilisable hors d'un contexte Qt) ;
- sans dépendance à ``FFmpeg`` (donc testable sans binaire externe) ;
- sans aucune interaction avec l'interface graphique.

Elle sera consommée ultérieurement par l'aperçu vidéo et le moteur
d'export pour rendre leur comportement réellement fidèle à la timeline
(positions, trous, trims, pistes V1/V2, clips désactivés).

Séquences imbriquées : un clip qui référence une séquence est remplacé
par les clips actifs **de cette séquence** à l'instant correspondant
(vitesse, reverse et arrêt sur image du clip pris en compte), récursivement.
Les entrées obtenues portent ``root_clip_id`` (le clip imbriqué de la
séquence évaluée) et ``nested_path``. Les cycles, références inconnues et
imbrications trop profondes produisent une évaluation vide pour le clip
fautif : jamais de récursion infinie.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .project_model import Clip, MediaAsset, Project, Sequence


# ---------------------------------------------------------------------------
# Vue immuable d'un clip actif
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ActiveClip:
    """Vue immuable d'un ``Clip`` actif à un instant donné de la timeline.

    Cette dataclass est totalement indépendante des structures métier
    modifiables (``Clip``, ``Track``, ``Project``) : elle capture toutes
    les informations nécessaires au rendu ou à l'export pour un clip
    actif, sans permettre de muter la timeline sous-jacente.

    Attributes:
        clip_id: Identifiant unique du clip dans le projet.
        asset_id: Identifiant du ``MediaAsset`` source du clip.
        track_id: Identifiant de la ``Track`` portant le clip.
        track_type: Type logique de la piste (``"video"``, ``"audio"``...).
        track_index: Position (0-based) de la piste dans ``project.tracks``.
        source_path: Chemin résolu vers le fichier média sur le disque.
        source_time: Position correspondante dans le média source, en
            secondes (``clip.source_in + (time - clip.timeline_start)``).
        timeline_start: Début du clip sur la timeline, en secondes.
        timeline_end: Fin du clip sur la timeline, en secondes
            (``timeline_start + clip.duration``).
        text: Texte porté par le clip (sous-titres). Vide pour les
            autres types de clips.
        root_clip_id: Pour un clip vu à travers une séquence imbriquée,
            identifiant du clip imbriqué de la séquence évaluée (vide
            sinon). ``track_id`` / ``track_index`` sont alors ceux de ce
            clip racine, et ``timeline_start`` / ``timeline_end`` sont
            exprimés dans le temps de la séquence évaluée.
        nested_path: Chaîne des clips imbriqués traversés (racine d'abord).
        sequence_id: Séquence qui contient réellement le clip (vide pour
            la séquence évaluée).
        silent: Image à jouer **sans son** : la politique audio d'une source
            Multicam écarte le son de cette piste (ex. enregistreur externe
            retenu à la place du son de la caméra). Vrai uniquement pour une
            entrée vidéo vue à travers une source Multicam.
    """

    clip_id: str
    asset_id: str
    track_id: str
    track_type: str
    track_index: int
    source_path: str
    source_time: float
    timeline_start: float
    timeline_end: float
    text: str = ""
    root_clip_id: str = ""
    nested_path: tuple[str, ...] = ()
    sequence_id: str = ""
    silent: bool = False

    @property
    def owner_clip_id(self) -> str:
        """Clip de la séquence évaluée qui produit cette entrée."""
        return self.root_clip_id or self.clip_id

    @property
    def is_nested(self) -> bool:
        return bool(self.root_clip_id)


# ---------------------------------------------------------------------------
# Helpers privés
# ---------------------------------------------------------------------------


def _find_asset(project: Project, asset_id: str) -> MediaAsset:
    """Retourne le ``MediaAsset`` correspondant à ``asset_id``.

    Raises:
        KeyError: si aucun média du projet ne porte cet identifiant.
    """
    for asset in project.media_assets:
        if asset.id == asset_id:
            return asset
    raise KeyError(
        f"Média '{asset_id}' introuvable dans le projet '{project.name}'."
    )


def _is_active(clip: Clip, time_seconds: float) -> bool:
    """Vrai si ``clip`` couvre l'instant ``time_seconds``.

    Le demi-ouvert ``[timeline_start, timeline_end)`` est conservé tel
    que décrit dans la spécification : un clip se termine exactement
    ``timeline_start + duration`` et n'est plus actif à cet instant.
    """
    return clip.timeline_start <= time_seconds < clip.timeline_start + clip.duration


def _build_active_clip(
    clip: Clip,
    track_index: int,
    track_type: str,
    source_path: str,
    time_seconds: float,
) -> ActiveClip:
    """Construit un :class:`ActiveClip` à partir d'un ``Clip`` et du temps."""
    from .time_remapping import timeline_to_source_time
    
    # Calculer le temps source en tenant compte du time remapping
    local_timeline_time = time_seconds - clip.timeline_start
    try:
        source_time = timeline_to_source_time(
            timeline_time=local_timeline_time,
            source_in=clip.source_in,
            source_out=clip.source_out,
            speed=clip.time_remapping.speed,
            reverse=clip.time_remapping.reverse,
            freeze_mode=clip.time_remapping.freeze_mode,
            freeze_source_time=clip.time_remapping.freeze_source_time,
        )
    except ValueError:
        # Si la conversion échoue (ex: hors bornes), utiliser la méthode classique
        source_time = clip.source_in + local_timeline_time
    
    return ActiveClip(
        clip_id=clip.id,
        asset_id=clip.asset_id,
        track_id=clip.track_id,
        track_type=track_type,
        track_index=track_index,
        source_path=source_path,
        source_time=source_time,
        timeline_start=clip.timeline_start,
        timeline_end=clip.timeline_start + clip.duration,
        text=clip.text or "",
    )


# ---------------------------------------------------------------------------
# API publique
# ---------------------------------------------------------------------------


def evaluate_timeline(
    project: Project, time_seconds: float, *, sequence_id: str | None = None
) -> list[ActiveClip]:
    """Retourne les clips actifs à ``time_seconds``, dans l'ordre des pistes.

    Le temps ``time_seconds`` est exprimé en secondes sur la timeline.
    Un clip est considéré actif s'il vérifie :

    .. code-block:: python

        clip.timeline_start <= time_seconds < clip.timeline_start + clip.duration

    Seuls les clips dont l'attribut ``enabled`` vaut ``True`` sont
    retenus. Les pistes verrouillées (``track.locked``) restent prises
    en compte : le verrouillage concerne l'éditeur, pas le rendu. Les
    pistes non visibles (``track.visible is False``) sont ignorées dans
    la mesure où elles ne participent pas au preview / export. Les
    pistes audio muettes (``track.muted is True``) renvoient leurs
    clips vidéo sans leur piste audio ; leurs propres clips audio ne
    sont pas comptés.

    Le média référencé par chaque clip actif est résolu via
    ``project.media_assets``. Tout clip actif dont l'identifiant de média
    est absent du projet provoque une :class:`KeyError` explicite.

    Args:
        project: projet à évaluer (non muté).
        time_seconds: instant de timeline à évaluer (en secondes, >= 0).

    Returns:
        Liste de :class:`ActiveClip` dans l'ordre des pistes du projet
        (``project.tracks``). Plusieurs clips d'une même piste sont
        conservés dans l'ordre de ``track.clips``.

    Raises:
        ValueError: si ``time_seconds`` est strictement négatif.
        KeyError: si un clip actif référence un média introuvable dans
            ``project.media_assets``.
    """
    if time_seconds < 0.0:
        raise ValueError(
            f"Le temps de timeline doit être positif ou nul "
            f"(reçu : {time_seconds})."
        )

    if sequence_id is None:
        sequence = project.active_sequence
    else:
        found = project.get_sequence(sequence_id)
        if found is None:
            raise KeyError(f"Séquence '{sequence_id}' introuvable dans le projet.")
        sequence = found
    assets = {asset.id: asset for asset in project.media_assets}
    return _evaluate_sequence(project, sequence, time_seconds, assets, (sequence.id,))


def _evaluate_sequence(
    project: Project,
    sequence: Sequence,
    time_seconds: float,
    assets: dict[str, MediaAsset],
    stack: tuple[str, ...],
) -> list[ActiveClip]:
    """Évaluation linéaire d'une séquence (descend dans les imbrications)."""
    active: list[ActiveClip] = []
    for track_index, track in enumerate(sequence.tracks):
        if not track.visible:
            continue
        for clip in track.clips:
            if not clip.enabled:
                continue
            if not _is_active(clip, time_seconds):
                continue
            if clip.sequence_id:
                active.extend(
                    expand_nested_clip(
                        project, clip, track, track_index, time_seconds, stack,
                        lambda child, inner_time, inner_stack: _evaluate_sequence(
                            project, child, inner_time, assets, inner_stack
                        ),
                    )
                )
                continue
            asset = assets.get(clip.asset_id)
            if asset is None:
                raise KeyError(
                    f"Média '{clip.asset_id}' introuvable dans le projet '{project.name}'."
                )
            active.append(
                _build_active_clip(
                    clip=clip,
                    track_index=track_index,
                    track_type=track.type,
                    source_path=asset.path,
                    time_seconds=time_seconds,
                )
            )
    return active


def apply_track_solo(tracks, active_clips: list) -> list:
    """Retire les entrées masquées par une piste solo de leur type."""
    solo_ids: dict[str, set[str]] = {}
    for track in tracks:
        if track.solo:
            solo_ids.setdefault(track.type, set()).add(track.id)
    if not solo_ids:
        return list(active_clips)
    return [
        clip for clip in active_clips
        if solo_ids.get(clip.track_type) is None or clip.track_id in solo_ids[clip.track_type]
    ]


def _map_to_parent(clip: Clip, low: float, high: float) -> tuple[float, float]:
    """Plage ``[low, high)`` de la séquence imbriquée, en temps parent (bornée au clip)."""
    start = clip.timeline_start
    end = start + clip.duration
    remapping = clip.time_remapping
    if getattr(remapping.freeze_mode, "value", remapping.freeze_mode) == "freeze":
        return start, end
    speed = float(remapping.speed) or 1.0
    if remapping.reverse:
        a = start + (clip.source_out - high) / speed
        b = start + (clip.source_out - low) / speed
    else:
        a = start + (low - clip.source_in) / speed
        b = start + (high - clip.source_in) / speed
    return max(start, a), min(end, b)


def expand_nested_clip(
    project: Project,
    clip: Clip,
    track,
    track_index: int,
    time_seconds: float,
    stack: tuple[str, ...],
    evaluate_inner,
    duration_of=None,
) -> list[ActiveClip]:
    """Entrées actives d'un clip imbriqué à ``time_seconds`` (temps parent).

    ``evaluate_inner(séquence, temps, pile)`` évalue la séquence enfant
    (linéairement ou via un index) ; ``duration_of(séquence)`` donne sa
    durée (un index la connaît déjà, sans reparcourir ses clips). Retourne
    ``[]`` — sans lever — pour une séquence introuvable, un cycle, une
    imbrication trop profonde ou un instant situé au-delà de la fin de la
    séquence source.
    """
    from .multicam import track_filter_for
    from .sequences import MAX_NESTING_DEPTH, nested_source_time

    child = project.get_sequence(clip.sequence_id)
    if child is None or child.id in stack or len(stack) > MAX_NESTING_DEPTH:
        return []
    inner_time = nested_source_time(clip, time_seconds)
    child_duration = duration_of(child) if duration_of is not None else child.duration
    if inner_time is None or inner_time < 0.0 or inner_time >= child_duration:
        return []
    entries = evaluate_inner(child, inner_time, stack + (child.id,))
    # Source Multicam : seul l'angle choisi par ce clip est évalué (même filtre que le plan de rendu).
    track_filter = track_filter_for(child, clip)
    visible_tracks = child.tracks
    if not track_filter.is_identity:
        kept: list[ActiveClip] = []
        for entry in entries:
            if entry.track_type == "video" and entry.track_id in track_filter.hide_video:
                if entry.track_id not in track_filter.hide_audio:
                    # Image masquée mais son retenu (politique audio fixe ou mixte) : il reste une entrée **audio**, que le
                    # moniteur en direct peut jouer ; elle n'a jamais de vidéo.
                    kept.append(replace(entry, track_type="audio"))
                continue
            if entry.track_type != "video" and entry.track_id in track_filter.hide_audio:
                continue
            kept.append(entry)
        entries = kept
        visible_tracks = [item for item in child.tracks if not track_filter.hides_track(item.id)]
    inner = apply_track_solo(visible_tracks, entries)
    accepted = None if track.type == "video" else {"audio"}
    mapped: list[ActiveClip] = []
    for entry in inner:
        if accepted is not None and entry.track_type not in accepted:
            continue
        low, high = _map_to_parent(clip, entry.timeline_start, entry.timeline_end)
        mapped.append(
            ActiveClip(
                clip_id=entry.clip_id,
                asset_id=entry.asset_id,
                track_id=track.id,
                track_type=entry.track_type,
                track_index=track_index,
                source_path=entry.source_path,
                source_time=entry.source_time,
                timeline_start=low,
                timeline_end=max(low, high),
                text=entry.text,
                root_clip_id=clip.id,
                nested_path=(clip.id,) + entry.nested_path,
                sequence_id=entry.sequence_id or child.id,
                silent=entry.silent
                or (entry.track_type == "video" and entry.track_id in track_filter.hide_audio),
            )
        )
    return mapped


def timeline_duration(project: Project, sequence_id: str | None = None) -> float:
    """Retourne la fin maximale des clips activés du projet.

    La durée est calculée comme :

    .. code-block:: python

        max(clip.timeline_start + clip.duration)

    sur l'ensemble des clips dont ``enabled`` vaut ``True``. Si aucun
    clip activé n'existe dans le projet, la durée retournée est ``0.0``.

    Args:
        project: projet à analyser (non muté).

    Returns:
        Durée maximale des clips activés, en secondes (``0.0`` si le
        projet ne contient aucun clip activé).
    """
    if sequence_id is not None:
        sequence = project.get_sequence(sequence_id)
        return sequence.duration if sequence is not None else 0.0
    return project.active_sequence.duration