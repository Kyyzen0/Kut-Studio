"""Suivi (tracking), liaisons, stabilisation et recadrage suivi (lot 6 de l'extension 0.8.0).

Chaque verbe appelle la fonction que l'interface appelle (``ui/main_window_mixins/tracking.py``, ``social.py``) : ``core/tracking_ops.py``
et ``core/follow_reframe.py``. Les liaisons et la stabilisation ne stockent aucune image clé (elles sont dérivées dans le plan de
rendu) ; les données d'un tracker sont en temps source.

**Aucune analyse.** Le suivi automatique d'un point (lecture des images du média, corrélation) est un calcul lourd sur un média : un
script est une donnée (ADR-0005, R1/R2) et n'en lance aucune. ``apply_tracking_result`` applique le résultat d'une telle analyse : il
reste un geste de l'interface. Un script fournit en revanche ses points **explicitement** : ``add_tracker`` pose le premier,
``set_tracker_position`` en ajoute (une image chacun, état « manuel » comme une correction à la main), bornés par
:mod:`core.scripting.tracking_rules`. Une liaison ou une stabilisation demande un tracker d'au moins deux images valides.

Règles communes (celles des autres verbes) :

- **verrous** : le clip modifié (piste et calque) est vérifié ; les clips qui **suivent** un tracker dont les données (ou la
  stabilisation de la source) changent le sont aussi, et ``remove_tracker`` (règle du modèle, ``core.tracking_locks``) refuse si l'un des clips dont il réécrit la liaison est verrouillé, AVANT d'écrire ;
- **rien n'est ramené en silence** : instant hors du clip, point hors de l'image, réglage que le modèle écrêterait, remplacement d'une
  liaison existante, réglage ignoré (``smoothing_frames`` hors du lissage « custom ») sont refusés ;
- **identifiants créés** : ``add_tracker`` et ``add_link`` acceptent ``id`` / ``as`` (uniques dans tout le projet).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields as dataclass_fields, replace
from typing import Any

from ..follow_reframe import apply_follow_reframe
from ..object_ids import resolve_object_id
from ..project_model import Clip, Project
from ..timeline_operations import find_clip
from ..tracking_model import (
    AdaptMode, BorderMode, Precision, Smoothing, StabilizationMode, TrackData, Tracker, TrackerSettings, TrackTarget,
)
from ..tracking_ops import (
    add_link, add_tracker, bake_link, media_of, media_rate, remove_correction, remove_link, remove_stabilization,
    remove_tracker, reset_tracker, set_link_enabled, set_stabilization, set_tracker_position, set_tracker_settings,
    source_index_at, stabilization_reference, trackable_clip, tracking_of, update_tracker,
)
from .tracking_rules import (
    MAX_TRACKERS_PER_CLIP, known_link_ids, known_tracker_ids, require_derived_size, require_followers_editable,
    require_span, require_tracker_room, require_within,
)
from .resource_limits import require_keyframes_after
from .verbs import MAX_TIME, Kind, Operation, Param, Verb, _CLIP, _clip_id, _editable_clip, _pick

_VIDEO = ("video",)
_VISUAL = ("video", "graphics")
_MAX_PIXELS = 100_000.0
"""Coordonnée d'un point, en pixels du média : bien au-delà de tout média, pour que le calcul reste exact."""
_NAME_LENGTH = 80
"""Longueur du nom d'un tracker au-delà de laquelle le modèle le tronque."""

_TRACKER = Param("tracker", Kind.SELECTOR, "Le tracker du clip, par identifiant.", of="tracker")
_LINK = Param("link", Kind.SELECTOR, "La liaison portée par le clip, par identifiant.", of="link")
_AT = Param("at", Kind.NUMBER, "Instant sur la timeline, en secondes (dans le clip) : l'image de ce clip qui est montrée alors.",
            minimum=0.0, maximum=MAX_TIME)
_TRACKERS = Param("trackers", Kind.LIST, f"Les trackers (1 à {MAX_TRACKERS_PER_CLIP}), par identifiant, sans doublon.",
                  minimum=1, maximum=MAX_TRACKERS_PER_CLIP, item=Param("tracker", Kind.SELECTOR, "Un tracker du clip.", of="tracker"))
_SETTING_FIELDS = {item.name for item in dataclass_fields(TrackerSettings)}


# --- Aides ---------------------------------------------------------------------------------------------------------------------


