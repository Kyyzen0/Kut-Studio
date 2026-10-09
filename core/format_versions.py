"""Versions d'un montage dans d'autres formats sociaux : une séquence par format, mise en page pour son cadre.

Un montage 9:16 se livre souvent aussi en 1:1, 4:5 ou 16:9. Exporter la même séquence dans un autre cadre la réduit
avec des bandes (``composition_size``) ; une **version** est une copie de la séquence (:func:`create_format_version`),
à son cadre, retouchable dans la timeline avant l'export (les zones de sa plateforme dans le viewer), et liée à sa
séquence d'origine (``Sequence.format_source``) pour être retrouvée, réutilisée ou refaite.

Mise en page, des règles simples et prévisibles :

1. **Plans vidéo plein cadre** (échelle ≥ 1, au centre) : cadrage « remplir », sans bandes. Une incrustation (image
   dans l'image) garde sa taille relative au cadre.
2. **Calques graphiques** : toute leur mise en page est mise à l'échelle du nouveau cadre, d'un même facteur et autour
   du centre (« contenir » : ``min(L1/L0, H1/H0)``, 56 % du 9:16 au 1:1 ou au 16:9). Espacements et proportions sont
   gardés : les lignes d'un titre ne se tassent pas l'une sur l'autre quand le cadre perd de la hauteur.
3. **Zones de la plateforme** (:data:`core.canvas_guides.PLATFORM_ZONES`) : un titre, une forme, un sticker qui
   tomberait sous l'interface (légende et boutons de TikTok…) est ramené dans la zone libre. Les calques qui se
   chevauchent bougent ensemble (un bandeau et son texte), du même décalage, animation comprise. Les calques plein cadre
   (lumière, grain, fond) et les décors qui traversent le cadre ne bougent pas.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .canvas_guides import platform_zone_rects
from .graphics import GraphicOverlay, GraphicType
from .project_model import Project, Sequence
from .social_formats import SocialFormat, social_format
from .visual_effects import TRANSFORM_PROPERTIES

FORMAT_PRESETS: dict[str, str] = {
    "vertical": "tiktok",
    "portrait": "instagram_feed_4_5",
    "square": "square",
    "landscape": "youtube",
}
"""Préréglage de rendu (:mod:`core.render_presets`) de chaque format social."""

FULL_FRAME_SHARE = 0.9
"""Un calque qui couvre au moins cette part du cadre dans les deux sens est un fond : il ne bouge pas."""

MAX_SHIFT = 0.2
"""Plus grand déplacement automatique d'un calque, sur chaque axe (part du cadre). Au-delà, c'est une autre mise en page
qu'il faudrait : le calque reste où il est, et l'utilisateur voit les zones dans le viewer."""


@dataclass(frozen=True)
class LayoutChange:
    """Ce que la mise en page a changé sur un clip de la version (résumé montré à l'utilisateur)."""

    clip_id: str
    kind: str
    """``fill`` (plan recadré pour remplir), ``fit`` (mise en page des calques mise à l'échelle du cadre), ``safe_zone``
    (calque ramené hors des zones de la plateforme ou dans le cadre)."""


def format_of(sequence: Sequence) -> SocialFormat | None:
    """Format social dont le cadre a celui de ``sequence`` (``None`` : cadre hors des formats sociaux)."""
    from .social_formats import format_for_frame

    return format_for_frame(sequence.width, sequence.height)


def find_format_version(project: Project, source_id: str, format_id: str) -> Sequence | None:
    """Version de la séquence ``source_id`` au format ``format_id`` déjà dans le projet."""
    target = social_format(format_id)
    for sequence in project.sequences:
        if sequence.format_source == source_id and (sequence.width, sequence.height) == (target.width, target.height):
            return sequence
    return None


def create_format_version(project: Project, source_id: str, format_id: str) -> tuple[Sequence, list[LayoutChange]]:
    """Ajoute au projet la version de ``source_id`` au format ``format_id`` ; une version déjà là est refaite.

    Une version refaite garde son identifiant (et donc les clips imbriqués ou exports qui la désignent) : seul son
    contenu repart de la séquence d'origine. Retourne la version et ce que la mise en page a changé.
    """
    from .sequences import duplicate_sequence, find_sequence

    source = find_sequence(project, source_id)
    target = social_format(format_id)
    if (source.width, source.height) == (target.width, target.height):
        raise ValueError(f"La séquence « {source.name} » est déjà au format {target.ratio}.")
    previous = find_format_version(project, source_id, format_id)
    version = duplicate_sequence(project, source_id, name=f"{source.name} · {target.ratio}")
    if previous is not None:
        index = project.sequences.index(previous)
        project.sequences.remove(version)
        version.id, version.name = previous.id, previous.name
        project.sequences[index] = version
        if project.active_sequence_id == previous.id:
            project.active_sequence_id = version.id
    backgrounds = _full_frame_layers(project, version)   # repérés au cadre d'origine, avant le changement
    version.width, version.height = target.width, target.height
    version.format_source = source.id
    version.guides = []                     # des repères posés pour l'autre cadre n'ont plus de sens
    changes = _fill_full_frame_clips(version)
    changes += _fit_graphics(version, source, target, backgrounds)
    changes += _place_layers(project, version, target.platforms[0] if target.platforms else "")
    return version, changes


# ---------------------------------------------------------------------------
# Règles de mise en page
# ---------------------------------------------------------------------------


def _fill_full_frame_clips(version: Sequence) -> list[LayoutChange]:
    changes = []
    for track in version.tracks:
        if track.type != "video":
            continue
        for clip in track.clips:
            transform = clip.transform
            animated_position = any(kf.property_name in ("position_x", "position_y") for kf in clip.transform_keyframes)
            full_frame = (
                min(transform.scale, transform.scale * transform.scale_x, transform.scale * transform.scale_y) >= 0.999
                and abs(transform.position_x) < 1e-3 and abs(transform.position_y) < 1e-3
                and not animated_position and not transform.rotation
            )
            if full_frame and not transform.fill:
                clip.transform = replace(transform, fill=True)
                changes.append(LayoutChange(clip.id, "fill"))
    return changes


def _full_frame_layers(project: Project, sequence: Sequence) -> frozenset[str]:
    """Calques de premier niveau qui couvrent le cadre de ``sequence`` (au moins :data:`FULL_FRAME_SHARE` dans les deux
    sens, au milieu de leur durée), et les lumières (grain, fuites, flashs : dessinées sur tout le cadre)."""
    scene = _scene_of(project, sequence)
    covering = set()
    for clip in _graphic_clips(sequence):
        graphic = clip.graphic
        if graphic.parent_id or graphic.group_id or clip.id not in scene.layers:
            continue
        if graphic.type == GraphicType.LIGHT:
            covering.add(clip.id)
            continue
        evaluated = scene.evaluate(clip.id, clip.timeline_start + clip.duration / 2.0)
        from .mograph_scene import map_box

        x0, y0, x1, y1 = map_box(evaluated.world, *evaluated.box)
        if (x1 - x0) >= FULL_FRAME_SHARE * sequence.width and (y1 - y0) >= FULL_FRAME_SHARE * sequence.height:
            covering.add(clip.id)
    return frozenset(covering)


def _scene_of(project: Project, sequence: Sequence):
    """Scène des calques de ``sequence`` (la séquence active du projet le temps de la construire)."""
    from .mograph_layers import scene_for_project

    active = project.active_sequence_id
    project.active_sequence_id = sequence.id
    try:
        return scene_for_project(project)
    finally:
        project.active_sequence_id = active


def _graphic_clips(version: Sequence):
    for track in version.tracks:
        if track.type != "graphics":
            continue
        for clip in track.clips:
            if isinstance(clip.graphic, GraphicOverlay):
                yield clip


def _fit_graphics(
    version: Sequence, source: Sequence, target: SocialFormat, backgrounds: frozenset[str] = frozenset(),
) -> list[LayoutChange]:
    """Réduit (ou agrandit) toute la mise en page des calques d'un même facteur, autour du centre, pour qu'elle tienne
    dans le nouveau cadre : le facteur « contenir » ``min(L1/L0, H1/H0)``, comme un plan en mode « adapter ».

    Les espacements et proportions entre calques sont gardés : rien ne se chevauche ni ne sort du cadre qui ne le
    faisait déjà. (Garder la taille des textes en pixels et la position en fraction du cadre tassait les lignes d'un
    titre l'une sur l'autre dès que le cadre perdait de la hauteur : 9:16 vers 1:1 ou 16:9.) Les calques enfants
    (parent, groupe) gardent leur décalage en pixels par rapport à leur parent, qui porte le facteur.

    Les fonds plein cadre (``backgrounds`` : image, lumière, grain qui couvraient le cadre d'origine) **couvrent** le
    nouveau cadre, comme un plan vidéo en mode « remplir » (``max(L1/L0, H1/H0)``) : réduits avec le reste, ils
    laissaient des bandes sur les côtés."""
    from .graphics import LayerLayout

    changes = []
    w0, h0 = float(source.width), float(source.height)
    w1, h1 = float(target.width), float(target.height)
    contain, cover = min(w1 / w0, h1 / h0), max(w1 / w0, h1 / h0)
    for clip in _graphic_clips(version):
        graphic = clip.graphic
        factor = cover if clip.id in backgrounds else contain
        if graphic.parent_id or graphic.group_id:
            # Décalage en fraction du cadre, multiplié ensuite par l'échelle du parent : on garde ses pixels.
            _rescale_position(clip, lambda x: x * w0 / w1, lambda y: y * h0 / h1)
            continue
        if graphic.layout == LayerLayout.LEGACY:
            # La position place le coin du calque depuis le coin du cadre : son centre suit le même facteur.
            _rescale_position(clip, lambda x: (factor * (x * w0 - w0 / 2.0) + w1 / 2.0) / w1,
                              lambda y: (factor * (y * h0 - h0 / 2.0) + h1 / 2.0) / h1)
        else:
            _rescale_position(clip, lambda x: x * factor * w0 / w1, lambda y: y * factor * h0 / h1)
        if abs(factor - 1.0) > 1e-6:
            _convert_transform(clip, {"scale": lambda value, factor=factor: value * factor})
            changes.append(LayoutChange(clip.id, "fit"))
    return changes


def _bounded(name: str, value: float) -> float:
    """``value`` ramenée dans les bornes de la propriété ``name`` du transform (position : ±4 cadres)."""
    return float(TRANSFORM_PROPERTIES[name].clamp(value))


def _convert_transform(clip, conversions) -> None:
    """Applique ``{propriété: fonction}`` à la valeur fixe et aux images-clés du transform du clip, dans ses bornes."""
    clip.transform = replace(clip.transform, **{
        name: _bounded(name, convert(getattr(clip.transform, name))) for name, convert in conversions.items()
    })
    clip.transform_keyframes = [
        replace(kf, value=_bounded(kf.property_name, conversions[kf.property_name](kf.value)))
        if kf.property_name in conversions else kf
        for kf in clip.transform_keyframes
    ]


def _rescale_position(clip, convert_x, convert_y) -> None:
    _convert_transform(clip, {"position_x": convert_x, "position_y": convert_y})


def _place_layers(project: Project, version: Sequence, platform: str) -> list[LayoutChange]:
    """Dégage des zones de ``platform`` (aucune : ``""``) les calques qui tomberaient sous son interface, ou hors du
    cadre.

    Un groupe de calques qui se chevauchent ne bouge que s'il perd vraiment quelque chose : une part sous une zone (la
    colonne de boutons de TikTok n'occupe que le milieu de la hauteur : un titre en haut de l'image n'est pas concerné),
    ou des lettres hors du cadre (un titre calé à gauche d'un 16:9 déborde d'un 9:16). Il prend alors le plus petit
    déplacement qui l'en dégage, sans jamais recouvrir un autre calque affiché en même temps (là où il est, déplacé ou
    non) ni faire sortir du cadre ce qui y était. Il ne bouge que si cela divise au moins par deux ce qu'il perd : un
    titre qui frôle la colonne de boutons reste à sa place plutôt que d'aller sur le logo, et jamais de plus de
    :data:`MAX_SHIFT` du cadre."""
    zones = [(x, y, x + w, y + h) for _kind, (x, y, w, h) in platform_zone_rects(1.0, 1.0, platform)] if platform else []
    boxes = _layer_boxes(project, version)
    clips = {clip.id: clip for clip in _graphic_clips(version)}
    changes = []
    # Les groupes qui perdent le plus d'abord ; chacun voit les autres **là où ils sont** (déjà déplacés ou non).
    groups = sorted(_overlapping_groups(boxes), key=lambda members: -_lost_alone(boxes, members, zones))
    for members in groups:
        start = min(boxes[m][1][0] for m in members)
        end = max(boxes[m][1][1] for m in members)
        others = [entry[0] for clip_id, entry in boxes.items()
                  if clip_id not in members and entry[1][0] < end and start < entry[1][1]]
        shift = _best_shift([(boxes[m][0], boxes[m][2]) for m in members], zones, others)
        if shift is None:
            continue
        for clip_id in members:
            _shift(clips[clip_id], *shift)
            box, span, is_text = boxes[clip_id]
            boxes[clip_id] = (_moved(box, *shift), span, is_text)
            changes.append(LayoutChange(clip_id, "safe_zone"))
    return changes


def _lost_alone(boxes, members, zones) -> float:
    """Ce qu'un groupe perd à sa place, sans compter les autres calques (ordre de traitement)."""
    total = 0.0
    for clip_id in members:
        box, _span, is_text = boxes[clip_id]
        total += _overlap_area(box, zones) + (_outside_area(box) if is_text else 0.0)
    return total


def _overlap_area(box, rects) -> float:
    return sum(
        max(0.0, min(box[2], r[2]) - max(box[0], r[0])) * max(0.0, min(box[3], r[3]) - max(box[1], r[1]))
        for r in rects
    )


def _outside_area(box) -> float:
    area = max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])
    return area - _overlap_area(box, [(0.0, 0.0, 1.0, 1.0)])


