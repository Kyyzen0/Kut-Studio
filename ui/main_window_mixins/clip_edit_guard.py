"""Méthodes de ``MainWindow`` regroupées : refus d'une modification de clip (piste ou calque verrouillé, mauvais genre).

Une seule règle (``core.compositing_guard.clip_edit_refusal``) pour tous les gestionnaires de l'inspecteur, des
images-clés, du moniteur : en cas de refus, rien n'est écrit (ni modèle, ni historique), la barre d'état le dit et
l'inspecteur reprend la vraie valeur.
"""

from __future__ import annotations

import logging

from core.compositing_guard import clip_edit_refusal
from ui.i18n import translate

LOGGER = logging.getLogger(__name__)


class ClipEditGuardMixin:
    """Mixin de ``MainWindow`` (clip_edit_guard)."""

    def _clip_edit_refused(
        self, clip_id: str, *, visual_only: bool = True, restore: bool = True,
        track_types: tuple[str, ...] | None = None,
    ) -> bool:
        """``True`` (et message d'état) si le clip n'est pas modifiable ; ``restore`` remet l'inspecteur à jour."""
        refusal = clip_edit_refusal(self.project, clip_id, visual_only=visual_only, track_types=track_types)
        if refusal is None:
            return False
        self.statusBar().showMessage(translate(f"edit.refused.{refusal}"), 4000)
        if restore:
            self._restore_inspector(clip_id)
        return True

    def _restore_inspector(self, clip_id: str) -> None:
        """Remet les champs de l'inspecteur sur les valeurs réelles du modèle (après un refus)."""
        from core.timeline_operations import find_clip

        panel = self.properties_panel
        if getattr(getattr(panel, "selected_clip", None), "id", None) != clip_id:
            return
        view = self.timeline_panel.find_view_by_id(clip_id)
        try:
            clip = find_clip(self.project, clip_id)
        except KeyError:
            return
        if view is None:
            return
        panel.show_clip(view)
        graphic = getattr(clip, "graphic", None)
        if graphic is not None:
            panel.update_graphic_from_clip(graphic)
        panel.update_transform_from_clip(
            clip.transform, clip.transform_keyframes, playhead_seconds=self.playhead_seconds,
        )
        self._refresh_mograph_inspector()
        self._sync_audio_inspector()
