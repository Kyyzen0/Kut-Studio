"""Rastériseur motion graphics (Qt, déterministe).

Dessine une pile de calques d'une :class:`~core.mograph_scene.GraphicsScene`
dans une image RGBA. **Le même code** produit :

- les flux de calques injectés dans le graphe FFmpeg de l'export et des
  segments d'aperçu fidèles (:mod:`core.mograph_stream`) ;
- l'aperçu interactif du viewer (manipulation directe).

Ordre de rendu d'un calque (voir ``docs/motion-graphics.md``) ::

    contenu (texte / forme / image, en espace calque)
    → transform du monde (parents, groupe, ancrage, échelle, inclinaison…)
    → masques (espace calque, contour adouci, opérations)
    → flou de mouvement (moyenne d'échantillons)
    → opacité + mode de fusion dans le groupe / la bande

Un groupe est **isolé** : ses membres sont composés dans un tampon, puis
masqués et fusionnés comme un seul calque. Les effets et les adjustment
layers ne passent pas ici : FFmpeg les applique au flux du calque
(:mod:`core.mograph_program`).

Les polices sont dimensionnées en **pixels** (corps × 96/72, l'échelle
historique des titres) : le rendu ne dépend pas du DPI de l'écran.
"""

from __future__ import annotations

import logging
import math
import os
from collections import OrderedDict
from collections.abc import Iterable, Sequence

from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QFontMetricsF,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
    QTransform,
)

from .blend_modes import BlendMode, coerce_blend_mode, qt_composition_mode
from .bundled_fonts import register_bundled_fonts
from .compositing import MaskMode, MaskShape
from .graphics import GraphicOverlay, GraphicType, ShapeKind
from .mograph_scene import (
    EvaluatedLayer,
    GraphicsScene,
    Matrix,
    map_box,
    mat_close,
    mat_mul,
    mat_scale,
    mat_scale_factor,
    mat_translate,
)
from .motion_blur import MotionBlurSettings

LOGGER = logging.getLogger("kut_studio.fonts")

RASTER_VERSION = 1
"""Version du dessin des calques, incluse dans le nom de chaque image du cache (:mod:`core.mograph_stream`) et dans
l'empreinte des segments d'aperçu (:func:`core.filter_graph.fingerprint_plan`).

À incrémenter dès que le **même état** de calque se dessine autrement (ordre contour / remplissage, nouvelle mise en page du
texte…) : le nom d'une image ne dépend que de l'état évalué, et le cache resservirait sinon les images de l'ancien dessin,
même après une hausse de ``RENDER_ENGINE_VERSION``. Les nouveaux champs à valeur par défaut n'en ont pas besoin : leur
valeur entre déjà dans l'état.

1 : première version numérotée (contour fusionné des polices à contours superposés)."""

POINT_TO_PIXEL = 96.0 / 72.0
"""Le corps d'un titre est en points à 96 ppp (échelle des titres historiques)."""

_FORMAT = QImage.Format_ARGB32_Premultiplied


# ---------------------------------------------------------------------------
# Couleurs, polices
# ---------------------------------------------------------------------------


def qcolor(value: str) -> QColor:
    """``#RRGGBB`` ou ``#RRGGBBAA`` → ``QColor``."""
    text = str(value or "#FFFFFF")
    if text.startswith("#") and len(text) == 9:
        return QColor(int(text[1:3], 16), int(text[3:5], 16), int(text[5:7], 16), int(text[7:9], 16))
    color = QColor(text)
    return color if color.isValid() else QColor(255, 255, 255)


_FAMILIES: dict[str, str] = {}


def resolve_family(family: str) -> str:
    """Famille installée (sinon la police système), mémorisée.

    Demander à Qt une famille absente reconstruit ses alias (centaines de ms) ;
    on ne le fait donc qu'une fois par nom.
    """
    cached = _FAMILIES.get(family)
    if cached is not None:
        return cached
    register_bundled_fonts()
    installed = set(QFontDatabase.families())
    if family in installed:
        resolved = family
    else:
        system = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family()
        fallbacks = (system, *_FALLBACK_FAMILIES)
        resolved = next((name for name in fallbacks if name in installed), None)
        if resolved is None:
            resolved = sorted(installed)[0] if installed else system
        if family not in _GENERIC_FAMILIES:
            # Le projet s'affichera autrement que sur la machine qui l'a créé : on le dit au journal, une fois par nom.
            LOGGER.warning("Police « %s » absente : remplacée par « %s »", family, resolved)
    _FAMILIES[family] = resolved
    return resolved


