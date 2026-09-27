"""Thème visuel de Kut-Studio (dark/light/system) + sélecteur.

Ce module expose un dictionnaire de palettes :data:`THEMES` (par thème)
et une petite classe :class:`ThemeManager` qui orchestre les changements
runtime. Les panneaux qui appliquent des styles ``setStyleSheet(...)``
locaux (timeline, propriétés, preview…) peuvent s'abonner via
:meth:`ThemeManager.subscribe` pour se rafraîchir après un changement
de thème.

Trois palettes :

- ``dark`` (par défaut historique) : fond très sombre, accents violets ;
- ``light`` : fond clair, texte sombre, accents violets conservés ;
- ``system`` : délègue au thème courant de Qt (QPalette) ; fallback
  ``dark`` si la résolution échoue.

Les palettes sont indépendantes de Qt (les codes couleurs sont des
``str`` hexadécimaux). Le :class:`ThemeManager` s'appuie sur Qt pour
détecter le thème système via ``QApplication.styleHints().colorScheme``.

La palette fournit deux niveaux de tokens :

1. **Tokens bruts** (ex. ``background``, ``surface``, ``border``) :
   valeurs sémantiques utilisées pour composer des styles locaux.
2. **Tokens avancés** (ex. ``track_video``, ``track_audio``,
   ``track_subtitle``, ``playhead``, ``clip_border``) : valeurs
   spécifiques à certains éléments de l'interface (timeline, clips).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


# ---------------------------------------------------------------------------
# Palettes
# ---------------------------------------------------------------------------


@dataclass
class ThemePalette:
    """Palette de couleurs pour un thème donné.

    Les valeurs reflètent l'identité visuelle de Kut-Studio :
    fond sombre ou clair, accents violets, accents colorés pour les
    états de validation, gris pour le texte / bordures.
    """

    # Surfaces
    background: str = "#11141A"
    panel: str = "#161A21"
    panel_alt: str = "#1C2129"
    surface: str = "#222834"
    surface_hover: str = "#2B3242"
    surface_active: str = "#323A4D"

    # Bordures / séparateurs
    border: str = "#2B303A"
    border_strong: str = "#3A4150"
    divider: str = "#232831"

    # Texte
    text: str = "#F4F5F7"
    text_strong: str = "#FFFFFF"
    muted: str = "#9098A4"
    muted_strong: str = "#B7BDC8"
    disabled_text: str = "#6B7280"

    # États désactivés
    button_disabled_bg: str = "#1A1D22"
    button_disabled_border: str = "#252A33"

    # Accents
    accent: str = "#7C5CFC"
    accent_hover: str = "#8E73FF"
    accent_dark: str = "#322569"
    accent_dark_hover: str = "#3B2C7A"
    selection: str = "#322569"

    # Statuts
    success: str = "#67D6A3"
    success_dark: str = "#1E3C2D"
    danger: str = "#F27686"
    danger_dark: str = "#3F1F25"
    warning: str = "#F7C948"

    # Tooltip
    tooltip_bg: str = "#222834"

    # Timeline / pistes
    timeline_bg: str = "#14171D"
    timeline_grid: str = "#1A1E25"
    track_header_bg: str = "#161A21"
    track_alt_bg: str = "#181C24"
    track_divider: str = "#262B34"
    ruler_bg: str = "#1B1F26"
    ruler_line: str = "#3A4150"
    playhead: str = "#67D6A3"
    playhead_dim: str = "#67D6A380"
    marker: str = "#F7C948"
    selection_line: str = "#7C5CFC"
    snap_line: str = "#7C5CFC80"
    transition_overlay: str = "#F7C948"
    transition_overlay_bg: str = "#14171D"

    # Pistes par type
    track_video: str = "#5B6CFF"
    track_audio: str = "#67D6A3"
    track_subtitle: str = "#F7C948"

    # Clip
    clip_border: str = "#3A4150"
    clip_border_selected: str = "#8E73FF"
    clip_border_hover: str = "#7C5CFC"
    clip_text: str = "#FFFFFF"
    clip_text_dim: str = "#FFFFFFB3"

    # Keyframe diamond
    diamond_filled: str = "#7C5CFC"
    diamond_outline: str = "#5C5C66"
    diamond_border: str = "#FFFFFF"


# ---------------------------------------------------------------------------
# Palette active
# ---------------------------------------------------------------------------
#
# Les panneaux construisent leurs styles locaux à partir de ``COLORS``.
# Il s'agit d'un ``dict`` simple figé sur le thème sombre, ce qui rendait
# le thème clair illisible (styles sombres sur fond clair) et imposait de
# dupliquer chaque couleur. ``COLORS`` est donc un mapping *vivant* qui
# délègue à la palette active : un seul point de bascule pour toute
# l'application.


_ACTIVE_PALETTE: ThemePalette = ThemePalette()


def active_palette() -> ThemePalette:
    """Retourne la palette actuellement appliquée à l'application."""
    return _ACTIVE_PALETTE


