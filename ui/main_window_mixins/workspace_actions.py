"""Méthodes de ``MainWindow`` regroupées : workspace_actions."""

from __future__ import annotations

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QInputDialog, QMenu
from core.workspace_state import PanelId, workspace_display_name
from ui import i18n


def _main_window():
    """Module ``ui.main_window`` résolu à l'appel (noms remplaçables en test)."""
    from ui import main_window

    return main_window


class WorkspaceActionsMixin:
    """Mixin de ``MainWindow`` (workspace_actions)."""

    def _workspace_icon(self, name: str):
        """Icône du registre d'icônes pour les menus de workspace."""
        from ui.design_system import Iconography
        from ui.icons import IconName, make_icon

        return make_icon(getattr(IconName, name), size=Iconography.md)

    def _build_workspace_menu(self, menu: QMenu) -> None:
        """Remplit le menu « Fenêtre » avec les actions de panneaux.

        Ce menu est la voie *principale* et découvrable : la barre
        d'options au survol reste un raccourci discret. Les deux
        partagent la même source d'actions
        (:meth:`WorkspaceManager.build_actions`), donc aucune logique
        n'est dupliquée.
        """
        panels_menu = menu.addMenu(i18n.translate("menu.item.panels"))
        # Retrouvé par son nom, jamais par son titre (qui change avec la langue).
        panels_menu.setObjectName("panels_menu")
        self._translated_menus.append((panels_menu, "menu.item.panels"))
        for panel in PanelId:
            action = QAction(panel.label(), self)
            action.setCheckable(True)
            action.setChecked(self.workspace.is_visible(panel))
            action.setIcon(self._workspace_icon("PANEL_MAXIMIZE"))
            action.triggered.connect(
                lambda _checked, p=panel: self.toggle_panel(p)
            )
            panels_menu.addAction(action)

        menu.addSeparator()
        for panel in PanelId:
            # Un sous-menu par panneau : sans étiquette, quatre groupes
            # d'actions identiques seraient ambiguës.
            panel_menu = menu.addMenu(panel.label())
            for action in self.workspace.build_actions(panel):
                if isinstance(action, QMenu):
                    # ``addMenu`` gère l'action associée au sous-menu.
                    # Reparenté ici, le QMenu devient un enfant visuel du
                    # parent et Qt calcule son popup en coordonnées locales,
                    # ce qui le faisait se chevaucher au lieu de s'ouvrir
                    # à droite.
                    panel_menu.addMenu(action)
                else:
                    panel_menu.addAction(action)
        restore_action = self._labelled_action("menu.item.restore_layout")
        restore_action.setIcon(self._workspace_icon("PANEL_RESTORE"))
        restore_action.triggered.connect(self.restore_workspace_layout)
        menu.addSeparator()
        menu.addAction(restore_action)

        # Espaces de travail nommés (§8) : appliqués ou enregistrés.
        spaces = menu.addMenu(i18n.translate("menu.item.workspaces"))
        self._translated_menus.append((spaces, "menu.item.workspaces"))
        for name in self.workspace.list_workspaces():
            action = QAction(workspace_display_name(name), self)
            action.triggered.connect(
                lambda _c=False, n=name: self.apply_workspace(n)
            )
            spaces.addAction(action)
        spaces.addSeparator()
        save_space = self._labelled_action("menu.item.save_layout_as")
        save_space.triggered.connect(self.save_workspace_as)
        spaces.addAction(save_space)

    def apply_workspace(self, name: str) -> None:
        """Applique un espace de travail nommé."""
        if not self.workspace.apply_workspace(name):
            return
        self._sync_workspace_menu()

    def save_workspace_as(self) -> None:
        """Enregistre la disposition courante sous un nom choisi."""
        name, accepted = QInputDialog.getText(
            self, "Espace de travail", "Nom de l'espace de travail :"
        )
        if not accepted or not name.strip():
            return
        if self.workspace.save_workspace_as(name):
            _main_window().QMessageBox.information(
                self,
                "Espace de travail",
                f"Disposition enregistrée sous « {name.strip()} ».",
            )
        else:
            _main_window().QMessageBox.warning(
                self,
                "Espace de travail",
                "Impossible d'enregistrer cet espace de travail.",
            )

    def toggle_panel(self, panel: PanelId) -> None:
        """Ouvre ou ferme un panneau depuis le menu."""
        if self.workspace.state.is_visible(panel) and self.workspace.is_visible(
            panel
        ):
            self.workspace.set_panel_visible(panel, False)
        else:
            self.workspace.set_panel_visible(panel, True)
        self._sync_workspace_menu()

    def maximize_panel(self, panel: PanelId) -> None:
        """Maximise temporairement un panneau."""
        self.workspace.maximize_panel(panel)
        self._sync_workspace_menu()

    def restore_workspace_layout(self) -> None:
        """Restaure la disposition précédant une maximisation."""
        self.workspace.restore_layout()
        self._sync_workspace_menu()

    def reset_workspace_layout(self) -> None:
        """Rétablit la disposition et la taille par défaut."""
        self.workspace.reset_layout()
        self._sync_workspace_menu()

    def _sync_workspace_menu(self) -> None:
        """Recalcule l'état coché du menu « Panneaux »."""
        window_menu = getattr(self, "window_menu", None)
        if window_menu is None:
            return
        panels_menu = window_menu.findChild(QMenu, "panels_menu")
        if panels_menu is None:
            return
        for action, panel in zip(panels_menu.actions(), PanelId):
            action.setChecked(self.workspace.is_visible(panel))
