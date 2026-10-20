"""Les plafonds de ressources des verbes de script, et leurs gardes (ADR-0005 ; ``docs/scripting.md`` « Plafonds des verbes »).

Six revues de suite ont trouvé le même défaut : un plafond vérifié par un chemin de création et pas par les autres. Ce module est donc le
**seul** endroit où une ressource est comptée et plafonnée : un verbe qui crée appelle la garde de sa ressource (``require_*_room``) AVANT de
créer, avec le nombre EXACT d'objets que l'opération ajoute ; aucun verbe ne recompte pour son compte.

- **Interface non concernée** : seuls les verbes de script sont plafonnés. L'interface (menus, glisser-déposer) et l'ouverture d'un fichier ne
  passent jamais par ici.
- **Rien n'est supprimé ni ramené** : un projet qui dépasse déjà un plafond (fichier ouvert dans l'application) n'est pas modifié ; le verbe
  refuse seulement d'AJOUTER (``adding > 0``). Un refus lève ``ValueError`` avant toute écriture.
- **Portée** : les ressources d'un projet (clips, calques, séquences, suiveurs, médias) se comptent sur TOUTES les séquences
  (``Project.tracks`` n'est que la séquence active) ; celles qui appartiennent à une séquence (pistes, transitions, repères, guides) se
  comptent sur la séquence où le verbe écrit, la séquence active ; celles d'un objet (images clés, effets, masques, nœuds…) sur cet objet.
- **Images clés** : UNE seule mesure par clip (:func:`keyframe_count`, transform + animation générique : effets, masques, vitesse, calques),
  pour qu'un verbe ne puisse pas contourner le plafond d'un autre.

Sans import de la table des verbes (comme ``layer_rules``) : ``verbs.py`` et les modules de verbes l'emploient tous.
"""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from typing import Any

from ..project_model import Clip, Project, Track

# --- Clips et images clés (valeurs d'origine de ``auto_rules``) ---------------------------------------------------------------------

MAX_NEW_CLIPS_PER_OPERATION = 500
"""Clips (morceaux de coupe, lignes de sous-titres, plans de diaporama, calques) qu'une seule opération crée."""
MAX_CLIPS_IN_PROJECT = 5_000
"""Clips d'un projet (toutes séquences) au-delà desquels ces verbes n'en créent plus : le nombre maximal d'opérations d'un script."""
MAX_KEYFRAMES_PER_CLIP = 2_000
"""Images clés qu'un clip porte au plus après un verbe de script (transform + animation générique : effets, masques, vitesse, calques)."""

# --- Nouveaux plafonds : larges (plusieurs fois ce qu'un projet réaliste utilise), jamais rencontrés par l'interface ----------------

MAX_TRACKS_PER_SEQUENCE = 100
"""Pistes d'une séquence (un montage réaliste en a de 5 à 30)."""
MAX_MARKERS_PER_SEQUENCE = 5_000
"""Repères d'une séquence."""
MAX_GUIDES_PER_SEQUENCE = 200
"""Guides du moniteur d'une séquence."""
MAX_INSERTS_PER_CHAIN = 32
"""Inserts audio d'une piste, ou d'un bus (chacun ajoute un filtre à la chaîne d'export)."""
MAX_EFFECTS_PER_CLIP = 100
"""Effets visuels d'un clip."""
MAX_AUDIO_EFFECTS_PER_CLIP = 100
"""Effets audio d'un clip."""
MAX_MASKS_PER_CLIP = 64
"""Masques d'un clip (le recadrage et la stabilisation en sont, comme dans le modèle)."""
MAX_COLOR_NODES_PER_CLIP = 64
"""Nœuds d'étalonnage d'un clip (correcteurs et mélangeurs)."""
MAX_COLOR_WINDOWS_PER_CLIP = 64
"""Fenêtres d'étalonnage d'un clip (tous nœuds confondus)."""
MAX_MEDIA_IN_PROJECT = 10_000
"""Médias d'un projet : 5 000 importés (chacun est analysé par ``ffprobe``) et jusqu'à 5 000 médias internes, qu'un calque graphique ou un
sous-titre crée un à un (le plafond de clips les borne). ``import_media`` compte tous les médias, internes compris."""


# --- Le mécanisme commun ------------------------------------------------------------------------------------------------------


def require_count(count: int, limit: int, what: str) -> None:
    """Refuse ``count`` éléments au-delà de ``limit`` (sans en créer un seul)."""
    if count > limit:
        raise ValueError(f"{count} {what} d'un coup dépassent la limite de {limit} par opération.")