def set_active_palette(palette: ThemePalette) -> None:
    """Bascule la palette utilisée par :data:`COLORS` et ``label_style``."""
    global _ACTIVE_PALETTE
    _ACTIVE_PALETTE = palette


class _LiveColors:
    """Mapping en lecture seule suivant la palette active.

    Se comporte comme un ``dict[str, str]`` pour tous les usages
    existants (``COLORS["panel"]``, ``.get(...)``, ``in``, ``copy()``).
    """

    __slots__ = ()

    def __getitem__(self, key: str) -> str:
        try:
            return getattr(_ACTIVE_PALETTE, key)
        except AttributeError as exc:  # pragma: no cover - garde-fou
            raise KeyError(key) from exc

    def __iter__(self):
        return iter(vars(_ACTIVE_PALETTE))

    def __len__(self) -> int:
        return len(vars(_ACTIVE_PALETTE))

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and hasattr(_ACTIVE_PALETTE, key)

    def keys(self):
        return vars(_ACTIVE_PALETTE).keys()

    def values(self):
        return vars(_ACTIVE_PALETTE).values()

    def items(self):
        return vars(_ACTIVE_PALETTE).items()

    def get(self, key, default=None):
        return getattr(_ACTIVE_PALETTE, key, default)

    def copy(self) -> dict:
        return vars(_ACTIVE_PALETTE).copy()

    def __repr__(self) -> str:  # pragma: no cover - confort de débogage
        return f"_LiveColors({len(self)} tokens, thème actif inclus)"


# Mapping vivant des couleurs (remplace l'ancien instantané du thème sombre).
COLORS: _LiveColors = _LiveColors()


THEMES: dict[str, ThemePalette] = {
    "dark": ThemePalette(),
    "light": ThemePalette(
        background="#F4F5F8",
        panel="#FFFFFF",
        panel_alt="#F0F2F6",
        surface="#FFFFFF",
        surface_hover="#E7EAF2",
        surface_active="#DDE2EE",
        border="#CFD3DC",
        border_strong="#B7BDC8",
        divider="#E5E8EE",
        text="#1B1F26",
        text_strong="#0E1116",
        muted="#5C6470",
        muted_strong="#3F4651",
        disabled_text="#9098A2",
        button_disabled_bg="#EDF0F4",
        button_disabled_border="#DDE1E8",
        accent="#7C5CFC",
        accent_hover="#6A4DE0",
        accent_dark="#E2DBFF",
        accent_dark_hover="#D4C9FF",
        selection="#E2DBFF",
        success="#22A06B",
        success_dark="#DEF2E5",
        danger="#D63A52",
        danger_dark="#F8DDE2",
        warning="#B8860B",
        tooltip_bg="#FFFFFF",
        timeline_bg="#F4F5F8",
        timeline_grid="#EEF0F4",
        track_header_bg="#FFFFFF",
        track_alt_bg="#F6F7FA",
        track_divider="#E0E3EA",
        ruler_bg="#FFFFFF",
        ruler_line="#CFD3DC",
        playhead="#22A06B",
        playhead_dim="#22A06B80",
        marker="#B8860B",
        selection_line="#7C5CFC",
        snap_line="#7C5CFC80",
        transition_overlay="#B8860B",
        transition_overlay_bg="#FFFFFFE6",
        track_video="#3F4FBF",
        track_audio="#1E8A5C",
        track_subtitle="#B8860B",
        clip_border="#B7BDC8",
        clip_border_selected="#6A4DE0",
        clip_border_hover="#7C5CFC",
        clip_text="#FFFFFF",
        clip_text_dim="#FFFFFFB3",
        diamond_filled="#7C5CFC",
        diamond_outline="#9098A2",
        diamond_border="#FFFFFF",
    ),
    "system": ThemePalette(),  # valeur par défaut, résolu à l'application.
}


