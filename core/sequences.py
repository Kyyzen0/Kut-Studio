"""Multi-séquence et séquences imbriquées : logique métier pure.

Une :class:`~core.project_model.Sequence` est une timeline autonome du
projet. Un *nested sequence clip* est un :class:`~core.project_model.Clip`
dont ``sequence_id`` référence une autre séquence du même projet : la
séquence n'est **jamais copiée** dans le clip, elle est référencée. Toutes
ses occurrences reflètent donc immédiatement ses modifications.

Ce module regroupe tout ce qui raisonne sur les séquences :

- le graphe des dépendances (qui contient qui) et la **détection de
  cycles** (A ⊃ A, A ⊃ B ⊃ A, A ⊃ B ⊃ C ⊃ A) ;
- les opérations utilisateur : créer, renommer, dupliquer, supprimer,
  insérer comme clip, convertir une sélection en séquence imbriquée ;
- la **politique de durée** (voir :func:`clamp_nested_clips`) ;
- le diagnostic d'un projet chargé (références cassées, cycles).

Garde-fous : aucune fonction de ce module ne récurse sans borne. Les
parcours de graphe sont itératifs et mémorisent les nœuds visités ; un
projet corrompu (cycle, référence inconnue) produit un diagnostic, jamais
une récursion infinie ni une exception non contrôlée.

Aucune dépendance PySide6 ni FFmpeg.
"""

from __future__ import annotations

import uuid
from copy import deepcopy
from dataclasses import dataclass, replace

from . import graph_cycles
from .project_model import Clip, Marker, Project, Sequence, Track

MAX_NESTING_DEPTH = 32
"""Profondeur d'imbrication maximale évaluée.

Une chaîne plus profonde est très probablement une erreur (ou un fichier
corrompu) : le rendu s'arrête à cette profondeur et le signale, au lieu
de construire un graphe FFmpeg démesuré. 10 niveaux restent rapides.
"""

NESTABLE_TRACK_TYPES = frozenset({"video", "audio"})
"""Pistes pouvant accueillir un clip imbriqué.

Sur une piste vidéo, le clip apporte l'image **et** le son de la séquence ;
sur une piste audio, uniquement le son.
"""

_EPSILON = 1e-6


# ---------------------------------------------------------------------------
# Erreurs et rapports
# ---------------------------------------------------------------------------


class SequenceError(ValueError):
    """Opération refusée sur une séquence (message lisible pour l'UI)."""


class SequenceCycleError(SequenceError):
    """L'opération créerait une imbrication circulaire."""


class SequenceInUseError(SequenceError):
    """Suppression refusée : la séquence est encore utilisée."""

    def __init__(self, message: str, usages: list["SequenceUsage"]) -> None:
        super().__init__(message)
        self.usages = list(usages)


@dataclass(frozen=True)
class SequenceUsage:
    """Un endroit où une séquence est utilisée comme clip."""

    parent_sequence_id: str
    parent_sequence_name: str
    track_id: str
    clip_id: str
    clip_label: str
    timeline_start: float


@dataclass(frozen=True)
class SequenceIssue:
    """Problème détecté dans le graphe des séquences.

    ``kind`` vaut ``"missing"`` (séquence référencée introuvable),
    ``"cycle"`` (imbrication circulaire) ou ``"depth"`` (imbrication plus
    profonde que :data:`MAX_NESTING_DEPTH`).
    """

    kind: str
    sequence_id: str
    clip_id: str
    message: str


@dataclass(frozen=True)
class ClampAdjustment:
    """Un clip imbriqué raccourci parce que sa séquence source a raccourci."""

    sequence_id: str
    clip_id: str
    old_source_out: float
    new_source_out: float


@dataclass(frozen=True)
class NestResult:
    """Résultat de :func:`create_sequence_from_selection`."""

    sequence: Sequence
    clip: Clip
    dropped_transition_ids: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Recherche et identifiants
# ---------------------------------------------------------------------------


def new_sequence_id() -> str:
    """Identifiant de séquence unique et stable (jamais réutilisé)."""
    return f"seq-{uuid.uuid4().hex[:12]}"


