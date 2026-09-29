"""Système de design minimal de Kut-Studio.

Ce module centralise les valeurs de design réutilisées dans toute
l'application :

- :data:`Spacing` : valeurs d'espacement cohérentes ;
- :data:`Radius` : rayons de bordures ;
- :data:`Sizes` : hauteurs / largeurs de composants ;
- :data:`Typography` : tailles et poids typographiques ;
- :data:`Iconography` : tailles d'icônes ;
- :class:`DesignMetrics` : bundle immuable prêt à être consommé par
  les widgets.

L'objectif est d'éviter les valeurs magiques (``12px``, ``7px``,
``9px``...) répétées dans plusieurs fichiers. Tout nouveau composant
devrait puiser ses dimensions ici.
"""

from __future__ import annotations

from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Espacements
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Spacing:
    none: int = 0
    xs: int = 4
    sm: int = 6
    md: int = 10
    lg: int = 14
    xl: int = 20
    xxl: int = 28


Spacing = _Spacing()


# ---------------------------------------------------------------------------
# Rayons de bordures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Radius:
    none: int = 0
    xs: int = 3
    sm: int = 4
    md: int = 6
    lg: int = 8
    xl: int = 10
    xxl: int = 12
    pill: int = 999


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
    toolbar: int = 44
    # Barre supérieure compacte façon DaVinci / Final Cut : pas plus
    # de 50-56 px pour rester un repère, pas une bande.
    top_bar: int = 50
    # Rail vertical principal. La largeur inclut un libellé court à côté
    # de chaque icône pour que les sections restent identifiables.
    side_rail_width: int = 112
    panel_min_width: int = 240
    panel_default_width: int = 300
    timeline_track_min_height: int = 56
    timeline_track_height: int = 68
    timeline_left_margin: int = 248
    timeline_ruler_height: int = 30
    timeline_header_height: int = 44
    timeline_min_height: int = 320


Sizes = _Sizes()


# ---------------------------------------------------------------------------
# Typographie
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Typography:
    caption: int = 10
    micro: int = 11
    small: int = 12
    body: int = 13
    body_lg: int = 14
    title: int = 15
    heading: int = 17
    display: int = 22


@dataclass(frozen=True)
class _Weights:
    regular: int = 400
    medium: int = 500
    semibold: int = 600
    bold: int = 700
    heavy: int = 800


Typography = _Typography()
Weights = _Weights()


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
# Bundle unique
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DesignMetrics:
    spacing: _Spacing = Spacing
    radius: _Radius = Radius
    sizes: _Sizes = Sizes
    typography: _Typography = Typography
    weights: _Weights = Weights
    iconography: _Iconography = Iconography


METRICS = DesignMetrics()


def metrics() -> DesignMetrics:
    """Retourne le bundle de métriques (singleton immuable)."""
    return METRICS


__all__ = [
    "Iconography",
    "METRICS",
    "Radius",
    "Sizes",
    "Spacing",
    "Typography",
    "Weights",
    "metrics",
]
