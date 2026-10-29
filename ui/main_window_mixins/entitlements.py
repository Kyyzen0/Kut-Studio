"""Droits d'usage dans la fenêtre principale : lecture seule et export bloqué (ADR-0013, étape 12, ``docs/entitlements.md``).

Trois couches, de la plus fine à la plus sûre :

1. **Interface** : en lecture seule, les entrées de menu et les commandes qui modifient le projet sont grisées, les panneaux
   d'édition (inspecteur, mixeur, bibliothèque, calques) sont désactivés, les boutons d'export aussi. Les raccourcis et la palette
   passent par ``ShortcutManager.trigger``, qui refuse la commande (``command_guard``) même si une action a été réactivée par
   ailleurs. Le glisser-déposer et l'import refusent d'emblée (:meth:`_edit_refused_ui`).
2. **Modèle** : ``ProjectHistory.record`` refuse d'enregistrer et remet le projet dans son dernier état enregistré ; la fenêtre
   rafraîchit alors ses vues (:meth:`_on_edit_refused`). C'est la garantie : un signal qui atteint le modèle sans passer par un
   champ désactivé ne laisse aucune modification.
3. **Export** : ``ExportEngine.start``, ``run_export``, la file de rendu et les livrables refusent (``core.entitlements``).

Hors Store (``core.entitlements.enforced()`` faux), tout ce module ne fait rien : aucun widget n'est touché, aucun minuteur n'est
créé.
"""

from __future__ import annotations

from core.build_flavor import is_store
from core.entitlements import (
    EntitlementRefused, edit_refusal, enforced, export_refusal, is_read_only_command, READ_ONLY_MENU_KEYS,
)

_EDIT_PANELS = ("properties_panel", "mixer_panel", "layers_panel")
"""Panneaux d'édition désactivés en lecture seule (ceux qui existent dans la fenêtre). La bibliothèque et les pages Couleur / Composition
restent actives : on y regarde, on y prévisualise ; ce qu'on y modifierait est refusé par l'historique (couche 2)."""

CHECK_INTERVAL_MS = 60_000
"""Un essai peut finir pendant que la fenêtre est ouverte : l'état est relu chaque minute (seulement quand le contrôle est exigé)."""


