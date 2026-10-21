"""Méthodes de ``MainWindow`` regroupées : bascule du niveau d'usage (ADR-0006, lot 2).

Trois emplacements pour un même réglage : la boîte Préférences (voir :mod:`ui.preferences_dialog`), le sous-menu
« Niveau d'usage » du menu Fenêtre et une puce dans la barre du haut. Le menu et la puce partagent les mêmes trois
actions (un ``QActionGroup`` exclusif) ; tous suivent ``MainWindow.usage_level`` par ``usage_level_changed``, quelle
que soit l'origine du changement. Mettre une action à jour par programme n'émet pas ``triggered`` : pas de boucle.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QMenu, QToolButton

from core.usage_level import UsageLevel
from ui import i18n


class _LevelChip(QToolButton):
    """Puce du niveau, à trois formes selon la place de la barre du haut : complète (« Niveau : Avancé »), abrégée (le seul nom du niveau), absente.

    La barre du haut est pleine à 1180 px avec des polices plus larges (Linux) : la puce ne doit jamais comprimer
    « Réglages » ni « Exporter ». ``text()`` renvoie le texte affiché, ``full_text()`` le libellé complet.
    """

    FULL, COMPACT, HIDDEN = "full", "compact", "hidden"

    def __init__(self) -> None:
        super().__init__()
        self._full = ""
        self._compact = ""
        self._form = self.FULL

    def full_text(self) -> str:
        return self._full

    def form(self) -> str:
        return self._form

    def setText(self, text: str, compact: str | None = None) -> None:  # noqa: N802 - Qt
        """``text`` : libellé complet ; ``compact`` : libellé de la forme abrégée (défaut : les deux premières lettres)."""
        self._full = text
        self._compact = compact if compact is not None else text[:2]
        self._show_form()

    def set_form(self, form: str) -> None:
        if form != self._form:
            self._form = form
            self._show_form()

    def _show_form(self) -> None:
        super().setText(self._compact if self._form == self.COMPACT else self._full)
        self.setVisible(self._form != self.HIDDEN)

    def width_for(self, form: str) -> int:
        """Largeur idéale de la puce sous la forme demandée (indépendante de la forme courante)."""
        chrome = max(0, super().sizeHint().width() - self.fontMetrics().horizontalAdvance(self.text()))
        text = self._compact if form == self.COMPACT else self._full
        return chrome + self.fontMetrics().horizontalAdvance(text)


class _FitLevelChip(QObject):
    """Choisit la forme de la puce selon la place restante dans la barre (somme des largeurs idéales des autres éléments)."""

    _EVENTS = (QEvent.Show, QEvent.Resize, QEvent.StyleChange, QEvent.FontChange, QEvent.LayoutRequest)

    def __init__(self, bar, chip: _LevelChip) -> None:
        super().__init__(bar)
        self._bar, self._chip, self._busy = bar, chip, False
        bar.installEventFilter(self)

    def fit(self) -> None:
        if self._busy:
            return
        self._busy = True
        try:
            layout = self._bar.layout()
            margins = layout.contentsMargins()
            others = [layout.itemAt(i).widget() for i in range(layout.count())]
            widgets = [w for w in others if w is not None and w is not self._chip and not w.isHidden()]
            used = sum(w.sizeHint().width() for w in widgets) + margins.left() + margins.right()
            gaps = layout.spacing() * (len(widgets) + 1)           # y compris l'espace propre à la puce
            room = self._bar.width() - used - gaps
            if self._chip.width_for(_LevelChip.FULL) <= room:
                form = _LevelChip.FULL
            elif self._chip.width_for(_LevelChip.COMPACT) <= room:
                form = _LevelChip.COMPACT
            else:
                form = _LevelChip.HIDDEN
            self._chip.set_form(form)
        finally:
            self._busy = False

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt
        if event.type() in self._EVENTS:
            self.fit()
        return False


class UsageLevelMixin:
    """Mixin de ``MainWindow`` (niveau d'usage : sous-menu Fenêtre et puce de la barre du haut)."""

    _usage_level_actions: dict[UsageLevel, QAction]
    usage_level_chip: _LevelChip | None = None
    _usage_level_fit: _FitLevelChip | None = None
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
        chip = _LevelChip()
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
            chip.setText(i18n.translate("usage.chip.label", level=label), compact=label)
            if self._usage_level_fit is not None:
                self._usage_level_fit.fit()
            chip.setToolTip(i18n.translate("usage.chip.tooltip"))
            chip.setAccessibleName(i18n.translate("usage.chip.name", level=label))