def _new_clip_id() -> str:
    return f"clip-{uuid.uuid4().hex[:12]}"


def find_sequence(project: Project, sequence_id: str) -> Sequence:
    """Séquence ``sequence_id`` ou ``KeyError`` explicite."""
    sequence = project.get_sequence(sequence_id)
    if sequence is None:
        raise KeyError(f"Séquence '{sequence_id}' introuvable dans le projet.")
    return sequence


def unique_sequence_name(project: Project, base: str) -> str:
    """``base`` si libre, sinon ``base 2``, ``base 3``…"""
    base = (base or "").strip() or "Séquence"
    taken = {sequence.name for sequence in project.sequences}
    if base not in taken:
        return base
    index = 2
    while f"{base} {index}" in taken:
        index += 1
    return f"{base} {index}"


def clip_source_limit(project: Project, clip: Clip) -> float | None:
    """Durée maximale lisible par ``clip`` (média ou séquence source).

    ``None`` quand la source est inconnue (média absent, séquence
    supprimée) : l'appelant décide alors de ne pas étendre le clip.
    """
    if clip.sequence_id:
        sequence = project.get_sequence(clip.sequence_id)
        return sequence.duration if sequence is not None else None
    for asset in project.media_assets:
        if asset.id == clip.asset_id:
            return float(asset.duration)
    return None


def clip_media_type(project: Project, clip: Clip) -> str:
    """Type de source du clip : ``"sequence"`` ou le ``media_type`` du média."""
    if clip.sequence_id:
        return "sequence"
    for asset in project.media_assets:
        if asset.id == clip.asset_id:
            return asset.media_type
    raise KeyError(f"Média '{clip.asset_id}' introuvable dans le projet '{project.name}'.")


def nested_overflow(project: Project, clip: Clip) -> float:
    """Portion (secondes source) du clip au-delà de la fin de sa séquence.

    ``0.0`` pour un clip normal, une séquence introuvable ou un clip qui
    tient dans sa source. Sert à l'affichage (zone hachurée).
    """
    if not clip.sequence_id:
        return 0.0
    sequence = project.get_sequence(clip.sequence_id)
    if sequence is None:
        return 0.0
    return max(0.0, clip.source_out - sequence.duration)


# ---------------------------------------------------------------------------
# Graphe des dépendances et cycles
# ---------------------------------------------------------------------------


def dependency_graph(project: Project) -> dict[str, set[str]]:
    """``{séquence: séquences référencées directement}`` pour tout le projet."""
    return {sequence.id: sequence.nested_sequence_ids for sequence in project.sequences}


def reachable_sequences(project: Project, start_id: str) -> set[str]:
    """Séquences atteignables depuis ``start_id`` (exclue sauf cycle).

    Parcours itératif avec ensemble des visités : termine toujours, même
    sur un graphe cyclique ou des références inconnues.
    """
    return graph_cycles.reachable(dependency_graph(project), start_id)


def would_create_cycle(project: Project, parent_id: str, child_id: str) -> bool:
    """Insérer ``child_id`` dans ``parent_id`` créerait-il un cycle ?

    Vrai si ``child_id == parent_id`` ou si ``parent_id`` est déjà
    atteignable depuis ``child_id`` (le parent serait alors son propre
    descendant).
    """
    return graph_cycles.would_create_cycle(dependency_graph(project), parent_id, child_id)


def find_cycles(project: Project) -> list[tuple[str, ...]]:
    """Cycles du graphe des séquences (composantes fortement connexes).

    Chaque cycle est un tuple d'identifiants trié. Une auto-référence
    (A contient A) est un cycle d'un élément. Voir
    :func:`core.graph_cycles.find_cycles` (partagé avec le parentage des calques).
    """
    return graph_cycles.find_cycles(dependency_graph(project))


