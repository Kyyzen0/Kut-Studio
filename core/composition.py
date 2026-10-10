"""Composition nodale : un clip dont l'image est calculée par un graphe de nœuds (ADR-0002, étape 4).

Comme un clip Fusion dans DaVinci Resolve, un clip de composition (``Clip.composition``) n'a pas de média : son image
sort d'un :class:`CompositionGraph`. Le graphe s'appuie sur le socle commun (:mod:`core.node_graph` : immuable, toujours
valide, ordre de calcul stable). Ses nœuds :

- **sources** (aucune entrée) : :class:`MediaNode` (un média du projet, une plage de sa source posée à un instant de
  la composition), :class:`GraphicNode` (un calque graphique : texte, forme, image, avec son transform et son
  animation), :class:`SolidNode` (une couleur unie) ;
- **traitements** (une entrée) : :class:`TransformNode` (position, échelle, rotation, ancrage, miroirs, opacité,
  animables : le transform d'un clip), :class:`MaskNode` (masques, ceux des clips), :class:`KeyNode` (incrustation),
  :class:`EffectsNode` (effets visuels), :class:`GradeNode` (étalonnage) ;
- :class:`MergeNode` : le premier plan (entrée 1) sur le fond (entrée 0), avec un mode de fusion et une opacité ;
- :class:`OutputNode` : l'image du clip (une seule par graphe).

Chaque image du graphe a **la taille du cadre** de la séquence, en RVBA, sur toute la durée de la composition :
transparente là où rien n'est posé. Le temps est celui de la composition (0 à :attr:`Composition.duration`) ; le clip
en montre une plage (``source_in`` / ``source_out``), comme un clip de séquence imbriquée. Un nœud que rien ne relie
à la sortie est permis (on construit un graphe pas à pas) et ne coûte rien au rendu.

Rendu : :mod:`core.composition_render` (graphe FFmpeg exact de l'export, donc de l'aperçu fidèle).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from typing import Any

from .blend_modes import BlendMode, coerce_blend_mode
from .color_grading import ColorGrade
from .compositing import ChromaKey, Mask, mask_from_dict, mask_to_dict
from .node_graph import NodeGraph, NodeGraphError, NodeLink
from .visual_effects import ClipTransform

LOGGER = logging.getLogger(__name__)

OUTPUT_ID = "output"
MAX_LABEL_LENGTH = 32
BACKGROUND_PORT = 0
FOREGROUND_PORT = 1


def _label(value: object) -> str:
    return str(value or "").strip()[:MAX_LABEL_LENGTH]


def _time(value: object, default: float = 0.0) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(0.0, number) if number == number else default


# --- Nœuds -----------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class MediaNode:
    """Un média du projet : sa source de ``source_in`` à ``source_out``, posée à l'instant ``start`` de la
    composition, adaptée au cadre comme un clip : bandes transparentes, ou cadrage « remplir » (``fill``, ``pan_x``,
    ``pan_y``, comme le transform avancé d'un clip). S'il est relié à la sortie, son son (``gain_db``, ``muted``) passe
    dans celui du clip de composition, tel quel : les traitements de l'image ne le touchent pas. Un média détaché
    ne sonne pas, comme il ne se voit pas."""

    id: str
    asset_id: str = ""
    start: float = 0.0
    source_in: float = 0.0
    source_out: float = 0.0
    label: str = ""
    gain_db: float = 0.0
    muted: bool = False
    fill: bool = False
    pan_x: float = 0.0
    pan_y: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "fill", bool(self.fill))
        for name in ("pan_x", "pan_y"):
            try:
                value = min(1.0, max(-1.0, float(getattr(self, name))))
            except (TypeError, ValueError):
                value = 0.0
            object.__setattr__(self, name, value if value == value else 0.0)
        object.__setattr__(self, "start", _time(self.start))
        object.__setattr__(self, "source_in", _time(self.source_in))
        object.__setattr__(self, "source_out", max(_time(self.source_out), self.source_in))
        object.__setattr__(self, "label", _label(self.label))
        try:
            gain = float(self.gain_db)
        except (TypeError, ValueError):
            gain = 0.0
        object.__setattr__(self, "gain_db", max(-60.0, min(24.0, gain)) if gain == gain else 0.0)
        object.__setattr__(self, "muted", bool(self.muted))

    @property
    def duration(self) -> float:
        return self.source_out - self.source_in


@dataclass(frozen=True)
class GraphicNode:
    """Un calque graphique (texte, forme, image) de ``start`` pendant ``duration``, avec son transform, ses masques (dans
    sa boîte, comme sur la timeline) et son animation (temps du calque : 0 à son début), rastérisé comme sur la
    timeline."""

    id: str
    graphic: Any = None
    start: float = 0.0
    duration: float = 0.0
    transform: ClipTransform = field(default_factory=ClipTransform)
    transform_keyframes: tuple = ()
    animation: tuple = ()
    label: str = ""
    masks: tuple[Mask, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "masks", tuple(self.masks or ()))
        object.__setattr__(self, "start", _time(self.start))
        object.__setattr__(self, "duration", _time(self.duration))
        object.__setattr__(self, "transform_keyframes", tuple(self.transform_keyframes or ()))
        object.__setattr__(self, "animation", tuple(self.animation or ()))
        object.__setattr__(self, "label", _label(self.label))


@dataclass(frozen=True)
class SolidNode:
    """Une couleur unie, opaque, sur tout le cadre et toute la composition."""

    id: str
    color: str = "#000000"
    label: str = ""

    def __post_init__(self) -> None:
        color = str(self.color or "").upper()
        if len(color) != 7 or not color.startswith("#") or any(c not in "0123456789ABCDEF" for c in color[1:]):
            color = "#000000"
        object.__setattr__(self, "color", color)
        object.__setattr__(self, "label", _label(self.label))


@dataclass(frozen=True)
class TransformNode:
    """Le transform d'un clip (position, échelle, rotation, ancrage, miroirs, opacité) et ses images-clés (temps de la
    composition) : l'image d'entrée est posée dans le cadre comme le serait un clip."""

    id: str
    transform: ClipTransform = field(default_factory=ClipTransform)
    keyframes: tuple = ()
    label: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "keyframes", tuple(self.keyframes or ()))
        object.__setattr__(self, "label", _label(self.label))


@dataclass(frozen=True)
class MergeNode:
    """Le premier plan (entrée 1) sur le fond (entrée 0) : mode de fusion, opacité du premier plan. Sans fond, le cadre
    transparent ; sans premier plan, le fond tel quel."""

    id: str
    blend: BlendMode = BlendMode.NORMAL
    opacity: float = 1.0
    label: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "blend", coerce_blend_mode(self.blend))
        try:
            opacity = min(1.0, max(0.0, float(self.opacity)))
        except (TypeError, ValueError):
            opacity = 1.0
        object.__setattr__(self, "opacity", opacity if opacity == opacity else 1.0)
        object.__setattr__(self, "label", _label(self.label))


@dataclass(frozen=True)
class MaskNode:
    """Masques (ceux d'un clip : formes, douceur, opérations), en coordonnées du cadre ; leurs images-clés vivent dans
    :attr:`Composition.animation` (``mask.<id>.*``)."""

    id: str
    masks: tuple[Mask, ...] = ()
    label: str = ""

    def __post_init__(self) -> None:
        masks = tuple(self.masks or ())
        if not all(isinstance(mask, Mask) for mask in masks):
            raise NodeGraphError(f"Masque invalide sur le nœud {self.id!r}.")
        object.__setattr__(self, "masks", masks)
        object.__setattr__(self, "label", _label(self.label))


@dataclass(frozen=True)
class KeyNode:
    """Incrustation (chroma key, suppression du débordement)."""

    id: str
    key: ChromaKey = field(default_factory=lambda: ChromaKey(enabled=True))
    label: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.key, ChromaKey):
            raise NodeGraphError(f"Incrustation invalide sur le nœud {self.id!r}.")
        object.__setattr__(self, "label", _label(self.label))


@dataclass(frozen=True)
class EffectsNode:
    """Effets visuels (ceux d'un clip), dans l'ordre ; l'alpha est gardé."""

    id: str
    effects: tuple = ()
    label: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "effects", tuple(self.effects or ()))
        object.__setattr__(self, "label", _label(self.label))


@dataclass(frozen=True)
class GradeNode:
    """Un étalonnage (réglage simple, ou nœuds d'étalonnage sans fenêtre) ; l'alpha est gardé."""

    id: str
    grade: Any = field(default_factory=ColorGrade)
    label: str = ""

    def __post_init__(self) -> None:
        correctors = getattr(self.grade, "correctors", None)
        if not isinstance(self.grade, ColorGrade) and correctors is None:
            raise NodeGraphError(f"Étalonnage invalide sur le nœud {self.id!r}.")
        if correctors is not None and any(node.windows for node in correctors):
            raise NodeGraphError("Un nœud d'étalonnage de composition n'a pas de fenêtre.")
        object.__setattr__(self, "label", _label(self.label))


@dataclass(frozen=True)
class OutputNode:
    """L'image du clip."""

    id: str = OUTPUT_ID


CompNode = (MediaNode | GraphicNode | SolidNode | TransformNode | MergeNode | MaskNode | KeyNode | EffectsNode
            | GradeNode | OutputNode)

SOURCE_TYPES = (MediaNode, GraphicNode, SolidNode)
KINDS: dict[type, str] = {
    MediaNode: "media", GraphicNode: "graphic", SolidNode: "solid", TransformNode: "transform", MergeNode: "merge",
    MaskNode: "mask", KeyNode: "key", EffectsNode: "effects", GradeNode: "grade", OutputNode: "output",
}
"""Le type de chaque nœud dans le fichier (et ses libellés : ``comp.node.<kind>``)."""


def input_count(node: CompNode) -> int:
    """Nombre d'entrées d'un nœud : 0 pour une source, 2 pour une fusion, 1 sinon."""
    if isinstance(node, SOURCE_TYPES):
        return 0
    return 2 if isinstance(node, MergeNode) else 1


def kind_of(node: CompNode) -> str:
    return KINDS[type(node)]


# --- Graphe ----------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CompositionGraph(NodeGraph[CompNode]):
    """Les nœuds d'une composition : une seule sortie, chaque lien sur une entrée qui existe."""

    def __post_init__(self) -> None:
        super().__post_init__()
        outputs = [node for node in self.nodes if isinstance(node, OutputNode)]
        if len(outputs) != 1:
            raise NodeGraphError("Une composition a une seule sortie.")
        for node in self.nodes:
            if not isinstance(node, tuple(KINDS)):
                raise NodeGraphError(f"Nœud de composition inconnu : {node!r}.")
        for link in self.links:
            if link.port >= input_count(self.node(link.target)) or link.port < 0:
                raise NodeGraphError(f"Le nœud {link.target!r} n'a pas d'entrée {link.port}.")
        if any(link.source == outputs[0].id for link in self.links):
            raise NodeGraphError("La sortie ne relie rien.")

    @classmethod
    def empty(cls) -> "CompositionGraph":
        return cls(nodes=(OutputNode(),))

    @property
    def output(self) -> OutputNode:
        return next(node for node in self.nodes if isinstance(node, OutputNode))

    def input_of(self, node_id: str, port: int = 0) -> str | None:
        """Le nœud relié à l'entrée ``port`` de ``node_id`` (``None`` : rien)."""
        return next((link.source for link in self.inputs(node_id) if link.port == port), None)

    def upstream(self, node_id: str) -> set[str]:
        """``node_id`` et tous les nœuds dont il dépend."""
        seen = {node_id}
        pending = [node_id]
        while pending:
            for link in self.inputs(pending.pop()):
                if link.source not in seen:
                    seen.add(link.source)
                    pending.append(link.source)
        return seen

    def rendered(self, sink: str | None = None) -> tuple[CompNode, ...]:
        """Les nœuds qui comptent pour l'image de ``sink`` (la sortie par défaut), dans l'ordre de calcul."""
        needed = self.upstream(sink or self.output.id)
        return tuple(node for node in self.order() if node.id in needed)

    # -- édition (chaque opération rend un nouveau graphe, valide) -------------------------------------------------

    def with_added(self, node: CompNode) -> "CompositionGraph":
        if self.has_node(node.id):
            raise NodeGraphError(f"Le nœud {node.id!r} existe déjà.")
        return replace(self, nodes=self.nodes + (node,))

    def connected(self, source: str, target: str, port: int = 0) -> "CompositionGraph":
        """``source`` alimente l'entrée ``port`` de ``target`` (à la place de ce qui y était) ; un cycle est refusé."""
        links = [link for link in self.links if not (link.target == target and link.port == port)]
        return replace(self, links=tuple(links) + (NodeLink(source, target, port),))

    def disconnected(self, target: str, port: int) -> "CompositionGraph":
        return replace(self, links=tuple(link for link in self.links
                                         if not (link.target == target and link.port == port)))

    def inserted(self, node: CompNode, after: str | None = None) -> "CompositionGraph":
        """Ajoute ``node`` à sa place logique, comme dans Fusion (sans tirer de lien soi-même) :

        - un **traitement** (une entrée) se glisse après ``after`` (le nœud choisi) : il en prend les lecteurs, et
          ``after`` l'alimente ; sans ``after`` (ou la sortie), juste avant la sortie ;
        - une **source** est posée par-dessus : une fusion nouvelle met la source au premier plan sur l'image de
          ``after`` (ou sur ce qui arrive à la sortie), et prend sa place ; sur une composition vide, la source va
          droit à la sortie ;
        - une **fusion** se glisse comme un traitement (``after`` en fond).
        """
        output = self.output.id
        if after is not None and (after == output or not self.has_node(after)):
            after = None
        graph = self.with_added(node)
        if isinstance(node, SOURCE_TYPES):
            target = after if after is not None else graph.input_of(output)
            if target is None:
                return graph.connected(node.id, output)
            merge = MergeNode(graph.next_id("f"))
            return graph._spliced(target, merge.id).connected(node.id, merge.id, FOREGROUND_PORT)
        if after is None:
            before = graph.input_of(output)
            graph = graph.connected(node.id, output)
            return graph.connected(before, node.id, 0) if before is not None else graph
        return graph._spliced(after, node.id, existing=True)

    def _spliced(self, after: str, node_id: str, *, existing: bool = False) -> "CompositionGraph":
        """``node_id`` (ajouté s'il n'existe pas : une fusion) reçoit ``after`` en entrée 0 et prend ses lecteurs."""
        graph = self if existing else self.with_added(MergeNode(node_id))
        readers = [link for link in graph.outputs(after)]
        links = [link for link in graph.links if link not in readers]
        links += [NodeLink(node_id, link.target, link.port) for link in readers]
        graph = replace(graph, links=tuple(links))
        return graph.connected(after, node_id, 0)

    def without(self, node_id: str) -> "CompositionGraph":
        """Le graphe sans ``node_id`` ; un nœud à une entrée est remplacé par ce qui l'alimentait (la chaîne reste
        reliée). La sortie ne se supprime pas."""
        node = self.node(node_id)
        if isinstance(node, OutputNode):
            raise NodeGraphError("La sortie d'une composition ne se supprime pas.")
        feeding = self.input_of(node_id, 0) if input_count(node) == 1 else None
        links = []
        for link in self.links:
            if link.target == node_id:
                continue
            if link.source == node_id:
                if feeding is None:
                    continue
                link = NodeLink(feeding, link.target, link.port)
            links.append(link)
        return replace(self, nodes=tuple(item for item in self.nodes if item.id != node_id), links=tuple(links))


@dataclass(frozen=True)
class Composition:
    """Le contenu d'un clip de composition : son graphe, sa durée, l'animation de ses masques (``mask.<id>.*``, temps
    de la composition)."""

    graph: CompositionGraph = field(default_factory=CompositionGraph.empty)
    duration: float = 1.0
    animation: tuple = ()

    def __post_init__(self) -> None:
        if not isinstance(self.graph, CompositionGraph):
            raise NodeGraphError("Composition sans graphe.")
        object.__setattr__(self, "duration", max(1e-3, _time(self.duration, 1.0)))
        object.__setattr__(self, "animation", tuple(self.animation or ()))

    def with_graph(self, graph: CompositionGraph) -> "Composition":
        return replace(self, graph=graph)

    def media_ids(self) -> set[str]:
        return {node.asset_id for node in self.graph.nodes if isinstance(node, MediaNode) and node.asset_id}


@dataclass(frozen=True)
class CompositionView:
    """Aperçu seulement (page Composition) : l'image du nœud ``node_id`` à la place de la sortie du clip. Passée comme
    une valeur montrée (:func:`core.preview_segments.apply_grade_overrides`), jamais écrite ni exportée."""

    node_id: str

    enabled = True

    def is_identity(self) -> bool:
        return False


# --- Fichier ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CompositionCodec:
    """Les codecs du fichier projet que la composition réutilise (transform, images-clés, effets, étalonnage, calque
    graphique) : :mod:`core.project_io` les fournit, ce module ne connaît pas leur format."""

    transform_to: Callable[[ClipTransform], Any]
    transform_from: Callable[[Any], ClipTransform]
    keyframe_to: Callable[[Any], Any]
    keyframe_from: Callable[[Any], Any]
    animation_from: Callable[[Any], Any]
    effect_to: Callable[[Any], Any]
    effects_from: Callable[[Any], list]
    grade_to: Callable[[Any], Any]
    grade_from: Callable[[Any], Any]
    graphic_to: Callable[[Any], Any]
    graphic_from: Callable[[Any], Any]


def composition_to_dict(composition: Composition, codec: CompositionCodec) -> dict[str, object]:
    """``{"duration", "nodes": [{"id", "kind", …}], "links": [[source, cible, entrée]], "animation"?}``."""
    nodes: list[dict[str, object]] = []
    for node in composition.graph.nodes:
        entry: dict[str, object] = {"id": node.id, "kind": kind_of(node)}
        if getattr(node, "label", ""):
            entry["label"] = node.label  # type: ignore[union-attr]
        if isinstance(node, MediaNode):
            entry.update(asset_id=node.asset_id, start=node.start, source_in=node.source_in, source_out=node.source_out)
            if node.gain_db:
                entry["gain_db"] = node.gain_db
            if node.muted:
                entry["muted"] = True
            if node.fill:
                entry.update(fill=True, pan_x=node.pan_x, pan_y=node.pan_y)
        elif isinstance(node, GraphicNode):
            entry.update(graphic=codec.graphic_to(node.graphic), start=node.start, duration=node.duration,
                         transform=codec.transform_to(node.transform),
                         transform_keyframes=[codec.keyframe_to(kf) for kf in node.transform_keyframes],
                         animation=[codec.keyframe_to(kf) for kf in node.animation])
            if node.masks:
                entry["masks"] = [mask_to_dict(mask) for mask in node.masks]
        elif isinstance(node, SolidNode):
            entry["color"] = node.color
        elif isinstance(node, TransformNode):
            entry.update(transform=codec.transform_to(node.transform),
                         keyframes=[codec.keyframe_to(kf) for kf in node.keyframes])
        elif isinstance(node, MergeNode):
            entry.update(blend=node.blend.value, opacity=node.opacity)
        elif isinstance(node, MaskNode):
            entry["masks"] = [mask_to_dict(mask) for mask in node.masks]
        elif isinstance(node, KeyNode):
            entry["key"] = vars(node.key)
        elif isinstance(node, EffectsNode):
            entry["effects"] = [codec.effect_to(effect) for effect in node.effects]
        elif isinstance(node, GradeNode):
            entry["grade"] = codec.grade_to(node.grade)
        nodes.append(entry)
    data: dict[str, object] = {
        "duration": composition.duration, "nodes": nodes,
        "links": [[link.source, link.target, link.port] for link in composition.graph.links],
    }
    if composition.animation:
        data["animation"] = [codec.keyframe_to(kf) for kf in composition.animation]
    return data


def _items(value: object) -> list | tuple:
    return value if isinstance(value, (list, tuple)) else ()


def _masks_from(raw: object) -> tuple[Mask, ...]:
    masks = []
    for item in _items(raw):
        if isinstance(item, dict):
            try:
                masks.append(mask_from_dict(item))
            except (TypeError, ValueError):
                continue
    return tuple(masks)


def _node_from(entry: dict, codec: CompositionCodec) -> CompNode | None:
    kind = entry.get("kind")
    node_id = str(entry.get("id") or "")
    label = entry.get("label", "")
    if not node_id:
        return None
    if kind == "output":
        return OutputNode(node_id)
    if kind == "media":
        return MediaNode(node_id, str(entry.get("asset_id") or ""), entry.get("start", 0.0),  # type: ignore[arg-type]
                         entry.get("source_in", 0.0), entry.get("source_out", 0.0), label,  # type: ignore[arg-type]
                         entry.get("gain_db", 0.0), bool(entry.get("muted", False)),  # type: ignore[arg-type]
                         bool(entry.get("fill", False)), entry.get("pan_x", 0.0), entry.get("pan_y", 0.0))  # type: ignore[arg-type]
    if kind == "graphic":
        graphic = codec.graphic_from(entry.get("graphic"))
        if graphic is None:
            return None
        return GraphicNode(node_id, graphic, entry.get("start", 0.0), entry.get("duration", 0.0),  # type: ignore[arg-type]
                           codec.transform_from(entry.get("transform")),
                           tuple(codec.keyframe_from(raw) for raw in _items(entry.get("transform_keyframes"))),
                           tuple(kf for raw in _items(entry.get("animation"))
                                 if (kf := codec.animation_from(raw)) is not None), label,  # type: ignore[arg-type]
                           _masks_from(entry.get("masks")))
    if kind == "solid":
        return SolidNode(node_id, str(entry.get("color") or ""), label)  # type: ignore[arg-type]
    if kind == "transform":
        return TransformNode(node_id, codec.transform_from(entry.get("transform")),
                             tuple(codec.keyframe_from(raw) for raw in _items(entry.get("keyframes"))), label)  # type: ignore[arg-type]
    if kind == "merge":
        return MergeNode(node_id, entry.get("blend", "normal"), entry.get("opacity", 1.0), label)  # type: ignore[arg-type]
    if kind == "mask":
        return MaskNode(node_id, _masks_from(entry.get("masks")), label)  # type: ignore[arg-type]
    if kind == "key":
        raw_key = entry.get("key")
        return KeyNode(node_id, ChromaKey(**raw_key) if isinstance(raw_key, dict) else ChromaKey(enabled=True),
                       label)  # type: ignore[arg-type]
    if kind == "effects":
        return EffectsNode(node_id, tuple(codec.effects_from(entry.get("effects"))), label)  # type: ignore[arg-type]
    if kind == "grade":
        grade = codec.grade_from(entry.get("grade")) or ColorGrade()
        return GradeNode(node_id, grade, label)  # type: ignore[arg-type]
    return None


def composition_from_dict(raw: object, codec: CompositionCodec) -> Composition | None:
    """Relit :func:`composition_to_dict` ; un nœud illisible est retiré (avec ses liens), un lien incohérent ignoré.
    ``None`` : pas une composition. Une composition abîmée s'ouvre avec ce qui se lit encore, plutôt que de faire
    échouer le projet."""
    if not isinstance(raw, dict) or "nodes" not in raw:
        return None
    nodes: list[CompNode] = []
    for entry in _items(raw.get("nodes")):
        if not isinstance(entry, dict):
            continue
        try:
            node = _node_from(entry, codec)
        except (NodeGraphError, TypeError, ValueError) as error:
            LOGGER.warning("Nœud de composition illisible, retiré : %s", error)
            continue
        if node is not None and all(existing.id != node.id for existing in nodes):
            nodes.append(node)
    first = next((node for node in nodes if isinstance(node, OutputNode)), None)
    nodes = [node for node in nodes if not isinstance(node, OutputNode) or node is first]   # sa place est gardée
    if first is None:                                       # sortie perdue : une neuve, d'identifiant libre
        taken = {node.id for node in nodes}
        nodes.append(OutputNode(next(name for name in (OUTPUT_ID, *(f"{OUTPUT_ID}{k}" for k in range(2, 1000)))
                                     if name not in taken)))
    graph = CompositionGraph(nodes=tuple(nodes))
    for entry in _items(raw.get("links")):
        if not (isinstance(entry, (list, tuple)) and len(entry) == 3):
            continue
        try:
            graph = graph.connected(str(entry[0]), str(entry[1]), int(entry[2]))
        except (NodeGraphError, TypeError, ValueError):
            continue
    animation = tuple(kf for item in _items(raw.get("animation")) if (kf := codec.animation_from(item)) is not None)
    return Composition(graph, raw.get("duration", 1.0), animation)  # type: ignore[arg-type]


def map_composition_grades(composition: Composition, change: Callable[[object], object]) -> Composition:
    """``composition`` avec ``change`` appliqué à l'étalonnage de chaque :class:`GradeNode` (LUT rendues portables…)."""
    nodes = tuple(replace(node, grade=change(node.grade)) if isinstance(node, GradeNode) else node
                  for node in composition.graph.nodes)
    return composition.with_graph(replace(composition.graph, nodes=nodes))


def nodes_of_kind(graph: CompositionGraph, kinds: Iterable[type]) -> tuple[CompNode, ...]:
    wanted = tuple(kinds)
    return tuple(node for node in graph.nodes if isinstance(node, wanted))
