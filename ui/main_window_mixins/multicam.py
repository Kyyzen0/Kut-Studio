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
        self._multicam_aux = None          # lecteurs audio du son en direct (créés au premier segment à son fixe ou mixé)
        timeline = self.timeline_panel
        timeline.multicam_replace_requested.connect(self.replace_multicam_angle)
        timeline.multicam_flatten_requested.connect(self.flatten_multicam_segment)
        timeline.multicam_viewer_requested.connect(self.show_multicam_viewer)
        timeline.multicam_settings_requested.connect(lambda: self.show_multicam_settings())
        self.multicam_viewer.settings_requested.connect(lambda: self.show_multicam_settings())
        viewer = self.multicam_viewer
        viewer.set_provider(self._multicam_viewer_context)
        viewer._resolve = self._multicam_tile_path           # noqa: SLF001 - la fenêtre choisit proxy ou original
        viewer.angle_requested.connect(lambda index: self.switch_multicam_angle(index + 1))

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
            "multicam_settings": lambda: self.show_multicam_settings(),
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
        """Le moniteur Multicam relit la tête de lecture (après une édition, un saut de lecture)."""
        viewer = getattr(self, "multicam_viewer", None)
        if viewer is not None:
            viewer.refresh()

    def _multicam_viewer_context(self):
        """(projet, tête de lecture, lecture en cours) : ce que le moniteur Multicam relit à chaque rafraîchissement."""
        return self.project, self.playhead_seconds, bool(getattr(self, "is_playing", False))

    def _multicam_tile_path(self, path: str) -> str:
        """Média lu par une tuile : le plus petit proxy prêt, sinon l'original (jamais de génération ici)."""
        proxies = getattr(self, "proxies", None)
        if proxies is None:
            return path
        try:
            return str(proxies.resolve(path, need_audio=False, divisor=4))
        except Exception:  # noqa: BLE001 - un proxy illisible ne doit pas priver la tuile de son image
            return path

    def _sync_multicam_audio(self, top_clip, active_clips) -> None:
        """Son en direct d'un segment Multicam dont la politique audio n'est pas « le son suit l'image ».

        ``top_clip`` : entrée vidéo montrée par le moniteur (``None`` : plus aucun segment). Quand son ``silent`` est vrai,
        le lecteur du moniteur est coupé ; les entrées audio du **même segment** (enregistreur, sources mixées) sont jouées
        par :class:`~ui.multicam_audio.AuxAudio`. Un clip ordinaire, ou un segment dont le son suit l'image, ne change rien.
        """
        aux = self._multicam_aux
        wanted: list[tuple[str, float]] = []
        silenced = False
        if top_clip is not None and top_clip.is_nested:
            owner = self._ensure_timeline_index().clip(top_clip.owner_clip_id)
            if owner is not None and is_multicam_clip(self.project, owner):
                silenced = bool(top_clip.silent)
                wanted = [
                    (entry.source_path, entry.source_time) for entry in active_clips
                    if entry.track_type == "audio" and entry.owner_clip_id == top_clip.owner_clip_id and entry.source_path
                ]
        self.preview_panel.set_silenced(silenced)
        if not wanted:
            if aux is not None:
                aux.stop()
            return
        if aux is None:
            from ui.multicam_audio import AuxAudio

            aux = self._multicam_aux = AuxAudio(self)
        aux.sync(wanted, bool(getattr(self, "is_playing", False)))

    def _release_multicam_audio(self) -> None:
        if self._multicam_aux is not None:
            self._multicam_aux.release()

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
        self.multicam_viewer.set_active_hint(number - 1)       # retour immédiat : la suite (historique, timeline) est plus lourde
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
    # Moniteur
    # ------------------------------------------------------------------

    def toggle_multicam_viewer(self) -> None:
        """Affiche / masque le moniteur Multicam (raccourci, menu)."""
        if self._monitor_stack.currentWidget() is self.multicam_viewer:
            self._monitor_stack.setCurrentWidget(self.preview_panel)
            return
        if not self._has_multicam_source():
            self._report_edit_refused(i18n.translate("multicam.message.no_source"))
            return
        self._monitor_stack.setCurrentWidget(self.multicam_viewer)

    def show_multicam_viewer(self, clip_id: str | None = None) -> None:
        """Montre le moniteur Multicam sur un segment (double-clic) : la tête de lecture rejoint le segment si besoin."""
        if clip_id:
            try:
                clip = find_clip(self.project, clip_id)
            except KeyError:
                clip = None
            if clip is not None and not (clip.timeline_start <= self.playhead_seconds < clip.timeline_start + clip.duration):
                self.seek_to_position(clip.timeline_start)
        self._monitor_stack.setCurrentWidget(self.multicam_viewer)
        self.multicam_viewer.refresh()
