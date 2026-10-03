"""Marqueur de transition posé sur la timeline."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QLabel,
)

from ui.timeline_widgets.common import _current_palette
from ui.i18n import translate

if TYPE_CHECKING:  # typage seul : évite le cycle marqueur -> panneau
    from ui.timeline_panel import TimelinePanel


class TransitionMarkerWidget(QLabel):
    """Marqueur interactif projeté depuis une transition du projet."""

    def __init__(self, transition_id: str, timeline: "TimelinePanel") -> None:
        super().__init__(timeline.timeline_grid)
        self.transition_id = transition_id
        self.timeline = timeline
        self.setAlignment(Qt.AlignCenter)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(translate("timeline.transition.tooltip"))

    def refresh_style(self, label: str) -> None:
        palette = _current_palette()
        selected = self.timeline.selected_transition_id == self.transition_id
        background = palette.accent if selected else palette.panel_alt
        border = palette.clip_border_selected if selected else palette.clip_border
        self.setText(label)
        self.setStyleSheet(
            f"QLabel {{ background: {background}; color: {palette.clip_text}; "
            f"border: 1px solid {border}; border-radius: 4px; "
            "font-size: 10px; font-weight: 700; padding: 1px 4px; }}"
        )

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.timeline.select_transition(self.transition_id)
            event.accept()
            return
        super().mousePressEvent(event)
