"""Méthodes de ``MainWindow`` regroupées : color_grading."""

from __future__ import annotations

import logging

from PySide6.QtCore import QTimer
from core.color_grading import (
    ColorCurve,
    ColorGrade,
    ColorGradingError,
    ColorGradingService,
    LUTResource,
    make_user_color_preset,
)
from ui.i18n import translate

LOGGER = logging.getLogger(__name__)


def _main_window():
    """Module ``ui.main_window`` résolu à l'appel (noms remplaçables en test)."""
    from ui import main_window

    return main_window


class ColorGradingMixin:
    """Mixin de ``MainWindow`` (color_grading).

    Chaque commande agit sur le **nœud courant** du clip (page Couleur, :mod:`ui.main_window_mixins.color_page`) ;
    un clip sans nœuds n'en a qu'un.
    """

    def _color_node_for(self, clip_id: str) -> str | None:
        """Nœud du clip que les commandes modifient : le nœud courant s'il y existe, sinon le premier."""
        try:
            graph = ColorGradingService().get_graph(self.project, clip_id)
        except ColorGradingError:
            return None
        return graph.node_or_first(getattr(self, "_color_node_id", None)).id

    def _color_grade_of(self, clip_id: str) -> ColorGrade:
        """Réglage du nœud courant du clip."""
        return ColorGradingService().get_grade(self.project, clip_id, self._color_node_for(clip_id))

    def _commit_color_grade(
        self, clip_id: str, grade: ColorGrade, label: str, *, coalesce: bool = False
    ) -> bool:
        clip, track = self._find_clip_and_track(clip_id)
        if clip is None or track is None or track.type != "video" or track.locked:
            return False
        if not coalesce:
            # Une rafale de curseur précédente devient sa propre étape,
            # distincte de cette action ponctuelle.
            self._finalize_color_history()
        try:
            ColorGradingService().set_grade(self.project, clip_id, grade, self._color_node_for(clip_id))
        except ColorGradingError as exc:
            self._report_edit_refused(exc)
            return False
        if coalesce:
            self._schedule_color_history(label, clip_id)
        else:
            self._record_history(label)
            self._reload_timeline_preserving_selection(clip_id)
        self._refresh_color_monitor(clip_id)
        return True

    def _refresh_color_monitor(self, clip_id: str) -> None:
        """Aligne le moniteur sur l'étalonnage courant du projet."""
        try:
            self._invalidate_preview_for_clip(clip_id)
            self._sync_preview_to_timeline()
        except Exception:
            LOGGER.warning(
                "Rafraîchissement de l'aperçu en échec après l'étalonnage du clip %s : le moniteur peut garder l'ancien étalonnage",
                clip_id, exc_info=True,
            )
        # Scopes (tâche 31) : un changement d'exposition, de contraste,
        # de saturation, de courbe ou de LUT doit être visible
        # immédiatement. En pause on force l'analyse (pas de
        # limitation de fréquence) car l'utilisateur juge son réglage
        # en direct ; pendant la lecture on laisse l'analyseur
        # borner la fréquence.
        self._request_scopes_analysis(force=not self.is_playing)

    def _schedule_color_history(self, label: str, clip_id: str) -> None:
        """Regroupe une rafale de curseur couleur dans une seule étape."""
        first = not getattr(self, "_color_session_active", False)
        self._color_session_active = True
        self._color_session_clip_id = clip_id
        if first:
            self._color_session_label = label
        if not hasattr(self, "_color_session_timer"):
            self._color_session_timer = QTimer(self)
            self._color_session_timer.setSingleShot(True)
            self._color_session_timer.timeout.connect(self._finalize_color_history)
        self._color_session_timer.start(400)
        self._mark_dirty()

    def _finalize_color_history(self) -> None:
        """Enregistre l'état final d'une rafale d'étalonnage."""
        if not getattr(self, "_color_session_active", False):
            return
        timer = getattr(self, "_color_session_timer", None)
        if timer is not None:
            timer.stop()
        self._color_session_active = False
        label = getattr(self, "_color_session_label", translate("history.color.default"))
        clip_id = getattr(self, "_color_session_clip_id", None)
        self._record_history(label)
        if clip_id:
            self._reload_timeline_preserving_selection(clip_id)

    def on_color_grade_field_changed(
        self, clip_id: str, field: str, value: float
    ) -> None:
        try:
            current = self._color_grade_of(clip_id)
            updated = current.with_field(field, float(value))
        except ColorGradingError as exc:
            self._report_edit_refused(exc)
            return
        self._commit_color_grade(clip_id, updated, translate("history.color.field", field=field), coalesce=True)

    def on_color_grade_enabled_changed(self, clip_id: str, enabled: bool) -> None:
        try:
            current = self._color_grade_of(clip_id)
        except ColorGradingError:
            return
        self._commit_color_grade(
            clip_id, current.with_enabled(enabled), translate("history.color.enable")
        )

    def on_color_curve_changed(
        self, clip_id: str, channel: str, points: object
    ) -> None:
        try:
            current = self._color_grade_of(clip_id)
            curve = ColorCurve(points=tuple(tuple(point) for point in points))
            curves = current.curves._replace(channel, curve)
            updated = current.with_curves(curves)
        except (ColorGradingError, TypeError, ValueError) as exc:
            self._report_edit_refused(exc)
            return
        self._commit_color_grade(clip_id, updated, translate("history.color.curve", channel=channel), coalesce=True)

    def on_color_grade_replaced(
        self, clip_id: str, grade_dict: dict | None
    ) -> None:
        """Remplace l'étalonnage d'un clip par un nouveau :class:`ColorGrade`.

        ``grade_dict`` est la représentation JSON-ready du grade ;
        on reconstruit un :class:`ColorGrade` côté métier pour
        garantir la validation des bornes et la cohérence des
        champs.
        """
        from core.project_io import _deserialize_color_grade

        grade = (
            ColorGrade.identity()
            if grade_dict is None
            else _deserialize_color_grade(
                grade_dict, project_root=self.project_io_root()
            )
        )
        if not isinstance(grade, ColorGrade):                # un graphe de nœuds ne remplace pas un nœud
            self._report_edit_refused(translate("status.color.invalid"))
            return
        self._commit_color_grade(clip_id, grade, translate("history.color.grade"))

    def on_color_grade_reset(self, clip_id: str) -> None:
        """Réinitialise l'étalonnage d'un clip."""
        from core.color_grading import (
            ColorGradingError,
            ColorGradingService,
        )

        clip, track = self._find_clip_and_track(clip_id)
        if clip is None or track is None or track.locked:
            return
        self._finalize_color_history()
        service = ColorGradingService()
        try:
            service.reset_grade(self.project, clip_id, self._color_node_for(clip_id))
        except ColorGradingError as exc:
            self._report_edit_refused(exc)
            return
        self._record_history(translate("history.color.reset"))
        self._reload_timeline_preserving_selection(clip_id)
        self._refresh_color_monitor(clip_id)

    def on_color_preset_applied(
        self, clip_id: str, preset_id: str
    ) -> None:
        """Applique un preset d'étalonnage au clip."""
        from core.color_grading import (
            ColorGradingError,
            ColorGradingService,
        )

        clip, track = self._find_clip_and_track(clip_id)
        if clip is None or track is None or track.locked:
            return
        self._finalize_color_history()
        store = self.properties_panel.color_preset_store
        preset = store.get_preset(preset_id)
        if preset is None:
            return
        service = ColorGradingService()
        try:
            service.apply_preset(self.project, clip_id, preset, self._color_node_for(clip_id))
        except ColorGradingError as exc:
            self._report_edit_refused(exc)
            return
        self._record_history(translate("history.color.apply_preset"))
        self._reload_timeline_preserving_selection(clip_id)
        self._refresh_color_monitor(clip_id)

    def on_color_preset_save_requested(self, clip_id: str, name: str) -> None:
        self._finalize_color_history()
        try:
            grade = self._color_grade_of(clip_id)
            preset = make_user_color_preset(name=name, description="", grade=grade)
            self.properties_panel.color_preset_store.add_user_preset(preset)
        except (ColorGradingError, OSError, ValueError) as exc:
            _main_window().QMessageBox.warning(self, translate("dialog.title.color_preset"), str(exc))
            return
        self.project.color_presets.append(preset)
        self._record_history(translate("history.color.save_preset"))
        self._mark_dirty()
        self.properties_panel._refresh_color_presets()

    def on_lut_loaded(self, clip_id: str, lut_path: str) -> None:
        """Importe un LUT et l'attache au clip sélectionné."""
        from core.color_grading import (
            ColorGradingError,
            ColorGradingService,
        )
        from core.lut_importer import (
            LUTImportError,
            ffmpeg_readable_lut,
            parse_cube_lut,
        )

        clip, track = self._find_clip_and_track(clip_id)
        if clip is None or track is None or track.locked:
            return
        self._finalize_color_history()
        try:
            parsed = parse_cube_lut(lut_path)
        except LUTImportError as exc:
            _main_window().QMessageBox.warning(self, "LUT", translate("dialog.lut.invalid", error=exc))
            return
        try:
            # FFmpeg lit le fichier tel quel : une LUT avec BOM ou en UTF-16 passe par sa copie UTF-8.
            resource = LUTResource.from_path(
                ffmpeg_readable_lut(lut_path),
                title=parsed.title,
                project_root=self.project_io_root()
                if hasattr(self, "project_io_root")
                else None,
            )
        except (ColorGradingError, OSError) as exc:
            _main_window().QMessageBox.warning(self, "LUT", translate("dialog.lut.invalid", error=exc))
            return
        # On préserve les réglages existants du clip ; seul le LUT
        # est remplacé.
        service = ColorGradingService()
        node_id = self._color_node_for(clip_id)
        current = service.get_grade(self.project, clip_id, node_id)
        # On reconstruit en s'assurant que le hash / la taille
        # reflètent le contenu lu (la valeur ``_from_path`` a déjà
        # re-calculé ces champs).
        try:
            updated = current.with_lut(resource)
            service.set_grade(self.project, clip_id, updated, node_id)
        except ColorGradingError as exc:
            _main_window().QMessageBox.warning(self, "LUT", str(exc))
            return
        self._record_history(translate("history.color.import_lut"))
        self._reload_timeline_preserving_selection(clip_id)
        self._refresh_color_monitor(clip_id)

    def on_color_lut_removed(self, clip_id: str) -> None:
        try:
            current = self._color_grade_of(clip_id)
        except ColorGradingError:
            return
        self._commit_color_grade(clip_id, current.with_lut(None), translate("history.color.remove_lut"))
