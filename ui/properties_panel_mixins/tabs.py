"""Onglets de l'inspecteur et visibilité des groupes."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget,
)

from ui.i18n import translate
from ui.keyboard_navigation import follow_layout_order



class TabsMixin:
    """Mixin de ``PropertiesPanel`` : onglets de l'inspecteur et visibilité des groupes."""

    def _sync_inspector_tab_order(self) -> None:
        """Tab visite les contrôles dans l'ordre où l'inspecteur les affiche (haut → bas, gauche → droite)."""
        follow_layout_order(self.layout())

    def _select_inspector_tab(self, index: int) -> None:
        """Bascule l'onglet actif de l'inspecteur."""
        for i, button in enumerate(self.inspector_tab_buttons):
            button.setChecked(i == index)
        specialized = index in {4, 5, 6}
        self.inspector_more_button.setText(
            translate(self._TAB_KEYS[index]) if index in {4, 5, 6} else "•••"
        )
        self.inspector_more_button.setProperty("active", specialized)
        self.inspector_more_button.style().unpolish(self.inspector_more_button)
        self.inspector_more_button.style().polish(self.inspector_more_button)
        self._on_inspector_tab_changed(index)

    def _set_group_condition(self, group: QWidget, allowed: bool) -> None:
        """Déclare si un groupe conditionnel est pertinent.

        N'agit pas sur le widget : c'est ``_apply_group_visibility`` qui
        combine l'onglet courant et cette condition.
        """
        self._group_conditional[group] = bool(allowed)
        self._apply_group_visibility()

    def _apply_group_visibility(self) -> None:
        """Point unique d'écriture de la visibilité des groupes.

        visible = (groupe présent dans l'onglet actif) ET
                  (pas de condition, ou condition remplie)
        """
        in_tab = set(self._tab_groups.get(self._active_inspector_tab,
                                          self._tab_groups[0]))
        for group in self._all_inspector_groups:
            conditional = self._group_conditional.get(group, True)
            group.setVisible(group in in_tab and conditional)

    def _on_inspector_tab_changed(self, row: int) -> None:
        """Filtre les groupes visibles selon l'onglet choisi."""
        self._active_inspector_tab = row
        self._apply_group_visibility()
        self.inspector_tab_changed.emit(row)