_GENERIC_FAMILIES = frozenset({"", "Sans Serif", "Serif", "Monospace"})
"""Noms génériques (le défaut des calques texte) : leur remplacement par la police système est voulu, pas un oubli."""

_FALLBACK_FAMILIES = (
    "Helvetica Neue", "Helvetica", "Arial", "Segoe UI", "DejaVu Sans", "Liberation Sans", "Noto Sans",
)


def make_font(graphic: GraphicOverlay) -> QFont:
    font = QFont(resolve_family(graphic.font_family))
    font.setPixelSize(max(1, int(round(graphic.font_size * POINT_TO_PIXEL))))
    font.setBold(graphic.bold)
    font.setItalic(graphic.italic)
    if graphic.tracking:
        font.setLetterSpacing(QFont.AbsoluteSpacing, float(graphic.tracking))
    return font


# ---------------------------------------------------------------------------
# Mise en page du texte
# ---------------------------------------------------------------------------


def _wrap(paragraph: str, metrics: QFontMetricsF, width: float) -> list[str]:
    words = paragraph.split(" ")
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if current and metrics.horizontalAdvance(candidate) > width:
            lines.append(current)
            current = word
        else:
            current = candidate
    lines.append(current)
    return lines


def text_lines(graphic: GraphicOverlay, box_width: float | None) -> tuple[list[str], QFontMetricsF, QFont]:
    font = make_font(graphic)
    metrics = QFontMetricsF(font)
    lines: list[str] = []
    for paragraph in graphic.text.split("\n"):
        if graphic.box_text and box_width is not None and not graphic.autosize:
            lines.extend(_wrap(paragraph, metrics, max(1.0, box_width)))
        else:
            lines.append(paragraph)
    return lines, metrics, font


def _line_height(graphic: GraphicOverlay, metrics: QFontMetricsF) -> float:
    return metrics.lineSpacing() * graphic.line_spacing


def measure_text(graphic: GraphicOverlay) -> tuple[float, float]:
    """Boîte d'un texte à taille automatique (bloc + marge de fond)."""
    lines, metrics, _font = text_lines(graphic, None)
    width = max((metrics.horizontalAdvance(line) for line in lines), default=0.0)
    height = (len(lines) - 1) * _line_height(graphic, metrics) + metrics.height()
    pad = 2.0 * graphic.background_padding if graphic.background_enabled else 0.0
    return (max(2.0, math.ceil(width + pad)), max(2.0, math.ceil(height + pad)))


def text_path(graphic: GraphicOverlay, width: float, height: float) -> tuple[QPainterPath, QRectF]:
    """Contours du texte en espace calque et rectangle du bloc de texte."""
    lines, metrics, font = text_lines(graphic, width)
    line_height = _line_height(graphic, metrics)
    block = (len(lines) - 1) * line_height + metrics.height()
    pad = float(graphic.background_padding) if (graphic.autosize and graphic.background_enabled) else 0.0
    if graphic.align_v == "top":
        top = pad
    elif graphic.align_v == "bottom":
        top = height - block - pad
    else:
        top = (height - block) / 2.0
    path = QPainterPath()
    # Les polices variables (Inter…) superposent leurs contours (barre du « t » sur sa hampe) :
    # en remplissage pair-impair le recouvrement s'annule et laisse un trou dans la lettre.
    path.setFillRule(Qt.WindingFill)
    left_most = width
    right_most = 0.0
    for index, line in enumerate(lines):
        advance = metrics.horizontalAdvance(line)
        if graphic.align_h == "left":
            x = pad
        elif graphic.align_h == "right":
            x = width - advance - pad
        else:
            x = (width - advance) / 2.0
        left_most = min(left_most, x)
        right_most = max(right_most, x + advance)
        if line:
            path.addText(x, top + index * line_height + metrics.ascent(), font, line)
    if right_most < left_most:
        left_most = right_most = width / 2.0
    return path, QRectF(left_most, top, right_most - left_most, block)


# ---------------------------------------------------------------------------
# Formes et masques
# ---------------------------------------------------------------------------


