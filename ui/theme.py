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

import logging
from dataclasses import dataclass
from typing import Callable

from ui.design_system import Radius, Sizes, Spacing, Typography, Weights

LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Palettes
# ---------------------------------------------------------------------------


@dataclass
class ThemePalette:
    """Palette de couleurs pour un thème donné.

    Direction artistique par défaut : identité vert-noir / turquoise
    menthe, inspirée des logiciels de montage haut de gamme. Les
    tokens restent sémantiques (``surface``, ``accent``, ``track_*``…)
    pour qu'un thème clair puisse les remplacer sans toucher au code
    métier.
    """

    # Surfaces — fond général vert-noir très sombre
    background: str = "#061514"
    panel: str = "#081C1A"
    panel_alt: str = "#0C2421"
    panel_elevated: str = "#102C28"
    surface: str = "#0E2724"
    surface_hover: str = "#143430"
    surface_active: str = "#1A3D38"
    # Champs de saisie : une surface à part dans la hiérarchie (fond → panneau → panneau relevé → champ → survol → actif).
    input_bg: str = "#0C2421"

    # Bordures / séparateurs discrets
    border: str = "#183834"
    border_strong: str = "#2A5F59"
    divider: str = "#0F2A27"

    # Texte — quasi-blanc légèrement teinté, secondaire gris-vert
    text: str = "#EEF8F5"
    text_strong: str = "#FFFFFF"
    muted: str = "#94AAA5"
    muted_strong: str = "#B7CFC9"
    disabled_text: str = "#62776F"

    # États désactivés
    button_disabled_bg: str = "#0B1E1C"
    button_disabled_border: str = "#152F2C"

    # Accent turquoise menthe (couleur d'identité)
    accent: str = "#36E6C3"
    accent_hover: str = "#5BEFD0"
    # Texte et icônes posés SUR une surface d'accent (bouton principal) : dessous, jamais de couleur en dur.
    on_accent: str = "#061515"
    # Anneau de focus clavier : visible sur toutes les surfaces du thème.
    focus_ring: str = "#36E6C3"
    accent_dark: str = "#0E3A33"
    accent_dark_hover: str = "#154E45"
    accent_glow: str = "#36E6C326"  # halo subtil, alpha ~15%
    selection: str = "#0E3A33"
    selection_line: str = "#36E6C3"

    # Couleurs sémantiques : même signification partout (succès, alerte, erreur, information) ; ``*_dark`` est le fond
    # discret d'un message ou d'une pastille de cet état.
    success: str = "#5BE0B4"
    success_dark: str = "#0F3A2D"
    danger: str = "#F27686"
    danger_dark: str = "#3A1F25"
    warning: str = "#F7C948"
    warning_dark: str = "#3A3112"
    info: str = "#5AA9E6"
    info_dark: str = "#16324A"

    # Tooltip
    tooltip_bg: str = "#0E2724"

    # Timeline / pistes
    timeline_bg: str = "#061918"
    timeline_grid: str = "#0A211F"
    track_header_bg: str = "#081C1A"
    track_alt_bg: str = "#0A1F1D"
    track_divider: str = "#102C29"
    ruler_bg: str = "#081C1A"
    ruler_line: str = "#2A5F59"
    playhead: str = "#36E6C3"
    playhead_dim: str = "#36E6C380"
    marker: str = "#F7C948"
    snap_line: str = "#36E6C380"
    # Conservés pour la palette : plus lus depuis la suppression de la pastille « Fondu enchaîné » de l'aperçu
    # (``docs/dead-code-audit.md``).
    transition_overlay: str = "#F7C948"
    transition_overlay_bg: str = "#061919"

    # Pistes par type — couleurs contrôlées, distinctes
    track_video: str = "#4FA3D9"        # bleu discret
    track_video_dim: str = "#1F4A66"
    track_audio: str = "#6FD08C"        # vert : distinct de l'accent, qui reste réservé à la sélection et au focus
    track_audio_dim: str = "#1C4A36"
    track_subtitle: str = "#B58EF9"     # violet pour titres et calques
    track_subtitle_dim: str = "#3F2D5A"

    # Clip : la couleur de fond dit la CATÉGORIE (vidéo, audio, titre, calque, imbriqué), jamais l'identité du clip ; le texte
    # ``clip_text`` reste lisible (≥ 4,5:1) sur chacun de ces fonds, la sélection se lit à la bordure et à l'éclaircissement.
    clip_border: str = "#2A5F59"
    clip_border_selected: str = "#36E6C3"
    clip_border_hover: str = "#5BEFD0"
    clip_text: str = "#FFFFFE"
    clip_text_dim: str = "#FFFFFECC"
    clip_title: str = "#B58EF9"
    clip_video_fill: str = "#2B6C99"
    clip_audio_fill: str = "#1D7A66"
    clip_title_fill: str = "#5A43A6"
    clip_graphic_fill: str = "#6B4DC4"
    clip_nested_fill: str = "#8C6D12"
    clip_broken_fill: str = "#9C3C42"
    clip_audio_wave: str = "#D6FFF1"
    clip_adjustment: str = "#7B5BE3"

    # Keyframe diamond : presque blanc sur n'importe quel fond de clip, anneau sombre ; l'accent est réservé à la sélection.
    diamond_filled: str = "#F4FFFC"
    diamond_outline: str = "#2A5F59"
    diamond_border: str = "#061513"
    # Palette catégorielle des angles Multicam (rang ``MulticamAngle.color_index`` modulo la taille) : jamais de
    # couleur d'angle dans la logique métier, seulement un rang que l'interface résout ici.
    angle_colors: tuple[str, ...] = (
        "#3B82F6", "#22C55E", "#A855F7", "#F97316", "#EC4899", "#14B8A6",
        "#EAB308", "#EF4444", "#6366F1", "#84CC16", "#06B6D4", "#F43F5E",
    )
    # Palette catégorielle des bibliothèques (catégories d'effets, de transitions, de styles) : six teintes qui ne reprennent ni
    # l'accent (réservé à la sélection et au focus) ni une couleur d'état. L'ordre est stable : une catégorie = un rang.
    category_colors: tuple[str, ...] = ("#5CA9E6", "#F7C948", "#F27686", "#8E7DFA", "#70D08C", "#F0A35E")
    # Courbes de couleur : l'identité des canaux (rouge, vert, bleu), qui n'a de sens que dans l'éditeur de courbes.
    channel_red: str = "#EF4445"
    channel_green: str = "#22C55F"
    channel_blue: str = "#3B82F7"


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
        # Surfaces : un blanc franc pour les panneaux sur un fond gris-vert très clair (l'identité du thème sombre, pas un gris
        # bleuté) ; les bordures restent des séparateurs, les surfaces font la hiérarchie.
        background="#EEF3F1",
        panel="#FAFCFB",
        panel_alt="#F2F6F4",
        panel_elevated="#FFFFFF",
        surface="#FFFFFE",
        surface_hover="#E4ECE9",
        surface_active="#D5E2DD",
        input_bg="#F2F6F4",
        border="#CBD7D3",
        border_strong="#8FA69F",
        divider="#E1E9E6",
        text="#16211E",
        text_strong="#0B1412",
        muted="#4E5F59",
        muted_strong="#34433E",
        disabled_text="#737C86",
        button_disabled_bg="#EDF1EF",
        button_disabled_border="#DCE4E1",
        # L'accent clair est plus profond que celui du thème sombre : lisible comme texte, icône ou focus sur blanc (≥ 4,5:1),
        # avec un texte blanc dessus.
        accent="#077A65",
        accent_hover="#055F4E",
        accent_dark="#D8F4ED",
        accent_dark_hover="#C2EBE0",
        accent_glow="#077A6526",
        on_accent="#FEFFFF",
        focus_ring="#077A65",
        selection="#D8F4ED",
        selection_line="#077A65",
        success="#157550",
        success_dark="#EAF7F0",
        danger="#C42D45",
        danger_dark="#FCEDEF",
        warning="#855E00",
        warning_dark="#FAF3DD",
        info="#256399",
        info_dark="#DCEAF6",
        tooltip_bg="#FFFFFE",
        timeline_bg="#EEF3F2",
        timeline_grid="#E6EDEA",
        track_header_bg="#FAFCFB",
        track_alt_bg="#F5F8F7",
        track_divider="#DCE5E1",
        ruler_bg="#FAFCFB",
        ruler_line="#8FA69F",
        playhead="#077A65",
        playhead_dim="#077A6580",
        marker="#855E00",
        snap_line="#077A6580",
        transition_overlay="#855E00",
        transition_overlay_bg="#FFFEFEE6",
        track_video="#2B6A9E",
        track_video_dim="#D9E7F4",
        track_audio="#12806A",
        track_audio_dim="#CDEEE5",
        track_subtitle="#6246C8",
        track_subtitle_dim="#E3DAF7",
        clip_border="#8FA69F",
        clip_border_selected="#077A65",
        clip_border_hover="#055F4E",
        clip_text="#FEFEFF",
        clip_text_dim="#FEFEFFD9",
        clip_title="#6246C8",
        clip_video_fill="#2B6A9F",
        clip_audio_fill="#12806B",
        clip_title_fill="#6246C9",
        clip_graphic_fill="#5B41BF",
        clip_nested_fill="#7F620E",
        clip_broken_fill="#B03A42",
        clip_audio_wave="#E3FFF6",
        clip_adjustment="#5C46BF",
        diamond_filled="#FEFFFE",
        diamond_outline="#8FA69F",
        diamond_border="#0B1413",
        category_colors=("#2B6A9D", "#855E00", "#C42D45", "#6246C7", "#157551", "#B45309"),
        channel_red="#DC2626",
        channel_green="#16A34A",
        channel_blue="#2563EB",
    ),
    "system": ThemePalette(),  # valeur par défaut, résolu à l'application.
}