SYSTEM_THEME_NAMES: tuple[str, ...] = ("system", "dark", "light")


# ---------------------------------------------------------------------------
# Stylesheet global
# ---------------------------------------------------------------------------


def _stylesheet(palette: ThemePalette) -> str:
    return f"""
    QWidget {{
        color: {palette.text};
        font-family: 'SF Pro Text', 'Helvetica Neue', 'Segoe UI', Arial, sans-serif;
        font-size: 13px;
    }}
    QMainWindow {{ background: {palette.background}; }}
    QToolTip {{
        background: {palette.tooltip_bg}; color: {palette.text};
        border: 1px solid {palette.border}; padding: 5px 8px;
        border-radius: 5px;
    }}
    QSplitter::handle {{ background: {palette.background}; }}
    QSplitter::handle:horizontal {{ width: 6px; }}
    QSplitter::handle:vertical {{ height: 6px; }}
    QSplitter::handle:hover {{ background: {palette.accent}; }}

    QPushButton {{
        background: {palette.surface}; color: {palette.text};
        border: 1px solid {palette.border}; border-radius: 6px;
        padding: 7px 12px; font-weight: 500;
    }}
    QPushButton:hover {{
        background: {palette.surface_hover}; border-color: {palette.border_strong};
    }}
    QPushButton:pressed {{ background: {palette.accent_dark}; }}
    QPushButton:disabled {{
        color: {palette.disabled_text}; background: {palette.button_disabled_bg};
        border-color: {palette.button_disabled_border};
    }}

    QToolButton {{
        background: transparent; color: {palette.text};
        border: 1px solid transparent; border-radius: 6px;
        padding: 5px 8px;
    }}
    /* Boutons « icône seule » : le padding par défaut rogne l'icône
       dans les boutons compacts, on le neutralise. */
    QToolButton#iconOnly, QToolButton#accentIcon {{
        padding: 0; border-radius: 5px;
    }}
    /* Action primaire (bouton Lecture, Exporter…) : une seule surface
       d'accent par écran, pour marquer clairement le geste principal. */
    QToolButton#accentIcon, QToolButton#accentText {{
        background: {palette.accent}; color: {palette.text_strong};
        border: 1px solid {palette.accent};
    }}
    QToolButton#accentIcon:hover, QToolButton#accentText:hover {{
        background: {palette.accent_hover};
        border-color: {palette.accent_hover};
    }}
    QToolButton#accentIcon:pressed, QToolButton#accentText:pressed {{
        background: {palette.accent}; border-color: {palette.accent};
    }}
    QToolButton#accentIcon:disabled, QToolButton#accentText:disabled {{
        background: {palette.button_disabled_bg}; color: {palette.disabled_text};
        border-color: {palette.button_disabled_border};
    }}
    QToolButton:hover {{ background: {palette.surface_hover}; }}
    QToolButton:pressed {{ background: {palette.accent_dark}; }}
    QToolButton:checked {{
        background: {palette.accent_dark}; color: {palette.text};
        border-color: {palette.accent};
    }}
    QToolButton#iconOnly:checked {{
        background: {palette.accent_dark}; border-color: {palette.accent};
    }}
    QToolButton:disabled {{
        color: {palette.disabled_text}; background: transparent;
    }}

    QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QListWidget, QSpinBox, QDoubleSpinBox {{
        background: {palette.panel_alt}; color: {palette.text};
        border: 1px solid {palette.border}; border-radius: 6px;
        selection-background-color: {palette.selection};
        padding: 4px 6px;
    }}
    QComboBox {{ padding: 6px 10px; }}
    QComboBox::drop-down {{ border: none; width: 18px; }}
    QComboBox QAbstractItemView {{
        background: {palette.surface}; color: {palette.text};
        border: 1px solid {palette.border};
    }}
    QSpinBox::up-button, QSpinBox::down-button,
    QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
        width: 14px; border: none;
    }}
    QSpinBox::up-button:hover, QSpinBox::down-button:hover,
    QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {{
        background: {palette.surface_hover};
    }}

    QSlider::groove:horizontal {{ height: 4px; background: {palette.border}; border-radius: 2px; }}
    QSlider::sub-page:horizontal {{ background: {palette.accent}; border-radius: 2px; }}
    QSlider::handle:horizontal {{
        width: 14px; margin: -6px 0; background: {palette.accent};
        border-radius: 7px; border: 2px solid {palette.surface};
    }}
    QSlider::handle:horizontal:hover {{ background: {palette.accent_hover}; }}
    QSlider:disabled {{ color: {palette.disabled_text}; }}

    QCheckBox {{
        color: {palette.text}; spacing: 8px;
    }}
    QCheckBox::indicator {{
        width: 16px; height: 16px; border-radius: 4px;
        border: 1px solid {palette.border_strong};
        background: {palette.panel_alt};
    }}
    QCheckBox::indicator:hover {{ border-color: {palette.accent}; }}
    QCheckBox::indicator:checked {{
        background: {palette.accent}; border-color: {palette.accent};
        image: none;
    }}

    QScrollBar:vertical {{
        background: transparent; width: 10px; margin: 2px;
    }}
    QScrollBar::handle:vertical {{
        background: {palette.border}; border-radius: 5px; min-height: 24px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {palette.border_strong}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar:horizontal {{
        background: transparent; height: 10px; margin: 2px;
    }}
    QScrollBar::handle:horizontal {{
        background: {palette.border}; border-radius: 5px; min-width: 32px;
    }}
    QScrollBar::handle:horizontal:hover {{ background: {palette.border_strong}; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

    QMenuBar {{
        background: {palette.panel}; color: {palette.muted};
        border-bottom: 1px solid {palette.border}; padding: 3px 8px;
    }}
    QMenuBar::item {{ padding: 5px 10px; border-radius: 4px; }}
    QMenuBar::item:selected {{ background: {palette.surface_hover}; color: {palette.text}; }}
    QMenu {{ background: {palette.surface}; color: {palette.text}; border: 1px solid {palette.border}; }}
    QMenu::item {{ padding: 7px 22px; }}
    QMenu::item:selected {{ background: {palette.accent_dark}; }}
    QMenu::separator {{ height: 1px; background: {palette.divider}; margin: 4px 6px; }}

    QGroupBox {{
        color: {palette.muted_strong}; border: 1px solid {palette.border};
        border-radius: 6px; margin-top: 12px; padding-top: 10px;
        font-weight: 600;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin; left: 12px; padding: 0 6px;
        color: {palette.muted_strong};
    }}

    QLabel {{ color: {palette.text}; }}
    QLabel:disabled {{ color: {palette.disabled_text}; }}

    QProgressBar {{
        background: {palette.panel_alt}; border: 1px solid {palette.border};
        border-radius: 6px; text-align: center; color: {palette.text};
        height: 18px;
    }}
    QProgressBar::chunk {{
        background: {palette.accent}; border-radius: 5px;
    }}

    QRadioButton {{ color: {palette.text}; spacing: 8px; }}
    QRadioButton::indicator {{
        width: 16px; height: 16px; border-radius: 8px;
        border: 1px solid {palette.border_strong}; background: {palette.panel_alt};
    }}
    QRadioButton::indicator:checked {{
        background: {palette.accent}; border-color: {palette.accent};
    }}
    """


