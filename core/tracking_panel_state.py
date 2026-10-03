"""Contrat typé entre la fenêtre et le panneau Suivi.

La fenêtre (:mod:`ui.main_window_mixins.tracking`) construit un « état » que le panneau
(:mod:`ui.tracking_panel`) affiche : jusqu'ici un ``dict`` sans forme, que seul un test d'interface ou une exécution
détectait de travers (une clé renommée d'un côté, oubliée de l'autre). Ces ``TypedDict`` en sont la forme : ``mypy``
la vérifie là où il passe, et ``tests/test_tracking_ui.py`` compare l'état réel à ce contrat à l'exécution (``ui/`` n'est
pas encore vérifié par ``mypy``).

Module **pur** (aucun import de Qt ni de ``ui/``) : il ne contient que des formes de données.
"""

from __future__ import annotations

from typing import Any, TypedDict


class LinkRow(TypedDict):
    """Une liaison reçue par le clip, telle que listée dans le panneau."""

    id: str
    label: str
    enabled: bool
    warning: str
    """Texte (déjà traduit) du défaut de la liaison, vide si elle suit correctement (voir ``link_issues``)."""


class TrackerRow(TypedDict):
    id: str
    name: str
    color: str
    visible: bool
    summary: str


class PrimaryTracker(TypedDict):
    """Le tracker sélectionné en premier : son résumé et ses réglages (``TrackerSettings.to_dict``)."""

    summary: str
    settings: dict[str, Any]


class TargetSpec(TypedDict):
    """Cible possible d'une liaison (choix de la liste « Cible »)."""

    clip_id: str
    target: str
    mask_id: str


class StabilizationInfo(TypedDict):
    """Réglages de la stabilisation (``Stabilization.to_dict``) et ce que le panneau en dit."""

    enabled: bool
    tracker_ids: list[str]
    mode: str
    smoothing: str
    smoothing_frames: float
    borders: str
    reference_index: int
    info: str
    warning: bool


class TrackingPanelState(TypedDict, total=False):
    """État complet du panneau. Vide quand aucun clip n'est sélectionné ; seuls ``kind`` et ``available`` sont toujours là
    sinon, le reste dépend de la nature du clip (un calque n'a ni trackers ni stabilisation)."""

    kind: str
    """``"video"``, ``"graphics"`` ou ``""`` (clip qui ne peut pas être suivi : voir ``message``)."""
    available: bool
    message: str
    links: list[LinkRow]
    trackers: list[TrackerRow]
    selected: list[str]
    primary: PrimaryTracker
    busy: bool
    progress: float
    status: str
    show_paths: bool
    analyzed: bool
    targets: list[tuple[str, TargetSpec]]
    stabilization: StabilizationInfo


__all__ = [
    "LinkRow", "PrimaryTracker", "StabilizationInfo", "TargetSpec", "TrackerRow", "TrackingPanelState",
]
