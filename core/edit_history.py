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

Multi-séquence : un snapshot ne recopie pas les séquences **inchangées**
depuis le snapshot précédent ; il réutilise leur copie (les snapshots ne
sont jamais modifiés, ils sont recopiés à la restauration). Éditer une
séquence d'un projet qui en compte vingt ne duplique donc qu'elle. Un
``undo`` / ``redo`` laisse ouverte la séquence où l'opération a eu lieu.
"""

from __future__ import annotations

import time
from collections.abc import Callable
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

    Le coût est assumé : chaque entrée duplique le projet, jusqu'à
    ``MAX_HISTORY`` copies. C'est acceptable tant que le projet ne
    contient que des métadonnées. Un partage de structure pourra
    remplacer :func:`_deepcopy_project` sans changer l'API, le jour
    où cette copie se voit dans l'interface.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._undo_stack: list[_Snapshot] = []
        self._redo_stack: list[_Snapshot] = []
        self._saved_index: Optional[int] = None
        self._project: Optional[Project] = None
        self._clock = clock
        self._merge_key: Optional[str] = None
        self._merge_time = 0.0

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
        snapshot = _Snapshot(label="État initial", project=_snapshot_project(project, None))
        self._undo_stack = [snapshot]
        self._redo_stack = []
        self._saved_index = 0
        self._merge_key = None

    def record(
        self, project: Project, label: str, *, merge_key: Optional[str] = None, merge_window: float = 1.0
    ) -> None:
        """Enregistre ``project`` dans l'historique sous ``label``.

        ``merge_key`` : regroupe une rafale d'un même geste (glissement d'un fader, d'un curseur) en **une**
        entrée. Un enregistrement qui suit le précédent, de même clé, dans les ``merge_window`` secondes et sans
        undo / redo / autre enregistrement entre-temps, remplace l'entrée du sommet au lieu d'en ajouter une :
        dix crans de fader s'annulent d'un coup, et ne chassent pas dix vraies éditions de la pile. Jamais
        fusionné avec l'état initial, ni avec l'état enregistré sur disque (l'indicateur « modifié » mentirait).

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
        now = self._clock()
        top = len(self._undo_stack) - 1
        if (
            merge_key is not None
            and merge_key == self._merge_key
            and now - self._merge_time <= merge_window
            and top >= 1
            and self._saved_index != top
            and not self._redo_stack
        ):
            before = self._undo_stack[top - 1].project
            self._undo_stack[top] = _Snapshot(label=label or "", project=_snapshot_project(project, before))
            self._merge_time = now
            self._project = project
            return
        self._merge_key = merge_key
        self._merge_time = now
        previous = self._undo_stack[-1].project if self._undo_stack else None
        snapshot = _Snapshot(label=label or "", project=_snapshot_project(project, previous))
        # L'état enregistré sur disque vivait dans la branche « redo » qu'on va jeter (annuler puis
        # éditer) : son index serait réutilisé par la nouvelle entrée, qui passerait pour « enregistrée ».
        if self._saved_index is not None and self._saved_index > len(self._undo_stack) - 1:
            self._saved_index = -1
        self._undo_stack.append(snapshot)
        # Limite la taille de la pile ``undo``.
        if len(self._undo_stack) > MAX_HISTORY:
            # On retire la plus ancienne entrée, en conservant un
            # minimum de 1 entrée pour préserver la base.
            overflow = len(self._undo_stack) - MAX_HISTORY
            del self._undo_stack[:overflow]
            # L'index sauvegardé doit suivre le décalage. Si l'entrée enregistrée est évincée, plus
            # aucun état de la pile n'est celui du fichier : marque négative (« jamais enregistré »),
            # et non ``max(0, …)`` qui désignait à tort la plus ancienne entrée restante.
            if self._saved_index is not None and self._saved_index >= 0:
                shifted = self._saved_index - overflow
                self._saved_index = shifted if shifted >= 0 else -1
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
        self._merge_key = None                      # un undo clôt le geste en cours
        current = self._undo_stack.pop()
        self._redo_stack.append(current)
        restored = _deepcopy_project(self._undo_stack[-1].project)
        # On reste dans la séquence où l'opération annulée a eu lieu (et
        # non celle qui était ouverte lors de l'opération précédente).
        _keep_active_sequence(restored, current.project.active_sequence_id)
        self._project = restored
        return restored

    def redo(self) -> Optional[Project]:
        """Rétablit la dernière opération annulée.

        Returns:
            Le projet restauré, ou ``None`` si rien à rétablir.
        """
        if not self._redo_stack:
            return None
        self._merge_key = None
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

    def mark_unsaved(self) -> None:
        """L'état courant ne correspond pas au fichier sur disque.

        Aucune opération n'est ajoutée à la pile. Sert après la
        restauration d'un autosave : le projet affiché est plus récent
        que le ``.kut``, donc il reste « non enregistré » jusqu'à une
        vraie sauvegarde.
        """
        self._saved_index = -1

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


def _keep_active_sequence(project: Project, sequence_id: str) -> None:
    if project.get_sequence(sequence_id) is not None:
        project.active_sequence_id = sequence_id


def _snapshot_project(project: Project, previous: Optional[Project]) -> Project:
    """Copie de ``project`` qui partage les séquences inchangées de ``previous``.

    Pour chaque séquence non active, si la copie du snapshot précédent lui
    est égale, elle est réutilisée telle quelle (pré-remplissage du
    ``memo`` de ``deepcopy``). La séquence active, presque toujours
    modifiée, est copiée sans comparaison. Partager est sûr : un snapshot
    n'est jamais muté, :meth:`ProjectHistory.undo` en restaure une copie.
    """
    memo: dict = {}
    sequences = getattr(project, "sequences", None)
    if previous is not None and sequences:
        before = {sequence.id: sequence for sequence in previous.sequences}
        active_id = project.active_sequence_id
        for sequence in sequences:
            if sequence.id == active_id:
                continue
            shared = before.get(sequence.id)
            if shared is not None and shared == sequence:
                memo[id(sequence)] = shared
    return deepcopy(project, memo)


def _deepcopy_project(project: Project) -> Project:
    """Copie profonde d'un ``Project`` avec ses listes internes.

    ``copy.deepcopy`` traverse récursivement les dataclasses et les
    listes. On s'assure également que les listes ``tracks``,
    ``clips`` (par track) et ``media_assets`` sont bien des copies
    indépendantes de la source.
    """
    return deepcopy(project)