def require_room_in(count: int, adding: int, limit: int, what: str, owner: str) -> None:
    """Refuse ``adding`` objets de plus si ``owner`` en porterait plus de ``limit``.

    ``adding <= 0`` n'est jamais refusé : un propriétaire déjà au-delà du plafond (fichier ouvert dans l'application) ne perd rien, et
    une opération qui n'ajoute rien (ou remplace) passe."""
    if adding > 0 and count + adding > limit:
        raise ValueError(f"{owner} compte déjà {count} {what} : {adding} de plus dépasseraient la limite de {limit}.")


# --- Clips --------------------------------------------------------------------------------------------------------------------


def total_clips(project: Project) -> int:
    """Les clips de TOUTES les séquences du projet."""
    return sum(len(track.clips) for sequence in project.sequences for track in sequence.tracks)


def require_clip_room(project: Project, clips: int) -> None:
    """Refuse, AVANT toute création, ``clips`` clips (calques graphiques compris) de plus s'ils dépassaient le plafond de l'opération ou du projet."""
    require_count(clips, MAX_NEW_CLIPS_PER_OPERATION, "clips")
    require_room_in(total_clips(project), clips, MAX_CLIPS_IN_PROJECT, "clips", "Le projet")


def multicam_switch_adds(project: Project, at: float, angle_id: str, segment: Clip) -> int:
    """Clips qu'un changement d'angle crée : un seul (la coupe), et aucun si le segment montre déjà cet angle ou si l'instant est au début
    du segment (``core.multicam_ops.switch_angle`` : l'angle est alors simplement remplacé)."""
    from ..multicam_ops import effective_angle_id

    if effective_angle_id(project, segment) == angle_id:
        return 0
    half_frame = 0.5 / max(1.0, float(project.active_sequence.fps))
    return 0 if at - segment.timeline_start <= half_frame else 1


def tracker_copies(clip: Clip) -> int:
    """Suiveurs qu'une copie ou une moitié de coupe de ``clip`` ajoute au projet (la coupe et la copie reprennent ses suiveurs, mêmes identifiants)."""
    tracking = getattr(clip, "tracking", None)
    return len(tracking.trackers) if tracking is not None else 0


# --- Images clés (UNE mesure par clip) ------------------------------------------------------------------------------------------


def keyframe_count(clip: Clip) -> int:
    """Les images clés d'un clip, toutes familles confondues : transform (``transform_keyframes``) et animation générique (``animation`` :
    paramètres d'effet ``fx.*``, masques ``mask.*``, vitesse ``time.speed``, propriétés de calque ``graphic.*``)."""
    return len(clip.transform_keyframes) + len(clip.animation or ())


def require_keyframes_total(clip: Clip, total: int, what: str) -> None:
    """Refuse, AVANT d'écrire, que le clip porte ``total`` images clés après ``what`` (plus que le plafond ET plus qu'il n'en a déjà)."""
    have = keyframe_count(clip)
    if total > have and total > MAX_KEYFRAMES_PER_CLIP:
        raise ValueError(f"{what} : le clip '{clip.id}' porterait {total} images clés (au plus {MAX_KEYFRAMES_PER_CLIP}) ; il en a déjà {have}.")


def require_keyframe_room(clip: Clip, adding: int, what: str) -> None:
    """Refuse, AVANT d'appeler le modèle, ``adding`` images clés de plus si le clip dépasserait :data:`MAX_KEYFRAMES_PER_CLIP`."""
    if adding > 0:
        require_keyframes_total(clip, keyframe_count(clip) + adding, what)


def require_keyframe_slot(clip: Clip, target: Any, at: float, what: str) -> None:
    """Pose d'UNE image clé de ``target`` à ``at`` : elle en ajoute une, sauf si le modèle en remplace une posée au même instant."""
    from ..animation import normalize_time

    adding = 0 if target.curve(clip).keyframe_at(normalize_time(at)) is not None else 1
    require_keyframe_room(clip, adding, what)


def require_keyframes_after(clip: Clip, edit: Callable[[Clip], Any], what: str) -> None:
    """Rejoue ``edit`` (la fonction du modèle qui pose les images clés d'un clip) sur une COPIE du clip et refuse si le résultat dépasse le
    plafond : le nombre est exactement celui que le modèle écrira (remplacements compris), sans le recopier ici."""
    probe = deepcopy(clip)
    edit(probe)
    require_keyframes_total(clip, keyframe_count(probe), what)


