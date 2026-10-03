"""Méthodes de ``MainWindow`` regroupées : montage Multicam (bascule d'angle, remplacement, aplatir, création).

Toute la règle est dans :mod:`core.multicam_ops` ; ce mixin ne fait que la lire, enregistrer **une** entrée d'historique par
action réussie et rafraîchir l'interface. Une bascule d'angle est une vraie coupe (``cut_clip``) : pendant une session de
montage en direct, chaque touche est donc une entrée d'historique, annulable une à une.

Une action refusée (aucun segment sous la tête de lecture, angle inconnu…) est dite dans la barre d'état et journalisée,
comme les autres éditions (``_report_edit_refused``) ; elle ne modifie jamais le projet.
"""

from __future__ import annotations

import logging

from core.multicam import is_multicam_clip
from core.multicam_ops import (
    MulticamError,
    create_multicam_from_clips,
    flatten_multicam_clip,
    multicam_segment_at,
    replace_angle,
    switch_angle,
)
from core.timeline_operations import find_clip
from ui import i18n

LOGGER = logging.getLogger("kut_studio.multicam")

ANGLE_SHORTCUT_COUNT = 9
"""Nombre de commandes clavier « Angle N » (``core/shortcuts.py``)."""


class MulticamMixin:
    """Mixin de ``MainWindow`` (Multicam)."""

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def _init_multicam(self) -> None:
        timeline = self.timeline_panel
        timeline.multicam_replace_requested.connect(self.replace_multicam_angle)
        timeline.multicam_flatten_requested.connect(self.flatten_multicam_segment)
        timeline.multicam_create_requested.connect(self.create_multicam_from_timeline_selection)

    def _multicam_shortcut_handlers(self) -> dict:
        handlers = {
            f"multicam_angle_{number}": (lambda n=number: self.switch_multicam_angle(n))
            for number in range(1, ANGLE_SHORTCUT_COUNT + 1)
        }
        handlers.update({
            "multicam_viewer": lambda: self.toggle_multicam_viewer(),
            "multicam_create": lambda: self.create_multicam_from_timeline_selection(),
            "multicam_open_source": lambda: self.open_multicam_source(),
            "multicam_flatten": lambda: self.flatten_multicam_segment(),
        })
        return handlers

    # ------------------------------------------------------------------
    # Aides
    # ------------------------------------------------------------------

    def _has_multicam_source(self) -> bool:
        return any(sequence.multicam is not None for sequence in self.project.sequences)

    def _after_multicam_edit(self, select_clip_id: str | None = None) -> None:
        """Rafraîchit la timeline et le moniteur après une édition Multicam (assez léger pour le montage en direct)."""
        self.timeline_panel.set_project(self.project)
        self._update_timeline_duration()
        if select_clip_id:
            self.timeline_panel.select_clip(select_clip_id)
        self._sync_preview_to_timeline()
        self._refresh_multicam_views()
        self._mark_dirty()

    def _refresh_multicam_views(self) -> None:
        """Point d'extension : le moniteur Multicam se met à jour quand il existe."""
        viewer = getattr(self, "multicam_viewer", None)
        if viewer is not None:
            viewer.refresh()

    def _selected_clip_id(self) -> str | None:
        timeline = self.timeline_panel
        if timeline.selected_clip_id:
            return timeline.selected_clip_id
        return next(iter(timeline.selected_clip_ids), None)

    # ------------------------------------------------------------------
    # Montage en direct
    # ------------------------------------------------------------------

    def switch_multicam_angle(self, number: int, *, at: float | None = None):
        """Touche « Angle N » : coupe le segment à la tête de lecture et montre l'angle ``number`` (1 = premier).

        Retourne le résultat de la bascule, ou ``None`` (déjà cet angle, ou refus dit dans la barre d'état). Sans aucune
        source Multicam dans le projet, la touche est ignorée en silence : les chiffres restent libres pour le reste.
        """
        time_seconds = self.playhead_seconds if at is None else float(at)
        segment = multicam_segment_at(self.project, time_seconds)
        if segment is None:
            if self._has_multicam_source():
                self._report_edit_refused(i18n.translate("multicam.message.no_segment"))
            return None
        source = self.project.get_sequence(segment.sequence_id)
        angles = source.multicam.angles if source is not None and source.multicam is not None else []
        if not 1 <= number <= len(angles):
            self._report_edit_refused(i18n.translate("multicam.message.no_such_angle", count=len(angles)))
            return None
        angle = angles[number - 1]
        try:
            result = switch_angle(self.project, time_seconds, angle.id, clip_id=segment.id)
        except (KeyError, MulticamError) as error:
            self._report_edit_refused(error)
            return None
        if result is None:
            return None
        LOGGER.info("Bascule Multicam : angle %s (« %s ») à %.3f s, coupe=%s", number, angle.name, time_seconds, result.cut)
        self._record_history(i18n.translate("multicam.history.switch", number=number, name=angle.name))
        self._after_multicam_edit()
        return result

    def replace_multicam_angle(self, clip_id: str, angle_index: int):
        """« Remplacer par l'angle N » : change l'angle d'un segment existant, sans créer de coupe."""
        try:
            clip = find_clip(self.project, clip_id)
            source = self.project.get_sequence(clip.sequence_id)
            angles = source.multicam.angles if source is not None and source.multicam is not None else []
            if not 0 <= angle_index < len(angles):
                raise MulticamError(i18n.translate("multicam.message.no_such_angle", count=len(angles)))
            angle = angles[angle_index]
            replace_angle(self.project, clip_id, angle.id)
        except (KeyError, MulticamError) as error:
            self._report_edit_refused(error)
            return None
        self._record_history(i18n.translate("multicam.history.replace", number=angle_index + 1, name=angle.name))
        self._after_multicam_edit(select_clip_id=clip.id)
        return clip

    def open_multicam_source(self, clip_id: str | None = None) -> None:
        """Ouvre la source du segment sélectionné : corriger la synchro, étalonner, repositionner une caméra."""
        target = clip_id or self._selected_clip_id()
        clip = None
        if target:
            try:
                clip = find_clip(self.project, target)
            except KeyError:
                clip = None
        if clip is None or not is_multicam_clip(self.project, clip):
            self._report_edit_refused(i18n.translate("multicam.message.not_multicam"))
            return
        self.open_nested_clip(clip.id)

    def flatten_multicam_segment(self, clip_id: str | None = None):
        """« Aplatir le segment Multicam » : le remplace par les clips ordinaires qu'il montre."""
        target = clip_id or self._selected_clip_id()
        if not target:
            self._report_edit_refused(i18n.translate("multicam.message.not_multicam"))
            return None
        try:
            result = flatten_multicam_clip(self.project, target)
        except (KeyError, MulticamError) as error:
            self._report_edit_refused(error)
            return None
        self._record_history(i18n.translate("multicam.history.flatten"))
        first = result.video[0].id if result.video else None
        self._after_multicam_edit(select_clip_id=first)
        self._refresh_project_library()
        return result

    # ------------------------------------------------------------------
    # Création
    # ------------------------------------------------------------------

    def create_multicam_from_timeline_selection(self, name: str | None = None):
        """« Créer une séquence Multicam… » depuis des clips déjà alignés (méthode : positions actuelles)."""
        timeline = self.timeline_panel
        ids = [view.id for view in timeline.clip_views if view.id in timeline.selected_clip_ids]
        if len(ids) < 2:
            self._report_edit_refused(i18n.translate("multicam.message.select_two"))
            return None
        if name is None:
            name = self._ask_sequence_name("multicam.dialog.create_title", "Multicam")
            if name is None:
                return None
        try:
            sequence, segment = create_multicam_from_clips(self.project, ids, name=name)
        except (KeyError, MulticamError) as error:
            self._report_edit_refused(error)
            return None
        LOGGER.info("Source Multicam « %s » créée depuis %d clips (positions actuelles)", sequence.name, len(ids))
        self._record_history(i18n.translate("multicam.history.create", name=sequence.name))
        self._after_multicam_edit(select_clip_id=segment.id)
        self._refresh_project_library()
        self._refresh_sequence_ui()
        return sequence, segment

    # ------------------------------------------------------------------
    # Moniteur
    # ------------------------------------------------------------------

    def toggle_multicam_viewer(self) -> None:
        """Affiche / masque le moniteur Multicam (point d'extension : posé par ``multicam_viewer``)."""
        viewer = getattr(self, "multicam_viewer", None)
        if viewer is None:
            self._report_edit_refused(i18n.translate("status.unavailable"))
            return
        viewer.toggle()
