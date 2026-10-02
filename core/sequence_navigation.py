"""Navigation entre séquences : fil d'Ariane, précédent / suivant, parent.

État d'interface **pur** (aucune dépendance Qt) : il n'entre ni dans le
``.kut`` ni dans l'historique d'annulation. Seule la séquence active est
une donnée du projet (``Project.active_sequence_id``).

- Le **chemin** est le fil d'Ariane affiché (``Master › Scene 01 › Intro``) :
  ouvrir un clip imbriqué ajoute un niveau ; ouvrir une séquence depuis la
  bibliothèque repart d'un chemin d'un seul élément.
- **Précédent / Suivant** rejouent les chemins visités, comme un navigateur.
- **Parent** remonte d'un niveau dans le chemin ; si le chemin n'a qu'un
  élément, il remonte vers une séquence qui contient la séquence active.
- La **tête de lecture** est mémorisée par séquence : revenir dans une
  séquence la retrouve là où on l'avait laissée.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .project_model import Project

MAX_HISTORY = 50
"""Profondeur maximale de l'historique précédent / suivant."""


@dataclass
class SequenceNavigator:
    """Fil d'Ariane et historique de navigation entre séquences."""

    path: list[str] = field(default_factory=list)
    back_stack: list[list[str]] = field(default_factory=list)
    forward_stack: list[list[str]] = field(default_factory=list)
    playheads: dict[str, float] = field(default_factory=dict)

    # ------------------------------------------------------------------

    def reset(self, project: Project) -> None:
        """Repart de la séquence active du projet (nouveau projet, chargement)."""
        self.path = [project.active_sequence_id]
        self.back_stack.clear()
        self.forward_stack.clear()
        self.playheads.clear()

    @property
    def current(self) -> str:
        return self.path[-1] if self.path else ""

    @property
    def can_go_back(self) -> bool:
        return bool(self.back_stack)

    @property
    def can_go_forward(self) -> bool:
        return bool(self.forward_stack)

    def _push(self, new_path: list[str]) -> None:
        if new_path == self.path:
            return
        if self.path:
            self.back_stack.append(list(self.path))
            del self.back_stack[:-MAX_HISTORY]
        self.forward_stack.clear()
        self.path = list(new_path)

    # ------------------------------------------------------------------

    def descend(self, sequence_id: str) -> list[str]:
        """Ouvre ``sequence_id`` depuis un clip imbriqué de la séquence courante."""
        if sequence_id in self.path:
            new_path = self.path[: self.path.index(sequence_id) + 1]
        else:
            new_path = self.path + [sequence_id]
        self._push(new_path)
        return self.path

    def open(self, sequence_id: str) -> list[str]:
        """Ouvre ``sequence_id`` directement (bibliothèque, menu déroulant).

        Si la séquence figure déjà dans le fil d'Ariane, on y remonte au
        lieu de le perdre.
        """
        if sequence_id in self.path:
            new_path = self.path[: self.path.index(sequence_id) + 1]
        else:
            new_path = [sequence_id]
        self._push(new_path)
        return self.path

    def parent_target(self, project: Project) -> str | None:
        """Séquence parente de la séquence courante, ou ``None``."""
        if len(self.path) > 1:
            return self.path[-2]
        from .sequences import sequence_usages

        current = self.current
        for usage in sequence_usages(project, current):
            if usage.parent_sequence_id != current:
                return usage.parent_sequence_id
        return None

    def go_parent(self, project: Project) -> str | None:
        """Remonte d'un niveau ; retourne la séquence à ouvrir (ou ``None``)."""
        target = self.parent_target(project)
        if target is None:
            return None
        if len(self.path) > 1:
            self._push(self.path[:-1])
        else:
            self._push([target])
        return target

    def back(self) -> str | None:
        if not self.back_stack:
            return None
        self.forward_stack.append(list(self.path))
        self.path = self.back_stack.pop()
        return self.current

    def forward(self) -> str | None:
        if not self.forward_stack:
            return None
        self.back_stack.append(list(self.path))
        self.path = self.forward_stack.pop()
        return self.current

    # ------------------------------------------------------------------

    def sync(self, project: Project) -> None:
        """Recale l'état sur le projet (undo, suppression, séquence renommée…).

        Les séquences disparues sont retirées du chemin et des historiques ;
        si la séquence active n'est plus au bout du chemin, le chemin est
        tronqué jusqu'à elle, ou réduit à elle seule.
        """
        known = {sequence.id for sequence in project.sequences}
        active = project.active_sequence_id

        def clean(path: list[str]) -> list[str]:
            return [item for item in path if item in known]

        self.path = clean(self.path)
        self.back_stack = [path for path in map(clean, self.back_stack) if path]
        self.forward_stack = [path for path in map(clean, self.forward_stack) if path]
        self.playheads = {key: value for key, value in self.playheads.items() if key in known}
        if not self.path or self.path[-1] != active:
            if active in self.path:
                self.path = self.path[: self.path.index(active) + 1]
            else:
                self.path = [active]

    def breadcrumb(self, project: Project) -> list[tuple[str, str]]:
        """``[(id, nom), ...]`` du fil d'Ariane, racine d'abord."""
        crumbs = []
        for sequence_id in self.path:
            sequence = project.get_sequence(sequence_id)
            if sequence is not None:
                crumbs.append((sequence.id, sequence.name))
        return crumbs

    def remember_playhead(self, sequence_id: str, seconds: float) -> None:
        self.playheads[sequence_id] = max(0.0, float(seconds))

    def playhead_for(self, sequence_id: str, default: float = 0.0) -> float:
        return float(self.playheads.get(sequence_id, default))


__all__ = ["MAX_HISTORY", "SequenceNavigator"]