def nesting_depth(project: Project, sequence_id: str) -> int:
    """Profondeur d'imbrication maximale sous ``sequence_id`` (0 = aucune).

    Les cycles sont ignorés (le parcours ne revisite pas un nœud de la
    chaîne courante) et la valeur est plafonnée à
    ``MAX_NESTING_DEPTH + 1``.
    """
    graph = dependency_graph(project)
    best = 0
    stack: list[tuple[str, tuple[str, ...]]] = [(sequence_id, (sequence_id,))]
    while stack:
        node, chain = stack.pop()
        depth = len(chain) - 1
        best = max(best, depth)
        if depth > MAX_NESTING_DEPTH:
            continue
        for child in graph.get(node, ()):
            if child in chain or child not in graph:
                continue
            stack.append((child, chain + (child,)))
    return min(best, MAX_NESTING_DEPTH + 1)


def sequence_usages(project: Project, sequence_id: str) -> list[SequenceUsage]:
    """Tous les clips (toutes séquences) qui référencent ``sequence_id``."""
    usages: list[SequenceUsage] = []
    for parent in project.sequences:
        for track in parent.tracks:
            for clip in track.clips:
                if clip.sequence_id == sequence_id:
                    usages.append(
                        SequenceUsage(
                            parent_sequence_id=parent.id,
                            parent_sequence_name=parent.name,
                            track_id=track.id,
                            clip_id=clip.id,
                            clip_label=clip.label,
                            timeline_start=clip.timeline_start,
                        )
                    )
    return usages


def dependent_sequence_ids(project: Project, sequence_id: str) -> set[str]:
    """Séquences qui contiennent ``sequence_id``, directement ou non."""
    parents: dict[str, set[str]] = {}
    for parent, children in dependency_graph(project).items():
        for child in children:
            parents.setdefault(child, set()).add(parent)
    seen: set[str] = set()
    stack = list(parents.get(sequence_id, ()))
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(parents.get(current, ()))
    seen.discard(sequence_id)
    return seen


def dependent_nested_clip_ids(project: Project, sequence_id: str) -> set[str]:
    """Clips imbriqués dont le rendu dépend de ``sequence_id``.

    Ce sont les clips qui référencent la séquence, ou une séquence qui la
    contient : quand ``sequence_id`` change, seuls les segments d'aperçu
    de ces clips sont à invalider.
    """
    targets = dependent_sequence_ids(project, sequence_id) | {sequence_id}
    return {
        clip.id
        for sequence in project.sequences
        for track in sequence.tracks
        for clip in track.clips
        if clip.sequence_id in targets
    }


def sequence_issues(project: Project) -> list[SequenceIssue]:
    """Diagnostic complet : références cassées, cycles, profondeur excessive."""
    issues: list[SequenceIssue] = []
    known = {sequence.id for sequence in project.sequences}
    for sequence in project.sequences:
        for track in sequence.tracks:
            for clip in track.clips:
                if clip.sequence_id and clip.sequence_id not in known:
                    issues.append(
                        SequenceIssue(
                            kind="missing",
                            sequence_id=sequence.id,
                            clip_id=clip.id,
                            message=(
                                f"Le clip « {clip.label or clip.id} » de « {sequence.name} » "
                                f"référence une séquence introuvable ({clip.sequence_id})."
                            ),
                        )
                    )
    names = {sequence.id: sequence.name for sequence in project.sequences}
    for cycle in find_cycles(project):
        members = set(cycle)
        for sequence in project.sequences:
            if sequence.id not in members:
                continue
            for track in sequence.tracks:
                for clip in track.clips:
                    if clip.sequence_id in members:
                        issues.append(
                            SequenceIssue(
                                kind="cycle",
                                sequence_id=sequence.id,
                                clip_id=clip.id,
                                message=(
                                    "Imbrication circulaire : "
                                    + " → ".join(names.get(item, item) for item in cycle)
                                    + ". Ce clip est rendu vide."
                                ),
                            )
                        )
    for sequence in project.sequences:
        if nesting_depth(project, sequence.id) > MAX_NESTING_DEPTH:
            issues.append(
                SequenceIssue(
                    kind="depth",
                    sequence_id=sequence.id,
                    clip_id="",
                    message=(
                        f"« {sequence.name} » dépasse {MAX_NESTING_DEPTH} niveaux "
                        "d'imbrication : les niveaux suivants ne sont pas rendus."
                    ),
                )
            )
    return issues