def global_stylesheet(palette: ThemePalette | None = None) -> str:
    """Retourne la feuille de style globale pour ``palette``.

    Args:
        palette: thème à rendre (par défaut : thème courant dans :data:`THEMES`).
    """
    if palette is None:
        palette = THEMES["dark"]
    return _stylesheet(palette)


def _compat_stylesheet() -> str:
    return _stylesheet(THEMES["dark"])


def label_style(
    size: int = 12,
    color: str = "text",
    weight: int = 400,
    palette: ThemePalette | None = None,
) -> str:
    """Génère un style inline minimal à partir des couleurs nommées."""
    if palette is None:
        # Suit la palette active : les libellés restent lisibles quel
        # que soit le thème courant.
        palette = active_palette()
    hex_color = getattr(palette, color, color) if isinstance(color, str) else color
    return f"color: {hex_color}; font-size: {size}px; font-weight: {weight};"


def colors_dict(palette: ThemePalette | None = None) -> dict:
    """Retourne un ``dict[str, str]`` des couleurs de ``palette``."""
    if palette is None:
        palette = ThemePalette()
    return palette.__dict__.copy()


# ---------------------------------------------------------------------------
# ThemeManager
# ---------------------------------------------------------------------------


class ThemeManager:
    """Sélecteur de thème pour l'application Qt.

    Le manager conserve la préférence de l'utilisateur (``requested_mode``)
    et la palette effective (``effective_palette``). Le mode ``system``
    est résolu à l'application de Qt, avec fallback ``dark`` sûr.

    Les abonnés reçoivent un :class:`ThemeChangeEvent` lors de chaque
    changement ; les widgets peuvent ainsi se reconstruire ou
    réappliquer leur style local.
    """

    def __init__(self, requested_mode: str = "dark") -> None:
        if requested_mode not in SYSTEM_THEME_NAMES:
            requested_mode = "dark"
        self.requested_mode: str = requested_mode
        self.effective_palette: ThemePalette = self._resolve_palette(requested_mode)
        self._subscribers: list[Callable] = []
        # Les panneaux lisent leurs couleurs via ``COLORS`` : on publie
        # la palette résolue avant même la construction des widgets.
        set_active_palette(self.effective_palette)

    # ------------------------------------------------------------------
    # API publique
    # ------------------------------------------------------------------

    def apply_to(self, app) -> None:
        """Applique la feuille de style à ``app``."""
        stylesheet = _stylesheet(self.effective_palette)
        if app is not None:
            try:
                marker_name = "_kut_studio_theme_stylesheet"
                if app.property(marker_name) == stylesheet:
                    return
                app.setStyleSheet(stylesheet)
                app.setProperty(marker_name, stylesheet)
            except Exception:
                pass

    def set_mode(self, mode: str) -> bool:
        """Change la palette active."""
        if mode not in SYSTEM_THEME_NAMES:
            return False
        self.requested_mode = mode
        new_palette = self._resolve_palette(mode)
        if new_palette is self.effective_palette:
            return False
        self.effective_palette = new_palette
        set_active_palette(new_palette)
        self._notify()
        return True

    def refresh(self) -> None:
        """Recalcule la palette effective (par exemple si le système change)."""
        new_palette = self._resolve_palette(self.requested_mode)
        if new_palette is self.effective_palette:
            return False
        self.effective_palette = new_palette
        set_active_palette(new_palette)
        self._notify()
        return True

    def stylesheet(self) -> str:
        """Retourne la feuille de style correspondant à la palette active."""
        return _stylesheet(self.effective_palette)

    def subscribe(self, callback: Callable) -> None:
        """Inscrit ``callback(manager)`` aux changements de palette."""
        if callback not in self._subscribers:
            self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable) -> None:
        """Désabonne un callback."""
        try:
            self._subscribers.remove(callback)
        except ValueError:
            pass

    def snapshot(self) -> dict:
        """Sérialise l'état pour persistance."""
        return {"requested_mode": self.requested_mode}

    @classmethod
    def from_snapshot(cls, snapshot: dict | None) -> "ThemeManager":
        """Reconstruit un manager depuis un snapshot sérialisé."""
        if not snapshot:
            return cls()
        requested = snapshot.get("requested_mode", "dark")
        return cls(requested_mode=requested)

    # ------------------------------------------------------------------
    # Helpers privés
    # ------------------------------------------------------------------

    def _resolve_palette(self, mode: str) -> ThemePalette:
        if mode == "light":
            return THEMES["light"]
        if mode == "system":
            detected = self._detect_system_palette_name()
            if detected == "light":
                return THEMES["light"]
        return THEMES["dark"]

    @staticmethod
    def _detect_system_palette_name() -> str:
        """Demande à Qt le thème système, avec import paresseux."""
        try:
            from PySide6.QtCore import QCoreApplication
        except Exception:
            return "dark"
        app = QCoreApplication.instance()
        if app is None:
            return "dark"
        try:
            from PySide6.QtGui import Qt
            hints = app.styleHints()
            if hints is None:
                return "dark"
            scheme = hints.colorScheme()
            from PySide6.QtCore import Qt as QtCore
            if QtCore.Qt is not None and hasattr(QtCore, "ColorScheme"):
                if scheme == QtCore.Qt.ColorScheme.Light:
                    return "light"
        except Exception:
            pass
        return "dark"

    def _notify(self) -> None:
        for callback in list(self._subscribers):
            try:
                callback(self)
            except Exception:
                pass


__all__ = [
    "COLORS",
    "ThemePalette",
    "ThemeManager",
    "THEMES",
    "SYSTEM_THEME_NAMES",
    "colors_dict",
    "global_stylesheet",
    "label_style",
]