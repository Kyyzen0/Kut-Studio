"""Poignées d'un masque dans le moniteur : calcul pur, sans Qt (ADR-0006, étape 3, lot 3a).

Repères. Un masque vit dans le repère du **calque** (boîte ``box`` = ``(largeur, hauteur)`` en pixels) ; la matrice
monde ``world`` le place dans le cadre de la séquence, comme pour les fenêtres d'étalonnage. Sa boîte propre est
centrée sur ``(position_x × largeur, position_y × hauteur)``, mesure ``(width + expansion)`` × ``(height + expansion)``
fractions de la boîte du calque (comme :func:`core.mograph_raster.mask_path`) et tourne de ``rotation`` degrés.
Les coordonnées ``(u, v)`` d'un point de cette boîte vont de ``-0,5`` à ``0,5`` ; les sommets d'un polygone sont
exprimés ainsi.

Valeurs. Un glisser part des valeurs du masque **à la tête de lecture** (``MaskGeometry.values`` : les valeurs
saisies, animation comprise : aucun suivi ne déplace un masque, rien n'est donc compté deux fois) et recalcule des
valeurs **absolues** depuis la position de départ de la souris. Chaque valeur calculée est **collée explicitement**
aux bornes du registre d'animation (:data:`core.compositing.MASK_PROPERTY_SPECS`, ±2 pour les sommets) avant d'être émise :
la valeur émise est celle que la poignée affiche ensuite, et l'on atteint exactement 0 ou 1 en glissant vite. La rotation
n'est pas collée : hors ±3600, le pas est refusé (``None``). :func:`out_of_bounds` sert de garde-fou à qui écrit.

:func:`mask_edit_refusal` dit si le masque d'un clip peut être modifié (piste ou calque verrouillés, clip non rendu,
hors de sa fenêtre temporelle…) : l'interface s'en sert pour désactiver les poignées, la fenêtre principale pour
refuser l'écriture.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .compositing import MASK_PROPERTY_ORDER, MASK_PROPERTY_SPECS, Mask
from .mograph_scene import Matrix, mat_apply, mat_invert

POINT_LIMIT = 2.0
"""Bornes des sommets d'un polygone (fractions de la boîte du masque) : celles que le modèle impose à ``Mask.points``."""

CORNERS: dict[str, tuple[float, float]] = {
    "tl": (-0.5, -0.5), "tr": (0.5, -0.5), "br": (0.5, 0.5), "bl": (-0.5, 0.5),
}
SIDES: dict[str, tuple[float, float]] = {
    "t": (0.0, -0.5), "r": (0.5, 0.0), "b": (0.0, 0.5), "l": (-0.5, 0.0),
}
VISUAL_KINDS = ("video", "graphics")
_EPS = 1e-9


@dataclass(frozen=True)
class MaskGeometry:
    """Le masque édité dans le moniteur, avec ce qu'il faut pour le dessiner et le manipuler."""

    clip_id: str
    mask_id: str
    world: Matrix
    box: tuple[float, float]
    shape: str                       # "rectangle" | "ellipse" | "polygon"
    mode: str                        # "add" | "subtract" | "intersect"
    inverted: bool
    values: dict                     # MASK_PROPERTY_ORDER → valeur RENDUE à la tête de lecture (suivi compris) : le dessin
    points: tuple = ()
    editable: bool = True
    base: dict | None = None         # valeurs SAISIES (statique + courbes de l'utilisateur, sans le suivi) : le glisser en part

    @property
    def entered(self) -> dict:
        """Valeurs saisies ; sans ``base`` (aucun suivi), ce sont les valeurs montrées."""
        return self.values if self.base is None else self.base


@dataclass(frozen=True)
class MaskEdit:
    """Résultat d'un pas de glisser : propriétés animables modifiées, sommets (polygone), tour cumulé (rotation)."""

    values: dict
    points: tuple | None = None
    turn: float = 0.0