def _moved(box, dx: float, dy: float):
    return (box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy)


def _best_shift(members, zones, others=()) -> tuple[float, float] | None:
    """Plus petit déplacement ``(dx, dy)`` (fractions du cadre) d'un groupe ``[(rectangle, est un texte)]`` ; ``None`` :
    rester (rien ne divise au moins par deux ce qu'il perd).

    Ce que le groupe perd : sa part sous les ``zones``, les lettres de ses textes hors du cadre, sa part sur ``others``
    (calques affichés en même temps), et ce qu'un déplacement ferait sortir du cadre de ses autres calques."""
    union = (min(b[0] for b, _t in members), min(b[1] for b, _t in members),
             max(b[2] for b, _t in members), max(b[3] for b, _t in members))

    def lost(dx: float, dy: float) -> float:
        moved = _moved(union, dx, dy)
        cost = _overlap_area(moved, zones) + _overlap_area(moved, others)
        for box, is_text in members:
            outside = _outside_area(_moved(box, dx, dy))
            cost += outside if is_text else max(0.0, outside - _outside_area(box))
        return cost

    current = lost(0.0, 0.0)
    if current <= 1e-9:
        return None
    xs = {0.0}
    ys = {0.0}
    for rect in (*zones, *others):                        # chaque bord : s'y arrêter d'un côté ou de l'autre
        xs.update((rect[0] - union[2], rect[2] - union[0]))
        ys.update((rect[1] - union[3], rect[3] - union[1]))
    for box, is_text in members:                          # ou ramener un texte au bord du cadre
        if is_text:
            xs.update((-box[0], 1.0 - box[2]))
            ys.update((-box[1], 1.0 - box[3]))
    candidates = [(dx, dy) for dx in xs for dy in ys
                  if abs(dx) <= MAX_SHIFT + 1e-9 and abs(dy) <= MAX_SHIFT + 1e-9
                  and _overlap_area(_moved(union, dx, dy), others) <= 1e-9]  # jamais sur un autre calque
    if not candidates:
        return None
    best = min(((round(lost(dx, dy), 9), abs(dx) + abs(dy)), (dx, dy)) for dx, dy in candidates)
    if best[0][0] > current / 2.0 or best[1] == (0.0, 0.0):
        return None
    return best[1]


