"""Contours des caractères d'une police (Qt) : le SEUL module du texte 3D qui touche à Qt.

Déterminisme (ce qui dépend de la machine, et ce qui n'en dépend pas) :

- les familles ``BUNDLED_FAMILIES`` sont lues DANS LES FICHIERS livrés (``assets/fonts``, licence SIL OFL) avec ``QRawFont``,
  sans hinting : mêmes contours sur toutes les machines, même en `offscreen` sans aucune police système (CI Windows), aux
  arrondis près de la bibliothèque de polices de la plateforme (CoreText, FreeType : ~1e-5 em, jamais une forme différente) ;
- toute autre famille vient des polices INSTALLÉES de la machine : le même ``.kut3d`` donne un texte différent (ou est refusé si
  la famille manque) ailleurs. Une famille absente ou substituée par Qt est REFUSÉE, jamais remplacée en silence ;
- le crénage et les ligatures ne sont pas appliqués : chaque caractère avance de sa largeur nominale ;
- les courbes sont aplaties par un algorithme à nous (pas celui de Qt), à tolérance décroissante jusqu'à tenir dans
  ``MAX_GLYPH_POINTS`` points par caractère.

Qt exige une `QGuiApplication` pour ouvrir une police (sans elle, `QRawFont` plante le processus) : on réutilise celle de
l'hôte, sinon on en crée une (fil principal seulement), sans toucher à l'environnement.
"""

from __future__ import annotations

import math
import sys
import threading
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from kut3d.mathutil import Array
from kut3d.text_rules import MAX_GLYPH_POINTS, code, is_blank
from kut3d.triangulate import clean_ring, cross_properly, signed_area

EM_PIXELS = 256.0
_TOLERANCES = (
    0.002,
    0.004,
    0.008,
    0.016,
    0.032,
)  # écart maximal à la courbe, en em, essayé dans cet ordre
_DECIMATIONS = (
    0.0012,
    0.0025,
    0.005,
    0.01,
    0.02,
    0.04,
)  # polices à contours qui se recouvrent : écart maximal admis, em
_MIN_MERGED_AREA = 1.5e-3  # em^2 : en dessous, un contour issu de la fusion est une rognure invisible (interstice entre deux contours)
_MAX_SEGMENTS_PER_CURVE = 48
_BUNDLED = {
    "Anton": "Anton-Regular.ttf",
    "Saira ExtraCondensed": "SairaExtraCondensed-Regular.ttf",
    "Saira ExtraCondensed SemiBold": "SairaExtraCondensed-SemiBold.ttf",
    "Saira ExtraCondensed Bold": "SairaExtraCondensed-Bold.ttf",
    "Saira ExtraCondensed Black": "SairaExtraCondensed-Black.ttf",
}
BUNDLED_FAMILIES = tuple(_BUNDLED)
_LOCK = threading.RLock()
_FACES: dict[str, Any] = {}


def fonts_dir() -> Path:
    """``assets/fonts`` du dépôt, ou celui de l'application construite (PyInstaller : ``sys._MEIPASS``)."""
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle and Path(bundle, "assets", "fonts").is_dir():
        return Path(bundle, "assets", "fonts")
    return Path(__file__).resolve().parent.parent / "assets" / "fonts"


def _require_application() -> None:
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtGui import QGuiApplication

    instance = QCoreApplication.instance()
    if instance is None:
        if threading.current_thread() is not threading.main_thread():
            raise ValueError(
                "texte 3D : aucune application Qt n'existe et le fil courant n'est pas le fil principal "
                "(Qt ne sait pas ouvrir une police sans application)."
            )
        from kut3d.gpu.qtapp import ensure_application

        ensure_application(require_gpu=False)
    elif not isinstance(instance, QGuiApplication):
        raise ValueError(
            "texte 3D : l'application Qt existante n'a pas de module graphique (QCoreApplication) : les polices sont inutilisables."
        )