def geometry_of(clip_id: str, mask: Mask, world: Matrix, box: tuple[float, float], *, editable: bool = True,
                entered: Mask | None = None) -> MaskGeometry:
    """Géométrie d'un masque (déjà évalué à la tête de lecture, suivi compris).

    ``entered`` : le même masque évalué **sans** la dérivation du suivi (:func:`entered_mask`) ; le glisser en part, pour que
    le suivi ne s'ajoute pas une seconde fois à ce qu'il écrit.
    """
    return MaskGeometry(
        clip_id=clip_id, mask_id=mask.id, world=world, box=(float(box[0]), float(box[1])), shape=mask.shape.value,
        mode=mask.mode.value, inverted=bool(mask.inverted), values={name: float(getattr(mask, name)) for name in MASK_PROPERTY_ORDER},
        points=tuple(mask.points), editable=editable,
        base=None if entered is None else {name: float(getattr(entered, name)) for name in MASK_PROPERTY_ORDER},
    )


def entered_mask(clip, mask_id: str, local_time: float) -> Mask | None:
    """Le masque du clip à ``local_time`` d'après ce que l'utilisateur a saisi : ``clip.compositing`` et ``clip.animation`` bruts.

    Ni liaison de suivi ni stabilisation (:func:`core.tracking_bindings.effective_clip_state`) : c'est la valeur que
    ``_apply_property_values`` réécrit, donc celle d'où part un glisser.
    """
    from .animation_targets import animation_curves
    from .compositing import evaluate_mask_at

    mask = clip.compositing.mask_by_id(mask_id)
    if mask is None:
        return None
    curves = animation_curves(clip, prefix=f"mask.{mask_id}.")
    return evaluate_mask_at(mask, curves, max(0.0, min(float(clip.duration), float(local_time))))


# -- repères -------------------------------------------------------------------------------------


def mask_frame(geometry: MaskGeometry, values: dict | None = None) -> tuple[tuple[float, float], float, float, float]:
    """``(centre, demi-largeur, demi-hauteur, angle en radians)`` du masque dans le repère du calque."""
    v = values or geometry.values
    w, h = geometry.box
    expansion = v.get("expansion", 0.0)
    half_w = max(0.0, (v.get("width", 0.5) + expansion) * w) / 2.0
    half_h = max(0.0, (v.get("height", 0.5) + expansion) * h) / 2.0
    return (v.get("position_x", 0.5) * w, v.get("position_y", 0.5) * h), half_w, half_h, math.radians(v.get("rotation", 0.0))


def layer_point(geometry: MaskGeometry, u: float, v: float) -> tuple[float, float]:
    """Point ``(u, v)`` de la boîte du masque (-0,5..0,5), dans le repère du calque."""
    (cx, cy), half_w, half_h, angle = mask_frame(geometry)
    x, y = u * 2.0 * half_w, v * 2.0 * half_h
    cos, sin = math.cos(angle), math.sin(angle)
    return cx + x * cos - y * sin, cy + x * sin + y * cos


def canvas_point(geometry: MaskGeometry, u: float, v: float) -> tuple[float, float]:
    """Point ``(u, v)`` de la boîte du masque, dans le cadre de la séquence (pixels)."""
    return mat_apply(geometry.world, *layer_point(geometry, u, v))


def offset_point(geometry: MaskGeometry, x: float, y: float) -> tuple[float, float]:
    """Point à ``(x, y)`` pixels du calque du centre du masque, dans les axes **du masque** (tourné), dans le cadre."""
    (cx, cy), _half_w, _half_h, angle = mask_frame(geometry)
    cos, sin = math.cos(angle), math.sin(angle)
    return mat_apply(geometry.world, cx + x * cos - y * sin, cy + x * sin + y * cos)


