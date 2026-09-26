"""Historique d'édition non destructif pour les ``Project`` Kut-Studio.

Ce module implémente :class:`ProjectHistory`, qui conserve des
**snapshots indépendants** (copies profondes) d'un ``Project`` au fur
et à mesure des éditions de l'utilisateur, et permet l'annulation
(``undo``) et le rétablissement (``redo``).

Le module est volontairement pur : il ne dépend ni de PySide6, ni de
l'interface graphique, ni d'un système d'événements. Il peut être
utilisé en CLI, dans les tests, ou pour de l'édition scriptée.

Règles principales :

- chaque snapshot est une **copie profonde** : aucune liste (``tracks``,
  ``clips``, ``media_assets``) n'est partagée entre snapshots ;
- la pile est plafonnée à ``MAX_HISTORY`` opérations ;
- une nouvelle action après un ``undo`` vide la pile ``redo`` ;
- :meth:`ProjectHistory.mark_saved` pose une marque ``saved_index`` ;
- :attr:`ProjectHistory.is_dirty` indique si la position courante
  diffère de la marque sauvegardée.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Optional

from .project_model import Project


MAX_HISTORY = 100
"""Nombre maximal d'opérations conservées dans l'historique."""


class ProjectHistory:
    """Historique undo/redo sur des snapshots indépendants de ``Project``.

    L'état courant est toujours dérivé de la pile ``undo`` :

    - le sommet de ``undo`` correspond à l'état courant du projet ;
    - la pile ``redo`` contient les états disponibles pour un
      ``redo()``.

    Les snapshots sont stockés comme des copies profondes pour éviter
    toute fuite de référence entre versions successives.
    """

    def __init__(self) -> None:
        self._undo_stack: list[_Snapshot] = []
        self._redo_stack: list[_Snapshot] = []
        self._saved_index: Optional[int] = None
        self._project: Optional[Project] = None

    # ------------------------------------------------------------------
    # API publique
    # ------------------------------------------------------------------

    @property
    def can_undo(self) -> bool:
        """Vrai s'il reste une opération annidable."""
        return len(self._undo_stack) > 1

    @property
    def can_redo(self) -> bool:
        """Vrai s'il reste une opération rétablie."""
        return bool(self._redo_stack)

    @property
    def undo_label(self) -> Optional[str]:
        """Libellé de l'opération annidable, ou ``None``."""
        if len(self._undo_stack) <= 1:
            return None
        return self._undo_stack[-1].label

    @property
    def redo_label(self) -> Optional[str]:
        """Libellé de l'opération rétablie, ou ``None``."""
        if not self._redo_stack:
            return None
        return self._redo_stack[-1].label

    @property
    def is_dirty(self) -> bool:
        """Vrai si l'état courant diffère du dernier snapshot sauvegardé.

        Le projet est considéré comme « non enregistré » dès qu'une
        nouvelle opération est effectuée après un :meth:`mark_saved`,
        et redevient « enregistré » lorsque la pile revient au
        snapshot marqué.
        """
        if self._saved_index is None:
            return False
        return self._saved_index != len(self._undo_stack) - 1

    def reset(self, project: Project) -> None:
        """Réinitialise l'historique sur ``project`` (état propre).

        Vide les deux piles et pose la marque sauvegardée sur l'état
        courant. À utiliser après un *nouveau projet* ou un *chargement*
        de fichier.
        """
        self._project = project
        snapshot = _Snapshot(label="État initial", project=_deepcopy_project(project))
        self._undo_stack = [snapshot]
        self._redo_stack = []
        self._saved_index = 0

    def record(self, project: Project, label: str) -> None:
        """Enregistre ``project`` dans l'historique sous ``label``.

        Comportements :

        - dépile le ``redo`` (toute action après un ``undo`` invalide
          les rétablissements) ;
        - si la pile dépasse :const:`MAX_HISTORY`, l'entrée la plus
          ancienne est supprimée ;
        - le snapshot précédent est conservé comme ``undo`` ;
        - l'état courant devient le sommet de la pile.
        """
        if project is None:
            raise ValueError("Impossible d'enregistrer un projet None.")
        snapshot = _Snapshot(label=label or "", project=_deepcopy_project(project))
        self._undo_stack.append(snapshot)
        # Limite la taille de la pile ``undo``.
        if len(self._undo_stack) > MAX_HISTORY:
            # On retire la plus ancienne entrée, en conservant un
            # minimum de 1 entrée pour préserver la base.
            overflow = len(self._undo_stack) - MAX_HISTORY
            del self._undo_stack[:overflow]
            # L'index sauvegardé doit être ajusté s'il pointait sur
            # une entrée supprimée.
            if self._saved_index is not None:
                self._saved_index = max(0, self._saved_index - overflow)
        # Toute nouvelle action après ``undo`` vide le ``redo``.
        self._redo_stack = []
        self._project = project

    def undo(self) -> Optional[Project]:
        """Annule la dernière opération enregistrée.

        Returns:
            Le projet restauré, ou ``None`` si l'annulation est
            impossible (déjà à la base de l'historique).
        """
        if not self.can_undo:
            return None
        current = self._undo_stack.pop()
        self._redo_stack.append(current)
        restored = _deepcopy_project(self._undo_stack[-1].project)
        self._project = restored
        return restored

    def redo(self) -> Optional[Project]:
        """Rétablit la dernière opération annulée.

        Returns:
            Le projet restauré, ou ``None`` si rien à rétablir.
        """
        if not self._redo_stack:
            return None
        next_snapshot = self._redo_stack.pop()
        self._undo_stack.append(next_snapshot)
        restored = _deepcopy_project(next_snapshot.project)
        self._project = restored
        return restored

    def mark_saved(self) -> None:
        """Marque l'état courant comme étant l'état sauvegardé."""
        if not self._undo_stack:
            # Pas de pile : pas de marque.
            self._saved_index = None
            return
        self._saved_index = len(self._undo_stack) - 1

    def current_project(self) -> Optional[Project]:
        """Retourne le projet courant (référence partagée).

        Utile pour synchroniser un ``MainWindow`` qui souhaite garder
        une référence stable vers l'instance active.
        """
        return self._project

    def __len__(self) -> int:
        return len(self._undo_stack)


# ---------------------------------------------------------------------------
# Snapshot interne
# ---------------------------------------------------------------------------


class _Snapshot:
    """Snapshot immuable d'un projet + son libellé humain."""

    __slots__ = ("label", "project")

    def __init__(self, *, label: str, project: Project) -> None:
        self.label = label
        self.project = project


def _deepcopy_project(project: Project) -> Project:
    """Copie profonde d'un ``Project`` avec ses listes internes.

    ``copy.deepcopy`` traverse récursivement les dataclasses et les
    listes. On s'assure également que les listes ``tracks``,
    ``clips`` (par track) et ``media_assets`` sont bien des copies
    indépendantes de la source.
    """
    return deepcopy(project)