SYSTEM_THEME_NAMES: tuple[str, ...] = ("system", "dark", "light")


# ---------------------------------------------------------------------------
# Stylesheet global
# ---------------------------------------------------------------------------


_UI_FONT_FAMILIES = ("SF Pro Text", "Helvetica Neue", "Segoe UI", "Arial")


def _installed_families(candidates: tuple[str, ...]) -> str:
    """Liste ``font-family`` limitée aux polices réellement installées.

    Qt reconstruit ses alias de polices (~300 ms) dès qu'une feuille de
    style nomme une famille absente : on retire donc celles qui manquent
    (ex. « SF Pro Text » sur macOS). Sans application Qt, la liste est
    renvoyée telle quelle.
    """
    from PySide6.QtGui import QFontDatabase, QGuiApplication

    names = list(candidates)
    if QGuiApplication.instance() is not None:
        installed = set(QFontDatabase.families())
        names = [name for name in candidates if name in installed] or [
            QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family()
        ]
    return ", ".join(f"'{name}'" for name in names)


def _stylesheet(palette: ThemePalette) -> str:
    """La feuille de style globale d'une palette.

    Toutes les mesures viennent de :mod:`ui.design_system` (rayons, espacements, tailles, graisses) et toutes les couleurs de la
    palette : aucune valeur en dur ici. Les variantes passent par des propriétés Qt dynamiques :

    * ``variant`` sur un ``QPushButton`` : ``primary`` / ``secondary`` (défaut) / ``ghost`` / ``destructive`` ;
    * ``role`` sur un ``QLabel`` : un des sept rôles de :data:`~ui.design_system.TextRoles` ;
    * ``state`` sur un ``QProgressBar`` ou un ``QLabel`` : ``idle`` / ``working`` / ``success`` / ``warning`` / ``error``.
    """
    r, s, t, w = Radius, Spacing, Typography, Weights
    return f"""
    QWidget {{
        color: {palette.text};
        font-family: {_installed_families(_UI_FONT_FAMILIES)};
        font-size: {t.body}px;
    }}
    QMainWindow {{ background: {palette.background}; }}
    /* Dialogues : sans fond explicite ils prennent celui de l'OS, et le texte
       du thème devenait illisible quand les deux ne s'accordent pas. */
    QDialog {{ background: {palette.background}; }}
    QToolTip {{
        background: {palette.tooltip_bg}; color: {palette.text};
        border: 1px solid {palette.border_strong}; padding: {s.xs}px {s.sm}px;
        border-radius: {r.md}px; font-size: {t.small}px;
    }}
    /* Séparateurs redimensionnables : zone de saisie confortable,
       aspect neutre au repos, teinte discrète au survol. */
    QSplitter::handle {{ background: {palette.background}; }}
    QSplitter::handle:horizontal {{ width: 4px; }}
    QSplitter::handle:vertical {{ height: 4px; }}
    QSplitter::handle:hover {{ background: {palette.accent}; opacity: 0.6; }}
    QSplitter::handle:pressed {{ background: {palette.accent_hover}; }}

    /* Boutons : quatre variantes (propriété ``variant``) et pas dix styles. Secondaire par défaut. */
    QPushButton {{
        background: {palette.surface}; color: {palette.text};
        border: 1px solid {palette.border}; border-radius: {r.md}px;
        padding: {Sizes.control_pad_y}px {s.md}px; font-weight: {w.medium};
    }}
    QPushButton:hover {{
        background: {palette.surface_hover}; border-color: {palette.border_strong};
    }}
    QPushButton:pressed {{ background: {palette.accent_dark}; }}
    QPushButton:disabled {{
        color: {palette.disabled_text}; background: {palette.button_disabled_bg};
        border-color: {palette.button_disabled_border};
    }}
    QPushButton[variant="primary"] {{
        background: {palette.accent}; color: {palette.on_accent};
        border-color: {palette.accent}; font-weight: {w.semibold};
    }}
    QPushButton[variant="primary"]:hover {{
        background: {palette.accent_hover}; border-color: {palette.accent_hover};
    }}
    QPushButton[variant="primary"]:pressed {{ background: {palette.accent}; border-color: {palette.accent}; }}
    QPushButton[variant="primary"]:disabled {{
        background: {palette.button_disabled_bg}; color: {palette.disabled_text};
        border-color: {palette.button_disabled_border};
    }}
    QPushButton[variant="ghost"] {{
        background: transparent; border-color: transparent; color: {palette.muted_strong};
    }}
    QPushButton[variant="ghost"]:hover {{ background: {palette.surface_hover}; color: {palette.text}; border-color: transparent; }}
    QPushButton[variant="ghost"]:pressed {{ background: {palette.accent_dark}; }}
    QPushButton[variant="ghost"]:disabled {{ background: transparent; border-color: transparent; color: {palette.disabled_text}; }}
    QPushButton[variant="destructive"] {{
        background: transparent; color: {palette.danger}; border-color: {palette.danger_dark};
    }}
    QPushButton[variant="destructive"]:hover {{ background: {palette.danger_dark}; border-color: {palette.danger}; }}
    QPushButton[variant="destructive"]:pressed {{ background: {palette.danger_dark}; }}
    QPushButton[variant="destructive"]:disabled {{
        color: {palette.disabled_text}; background: {palette.button_disabled_bg}; border-color: {palette.button_disabled_border};
    }}

    QToolButton {{
        background: transparent; color: {palette.text};
        border: 1px solid transparent; border-radius: {r.md}px;
        padding: {s.xs}px {s.sm}px;
    }}
    /* Boutons « icône seule » : le padding par défaut rogne l'icône
       dans les boutons compacts, on le neutralise. */
    QToolButton#iconOnly, QToolButton#accentIcon {{
        padding: 0; border-radius: {r.md}px;
    }}
    /* Action primaire (bouton Lecture, Exporter…) : une seule surface
       d'accent par écran, pour marquer clairement le geste principal. */
    QToolButton#accentIcon, QToolButton#accentText {{
        background: {palette.accent}; color: {palette.on_accent};
        border: 1px solid {palette.accent};
        font-weight: {w.semibold};
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

    /* Tranches du mixeur : le fond est déclaré dans la feuille globale
       pour que les boutons enfants conservent les règles globales. */
    QFrame#mixerStrip {{
        background: {palette.panel};
        border: 1px solid {palette.border};
        border-radius: {r.lg}px;
    }}
    QFrame#mixerMasterBar {{
        background: {palette.panel_alt};
        border-top: 1px solid {palette.border};
    }}

    /* Champs : une seule apparence (fond « champ », bordure discrète, anneau d'accent au focus). */
    QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QListWidget, QSpinBox, QDoubleSpinBox {{
        background: {palette.input_bg}; color: {palette.text};
        border: 1px solid {palette.border}; border-radius: {r.md}px;
        selection-background-color: {palette.selection};
        selection-color: {palette.text};
        padding: {s.xs}px {s.sm}px;
    }}
    QSpinBox, QDoubleSpinBox {{ min-height: {Sizes.icon_button_sm}px; }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
        border-color: {palette.focus_ring};
    }}
    QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
        color: {palette.disabled_text}; background: {palette.button_disabled_bg}; border-color: {palette.button_disabled_border};
    }}
    /* Focus clavier visible sur tous les contrôles (atteints avec Tab) : la même teinte d'accent que les champs. */
    QPushButton:focus, QToolButton:focus {{ border: 1px solid {palette.focus_ring}; }}
    QToolButton#accentIcon:focus, QToolButton#accentText:focus {{ border: 2px solid {palette.text}; }}
    QPushButton[variant="primary"]:focus {{ border: 2px solid {palette.text}; }}
    QCheckBox::indicator:focus, QRadioButton::indicator:focus {{ border: 2px solid {palette.focus_ring}; }}
    QListWidget:focus, QTreeWidget:focus, QTextEdit:focus, QPlainTextEdit:focus {{
        border: 1px solid {palette.focus_ring};
    }}
    QSlider::handle:horizontal:focus {{ border: 2px solid {palette.text}; }}
    /* Tableaux des raccourcis (Préférences), de la file de rendu et pile des calques : lisibles
       quel que soit le fond natif. */
    QTreeWidget#shortcutsTree, QTreeWidget#renderQueueTree, QTreeWidget#layers_tree {{
        background: {palette.input_bg}; color: {palette.text};
        alternate-background-color: {palette.panel};
        border: 1px solid {palette.border}; border-radius: {r.md}px; outline: 0;
    }}
    QTreeWidget#shortcutsTree::item, QTreeWidget#renderQueueTree::item, QTreeWidget#layers_tree::item {{
        padding: {s.xs}px;
    }}
    QTreeWidget#shortcutsTree::item:selected, QTreeWidget#renderQueueTree::item:selected,
    QTreeWidget#layers_tree::item:selected {{
        background: {palette.accent_dark}; color: {palette.text};
    }}
    QTreeWidget#shortcutsTree QHeaderView::section, QTreeWidget#renderQueueTree QHeaderView::section,
    QTreeWidget#layers_tree QHeaderView::section {{
        background: {palette.surface}; color: {palette.muted};
        border: none; border-bottom: 1px solid {palette.border};
        padding: {s.xs}px {s.sm}px; font-weight: {w.semibold};
    }}
    QComboBox {{ padding: {s.xs}px {s.md}px {s.xs}px {s.sm}px; }}
    QComboBox::drop-down {{ border: none; width: 18px; }}
    QComboBox QAbstractItemView {{
        background: {palette.panel_elevated}; color: {palette.text};
        border: 1px solid {palette.border_strong}; border-radius: {r.lg}px;
        selection-background-color: {palette.accent_dark};
        selection-color: {palette.text};
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
        border-radius: 7px; border: 2px solid {palette.panel};
    }}
    QSlider::handle:horizontal:hover {{ background: {palette.accent_hover}; }}
    QSlider:disabled {{ color: {palette.disabled_text}; }}

    QCheckBox {{
        color: {palette.text}; spacing: {s.sm}px;
    }}
    QCheckBox::indicator {{
        width: 16px; height: 16px; border-radius: {r.sm}px;
        border: 1px solid {palette.border_strong};
        background: {palette.input_bg};
    }}
    QCheckBox::indicator:hover {{ border-color: {palette.accent}; }}
    QCheckBox::indicator:checked {{
        background: {palette.accent}; border-color: {palette.accent};
        image: none;
    }}

    QScrollBar:vertical {{
        background: transparent; width: 8px; margin: 2px;
    }}
    QScrollBar::handle:vertical {{
        background: {palette.border_strong}; border-radius: {r.sm}px; min-height: 24px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {palette.muted}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar:horizontal {{
        background: transparent; height: 8px; margin: 2px;
    }}
    QScrollBar::handle:horizontal {{
        background: {palette.border_strong}; border-radius: {r.sm}px; min-width: 32px;
    }}
    QScrollBar::handle:horizontal:hover {{ background: {palette.muted}; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

    QMenuBar {{
        background: transparent; color: {palette.muted};
        border: none; padding: 0;
    }}
    QMenuBar::item {{ padding: {s.xs}px {s.sm}px; border-radius: {r.sm}px; }}
    QMenuBar::item:selected {{ background: {palette.surface_hover}; color: {palette.text}; }}
    /* Menus, listes déroulantes, infobulles : mêmes rayons (8 px), même fond relevé ; l'élément actif porte la sélection. */
    QMenu {{ background: {palette.panel_elevated}; color: {palette.text};
             border: 1px solid {palette.border_strong}; border-radius: {r.lg}px;
             padding: {s.xs}px; }}
    QMenu::item {{ padding: {s.sm}px {s.xl}px {s.sm}px {s.md}px; border-radius: {r.sm}px; }}
    QMenu::item:selected {{ background: {palette.accent_dark}; color: {palette.text}; }}
    QMenu::item:disabled {{ color: {palette.disabled_text}; }}
    QMenu::separator {{ height: 1px; background: {palette.divider}; margin: {s.xs}px {s.sm}px; }}

    /* Groupes : une section à plat (un titre au-dessus d'un contenu, une séparation discrète) et non plus une boîte
       bordée dont le titre mord sur la bordure : c'est ce qui donnait l'impression de « formulaire géant ». */
    QGroupBox {{
        color: {palette.muted_strong}; background: transparent;
        border: none; border-top: 1px solid {palette.divider};
        margin-top: {s.md}px; padding-top: {s.md}px;
        font-weight: {w.semibold};
    }}
    QGroupBox::title {{
        subcontrol-origin: margin; subcontrol-position: top left; left: 0; padding: 0 {s.xs}px 0 0;
        color: {palette.muted_strong}; font-size: {t.small}px;
    }}
    /* Section repliable (SectionBox) : la place du chevron à gauche du titre ; le titre s'éclaire au survol de l'en-tête. */
    QGroupBox[collapsible="true"]::title {{ left: {s.lg + s.xs}px; }}
    QGroupBox[folded="true"] {{ padding-top: 0; margin-bottom: 0; }}

    QLabel {{ color: {palette.text}; }}
    QLabel:disabled {{ color: {palette.disabled_text}; }}
    /* Rôles de texte (propriété ``role``) : sept rôles, six tailles ; les titres ressortent, les métadonnées s'effacent. */
    /* Bandeau de panneau (ui/panel_header.py) : même surface et même filet dans tous les panneaux. */
    QFrame#panelHeader {{ background: {palette.panel}; border: none; border-bottom: 1px solid {palette.border}; }}
    QFrame#panelHeader QLabel {{ background: transparent; }}
    QLabel[role="app-title"] {{ font-size: {t.heading}px; font-weight: {w.bold}; color: {palette.text_strong}; }}
    QLabel[role="panel-title"] {{ font-size: {t.body}px; font-weight: {w.semibold}; color: {palette.text}; }}
    QLabel[role="section-title"] {{ font-size: {t.small}px; font-weight: {w.semibold}; color: {palette.muted_strong}; }}
    QLabel[role="label"] {{ font-size: {t.body}px; font-weight: {w.regular}; color: {palette.text}; }}
    QLabel[role="label-secondary"] {{ font-size: {t.small}px; font-weight: {w.regular}; color: {palette.muted}; }}
    QLabel[role="helper"] {{ font-size: {t.micro}px; font-weight: {w.regular}; color: {palette.muted}; }}
    QLabel[role="meta"] {{ font-size: {t.caption}px; font-weight: {w.regular}; color: {palette.muted}; }}
    /* États (propriété ``state``) : même signification, mêmes couleurs partout. */
    QLabel[state="success"] {{ color: {palette.success}; }}
    QLabel[state="warning"] {{ color: {palette.warning}; }}
    QLabel[state="error"] {{ color: {palette.danger}; }}
    QLabel[state="working"] {{ color: {palette.accent}; }}
    QLabel[state="idle"] {{ color: {palette.muted}; }}
    QLabel[state="info"] {{ color: {palette.info}; }}

    /* Barre de progression : une seule épaisseur et une seule apparence (proxys, suivi, flux optique, export, import). */
    QProgressBar {{
        background: {palette.input_bg}; border: none;
        border-radius: {Sizes.progress_height // 2}px; text-align: center; color: {palette.text};
        min-height: {Sizes.progress_height}px; max-height: {Sizes.progress_height}px; font-size: {t.caption}px;
    }}
    QProgressBar::chunk {{
        background: {palette.accent}; border-radius: {Sizes.progress_height // 2}px;
    }}
    QProgressBar[state="success"]::chunk {{ background: {palette.success}; }}
    QProgressBar[state="warning"]::chunk {{ background: {palette.warning}; }}
    QProgressBar[state="error"]::chunk {{ background: {palette.danger}; }}
    QProgressBar[state="idle"]::chunk {{ background: {palette.border_strong}; }}

    QRadioButton {{ color: {palette.text}; spacing: {s.sm}px; }}
    QRadioButton::indicator {{
        width: 16px; height: 16px; border-radius: 8px;
        border: 1px solid {palette.border_strong}; background: {palette.input_bg};
    }}
    QRadioButton::indicator:checked {{
        background: {palette.accent}; border-color: {palette.accent};
    }}

    /* Onglets compacts (utilisés par inspecteur, transitions, etc.) */
    QTabWidget::pane {{
        border: none;
        background: transparent;
    }}
    QTabBar {{
        background: transparent;
        qproperty-drawBase: 0;
    }}
    QTabBar::tab {{
        background: transparent;
        color: {palette.muted};
        padding: {s.sm}px {s.md}px;
        border: none;
        border-bottom: 2px solid transparent;
        font-weight: {w.semibold};
        margin-right: {s.xs}px;
    }}
    QTabBar::tab:hover {{
        color: {palette.text};
    }}
    QTabBar::tab:selected {{
        color: {palette.text};
        border-bottom: 2px solid {palette.accent};
    }}

    /* Boutons « chips » utilisés pour les réglages rapides dans la top bar */
    QToolButton#chipButton {{
        background: {palette.surface};
        border: 1px solid {palette.border};
        border-radius: {r.md}px;
        padding: {s.xs}px {s.sm}px;
        color: {palette.text};
        font-weight: {w.medium};
    }}
    QToolButton#chipButton:hover {{
        background: {palette.surface_hover};
        border-color: {palette.border_strong};
    }}
    QToolButton#chipButton:checked {{
        background: {palette.accent_dark};
        border-color: {palette.accent};
        color: {palette.text};
    }}

    /* Switch (interrupteur) utilisé dans l'inspecteur */
    QToolButton#switchButton {{
        background: {palette.input_bg};
        border: 1px solid {palette.border_strong};
        border-radius: 11px;
        padding: 0;
        min-width: 32px;
        max-width: 32px;
        min-height: 18px;
        max-height: 18px;
    }}
    QToolButton#switchButton:checked {{
        background: {palette.accent};
        border-color: {palette.accent};
    }}

    /* Cards arrondies pour les blocs principaux : la surface les distingue du fond, la bordure n'est qu'un filet. */
    QFrame#card {{
        background: {palette.panel};
        border: 1px solid {palette.border};
        border-radius: {r.lg}px;
    }}
    QFrame#cardElevated {{
        background: {palette.panel_elevated};
        border: 1px solid {palette.border};
        border-radius: {r.lg}px;
    }}
    """


