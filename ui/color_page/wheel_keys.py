"""Losange ‹ ◆ › d'une roue de la page Couleur : un seul pour ses quatre canaux ``r``, ``g``, ``b``, ``y`` (ADR-0008, étape 8).

Le palet déplace les trois couleurs ensemble et la molette le maître : le losange pose (ou retire) les quatre images clés
``color.<roue>.<canal>`` d'un coup, une seule étape d'historique ; les canaux restent quatre courbes indépendantes dans le Graph
Editor. La rangée est construite à neuf avec la roue qu'elle accompagne (jamais déplacée d'un parent à l'autre).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QVBoxLayout, QWidget

from core.grade_keyframing import KeyState
from ui import i18n
from ui.design_system import Spacing
from ui.properties_widgets.keyframe_row import KeyframeRow, build_keyframe_row, set_diamond_quietly

if TYPE_CHECKING:
    from ui.color_page.wheels import ColorWheel


class WheelCell(QWidget):
    """Une roue et sa rangée d'images clés. ``keys_toggled``, ``navigated`` (-1 / +1), ``graph_requested`` et ``cleared``
    sont les demandes de l'utilisateur ; la fenêtre les applique au projet."""

    keys_toggled = Signal(str)
    navigated = Signal(str, int)
    graph_requested = Signal(str)
    cleared = Signal()

    def __init__(self, wheel: "ColorWheel", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.name = wheel.name
        self.setObjectName(f"color_wheel_cell_{wheel.name}")
        self.wheel = wheel
        self._has_keys = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Spacing.xs)
        layout.addWidget(wheel, 1)
        self.keys: KeyframeRow = build_keyframe_row(
            f"color.{wheel.name}", wheel.title,
            on_previous=lambda: self.navigated.emit(self.name, -1), on_toggle=lambda: self.keys_toggled.emit(self.name),
            on_next=lambda: self.navigated.emit(self.name, 1), on_menu=self._open_menu,
        )
        layout.addWidget(self.keys.inner, 0, Qt.AlignHCenter)
        self.set_state(None, animatable=True)
        self.retranslate()

    def retranslate(self) -> None:
        self.keys.previous.setAccessibleName(i18n.translate("a11y.keyframe.previous", property=self.wheel.title))
        self.keys.diamond.setAccessibleName(i18n.translate("a11y.keyframe.toggle", property=self.wheel.title))
        self.keys.next.setAccessibleName(i18n.translate("a11y.keyframe.next", property=self.wheel.title))
        self._tooltip(True)

    def _tooltip(self, animatable: bool) -> None:
        self.keys.diamond.setToolTip(
            i18n.translate("color.animation.wheel_tip", wheel=self.wheel.title) if animatable
            else i18n.translate("color.animation.disabled_tip")
        )

    def set_state(self, state: KeyState | None, *, animatable: bool) -> None:
        """Losange plein (une image clé sous la tête sur chaque canal), contour (animé, entre-deux), flèches ; sans signal."""
        state = state or KeyState()
        self._has_keys = state.animated
        set_diamond_quietly(self.keys.diamond, state.keyed_all, state.animated and animatable)
        self.keys.diamond.setEnabled(animatable)
        self.keys.previous.setEnabled(animatable and state.previous)
        self.keys.next.setEnabled(animatable and state.next)
        self._tooltip(animatable)

    def _open_menu(self, position: QPoint) -> None:
        menu = QMenu(self)
        graph = QAction(i18n.translate("animation.menu.graph"), menu)
        graph.triggered.connect(lambda: self.graph_requested.emit(self.name))
        menu.addAction(graph)
        clear = QAction(i18n.translate("animation.menu.clear_grade"), menu)
        clear.setEnabled(self._has_keys)
        clear.triggered.connect(self.cleared)
        menu.addAction(clear)
        menu.exec(self.keys.diamond.mapToGlobal(position))


__all__ = ["WheelCell"]
