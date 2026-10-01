"""Handlers des groupes Transition et Sous-titre."""

from __future__ import annotations




class TransitionSubtitleMixin:
    """Mixin de ``PropertiesPanel`` : handlers des groupes Transition et Sous-titre."""

    def _on_transition_type_changed(self, _index: int) -> None:
        if self.selected_transition_id is None or self._signal_block_depth > 0:
            return
        self.transition_type_changed.emit(
            self.selected_transition_id, str(self.transition_type_combo.currentData())
        )

    def _on_transition_duration_changed(self, value: float) -> None:
        if self.selected_transition_id is None or self._signal_block_depth > 0:
            return
        self.transition_duration_changed.emit(self.selected_transition_id, float(value))

    def _on_transition_remove(self) -> None:
        if self.selected_transition_id is not None:
            self.transition_remove_requested.emit(self.selected_transition_id)

    def _on_subtitle_content_changed(self, content: str) -> None:
        """Émet le signal ``subtitle_content_changed`` avec l'id du clip."""
        if self.selected_clip is None:
            return
        self.subtitle_content_changed.emit(self.selected_clip.id, content)

    def _on_subtitle_style_changed(self, style) -> None:
        """Émet le signal ``subtitle_style_changed`` avec l'id du clip."""
        if self.selected_clip is None:
            return
        self.subtitle_style_changed.emit(self.selected_clip.id, style)

    def _on_subtitle_style_reset(self) -> None:
        """Émet le signal ``subtitle_style_reset`` pour réinitialiser le style."""
        if self.selected_clip is None:
            return
        self.subtitle_style_reset.emit(self.selected_clip.id)