def _face(family: str) -> Any:
    """Le `QRawFont` de la famille (mémorisé) ; `ValueError` si elle est introuvable."""
    with _LOCK:
        cached = _FACES.get(family)
        if cached is not None:
            return cached
        _require_application()
        from PySide6.QtGui import QFont, QFontDatabase, QRawFont

        hinting = QFont.HintingPreference.PreferNoHinting
        if family in _BUNDLED:
            path = fonts_dir() / _BUNDLED[family]
            if not path.is_file():
                raise ValueError(f"police livrée introuvable : {_BUNDLED[family]}.")
            raw = QRawFont(str(path), EM_PIXELS, hinting)
        else:
            if family not in set(QFontDatabase.families()):
                raise ValueError(
                    f"police « {family[:40]} » introuvable sur cette machine (livrées avec l'application : "
                    f"{', '.join(BUNDLED_FAMILIES)})."
                )
            font = QFont(family)
            font.setPixelSize(int(EM_PIXELS))
            font.setHintingPreference(hinting)
            raw = QRawFont.fromFont(font)
            if raw.familyName() != family:
                raise ValueError(
                    f"police « {family[:40]} » remplacée par Qt : refusée (livrées avec l'application : "
                    f"{', '.join(BUNDLED_FAMILIES)})."
                )
        if not raw.isValid():
            raise ValueError(f"police « {family[:40]} » illisible.")
        _FACES[family] = raw
        return raw


def _flatten(path: Any, tolerance: float) -> list[Array]:
    """Contours (em, Y vers le haut) de `path`, courbes de Bézier cubiques aplaties à `tolerance` près."""
    from PySide6.QtGui import QPainterPath

    element_type = QPainterPath.ElementType
    rings: list[list[tuple[float, float]]] = []
    cur: list[tuple[float, float]] = []
    count = path.elementCount()
    i = 0

    def pt(k: int) -> tuple[float, float]:
        e = path.elementAt(k)
        return (e.x / EM_PIXELS, -e.y / EM_PIXELS)

    while i < count:
        e = path.elementAt(i)
        kind = e.type
        if kind == element_type.MoveToElement:
            if cur:
                rings.append(cur)
            cur = [pt(i)]
            i += 1
        elif kind == element_type.LineToElement:
            cur.append(pt(i))
            i += 1
        else:  # CurveToElement + 2 CurveToDataElement : cubique
            if i + 2 >= count or not cur:
                raise ValueError("contour de caractère illisible.")
            p0, p1, p2, p3 = cur[-1], pt(i), pt(i + 1), pt(i + 2)
            second = max(
                math.hypot(p0[0] - 2 * p1[0] + p2[0], p0[1] - 2 * p1[1] + p2[1]),
                math.hypot(p1[0] - 2 * p2[0] + p3[0], p1[1] - 2 * p2[1] + p3[1]),
            )
            n = max(
                2,
                min(
                    _MAX_SEGMENTS_PER_CURVE,
                    math.ceil(math.sqrt(0.75 * second / tolerance)),
                ),
            )
            for k in range(1, n + 1):
                t = k / n
                u = 1.0 - t
                cur.append(
                    (
                        u**3 * p0[0]
                        + 3 * u * u * t * p1[0]
                        + 3 * u * t * t * p2[0]
                        + t**3 * p3[0],
                        u**3 * p0[1]
                        + 3 * u * u * t * p1[1]
                        + 3 * u * t * t * p2[1]
                        + t**3 * p3[1],
                    )
                )
            i += 3
    if cur:
        rings.append(cur)
    out: list[Array] = []
    for ring in rings:
        arr = np.array(ring, dtype=np.float64)
        if len(arr) > 1 and np.array_equal(arr[0], arr[-1]):
            arr = arr[:-1]
        cleaned = clean_ring(arr)
        if cleaned is not None:
            out.append(cleaned)
    return out


def _simplify(points: Array, tolerance: float) -> Array:
    """Douglas-Peucker d'un contour fermé (coupé en deux au point le plus éloigné du premier)."""
    n = len(points)
    if n < 8:
        return points
    far = int(np.argmax(np.hypot(*(points - points[0]).T)))
    keep = np.zeros(n, dtype=bool)
    keep[[0, far]] = True
    stack = [(0, far), (far, n)]
    closed = np.vstack([points, points[:1]])
    while stack:
        lo, hi = stack.pop()
        if hi - lo < 2:
            continue
        a, b = closed[lo], closed[hi]
        mid = closed[lo + 1 : hi]
        chord = b - a
        length = math.hypot(*chord)
        if length > 0:
            dist = (
                np.abs(chord[0] * (mid[:, 1] - a[1]) - chord[1] * (mid[:, 0] - a[0]))
                / length
            )
        else:
            dist = np.hypot(mid[:, 0] - a[0], mid[:, 1] - a[1])
        worst = int(np.argmax(dist))
        if dist[worst] > tolerance:
            at = lo + 1 + worst
            keep[at] = True
            stack += [(lo, at), (at, hi)]
    return points[keep]


