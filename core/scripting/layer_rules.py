"""Règles communes des verbes de calques graphiques (lot 4 de l'extension 0.8.0) : bornes, verrous, profondeur de la hiérarchie.

Aucun verbe ici : ces fonctions travaillent sur le projet et des identifiants, sans importer la table des verbes (``verbs.py`` et
``layer_verbs.py`` les emploient toutes deux).

- **Bornes** : une liste de calques, le nombre de calques d'un projet et la profondeur des hiérarchies sont bornés avant d'allouer
  ou de parcourir (un script de 5 000 opérations ne doit pas pouvoir doubler un groupe jusqu'à saturer la mémoire, ni empiler
  mille parentages que l'évaluation de la scène parcourt récursivement).
- **Verrous** : un verrou vaut pour tous les chemins d'entrée. Un verbe qui modifie des **tiers** (les calques que renumérote un
  changement d'ordre, les enfants d'un parent supprimé, les membres d'un groupe défait) refuse si l'un d'eux est un calque ou une
  piste de graphiques verrouillé : :func:`frozen_layers` photographie ces calques avant, :func:`require_untouched` les compare
  après (la session restaure le projet quand le verbe lève, donc rien ne reste d'une opération refusée).
- **Hiérarchie** : la profondeur se calcule sur le graphe **résultant** (ancêtres du parent compris, descendants de l'enfant
  compris) et une boucle y est refusée, jamais écrêtée.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from ..graphics import GraphicOverlay, GraphicType
from ..mograph_layers import layer_clips
from ..mograph_scene import box_corners
from ..project_model import Clip, Project
from . import resource_limits
from .resource_limits import require_media_room, require_room_in, total_clips

MAX_LAYERS_PER_OPERATION = 200
"""Calques cités par une seule opération (groupe, suppression, duplication)."""
MAX_LAYERS_IN_PROJECT = 5_000
"""Calques d'un projet au-delà desquels un script n'en crée plus (le nombre maximal d'opérations d'un script)."""
MAX_HIERARCHY_DEPTH = 32
"""Niveaux de parentage, et de groupes imbriqués, au plus : l'évaluation de la scène et l'arbre du panneau Calques les parcourent récursivement."""
APPEARANCE_TOLERANCE = 0.01
"""Pixels : écart permis, à l'instant de référence, entre l'apparence d'un calque avant et après un changement de hiérarchie."""


def graphic_of(clip: Clip) -> GraphicOverlay:
    """Le contenu graphique d'un calque (ce que les fonctions de calques garantissent déjà) ; ``ValueError`` pour un clip qui n'en a pas."""
    graphic = clip.graphic
    if not isinstance(graphic, GraphicOverlay):
        raise ValueError(f"Le clip '{clip.id}' n'est pas un calque graphique.")
    return graphic


# --- Bornes -----------------------------------------------------------------------------------------------------------


def total_layers(project: Project) -> int:
    """Les calques graphiques de TOUTES les séquences du projet (le plafond est celui du projet, pas de la séquence active)."""
    return sum(
        1 for sequence in project.sequences for track in sequence.tracks if track.type == "graphics"
        for clip in track.clips if isinstance(clip.graphic, GraphicOverlay)
    )


def layers_in(sequence: Any) -> int:
    """Les calques graphiques d'UNE séquence (comme :func:`total_layers`, sur une seule)."""
    return sum(1 for track in sequence.tracks if track.type == "graphics" for clip in track.clips if isinstance(clip.graphic, GraphicOverlay))


def require_room(project: Project, adding: int) -> None:
    """Refuse, AVANT toute création, ``adding`` calques de plus si le projet (toutes séquences) dépasserait :data:`MAX_LAYERS_IN_PROJECT`."""
    count = total_layers(project)
    if count + adding > MAX_LAYERS_IN_PROJECT:
        raise ValueError(f"Le projet compte déjà {count} calques : {adding} de plus dépasseraient la limite de {MAX_LAYERS_IN_PROJECT}.")


def require_layers_room(project: Project, layers: int, *, media: int | None = None) -> None:
    """Refuse, AVANT toute création, ``layers`` calques de plus : ils comptent aussi comme clips (le plafond de clips peut être atteint avec peu de
    calques) et chacun crée un média interne (``media`` : le nombre exact de médias que l'opération ajoute, ``layers`` par défaut)."""
    require_room(project, layers)
    require_room_in(total_clips(project), layers, resource_limits.MAX_CLIPS_IN_PROJECT, "clips", "Le projet")
    require_media_room(project, layers if media is None else media)


# --- Verrous ----------------------------------------------------------------------------------------------------------


def _layers(project: Project) -> dict[str, tuple[Any, Clip]]:
    return {clip.id: (track, clip) for _index, track, clip in layer_clips(project)}


def require_editable_container(project: Project, clip: Clip, *, ignoring: frozenset[str] = frozenset()) -> None:
    """Un groupe verrouillé (ou sur une piste verrouillée) ne change pas de contenu : refuse si le groupe de ``clip`` l'est.

    ``ignoring`` : des groupes qui disparaissent avec l'opération (leur verrou ne protège plus rien)."""
    group_id = clip.graphic.group_id if isinstance(clip.graphic, GraphicOverlay) else ""
    if not group_id or group_id in ignoring:
        return
    found = _layers(project).get(group_id)
    if found is None:
        return                                          # référence cassée d'un fichier retouché : rien à protéger
    track, group = found
    if track.locked or graphic_of(group).locked:
        raise ValueError(f"Le groupe '{group_id}' est verrouillé : son contenu ne change pas.")


