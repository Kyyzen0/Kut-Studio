"""Outils d'édition, aimantation et aperçus de déplacement/trim."""

from __future__ import annotations


from PySide6.QtWidgets import (
    QMenu,
)

from core.timeline_editing import (
    ClipPlacement,
    shifted_track_index,
)
from ui.i18n import translate
from ui.timeline_widgets.clip_widget import ClipWidget

class DragToolsMixin:
    """Mixin de ``TimelinePanel`` : outils d'édition, aimantation et aperçus de déplacement/trim."""

    # ------------------------------------------------------------------
    # Snapping magnétique
    # ------------------------------------------------------------------

    def set_snap_enabled(self, enabled: bool) -> None:
        self.snap_enabled = bool(enabled)
        if not enabled:
            self.snap_line_x = None
            self.update()

    def snap_position(
        self,
        proposed_position: float,
        excluded_clip_id: str | None = None,
    ) -> tuple[float, float | None]:
        self.snap_line_x = None
        if not self.snap_enabled:
            return proposed_position, None
        threshold_seconds = self.snap_threshold_pixels / (
            self.pixels_per_second * self.zoom
        )
        # Index des bords de clips : le coût suit les bords dans le seuil,
        # pas le nombre total de clips (même résultat que
        # ``snap_timeline_position``, vérifié par les tests).
        index = self.snap_index(with_keyframes=False)
        if index is None:
            return proposed_position, None
        snapped = index.nearest(
            proposed_position,
            threshold_seconds,
            excluded_ids=(excluded_clip_id,) if excluded_clip_id is not None else (),
            playhead=self.playhead_seconds,
        )
        if abs(snapped - proposed_position) > 1e-6:
            self.snap_line_x = (
                self.left_margin + snapped * self.pixels_per_second * self.zoom
            )
        return snapped, self.snap_line_x

    def begin_drag(self, clip_id: str) -> None:
        self._drag_anchor = clip_id
        self._drag_delta = 0.0

    def snap_time(self, seconds: float, anchor_id: str) -> float:
        if not self.snap_enabled or self.project is None:
            self.snap_line_x = None
            return seconds
        scale = self.pixels_per_second * self.zoom
        threshold = self.snap_threshold_pixels / scale if scale else 0.0
        excluded = set(self.selected_clip_ids)
        excluded.add(anchor_id)
        # ``excluded`` n'est jamais vide ici : comme ``snap_edit_position``,
        # les images-clés et les marqueurs sont des points d'aimantation.
        index = self.snap_index(with_keyframes=True)
        snapped = index.nearest(
            seconds,
            threshold,
            excluded_ids=excluded,
            extra_points=[marker.time_seconds for marker in self.project.markers],
            playhead=self.playhead_seconds,
        )
        if abs(snapped - seconds) > 1e-6:
            self.snap_line_x = self.left_margin + snapped * scale
        else:
            self.snap_line_x = None
        self._publish_overlay()
        return snapped

    def preview_group_move(self, anchor_id: str, delta: float, global_y: int) -> None:
        """Déplace tout le groupe du même écart de temps et de pistes."""
        self._drag_delta = delta
        self._drag_track_delta = 0
        anchor = self.clip_widgets.get(anchor_id)
        if anchor is not None and self.project is not None:
            target = self._track_index_at_global_y(global_y, anchor.view.track_type)
            if target is not None:
                self._drag_track_delta = target - anchor.view.track_index
        for view in self.clip_views:
            if view.id != anchor_id and view.id not in self.selected_clip_ids:
                continue
            widget = self.clip_widgets.get(view.id)
            if widget is None or widget.drag_mode in {"trim-left", "trim-right"}:
                continue
            widget.pending_start = max(0.0, view.start + delta)
            widget.pending_end = widget.pending_start + (view.end - view.start)
            widget.pending_track_index = self._destination_index(view.track_index)
            widget._apply_pending_geometry()

    def _destination_index(self, origin: int) -> int:
        if self.project is None:
            return origin
        return shifted_track_index(self.project.tracks, origin, self._drag_track_delta)

    def finish_group_move(self, anchor_id: str) -> None:
        placements = []
        for view in self.clip_views:
            if view.id != anchor_id and view.id not in self.selected_clip_ids:
                continue
            widget = self.clip_widgets.get(view.id)
            index = widget.pending_track_index if widget is not None else self._destination_index(view.track_index)
            track_id = view.track_id
            if self.project is not None and 0 <= index < len(self.project.tracks):
                track_id = self.project.tracks[index].id
            placements.append(
                ClipPlacement(
                    clip_id=view.id,
                    timeline_start=max(0.0, view.start + self._drag_delta),
                    track_id=track_id,
                )
            )
        if placements:
            self.clips_move_requested.emit(placements)

    def preview_slip(self, widget: ClipWidget, delta: float) -> None:
        self._slip_delta = delta
        widget.duration_label.setText(f"slip {delta:+.2f}s")

    def preview_slide(self, widget: ClipWidget, new_start: float) -> None:
        widget.pending_start = new_start
        widget.pending_end = new_start + (widget.drag_original_end - widget.drag_original_start)
        widget._apply_pending_geometry()

    def preview_roll(self, widget: ClipWidget, mode: str, edge_time: float) -> None:
        if mode == "roll-left":
            widget.pending_start = min(widget.drag_original_end - 0.1, max(0.0, edge_time))
        else:
            widget.pending_end = max(widget.drag_original_start + 0.1, edge_time)
        widget._apply_pending_geometry()

    def preview_fade(self, widget: ClipWidget, which: str, seconds: float) -> None:
        """Prévisualise un fondu pendant le glisser de sa poignée.

        La valeur est bornée à la durée du clip et ne peut pas empiéter
        sur le fondu opposé : c'est le modèle qui applique la contrainte
        finale, l'aperçu doit juste rester plausible.
        """
        duration = max(widget.view.end - widget.view.start, 0.0)
        bounded = max(0.0, min(float(seconds), duration))
        other = widget.fade_seconds("out" if which == "in" else "in")
        bounded = max(0.0, min(bounded, duration - other))
        widget.pending_fade = bounded
        model = widget.clip_model()
        if model is not None:
            if which == "in":
                model.set_fade_in(bounded)
            else:
                model.set_fade_out(bounded)
        widget.update()

    def set_tool(self, name: str) -> None:
        """Outil actif : select, blade, roll, slip ou slide. Un seul à la fois."""
        if name not in {"select", "blade", "roll", "slip", "slide"}:
            name = "select"
        self.tool = name
        buttons = {
            "blade": getattr(self, "blade_button", None),
            "roll": getattr(self, "roll_button", None),
            "slip": getattr(self, "slip_button", None),
            "slide": getattr(self, "slide_button", None),
        }
        for tool, button in buttons.items():
            if button is None:
                continue
            button.blockSignals(True)
            button.setChecked(tool == name)
            button.blockSignals(False)

    def open_clip_menu(self, clip_id: str, global_pos) -> None:
        if clip_id not in self.selected_clip_ids:
            self.select_clip(clip_id)
        menu = QMenu(self)
        cut = menu.addAction("Couper au playhead")
        duplicate = menu.addAction("Dupliquer")
        toggle = menu.addAction("Activer / désactiver")
        ripple = menu.addAction("Supprimer et refermer")
        remove = menu.addAction("Supprimer")
        menu.addSeparator()
        nest = menu.addAction(translate("sequence.action.nest_selection"))
        view = self.find_view_by_id(clip_id)
        open_nested = None
        if view is not None and getattr(view, "sequence_id", ""):
            open_nested = menu.addAction(translate("sequence.action.open_nested"))
        chosen = menu.exec(global_pos)
        if chosen is cut:
            self.blade_cut_requested.emit(clip_id, self.playhead_seconds)
        elif chosen is duplicate:
            self.duplicate_requested.emit()
        elif chosen is toggle:
            self.toggle_enabled_requested.emit()
        elif chosen is ripple:
            self.ripple_delete_requested.emit()
        elif chosen is remove:
            window = self.window()
            if hasattr(window, "delete_selected_clip_with_check"):
                window.delete_selected_clip_with_check()
        elif chosen is nest:
            self.nest_selection_requested.emit()
        elif open_nested is not None and chosen is open_nested:
            self.nested_open_requested.emit(clip_id)