def nested_clip_status(project: Project, clip: Clip, *, cycles=None) -> str:
    """État d'affichage d'un clip : ``""``, ``"missing"``, ``"cycle"`` ou ``"overflow"``."""
    if not clip.sequence_id:
        return ""
    sequence = project.get_sequence(clip.sequence_id)
    if sequence is None:
        return "missing"
    if cycles is None:
        cycles = find_cycles(project)
    if any(clip.sequence_id in cycle for cycle in cycles):
        return "cycle"
    if clip.source_out > sequence.duration + _EPSILON:
        return "overflow"
    return ""


# ---------------------------------------------------------------------------
# Création, renommage, duplication, suppression
# ---------------------------------------------------------------------------


def _blank_track_like(track: Track) -> Track:
    return Track(
        id=track.id,
        name=track.name,
        type=track.type,
        height_mode=track.height_mode,
    )


def create_sequence(
    project: Project,
    name: str | None = None,
    *,
    width: int | None = None,
    height: int | None = None,
    fps: float | None = None,
    tracks: list[Track] | None = None,
    activate: bool = False,
) -> Sequence:
    """Ajoute une séquence vide au projet et la retourne.

    Par défaut, elle reprend la résolution, le fps et la **structure de
    pistes** (sans clips) de la séquence active : une nouvelle séquence
    est immédiatement éditable avec les mêmes pistes.
    """
    active = project.active_sequence
    if tracks is None:
        tracks = [_blank_track_like(track) for track in active.tracks]
        if not tracks:
            tracks = [Track(id="V1", name="V1", type="video"), Track(id="A1", name="A1", type="audio")]
    sequence = Sequence(
        id=new_sequence_id(),
        name=unique_sequence_name(project, name or "Séquence"),
        width=int(width or active.width),
        height=int(height or active.height),
        fps=float(fps or active.fps),
        tracks=tracks,
    )
    project.sequences.append(sequence)
    if activate:
        project.active_sequence_id = sequence.id
    return sequence


def rename_sequence(project: Project, sequence_id: str, name: str) -> Sequence:
    """Renomme une séquence. Un nom vide est refusé."""
    clean = (name or "").strip()
    if not clean:
        raise SequenceError("Le nom d'une séquence ne peut pas être vide.")
    sequence = find_sequence(project, sequence_id)
    sequence.name = clean
    return sequence


def duplicate_sequence(
    project: Project, sequence_id: str, name: str | None = None
) -> Sequence:
    """Copie indépendante de la séquence (nouvel ID, nouveaux IDs de clips).

    La timeline est copiée ; les médias, effets et images-clés sont les
    mêmes. Les clips imbriqués de la copie **continuent de référencer les
    mêmes séquences** : pas de copie profonde récursive de l'arbre.
    """
    source = find_sequence(project, sequence_id)
    clone = deepcopy(source)
    clone.id = new_sequence_id()
    clone.name = unique_sequence_name(project, name or f"{source.name} copie")
    renamed: dict[str, str] = {}
    for track in clone.tracks:
        for clip in track.clips:
            new_id = _new_clip_id()
            renamed[clip.id] = new_id
            clip.id = new_id
    clone.transitions = [
        replace(
            transition,
            id=f"transition-{uuid.uuid4().hex[:12]}",
            from_clip_id=renamed.get(transition.from_clip_id, transition.from_clip_id),
            to_clip_id=renamed.get(transition.to_clip_id, transition.to_clip_id),
        )
        for transition in clone.transitions
    ]
    for marker in clone.markers:
        marker.id = f"marker-{uuid.uuid4().hex[:8]}"
    project.sequences.append(clone)
    return clone