def require_keyframes_after_project_edit(project: Project, clip: Clip, edit: Callable[[Project], Any], what: str) -> None:
    """Comme :func:`require_keyframes_after` pour une fonction du modèle qui prend le PROJET et l'identifiant du clip (vitesse : ``add_speed_point``,
    ``insert_hold``, préréglages de temps). Elle est rejouée sur un projet jetable qui ne contient qu'une copie du clip, seul sur sa piste
    (les médias sont partagés, jamais modifiés par ces fonctions) : le coût ne dépend pas de la taille du projet."""
    owner = next(track for track in project.tracks if any(item is clip for item in track.clips))
    scratch = Project(project.name, width=project.width, height=project.height, fps=project.fps, media_assets=project.media_assets,
                      tracks=[Track(owner.id, owner.name, owner.type)])
    probe = deepcopy(clip)
    scratch.tracks[0].clips.append(probe)
    edit(scratch)
    require_keyframes_total(clip, keyframe_count(probe), what)


# --- Autres ressources --------------------------------------------------------------------------------------------------------


def require_track_room(project: Project, adding: int = 1) -> None:
    """Refuse ``adding`` pistes de plus dans la séquence active (celle où ``add_track`` écrit)."""
    require_room_in(len(project.tracks), adding, MAX_TRACKS_PER_SEQUENCE, "pistes", "La séquence active")


def require_marker_room(project: Project, adding: int = 1) -> None:
    require_room_in(len(project.markers), adding, MAX_MARKERS_PER_SEQUENCE, "repères", "La séquence active")


def require_guide_room(project: Project, adding: int = 1) -> None:
    require_room_in(len(project.active_sequence.guides), adding, MAX_GUIDES_PER_SEQUENCE, "guides", "La séquence active")


def require_insert_room(owner: Any, adding: int = 1) -> None:
    """Inserts d'une piste ou d'un bus (``owner.inserts``)."""
    require_room_in(len(owner.inserts or ()), adding, MAX_INSERTS_PER_CHAIN, "inserts", f"'{owner.id}'")


def require_effect_room(clip: Clip, adding: int = 1) -> None:
    require_room_in(len(clip.effects or ()), adding, MAX_EFFECTS_PER_CLIP, "effets", f"Le clip '{clip.id}'")


def require_audio_effect_room(clip: Clip, adding: int = 1) -> None:
    require_room_in(len(clip.audio_effects or ()), adding, MAX_AUDIO_EFFECTS_PER_CLIP, "effets audio", f"Le clip '{clip.id}'")


def require_mask_room(clip: Clip, adding: int = 1) -> None:
    masks = getattr(clip.compositing, "masks", ()) or ()
    require_room_in(len(masks), adding, MAX_MASKS_PER_CLIP, "masques", f"Le clip '{clip.id}'")


def require_color_node_room(clip: Clip, adding: int = 1) -> None:
    from ..color_nodes import as_graph

    nodes = len(as_graph(getattr(clip, "color_grade", None)).nodes)
    require_room_in(nodes, adding, MAX_COLOR_NODES_PER_CLIP, "nœuds d'étalonnage", f"Le clip '{clip.id}'")


def require_color_window_room(clip: Clip, adding: int = 1) -> None:
    from ..color_nodes import as_graph

    windows = sum(len(node.windows) for node in as_graph(getattr(clip, "color_grade", None)).correctors)
    require_room_in(windows, adding, MAX_COLOR_WINDOWS_PER_CLIP, "fenêtres d'étalonnage", f"Le clip '{clip.id}'")


def require_media_room(project: Project, adding: int = 1) -> None:
    require_room_in(len(project.media_assets), adding, MAX_MEDIA_IN_PROJECT, "médias", "Le projet")


__all__ = [
    "MAX_AUDIO_EFFECTS_PER_CLIP", "MAX_CLIPS_IN_PROJECT", "MAX_COLOR_NODES_PER_CLIP", "MAX_COLOR_WINDOWS_PER_CLIP", "MAX_EFFECTS_PER_CLIP",
    "MAX_GUIDES_PER_SEQUENCE", "MAX_INSERTS_PER_CHAIN", "MAX_KEYFRAMES_PER_CLIP", "MAX_MARKERS_PER_SEQUENCE",
    "MAX_MASKS_PER_CLIP", "MAX_MEDIA_IN_PROJECT", "MAX_NEW_CLIPS_PER_OPERATION", "MAX_TRACKS_PER_SEQUENCE",
    "keyframe_count", "multicam_switch_adds", "require_audio_effect_room", "require_clip_room", "require_color_node_room",
    "require_color_window_room", "require_count", "require_effect_room", "require_guide_room", "require_insert_room",
    "require_keyframe_room", "require_keyframe_slot", "require_keyframes_after", "require_keyframes_after_project_edit", "require_keyframes_total", "require_marker_room",
    "require_mask_room", "require_media_room", "require_room_in", "require_track_room", "total_clips",
    "tracker_copies",
]