class EntitlementsMixin:
    """Mixin de ``MainWindow`` (droits d'usage)."""

    _entitlement_disabled: list
    _entitlement_panels_disabled: list

    def _init_entitlements(self) -> None:
        """À appeler quand l'historique, les menus, les panneaux et la file de rendu existent."""
        self._entitlement_disabled = []
        self._entitlement_panels_disabled = []
        self.history.on_edit_refused = self._on_edit_refused  # type: ignore[attr-defined]
        self.shortcuts.command_guard = self._command_allowed  # type: ignore[attr-defined]
        self.render_queue.export_refused.connect(self._show_refusal_message)  # type: ignore[attr-defined]
        if not enforced():
            return                                                # hors Store : rien de plus, aucun minuteur
        from PySide6.QtCore import QTimer

        self._entitlement_timer = QTimer(self)  # type: ignore[arg-type]
        self._entitlement_timer.setInterval(CHECK_INTERVAL_MS)
        self._entitlement_timer.timeout.connect(self.refresh_entitlements)
        self._entitlement_timer.start()
        if is_store():                                            # pont StoreKit : seulement dans le build Store
            self._start_store_entitlements()
        self.refresh_entitlements()

    def _start_store_entitlements(self) -> None:
        """Installe le service de droits du Store et démarre la lecture StoreKit (thread de travail, signal Qt vers cette fenêtre)."""
        from ui.store_entitlements import StoreEntitlementsController

        self._store_entitlements = StoreEntitlementsController(self)  # type: ignore[arg-type]
        self._store_entitlements.state_changed.connect(self.refresh_entitlements)
        self._store_entitlements.start()

    # -- refus --------------------------------------------------------------------------------------------------------------

    def _show_refusal_message(self, message: str) -> None:
        self.statusBar().showMessage(message, 8000)  # type: ignore[attr-defined]

    def _edit_refused_ui(self) -> bool:
        """Vrai (et message dans la barre d'état) si modifier le projet est refusé : à appeler en tête d'un geste qui écrit."""
        refusal = edit_refusal()
        if refusal is None:
            return False
        self._show_refusal_message(refusal.user_message())
        return True

    def _export_refused_ui(self) -> bool:
        """Comme :meth:`_edit_refused_ui`, pour l'export."""
        refusal = export_refusal()
        if refusal is None:
            return False
        self._show_refusal_message(refusal.user_message())
        return True

    def _settle_forbidden_edits(self) -> None:
        """Lecture seule : défait toute édition encore différée ou non enregistrée AVANT d'écrire le projet (Enregistrer, sauvegarde auto).

        Une édition différée (minuteur de 400 ms de la couleur, du transform, des titres, du sous-titre) ou un geste en cours a déjà modifié
        le projet vivant ; sans cela un enregistrement immédiat l'écrirait avant que la garde de l'historique la remette en place.
        Hors Store ou si l'état permet d'éditer : ne fait rien."""
        if edit_refusal() is None:
            return
        self._finalize_pending_edit_sessions()  # type: ignore[attr-defined]   # chaque ``record`` refusé remet le projet en place
        if self.history.discard_uncommitted(self.project):  # type: ignore[attr-defined]   # gestes sans minuteur
            self._apply_history_snapshot(self.project, "")  # type: ignore[attr-defined]

    def _on_edit_refused(self, refusal: EntitlementRefused) -> None:
        """``ProjectHistory`` a refusé un enregistrement et remis le projet en place : les vues relisent le modèle."""
        self._show_refusal_message(refusal.user_message())
        self._apply_history_snapshot(self.project, "")  # type: ignore[attr-defined]
        self.refresh_entitlements()

    def _command_allowed(self, command_id: str) -> bool:
        """``ShortcutManager.command_guard`` : en lecture seule, seules les commandes de la liste blanche s'exécutent."""
        if is_read_only_command(command_id):
            return True
        refusal = edit_refusal()
        if refusal is None:
            return True
        self._show_refusal_message(refusal.user_message())
        return False

    # -- apparence ----------------------------------------------------------------------------------------------------------

    def refresh_entitlements(self) -> None:
        """Grise ce que l'état courant interdit, et le rétablit quand il le permet de nouveau. Sans effet hors Store."""
        if not enforced():
            return
        read_only = edit_refusal() is not None
        self._apply_read_only_actions(read_only)
        self._apply_read_only_panels(read_only)
        self._apply_export_blocked(export_refusal() is not None)

    def _read_only_allowed_action(self, action, command_by_action: dict, key_by_action: dict) -> bool:
        command_id = command_by_action.get(id(action))
        if command_id is not None:
            return is_read_only_command(command_id)
        return key_by_action.get(id(action)) in READ_ONLY_MENU_KEYS

    def _apply_read_only_actions(self, read_only: bool) -> None:
        for action in self._entitlement_disabled:
            action.setEnabled(True)                                # la plupart se recalculent d'eux-mêmes à la prochaine sélection
        self._entitlement_disabled = []
        if not read_only:
            return
        command_by_action = {id(action): cid for cid, action in self._command_actions_by_id().items()}
        key_by_action = {id(action): key for action, key in self._translated_actions}  # type: ignore[attr-defined]
        actions = {id(a): a for a in self.shortcuts.command_actions()}  # type: ignore[attr-defined]
        actions.update({id(a): a for a, _key in self._translated_actions})  # type: ignore[attr-defined]
        for action in actions.values():
            if action.isEnabled() and not self._read_only_allowed_action(action, command_by_action, key_by_action):
                action.setEnabled(False)
                self._entitlement_disabled.append(action)

    def _command_actions_by_id(self) -> dict:
        from core.shortcuts import COMMANDS_BY_ID

        found = {}
        for command_id in COMMANDS_BY_ID:
            action = self.shortcuts.action(command_id)  # type: ignore[attr-defined]
            if action is not None:
                found[command_id] = action
        return found

    def _apply_read_only_panels(self, read_only: bool) -> None:
        for panel in self._entitlement_panels_disabled:
            panel.setEnabled(True)
        self._entitlement_panels_disabled = []
        if not read_only:
            return
        for name in _EDIT_PANELS:
            panel = getattr(self, name, None)
            if panel is not None and panel.isEnabled():
                panel.setEnabled(False)
                self._entitlement_panels_disabled.append(panel)

    def _apply_export_blocked(self, blocked: bool) -> None:
        panel = getattr(self, "export_panel", None)
        if panel is not None:
            refusal = export_refusal()
            panel.set_export_blocked(blocked, refusal.user_message() if refusal is not None else "")
        queue = getattr(self, "render_queue", None)
        if queue is not None:
            queue.jobs_changed.emit()                              # le panneau de la file relit ses boutons (``export_allowed``)


__all__ = ["CHECK_INTERVAL_MS", "EntitlementsMixin"]