def outline_units(geometry: MaskGeometry, grow: tuple[float, float] = (0.0, 0.0), steps: int = 48) -> list[tuple[float, float]]:
    """Contour du masque en coordonnées ``(u, v)`` ; ``grow`` : agrandi de ces parts en largeur et en hauteur."""
    kx, ky = 1.0 + grow[0], 1.0 + grow[1]
    if geometry.shape == "ellipse":
        return [(0.5 * kx * math.cos(2 * math.pi * i / steps), 0.5 * ky * math.sin(2 * math.pi * i / steps)) for i in range(steps)]
    if geometry.shape == "polygon" and len(geometry.points) >= 3:
        return [(x * kx, y * ky) for x, y in geometry.points]
    return [(-0.5 * kx, -0.5 * ky), (0.5 * kx, -0.5 * ky), (0.5 * kx, 0.5 * ky), (-0.5 * kx, 0.5 * ky)]


def handle_units(geometry: MaskGeometry) -> dict[str, tuple[float, float]]:
    """Poignées de forme, en ``(u, v)`` : coins, côtés (pas pour un polygone : ses sommets les couvrent), sommets ``p<i>``.

    La poignée de rotation et celle d'adoucissement sont à distance fixe **à l'écran** : l'interface les place.
    """
    handles = dict(CORNERS)
    if geometry.shape == "polygon":
        for index, point in enumerate(geometry.points):
            handles[f"p{index}"] = (point[0], point[1])
    else:
        handles.update(SIDES)
    return handles


def feather_distance(geometry: MaskGeometry) -> float:
    """Étendue de l'adoucissement, en pixels du calque (rayon de flou du rendu : ``feather × min(largeur, hauteur)``)."""
    return geometry.values.get("feather", 0.0) * min(geometry.box)


def feather_growth(geometry: MaskGeometry) -> tuple[float, float]:
    """Parts dont on agrandit le contour (largeur, hauteur) pour montrer l'étendue de l'adoucissement : ``d / demi-côté``."""
    _centre, half_w, half_h, _angle = mask_frame(geometry)
    distance = feather_distance(geometry)
    return (distance / half_w if half_w > _EPS else 0.0, distance / half_h if half_h > _EPS else 0.0)


# -- bornes ----------------------------------------------------------------------------------------


def out_of_bounds(values: dict, points: tuple | None = None) -> list[str]:
    """Noms des valeurs hors des bornes du modèle (propriétés animables, sommets)."""
    refused = []
    for name, value in values.items():
        spec = MASK_PROPERTY_SPECS[name]
        if value != value or (spec.minimum is not None and value < spec.minimum) or (spec.maximum is not None and value > spec.maximum):
            refused.append(name)
    for x, y in points or ():
        if not (-POINT_LIMIT <= x <= POINT_LIMIT and -POINT_LIMIT <= y <= POINT_LIMIT):
            refused.append("points")
            break
    return refused


def _changed(geometry: MaskGeometry, values: dict) -> dict:
    return {name: value for name, value in values.items() if abs(value - geometry.entered.get(name, 0.0)) > _EPS}


def _pin(name: str, value: float) -> float:
    """``value`` ramené EXPLICITEMENT aux bornes du modèle (la rotation n'est pas collée : elle est refusée hors bornes)."""
    spec = MASK_PROPERTY_SPECS[name]
    if name == "rotation" or value != value:
        return value
    low = spec.minimum if spec.minimum is not None else value
    high = spec.maximum if spec.maximum is not None else value
    return max(low, min(high, value))


def _finish(geometry: MaskGeometry, values: dict, points: tuple | None = None, turn: float = 0.0) -> MaskEdit | None:
    """Un pas de glisser : valeurs collées aux bornes (valeur émise = valeur affichée), puis comparées à l'existant."""
    values = _changed(geometry, {name: _pin(name, value) for name, value in values.items()})
    if points is not None:
        points = tuple((max(-POINT_LIMIT, min(POINT_LIMIT, x)), max(-POINT_LIMIT, min(POINT_LIMIT, y))) for x, y in points)
        if points == tuple(map(tuple, geometry.points)):
            points = None
    if not values and points is None:
        return None
    if out_of_bounds(values, points):                  # reste : rotation hors ±3600, valeur non numérique
        return None
    return MaskEdit(values, points, turn)


