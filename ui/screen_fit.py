"""Applique une géométrie de fenêtre en la bornant à la zone utilisable d'un écran (voir ``core.window_fit``)."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtGui import QCursor, QGuiApplication
from PySide6.QtWidgets import QWidget

from core.window_fit import FRAME_ALLOWANCE, Rect, fit_rect, pick_screen


def screen_rects() -> list[Rect]:
    """Zones utilisables (hors barre de menus et Dock) de tous les écrans, écran principal en premier."""
    primary = QGuiApplication.primaryScreen()
    screens = sorted(QGuiApplication.screens(), key=lambda screen: screen is not primary)
    return [(a.x(), a.y(), a.width(), a.height()) for a in (screen.availableGeometry() for screen in screens)]


def usable_rect(widget: QWidget | None = None) -> Rect | None:
    """Zone utilisable de l'écran du widget, sinon de l'écran principal ; ``None`` sans écran."""
    screen = widget.screen() if widget is not None else None
    screen = screen or QGuiApplication.primaryScreen()
    if screen is None:
        return None
    area = screen.availableGeometry()
    return area.x(), area.y(), area.width(), area.height()


def cursor_anchor() -> Rect:
    """Petit rectangle sous le curseur : l'écran où s'ouvre une fenêtre sans position enregistrée."""
    position = QCursor.pos()
    return position.x(), position.y(), 1, 1


def apply_fitted_geometry(
    widget: QWidget,
    rect: Rect,
    *,
    center: bool = False,
    frame: int = FRAME_ALLOWANCE,
    anchor: Rect | None = None,
    screens: Callable[[], list[Rect]] = screen_rects,
) -> Rect:
    """``setGeometry`` borné à l'écran qui contient (ou recouvre le plus) ``anchor`` (par défaut ``rect``).

    Un panneau enregistré sur un moniteur secondaire toujours branché y reste ; sans écran qui le rencontre, on se replie
    sur l'écran du widget puis sur l'écran principal. ``screens`` est injectable (tests).
    """
    area = pick_screen(anchor or rect, screens(), usable_rect(widget))
    fitted = rect if area is None else fit_rect(rect, area, frame=frame, center=center)
    widget.setGeometry(*fitted)
    return fitted


__all__ = ["apply_fitted_geometry", "cursor_anchor", "screen_rects", "usable_rect"]