def _video_clip(project: Project, selector: str) -> tuple[str, Clip]:
    """Le clip vidéo éditable (piste et calque déverrouillés) qui peut porter des trackers."""
    clip_id = _editable_clip(project, selector, track_types=_VIDEO)
    return clip_id, find_clip(project, clip_id)


def _tracker_of(clip: Clip, selector: str) -> Tracker:
    found: Tracker = _pick(tracking_of(clip).trackers, selector, "Tracker", names=False)
    return found


def _trackers_of(clip: Clip, selectors: list[str]) -> list[Tracker]:
    trackers = [_tracker_of(clip, selector) for selector in selectors]
    if len({tracker.id for tracker in trackers}) != len(trackers):
        raise ValueError("Un tracker est cité deux fois.")
    return trackers


def _require_analysed(tracker: Tracker) -> None:
    if len(tracker.data.valid_indices()) < 2:
        raise ValueError(f"Le tracker '{tracker.id}' n'a pas assez de points : donnez-lui au moins deux images (set_tracker_position).")


def _new_id(a: Mapping[str, Any], taken: set[str], kind: str) -> str | None:
    """L'identifiant imposé (vérifié contre tout le projet), ou ``None`` : le modèle en génère un comme pour l'interface."""
    requested = a.get("id")
    return None if requested is None else resolve_object_id(requested, prefix=kind, kind=kind, taken=taken)


def _in_image(name: str, value: float, size: float, low: float, high: float) -> float:
    if not low * size <= value <= high * size:
        raise ValueError(f"{name} = {value:g} est hors de l'image (de {low * size:g} à {high * size:g} px).")
    return float(value)


# --- Trackers ------------------------------------------------------------------------------------------------------------------