# -- glisser -----------------------------------------------------------------------------------------


def unwrap_turn(raw_degrees: float, previous_turn: float) -> float:
    """``raw_degrees`` ramené à la valeur congrue à 360° la plus proche de ``previous_turn`` (rotation continue)."""
    return raw_degrees + 360.0 * round((previous_turn - raw_degrees) / 360.0)


def drag(geometry: MaskGeometry, mode: str, start: tuple[float, float], current: tuple[float, float],
         *, shift: bool = False, previous_turn: float = 0.0) -> MaskEdit | None:
    """Valeurs du masque après un glisser de ``start`` à ``current`` (points du cadre, pixels).

    ``mode`` : ``move``, ``rotate``, ``feather``, un coin (``tl``…), un côté (``t``…) ou un sommet (``p<i>``). ``shift`` :
    déplacement sur un seul axe, tailles de coin proportionnelles, rotation par 15°. Valeurs collées à leurs bornes ;
    ``None`` : rien à changer, ou rotation hors ±3600.
    """
    w, h = geometry.box
    if w <= _EPS or h <= _EPS:
        return None
    try:
        inverse = mat_invert(geometry.world)
    except ValueError:
        return None
    lx, ly = mat_apply(inverse, *current)
    sx, sy = mat_apply(inverse, *start)
    v = geometry.values                  # rendu (suivi compris) : le repère du pointeur
    b = geometry.entered                 # saisi : la valeur de départ de ce qu'on écrit
    (cx, cy), half_w, half_h, angle = mask_frame(geometry)

    if mode == "move":
        dx, dy = lx - sx, ly - sy
        if shift:
            dx, dy = (dx, 0.0) if abs(dx) > abs(dy) else (0.0, dy)
        return _finish(geometry, {"position_x": b["position_x"] + dx / w, "position_y": b["position_y"] + dy / h})
    if mode == "rotate":
        raw = math.degrees(math.atan2(ly - cy, lx - cx) - math.atan2(sy - cy, sx - cx))
        raw = (raw + 180.0) % 360.0 - 180.0
        turn = unwrap_turn(raw, previous_turn)
        rotation = v["rotation"] + turn
        if shift:
            rotation = round(rotation / 15.0) * 15.0
        return _finish(geometry, {"rotation": b["rotation"] + rotation - v["rotation"]}, turn=turn)

    cos, sin = math.cos(-angle), math.sin(-angle)
    qx, qy = (lx - cx) * cos - (ly - cy) * sin, (lx - cx) * sin + (ly - cy) * cos
    if mode == "feather":
        sqx = (sx - cx) * cos - (sy - cy) * sin
        return _finish(geometry, {"feather": b["feather"] + (qx - sqx) / min(w, h)})
    if mode.startswith("p") and mode[1:].isdigit():
        index = int(mode[1:])
        points = list(geometry.points)
        if geometry.shape != "polygon" or index >= len(points) or half_w <= _EPS or half_h <= _EPS:
            return None
        points[index] = (qx / (2.0 * half_w), qy / (2.0 * half_h))
        return _finish(geometry, {}, tuple(points))
    if mode in CORNERS or mode in SIDES:
        return _resize(geometry, mode, 2.0 * abs(qx), 2.0 * abs(qy), shift)
    return None


