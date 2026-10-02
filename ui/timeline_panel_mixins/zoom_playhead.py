"""Zoom, durée, position de lecture et état de lecture."""

from __future__ import annotations



from core.timeline_navigation import (
    clamp_zoom,
    fit_zoom,
    format_timecode,
    scroll_for_anchor,
)
from ui.design_system import Iconography
from ui.icons import IconName


class ZoomPlayheadMixin:
    """Mixin de ``TimelinePanel`` : zoom, durée, position de lecture et état de lecture."""

    # ------------------------------------------------------------------
    # Zoom
    # ------------------------------------------------------------------

    def zoom_out(self) -> None:
        self._zoom_by(1 / 1.25, self.scroll.viewport().width() / 2)

    def zoom_in(self) -> None:
        self._zoom_by(1.25, self.scroll.viewport().width() / 2)

    def fit_timeline(self) -> None:
        """Cale le zoom pour voir tout le montage. Le playhead ne bouge pas."""
        self.zoom = fit_zoom(
            self.duration_seconds,
            self.scroll.viewport().width(),
            self.left_margin,
            self.pixels_per_second,
        )
        self._apply_zoom()
        self.scroll.horizontalScrollBar().setValue(0)

    def _apply_zoom(self) -> None:
        """Le zoom change la géométrie, pas les pistes.

        Recréer les en-têtes ici faisait clignoter la barre de pistes
        et réallouait tous les boutons à chaque cran.
        """
        self._update_zoom_label()
        self._update_scroll_extent()
        self._sync_mounted_clips()
        self._layout_children()
        self._sync_transition_widgets()
        self.update()

    def _update_zoom_label(self) -> None:
        self.zoom_label.setText(f"{int(self.zoom * 100)}%")

    def setDuration(self, duration_ms):
        self.set_timeline_duration(float(duration_ms))

    def set_timeline_duration(self, duration_seconds: float) -> None:
        duration_seconds = max(0.0, float(duration_seconds))
        self.duration_seconds = max(duration_seconds, 1.0)
        self.total_time_label.setText(f"/ {self.format_time(self.duration_seconds)}")
        self._update_scroll_extent()
        self._sync_ruler()
        self._publish_overlay()
        self.update()

    def setPlaybackPosition(self, position_ms):
        self.set_playhead_seconds(float(position_ms))

    def set_playhead_seconds(self, position_seconds: float) -> None:
        previous = self.playhead_seconds
        self.playhead_seconds = min(
            max(float(position_seconds), 0.0), self.duration_seconds
        )
        self.time_label.setText(self.format_time(self.playhead_seconds))
        if hasattr(self, "timecode_label"):
            self.timecode_label.setText(format_timecode(self.playhead_seconds, self.fps))
        # Synchronise le timecode turquoise du panneau de prévisualisation.
        preview_panel = getattr(self, "_preview_panel", None)
        if preview_panel is not None and hasattr(preview_panel, "set_timecode"):
            preview_panel.set_timecode(self.playhead_seconds, self.duration_seconds)
        if abs(previous - self.playhead_seconds) < 1e-6:
            self._sync_ruler()
            return
        # Seules les deux bandes de la tête sont invalidées, dans la
        # grille qui défile avec les clips. La règle, elle, est une
        # fine bande indépendante. La grille peint la tête à
        # ``playhead_x`` : sans cette mise à jour, un saut (changement de
        # séquence) laissait une ligne à l'ancienne position.
        if hasattr(self, "timeline_grid"):
            scale = self.pixels_per_second * self.zoom
            self.timeline_grid.playhead_x = self.left_margin + self.playhead_seconds * scale
        self._invalidate_playhead_at(previous)
        self._invalidate_playhead_at(self.playhead_seconds)
        self._sync_ruler()

    def _invalidate_playhead_at(self, seconds: float) -> None:
        x = int(self.left_margin + seconds * self.pixels_per_second * self.zoom)
        if hasattr(self, "timeline_grid"):
            self.timeline_grid.update(x - 8, 0, 16, max(self.timeline_grid.height(), 1))

    def setPlayState(self, is_playing):
        from ui.icons import make_icon
        if is_playing:
            self.play_button.setIcon(make_icon(IconName.PAUSE, size=Iconography.md))
        else:
            self.play_button.setIcon(make_icon(IconName.PLAY, size=Iconography.md))

    def _zoom_by(self, factor: float, viewport_x: float) -> None:
        old = self.zoom
        new = clamp_zoom(old * factor)
        if abs(new - old) < 1e-4:
            return
        scroll = self.scroll.horizontalScrollBar().value()
        new_scroll = scroll_for_anchor(
            old,
            new,
            viewport_x,
            scroll,
            self.left_margin,
            self.pixels_per_second,
        )
        self.zoom = new
        self._apply_zoom()
        self.scroll.horizontalScrollBar().setValue(new_scroll)