def shape_path(graphic: GraphicOverlay, width: float, height: float) -> QPainterPath:
    path = QPainterPath()
    kind = graphic.shape if graphic.type == GraphicType.SHAPE else ShapeKind.RECTANGLE
    if kind == ShapeKind.ELLIPSE:
        path.addEllipse(QRectF(0, 0, width, height))
    elif kind == ShapeKind.LINE:
        path.moveTo(0.0, height / 2.0)
        path.lineTo(width, height / 2.0)
    elif kind == ShapeKind.POLYGON:
        sides = max(3, int(graphic.polygon_sides))
        polygon = QPolygonF()
        for i in range(sides):
            angle = -math.pi / 2.0 + 2.0 * math.pi * i / sides
            polygon.append(QPointF(width / 2.0 * (1 + math.cos(angle)), height / 2.0 * (1 + math.sin(angle))))
        path.addPolygon(polygon)
        path.closeSubpath()
    elif kind == ShapeKind.ROUNDED_RECTANGLE or graphic.corner_radius > 0:
        radius = min(float(graphic.corner_radius), width / 2.0, height / 2.0)
        path.addRoundedRect(QRectF(0, 0, width, height), radius, radius)
    else:
        path.addRect(QRectF(0, 0, width, height))
    return path


def mask_path(mask, width: float, height: float) -> QPainterPath:
    """Contour d'un masque en espace calque (boîte ``width × height``)."""
    w = max(0.0, (mask.width + mask.expansion) * width)
    h = max(0.0, (mask.height + mask.expansion) * height)
    path = QPainterPath()
    if mask.shape == MaskShape.ELLIPSE:
        path.addEllipse(QRectF(-w / 2.0, -h / 2.0, w, h))
    elif mask.shape == MaskShape.POLYGON:
        polygon = QPolygonF([QPointF(x * w, y * h) for x, y in mask.points])
        path.addPolygon(polygon)
        path.closeSubpath()
    else:
        path.addRect(QRectF(-w / 2.0, -h / 2.0, w, h))
    transform = QTransform()
    transform.translate(mask.position_x * width, mask.position_y * height)
    transform.rotate(mask.rotation)
    return transform.map(path)


