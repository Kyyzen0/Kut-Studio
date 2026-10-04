"""Système de design de Kut-Studio : les mesures, les rôles typographiques et les variantes d'états.

Ce module centralise les valeurs de design réutilisées dans toute l'application (les **couleurs** sont dans
:mod:`ui.theme`, par thème) :

- :data:`Spacing` : l'échelle d'espacement, sur la grille de 4 px ;
- :data:`Radius` : quatre rayons seulement (petit, contrôle, conteneur, boîte de dialogue) ;
- :data:`Sizes` : hauteurs / largeurs de composants ;
- :data:`Typography`, :data:`Weights` et :data:`TextRoles` : les tailles, les graisses et **sept rôles** de texte ;
- :data:`Iconography` : tailles d'icônes ;
- :data:`Motion` : durées des (rares) animations ;
- :class:`ButtonVariant` et :class:`StatusKind` : les variantes de boutons et les états (repos, travail, succès, alerte, erreur).

L'objectif est d'éviter les valeurs magiques (``12px``, ``7px``, ``9px``…) répétées dans plusieurs fichiers : tout nouveau
composant puise ses dimensions ici, et ``tests/test_design_tokens.py`` garde les échelles cohérentes.

**Échelle d'espacement.** ``4 · 8 · 12 · 16 · 24 · 32``. Un espacement qui n'est pas dans l'échelle est une exception à justifier.

**Rayons.** ``4`` (case à cocher, pastille, curseur), ``6`` (bouton, champ, carte), ``8`` (menu, infobulle, fenêtre flottante),
``12`` (boîte de dialogue). Pas de 2, 3, 5, 7, 10 ou 11 px.

**Texte.** Sept rôles, six tailles : un titre d'application, un titre de panneau, un titre de section, une étiquette, une
étiquette secondaire, un texte d'aide et une métadonnée. Les graisses : 400, 500, 600 et 700 (jamais plus).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


# ---------------------------------------------------------------------------
# Espacements
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Spacing:
    none: int = 0
    xs: int = 4
    sm: int = 8
    md: int = 12
    lg: int = 16
    xl: int = 24
    xxl: int = 32

    def scale(self) -> tuple[int, ...]:
        """Les valeurs de l'échelle, sans zéro, dans l'ordre."""
        return (self.xs, self.sm, self.md, self.lg, self.xl, self.xxl)


Spacing = _Spacing()


# ---------------------------------------------------------------------------
# Rayons de bordures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Radius:
    none: int = 0
    sm: int = 4       # case à cocher, pastille, poignée, onglet
    md: int = 6       # bouton, champ, carte, clip
    lg: int = 8       # menu, infobulle, fenêtre flottante, groupe
    xl: int = 12      # boîte de dialogue
    pill: int = 999
    # Alias conservés : d'anciens appels nommaient ces rayons ; ils retombent sur l'un des quatre.
    xs: int = 4
    xxl: int = 12

    def scale(self) -> tuple[int, ...]:
        return (self.sm, self.md, self.lg, self.xl)


Radius = _Radius()


# ---------------------------------------------------------------------------
# Hauteurs / largeurs standards
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Sizes:
    button_sm: int = 26
    button_md: int = 32
    button_lg: int = 38
    icon_button: int = 30
    icon_button_sm: int = 22
    icon_button_lg: int = 36
    input_md: int = 32
    # Champ de recherche (``ui/search_field.py``) : le même sur toutes les pages de bibliothèque.
    search_field: int = 28
    toolbar: int = 44
    # Barre supérieure compacte façon DaVinci / Final Cut : pas plus
    # de 50-56 px pour rester un repère, pas une bande.
    top_bar: int = 50
    # Rail vertical principal, volontairement icon-only. Les libellés
    # restent disponibles dans les infobulles et via l'accessibilité Qt.
    side_rail_width: int = 52
    panel_min_width: int = 240
    panel_default_width: int = 300
    # En-tête de panneau : la même hauteur partout (bibliothèque, visionneuse, inspecteur, scopes, timeline).
    panel_header: int = 32
    timeline_track_min_height: int = 56
    timeline_track_height: int = 46
    timeline_left_margin: int = 220
    timeline_ruler_height: int = 22
    timeline_header_height: int = 38
    # Doit rester ≤ ``MIN_SIZE[PanelId.TIMELINE]`` (core.workspace_state) : un panneau plus haut que le
    # minimum de sa zone déborde de son hôte (30 px rognés à 1280 × 720).
    timeline_min_height: int = 240
    # Colonne de la visionneuse : hauteur minimale utile de la visionneuse (en-tête, image, transport) et
    # plancher des scopes, qui cèdent d'abord quand la fenêtre est basse (voir ``ui.viewer_host``).
    monitor_min_height: int = 244
    scopes_min_height: int = 128
    scopes_comfort_height: int = 200
    # En-tête (recherche + puces de filtre) d'une page de bibliothèque : il défile en dessous de cette hauteur.
    library_header_min_height: int = 64
    # Barre de progression : une seule épaisseur dans toute l'application (proxys, suivi, flux optique, export, import).
    progress_height: int = 10
    # Zone cliquable minimale d'un bouton-icône (même si l'icône est plus petite) : la cible tactile et souris.
    hit_target: int = 24
    # Remplissage vertical d'un bouton : 6 px autour d'une ligne de 13 px et d'une bordure de 1 px donnent les 32 px de
    # ``button_md``. C'est une taille de contrôle, pas un espacement de mise en page : les pastilles de filtre, qui n'ont pas
    # de hauteur minimale, en dépendent.
    control_pad_y: int = 6


Sizes = _Sizes()


# ---------------------------------------------------------------------------
# Typographie
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Typography:
    """Les tailles de police (px). Six pas : ``10 · 11 · 12 · 13 · 15 · 17``, plus l'afficheur du timecode."""

    caption: int = 10
    micro: int = 11
    small: int = 12
    body: int = 13
    body_lg: int = 15
    title: int = 15
    heading: int = 17
    display: int = 22