def set_style_property(widget, name: str, value) -> None:
    """Pose la propriété Qt ``name`` et fait relire la feuille de style au widget : c'est ainsi qu'une variante, un rôle ou un
    état (``variant``, ``role``, ``state``) change l'apparence sans aucun style local. Sans effet si la valeur est déjà posée."""
    value = getattr(value, "value", value)
    if widget.property(name) == value:
        return
    widget.setProperty(name, value)
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def set_variant(button, variant) -> None:
    """Variante d'un bouton : ``primary`` / ``secondary`` / ``ghost`` / ``destructive`` (:class:`~ui.design_system.ButtonVariant`)."""
    set_style_property(button, "variant", variant)


def set_role(label, role) -> None:
    """Rôle de texte d'un libellé (:data:`~ui.design_system.TextRoles` : ``panel-title``, ``section-title``, ``helper``…)."""
    set_style_property(label, "role", getattr(role, "name", role))


def set_state(widget, kind) -> None:
    """État d'un traitement ou d'un message (:class:`~ui.design_system.StatusKind`) : même couleur partout."""
    set_style_property(widget, "state", kind)


WHITE = "#FFFFFF"
BLACK = "#000000"
"""Les deux extrémités d'un mélange (:func:`mix_colors`) : éclaircir ou assombrir une teinte du thème."""