def blur_image(image: QImage, radius: float) -> QImage:
    """Flou approché (réduction lissée puis agrandissement), déterministe."""
    if radius < 0.75 or image.isNull():
        return image
    factor = max(1.0, radius / 1.5)
    small_w = max(1, int(round(image.width() / factor)))
    small_h = max(1, int(round(image.height() / factor)))
    small = image.scaled(small_w, small_h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    return small.scaled(image.width(), image.height(), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)


def _qtransform(m: Matrix, dx: float = 0.0, dy: float = 0.0) -> QTransform:
    a, b, c, d, e, f = m
    return QTransform(a, b, c, d, e - dx, f - dy)


def render_matte(
    masks: Sequence, box: tuple[float, float], device: Matrix, rect: QRect
) -> QImage | None:
    """Couverture combinée des masques (alpha) dans le rectangle ``rect`` du cadre.

    ``add`` : somme bornée ; ``subtract`` : retire ; ``intersect`` : garde
    l'intersection. Le premier masque part d'un calque vide s'il est ``add``,
    plein sinon. Contour adouci, inversion et opacité sont appliqués par masque.
    """
    if not masks:
        return None
    width, height = box
    scale = mat_scale_factor(device)
    accumulated = QImage(rect.width(), rect.height(), _FORMAT)
    accumulated.fill(Qt.transparent if masks[0].mode == MaskMode.ADD else QColor(255, 255, 255))
    for mask in masks:
        shape = QImage(rect.width(), rect.height(), _FORMAT)
        shape.fill(Qt.transparent)
        painter = QPainter(shape)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setTransform(_qtransform(device, rect.x(), rect.y()))
        painter.fillPath(mask_path(mask, width, height), QColor(255, 255, 255))
        painter.end()
        if mask.feather > 0:
            shape = blur_image(shape, mask.feather * min(width, height) * scale)
        if mask.inverted:
            inverted = QImage(rect.width(), rect.height(), _FORMAT)
            inverted.fill(QColor(255, 255, 255))
            painter = QPainter(inverted)
            painter.setCompositionMode(QPainter.CompositionMode_DestinationOut)
            painter.drawImage(0, 0, shape)
            painter.end()
            shape = inverted
        painter = QPainter(accumulated)
        painter.setOpacity(mask.opacity)
        painter.setCompositionMode({
            MaskMode.ADD: QPainter.CompositionMode_Plus,
            MaskMode.SUBTRACT: QPainter.CompositionMode_DestinationOut,
            MaskMode.INTERSECT: QPainter.CompositionMode_DestinationIn,
        }[mask.mode])
        if mask.mode == MaskMode.INTERSECT and mask.opacity < 1.0:
            # Opacité d'une intersection : (1 − o) + o·m (une zone hors masque
            # n'est atténuée que de l'opacité).
            painter.setOpacity(1.0)
            blended = QImage(rect.width(), rect.height(), _FORMAT)
            blended.fill(QColor(255, 255, 255, int(round(255 * (1.0 - mask.opacity)))))
            inner = QPainter(blended)
            inner.setOpacity(mask.opacity)
            inner.drawImage(0, 0, shape)
            inner.end()
            shape = blended
        painter.drawImage(0, 0, shape)
        painter.end()
    return accumulated


# ---------------------------------------------------------------------------
# Contenu d'un calque
# ---------------------------------------------------------------------------


class _ImageCache:
    def __init__(self, size: int = 32) -> None:
        self._items: OrderedDict[tuple, QImage] = OrderedDict()
        self._size = size

    def get(self, path: str) -> QImage | None:
        try:
            stat = os.stat(path)
        except OSError:
            return None
        key = (path, stat.st_mtime_ns, stat.st_size)
        image = self._items.get(key)
        if image is None:
            image = QImage(path)
            if image.isNull():
                return None
            image = image.convertToFormat(_FORMAT)
            self._items[key] = image
            if len(self._items) > self._size:
                self._items.popitem(last=False)
        else:
            self._items.move_to_end(key)
        return image


IMAGES = _ImageCache()


def image_signature(path: str) -> str:
    try:
        stat = os.stat(path)
    except OSError:
        return "missing"
    return f"{stat.st_mtime_ns}:{stat.st_size}"


def content_margin(graphic: GraphicOverlay) -> float:
    """Débordement possible du contenu hors de sa boîte (contour, ombre, fond)."""
    margin = float(graphic.stroke_width) * 2.0 + 2.0
    if graphic.type == GraphicType.TEXT:
        margin += abs(graphic.shadow_offset_x) + abs(graphic.shadow_offset_y) + 3.0 * graphic.shadow_blur
        if graphic.background_enabled:
            margin += graphic.background_padding
        margin += graphic.font_size  # jambages et italiques hors boîte
    return margin


def needs_isolation(graphic: GraphicOverlay) -> bool:
    """Le contenu exige-t-il un tampon intermédiaire (ombre floue) ?"""
    return graphic.type == GraphicType.TEXT and graphic.shadow_blur > 0 and _has_shadow(graphic)


def _has_shadow(graphic: GraphicOverlay) -> bool:
    return (
        qcolor(graphic.shadow_color).alpha() > 0
        and (graphic.shadow_offset_x or graphic.shadow_offset_y or graphic.shadow_blur > 0)
    )


def draw_content(painter: QPainter, graphic: GraphicOverlay, box: tuple[float, float], *, device_scale: float = 1.0) -> None:
    """Dessine le contenu du calque en espace calque (transform déjà posée)."""
    width, height = box
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.TextAntialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
    kind = graphic.type
    if kind == GraphicType.SOLID:
        painter.fillRect(QRectF(0, 0, width, height), qcolor(graphic.fill_color))
    elif kind == GraphicType.IMAGE:
        image = IMAGES.get(graphic.source_path)
        if image is not None:
            painter.drawImage(QRectF(0, 0, width, height), image)
    elif kind == GraphicType.RECTANGLE:
        # Rectangle historique : contour **intérieur** (comme ``drawbox``).
        painter.fillRect(QRectF(0, 0, width, height), qcolor(graphic.fill_color))
        if graphic.stroke_width > 0:
            stroke = float(graphic.stroke_width)
            pen = QPen(qcolor(graphic.stroke_color), stroke)
            pen.setJoinStyle(Qt.MiterJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(QRectF(stroke / 2, stroke / 2, width - stroke, height - stroke))
    elif kind == GraphicType.SHAPE:
        path = shape_path(graphic, width, height)
        if graphic.fill_enabled and graphic.shape != ShapeKind.LINE:
            painter.fillPath(path, qcolor(graphic.fill_color))
        stroke = float(graphic.stroke_width)
        if graphic.shape == ShapeKind.LINE and stroke <= 0:
            stroke = 2.0
        if stroke > 0:
            color = graphic.stroke_color if (graphic.stroke_width > 0 or not graphic.fill_enabled) else graphic.fill_color
            pen = QPen(qcolor(color), stroke, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            painter.strokePath(path, pen)
    elif kind == GraphicType.TEXT:
        _draw_text(painter, graphic, width, height, device_scale=device_scale)


def _draw_text(painter: QPainter, graphic: GraphicOverlay, width: float, height: float, *, device_scale: float) -> None:
    path, block = text_path(graphic, width, height)
    if graphic.background_enabled:
        pad = float(graphic.background_padding)
        rect = block.adjusted(-pad, -pad, pad, pad)
        radius = min(graphic.background_radius, rect.width() / 2.0, rect.height() / 2.0)
        background = QPainterPath()
        background.addRoundedRect(rect, radius, radius)
        painter.fillPath(background, qcolor(graphic.background_color))
    if _has_shadow(graphic):
        shadow = path.translated(graphic.shadow_offset_x, graphic.shadow_offset_y)
        if graphic.shadow_blur > 0:
            _draw_blurred_path(painter, shadow, qcolor(graphic.shadow_color), graphic.shadow_blur * device_scale)
        else:
            painter.fillPath(shadow, qcolor(graphic.shadow_color))
    painter.fillPath(path, qcolor(graphic.fill_color))
    if graphic.stroke_width > 0:
        pen = QPen(qcolor(graphic.stroke_color), max(1, int(graphic.stroke_width) * 2),
                   Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        painter.strokePath(_merged_outline(path, _largest_axis_scale(painter.transform())), pen)


# ``simplified()`` aplatit les courbes avec une tolérance absolue (en unités du chemin) : on agrandit
# le chemin avant la fusion puis on le ramène, ce qui divise l'écart de polyligne par ce facteur.
# Mesuré sur Inter et Helvetica Neue : à ×1 le bord dévie jusqu'à ~30 % de couverture d'un pixel
# ('a', 'ñ', 's') ; dès ×2 il tombe sous le bruit du rasteriseur (×4 ou ×16 n'apportent rien) pour
# la moitié du coût de ×4. Mais le tracé est ensuite agrandi par la transformation du calque : l'écart
# l'est autant (×16 : 401 et 530 pixels de contour faussés sur 'S' et 'a'), d'où un facteur qui suit
# cet agrandissement (``_OUTLINE_MERGE_SCALE`` par unité d'écran).
_OUTLINE_MERGE_SCALE = 2.0
# QPathClipper plafonne le nombre de segments par courbe (mesuré sur 38 caractères : 25 400 éléments à
# ×16, 27 000 à ×32, 27 190 à ×64 comme à ×128, ~10 ms) : au-delà de ×32 le contour ne change plus, monter
# plus haut ne gagnerait rien.
_OUTLINE_MERGE_MAX_SCALE = 32.0


def _largest_axis_scale(transform: QTransform) -> float:
    """Plus grand agrandissement que ``transform`` applique à un vecteur (valeur singulière maximale).

    ``mat_scale_factor`` (racine du déterminant) moyenne les axes : un calque étiré ×10 en largeur et
    ×0,1 en hauteur y vaut ×1 alors que son contour y est agrandi ×10.
    """
    a, b, c, d = transform.m11(), transform.m12(), transform.m21(), transform.m22()
    total = a * a + b * b + c * c + d * d
    det = a * d - b * c
    return math.sqrt((total + math.sqrt(max(0.0, total * total - 4.0 * det * det))) / 2.0)


def _merged_outline(path: QPainterPath, scale: float = 1.0) -> QPainterPath:
    """Contour extérieur de ``path`` sans les arêtes internes de ses contours superposés.

    Un ``QPen`` trace chaque sous-contour séparément, même quand la règle d'enroulement les fusionne
    au remplissage : la barre du « t » ou du « f » d'une police variable (Inter…) laisse alors un
    liseré de la couleur du contour dans la lettre. ``simplified()`` fusionne les sous-chemins qui se
    recouvrent selon la règle du chemin (``text_path`` impose l'enroulement) ; seul le tracé du
    contour en profite, le remplissage et l'ombre gardent les courbes d'origine.

    ``scale`` est l'agrandissement que le tracé subira ensuite à l'écran (voir ``_largest_axis_scale``) ;
    en dessous de 1 on garde la précision de ×1, validée.
    """
    factor = min(_OUTLINE_MERGE_SCALE * max(1.0, scale), _OUTLINE_MERGE_MAX_SCALE)
    up = QTransform.fromScale(factor, factor)
    down = QTransform.fromScale(1.0 / factor, 1.0 / factor)
    merged = down.map(up.map(path).simplified())
    return merged if not merged.isEmpty() else path


def _draw_blurred_path(painter: QPainter, path: QPainterPath, color: QColor, radius: float) -> None:
    """Remplit ``path`` flouté de ``radius`` pixels (repère du périphérique)."""
    transform = painter.transform()
    device_path = transform.map(path)
    bounds = device_path.boundingRect().adjusted(-3 * radius - 2, -3 * radius - 2, 3 * radius + 2, 3 * radius + 2)
    rect = bounds.toAlignedRect()
    if rect.width() <= 0 or rect.height() <= 0 or rect.width() * rect.height() > 64_000_000:
        return
    buffer = QImage(rect.width(), rect.height(), _FORMAT)
    buffer.fill(Qt.transparent)
    inner = QPainter(buffer)
    inner.setRenderHint(QPainter.Antialiasing, True)
    inner.translate(-rect.x(), -rect.y())
    inner.fillPath(device_path, color)
    inner.end()
    buffer = blur_image(buffer, radius)
    painter.save()
    painter.resetTransform()
    painter.drawImage(rect.topLeft(), buffer)
    painter.restore()


# ---------------------------------------------------------------------------
# Rendu d'une pile
# ---------------------------------------------------------------------------


class MographRenderer:
    """Rend des calques d'une scène à la résolution de sortie.

    Args:
        scene: hiérarchie évaluable.
        width / height: taille de l'image produite (l'échelle par rapport au
            cadre de la séquence est appliquée à la racine).
        fps: cadence (intervalle d'obturation du flou de mouvement).
        quality: ``draft`` / ``standard`` / ``high`` / ``export``.
        motion_blur: réglages de la séquence (``None`` = désactivé).
    """

    def __init__(
        self,
        scene: GraphicsScene,
        width: int,
        height: int,
        *,
        fps: float = 30.0,
        quality: str = "export",
        motion_blur: MotionBlurSettings | None = None,
    ) -> None:
        self.scene = scene
        self.width = max(1, int(width))
        self.height = max(1, int(height))
        self.fps = float(fps) if fps else 30.0
        self.quality = quality
        self.motion_blur = motion_blur
        self.root: Matrix = mat_scale(self.width / scene.width, self.height / scene.height)
        self._offsets = (
            motion_blur.offsets(self.fps, quality) if motion_blur is not None else (0.0,)
        )

    # -- état (cache de frames) ---------------------------------------------------------------

    def _sample_worlds(self, clip_id: str, t: float) -> list[Matrix]:
        evaluated = self.scene.evaluate(clip_id, t)
        graphic = evaluated.graphic
        if (
            len(self._offsets) <= 1
            or not isinstance(graphic, GraphicOverlay)
            or not graphic.motion_blur
        ):
            return [evaluated.world]
        worlds = [self.scene.world_matrix(clip_id, t + offset) for offset in self._offsets]
        if all(mat_close(worlds[0], other, 1e-7) for other in worlds[1:]):
            return [evaluated.world]
        return worlds

    def layer_key(self, clip_id: str, t: float) -> tuple:
        """Clé de tout ce qui change l'apparence de ``clip_id`` à ``t``."""
        evaluated = self.scene.evaluate(clip_id, t)
        if not evaluated.active:
            return (clip_id, "inactive")
        key = [evaluated.state_key()]
        graphic = evaluated.graphic
        if isinstance(graphic, GraphicOverlay):
            if graphic.type == GraphicType.IMAGE:
                key.append(image_signature(graphic.source_path))
            if graphic.type == GraphicType.GROUP:
                key.append(repr(getattr(self.scene.layers[clip_id], "compositing", None)))
                key.extend(self.layer_key(member, t) for member in self.scene.members(clip_id))
            blend = getattr(getattr(self.scene.layers[clip_id], "compositing", None), "blend_mode", "normal")
            key.append(str(getattr(blend, "value", blend)))
        worlds = self._sample_worlds(clip_id, t)
        if len(worlds) > 1:
            key.append(tuple(tuple(round(v, 5) for v in m) for m in worlds))
        return tuple(key)

    def frame_key(self, layer_ids: Iterable[str], t: float) -> tuple:
        return (self.width, self.height, self.quality) + tuple(
            self.layer_key(clip_id, t) for clip_id in layer_ids
        )

    def any_active(self, layer_ids: Iterable[str], t: float) -> bool:
        return any(self.scene.evaluate(clip_id, t).active for clip_id in layer_ids)

    # -- rendu -------------------------------------------------------------------------------

    def render(self, layer_ids: Iterable[str], t: float, *, target: QImage | None = None,
               blend_modes: bool = True) -> QImage:
        """Image des calques ``layer_ids`` (du bas vers le haut) au temps ``t``.

        ``blend_modes`` : appliquer le mode de fusion de chaque calque (faux
        quand l'appelant fusionne lui-même le résultat, ex. FFmpeg).
        """
        image = target
        if image is None:
            image = QImage(self.width, self.height, _FORMAT)
            image.fill(Qt.transparent)
        painter = QPainter(image)
        try:
            for clip_id in layer_ids:
                self._draw_layer(painter, clip_id, t, apply_blend=blend_modes)
        finally:
            painter.end()
        return image

    def _blend_of(self, clip_id: str) -> BlendMode:
        compositing = getattr(self.scene.layers[clip_id], "compositing", None)
        return coerce_blend_mode(getattr(compositing, "blend_mode", "normal"))

    def _draw_layer(self, painter: QPainter, clip_id: str, t: float, *, apply_blend: bool = True) -> None:
        evaluated = self.scene.evaluate(clip_id, t)
        graphic = evaluated.graphic
        if not evaluated.active or not isinstance(graphic, GraphicOverlay) or evaluated.opacity <= 0.0:
            return
        blend = self._blend_of(clip_id) if apply_blend else BlendMode.NORMAL
        if graphic.type == GraphicType.GROUP:
            self._draw_group(painter, evaluated, t, blend)
            return
        if graphic.is_container:
            return  # contrôleur / adjustment layer : rien à dessiner ici
        worlds = [mat_mul(self.root, world) for world in self._sample_worlds(clip_id, t)]
        isolated = (
            len(worlds) > 1 or evaluated.masks or blend is not BlendMode.NORMAL or needs_isolation(graphic)
        )
        if not isolated:
            painter.save()
            painter.setTransform(_qtransform(worlds[0]))
            painter.setOpacity(evaluated.opacity)
            draw_content(painter, graphic, evaluated.box, device_scale=mat_scale_factor(worlds[0]))
            painter.restore()
            return
        rect = self._device_rect(graphic, evaluated.box, worlds)
        if rect is None:
            return
        if len(worlds) == 1:
            buffer = self._render_isolated(graphic, evaluated, worlds[0], rect)
        else:
            buffer = QImage(rect.width(), rect.height(), QImage.Format_RGBA64_Premultiplied)
            buffer.fill(Qt.transparent)
            for index, world in enumerate(worlds, start=1):
                sample = self._render_isolated(graphic, evaluated, world, rect)
                accumulate = QPainter(buffer)
                # Moyenne courante : lerp(acc, échantillon, 1/k) (mode Source + opacité).
                accumulate.setCompositionMode(QPainter.CompositionMode_Source)
                accumulate.setOpacity(1.0 / index)
                accumulate.drawImage(0, 0, sample)
                accumulate.end()
            buffer = buffer.convertToFormat(_FORMAT)
        painter.save()
        painter.resetTransform()
        painter.setOpacity(evaluated.opacity)
        painter.setCompositionMode(qt_composition_mode(blend))
        painter.drawImage(rect.topLeft(), buffer)
        painter.restore()

    def _device_rect(self, graphic: GraphicOverlay, box, worlds: list[Matrix]) -> QRect | None:
        margin = content_margin(graphic)
        x0 = y0 = math.inf
        x1 = y1 = -math.inf
        for world in worlds:
            expanded = mat_mul(world, mat_translate(-margin, -margin))
            bx0, by0, bx1, by1 = map_box(expanded, box[0] + 2 * margin, box[1] + 2 * margin)
            x0, y0, x1, y1 = min(x0, bx0), min(y0, by0), max(x1, bx1), max(y1, by1)
        left = max(0, int(math.floor(x0)))
        top = max(0, int(math.floor(y0)))
        right = min(self.width, int(math.ceil(x1)))
        bottom = min(self.height, int(math.ceil(y1)))
        if right <= left or bottom <= top:
            return None
        return QRect(left, top, right - left, bottom - top)

    def _render_isolated(self, graphic: GraphicOverlay, evaluated: EvaluatedLayer, world: Matrix, rect: QRect) -> QImage:
        buffer = QImage(rect.width(), rect.height(), _FORMAT)
        buffer.fill(Qt.transparent)
        painter = QPainter(buffer)
        painter.setTransform(_qtransform(world, rect.x(), rect.y()))
        draw_content(painter, graphic, evaluated.box, device_scale=mat_scale_factor(world))
        painter.end()
        matte = render_matte(evaluated.masks, evaluated.box, world, rect)
        if matte is not None:
            painter = QPainter(buffer)
            painter.setCompositionMode(QPainter.CompositionMode_DestinationIn)
            painter.drawImage(0, 0, matte)
            painter.end()
        return buffer

    # -- adjustment layers ---------------------------------------------------------------------

    def coverage_key(self, clip_id: str, t: float):
        """État de la couverture d'un adjustment layer (``None`` = inactif)."""
        evaluated = self.scene.evaluate(clip_id, t)
        if not evaluated.active or evaluated.opacity <= 0.0:
            return None
        return ("coverage", evaluated.state_key())

    def render_coverage(self, clip_id: str, t: float) -> QImage:
        """Zone d'action d'un adjustment layer : sa boîte transformée, masquée, × opacité."""
        image = QImage(self.width, self.height, _FORMAT)
        image.fill(Qt.transparent)
        evaluated = self.scene.evaluate(clip_id, t)
        if not evaluated.active or not isinstance(evaluated.graphic, GraphicOverlay):
            return image
        from dataclasses import replace as _replace

        white = _replace(evaluated.graphic, type=GraphicType.SOLID, fill_color="#FFFFFF")
        world = mat_mul(self.root, evaluated.world)
        rect = self._device_rect(white, evaluated.box, [world])
        if rect is None:
            return image
        buffer = self._render_isolated(white, evaluated, world, rect)
        painter = QPainter(image)
        painter.setOpacity(evaluated.opacity)
        painter.drawImage(rect.topLeft(), buffer)
        painter.end()
        return image

    def _draw_group(self, painter: QPainter, evaluated: EvaluatedLayer, t: float, blend: BlendMode) -> None:
        buffer = QImage(self.width, self.height, _FORMAT)
        buffer.fill(Qt.transparent)
        inner = QPainter(buffer)
        try:
            for member in self.scene.members(evaluated.clip_id):
                self._draw_layer(inner, member, t)
        finally:
            inner.end()
        if evaluated.masks:
            world = mat_mul(self.root, evaluated.world)
            matte = render_matte(evaluated.masks, evaluated.box, world, QRect(0, 0, self.width, self.height))
            if matte is not None:
                inner = QPainter(buffer)
                inner.setCompositionMode(QPainter.CompositionMode_DestinationIn)
                inner.drawImage(0, 0, matte)
                inner.end()
        painter.save()
        painter.resetTransform()
        painter.setOpacity(evaluated.opacity)
        painter.setCompositionMode(qt_composition_mode(blend))
        painter.drawImage(0, 0, buffer)
        painter.restore()


def render_layer_matte(
    scene: GraphicsScene, clip_id: str, t: float, width: int, height: int
) -> QImage:
    """Matte (blanc + alpha) des masques d'un clip vidéo, en espace calque.

    Un clip vidéo est un calque de la taille du cadre : la matte est rendue
    à la résolution du flux, sans transform (FFmpeg l'applique **avant**
    échelle et rotation, comme les masques d'un calque graphique).
    """
    evaluated = scene.evaluate(clip_id, t)
    world = mat_scale(width / scene.width, height / scene.height)
    matte = render_matte(evaluated.masks, (scene.width, scene.height), world, QRect(0, 0, width, height))
    if matte is None:
        matte = QImage(width, height, _FORMAT)
        matte.fill(QColor(255, 255, 255))
    return matte


def scene_for_plan(plan, *, measure: bool = True) -> GraphicsScene:
    """Scène des calques d'un plan de rendu (mesure du texte par Qt)."""
    return GraphicsScene(
        getattr(plan, "graphics_layers", ()), plan.width, plan.height,
        measure=measure_text if measure else None,
    )


__all__ = [
    "IMAGES", "MographRenderer", "POINT_TO_PIXEL", "blur_image", "content_margin", "draw_content",
    "image_signature", "make_font", "mask_path", "measure_text", "qcolor", "render_layer_matte",
    "render_matte", "resolve_family", "scene_for_plan", "shape_path", "text_path",
]