def _resize(geometry: MaskGeometry, mode: str, total_w: float, total_h: float, shift: bool) -> MaskEdit | None:
    """Taille voulue (pixels du calque, expansion comprise) → ``width`` / ``height``, autour du centre du masque."""
    w, h = geometry.box
    v = geometry.values
    expansion = v["expansion"]
    start_w, start_h = max(0.0, (v["width"] + expansion) * w), max(0.0, (v["height"] + expansion) * h)
    if mode in ("l", "r"):
        total_h = start_h
    elif mode in ("t", "b"):
        total_w = start_w
    elif shift and start_w > 1e-6 and start_h > 1e-6:
        ratio = max(total_w / start_w, total_h / start_h)
        # Une dimension collée à sa borne ne déforme pas la forme : le même facteur borne l'autre.
        lows, highs = [], []
        for start, size, name in ((start_w, w, "width"), (start_h, h, "height")):
            spec = MASK_PROPERTY_SPECS[name]
            lows.append((spec.minimum + expansion) * size / start)
            highs.append((spec.maximum + expansion) * size / start)
        ratio = max(max(lows), min(min(highs), ratio))
        total_w, total_h = start_w * ratio, start_h * ratio
    # Le suivi peut avoir mis le masque à l'échelle (k) : la taille rendue vaut (saisie + expansion) × k ; on reporte sur la saisie.
    values = {}
    for name, total, size in (("width", total_w, w), ("height", total_h, h)):
        shown, entered = v[name] + expansion, geometry.entered[name] + expansion
        k = shown / entered if entered > 1e-6 and shown > 1e-6 else 1.0
        values[name] = (total / size) / k - expansion
    return _finish(geometry, values)


# -- droits d'édition ----------------------------------------------------------------------------------


def mask_edit_refusal(project, clip_id: str, t: float, mask_id: str = "", *, scene=None) -> str | None:
    """Pourquoi le masque du clip ne peut pas être modifié à ``t`` (``None`` : il le peut).

    Raisons : ``missing`` (clip ou masque introuvable), ``kind`` (piste ni vidéo ni graphique), ``nested`` (séquence
    imbriquée : pas dans la scène du moniteur), ``locked`` (piste ou calque verrouillés), ``hidden`` (clip non rendu :
    piste masquée ou hors solo, clip désactivé, calque masqué), ``outside`` (hors de la fenêtre temporelle du clip).
    Mêmes règles que :func:`core.mograph_layers.viewer_pick_boxes`, sauf le verrou : un clip verrouillé reste affiché.
    """
    from .mograph_layers import LayerError, _require_editable, scene_for_project
    from .timeline_operations import find_clip

    try:
        clip = find_clip(project, clip_id)
    except KeyError:
        return "missing"
    track = next((item for item in project.tracks if any(c.id == clip_id for c in item.clips)), None)
    if track is None:
        return "missing"
    if track.type not in VISUAL_KINDS:
        return "kind"
    if clip.sequence_id:
        return "nested"
    if mask_id and clip.compositing.mask_by_id(mask_id) is None:
        return "missing"
    if not (clip.timeline_start - _EPS <= t < clip.timeline_start + clip.duration - _EPS):
        return "outside"
    if track.locked:
        return "locked"
    if track.type == "graphics":
        try:
            _require_editable(project, clip_id)
        except LayerError:
            return "locked"
    if track.type == "video":
        video_solo = {item.id for item in project.tracks if item.type == "video" and item.solo}
        if not track.visible or not clip.enabled or (video_solo and track.id not in video_solo):
            return "hidden"
    scene = scene or scene_for_project(project)
    if clip_id not in scene.layers:
        return "missing"
    if not scene.evaluate(clip_id, t).active:
        return "hidden"
    return None


__all__ = [
    "CORNERS", "MaskEdit", "MaskGeometry", "POINT_LIMIT", "SIDES", "VISUAL_KINDS", "canvas_point", "drag",
    "feather_distance", "feather_growth", "geometry_of", "handle_units", "layer_point", "mask_edit_refusal",
    "mask_frame", "offset_point", "outline_units", "out_of_bounds", "unwrap_turn",
]