@dataclass(frozen=True)
class OverlayColors:
    """Couleurs des repères dessinés **sur l'image** (guides, zones de sécurité, poignées, états du suivi).

    Elles ne suivent pas le thème : elles se posent sur une image de caméra, dont la clarté n'a rien à voir avec lui. Elles sont
    centralisées ici (une seule définition par rôle) et dessinées avec un halo sombre (:data:`halo`) pour rester lisibles sur une
    image claire comme sombre."""

    selection: str = "#36E6C3"          # cadre et poignées du calque ou du tracker sélectionné
    selection_locked: str = "#A0A0A0"   # calque non modifiable
    guide: str = "#50C8FF"
    safe_action: str = "#FFD200"
    safe_title: str = "#00DCFF"
    platform_zone: str = "#FF3C5A"     # zone couverte par l'interface d'une plateforme (TikTok, Reels, Shorts)
    centre: str = "#FFFFFF"
    grid: str = "#FFFFFF"
    snap: str = "#FF3CA0"               # ligne de magnétisme pendant un glisser
    anchor: str = "#FFC828"
    handle_fill: str = "#14181C"
    halo: str = "#000000"
    uncertain: str = "#FFAA28"          # suivi : mesure douteuse (rond pointillé, jamais la couleur seule)
    lost: str = "#FF4646"               # suivi : mesure perdue (croix)
    manual: str = "#FFFFFF"             # suivi : mesure corrigée à la main
    unknown: str = "#B4B4B4"            # suivi : pas encore de mesure


