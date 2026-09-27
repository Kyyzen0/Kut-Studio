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
"""

from __future__ import annotations

from dataclasses import dataclass, field
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

    background: str = "#111318"
    panel: str = "#191C22"
    panel_alt: str = "#15181D"
    surface: str = "#20242C"
    surface_hover: str = "#282D37"
    border: str = "#2B303A"
    text: str = "#F4F4F5"
    muted: str = "#9298A5"
    accent: str = "#7C5CFC"
    accent_hover: str = "#8B70FF"
    accent_dark: str = "#30245F"
    success: str = "#67D6A3"
    danger: str = "#F27686"
    disabled_text: str = "#9AA0AC"
    button_disabled_bg: str = "#1A1D22"
    tooltip_bg: str = "#20242C"
    selection: str = "#30245F"
    diamond_filled: str = "#7C5CFC"
    diamond_outline: str = "#5C5C66"


# Alias historique conservé : la palette "dark" courante.
COLORS: dict = ThemePalette().__dict__


THEMES: dict[str, ThemePalette] = {
    "dark": ThemePalette(),
    "light": ThemePalette(
        background="#F4F5F8",
        panel="#FFFFFF",
        panel_alt="#F0F2F6",
        surface="#FFFFFF",
        surface_hover="#E7EAF2",
        border="#CFD3DC",
        text="#1B1F26",
        muted="#5C6470",
        accent="#7C5CFC",
        accent_hover="#8B70FF",
        accent_dark="#E2DBFF",
        success="#22A06B",
        danger="#D63A52",
        disabled_text="#9098A2",
        button_disabled_bg="#EDF0F4",
        tooltip_bg="#FFFFFF",
        selection="#E2DBFF",
        diamond_filled="#7C5CFC",
        diamond_outline="#9098A2",
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
        font-family: 'SF Pro Text', 'Helvetica Neue', Arial, sans-serif;
        font-size: 13px;
    }}
    QMainWindow {{ background: {palette.background}; }}
    QToolTip {{
        background: {palette.tooltip_bg}; color: {palette.text};
        border: 1px solid {palette.border}; padding: 5px 8px;
    }}
    QSplitter::handle {{ background: {palette.background}; }}
    QSplitter::handle:horizontal {{ width: 6px; }}
    QSplitter::handle:vertical {{ height: 6px; }}
    QPushButton {{
        background: {palette.surface}; color: {palette.text};
        border: 1px solid {palette.border}; border-radius: 6px;
        padding: 7px 11px;
    }}
    QPushButton:hover {{ background: {palette.surface_hover}; border-color: {palette.border}; }}
    QPushButton:pressed {{ background: {palette.accent_dark}; }}
    QPushButton:disabled {{
        color: {palette.disabled_text}; background: {palette.button_disabled_bg};
        border-color: {palette.border};
    }}
    QLineEdit, QTextEdit, QComboBox, QListWidget {{
        background: {palette.panel_alt}; color: {palette.text};
        border: 1px solid {palette.border}; border-radius: 6px;
        selection-background-color: {palette.selection};
    }}
    QComboBox {{ padding: 6px 8px; }}
    QComboBox QAbstractItemView {{ background: {palette.surface}; color: {palette.text}; }}
    QSlider::groove:horizontal {{ height: 4px; background: {palette.border}; border-radius: 2px; }}
    QSlider::handle:horizontal {{
        width: 12px; margin: -4px 0; background: {palette.accent}; border-radius: 6px;
    }}
    QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
    QScrollBar::handle:vertical {{
        background: {palette.border}; border-radius: 4px; min-height: 24px;
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QMenuBar {{
        background: {palette.panel}; color: {palette.muted};
        border-bottom: 1px solid {palette.border}; padding: 3px 8px;
    }}
    QMenuBar::item {{ padding: 5px 9px; border-radius: 4px; }}
    QMenuBar::item:selected {{ background: {palette.surface_hover}; color: {palette.text}; }}
    QMenu {{ background: {palette.surface}; color: {palette.text}; border: 1px solid {palette.border}; }}
    QMenu::item {{ padding: 7px 22px; }}
    QMenu::item:selected {{ background: {palette.accent_dark}; }}
    QGroupBox {{
        color: {palette.muted}; border: 1px solid {palette.border};
        border-radius: 6px; margin-top: 10px; padding-top: 10px;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin; left: 10px; padding: 0 5px; color: {palette.muted};
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


# Compatibilité ascendante : garde l'ancienne signature sans argument.
def _compat_stylesheet() -> str:
    return _stylesheet(THEMES["dark"])


# L'ancien nom ``global_stylesheet`` est exposé avec la nouvelle
# signature : on garde ``label_style`` inchangé pour les widgets existants.


def label_style(size=12, color="text", weight=400, palette: ThemePalette | None = None) -> str:
    """Génère un style inline minimal à partir des couleurs nommées."""
    if palette is None:
        palette = ThemePalette()
    hex_color = getattr(palette, color, color) if isinstance(color, str) else color
    return f"color: {hex_color}; font-size: {size}px; font-weight: {weight};"


# Compatibilité avec la signature historique : ``label_style(size, color,
# weight)`` accepte toujours ``color="text"`` etc.
# Le module ``ui.theme`` historique exposait ``COLORS`` et ``global_stylesheet``
# ; on garde ces symboles fonctionnels.


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

    # ------------------------------------------------------------------
    # API publique
    # ------------------------------------------------------------------

    def apply_to(self, app) -> None:
        """Applique la feuille de style à ``app``.

        Args:
            app: instance ``QApplication`` ou équivalent (objet Qt racine).
        """
        stylesheet = _stylesheet(self.effective_palette)
        if app is not None:
            try:
                app.setStyleSheet(stylesheet)
            except Exception:
                # Compat : si ``app`` n'a pas ``setStyleSheet``, on
                # ignore silencieusement (cas des tests dry-run).
                pass

    def set_mode(self, mode: str) -> bool:
        """Change la palette active.

        Args:
            mode: ``"dark"``, ``"light"`` ou ``"system"``.

        Returns:
            ``True`` si la palette effective a changé.
        """
        if mode not in SYSTEM_THEME_NAMES:
            return False
        self.requested_mode = mode
        new_palette = self._resolve_palette(mode)
        if new_palette is self.effective_palette:
            return False
        self.effective_palette = new_palette
        self._notify()
        return True

    def refresh(self) -> None:
        """Recalcule la palette effective (par exemple si le système change)."""
        new_palette = self._resolve_palette(self.requested_mode)
        if new_palette is self.effective_palette:
            return
        self.effective_palette = new_palette
        self._notify()

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
