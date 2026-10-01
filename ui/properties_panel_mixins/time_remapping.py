"""Handlers du groupe Vitesse et durée (remappage temporel)."""

from __future__ import annotations




class TimeRemappingMixin:
    """Mixin de ``PropertiesPanel`` : handlers du groupe Vitesse et durée (remappage temporel)."""

    def _on_speed_changed(self, value: float) -> None:
        if self.selected_clip is None or self._signal_block_depth > 0:
            return
        self.speed_changed.emit(self.selected_clip.id, float(value))

    def _on_reverse_toggled(self, checked: bool) -> None:
        if self.selected_clip is None or self._signal_block_depth > 0:
            return
        self.reverse_toggled.emit(self.selected_clip.id, bool(checked))

    def _on_freeze_frame_clicked(self) -> None:
        if self.selected_clip is None or self._signal_block_depth > 0:
            return
        # Créer un freeze frame au milieu du clip par défaut
        source_mid = self.selected_clip.source_in + (
            self.selected_clip.source_out - self.selected_clip.source_in
        ) / 2.0
        self.freeze_frame_created.emit(
            self.selected_clip.id, source_mid, self.freeze_duration_spinbox.value()
        )

    def _on_freeze_duration_changed(self, value: float) -> None:
        if self.selected_clip is None or self._signal_block_depth > 0:
            return
        self.freeze_duration_changed.emit(self.selected_clip.id, float(value))

    def _on_time_remapping_reset(self) -> None:
        if self.selected_clip is None or self._signal_block_depth > 0:
            return
        self.time_remapping_reset.emit(self.selected_clip.id)