OVERLAY = OverlayColors()


def overlay_qcolor(color: str, alpha: int = 255):
    """``QColor`` d'une couleur de :data:`OVERLAY` avec un canal alpha explicite (0-255).

    Jamais ``QColor("#rrggbbaa")`` : Qt lit les huit chiffres comme « #aarrggbb »."""
    from PySide6.QtGui import QColor

    result = QColor(color)
    result.setAlpha(alpha)
    return result


def _rgb(color: str) -> tuple[int, int, int]:
    text = color.lstrip("#")
    return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)


def mix_colors(base: str, other: str, amount: float) -> str:
    """Mélange ``base`` avec ``other`` (``amount`` de 0 à 1) : ``#rrggbb``. Éclaircir, assombrir ou fondre une teinte du thème
    sans coder une nouvelle couleur : ``mix_colors(fill, "#FFFFFF", 0.15)`` pour une surface sélectionnée."""
    amount = min(1.0, max(0.0, float(amount)))
    mixed = (round(a + (b - a) * amount) for a, b in zip(_rgb(base), _rgb(other)))
    return "#{:02X}{:02X}{:02X}".format(*mixed)


def set_stylesheet_if_changed(widget, sheet: str) -> bool:
    """``widget.setStyleSheet(sheet)`` seulement si la feuille a changé ; retourne ``True`` si elle a été écrite.

    Qt reparse et repolit la feuille de style **à chaque appel**, même avec un texte identique. Un clip de la timeline en posait trois à
    chaque rafraîchissement : 87 % du temps de ``refresh_clip_widgets`` (+45 % mesurés par ``tools.perf.bench`` après le polish). Les
    rafraîchissements qui ne changent rien (la grande majorité) ne coûtent plus rien."""
    if widget.styleSheet() == sheet:
        return False
    widget.setStyleSheet(sheet)
    return True