def delete_sequence(
    project: Project, sequence_id: str, *, force: bool = False
) -> list[SequenceUsage]:
    """Supprime une séquence. Retourne les usages rendus « hors ligne ».

    - La dernière séquence d'un projet ne peut pas être supprimée.
    - Une séquence utilisée ailleurs lève :class:`SequenceInUseError`
      (avec la liste des usages) sauf ``force=True``. Une suppression
      forcée laisse les clips en place : ils deviennent *hors ligne*
      (référence introuvable, rendus vides, signalés dans l'interface).
      Rien n'est cassé silencieusement et l'opération reste annulable.
    - Si la séquence supprimée était active, la première séquence restante
      devient active.
    """
    sequence = find_sequence(project, sequence_id)
    if len(project.sequences) <= 1:
        raise SequenceError("Impossible de supprimer la dernière séquence du projet.")
    usages = [
        usage for usage in sequence_usages(project, sequence_id)
        if usage.parent_sequence_id != sequence_id
    ]
    if usages and not force:
        places = ", ".join(
            f"« {usage.parent_sequence_name} » ({usage.clip_label or usage.clip_id})"
            for usage in usages[:5]
        )
        more = f" et {len(usages) - 5} autre(s)" if len(usages) > 5 else ""
        raise SequenceInUseError(
            f"La séquence « {sequence.name} » est utilisée dans {places}{more}.",
            usages,
        )
    project.sequences.remove(sequence)
    if project.active_sequence_id == sequence_id:
        project.active_sequence_id = project.sequences[0].id
    return usages


# ---------------------------------------------------------------------------
# Insertion d'une séquence comme clip
# ---------------------------------------------------------------------------


def check_can_nest(project: Project, parent_id: str, child_id: str) -> None:
    """Lève :class:`SequenceCycleError` si l'imbrication est interdite."""
    find_sequence(project, child_id)
    find_sequence(project, parent_id)
    if parent_id == child_id:
        raise SequenceCycleError("Une séquence ne peut pas se contenir elle-même.")
    if would_create_cycle(project, parent_id, child_id):
        names = {sequence.id: sequence.name for sequence in project.sequences}
        raise SequenceCycleError(
            f"Imbrication circulaire refusée : « {names[child_id]} » contient déjà "
            f"« {names[parent_id]} »."
        )
    if nesting_depth(project, child_id) + 1 > MAX_NESTING_DEPTH:
        raise SequenceError(
            f"Imbrication trop profonde (plus de {MAX_NESTING_DEPTH} niveaux)."
        )


def insert_sequence_clip(
    project: Project,
    sequence_id: str,
    track_id: str,
    timeline_start: float,
    *,
    parent_sequence_id: str | None = None,
    source_in: float = 0.0,
    source_out: float | None = None,
) -> Clip:
    """Insère ``sequence_id`` comme clip dans ``parent_sequence_id`` (active par défaut).

    Raises:
        KeyError: séquence ou piste inconnue.
        SequenceCycleError: l'insertion créerait un cycle.
        SequenceError: séquence source vide, piste incompatible ou verrouillée.
    """
    parent_id = parent_sequence_id or project.active_sequence_id
    check_can_nest(project, parent_id, sequence_id)
    parent = find_sequence(project, parent_id)
    child = find_sequence(project, sequence_id)
    if timeline_start < 0.0:
        raise SequenceError("Impossible d'insérer une séquence avant 0 seconde.")
    track = next((item for item in parent.tracks if item.id == track_id), None)
    if track is None:
        raise KeyError(f"Piste '{track_id}' introuvable dans « {parent.name} ».")
    if track.type not in NESTABLE_TRACK_TYPES:
        raise SequenceError(
            f"Une séquence ne peut être placée que sur une piste vidéo ou audio "
            f"(piste '{track.id}' de type '{track.type}')."
        )
    if track.locked:
        raise SequenceError(f"La piste '{track.id}' est verrouillée : modifications refusées.")
    out = child.duration if source_out is None else float(source_out)
    if out <= source_in + _EPSILON:
        raise SequenceError(f"La séquence « {child.name} » est vide : rien à insérer.")
    clip = Clip(
        id=_new_clip_id(),
        asset_id="",
        track_id=track.id,
        timeline_start=float(timeline_start),
        source_in=float(source_in),
        source_out=out,
        label=child.name,
        sequence_id=child.id,
    )
    track.clips.append(clip)
    return clip


