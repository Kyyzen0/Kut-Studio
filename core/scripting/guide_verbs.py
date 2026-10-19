"""Guides du moniteur : poser, déplacer, retirer, vider (lot 4 de l'extension 0.8.0).

Aucune sémantique propre : chaque verbe appelle la fonction de ``core/canvas_guides.py`` que le moniteur et le menu « Guides » appellent
(``ui/main_window_mixins/motion_graphics.py``). Un guide est **visuel** : jamais rendu à l'export, enregistré avec sa séquence (la séquence
active). Les guides se citent par leur identifiant ; ``add_guide`` accepte ``id`` / ``as``.

- **rien n'est ramené en silence** : le modèle bornerait une position à −1…2 et lirait une orientation inconnue comme verticale ; un script
  les reçoit refusés (la position est bornée à la lecture, l'orientation est une énumération) ;
- **verrou d'un guide** : un guide verrouillé (par l'interface) ne se déplace ni ne se retire ; ``clear_guides`` laisse les guides verrouillés
  en place, comme la commande de menu. Aucun verbe ne (dé)verrouille un guide.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..canvas_guides import GuideOrientation, add_guide, clear_guides, move_guide, remove_guide
from ..project_model import Project
from .verbs import Kind, Operation, Param, Verb, _pick

_GUIDE = Param("guide", Kind.SELECTOR, "Le guide, par son identifiant.", of="guide")
_POSITION = Param("position", Kind.NUMBER, "Position normalisée au cadre (0 : bord haut ou gauche, 1 : bord bas ou droit), de −1 à 2.",
                  minimum=-1.0, maximum=2.0)


def _guide_id(project: Project, selector: str) -> str:
    return str(_pick(project.active_sequence.guides, selector, "Guide", names=False).id)


def _add_guide(a: Mapping[str, Any]) -> Operation:
    return lambda p: add_guide(p.active_sequence, a["orientation"], float(a["position"]), guide_id=a.get("id"))


def _move_guide(a: Mapping[str, Any]) -> Operation:
    return lambda p: move_guide(p.active_sequence, _guide_id(p, a["guide"]), float(a["position"]))


def _remove_guide(a: Mapping[str, Any]) -> Operation:
    return lambda p: remove_guide(p.active_sequence, _guide_id(p, a["guide"]))


def _clear_guides(a: Mapping[str, Any]) -> Operation:
    return lambda p: clear_guides(p.active_sequence)


GUIDE_VERBS: tuple[Verb, ...] = (
    Verb("add_guide", "Pose un guide dans le moniteur de la séquence active (visuel, jamais exporté) ; le guide créé se cite ensuite (`id` / `as`).",
         (Param("orientation", Kind.ENUM, "« vertical » (position = x) ou « horizontal » (position = y).",
                choices=tuple(item.value for item in GuideOrientation)), _POSITION),
         _add_guide, creates="guide", group="guides"),
    Verb("move_guide", "Déplace un guide ; un guide verrouillé ne bouge pas.", (_GUIDE, _POSITION), _move_guide, group="guides"),
    Verb("remove_guide", "Retire un guide ; un guide verrouillé reste.", (_GUIDE,), _remove_guide, group="guides"),
    Verb("clear_guides", "Retire tous les guides de la séquence active, sauf les guides verrouillés.", (), _clear_guides, group="guides"),
)

__all__ = ["GUIDE_VERBS"]
