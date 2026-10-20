"""Règles communes des verbes de séquences (lot 9 de l'extension 0.8.0) : plafond de séquences et de clips d'un projet.

Aucun verbe ici (les modules de verbes et ``verbs.py`` les emploient). Le plafond vaut pour **tous** les chemins qui créent une séquence
(``create_sequence``, ``duplicate_sequence``, ``create_multicam_source``, ``nest_clips``, ``create_multicam_from_clips``,
``create_format_version``) et se vérifie sur **toutes** les séquences du projet — ``Project.tracks`` ne montre que l'active — AVANT de créer.
"""

from __future__ import annotations

from ..project_model import Project
from . import resource_limits
from .layer_rules import require_room
from .resource_limits import require_room_in, total_clips

MAX_SEQUENCES_IN_PROJECT = 200
"""Séquences d'un projet au-delà desquelles un script n'en crée plus."""


def require_sequence_room(project: Project, adding: int = 1, *, clips: int = 0, layers: int = 0) -> None:
    """Refuse, AVANT toute création, ``adding`` séquences (et ``clips`` clips, dont ``layers`` calques, copiés) de plus si le projet dépasserait ses plafonds."""
    require_room_in(len(project.sequences), adding, MAX_SEQUENCES_IN_PROJECT, "séquences", "Le projet")
    require_room_in(total_clips(project), clips, resource_limits.MAX_CLIPS_IN_PROJECT, "clips", "Le projet")      # lu à l'appel (tests)
    if layers:
        require_room(project, layers)


def clips_in(sequence) -> int:
    """Le nombre de clips d'une séquence (toutes ses pistes)."""
    return sum(len(track.clips) for track in sequence.tracks)


__all__ = ["MAX_SEQUENCES_IN_PROJECT", "clips_in", "require_sequence_room"]