def with_alpha(color: str, alpha: float) -> str:
    """``#rrggbbaa`` : ``color`` (``#rrggbb`` ou ``#rrggbbaa``) avec la transparence ``alpha`` (0 à 1), pour une **feuille de style**.

    À ne pas donner à ``QColor`` : Qt lit les huit chiffres comme « #aarrggbb » (canal alpha en premier) ; pour peindre, on construit
    la couleur puis ``setAlpha``."""
    return f"#{color.lstrip('#')[:6]}{round(min(1.0, max(0.0, float(alpha))) * 255):02X}"


def qt_palette(palette: ThemePalette):
    """``QPalette`` Qt des couleurs du thème : ce que prennent les widgets que la feuille de style ne décrit pas.

    L'application ne posait que du QSS : tout widget sans fond explicite (le viewport d'une liste, une ligne de source, un panneau de
    dialogue) retombait sur la palette **native** de l'OS, donc des panneaux presque blancs dans un dialogue sombre, avec du texte
    clair dessus. Publier la palette du thème règle la cause plutôt que chaque symptôme, et les thèmes ne dépendent plus du mode
    d'apparence du système."""
    from PySide6.QtGui import QColor, QPalette

    qt = QPalette()
    roles = {
        QPalette.Window: palette.background,
        QPalette.WindowText: palette.text,
        QPalette.Base: palette.input_bg,
        QPalette.AlternateBase: palette.panel_alt,
        QPalette.Text: palette.text,
        QPalette.Button: palette.surface,
        QPalette.ButtonText: palette.text,
        QPalette.BrightText: palette.text_strong,
        QPalette.Highlight: palette.accent,
        QPalette.HighlightedText: palette.on_accent,
        QPalette.ToolTipBase: palette.surface,
        QPalette.ToolTipText: palette.text,
        QPalette.PlaceholderText: palette.muted,
        QPalette.Link: palette.accent,
        QPalette.Mid: palette.border,
        QPalette.Dark: palette.border_strong,
    }
    for role, color in roles.items():
        qt.setColor(role, QColor(color))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        qt.setColor(QPalette.Disabled, role, QColor(palette.disabled_text))
    return qt


