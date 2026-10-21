"""Applique une géométrie de fenêtre en la bornant à la zone utilisable de l'écran courant (voir ``core.window_fit``)."""

from __future__ import annotations

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QWidget

from core.window_fit import FRAME_ALLOWANCE, Rect, fit_rect


def usable_rect(widget: QWidget | None = None) -> Rect | None:
    """Zone utilisable (hors barre de menus et Dock) de l'écran du widget, sinon de l'écran principal ; ``None`` sans écran."""
    screen = widget.screen() if widget is not None else None
    screen = screen or QGuiApplication.primaryScreen()
    if screen is None:
        return None
    area = screen.availableGeometry()
    return area.x(), area.y(), area.width(), area.height()


def apply_fitted_geometry(widget: QWidget, rect: Rect, *, center: bool = False, frame: int = FRAME_ALLOWANCE) -> Rect:
    """``setGeometry`` borné à l'écran : une géométrie venue d'un autre écran ne sort jamais de celui-ci."""
    area = usable_rect(widget)
    fitted = rect if area is None else fit_rect(rect, area, frame=frame, center=center)
    widget.setGeometry(*fitted)
    return fitted


__all__ = ["apply_fitted_geometry", "usable_rect"]