def _shift(clip, dx: float, dy: float) -> None:
    """Décale la position du calque de ``(dx, dy)`` (fractions du cadre), image-clé par image-clé."""
    _rescale_position(clip, lambda x: x + dx, lambda y: y + dy)


def _layer_boxes(project: Project, version: Sequence) -> dict[str, tuple[tuple[float, float, float, float], tuple, bool]]:
    """Rectangle normalisé (au milieu de sa durée), plage de temps et nature (texte ?) de chaque calque de premier niveau
    à placer.

    Les calques plein cadre (fonds, lumière, grain), les décors qui traversent le cadre d'un bord à l'autre, les
    conteneurs et les calques rattachés à un parent sont écartés : ils suivent le cadre ou leur parent."""
    scene = _scene_of(project, version)
    result = {}
    for clip in _graphic_clips(version):
        graphic = clip.graphic
        if graphic.parent_id or graphic.group_id or graphic.is_container or graphic.type == GraphicType.LIGHT:
            continue
        if clip.id not in scene.layers:
            continue
        middle = clip.timeline_start + clip.duration / 2.0
        evaluated = scene.evaluate(clip.id, middle)
        x0, y0, x1, y1 = _visible_extent(evaluated)
        box = (x0 / version.width, y0 / version.height, x1 / version.width, y1 / version.height)
        if box[2] - box[0] >= FULL_FRAME_SHARE and box[3] - box[1] >= FULL_FRAME_SHARE:
            continue                                      # fond plein cadre
        if (box[0] < 0.0 and box[2] > 1.0) or (box[1] < 0.0 and box[3] > 1.0):
            continue                                      # décor qui traverse le cadre d'un bord à l'autre
        result[clip.id] = (box, (clip.timeline_start, clip.timeline_start + clip.duration),
                           graphic.type == GraphicType.TEXT)
    return result


