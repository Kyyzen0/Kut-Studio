"""Bouton large « icône + texte » des barres d'action de la bibliothèque.

Partagé par le panneau projet, la bibliothèque d'effets et celle des transitions : les trois
construisaient ce même bouton, à une ligne près. Un appelant qui veut un style propre (par
exemple un remplissage plus serré) l'ajoute après coup, sur le bouton retourné.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QSizePolicy

from ui.design_system import Sizes
from ui.icons import IconButton, IconName


def make_wide_button(
    icon: IconName,
    text: str,
    *,
    accent: bool = False,
    tooltip: str | None = None,
) -> IconButton:
    """Retourne un bouton icône + texte qui s'étend sur la largeur de son conteneur."""
    button = IconButton(
        icon=icon,
        tooltip=tooltip or text,
        size=Sizes.icon_button,
        accent=accent,
        square=False,
    )
    button.setText(f"  {text}")
    button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
    button.setMinimumHeight(Sizes.button_md)
    # Le bouton s'étend pour suivre la largeur du conteneur parent.
    button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    return button
