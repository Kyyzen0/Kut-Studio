"""Poser un clip « scène 3D » : menu Calques > Ajouter > Scène 3D… et glisser-déposer d'un fichier ``.kut3d`` (lot B3).

Fonctions de module qui prennent la fenêtre principale (le code neuf n'allonge pas les mixins, déjà hors limite). Un clip 3D est
un calque graphique (:func:`core.graphics.add_graphic_clip`) : une seule entrée d'historique, annulable. Les refus sont ceux du
modèle (fichier introuvable, illisible, modèle manquant : :class:`core.scene3d.Scene3DError`) et se disent à l'utilisateur sans
rien créer. Une piste graphique **verrouillée** visée par un dépôt est refusée (la scène n'atterrit pas ailleurs en silence).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QFileDialog, QMessageBox

from core.graphics import GraphicType, add_graphic_clip
from core.scene3d import SCENE_SUFFIX, Scene3DError, has_scene_suffix, reload_scene, validate
from core.security_scope import remember_user_path
from ui.i18n import translate

LOGGER = logging.getLogger(__name__)

DEFAULT_DURATION = 5.0
MAX_MESSAGE_CHARS = 300
"""Un message d'erreur n'est jamais plus long : jamais l'entrée entière dans une boîte de dialogue."""


def _message(error: object) -> str:
    text = " ".join(str(error).split())
    return text if len(text) <= MAX_MESSAGE_CHARS else text[: MAX_MESSAGE_CHARS - 1] + "…"


def _refuse(window: Any, error: object, *, dialog: bool) -> None:
    text = translate("scene3d.add.refused", error=_message(error))
    window.statusBar().showMessage(text, 8000)
    if dialog:
        QMessageBox.warning(window, translate("dialog.title.graphic"), text)


def add_scene3d_clip(window: Any, path: str, *, at: float | None = None, track_id: str | None = None,
                     dialog: bool = True) -> Any | None:
    """Ajoute le clip ``path`` (``.kut3d``) à ``at`` secondes (défaut : la tête de lecture), sur ``track_id`` si c'est une piste
    graphique éditable. ``None`` si rien n'a été créé (le refus a été dit). Une entrée d'historique."""
    try:
        validate(path)
    except Scene3DError as exc:
        _refuse(window, exc, dialog=dialog)
        return None
    project = window.project
    target = None
    if track_id is not None:
        target = next((track for track in project.tracks if track.id == track_id and track.type == "graphics"), None)
        if target is not None and target.locked:
            window.statusBar().showMessage(translate("scene3d.add.locked", track=target.name or target.id), 8000)
            return None
    start = float(window.playhead_seconds) if at is None else max(0.0, float(at))

    def create() -> Any:
        return add_graphic_clip(
            project, GraphicType.SCENE3D, timeline_start=start, duration=DEFAULT_DURATION, source_path=str(path), track=target,
        )

    try:
        clip = window._layer_operation(translate("history.scene3d.add"), create)
    except (OSError, ValueError) as exc:               # _layer_operation attrape déjà ValueError ; OSError (disque) jamais
        _refuse(window, exc, dialog=dialog)
        return None
    if clip is not None:
        window._select_layer(clip.id)
        window.properties_panel._select_inspector_tab(4)
    return clip


def add_scene3d_from_dialog(window: Any) -> Any | None:
    """Menu Calques > Ajouter > Scène 3D… : sélecteur de fichier ``.kut3d`` puis clip à la tête de lecture."""
    path, _ = QFileDialog.getOpenFileName(
        window, translate("dialog.scene3d.open_title"), "", translate("dialog.filter.scene3d", ext=SCENE_SUFFIX.lstrip(".")),
    )
    if not path:
        return None
    remember_user_path(path)                     # Store : signet du fichier choisi (ADR-0013 étape 5) ; ailleurs, sans effet
    return add_scene3d_clip(window, path)


def handle_scene3d_drop(window: Any, local_path: str, position: QPoint | None = None) -> bool:
    """Dépôt d'un fichier sur la fenêtre. ``True`` si c'est une scène ``.kut3d`` (traitée, ou refusée avec un message) ;
    ``False`` : autre extension, l'appelant fait comme avant (import de média). ``position`` : point du dépôt dans la fenêtre.

    Déposé sur la timeline, le clip se pose à l'instant visé, sur la piste graphique visée ; ailleurs, à la tête de lecture.
    """
    if not has_scene_suffix(local_path):
        return False
    remember_user_path(local_path)
    at: float | None = None
    track_id: str | None = None
    timeline = getattr(window, "timeline_panel", None)
    if timeline is not None and position is not None:
        point = timeline.mapFrom(window, position)
        if timeline.rect().contains(point):
            target = timeline.drop_target_at(point)
            if target is not None:
                track_id, at = target
    add_scene3d_clip(window, str(Path(local_path)), at=at, track_id=track_id, dialog=False)
    return True


def reload_scene_files(window: Any, path: str) -> None:
    """Bouton « Recharger » : oublie ce que le modèle a mémorisé du fichier et redessine le moniteur."""
    reload_scene(path)
    window._schedule_viewer_graphics()
    refresh_segments = getattr(window, "_refresh_preview_cache_state", None)
    if refresh_segments is not None:                 # les segments d'aperçu fidèle suivent l'empreinte du plan (donc du fichier)
        refresh_segments()
    window.statusBar().showMessage(translate("scene3d.reloaded"), 4000)


__all__ = ["add_scene3d_clip", "add_scene3d_from_dialog", "handle_scene3d_drop", "reload_scene_files"]
