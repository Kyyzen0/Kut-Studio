"""Catalogue élargi de transitions : les 25 types ``xfade`` ajoutés après la tâche 26.

Source unique des textes français et de la durée par défaut des presets intégrés correspondants ; l'interface
(:mod:`ui.i18n_transitions`) y ajoute l'anglais et l'espagnol. Les types ne se relisent que depuis la version 21 du
format ``.kut`` (:data:`core.project_io.TRANSITION_CATALOG_VERSION`).
"""

from __future__ import annotations

from typing import NamedTuple

from .transitions import TransitionType


class CatalogEntry(NamedTuple):
    transition_type: TransitionType
    name: str
    description: str
    default_duration: float


T = TransitionType

EXTENDED_TRANSITION_CATALOG: tuple[CatalogEntry, ...] = (
    # Couvrir / révéler
    CatalogEntry(T.COVER_LEFT, "Recouvrement gauche",
                 "Le nouveau clip entre par la droite et recouvre l'ancien, qui reste en place.", 0.6),
    CatalogEntry(T.COVER_RIGHT, "Recouvrement droite",
                 "Le nouveau clip entre par la gauche et recouvre l'ancien, qui reste en place.", 0.6),
    CatalogEntry(T.COVER_UP, "Recouvrement haut",
                 "Le nouveau clip monte depuis le bas et recouvre l'ancien, qui reste en place.", 0.6),
    CatalogEntry(T.COVER_DOWN, "Recouvrement bas",
                 "Le nouveau clip descend depuis le haut et recouvre l'ancien, qui reste en place.", 0.6),
    CatalogEntry(T.REVEAL_LEFT, "Dévoilement gauche",
                 "L'ancien clip sort par la gauche et découvre le nouveau, qui reste en place.", 0.6),
    CatalogEntry(T.REVEAL_RIGHT, "Dévoilement droite",
                 "L'ancien clip sort par la droite et découvre le nouveau, qui reste en place.", 0.6),
    CatalogEntry(T.REVEAL_UP, "Dévoilement haut",
                 "L'ancien clip sort par le haut et découvre le nouveau, qui reste en place.", 0.6),
    CatalogEntry(T.REVEAL_DOWN, "Dévoilement bas",
                 "L'ancien clip sort par le bas et découvre le nouveau, qui reste en place.", 0.6),
    # Balayages en diagonale
    CatalogEntry(T.WIPE_TL, "Balayage coin haut gauche",
                 "Le nouveau clip balaie l'image depuis le coin inférieur droit jusqu'au coin supérieur gauche.", 0.6),
    CatalogEntry(T.WIPE_TR, "Balayage coin haut droit",
                 "Le nouveau clip balaie l'image depuis le coin inférieur gauche jusqu'au coin supérieur droit.", 0.6),
    CatalogEntry(T.WIPE_BL, "Balayage coin bas gauche",
                 "Le nouveau clip balaie l'image depuis le coin supérieur droit jusqu'au coin inférieur gauche.", 0.6),
    CatalogEntry(T.WIPE_BR, "Balayage coin bas droit",
                 "Le nouveau clip balaie l'image depuis le coin supérieur gauche jusqu'au coin inférieur droit.", 0.6),
    CatalogEntry(T.DIAG_TL, "Diagonale haut gauche",
                 "Une frontière en diagonale traverse l'image depuis le coin inférieur droit vers le coin supérieur gauche.", 0.6),
    CatalogEntry(T.DIAG_TR, "Diagonale haut droit",
                 "Une frontière en diagonale traverse l'image depuis le coin inférieur gauche vers le coin supérieur droit.", 0.6),
    CatalogEntry(T.DIAG_BL, "Diagonale bas gauche",
                 "Une frontière en diagonale traverse l'image depuis le coin supérieur droit vers le coin inférieur gauche.", 0.6),
    CatalogEntry(T.DIAG_BR, "Diagonale bas droit",
                 "Une frontière en diagonale traverse l'image depuis le coin supérieur gauche vers le coin inférieur droit.", 0.6),
    # Ouvertures et lissés
    CatalogEntry(T.HORZ_OPEN, "Ouverture horizontale",
                 "Le nouveau clip apparaît en bande horizontale au centre, qui s'élargit vers le haut et le bas.", 0.6),
    CatalogEntry(T.HORZ_CLOSE, "Fermeture horizontale",
                 "Le nouveau clip se referme depuis le haut et le bas vers le centre.", 0.6),
    CatalogEntry(T.VERT_OPEN, "Ouverture verticale",
                 "Le nouveau clip apparaît en bande verticale au centre, qui s'élargit vers la gauche et la droite.", 0.6),
    CatalogEntry(T.VERT_CLOSE, "Fermeture verticale",
                 "Le nouveau clip se referme depuis la gauche et la droite vers le centre.", 0.6),
    CatalogEntry(T.SMOOTH_UP, "Lissé haut",
                 "Le nouveau clip monte depuis le bas avec une frontière douce.", 0.6),
    CatalogEntry(T.SMOOTH_DOWN, "Lissé bas",
                 "Le nouveau clip descend depuis le haut avec une frontière douce.", 0.6),
    # Variantes de fondu
    CatalogEntry(T.FADE_FAST, "Fondu rapide",
                 "Fondu enchaîné qui s'accélère : le nouveau clip s'impose vite.", 0.5),
    CatalogEntry(T.FADE_SLOW, "Fondu lent",
                 "Fondu enchaîné qui s'attarde sur l'ancien clip avant de passer au nouveau.", 1.0),
    CatalogEntry(T.FADE_GRAYS, "Fondu par les gris",
                 "Les deux clips passent par le noir et blanc pendant le fondu.", 0.5),
)
