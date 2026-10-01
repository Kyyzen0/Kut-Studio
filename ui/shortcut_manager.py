"""Branchement Qt des raccourcis clavier de Kut-Studio.

:class:`ShortcutManager` est le seul endroit qui connaît à la fois la
carte des raccourcis (:class:`core.shortcuts.ShortcutMap`) et Qt :

- il crée **une seule** ``QAction`` par commande de portée ``ACTION``
  (celles qu'on retrouve dans les menus) et met à jour ses raccourcis
  dès que la configuration change ;
- il résout les touches des commandes de portée ``KEY`` (lettres nues,
  flèches…) dans :meth:`handle_key_event`, appelé par
  ``MainWindow.keyPressEvent`` ;
- il ignore ces touches quand un champ texte a le focus, pour qu'une
  frappe de saisie ne déclenche jamais une action globale.

Les fonctions associées aux commandes sont enregistrées par la fenêtre
(:meth:`register_handlers`) ; rien d'autre n'a besoin de connaître les
touches.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from PySide6.QtCore import QKeyCombination, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QApplication

from core.shortcuts import (
    AssignResult,
    Scope,
    ShortcutMap,
    format_sequence,
    key_candidates,
    loose_candidates,
    normalize_sequence,
)

CHORD_TIMEOUT_MS = 1500
"""Délai laissé pour taper l'étape suivante d'un accord."""

_TEXT_INPUT_CLASSES = ("QLineEdit", "QTextEdit", "QPlainTextEdit")
_SHORTCUT_MODIFIERS = (
    Qt.ControlModifier | Qt.ShiftModifier | Qt.AltModifier | Qt.MetaModifier
)


def event_sequence(event) -> str | None:
    """Raccourci canonique correspondant à un ``QKeyEvent``.

    Le modificateur « pavé numérique » est ignoré : ``Droite`` du pavé
    et ``Droite`` du clavier principal sont la même commande. Retourne
    ``None`` pour une touche seule de modificateur.
    """
    combination = QKeyCombination(
        event.modifiers() & _SHORTCUT_MODIFIERS, Qt.Key(event.key())
    )
    return normalize_sequence(
        QKeySequence(combination).toString(QKeySequence.PortableText)
    )


def is_text_input(widget) -> bool:
    """``True`` si ``widget`` est un champ de saisie de texte."""
    return widget is not None and any(widget.inherits(name) for name in _TEXT_INPUT_CLASSES)


class ShortcutManager(QObject):
    """Porte la configuration des raccourcis et l'applique à l'interface.

    Signals:
        changed: émis après toute modification de la configuration (y
            compris une réinitialisation), une fois Qt à jour. Sert à
            rafraîchir les info-bulles et la fenêtre de préférences.
        overrides_changed: émis avec la configuration sérialisable
            (:meth:`ShortcutMap.overrides`) pour la persister.
    """

    changed = Signal()
    overrides_changed = Signal(dict)

    def __init__(self, shortcut_map: ShortcutMap | None = None, parent=None) -> None:
        super().__init__(parent)
        self._map = shortcut_map if shortcut_map is not None else ShortcutMap()
        self._handlers: dict[str, Callable[[], object]] = {}
        self._actions: dict[str, QAction] = {}
        self._pending: list[str] = []  # étapes d'accord déjà tapées
        self._pending_timer = QTimer(self)
        self._pending_timer.setSingleShot(True)
        self._pending_timer.setInterval(CHORD_TIMEOUT_MS)
        self._pending_timer.timeout.connect(self.cancel_chord)

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    @property
    def shortcut_map(self) -> ShortcutMap:
        return self._map

    def overrides(self) -> dict[str, list[str]]:
        return self._map.overrides()

    def load_overrides(self, data: object) -> None:
        """Remplace la configuration (par exemple après lecture du disque)."""
        self._map = ShortcutMap.from_overrides(data)
        self.cancel_chord()
        self._apply()
        self.changed.emit()

    def assign(
        self,
        command_id: str,
        slot: int,
        sequence: str | None,
        *,
        replace: bool = False,
    ) -> AssignResult:
        """Assigne un raccourci ; applique et persiste seulement en cas de succès."""
        result = self._map.assign(command_id, slot, sequence, replace=replace)
        if result.ok:
            self._commit()
        return result

    def reset(self, command_id: str) -> tuple[str, ...]:
        """Rétablit une commande ; retourne celles qui ont perdu un raccourci pour cela."""
        affected = self._map.reset(command_id)
        self._commit()
        return affected

    def reset_all(self) -> None:
        self._map.reset_all()
        self._commit()

    def _commit(self) -> None:
        self.cancel_chord()
        self._apply()
        self.changed.emit()
        self.overrides_changed.emit(self._map.overrides())

    # ------------------------------------------------------------------
    # Fonctions associées
    # ------------------------------------------------------------------

    def register_handlers(self, handlers: Mapping[str, Callable[[], object]]) -> None:
        """Associe des fonctions aux identifiants de commande."""
        self._handlers.update(handlers)

    def missing_handlers(self) -> list[str]:
        """Commandes déclarées sans fonction associée (doit être vide)."""
        return [c.id for c in self._map.commands if c.id not in self._handlers]

    def trigger(self, command_id: str) -> bool:
        """Exécute la fonction d'une commande ; ``False`` si elle n'existe pas."""
        handler = self._handlers.get(command_id)
        if handler is None:
            return False
        handler()
        return True

    # ------------------------------------------------------------------
    # QAction (commandes de portée ACTION)
    # ------------------------------------------------------------------

    def create_action(
        self,
        command_id: str,
        text: str,
        parent=None,
        *,
        checkable: bool = False,
    ) -> QAction:
        """Crée l'unique ``QAction`` d'une commande et la garde à jour.

        Un second appel pour la même commande est une erreur : deux
        actions portant le même raccourci le rendraient ambigu pour Qt.
        """
        command = self._map.command(command_id)
        if command.scope is not Scope.ACTION:
            raise ValueError(f"{command_id!r} n'est pas une commande de portée ACTION")
        if command_id in self._actions:
            raise ValueError(f"{command_id!r} possède déjà une QAction")
        action = QAction(text, parent if parent is not None else self)
        action.setCheckable(checkable)
        action.setShortcutContext(
            Qt.ApplicationShortcut if command.application_wide else Qt.WindowShortcut
        )
        action.triggered.connect(lambda _checked=False, cid=command_id: self.trigger(cid))
        self._actions[command_id] = action
        self._apply_action(command_id)
        return action

    def action(self, command_id: str) -> QAction | None:
        return self._actions.get(command_id)

    def _apply(self) -> None:
        for command_id in self._actions:
            self._apply_action(command_id)

    def _apply_action(self, command_id: str) -> None:
        self._actions[command_id].setShortcuts(
            [QKeySequence(sequence) for sequence in self._map.sequences(command_id)]
        )

    # ------------------------------------------------------------------
    # Touches (commandes de portée KEY)
    # ------------------------------------------------------------------

    def handle_key_event(self, event, focus_widget=None) -> bool:
        """Exécute la commande liée à une touche ; ``True`` si elle l'a été.

        À appeler depuis ``keyPressEvent`` : la touche n'est arrivée ici
        que parce qu'aucun widget focalisé ne l'a consommée. Elle est
        ignorée quand un champ texte a le focus, et pour les commandes
        de portée ``ACTION`` que Qt déclenche déjà via leur ``QAction``.

        Une touche qui commence un accord (``Ctrl+K, B``) est consommée
        et attend l'étape suivante, pendant :data:`CHORD_TIMEOUT_MS`.
        Une étape qui ne mène à rien abandonne l'accord et est
        réinterprétée comme une première touche.
        """
        focus = focus_widget if focus_widget is not None else QApplication.focusWidget()
        if is_text_input(focus):
            self.cancel_chord()
            return False
        step = event_sequence(event)
        if step is None:  # une touche de modificateur ne casse pas l'accord
            return False
        if self._advance(step):
            return True
        if self._pending:
            self.cancel_chord()
            return self._advance(step)
        return False

    def cancel_chord(self) -> None:
        """Abandonne l'accord en cours de saisie."""
        self._pending.clear()
        self._pending_timer.stop()

    @property
    def pending_chord(self) -> str:
        """Étapes déjà tapées de l'accord en cours (``""`` si aucun)."""
        return ", ".join(self._pending)

    def _advance(self, step: str) -> bool:
        shortcut_map = self._map
        strict = key_candidates(step)
        candidates = [(c, False) for c in strict]
        if not self._pending:
            candidates += [(c, True) for s in strict for c in loose_candidates(s)]
        for candidate, loose in candidates:
            attempt = ", ".join([*self._pending, candidate])
            command_id = shortcut_map.command_for(attempt)
            if command_id is not None:
                command = shortcut_map.command(command_id)
                if command.scope is not Scope.KEY or (loose and not command.lenient_modifiers):
                    continue
                self.cancel_chord()
                return self.trigger(command_id)
            if not loose and any(
                shortcut_map.command(c).scope is Scope.KEY
                for c in shortcut_map.continuations(attempt)
            ):
                self._pending.append(candidate)
                self._pending_timer.start()
                return True
        return False

    # ------------------------------------------------------------------
    # Affichage
    # ------------------------------------------------------------------

    def display(self, command_id: str) -> str:
        """Raccourcis d'une commande formatés pour la plateforme (``⌘S`` / ``Ctrl+S``)."""
        return " / ".join(format_sequence(s) for s in self._map.sequences(command_id))

    def hint(self, command_id: str) -> str:
        """Raccourci principal seul, pour une info-bulle ; ``""`` s'il n'y en a pas."""
        sequences = self._map.sequences(command_id)
        return format_sequence(sequences[0]) if sequences else ""


__all__ = ["ShortcutManager", "event_sequence", "is_text_input"]