# ---------------------------------------------------------------------------
# Créer une séquence à partir de la sélection
# ---------------------------------------------------------------------------


def create_sequence_from_selection(
    project: Project, clip_ids, name: str | None = None
) -> NestResult:
    """Déplace les clips sélectionnés dans une nouvelle séquence imbriquée.

    Workflow (séquence active = parent) :

    1. une nouvelle séquence reçoit les clips, décalés pour que le premier
       commence à 0 : positions relatives, effets, images-clés, audio,
       remappage et étalonnage sont conservés (les clips sont **déplacés**,
       leurs identifiants ne changent pas) ;
    2. chaque piste concernée est recréée dans la séquence avec les mêmes
       identifiant, nom, type, visibilité et mute ; les réglages audio de
       piste (volume, pan, automation recalée) sont recopiés, sauf sur la
       piste qui accueille le clip imbriqué : son volume continue de
       s'appliquer au clip imbriqué dans le parent ;
    3. les transitions dont les deux clips sont sélectionnés suivent les
       clips ; celles qui relieraient un clip resté dehors sont retirées
       (``dropped_transition_ids``) ;
    4. les repères du parent compris dans la plage sont **copiés** ;
    5. la sélection est remplacée par un clip imbriqué sur la piste vidéo
       sélectionnée la plus basse (ou audio si la sélection n'a pas de
       vidéo), à la position du premier clip.

    Toute la modification est faite sur le projet passé : l'appelant
    enregistre ensuite **une** entrée d'historique (annulable en une fois).
    """
    wanted = [clip_id for clip_id in dict.fromkeys(clip_ids or ())]
    if not wanted:
        raise SequenceError("Sélectionnez au moins un clip.")
    parent = project.active_sequence
    located: list[tuple[int, Track, Clip]] = []
    for track_index, track in enumerate(parent.tracks):
        for clip in track.clips:
            if clip.id in wanted:
                located.append((track_index, track, clip))
    missing = set(wanted) - {clip.id for _index, _track, clip in located}
    if missing:
        raise KeyError(f"Clip(s) introuvable(s) dans la séquence active : {sorted(missing)}.")
    locked = sorted({track.id for _index, track, _clip in located if track.locked})
    if locked:
        raise SequenceError(f"Piste(s) verrouillée(s) : {', '.join(locked)}.")
    start = min(clip.timeline_start for _i, _t, clip in located)
    end = max(clip.timeline_start + clip.duration for _i, _t, clip in located)
    if end - start <= _EPSILON:
        raise SequenceError("La sélection n'a pas de durée.")

    # Piste hôte du clip imbriqué : la piste vidéo sélectionnée la plus
    # basse (rendue en dessous des autres), sinon audio, sinon la première
    # piste vidéo éditable du parent.
    selected_tracks = sorted({index: track for index, track, _clip in located}.items())
    host = next((track for _i, track in selected_tracks if track.type == "video"), None)
    if host is None:
        host = next((track for _i, track in selected_tracks if track.type == "audio"), None)
    if host is None:
        host = next(
            (track for track in parent.tracks if track.type == "video" and not track.locked),
            None,
        )
    if host is None:
        raise SequenceError(
            "Aucune piste vidéo ou audio disponible pour accueillir la séquence imbriquée."
        )

    from .audio_automation import AutomationPoint  # import tardif (pas de cycle)

    inner_tracks: list[Track] = []
    for _index, track in selected_tracks:
        copy = Track(
            id=track.id,
            name=track.name,
            type=track.type,
            visible=track.visible,
            muted=track.muted,
            height_mode=track.height_mode,
            audio_role=track.audio_role,
        )
        if track is not host:
            copy.volume_db = track.volume_db
            copy.pan = track.pan
            copy.ducking_config = deepcopy(track.ducking_config)
            points = list(getattr(track.automation, "points", track.automation) or [])
            shifted = []
            for point in points:
                time_value = float(getattr(point, "time_seconds", 0.0)) - start
                if time_value < 0.0:
                    continue
                try:
                    shifted.append(
                        AutomationPoint(
                            time_seconds=time_value,
                            gain_db=point.gain_db,
                            fade_seconds=getattr(point, "fade_seconds", 0.0),
                        )
                    )
                except (TypeError, ValueError):
                    continue
            copy.automation = shifted
        inner_tracks.append(copy)
    by_track_id = {track.id: track for track in inner_tracks}

    moved_ids: set[str] = set()
    for _index, track, clip in located:
        track.clips.remove(clip)
        clip.timeline_start = max(0.0, clip.timeline_start - start)
        by_track_id[track.id].clips.append(clip)
        moved_ids.add(clip.id)
    for track in inner_tracks:
        track.clips.sort(key=lambda item: item.timeline_start)

    inner_transitions = []
    dropped: list[str] = []
    for transition in list(parent.transitions):
        inside = (transition.from_clip_id in moved_ids, transition.to_clip_id in moved_ids)
        if all(inside):
            inner_transitions.append(transition)
            parent.transitions.remove(transition)
        elif any(inside):
            dropped.append(transition.id)
            parent.transitions.remove(transition)

    inner_markers = [
        Marker(
            id=f"marker-{uuid.uuid4().hex[:8]}",
            time_seconds=marker.time_seconds - start,
            name=marker.name,
            category=marker.category,
        )
        for marker in parent.markers
        if start - _EPSILON <= marker.time_seconds < end
    ]

    inner_ids = set(by_track_id)
    inner_ducking = [
        deepcopy(sidechain)
        for sidechain in parent.ducking_sidechains
        if getattr(sidechain, "music_track_id", None) in inner_ids
        and getattr(sidechain, "voice_track_id", None) in inner_ids
    ]

    sequence = Sequence(
        id=new_sequence_id(),
        name=unique_sequence_name(project, name or "Séquence imbriquée"),
        width=parent.width,
        height=parent.height,
        fps=parent.fps,
        tracks=inner_tracks,
        markers=inner_markers,
        transitions=inner_transitions,
        ducking_sidechains=inner_ducking,
    )
    project.sequences.append(sequence)
    nested = Clip(
        id=_new_clip_id(),
        asset_id="",
        track_id=host.id,
        timeline_start=start,
        source_in=0.0,
        source_out=end - start,
        label=sequence.name,
        sequence_id=sequence.id,
    )
    host.clips.append(nested)
    return NestResult(sequence=sequence, clip=nested, dropped_transition_ids=tuple(dropped))