def _add_tracker(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id, clip = _video_clip(project, a["clip"])
        _clip, asset = trackable_clip(project, clip_id)
        require_within(clip, float(a["at"]))
        require_tracker_room(project, clip)
        # Le nouveau tracker porte déjà une image : le total d'images suivies du projet (toutes séquences) compte celle-là aussi.
        require_span(project, TrackData(), source_index_at(clip, media_rate(asset), float(a["at"])))
        x = None if "x" not in a else _in_image("x", float(a["x"]), float(asset.width), 0.0, 1.0)
        y = None if "y" not in a else _in_image("y", float(a["y"]), float(asset.height), 0.0, 1.0)
        tracker_id = _new_id(a, known_tracker_ids(project), "tracker")
        return add_tracker(
            project, clip_id, timeline_time=float(a["at"]), x=x, y=y, name=a.get("name", ""), tracker_id=tracker_id,
        )

    return operation


def _update_tracker(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id, clip = _video_clip(project, a["clip"])
        tracker = _tracker_of(clip, a["tracker"])
        changes = {name: a[name] for name in ("name", "color", "visible", "show_path") if name in a}
        if not changes:
            raise ValueError("update_tracker demande au moins un champ : name, color, visible ou show_path.")
        if "color" in changes and len(str(changes["color"])) != 7:
            raise ValueError("La couleur d'un tracker est « #RRGGBB » (sans transparence).")
        return update_tracker(project, clip_id, tracker.id, **changes)

    return operation


def _set_tracker_settings(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id, clip = _video_clip(project, a["clip"])
        tracker = _tracker_of(clip, a["tracker"])
        changes = {name: a[name] for name in _SETTING_FIELDS if name in a}
        if not changes:
            raise ValueError("set_tracker_settings demande au moins un réglage.")
        # Le modèle ramène à ses bornes ce qui en sort, et ajuste les champs liés (zone de recherche > zone cible, bonne ≥ minimale
        # confiance) : un script voit refusé tout réglage qui ne serait pas enregistré tel qu'écrit, ou qui en changerait un autre.
        result = replace(tracker.settings, **changes)
        for name in _SETTING_FIELDS:
            expected = changes.get(name, getattr(tracker.settings, name))
            if getattr(result, name) != expected:
                raise ValueError(
                    f"Réglage '{name}' : {expected!r} serait ramené à {getattr(result, name)!r} "
                    "(la zone de recherche dépasse la zone cible d'au moins 4 px ; la bonne confiance n'est pas sous la minimale)."
                )
        return set_tracker_settings(project, clip_id, tracker.id, **changes)

    return operation


def _set_tracker_position(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id, clip = _video_clip(project, a["clip"])
        _clip, asset = trackable_clip(project, clip_id)
        tracker = _tracker_of(clip, a["tracker"])
        require_within(clip, float(a["at"]))
        # Les bornes du modèle (media × [−1, 2]) : au-delà, il ramènerait le point au bord.
        x = _in_image("x", float(a["x"]), float(asset.width), -1.0, 2.0)
        y = _in_image("y", float(a["y"]), float(asset.height), -1.0, 2.0)
        index = source_index_at(clip, tracker.data.rate or media_rate(asset), float(a["at"]))
        require_span(project, tracker.data, index)
        require_followers_editable(project, clip_id, tracker.id)
        return set_tracker_position(project, clip_id, tracker.id, float(a["at"]), x, y)

    return operation


def _remove_correction(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id, clip = _video_clip(project, a["clip"])
        tracker = _tracker_of(clip, a["tracker"])
        require_within(clip, float(a["at"]))
        require_followers_editable(project, clip_id, tracker.id)
        if not remove_correction(project, clip_id, tracker.id, float(a["at"])):
            raise ValueError(f"Il n'y a pas de point posé à la main sur le tracker '{tracker.id}' à cet instant.")
        return None

    return operation


def _reset_tracker(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id, clip = _video_clip(project, a["clip"])
        tracker = _tracker_of(clip, a["tracker"])
        direction = {"all": 0, "after": 1, "before": -1}[str(a.get("direction", "all"))]
        if direction == 0 and "at" in a:
            raise ValueError("« at » ne sert qu'avec direction « after » ou « before » (sinon le modèle l'ignorerait).")
        if direction != 0:
            if "at" not in a:
                raise ValueError("direction « after » ou « before » demande « at » (l'image à partir de laquelle effacer).")
            require_within(clip, float(a["at"]))
        require_followers_editable(project, clip_id, tracker.id)
        return reset_tracker(
            project, clip_id, tracker.id, timeline_time=None if "at" not in a else float(a["at"]), direction=direction,
        )

    return operation


def _remove_tracker(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id, clip = _video_clip(project, a["clip"])
        tracker = _tracker_of(clip, a["tracker"])
        return remove_tracker(project, clip_id, tracker.id)

    return operation


# --- Liaisons ------------------------------------------------------------------------------------------------------------------


def _add_link(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        target_id = _editable_clip(project, a["clip"], track_types=_VISUAL)
        source_id = _clip_id(project, a["source"])
        source, _asset = trackable_clip(project, source_id)
        trackers = _trackers_of(source, list(a["trackers"]))
        for tracker in trackers:
            _require_analysed(tracker)
        require_within(source, float(a["at"]))
        target = str(a.get("target", TrackTarget.TRANSFORM))
        mask_id = str(a["mask"]) if "mask" in a else ""
        if (target == TrackTarget.MASK) != bool(mask_id):
            raise ValueError("« mask » va avec target « mask » (et seulement avec lui).")
        position = bool(a.get("position", True))
        rotation, scale = bool(a.get("rotation", False)), bool(a.get("scale", False))
        if target == TrackTarget.ANCHOR and not position:
            raise ValueError("Le point d'ancrage suit toujours la position : position ne peut pas être faux.")
        if not (position or rotation or scale):
            raise ValueError("Une liaison suit au moins la position, la rotation ou l'échelle.")
        existing = [
            link for link in tracking_of(find_clip(project, target_id)).links if link.target == target and link.mask_id == mask_id
        ]
        if existing:
            raise ValueError(
                f"Le clip '{target_id}' a déjà la liaison '{existing[0].id}' sur cette propriété : l'interface la remplacerait, "
                "un script la retire d'abord (remove_link)."
            )
        link_id = _new_id(a, known_link_ids(project), "link")
        return add_link(
            project, target_id, source_id, [tracker.id for tracker in trackers], target=target, mask_id=mask_id, position=position,
            rotation=rotation, scale=scale, timeline_time=float(a["at"]), link_id=link_id,
        )

    return operation


def _link_of(project: Project, a: Mapping[str, Any]) -> tuple[str, str]:
    clip_id = _editable_clip(project, a["clip"], track_types=_VISUAL)
    link = _pick(tracking_of(find_clip(project, clip_id)).links, str(a["link"]), "Liaison", names=False)
    return clip_id, str(link.id)


def _remove_link(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id, link_id = _link_of(project, a)
        return remove_link(project, clip_id, link_id)

    return operation


def _set_link_enabled(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id, link_id = _link_of(project, a)
        return set_link_enabled(project, clip_id, link_id, bool(a["enabled"]))

    return operation


def _bake_link(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id, link_id = _link_of(project, a)
        require_derived_size(project, find_clip(project, clip_id), "bake_link")
        return bake_link(project, clip_id, link_id)

    return operation


# --- Stabilisation et recadrage suivi --------------------------------------------------------------------------------------------


def _set_stabilization(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id, clip = _video_clip(project, a["clip"])
        trackable_clip(project, clip_id)
        current = tracking_of(clip).stabilization
        enabled = bool(a.get("enabled", True))
        changes: dict[str, Any] = {}
        if current is None and (not enabled or "trackers" not in a):
            raise ValueError("Ce clip n'est pas stabilisé : donnez « trackers » (et laissez « enabled » vrai) pour créer la stabilisation.")
        if "enabled" in a:
            changes["enabled"] = enabled
        if "trackers" in a:
            changes["tracker_ids"] = tuple(tracker.id for tracker in _trackers_of(clip, list(a["trackers"])))
        for name in ("mode", "smoothing", "borders"):
            if name in a:
                changes[name] = a[name]
        smoothing = changes.get("smoothing", current.smoothing if current is not None else Smoothing.MEDIUM)
        if "smoothing_frames" in a:
            if smoothing != Smoothing.CUSTOM:
                raise ValueError("smoothing_frames ne sert qu'avec le lissage « custom » (sinon il serait ignoré).")
            changes["smoothing_frames"] = float(a["smoothing_frames"])
        if "at" in a:
            require_within(clip, float(a["at"]))
            changes["reference_index"] = stabilization_reference(project, clip_id, float(a["at"]))
        elif current is None:
            changes["reference_index"] = stabilization_reference(project, clip_id, float(clip.timeline_start))
        ids = changes.get("tracker_ids", current.tracker_ids if current is not None else ())
        if changes.get("enabled", True if current is None else current.enabled):
            for tracker_id in ids:
                _require_analysed(_tracker_of(clip, tracker_id))
        require_followers_editable(project, clip_id)
        return set_stabilization(project, clip_id, **changes)

    return operation


def _remove_stabilization(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id, clip = _video_clip(project, a["clip"])
        if tracking_of(clip).stabilization is None:
            raise ValueError(f"Le clip '{clip_id}' n'est pas stabilisé.")
        require_followers_editable(project, clip_id)
        return remove_stabilization(project, clip_id)

    return operation


def _follow_reframe(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id, clip = _video_clip(project, a["clip"])
        if clip.sequence_id or clip.composition is not None:                 # la règle de la sélection de l'interface (social.py)
            raise ValueError(f"Le clip '{clip_id}' est une séquence imbriquée ou une composition : il n'a pas de média à recadrer.")
        asset = media_of(project, clip)
        if asset is None:
            raise ValueError(f"Le média du clip '{clip_id}' est introuvable.")
        trackers = _trackers_of(clip, list(a["trackers"]))
        for tracker in trackers:
            if tracker.data.valid_range() is None:
                raise ValueError(f"Le tracker '{tracker.id}' n'a aucune position.")
        require_derived_size(project, clip, "follow_reframe")
        sequence = project.active_sequence

        def reframe(target: Clip) -> Any:
            return apply_follow_reframe(
                target, trackers, media_size=(asset.width, asset.height), canvas_size=(sequence.width, sequence.height), fps=sequence.fps,
            )

        require_keyframes_after(clip, reframe, "follow_reframe")          # le nombre exact que le modèle écrira, sur une copie du clip
        reframe(clip)
        return None

    return operation


TRACKING_VERBS: tuple[Verb, ...] = (
    Verb("add_tracker", "Ajoute un tracker (un point à suivre) à un clip vidéo, posé à l'image montrée à « at » : une seule image, "
                        "sans analyse (d'autres points s'ajoutent avec set_tracker_position).",
         (_CLIP, Param("at", Kind.NUMBER, "Instant sur la timeline, en secondes (dans le clip) : l'image où le point est posé.",
                       minimum=0.0, maximum=MAX_TIME),
          Param("x", Kind.NUMBER, "Position X, en pixels du média, dans l'image (défaut : le centre).", required=False,
                minimum=0.0, maximum=_MAX_PIXELS),
          Param("y", Kind.NUMBER, "Position Y, en pixels du média, dans l'image (défaut : le centre).", required=False,
                minimum=0.0, maximum=_MAX_PIXELS),
          Param("name", Kind.STRING, f"Nom du tracker, {_NAME_LENGTH} caractères au plus (défaut : « Tracker N »).", required=False,
                max_length=_NAME_LENGTH)),
         _add_tracker, creates="tracker", group="tracking"),
    Verb("update_tracker", "Renomme un tracker, change sa couleur, sa visibilité ou l'affichage de sa trajectoire (au moins un champ).",
         (_CLIP, _TRACKER,
          Param("name", Kind.STRING, f"Nom, {_NAME_LENGTH} caractères au plus.", required=False, max_length=_NAME_LENGTH),
          Param("color", Kind.COLOR, "Couleur « #RRGGBB ».", required=False),
          Param("visible", Kind.BOOLEAN, "Le tracker est affiché (et utilisé par une analyse).", required=False),
          Param("show_path", Kind.BOOLEAN, "Sa trajectoire est affichée.", required=False)),
         _update_tracker, group="tracking"),
    Verb("set_tracker_settings", "Règle les zones et seuils d'un tracker (les points déjà posés restent) ; un réglage que le modèle "
                                 "ramènerait à une borne ou qui en changerait un autre est refusé.",
         (_CLIP, _TRACKER,
          Param("pattern_width", Kind.NUMBER, "Largeur de la zone cible, en pixels du média (8 à 1024).", required=False, minimum=8.0, maximum=1024.0),
          Param("pattern_height", Kind.NUMBER, "Hauteur de la zone cible (8 à 1024).", required=False, minimum=8.0, maximum=1024.0),
          Param("search_width", Kind.NUMBER, "Largeur de la zone de recherche (au moins 4 px de plus que la zone cible, 4096 au plus).",
                required=False, minimum=12.0, maximum=4096.0),
          Param("search_height", Kind.NUMBER, "Hauteur de la zone de recherche (au moins 4 px de plus que la zone cible).",
                required=False, minimum=12.0, maximum=4096.0),
          Param("min_confidence", Kind.NUMBER, "Confiance minimale (0,05 à 0,95) : en dessous, le suivi est perdu.", required=False,
                minimum=0.05, maximum=0.95),
          Param("good_confidence", Kind.NUMBER, "Bonne confiance (au moins la minimale, 0,99 au plus) : en dessous, le point est incertain.",
                required=False, minimum=0.05, maximum=0.99),
          Param("adapt", Kind.ENUM, "Motif : adaptive (rafraîchi) ou fixed (celui de l'image de départ).", required=False,
                choices=AdaptMode.ALL),
          Param("precision", Kind.ENUM, "Résolution d'analyse : auto, fast ou full.", required=False, choices=Precision.ALL),
          Param("stop_on_loss", Kind.BOOLEAN, "S'arrêter quand le suivi est perdu.", required=False)),
         _set_tracker_settings, group="tracking"),
    Verb("set_tracker_position", "Pose un point d'un tracker à l'image montrée à « at » (état « manuel », jamais écrasé par une analyse) ; "
                                 "au moins deux images sont nécessaires pour le lier ou stabiliser. Refusé si un clip qui suit ce tracker est verrouillé.",
         (_CLIP, _TRACKER, _AT,
          Param("x", Kind.NUMBER, "Position X, en pixels du média (de −1 à 2 fois la largeur).", minimum=-2 * _MAX_PIXELS, maximum=2 * _MAX_PIXELS),
          Param("y", Kind.NUMBER, "Position Y, en pixels du média (de −1 à 2 fois la hauteur).", minimum=-2 * _MAX_PIXELS, maximum=2 * _MAX_PIXELS)),
         _set_tracker_position, group="tracking"),
    Verb("remove_correction", "Retire le point posé à la main (set_tracker_position) à l'image montrée à « at » ; refusé si ce n'en est pas un "
                              "ou si c'est le dernier point du tracker.",
         (_CLIP, _TRACKER, _AT), _remove_correction, group="tracking"),
    Verb("reset_tracker", "Efface les images analysées d'un tracker (les points posés à la main restent) : tout, ou après / avant « at ».",
         (_CLIP, _TRACKER,
          Param("direction", Kind.ENUM, "all (défaut : tout le tracker), after (après « at ») ou before (avant « at »).", required=False,
                choices=("all", "after", "before")),
          Param("at", Kind.NUMBER, "Instant sur la timeline, en secondes : demandé avec after et before.", required=False,
                minimum=0.0, maximum=MAX_TIME)),
         _reset_tracker, group="tracking"),
    Verb("remove_tracker", "Supprime un tracker, son usage par la stabilisation et les liaisons qui le suivent ; refusé si l'un des clips "
                           "dont la liaison serait réécrite est verrouillé.",
         (_CLIP, _TRACKER), _remove_tracker, group="tracking"),
    Verb("add_link", "Lie une propriété du clip « clip » aux trackers (analysés : deux images au moins) du clip « source » : position "
                     "(et rotation, échelle avec deux trackers) du clip, ou masque, ou point d'ancrage de la source elle-même. "
                     "Refusé si la propriété a déjà une liaison.",
         (_CLIP, Param("source", Kind.SELECTOR, "Le clip vidéo qui porte les trackers, par identifiant.", of="clip"), _TRACKERS,
          Param("at", Kind.NUMBER, "Instant de référence sur la timeline (dans la source) : la cible ne bouge pas à cette image.",
                minimum=0.0, maximum=MAX_TIME),
          Param("target", Kind.ENUM, "transform (défaut : le clip suit), anchor (point d'ancrage de la source) ou mask (un masque du clip).",
                required=False, choices=TrackTarget.ALL),
          Param("mask", Kind.SELECTOR, "Le masque du clip (ou la fenêtre d'étalonnage), par identifiant : avec target « mask » seulement.",
                required=False, of="mask"),
          Param("position", Kind.BOOLEAN, "Suit la position (défaut : vrai).", required=False),
          Param("rotation", Kind.BOOLEAN, "Suit la rotation (deux trackers au moins ; défaut : faux).", required=False),
          Param("scale", Kind.BOOLEAN, "Suit l'échelle (deux trackers au moins ; défaut : faux).", required=False)),
         _add_link, creates="link", group="tracking"),
    Verb("remove_link", "Retire une liaison de suivi du clip.", (_CLIP, _LINK), _remove_link, group="tracking"),
    Verb("set_link_enabled", "Active ou désactive une liaison de suivi (désactivée : les valeurs saisies seules).",
         (_CLIP, _LINK, Param("enabled", Kind.BOOLEAN, "Liaison active.")), _set_link_enabled, group="tracking"),
    Verb("bake_link", "Convertit une liaison en images clés indépendantes du tracker et la retire (une image clé par image du clip, "
                      "au plus quelques dizaines de milliers).",
         (_CLIP, _LINK), _bake_link, group="tracking"),
    Verb("set_stabilization", "Crée ou règle la stabilisation d'un clip vidéo d'après ses trackers (analysés). À la création : « trackers » "
                              "obligatoire, mode position, lissage medium, bords zoom, image de référence au début du clip.",
         (_CLIP,
          Param("trackers", Kind.LIST, f"Les trackers (1 à {MAX_TRACKERS_PER_CLIP}) ; deux au moins pour la rotation et l'échelle.",
                required=False, minimum=1, maximum=MAX_TRACKERS_PER_CLIP,
                item=Param("tracker", Kind.SELECTOR, "Un tracker du clip.", of="tracker")),
          Param("enabled", Kind.BOOLEAN, "Stabilisation active (défaut : vrai).", required=False),
          Param("mode", Kind.ENUM, "Compensation : position, position_rotation ou position_rotation_scale.", required=False,
                choices=StabilizationMode.ALL),
          Param("smoothing", Kind.ENUM, "Lissage : medium, low, high, custom (voir smoothing_frames) ou locked (plan fixe).", required=False,
                choices=Smoothing.ALL),
          Param("smoothing_frames", Kind.NUMBER, "Écart-type du lissage « custom », en images (0,5 à 600).", required=False,
                minimum=0.5, maximum=600.0),
          Param("borders", Kind.ENUM, "Bords vides : zoom (agrandit), crop (cadre fixe) ou black (noirs conservés).", required=False,
                choices=BorderMode.ALL),
          Param("at", Kind.NUMBER, "Instant de référence sur la timeline, dans le clip : l'image qui sert de plan fixe.", required=False,
                minimum=0.0, maximum=MAX_TIME)),
         _set_stabilization, group="tracking"),
    Verb("remove_stabilization", "Retire la stabilisation d'un clip (refusé s'il n'est pas stabilisé).", (_CLIP,),
         _remove_stabilization, group="tracking"),
    Verb("follow_reframe", "Recadrage vertical suivi : le clip remplit le cadre et son panoramique (pan_x / pan_y) suit les trackers "
                           "(images clés remplaçant son animation de pan). Refusé si un tracker n'a aucune position ou si le plan a déjà la forme du cadre.",
         (_CLIP, _TRACKERS), _follow_reframe, group="tracking"),
)

__all__ = ["TRACKING_VERBS"]