def frozen_layers(project: Project) -> dict[str, Clip]:
    """Copie des calques verrouillés (le calque l'est, ou sa piste de graphiques) : l'état que :func:`require_untouched` compare
    après l'opération."""
    frozen: dict[str, Clip] = {}
    for track in project.tracks:
        if track.type != "graphics":
            continue
        for clip in track.clips:
            if isinstance(clip.graphic, GraphicOverlay) and (track.locked or clip.graphic.locked):
                frozen[clip.id] = deepcopy(clip)
    return frozen


def require_untouched(project: Project, frozen: dict[str, Clip]) -> None:
    """Lève si un calque de ``frozen`` a disparu ou changé (champ, rang, piste) depuis la photographie."""
    present = {clip.id: clip for track in project.tracks for clip in track.clips}
    for clip_id, before in frozen.items():
        now = present.get(clip_id)
        if now is None or now != before:
            raise ValueError(f"Le calque verrouillé '{clip_id}' serait modifié par cette opération : refusée.")


# --- Hiérarchie -------------------------------------------------------------------------------------------------------


def _parents(project: Project) -> tuple[dict[str, str], dict[str, str]]:
    """``(parent de transform, groupe)`` de chaque calque, avec la règle de la scène (:class:`~core.mograph_scene.GraphicsScene`) :
    le parent explicite s'il existe (calque ou clip vidéo) et n'est pas le calque lui-même, sinon le groupe."""
    known = {clip.id for track in project.tracks if track.type in ("graphics", "video") for clip in track.clips}
    layers = _layers(project)
    groups = {cid for cid, (_t, clip) in layers.items() if graphic_of(clip).type == GraphicType.GROUP}
    group_of: dict[str, str] = {}
    transform_of: dict[str, str] = {}
    for cid, (_t, clip) in layers.items():
        graphic = graphic_of(clip)
        group = graphic.group_id if graphic.group_id in groups and graphic.group_id != cid else ""
        if group:
            group_of[cid] = group
        parent = graphic.parent_id if graphic.parent_id in known and graphic.parent_id != cid else group
        if parent:
            transform_of[cid] = parent
    return transform_of, group_of


def _extent(parents: dict[str, str], start: str, what: str) -> int:
    """Nombre de niveaux de la chaîne qui passe par ``start`` (ancêtres + lui + descendants) ; lève sur une boucle."""
    up, seen, current = 0, {start}, parents.get(start, "")
    while current:
        if current in seen:
            raise ValueError(f"Ce changement créerait une boucle ({what}).")
        seen.add(current)
        up += 1
        current = parents.get(current, "")
    children: dict[str, list[str]] = {}
    for child, parent in parents.items():
        children.setdefault(parent, []).append(child)
    down, level, visited = 0, [start], {start}
    while level:
        following = []
        for node in level:
            for child in children.get(node, ()):
                if child in visited:
                    raise ValueError(f"Ce changement créerait une boucle ({what}).")
                visited.add(child)
                following.append(child)
        if following:
            down += 1
        level = following
    return up + 1 + down


def require_shallow(project: Project, *clip_ids: str) -> None:
    """Refuse si, **après** le changement, la chaîne de parentage ou celle des groupes qui passe par l'un de ``clip_ids`` dépasse
    :data:`MAX_HIERARCHY_DEPTH` niveaux, ou forme une boucle."""
    transform_of, group_of = _parents(project)
    for what, parents in (("parentage", transform_of), ("groupes imbriqués", group_of)):
        for clip_id in clip_ids:
            if _extent(parents, clip_id, what) > MAX_HIERARCHY_DEPTH:
                raise ValueError(f"Ce changement ferait plus de {MAX_HIERARCHY_DEPTH} niveaux de {what}.")


def appearance(project: Project, clip_id: str, at: float) -> list[tuple[float, float]]:
    """Les quatre coins du calque à l'instant ``at`` (pixels du cadre) : ce que voit le spectateur, suivi et images clés compris."""
    from ..mograph_layers import scene_for_project

    evaluated = scene_for_project(project).evaluate(clip_id, at)
    return box_corners(evaluated.world, *evaluated.box)


def require_same_appearance(before: list[tuple[float, float]], after: list[tuple[float, float]], at: float) -> None:
    """Refuse un changement de hiérarchie qui déplacerait le calque à l'instant ``at`` : le modèle garde l'apparence en recalculant
    le transform, mais borne ce qu'il ne sait pas représenter (échelle, position) et ignore un parent à échelle nulle ou un calque
    dont le suivi pilote la position ; un script le reçoit refusé plutôt que de voir le calque sauter."""
    worst = max((abs(a - b) for (ax, ay), (bx, by) in zip(before, after) for a, b in ((ax, bx), (ay, by))), default=0.0)
    if worst > APPEARANCE_TOLERANCE:
        raise ValueError(
            f"Ce changement ne peut pas garder l'apparence du calque à {at:g} s (écart de {worst:.2f} px) : "
            "parent à échelle nulle, valeur hors des bornes du transform ou position pilotée par un suivi."
        )


__all__ = [
    "APPEARANCE_TOLERANCE", "MAX_HIERARCHY_DEPTH", "MAX_LAYERS_IN_PROJECT", "MAX_LAYERS_PER_OPERATION", "appearance", "frozen_layers", "graphic_of",
    "require_editable_container", "total_layers", "require_room", "require_same_appearance", "require_shallow", "require_untouched",
]
