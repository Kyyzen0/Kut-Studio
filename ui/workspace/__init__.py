"""Système de workspace de Kut-Studio (panneaux, docking, détachement).

Ce paquet regroupe l'architecture d'interface :

- :mod:`ui.workspace.panel_host` : conteneur de panneau et fenêtre
  détachée ;
- :mod:`ui.workspace.manager` : autorité sur la disposition.

L'état lui-même est décrit par :mod:`core.workspace_state`, volontairement
pur (aucune dépendance Qt) et testable en ligne de commande.
"""

from ui.workspace.manager import WorkspaceManager
from ui.workspace.panel_host import PanelHost, PanelWindow

__all__ = ["PanelHost", "PanelWindow", "WorkspaceManager"]