def _visible_extent(evaluated) -> tuple[float, float, float, float]:
    """Rectangle à l'écran de ce que le calque montre : les lettres d'un texte (sa boîte est souvent bien plus large),
    la boîte des autres calques."""
    from .mograph_scene import map_box

    graphic = evaluated.graphic
    width, height = evaluated.box
    if graphic is not None and graphic.type == GraphicType.TEXT and graphic.text.strip():
        from .mograph_raster import text_path
        from .mograph_stream import ensure_qt_gui

        ensure_qt_gui()
        path, _block = text_path(graphic, width, height)
        bounds = path.boundingRect()
        if not bounds.isEmpty():
            margin = float(graphic.stroke_width)          # le contour déborde des lettres
            x0, y0, x1, y1 = map_box(evaluated.world, bounds.width() + 2 * margin, bounds.height() + 2 * margin)
            ox, oy = bounds.x() - margin, bounds.y() - margin
            shifted = (evaluated.world[0] * ox + evaluated.world[2] * oy, evaluated.world[1] * ox + evaluated.world[3] * oy)
            return (x0 + shifted[0], y0 + shifted[1], x1 + shifted[0], y1 + shifted[1])
    return map_box(evaluated.world, width, height)


def _overlapping_groups(boxes) -> list[list[str]]:
    """Calques qui se chevauchent, à l'écran et dans le temps, regroupés (ils bougeront ensemble)."""
    ids = list(boxes)
    parent = {clip_id: clip_id for clip_id in ids}

    def root(clip_id: str) -> str:
        while parent[clip_id] != clip_id:
            parent[clip_id] = parent[parent[clip_id]]
            clip_id = parent[clip_id]
        return clip_id

    for index, first in enumerate(ids):
        a, ta = boxes[first][:2]
        for second in ids[index + 1:]:
            b, tb = boxes[second][:2]
            on_screen = a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]
            in_time = ta[0] < tb[1] and tb[0] < ta[1]
            if on_screen and in_time:
                parent[root(second)] = root(first)
    groups: dict[str, list[str]] = {}
    for clip_id in ids:
        groups.setdefault(root(clip_id), []).append(clip_id)
    return list(groups.values())


__all__ = [
    "FORMAT_PRESETS", "LayoutChange", "create_format_version", "find_format_version", "format_of",
]
