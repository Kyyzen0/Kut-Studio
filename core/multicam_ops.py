"""Opérations d'édition Multicam (pures : elles mutent le ``Project`` donné, sans Qt, sans E/S, sans historique).

Même contrat que :mod:`core.timeline_operations` : une opération lève ``KeyError`` (identifiant inconnu) ou
:class:`MulticamError` (règle violée) avant de toucher au projet, et la fenêtre enregistre **une** entrée d'historique par
opération réussie (``_record_history``).

Rien ici ne réinvente le montage : un « changement d'angle » est un :func:`~core.timeline_operations.cut_clip` suivi
d'un autre ``angle_id`` sur la moitié droite, si bien que rogner, déplacer, couper, imbriquer et annuler un segment
Multicam passent par le code des clips ordinaires.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence as SequenceType
from copy import deepcopy
from dataclasses import dataclass, field, replace

from .compositing import Compositing
from .multicam import (
    angle_extent,
    angle_track,
    audio_angle_ids,
    is_multicam_clip,
    resolve_angle,
)
from .multicam_model import (
    AudioMode,
    MulticamAngle,
    MulticamAudio,
    MulticamSource,
    SyncMethod,
    SyncStatus,
)
from .project_model import MIN_GAIN_DB, Clip, Project, Sequence, Track
from .sequences import (
    SequenceError,
    find_sequence,
    insert_sequence_clip,
    new_sequence_id,
    unique_sequence_name,
)
from .time_editing import needs_general_path
from .time_remapping import FreezeFrameMode, TimeInterpolation
from .timeline_operations import cut_clip
from .visual_effects import ClipTransform

_EPSILON = 1e-6


class MulticamError(ValueError):
    """Opération Multicam refusée (le projet n'est pas modifié)."""


# ---------------------------------------------------------------------------
# Création d'une source Multicam
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AngleSpec:
    """Une source à transformer en angle.

    ``asset_id`` : média de la bibliothèque (vidéo ou audio). ``clip`` : clip de la timeline à **copier** dans
    la source (création depuis la timeline : effets, rognage et réglages sont conservés) ; il prime sur ``asset_id``.
    ``offset`` : position de départ de l'angle dans la source (secondes, ≥ 0). ``sync_*`` : comment et avec quelle
    fiabilité ce décalage a été obtenu (information affichée, jamais une règle de rendu).
    """

    asset_id: str = ""
    clip: Clip | None = None
    track_type: str = "video"
    offset: float = 0.0
    name: str = ""
    sync_method: SyncMethod | None = None
    sync_status: SyncStatus = SyncStatus.NONE
    sync_confidence: float | None = None


@dataclass(frozen=True)
class AudioSuggestion:
    """Politique audio proposée à la création, et si elle est sûre.

    ``needs_choice`` : la logique ne peut pas trancher sans ambiguïté, l'interface doit demander.
    ``reason`` : ``"single_source"`` (une seule source sonore), ``"recorder"`` (un enregistreur externe bien
    synchronisé), ``"ambiguous_recorder"`` (enregistreur dont la synchronisation n'est pas fiable) ou
    ``"several_recorders"``.
    """

    mode: AudioMode
    angle_ids: tuple[str, ...]
    needs_choice: bool
    reason: str


def new_clip_id() -> str:
    return f"clip-{uuid.uuid4().hex[:12]}"


def normalize_offsets(offsets: Mapping[str, float]) -> dict[str, float]:
    """Ramène les décalages à ``>= 0`` : le plus petit devient 0 (les écarts relatifs sont conservés)."""
    if not offsets:
        return {}
    low = min(offsets.values())
    return {key: max(0.0, float(value) - low) for key, value in offsets.items()}


def _asset_for(project: Project, spec: AngleSpec):
    asset = next((item for item in project.media_assets if item.id == spec.asset_id), None)
    if asset is None:
        raise KeyError(f"Média '{spec.asset_id}' introuvable dans le projet.")
    if asset.media_type not in {"video", "audio"}:
        raise MulticamError(f"« {asset.name} » n'est ni une vidéo ni un son : il ne peut pas devenir un angle.")
    return asset


def _angle_clip(project: Project, spec: AngleSpec, track_id: str) -> tuple[Clip, str, str]:
    """Clip de l'angle (copie du clip de la timeline, ou plein média), type de piste, nom par défaut."""
    if spec.clip is not None:
        if is_multicam_clip(project, spec.clip):
            raise MulticamError("Un segment Multicam ne peut pas devenir un angle d'une autre source Multicam.")
        clip = deepcopy(spec.clip)
        clip.id = new_clip_id()
        clip.track_id = track_id
        clip.timeline_start = max(0.0, float(spec.offset))
        return clip, spec.track_type, spec.name or spec.clip.label
    asset = _asset_for(project, spec)
    track_type = "audio" if asset.media_type == "audio" else "video"
    clip = Clip(
        id=new_clip_id(), asset_id=asset.id, track_id=track_id, timeline_start=max(0.0, float(spec.offset)),
        source_in=0.0, source_out=asset.duration, label=asset.name,
    )
    return clip, track_type, spec.name or asset.name


def _unique_name(taken: set[str], name: str) -> str:
    base = name.strip() or "Angle"
    candidate, number = base, 2
    while candidate in taken:
        candidate = f"{base} {number}"
        number += 1
    taken.add(candidate)
    return candidate


def suggest_audio_policy(angles: SequenceType[MulticamAngle], audio_only_ids: SequenceType[str]) -> AudioSuggestion:
    """Politique audio par défaut d'une nouvelle source, avec indication d'ambiguïté.

    * aucun enregistreur externe : le son suit l'image (sans ambiguïté) ;
    * un enregistreur externe dont la synchronisation est mesurée fiable (``EXCELLENT`` / ``GOOD``) ou posée par
      l'utilisateur / un timecode / les positions actuelles : son fixe, **sans** question ;
    * un enregistreur dont la synchronisation est incertaine ou échouée : on propose le son de la caméra et on demande ;
    * plusieurs enregistreurs : on demande.
    """
    recorders = [angle for angle in angles if angle.id in set(audio_only_ids)]
    if not recorders:
        return AudioSuggestion(AudioMode.FOLLOW_VIDEO, (), False, "single_source")
    if len(recorders) > 1:
        return AudioSuggestion(AudioMode.FOLLOW_VIDEO, (), True, "several_recorders")
    recorder = recorders[0]
    if recorder.sync_status in {SyncStatus.UNCERTAIN, SyncStatus.FAILED}:
        return AudioSuggestion(AudioMode.FOLLOW_VIDEO, (), True, "ambiguous_recorder")
    return AudioSuggestion(AudioMode.FIXED, (recorder.id,), False, "recorder")


def create_multicam_source(
    project: Project,
    specs: SequenceType[AngleSpec],
    *,
    name: str = "Multicam",
    sync_method: SyncMethod | None = None,
    audio: MulticamAudio | None = None,
) -> Sequence:
    """Crée une source Multicam (une séquence) à partir de ``specs`` et l'ajoute au projet.

    Une piste par angle (vidéo pour une caméra, audio pour un enregistreur), chaque clip placé à son décalage ;
    les décalages sont normalisés (le plus petit est 0). La source reprend la résolution et la cadence de la séquence
    active : tous les angles sont convertis temporellement par le moteur ordinaire, jamais tronqués.
    ``audio=None`` : politique proposée par :func:`suggest_audio_policy`.

    Raises:
        MulticamError: aucune source, ou un média inutilisable. ``KeyError`` : média inconnu.
    """
    if not specs:
        raise MulticamError("Une source Multicam a besoin d'au moins un angle.")
    offsets = normalize_offsets({str(index): spec.offset for index, spec in enumerate(specs)})
    active = project.active_sequence
    tracks: list[Track] = []
    angles: list[MulticamAngle] = []
    audio_only: list[str] = []
    taken_names: set[str] = set()
    counters = {"video": 0, "audio": 0}
    for index, spec in enumerate(specs):
        clip_spec = replace(spec, offset=offsets[str(index)])
        clip, track_type, default_name = _angle_clip(project, clip_spec, "?")
        counters[track_type] += 1
        track_id = f"{'V' if track_type == 'video' else 'A'}{counters[track_type]}"
        clip.track_id = track_id
        angle_name = _unique_name(taken_names, default_name)
        tracks.append(Track(id=track_id, name=angle_name, type=track_type, clips=[clip]))
        angle = MulticamAngle(
            id=f"angle-{index + 1}", name=angle_name, track_id=track_id, color_index=index,
            sync_method=spec.sync_method or sync_method, sync_status=spec.sync_status,
            sync_confidence=spec.sync_confidence,
        )
        angles.append(angle)
        if track_type == "audio":
            audio_only.append(angle.id)
    if audio is None:
        suggestion = suggest_audio_policy(angles, audio_only)
        audio = MulticamAudio(suggestion.mode, suggestion.angle_ids)
    sequence = Sequence(
        id=new_sequence_id(),
        name=unique_sequence_name(project, name),
        width=active.width,
        height=active.height,
        fps=active.fps,
        tracks=tracks,
        multicam=MulticamSource(angles=angles, audio=audio, sync_method=sync_method),
    )
    project.sequences.append(sequence)
    return sequence


def insert_multicam_clip(
    project: Project,
    sequence_id: str,
    track_id: str,
    timeline_start: float,
    *,
    angle_id: str = "",
    parent_sequence_id: str | None = None,
) -> Clip:
    """Insère une source Multicam comme clip du montage, sur l'angle ``angle_id`` (le premier par défaut).

    L'angle est écrit explicitement : réordonner les angles ne change pas un segment existant.
    """
    child = find_sequence(project, sequence_id)
    source = _source_of(child)
    angle = resolve_angle(source, angle_id)
    if angle is None:
        raise MulticamError(f"Angle inconnu : « {angle_id} ».")
    try:
        clip = insert_sequence_clip(
            project, sequence_id, track_id, timeline_start, parent_sequence_id=parent_sequence_id
        )
    except SequenceError as exc:
        raise MulticamError(str(exc)) from exc
    clip.angle_id = angle.id
    return clip


def _source_of(sequence: Sequence) -> MulticamSource:
    if sequence.multicam is None:
        raise MulticamError(f"« {sequence.name} » n'est pas une source Multicam.")
    return sequence.multicam


def _angle_of(sequence: Sequence, angle_id: str) -> MulticamAngle:
    angle = _source_of(sequence).angle(angle_id)
    if angle is None:
        raise MulticamError(f"Angle inconnu : « {angle_id} ».")
    return angle


# ---------------------------------------------------------------------------
# Montage : segments, bascule d'angle, remplacement
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SwitchResult:
    """Résultat d'une bascule : le segment qui montre maintenant l'angle, et si une coupe a été faite."""

    clip: Clip
    previous_angle_id: str
    cut: bool


def multicam_segment_at(
    project: Project, time_seconds: float, *, clip_id: str = "", track_id: str = ""
) -> Clip | None:
    """Segment Multicam de la séquence active sous ``time_seconds`` (piste vidéo la plus haute par défaut).

    ``clip_id`` désigne un segment précis (il doit couvrir l'instant) ; ``track_id`` restreint la recherche à une piste.
    """
    candidates: list[tuple[int, Clip]] = []
    for index, track in enumerate(project.tracks):
        if track.type != "video" or (track_id and track.id != track_id):
            continue
        for clip in track.clips:
            if not clip.enabled or not is_multicam_clip(project, clip):
                continue
            if clip.timeline_start - _EPSILON <= time_seconds < clip.timeline_start + clip.duration - _EPSILON:
                if clip_id and clip.id != clip_id:
                    continue
                candidates.append((index, clip))
    if not candidates:
        return None
    return max(candidates, key=lambda pair: pair[0])[1]


def effective_angle_id(project: Project, clip: Clip) -> str:
    """Angle réellement montré par un segment (``""`` : source introuvable ou angle inconnu)."""
    child = project.get_sequence(clip.sequence_id)
    if child is None or child.multicam is None:
        return ""
    angle = resolve_angle(child.multicam, clip.angle_id)
    return angle.id if angle is not None else ""


def switch_angle(
    project: Project, time_seconds: float, angle_id: str, *, clip_id: str = "", track_id: str = ""
) -> SwitchResult | None:
    """Montre ``angle_id`` à partir de ``time_seconds`` : coupe le segment sous la tête de lecture, change la moitié droite.

    Retourne ``None`` si le segment montre déjà cet angle (aucune coupe inutile). Au tout début d'un segment, l'angle
    est simplement remplacé. Rien n'est modifié si l'opération est refusée.

    Raises:
        MulticamError: aucun segment Multicam à cet instant, angle inconnu, ou instant à la toute fin du segment.
    """
    clip = multicam_segment_at(project, time_seconds, clip_id=clip_id, track_id=track_id)
    if clip is None:
        raise MulticamError("Aucun segment Multicam sous la tête de lecture.")
    child = find_sequence(project, clip.sequence_id)
    _angle_of(child, angle_id)
    previous = effective_angle_id(project, clip)
    if previous == angle_id:
        return None
    half_frame = 0.5 / max(1.0, float(project.active_sequence.fps))
    if time_seconds - clip.timeline_start <= half_frame:
        clip.angle_id = angle_id
        return SwitchResult(clip, previous, cut=False)
    if clip.timeline_start + clip.duration - time_seconds <= half_frame:
        raise MulticamError("Trop près de la fin du segment pour y changer d'angle.")
    _left, right = cut_clip(project, clip.id, time_seconds)
    right.angle_id = angle_id
    return SwitchResult(right, previous, cut=True)


def replace_angle(project: Project, clip_id: str, angle_id: str) -> Clip:
    """Remplace l'angle d'un segment existant, **sans** créer de coupe (« Remplacer par l'angle 3 »)."""
    from .timeline_operations import find_clip

    clip = find_clip(project, clip_id)
    if not is_multicam_clip(project, clip):
        raise MulticamError("Ce clip n'est pas un segment Multicam.")
    _angle_of(find_sequence(project, clip.sequence_id), angle_id)
    clip.angle_id = angle_id
    return clip


def angle_usages(project: Project, sequence_id: str, angle_id: str) -> list[Clip]:
    """Segments (de toutes les séquences) qui montrent ``angle_id`` de la source ``sequence_id``."""
    source = find_sequence(project, sequence_id).multicam
    found: list[Clip] = []
    for sequence in project.sequences:
        for track in sequence.tracks:
            for clip in track.clips:
                if clip.sequence_id != sequence_id:
                    continue
                shown = resolve_angle(source, clip.angle_id) if source is not None else None
                if shown is not None and shown.id == angle_id:
                    found.append(clip)
    return found


# ---------------------------------------------------------------------------
# Réglages de la source
# ---------------------------------------------------------------------------


def angle_has_audio(project: Project, sequence: Sequence, angle: MulticamAngle) -> bool:
    """L'angle porte-t-il du son ? Piste audio, clip imbriqué (non analysé) ou média déclaré avec son.

    Une caméra dont le média n'a pas de son ne peut pas être « la source audio » : la source entière deviendrait muette.
    """
    track = next((item for item in sequence.tracks if item.id == angle.track_id), None)
    if track is None:
        return False
    if track.type == "audio":
        return True
    assets = {asset.id: asset for asset in project.media_assets}
    return any(
        clip.is_nested or (clip.asset_id in assets and bool(assets[clip.asset_id].has_audio)) for clip in track.clips
    )


def set_audio_policy(
    project: Project, sequence_id: str, mode: AudioMode, angle_ids: SequenceType[str] = ()
) -> MulticamAudio:
    """Change la politique audio de la source ; l'audio des segments s'en déduit à la lecture du plan.

    ``FIXED`` demande exactement un angle, ``MIX`` au moins un ; ``FOLLOW_VIDEO`` n'en demande aucun.
    """
    sequence = find_sequence(project, sequence_id)
    source = _source_of(sequence)
    ids = tuple(dict.fromkeys(angle_ids))
    for angle_id in ids:
        angle = _angle_of(sequence, angle_id)
        if mode is not AudioMode.FOLLOW_VIDEO and not angle_has_audio(project, sequence, angle):
            raise MulticamError(f"« {angle.name} » n'a pas de son : il ne peut pas être une source audio de la source Multicam.")
    if mode is AudioMode.FIXED and len(ids) != 1:
        raise MulticamError("Un son fixe désigne exactement une source audio.")
    if mode is AudioMode.MIX and not ids:
        raise MulticamError("Un son mixé désigne au moins une source audio.")
    if mode is AudioMode.FOLLOW_VIDEO:
        ids = ()
    source.audio = MulticamAudio(mode, ids)
    return source.audio


def rename_angle(project: Project, sequence_id: str, angle_id: str, name: str) -> MulticamAngle:
    """Renomme un angle (et sa piste, pour que la timeline de la source reste lisible)."""
    clean = (name or "").strip()
    if not clean:
        raise MulticamError("Le nom d'un angle ne peut pas être vide.")
    sequence = find_sequence(project, sequence_id)
    angle = _angle_of(sequence, angle_id)
    angle.name = clean
    track = angle_track(sequence, angle)
    if track is not None:
        track.name = clean
    return angle


def set_angle_color(project: Project, sequence_id: str, angle_id: str, color_index: int) -> MulticamAngle:
    """Choisit le rang de palette d'un angle (l'interface en tire la couleur dans le thème)."""
    angle = _angle_of(find_sequence(project, sequence_id), angle_id)
    angle.color_index = max(0, int(color_index))
    return angle


def set_angle_offset(
    project: Project,
    sequence_id: str,
    angle_id: str,
    offset: float,
    *,
    method: SyncMethod | None = SyncMethod.MANUAL,
    status: SyncStatus = SyncStatus.MANUAL,
    confidence: float | None = None,
) -> float:
    """Déplace tous les clips d'un angle pour que son premier clip commence à ``offset`` ; retourne le décalage appliqué.

    C'est la correction manuelle de synchronisation (valeur saisie, glissement, repère) : le décalage **est** la
    position des clips, rien d'autre n'est stocké. L'angle est marqué « à la main » (``MANUAL``), sauf si l'appelant
    pose le résultat d'une mesure (``method``/``status``/``confidence``).
    """
    if offset < 0.0:
        raise MulticamError("Le décalage d'un angle ne peut pas être négatif.")
    sequence = find_sequence(project, sequence_id)
    angle = _angle_of(sequence, angle_id)
    extent = angle_extent(sequence, angle)
    track = angle_track(sequence, angle)
    if track is None or extent is None:
        raise MulticamError(f"L'angle « {angle.name} » n'a aucun clip à décaler.")
    delta = float(offset) - extent[0]
    for clip in track.clips:
        clip.timeline_start = max(0.0, clip.timeline_start + delta)
    angle.sync_method = method
    angle.sync_status = status
    angle.sync_confidence = confidence
    return delta


@dataclass(frozen=True)
class SyncOutcome:
    """Résultat de synchronisation d'un angle (mesure ou lecture du timecode)."""

    offset: float | None
    method: SyncMethod
    status: SyncStatus
    confidence: float | None = None


def apply_sync(project: Project, sequence_id: str, outcomes: Mapping[str, SyncOutcome]) -> dict[str, float]:
    """Applique des résultats de synchronisation (un par angle) : déplace les clips, note méthode et fiabilité.

    Un résultat sans décalage (mesure échouée) laisse l'angle à sa place et note l'échec : on ne prétend jamais qu'un
    angle est synchronisé faute d'avoir pu le mesurer. Les décalages obtenus sont normalisés (le plus petit devient 0),
    puis appliqués d'un seul bloc. Retourne le décalage appliqué à chaque angle déplacé.
    """
    sequence = find_sequence(project, sequence_id)
    source = _source_of(sequence)
    for angle_id in outcomes:
        _angle_of(sequence, angle_id)
    measured = {angle_id: out.offset for angle_id, out in outcomes.items() if out.offset is not None}
    placed = normalize_offsets({key: value for key, value in measured.items() if value is not None})
    applied: dict[str, float] = {}
    for angle_id, outcome in outcomes.items():
        angle = source.angle(angle_id)
        if angle is None:
            continue
        if angle_id in placed:
            applied[angle_id] = set_angle_offset(
                project, sequence_id, angle_id, placed[angle_id],
                method=outcome.method, status=outcome.status, confidence=outcome.confidence,
            )
        else:
            angle.sync_method = outcome.method
            angle.sync_status = outcome.status
            angle.sync_confidence = outcome.confidence
    methods = {out.method for out in outcomes.values()}
    if len(methods) == 1:
        source.sync_method = next(iter(methods))
    return applied


def add_angle(project: Project, sequence_id: str, spec: AngleSpec) -> MulticamAngle:
    """Ajoute un angle (une piste et son clip) à une source existante."""
    sequence = find_sequence(project, sequence_id)
    source = _source_of(sequence)
    clip, track_type, default_name = _angle_clip(project, spec, "?")
    prefix = "V" if track_type == "video" else "A"
    numbers = [
        int(track.id[1:]) for track in sequence.tracks
        if track.type == track_type and track.id.startswith(prefix) and track.id[1:].isdigit()
    ]
    track_id = f"{prefix}{(max(numbers) + 1) if numbers else 1}"
    clip.track_id = track_id
    name = _unique_name({angle.name for angle in source.angles}, default_name)
    sequence.tracks.append(Track(id=track_id, name=name, type=track_type, clips=[clip]))
    numbers_used = [
        int(angle.id.split("-")[1]) for angle in source.angles if angle.id.startswith("angle-") and angle.id[6:].isdigit()
    ]
    angle = MulticamAngle(
        id=f"angle-{(max(numbers_used) + 1) if numbers_used else 1}", name=name, track_id=track_id,
        color_index=len(source.angles), sync_method=spec.sync_method, sync_status=spec.sync_status,
        sync_confidence=spec.sync_confidence,
    )
    source.angles.append(angle)
    return angle


def remove_angle(project: Project, sequence_id: str, angle_id: str, *, replace_with: str = "") -> int:
    """Supprime un angle (sa piste et ses clips) ; retourne le nombre de segments redirigés vers ``replace_with``.

    Si des segments montrent encore cet angle, ``replace_with`` est obligatoire : on ne laisse jamais un segment
    désigner un angle disparu. La politique audio est corrigée si elle citait l'angle.
    """
    sequence = find_sequence(project, sequence_id)
    source = _source_of(sequence)
    angle = _angle_of(sequence, angle_id)
    if len(source.angles) == 1:
        raise MulticamError("Une source Multicam garde au moins un angle.")
    usages = angle_usages(project, sequence_id, angle_id)
    if usages:
        if not replace_with or replace_with == angle_id:
            raise MulticamError(
                f"{len(usages)} segment(s) montrent l'angle « {angle.name} » : choisissez l'angle qui les remplace."
            )
        _angle_of(sequence, replace_with)
        for clip in usages:
            clip.angle_id = replace_with
    sequence.tracks = [track for track in sequence.tracks if track.id != angle.track_id]
    source.angles = [item for item in source.angles if item.id != angle_id]
    remaining = tuple(item for item in source.audio.angle_ids if item != angle_id)
    mode = source.audio.mode
    if mode is not AudioMode.FOLLOW_VIDEO and not remaining:
        mode = AudioMode.FOLLOW_VIDEO
    source.audio = MulticamAudio(mode, remaining if mode is not AudioMode.FOLLOW_VIDEO else ())
    return len(usages)


# ---------------------------------------------------------------------------
# Création depuis la timeline
# ---------------------------------------------------------------------------


def create_multicam_from_clips(
    project: Project,
    clip_ids: SequenceType[str],
    *,
    name: str = "Multicam",
    sync_method: SyncMethod | None = SyncMethod.POSITIONS,
    offsets: Mapping[str, float] | None = None,
    names: Mapping[str, str] | None = None,
    outcomes: Mapping[str, SyncOutcome] | None = None,
    audio: MulticamAudio | None = None,
) -> tuple[Sequence, Clip]:
    """Transforme des clips de la timeline en une source Multicam et les remplace par **un** segment.

    Chaque clip devient un angle (copie : effets, rognage et réglages conservés). Sans ``offsets`` ce sont leurs
    positions actuelles qui servent de synchronisation (« utiliser les positions actuelles »). ``names`` : nom d'angle par
    clip ; ``outcomes`` : résultat de synchronisation par clip (un décalage mesuré remplace la position, une mesure échouée
    garde la position et note l'échec) ; ``audio`` : politique audio, sinon celle de :func:`suggest_audio_policy`. Les
    clips d'origine sont retirés et un segment unique, sur l'angle 1, occupe la piste vidéo la plus basse de la sélection
    à la plus petite position. Rien n'est modifié si l'opération est refusée.
    """
    from .timeline_operations import _find_track_for_clip

    if len(clip_ids) < 2:
        raise MulticamError("Sélectionnez au moins deux clips.")
    found: list[tuple[Track, Clip]] = []
    for clip_id in clip_ids:
        track, index = _find_track_for_clip(project, clip_id)
        if track.locked:
            raise MulticamError(f"La piste '{track.id}' est verrouillée : modifications refusées.")
        clip = track.clips[index]
        if track.type not in {"video", "audio"}:
            raise MulticamError("Seuls des clips vidéo ou audio peuvent devenir des angles.")
        found.append((track, clip))
    ordered = sorted(found, key=lambda pair: (pair[0].type != "video", pair[1].timeline_start))
    known: dict[str, float] = {}
    for _track, clip in ordered:
        measured = (outcomes or {}).get(clip.id)
        if measured is not None and measured.offset is not None:
            known[clip.id] = float(measured.offset)
        elif clip.id in (offsets or {}):
            known[clip.id] = float((offsets or {})[clip.id])
    anchor = next(((clip, known[clip.id]) for _track, clip in ordered if clip.id in known), None)

    def placed(clip: Clip) -> float:
        """Position d'un clip dans le repère des décalages : celle qui est connue, sinon sa place d'origine.

        Les décalages mesurés (ou fournis) ont leur propre origine (le plus petit vaut 0), pas celle de la timeline : un
        clip dont la mesure a échoué ne peut donc pas reprendre sa position absolue (il atterrirait à des minutes des
        autres). On le garde au même écart qu'avant d'un clip dont le décalage est connu. Sans aucun décalage connu,
        les positions actuelles sont la seule information et servent telles quelles.
        """
        if clip.id in known:
            return known[clip.id]
        if anchor is None:
            return float(clip.timeline_start)
        return anchor[1] + (clip.timeline_start - anchor[0].timeline_start)

    specs = []
    for track, clip in ordered:
        outcome = (outcomes or {}).get(clip.id)
        specs.append(AngleSpec(
            clip=clip, track_type=track.type,
            offset=placed(clip),
            name=(names or {}).get(clip.id) or clip.label,
            sync_method=outcome.method if outcome is not None else sync_method,
            sync_status=outcome.status if outcome is not None else SyncStatus.NONE,
            sync_confidence=outcome.confidence if outcome is not None else None,
        ))
    sequence = create_multicam_source(project, specs, name=name, sync_method=sync_method, audio=audio)
    video_tracks = [track for track, _clip in found if track.type == "video"]
    host = video_tracks[0] if video_tracks else found[0][0]
    for candidate in video_tracks[1:]:
        if project.tracks.index(candidate) < project.tracks.index(host):
            host = candidate
    start = min(clip.timeline_start for _track, clip in found)
    for track, clip in found:
        track.clips = [item for item in track.clips if item.id != clip.id]
    first_angle = sequence.multicam.angles[0].id if sequence.multicam is not None else ""
    try:
        segment = insert_multicam_clip(project, sequence.id, host.id, start, angle_id=first_angle)
    except MulticamError:
        # Rétablit les clips retirés : l'opération est atomique.
        for track, clip in found:
            track.clips.append(clip)
        project.sequences.remove(sequence)
        raise
    return sequence, segment


# ---------------------------------------------------------------------------
# Aplatir
# ---------------------------------------------------------------------------


@dataclass
class FlattenResult:
    """Clips ordinaires qui remplacent un segment Multicam."""

    video: list[Clip] = field(default_factory=list)
    audio: list[Clip] = field(default_factory=list)
    removed_id: str = ""


def _is_plain(clip: Clip) -> bool:
    """Vitesse constante positive : seul cas où deux mappings se composent par un simple produit de vitesses."""
    remap = clip.time_remapping
    return not remap.reverse and remap.freeze_mode is not FreezeFrameMode.FREEZE and not needs_general_path(clip)


def _flatten_part(
    segment: Clip, source_clip: Clip, low: float, high: float, track_id: str, *, silent: bool,
) -> Clip:
    """Portion ``[low, high)`` (temps de la source) du clip d'angle ``source_clip``, placée comme le segment."""
    speed = float(segment.time_remapping.speed) or 1.0
    clip = deepcopy(source_clip)
    clip.id = new_clip_id()
    clip.track_id = track_id
    clip.source_in = source_clip.source_in + (low - source_clip.timeline_start) * source_clip.time_remapping.speed
    clip.source_out = source_clip.source_in + (high - source_clip.timeline_start) * source_clip.time_remapping.speed
    clip.timeline_start = segment.timeline_start + (low - segment.source_in) / speed
    if speed != 1.0:
        clip.time_remapping = replace(
            source_clip.time_remapping, speed=source_clip.time_remapping.speed * speed
        )
    clip.tracking = None  # les données de suivi décrivent le clip d'angle de la source, pas cette copie
    if silent:
        clip.gain_db = MIN_GAIN_DB
    return clip


def flatten_multicam_clip(project: Project, clip_id: str) -> FlattenResult:
    """Remplace un segment Multicam par les clips ordinaires qu'il montre (vidéo et son).

    Pour chaque piste d'angle visible, la portion de ses clips couverte par le segment devient un clip de la piste du
    segment (réglages du clip d'angle conservés : effets, étalonnage, transformation…) ; le son suit la politique
    audio de la source : le son d'une autre source que l'angle vidéo devient un clip d'une piste audio ajoutée au
    montage (``Multicam audio``) et la vidéo, alors, est coupée à −60 dB. Refus (rien n'est modifié) si le segment ou un
    clip d'angle utilise un remappage de lecture (inverse, arrêt sur image), si le segment porte un étalonnage, une
    transformation ou une composition propres (impossible à fusionner sans changer l'image), un gain, un panoramique,
    des fondus ou des effets audio propres (impossibles à reprendre sans changer le son) ou si l'angle est inconnu.
    """
    from .timeline_operations import _find_track_for_clip

    track, index = _find_track_for_clip(project, clip_id)
    if track.locked:
        raise MulticamError(f"La piste '{track.id}' est verrouillée : modifications refusées.")
    segment = track.clips[index]
    if not is_multicam_clip(project, segment):
        raise MulticamError("Ce clip n'est pas un segment Multicam.")
    if not _is_plain(segment):
        raise MulticamError(
            "Aplatir : un segment en lecture inverse, en arrêt sur image ou à vitesse animée n'est pas pris en charge."
        )
    remapping = segment.time_remapping
    if remapping.interpolation is not TimeInterpolation.SAMPLING or remapping.preserve_pitch or not remapping.remap_audio:
        raise MulticamError(
            "Aplatir : le segment porte une interpolation d'images ou un réglage audio du temps propres, que les clips "
            "remplaçants ne reprendraient pas ; retirez-les ou imbriquez le segment."
        )
    if (
        segment.color_grade is not None
        or segment.transform != ClipTransform()
        or segment.transform_keyframes
        or segment.compositing != Compositing()
        or segment.animation
    ):
        raise MulticamError(
            "Aplatir : le segment porte un étalonnage, une transformation ou une composition propres ; "
            "retirez-les ou imbriquez le segment."
        )
    if segment.gain_db or segment.pan or segment.fade_in or segment.fade_out or segment.audio_effects:
        raise MulticamError(
            "Aplatir : le segment porte un gain, un panoramique, des fondus ou des effets audio propres, que les clips "
            "remplaçants ne peuvent pas reprendre sans changer le son ; retirez-les ou imbriquez le segment."
        )
    child = find_sequence(project, segment.sequence_id)
    source = _source_of(child)
    active = resolve_angle(source, segment.angle_id)
    if active is None:
        raise MulticamError("Aplatir : le segment désigne un angle inconnu.")
    audible = audio_angle_ids(source, active)
    wanted = [active.id] + [angle_id for angle_id in audible if angle_id != active.id]
    low, high = segment.source_in, segment.source_out
    result = FlattenResult(removed_id=segment.id)
    video_silent = active.id not in audible
    plans: list[tuple[MulticamAngle, Track, list[Clip]]] = []
    for angle_id in wanted:
        angle = source.angle(angle_id)
        angle_clips = angle_track(child, angle) if angle is not None else None
        if angle is None or angle_clips is None:
            continue
        overlapping = [
            item for item in angle_clips.clips
            if item.enabled and item.timeline_start < high - _EPSILON and item.timeline_start + item.duration > low + _EPSILON
        ]
        for item in overlapping:
            if item.is_nested or not _is_plain(item):
                raise MulticamError("Aplatir : un clip d'angle imbriqué, en lecture inverse ou à vitesse animée n'est pas pris en charge.")
        plans.append((angle, angle_clips, overlapping))
    new_audio_track: Track | None = None
    for angle, angle_clips, overlapping in plans:
        is_video_part = angle.id == active.id and angle_clips.type == "video"
        for item in overlapping:
            part_low = max(item.timeline_start, low)
            part_high = min(item.timeline_start + item.duration, high)
            if is_video_part:
                new = _flatten_part(segment, item, part_low, part_high, track.id, silent=video_silent)
                new.effects = list(new.effects) + deepcopy(segment.effects)
                result.video.append(new)
            elif angle.id in audible:
                if new_audio_track is None:
                    new_audio_track = _add_flatten_audio_track(project)
                new = _flatten_part(segment, item, part_low, part_high, new_audio_track.id, silent=False)
                result.audio.append(new)
    track.clips = [item for item in track.clips if item.id != segment.id] + result.video
    if result.audio and new_audio_track is not None:
        new_audio_track.clips.extend(result.audio)
    _rewire_transitions(project, segment.id, result.video)
    return result


def _add_flatten_audio_track(project: Project) -> Track:
    from .track_operations import add_track

    return add_track(project, "audio", "Multicam audio")


def _rewire_transitions(project: Project, removed_id: str, new_video: list[Clip]) -> None:
    """Les transitions du segment suivent ses clips de remplacement ; sans remplaçant, elles disparaissent."""
    kept = []
    ordered = sorted(new_video, key=lambda item: item.timeline_start)
    for transition in project.transitions:
        if transition.from_clip_id == removed_id:
            if not ordered:
                continue
            transition = replace(transition, from_clip_id=ordered[-1].id)
        if transition.to_clip_id == removed_id:
            if not ordered:
                continue
            transition = replace(transition, to_clip_id=ordered[0].id)
        kept.append(transition)
    project.transitions[:] = kept


__all__ = [
    "AngleSpec",
    "AudioSuggestion",
    "FlattenResult",
    "MulticamError",
    "SwitchResult",
    "SyncOutcome",
    "add_angle",
    "angle_has_audio",
    "angle_usages",
    "apply_sync",
    "create_multicam_from_clips",
    "create_multicam_source",
    "effective_angle_id",
    "flatten_multicam_clip",
    "insert_multicam_clip",
    "multicam_segment_at",
    "new_clip_id",
    "normalize_offsets",
    "remove_angle",
    "rename_angle",
    "replace_angle",
    "set_angle_color",
    "set_angle_offset",
    "set_audio_policy",
    "suggest_audio_policy",
    "switch_angle",
]