# ---------------------------------------------------------------------------
# Politique de durée
# ---------------------------------------------------------------------------


def clamp_nested_clips(
    project: Project, sequence_ids: set[str] | None = None
) -> list[ClampAdjustment]:
    """Ramène les clips imbriqués qui dépassent la fin de leur source.

    Politique documentée (``docs/nested-sequences.md``) :

    - **séquence rallongée** : rien ne change, le clip garde son in/out ;
      l'utilisateur étend le clip (trim) s'il veut voir la suite ;
    - **séquence raccourcie** : le ``source_out`` d'un clip qui dépasse la
      nouvelle fin est ramené à cette fin. L'appelant (l'interface)
      applique ce recadrage **dans la même entrée d'historique** que
      l'édition qui l'a provoqué, et l'annonce : c'est visible et annulé
      en une fois avec l'action utilisateur ;
    - un clip qui commencerait après la nouvelle fin n'est **pas** supprimé
      (le montage parent n'est jamais détruit) : il est rendu vide et
      signalé « hors source » jusqu'à ce que la source rallonge ;
    - au chargement d'un projet, rien n'est modifié : le rendu borne de
      lui-même les clips qui débordent.

    Args:
        sequence_ids: séquences sources à considérer (toutes par défaut).
    """
    durations: dict[str, float] = {}
    adjustments: list[ClampAdjustment] = []
    for parent in project.sequences:
        for track in parent.tracks:
            for clip in track.clips:
                target = clip.sequence_id
                if not target or (sequence_ids is not None and target not in sequence_ids):
                    continue
                if target not in durations:
                    source = project.get_sequence(target)
                    durations[target] = source.duration if source is not None else -1.0
                limit = durations[target]
                if limit < 0.0 or clip.source_out <= limit + _EPSILON:
                    continue
                if limit <= clip.source_in + _EPSILON:
                    continue  # entièrement hors source : conservé, rendu vide
                old = clip.source_out
                clip.source_out = limit
                remapping = clip.time_remapping
                if getattr(remapping, "freeze_source_time", 0.0) > limit:
                    clip.time_remapping = replace(remapping, freeze_source_time=limit)
                clip._rebalance_fades()
                adjustments.append(
                    ClampAdjustment(
                        sequence_id=parent.id,
                        clip_id=clip.id,
                        old_source_out=old,
                        new_source_out=limit,
                    )
                )
    return adjustments


