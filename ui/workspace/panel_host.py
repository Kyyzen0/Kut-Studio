"""Hôtes de panneaux et fenêtres détachées.

Deux classes vivent ici :

- :class:`PanelHost` : conteneur d'un panneau dans la fenêtre principale.
  Il n'ajoute **aucun chrome permanent** — les panneaux ont déjà leurs
  propres en-têtes. Une barre d'options discrète apparaît au survol dans
  le coin supérieur droit ; au clic elle propose les actions du panneau
  (détacher, fermer, maximiser, réinitialiser). C'est le principe
  « puissant mais discret » du cahier des charges.

- :class:`PanelWindow` : fenêtre séparée pour un panneau détaché. Elle
  **réparente le widget existant** : il ne s'agit jamais d'une seconde
  timeline, mais du même composant déplacé. Le projet reste donc la
  source de vérité unique.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMenu,
    QVBoxLayout,
    QWidget,
)

from core.workspace_state import FLOATING_TITLEBAR_SIZE, PanelId
from ui.design_system import Spacing
from ui.i18n import translate
from ui.icons import IconButton, IconName
from ui.theme import COLORS, label_style


def panel_label(panel: PanelId) -> str:
    """Nom du panneau dans la langue courante (``PanelId.label()`` reste le nom français du cœur)."""
    return translate(f"workspace.panel.{panel.value}")


# ---------------------------------------------------------------------------
# Barre d'options d'un panneau
# ---------------------------------------------------------------------------


class PanelOptionsBar(QWidget):
    """Bouton « options » + menu contextuel d'un panneau.

    La barre est flottante (``WA_TransparentForMouseEvents`` désactivé
    seulement sur elle) et apparaît au survol du panneau. Aucune
    logique métier ici : la barre ne fait que déléguer au
    :class:`~ui.workspace.manager.WorkspaceManager`.
    """

    def __init__(self, panel: PanelId, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._panel = panel
        self._menu: QMenu | None = None
        self.setObjectName("panelOptionsBar")
        self.setAttribute(Qt.WA_StyledBackground, True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._button = IconButton(
            icon=IconName.MORE,
            tooltip=translate("workspace.options_tooltip", panel=panel_label(panel)),
            size=24,
        )
        self._button.setObjectName("panelOptionsButton")
        self._button.clicked.connect(self._on_clicked)
        layout.addWidget(self._button)
        # Taille figée : la barre est posée en absolu dans le coin du
        # panneau, elle ne doit pas hériter de la taille par défaut
        # d'un QWidget (100 px), sinon elle déborde du panneau.
        self.setFixedSize(24, 24)

    def retranslate(self) -> None:
        """Infobulle du bouton dans la langue courante."""
        self._button.setToolTip(translate("workspace.options_tooltip", panel=panel_label(self._panel)))

    def set_actions(self, actions: list) -> None:
        """Remplit le menu d'actions (rechargé à chaque ouverture).

        Accepte indifféremment des ``QAction`` et des sous-menus
        (``QMenu``) : le déplacement de zone est un sous-menu, pas une
        action.
        """
        menu = self._menu
        if menu is None:
            menu = QMenu(self)
            self._menu = menu
            menu.clear()
        for action in actions:
            if isinstance(action, QMenu):
                # Un objet de menu ne peut pas être inséré via
                # ``addAction`` : ``addMenu`` crée l'action associée et
                # préserve le positionnement de popup calculé par Qt.
                # Le reparentage manuel transformerait le menu en enfant
                # visuel et ferait chevaucher les menus imbriqués.
                menu.addMenu(action)
            else:
                menu.addAction(action)
        self._button.setMenu(None)
        self._button.setPopupMode(IconButton.ToolButtonPopupMode.InstantPopup)

    def _on_clicked(self) -> None:
        if self._menu is not None:
            self._menu.exec(self._button.mapToGlobal(QPoint(0, self._button.height())))


# ---------------------------------------------------------------------------
# Hôte d'un panneau dans la fenêtre principale
# ---------------------------------------------------------------------------


class PanelHost(QWidget):
    """Conteneur d'un panneau docké, avec barre d'options au survol.

    Le panneau enfant n'est jamais reconstruit : l'hôte ne fait que le
    reparenter. C'est ce qui garantit qu'une vue détachée et une vue
    dockée partagent le même état.
    """

    def __init__(
        self,
        panel: PanelId,
        content: QWidget,
        manager=None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.panel = panel
        self.manager = manager
        self.setObjectName(f"panelHost_{panel.value}")
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_StyledBackground, True)

        self._content = content
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(content)

        # Barre d'options : discrète, révélée au survol.
        self._options = PanelOptionsBar(panel, self)
        self._options.hide()
        self._options.setMouseTracking(True)

        self._hovered = False
        # Décalage vertical de la barre d'options. Certains panneaux ont
        # des commandes dans leur coin supérieur droit (zoom de la
        # timeline) : l'hôte décale alors la barre sous cette zone pour
        # ne jamais masquer un contrôle cliquable.
        self._options_offset_y = 0

    def set_options_offset_y(self, offset: int) -> None:
        """Décale verticalement la barre d'options du panneau."""
        self._options_offset_y = max(0, int(offset))
        self._reposition_options()

    def _reposition_options(self) -> None:
        """Recalcule la position de la barre d'options."""
        bar = self._options
        x = max(0, self.width() - bar.width() - Spacing.sm)
        y = self._options_offset_y + Spacing.xs
        if bar.pos() != (x, y):
            bar.move(x, y)

    # ------------------------------------------------------------------
    # Contenu
    # ------------------------------------------------------------------

    @property
    def content(self) -> QWidget:
        """Le composant de panneau hébergé (jamais dupliqué)."""
        return self._content

    def refresh_actions(self) -> None:
        """Demande au gestionnaire de reconstruire le menu d'actions."""
        if self.manager is not None:
            self._options.set_actions(self.manager.build_actions(self.panel))

    def retranslate(self) -> None:
        """Textes de l'hôte dans la langue courante (après un changement de langue)."""
        self._options.retranslate()

    def set_options_visible(self, visible: bool) -> None:
        """Affiche ou masque la barre d'options (au survol)."""
        if visible and not self._options.isVisible():
            self._options.show()
            self._options.raise_()
        elif not visible and self._options.isVisible():
            self._options.hide()

    # ------------------------------------------------------------------
    # Événements
    # ------------------------------------------------------------------

    def enterEvent(self, event) -> None:  # noqa: D401 - Qt
        self._hovered = True
        self.set_options_visible(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: D401 - Qt
        self._hovered = False
        self.set_options_visible(False)
        super().leaveEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: D401 - Qt
        # Positionne la barre d'options dans le coin supérieur droit, en
        # respectant le décalage déclaré par le panneau.
        super().resizeEvent(event)
        self._reposition_options()

    def showEvent(self, event) -> None:  # noqa: D401 - Qt
        super().showEvent(event)
        self.refresh_actions()


# ---------------------------------------------------------------------------
# Fenêtre détachée
# ---------------------------------------------------------------------------


class PanelWindow(QWidget):
    """Fenêtre séparée affichant un panneau détaché.

    Le panneau est **réparenté** dans cette fenêtre : il n'y a jamais
    deux instances du même composant, donc jamais deux sources de vérité.
    """

    def __init__(
        self,
        panel: PanelId,
        content: QWidget,
        manager=None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.panel = panel
        self.manager = manager
        self._content = content
        self.setObjectName(f"panelWindow_{panel.value}")
        self.setWindowTitle(f"Kut-Studio — {panel_label(panel)}")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            f"QWidget#panelWindow_{panel.value} {{"
            f" background: {COLORS['panel']}; color: {COLORS['text']}; }}"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Barre de titre minimale : nom du panneau + options + rattachement.
        self._titlebar = QWidget()
        self._titlebar.setFixedHeight(FLOATING_TITLEBAR_SIZE)
        self._titlebar.setObjectName("floatingTitlebar")
        self._titlebar.setStyleSheet(
            f"QWidget#floatingTitlebar {{ background: {COLORS['panel']};"
            f" border-bottom: 1px solid {COLORS['border']}; }}"
        )
        title_layout = QHBoxLayout(self._titlebar)
        title_layout.setContentsMargins(Spacing.md, 0, Spacing.sm, 0)
        title_layout.setSpacing(Spacing.sm)

        title = QLabel(panel_label(panel))
        self._title_label = title
        title.setStyleSheet(label_style(11, "muted_strong", 700))
        title_layout.addWidget(title)
        title_layout.addStretch(1)

        dock_button = IconButton(
            icon=IconName.PANEL_DOCK,
            tooltip=translate("workspace.dock_tooltip"),
            size=24,
        )
        self._dock_button = dock_button
        dock_button.setObjectName("floatingDockButton")
        dock_button.clicked.connect(self._on_dock_requested)
        title_layout.addWidget(dock_button)

        self._options = PanelOptionsBar(panel, self._titlebar)
        title_layout.addWidget(self._options)

        layout.addWidget(self._titlebar)
        layout.addWidget(content, 1)

    # ------------------------------------------------------------------
    # Contenu & actions
    # ------------------------------------------------------------------

    @property
    def content(self) -> QWidget:
        return self._content

    def refresh_actions(self) -> None:
        if self.manager is not None:
            self._options.set_actions(self.manager.build_actions(self.panel))

    def retranslate(self) -> None:
        """Titre et infobulles de la fenêtre détachée dans la langue courante."""
        self.setWindowTitle(f"Kut-Studio — {panel_label(self.panel)}")
        self._title_label.setText(panel_label(self.panel))
        self._dock_button.setToolTip(translate("workspace.dock_tooltip"))
        self._options.retranslate()

    def _on_dock_requested(self) -> None:
        if self.manager is not None:
            self.manager.dock_panel(self.panel)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: D401 - Qt
        # Fermer la fenêtre revient à rattacher le panneau : on évite
        # d'orpheliner un composant et on préserve la disposition.
        if self.manager is not None:
            event.accept()
            self.manager.dock_panel(self.panel)
            return
        super().closeEvent(event)


__all__ = ["PanelHost", "PanelOptionsBar", "PanelWindow"]
