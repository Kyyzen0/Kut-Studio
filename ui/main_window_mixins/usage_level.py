"""Méthodes de ``MainWindow`` regroupées : bascule du niveau d'usage (ADR-0006, lot 2).

Trois emplacements pour un même réglage : la boîte Préférences (voir :mod:`ui.preferences_dialog`), le sous-menu
« Niveau d'usage » du menu Fenêtre et une puce dans la barre du haut. Le menu et la puce partagent les mêmes trois
actions (un ``QActionGroup`` exclusif) ; tous suivent ``MainWindow.usage_level`` par ``usage_level_changed``, quelle
que soit l'origine du changement. Mettre une action à jour par programme n'émet pas ``triggered`` : pas de boucle.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QMenu, QToolButton

from core.usage_level import UsageLevel
from ui import i18n


class UsageLevelMixin:
    """Mixin de ``MainWindow`` (niveau d'usage : sous-menu Fenêtre et puce de la barre du haut)."""

    _usage_level_actions: dict[UsageLevel, QAction]
    usage_level_chip: QToolButton | None = None
    usage_level_menu: QMenu | None = None

    def _build_usage_level_menu(self, window_menu: QMenu) -> QMenu:
        """Sous-menu « Niveau d'usage » du menu Fenêtre ; crée aussi les trois actions partagées avec la puce."""
        menu = window_menu.addMenu(i18n.translate("usage.menu.title"))
        menu.setObjectName("usage_level_menu")
        self._translated_menus.append((menu, "usage.menu.title"))  # type: ignore[attr-defined]
        group = QActionGroup(self)  # type: ignore[arg-type]
        group.setExclusive(True)
        self._usage_level_actions = {}
        for level in UsageLevel:
            action = self._labelled_action(f"usage.menu.{level.value}")  # type: ignore[attr-defined]
            action.setObjectName(f"usage_level_{level.value}")
            action.setCheckable(True)
            action.setChecked(level == self.usage_level)  # type: ignore[attr-defined]
            action.triggered.connect(lambda _checked=False, chosen=level: self.set_usage_level(chosen))  # type: ignore[attr-defined]
            group.addAction(action)
            menu.addAction(action)
            self._usage_level_actions[level] = action
        self.usage_level_menu = menu
        self.usage_level_changed.connect(self._on_usage_level_changed)  # type: ignore[attr-defined]
        self._retranslate_usage_level()
        return menu

    def _build_usage_level_chip(self) -> QToolButton:
        """Puce compacte de la barre du haut : le niveau courant, et les trois choix en menu."""
        # ``QToolButton`` simple : ``IconButton`` impose 60 px de plus à tout bouton à libellé, ce qui tronque « Exporter ».
        chip = QToolButton()
        chip.setCursor(Qt.PointingHandCursor)
        chip.setFocusPolicy(Qt.NoFocus)
        chip.setObjectName("usageLevelChip")
        chip.setPopupMode(QToolButton.InstantPopup)
        # Puce étroite : la barre du haut est pleine à 1180 px, +20 % de police (l'export ne doit pas être tronqué).
        chip.setStyleSheet(
            "QToolButton#usageLevelChip { padding: 0 2px 0 4px; }"
            " QToolButton#usageLevelChip::menu-indicator { width: 8px; subcontrol-position: right center; }"
        )
        menu = QMenu(chip)
        for action in self._usage_level_actions.values():
            menu.addAction(action)
        chip.setMenu(menu)
        self.usage_level_chip = chip
        self._retranslate_usage_level()
        return chip

    def _on_usage_level_changed(self, _value: str = "") -> None:
        """Abonné à ``usage_level_changed`` : coche le bon choix et réécrit la puce (jamais de réémission)."""
        self._retranslate_usage_level()

    def _retranslate_usage_level(self) -> None:
        """Coche le niveau courant et réécrit les libellés de la puce (langue et niveau)."""
        current = self.usage_level  # type: ignore[attr-defined]
        action = self._usage_level_actions.get(current)
        if action is not None and not action.isChecked():
            action.setChecked(True)  # ne déclenche pas ``triggered``
        chip = self.usage_level_chip
        if chip is not None:
            label = i18n.translate(f"prefs.usage.{current.value}")
            chip.setText(label)
            chip.setToolTip(i18n.translate("usage.chip.tooltip"))
            chip.setAccessibleName(i18n.translate("usage.chip.name", level=label))
