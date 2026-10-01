"""Constantes et aides partagées par les widgets de la timeline."""

from __future__ import annotations



from ui.design_system import Iconography
from ui.theme import ThemePalette


_TRACK_TYPE_LABELS = {
    "video": "Vidéo",
    "audio": "Audio",
    "subtitle": "Sous-titres",
    "graphics": "Graphiques",
}

_CONTENT_TOP = 8

_HEIGHTS = {"compact": 34, "normal": 48, "large": 88}

_COLLAPSED_HEIGHT = 28

def _color_for_track_type(track_type: str, palette: ThemePalette) -> str:
    """Couleur d'accent utilisée pour la pastille de type de piste."""
    mapping = {
        "video": palette.track_video,
        "audio": palette.track_audio,
        "subtitle": palette.track_subtitle,
        "graphics": palette.clip_adjustment,
    }
    return mapping.get(track_type, palette.accent)

# Petit helper pour accéder à la palette courante (utilisée par les
# en-têtes de piste pour récupérer les couleurs d'accent sans avoir à
# leur passer une dépendance explicite).
def _current_palette() -> ThemePalette:
    """Retourne la palette active de l'application.

    La résolution passe toujours par :mod:`ui.theme`, ce qui garantit que
    la timeline reste cohérente avec le thème courant (et pas figée sur
    le thème sombre au premier construit).
    """
    return _resolve_current_palette()

# Petite icône « − » réutilisée pour le bouton Zoom-.
def _minus_icon():
    from PySide6.QtGui import QIcon
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M5 12h14"/></svg>'
    )
    from PySide6.QtCore import QByteArray, Qt
    from PySide6.QtGui import QPixmap, QPainter
    from PySide6.QtSvg import QSvgRenderer
    icon = QIcon()
    for dpr in (1.0, 2.0):
        s = max(1, int(round(Iconography.md * dpr)))
        pixmap = QPixmap(s, s)
        pixmap.fill(Qt.transparent)
        renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        renderer.render(painter)
        painter.end()
        icon.addPixmap(pixmap)
    return icon

def _resolve_current_palette():
    """Retourne la palette active de l'application.

    La palette publiée par :func:`ui.theme.set_active_palette` est la
    source de vérité : la timeline suit ainsi le thème courant au lieu
    d'être figée sur le thème sombre.
    """
    try:
        from ui.theme import active_palette

        return active_palette()
    except Exception:  # pragma: no cover - garde-fou
        from ui.theme import ThemePalette

        return ThemePalette()
