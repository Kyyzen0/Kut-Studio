"""Règles communes des verbes de séquences (lot 9 de l'extension 0.8.0) : plafond de séquences et de clips d'un projet.

Aucun verbe ici (les modules de verbes et ``verbs.py`` les emploient). Le plafond vaut pour **tous** les chemins qui créent une séquence
(``create_sequence``, ``duplicate_sequence``, ``create_multicam_source``, ``nest_clips``, ``create_multicam_from_clips``,
``create_format_version``) et se vérifie sur **toutes** les séquences du projet — ``Project.tracks`` ne montre que l'active — AVANT de créer.
"""

from __future__ import annotations

from ..project_model import Project

MAX_SEQUENCES_IN_PROJECT = 200
"""Séquences d'un projet au-delà desquelles un script n'en crée plus."""


def require_sequence_room(project: Project, adding: int = 1, *, clips: int = 0) -> None:
    """Refuse, AVANT toute création, ``adding`` séquences (et ``clips`` clips copiés) de plus si le projet dépasserait ses plafonds."""
    count = len(project.sequences)
    if count + adding > MAX_SEQUENCES_IN_PROJECT:
        raise ValueError(f"Le projet compte déjà {count} séquences : {adding} de plus dépasseraient la limite de {MAX_SEQUENCES_IN_PROJECT}.")
    if clips:
        from .auto_rules import MAX_CLIPS_IN_PROJECT, total_clips          # import tardif : auto_rules importe la table des verbes

        have = total_clips(project)
        if have + clips > MAX_CLIPS_IN_PROJECT:
            raise ValueError(f"Le projet compte déjà {have} clips : {clips} de plus dépasseraient la limite de {MAX_CLIPS_IN_PROJECT}.")


def clips_in(sequence) -> int:
    """Le nombre de clips d'une séquence (toutes ses pistes)."""
    return sum(len(track.clips) for track in sequence.tracks)


__all__ = ["MAX_SEQUENCES_IN_PROJECT", "clips_in", "require_sequence_room"]