# ---------------------------------------------------------------------------
# Temps parent → temps de la séquence imbriquée
# ---------------------------------------------------------------------------


def nested_source_time(clip: Clip, parent_time: float) -> float | None:
    """Instant de la séquence imbriquée lu par ``clip`` à ``parent_time``.

    ``None`` si ``parent_time`` est hors du clip. Tient compte de la
    vitesse, du reverse et de l'arrêt sur image du clip.
    """
    from .time_remapping import timeline_to_source_time

    local = parent_time - clip.timeline_start
    if local < 0.0 or local >= clip.duration:
        return None
    remapping = clip.time_remapping
    try:
        return timeline_to_source_time(
            timeline_time=local,
            source_in=clip.source_in,
            source_out=clip.source_out,
            speed=remapping.speed,
            reverse=remapping.reverse,
            freeze_mode=remapping.freeze_mode,
            freeze_source_time=remapping.freeze_source_time,
        )
    except ValueError:
        return clip.source_in + local


def nested_source_window(clip: Clip, low: float, high: float) -> tuple[float, float]:
    """Plage de la séquence imbriquée lue pendant ``[low, high)`` (temps parent).

    Toujours incluse dans ``[source_in, source_out]``. En arrêt sur image,
    la plage se réduit à l'image figée ; en cas de doute (remappage
    exotique) on retourne toute la plage source, ce qui reste correct.
    """
    remapping = clip.time_remapping
    start = clip.timeline_start
    end = start + clip.duration
    low = max(low, start)
    high = min(high, end)
    if high <= low:
        return clip.source_in, clip.source_in
    if getattr(remapping.freeze_mode, "value", remapping.freeze_mode) == "freeze":
        frozen = float(remapping.freeze_source_time)
        return frozen, frozen + 1e-3
    speed = float(remapping.speed) or 1.0
    if remapping.reverse:
        a = clip.source_out - (high - start) * speed
        b = clip.source_out - (low - start) * speed
    else:
        a = clip.source_in + (low - start) * speed
        b = clip.source_in + (high - start) * speed
    return max(clip.source_in, a), min(clip.source_out, b)


__all__ = [
    "MAX_NESTING_DEPTH",
    "NESTABLE_TRACK_TYPES",
    "ClampAdjustment",
    "NestResult",
    "SequenceCycleError",
    "SequenceError",
    "SequenceInUseError",
    "SequenceIssue",
    "SequenceUsage",
    "check_can_nest",
    "clamp_nested_clips",
    "clip_media_type",
    "clip_source_limit",
    "create_sequence",
    "create_sequence_from_selection",
    "delete_sequence",
    "dependency_graph",
    "dependent_nested_clip_ids",
    "dependent_sequence_ids",
    "duplicate_sequence",
    "find_cycles",
    "find_sequence",
    "insert_sequence_clip",
    "nested_clip_status",
    "nested_overflow",
    "nested_source_time",
    "nested_source_window",
    "nesting_depth",
    "new_sequence_id",
    "reachable_sequences",
    "rename_sequence",
    "sequence_issues",
    "sequence_usages",
    "unique_sequence_name",
    "would_create_cycle",
]