def _merged(path: Any, tolerance: float) -> list[Array]:
    """Contours de `path.simplified()` (Qt fusionne les contours qui se recouvrent), décimés à `tolerance` em près."""
    flat = path.simplified()
    rings: list[list[tuple[float, float]]] = []
    for i in range(flat.elementCount()):
        e = flat.elementAt(i)
        point = (e.x / EM_PIXELS, -e.y / EM_PIXELS)
        if e.isMoveTo():
            rings.append([point])
        elif rings:
            rings[-1].append(point)
    out: list[Array] = []
    for ring in rings:
        arr = np.array(ring, dtype=np.float64)
        if len(arr) > 1 and np.array_equal(arr[0], arr[-1]):
            arr = arr[:-1]
        cleaned = clean_ring(_simplify(arr, tolerance))
        if cleaned is not None and abs(signed_area(cleaned)) >= _MIN_MERGED_AREA:
            out.append(cleaned)
    return out


def _outline(path: Any, family: str, char: str) -> list[Array]:
    for tolerance in _TOLERANCES:
        rings = _flatten(path, tolerance)
        if sum(len(r) for r in rings) <= MAX_GLYPH_POINTS:
            break
    else:
        rings = []
    if rings and not cross_properly(rings, rings):
        return rings
    # contours qui se recouvrent (polices dérivées de polices variables) ou trop détaillés : on laisse Qt les fusionner
    for tolerance in _DECIMATIONS:
        merged = _merged(path, tolerance)
        if sum(len(r) for r in merged) <= MAX_GLYPH_POINTS:
            if cross_properly(merged, merged):
                break
            return merged
    raise ValueError(
        f"le caractère {code(char)} de « {family[:40]} » est trop détaillé "
        f"(plus de {MAX_GLYPH_POINTS} points de contour) ou ses contours se coupent."
    )


@dataclass(frozen=True)
class Glyph:
    rings: tuple[
        Array, ...
    ]  # contours propres, em, baseline y = 0, Y vers le haut (vide pour une espace)
    advance: float  # em


@lru_cache(maxsize=512)
def glyph(family: str, char: str) -> Glyph:
    """Le caractère `char` (un seul) de la police : contours et avance. `ValueError` si la police n'a pas ce glyphe ou si
    son contour dépasse `MAX_GLYPH_POINTS` même à la tolérance la plus lâche."""
    if char == " ":
        char = " "
    with _LOCK:
        raw = _face(family)
        index = raw.glyphIndexesForString(char)
        if len(index) != 1 or index[0] == 0:
            raise ValueError(
                f"la police « {family[:40]} » n'a pas de glyphe pour {code(char)}."
            )
        advance = float(raw.advancesForGlyphIndexes(index)[0].x()) / EM_PIXELS
        if is_blank(char):
            return Glyph((), advance)
        rings = _outline(raw.pathForGlyph(index[0]), family, char)
    if not rings:
        raise ValueError(
            f"la police « {family[:40]} » n'a aucun contour pour {code(char)}."
        )
    return Glyph(tuple(rings), advance)


@dataclass(frozen=True)
class Metrics:
    pitch: float  # distance entre deux lignes, em
    cap_height: float  # hauteur des capitales (« H »), em


@lru_cache(maxsize=32)
def metrics(family: str) -> Metrics:
    with _LOCK:
        raw = _face(family)
        pitch = (
            float(raw.ascent()) + float(raw.descent()) + float(raw.leading())
        ) / EM_PIXELS
    try:
        top = max(float(r[:, 1].max()) for r in glyph(family, "H").rings)
    except ValueError:
        top = 0.7  # police sans « H » (cyrillique seul...) : capitale conventionnelle
    return Metrics(
        pitch=pitch if pitch > 0 else 1.2, cap_height=top if top > 0 else 0.7
    )
