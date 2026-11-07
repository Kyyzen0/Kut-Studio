"""Ce que l'interface dit des accès aux fichiers dans le bac à sable (ADR-0013, étape 5). Sans effet hors Store.

* :func:`announce_launch_report` : au lancement, des signets rangés qui ne se résolvent plus (fichier supprimé, volume absent, signet
  périmé non recréable) => un message qui demande de les rechoisir.
* :func:`report_offline_media` : à l'ouverture d'un projet, les médias sans signet valide => « hors ligne », à relier (le projet
  s'ouvre quand même : ``load_project`` ne lit aucun média).
"""

from __future__ import annotations

from typing import Any

from core.build_flavor import is_store
from core.security_scope import LaunchReport, scoped_access
from ui.i18n import translate

MESSAGE_MS = 12000


def offline_media_paths(project: Any) -> list[str]:
    """Chemins des médias du projet que le bac à sable ne laisse pas lire (aucun signet valide). Vide hors Store."""
    if not is_store() or project is None:
        return []
    paths = [str(asset.path) for asset in getattr(project, "media_assets", []) if getattr(asset, "path", "")]
    with scoped_access(*paths) as access:                 # ouvre puis referme aussitôt : seule la réponse nous importe
        return list(access.needs_relink)


def report_offline_media(window: Any) -> int:
    """Annonce dans la barre d'état le nombre de médias hors ligne ; rend ce nombre."""
    offline = len(offline_media_paths(getattr(window, "project", None)))
    if offline:
        window.statusBar().showMessage(translate("sandbox.access.offline", count=offline), MESSAGE_MS)
    return offline


def announce_launch_report(window: Any, report: LaunchReport) -> int:
    """Annonce les signets rangés devenus inutilisables ; rend leur nombre."""
    count = len(set(report.lost) | set(report.stale))
    if count:
        window.statusBar().showMessage(translate("sandbox.access.lost", count=count), MESSAGE_MS)
    return count


__all__ = ["announce_launch_report", "offline_media_paths", "report_offline_media"]