def global_stylesheet(palette: ThemePalette | None = None) -> str:
    """Retourne la feuille de style globale pour ``palette``.

    Args:
        palette: thème à rendre (par défaut : thème courant dans :data:`THEMES`).
    """
    if palette is None:
        palette = THEMES["dark"]
    return _stylesheet(palette)


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


def monospace_font_family() -> str:
    """Déclaration ``font-family`` à chasse fixe pour les timecodes.

    Utilise la police monospace du système (Menlo, Consolas, DejaVu Sans
    Mono…) : nommer une famille absente (ex. « SF Mono », non installée
    globalement sur macOS) force Qt à reconstruire ses alias de polices,
    ce qui coûte ~300 ms au démarrage.
    """
    from PySide6.QtGui import QFontDatabase

    family = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family()
    return f"font-family: '{family}', monospace;"


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
                set_palette = getattr(app, "setPalette", None)          # d'abord : les widgets sans style prennent les couleurs du thème
                if callable(set_palette):
                    set_palette(qt_palette(self.effective_palette))
                app.setStyleSheet(stylesheet)
                app.setProperty(marker_name, stylesheet)
            except Exception:
                LOGGER.warning(
                    "Application de la feuille de style du thème en échec : l'interface garde son apparence précédente",
                    exc_info=True,
                )

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
        except ImportError:
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
            LOGGER.debug(
                "Lecture du thème clair / sombre du système en échec : thème sombre par défaut",
                exc_info=True,
            )
        return "dark"

    def _notify(self) -> None:
        for callback in list(self._subscribers):
            try:
                callback(self)
            except Exception:
                LOGGER.debug(
                    "Abonné au thème en échec : changement de thème non transmis à cet abonné (%r)",
                    callback, exc_info=True,
                )


__all__ = [
    "COLORS",
    "ThemePalette",
    "ThemeManager",
    "THEMES",
    "SYSTEM_THEME_NAMES",
    "colors_dict",
    "global_stylesheet",
    "BLACK",
    "OVERLAY",
    "OverlayColors",
    "WHITE",
    "label_style",
    "overlay_qcolor",
    "qt_palette",
    "mix_colors",
    "set_role",
    "set_state",
    "set_style_property",
    "set_stylesheet_if_changed",
    "set_variant",
    "with_alpha",
]