@dataclass(frozen=True)
class _Weights:
    """Les graisses : quatre seulement. ``heavy`` est conservé comme alias de ``bold``."""

    regular: int = 400
    medium: int = 500
    semibold: int = 600
    bold: int = 700
    heavy: int = 700


Typography = _Typography()
Weights = _Weights()


@dataclass(frozen=True)
class TextRole:
    """Un rôle de texte : une taille, une graisse et le **nom du jeton de couleur** de la palette qui l'habille."""

    name: str
    size: int
    weight: int
    color: str


@dataclass(frozen=True)
class _TextRoles:
    """Les sept rôles de texte. Les titres ressortent (taille ou graisse), les métadonnées s'effacent (couleur discrète)."""

    app_title: TextRole = TextRole("app-title", Typography.heading, Weights.bold, "text_strong")
    panel_title: TextRole = TextRole("panel-title", Typography.body, Weights.semibold, "text")
    section_title: TextRole = TextRole("section-title", Typography.small, Weights.semibold, "muted_strong")
    label: TextRole = TextRole("label", Typography.body, Weights.regular, "text")
    label_secondary: TextRole = TextRole("label-secondary", Typography.small, Weights.regular, "muted")
    helper: TextRole = TextRole("helper", Typography.micro, Weights.regular, "muted")
    meta: TextRole = TextRole("meta", Typography.caption, Weights.regular, "muted")

    def all(self) -> tuple[TextRole, ...]:
        return (self.app_title, self.panel_title, self.section_title, self.label, self.label_secondary, self.helper, self.meta)


TextRoles = _TextRoles()


# ---------------------------------------------------------------------------
# Iconographie
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Iconography:
    xs: int = 12
    sm: int = 14
    md: int = 16
    lg: int = 18
    xl: int = 22
    xxl: int = 28


Iconography = _Iconography()


# ---------------------------------------------------------------------------
# Mouvement
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Motion:
    """Durées (ms) des animations, quand il y en a : discrètes, jamais permanentes, jamais sur le chemin de la lecture."""

    fast: int = 120      # survol, sélection
    normal: int = 180    # dépliage d'une section


Motion = _Motion()


# ---------------------------------------------------------------------------
# Variantes
# ---------------------------------------------------------------------------


class ButtonVariant(str, Enum):
    """La hiérarchie des boutons : quatre variantes, pas dix styles. Posée par la propriété Qt ``variant``."""

    PRIMARY = "primary"          # l'action principale de l'écran (une seule par écran)
    SECONDARY = "secondary"      # l'action normale (défaut)
    GHOST = "ghost"              # l'action légère, sans fond
    DESTRUCTIVE = "destructive"  # l'action dangereuse


class StatusKind(str, Enum):
    """Les états d'un traitement ou d'un message : même sens, mêmes couleurs partout. Posé par la propriété Qt ``state``."""

    IDLE = "idle"
    WORKING = "working"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


# ---------------------------------------------------------------------------
# Bundle unique
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DesignMetrics:
    spacing: _Spacing = Spacing
    radius: _Radius = Radius
    sizes: _Sizes = Sizes
    typography: _Typography = Typography
    weights: _Weights = Weights
    roles: _TextRoles = TextRoles
    iconography: _Iconography = Iconography
    motion: _Motion = Motion


METRICS = DesignMetrics()


def metrics() -> DesignMetrics:
    """Retourne le bundle de métriques (singleton immuable)."""
    return METRICS


__all__ = [
    "ButtonVariant",
    "Iconography",
    "METRICS",
    "Motion",
    "Radius",
    "Sizes",
    "Spacing",
    "StatusKind",
    "TextRole",
    "TextRoles",
    "Typography",
    "Weights",
    "metrics",
]
