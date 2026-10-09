"""Étalonnage par nœuds d'un clip : nœuds en série, en parallèle, en calques, chacun qualifiable.

``clip.color_grade`` porte soit un :class:`~core.color_grading.ColorGrade` (un seul réglage : tous les projets d'avant
les nœuds, et tout clip qu'on n'a pas découpé), soit un :class:`ColorNodeGraph`. Les deux formes ne se mélangent pas :

- :func:`as_graph` lit n'importe quelle valeur comme un graphe (un ``ColorGrade`` est un graphe d'un nœud, ``n1``) ;
- :func:`simplify` rend la forme la plus simple : un seul nœud sans nom ni qualifieur redevient ce ``ColorGrade``, donc
  un clip qu'on n'a pas découpé s'écrit dans le fichier exactement comme avant.

Le graphe a deux sortes de nœuds :

- :class:`ColorNode` (correcteur) : un ``ColorGrade`` complet (roues, courbes, LUT…), ``grade.enabled`` le contourne,
  un :class:`~core.color_qualifier.Qualifier` facultatif limite ce qu'il corrige. Une entrée au plus (aucune : l'image
  du clip) ;
- :class:`ColorMixer` (mélangeur) : réunit au moins deux branches. **Parallèle** : chaque branche corrige la même image
  (celle du point où elles se séparent) et les corrections s'additionnent. **Calques** : la branche de l'entrée la plus
  haute passe sur les autres, là où sa clé (son qualifieur) la sélectionne ; sans clé elle les recouvre.

Le graphe a une seule sortie (le nœud que rien ne suit). Rendu : :mod:`core.color_render` (export exact, moniteur par
une LUT 3D unique, quelle que soit la forme du graphe : chaque nœud ne dépend que du pixel).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from enum import Enum

from .color_grading import ColorGrade, LUTResource
from .color_qualifier import Qualifier
from .node_graph import NodeGraph, NodeGraphError, NodeLink

LOGGER = logging.getLogger(__name__)

MAX_LABEL_LENGTH = 32
FIRST_NODE_ID = "n1"


class MixerKind(str, Enum):
    PARALLEL = "parallel"
    LAYER = "layer"


@dataclass(frozen=True)
class ColorNode:
    """Un nœud correcteur : un réglage complet, un nom facultatif, un qualifieur facultatif."""

    id: str
    grade: ColorGrade = field(default_factory=ColorGrade)
    label: str = ""
    qualifier: Qualifier | None = None

    def __post_init__(self) -> None:
        if not self.id or not isinstance(self.grade, ColorGrade):
            raise NodeGraphError(f"Nœud d'étalonnage invalide : {self.id!r}.")
        if self.qualifier is not None and not isinstance(self.qualifier, Qualifier):
            raise NodeGraphError(f"Qualifieur invalide sur le nœud {self.id!r}.")
        object.__setattr__(self, "label", str(self.label).strip()[:MAX_LABEL_LENGTH])

    @property
    def enabled(self) -> bool:
        return self.grade.enabled

    def is_active(self) -> bool:
        """Change-t-il l'image ? (activé et pas neutre)"""
        return self.grade.enabled and not self.grade.is_identity()

    def restricted(self) -> bool:
        """Le qualifieur limite-t-il la correction ?"""
        return self.qualifier is not None and self.qualifier.restricts()


@dataclass(frozen=True)
class ColorMixer:
    """Un mélangeur : réunit les branches qui lui arrivent (entrées 0, 1, 2…)."""

    id: str
    kind: MixerKind = MixerKind.PARALLEL

    def __post_init__(self) -> None:
        if not self.id:
            raise NodeGraphError("Mélangeur sans identifiant.")
        try:
            object.__setattr__(self, "kind", MixerKind(self.kind))
        except ValueError as error:
            raise NodeGraphError(f"Mélangeur inconnu : {self.kind!r}.") from error


AnyNode = ColorNode | ColorMixer


@dataclass(frozen=True)
class ColorNodeGraph(NodeGraph[AnyNode]):
    """Les nœuds d'étalonnage d'un clip : au moins un correcteur, une seule sortie, des mélangeurs à deux entrées ou
    plus (0, 1, 2… sans trou), un correcteur à une entrée au plus."""

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.correctors:
            raise NodeGraphError("Un étalonnage par nœuds a au moins un nœud.")
        for node in self.nodes:
            ports = [link.port for link in self.inputs(node.id)]
            if isinstance(node, ColorMixer):
                if len(ports) < 2 or ports != list(range(len(ports))):
                    raise NodeGraphError(f"Le mélangeur {node.id!r} reçoit au moins deux branches.")
            elif not isinstance(node, ColorNode):
                raise NodeGraphError(f"Nœud inconnu : {node!r}.")
            elif ports not in ([], [0]):
                raise NodeGraphError(f"Le nœud {node.id!r} a une seule entrée.")
        if len([node for node in self.nodes if not self.outputs(node.id)]) != 1:
            raise NodeGraphError("Un étalonnage par nœuds a une seule sortie.")

    @classmethod
    def serial(cls, nodes: Iterable[ColorNode]) -> "ColorNodeGraph":
        chain = tuple(nodes)
        return cls(nodes=chain, links=_chain_links(chain))

    # -- lecture ------------------------------------------------------------------------------------------------

    @property
    def correctors(self) -> tuple[ColorNode, ...]:
        return tuple(node for node in self.nodes if isinstance(node, ColorNode))

    @property
    def mixers(self) -> tuple[ColorMixer, ...]:
        return tuple(node for node in self.nodes if isinstance(node, ColorMixer))

    @property
    def sink(self) -> AnyNode:
        return next(node for node in self.nodes if not self.outputs(node.id))

    def corrector(self, node_id: str) -> ColorNode:
        node = self.node(node_id)
        if not isinstance(node, ColorNode):
            raise NodeGraphError(f"{node_id!r} est un mélangeur, pas un nœud correcteur.")
        return node

    def input_of(self, node_id: str) -> str | None:
        """Source de l'entrée d'un correcteur (``None`` : l'image du clip)."""
        links = self.inputs(node_id)
        return links[0].source if links else None

    # Comme un ``ColorGrade`` pour le moniteur (:func:`core.gpu_grade.grade_is_active`).
    @property
    def enabled(self) -> bool:
        return any(node.enabled for node in self.correctors)

    def is_identity(self) -> bool:
        return not any(node.is_active() for node in self.correctors)

    def node_or_first(self, node_id: str | None) -> ColorNode:
        """Le correcteur ``node_id`` s'il existe, sinon le premier (le nœud courant d'un autre clip n'existe pas
        forcément ici ; un mélangeur ne se règle pas)."""
        if node_id and self.has_node(node_id) and isinstance(self.node(node_id), ColorNode):
            return self.corrector(node_id)
        return next(node for node in self.order() if isinstance(node, ColorNode))

    def position(self, node_id: str) -> int:
        """Rang du correcteur dans l'ordre de calcul (0 : le premier) : son numéro affiché, moins un."""
        return [node.id for node in self.order() if isinstance(node, ColorNode)].index(self.corrector(node_id).id)

    def split_point(self, node_id: str) -> str | None:
        """Le nœud par lequel passent toutes les branches qui arrivent à ``node_id`` (son dominateur immédiat ;
        ``None`` : l'image du clip). Pour un mélangeur parallèle, l'image que chaque branche corrige."""
        dominators: dict[str, set[str]] = {}
        for node in self.order():
            sources = [link.source for link in self.inputs(node.id)]
            common = set.intersection(*(dominators[source] for source in sources)) if sources else set()
            dominators[node.id] = common | {node.id}
        strict = dominators[node_id] - {node_id}
        if not strict:
            return None
        rank = {node.id: index for index, node in enumerate(self.order())}
        return max(strict, key=rank.__getitem__)

    def serial_run(self, node_id: str) -> list[str]:
        """La suite de correcteurs en série qui contient ``node_id`` (chacun l'unique entrée et l'unique sortie de
        son voisin) : là où un nœud peut changer de place."""
        def single_link_correctors(first: str, second: str) -> bool:
            return (isinstance(self.node(first), ColorNode) and isinstance(self.node(second), ColorNode)
                    and len(self.outputs(first)) == 1 and self.input_of(second) == first)

        run = [self.corrector(node_id).id]
        while (previous := self.input_of(run[0])) is not None and single_link_correctors(previous, run[0]):
            run.insert(0, previous)
        while (following := self.outputs(run[-1])) and len(following) == 1 \
                and single_link_correctors(run[-1], following[0].target):
            run.append(following[0].target)
        return run

    # -- modifications ------------------------------------------------------------------------------------------

    def with_node_after(self, after_id: str | None, grade: ColorGrade | None = None) -> tuple["ColorNodeGraph", str]:
        """Un correcteur de plus, en série juste après ``after_id`` (``None`` : après la sortie) ; il reprend tout ce
        que ``after_id`` alimentait. Rend le graphe et son identifiant."""
        anchor = self.node(after_id).id if after_id is not None else self.sink.id
        new = ColorNode(self.next_id(), grade or ColorGrade())
        links = [NodeLink(new.id, link.target, link.port) if link.source == anchor else link for link in self.links]
        links.append(NodeLink(anchor, new.id))
        return ColorNodeGraph(nodes=(*self.nodes, new), links=tuple(links)), new.id

    def with_branch(self, node_id: str, kind: MixerKind) -> tuple["ColorNodeGraph", str]:
        """Un correcteur de plus **à côté** de ``node_id`` : même entrée que lui, réunis par un mélangeur ``kind``
        (le nouveau sur l'entrée la plus haute : en calques, il passe dessus). Si ``node_id`` est déjà une branche
        directe d'un mélangeur de ce type, la nouvelle branche s'y ajoute. Rend le graphe et l'identifiant du nœud."""
        node = self.corrector(node_id)
        source = self.input_of(node.id)
        new = ColorNode(self.next_id(), ColorGrade())
        feeds = [NodeLink(source, new.id)] if source is not None else []
        outputs = self.outputs(node.id)
        if len(outputs) == 1:
            mixer = self.node(outputs[0].target)
            if isinstance(mixer, ColorMixer) and mixer.kind is MixerKind(kind) and self.split_point(mixer.id) == source:
                port = len(self.inputs(mixer.id))
                joined = (*self.links, *feeds, NodeLink(new.id, mixer.id, port))
                return ColorNodeGraph(nodes=(*self.nodes, new), links=joined), new.id
        mixer = ColorMixer(self.next_id("m"), MixerKind(kind))
        links = [NodeLink(mixer.id, link.target, link.port) if link.source == node.id else link for link in self.links]
        links += [*feeds, NodeLink(node.id, mixer.id, 0), NodeLink(new.id, mixer.id, 1)]
        return ColorNodeGraph(nodes=(*self.nodes, new, mixer), links=tuple(links)), new.id

    def without(self, node_id: str) -> "ColorNodeGraph":
        """Le graphe sans ce correcteur, refermé : son entrée alimente ce qu'il alimentait. Une branche qui devient
        vide quitte son mélangeur ; un mélangeur qui n'a plus qu'une branche disparaît. Le dernier correcteur ne se
        supprime pas (on le réinitialise)."""
        node = self.corrector(node_id)
        if len(self.correctors) == 1:
            raise NodeGraphError("Le dernier nœud d'un clip ne se supprime pas : on le réinitialise.")
        source = self.input_of(node.id)
        links = [link for link in self.links if node.id not in (link.source, link.target)]
        for link in sorted(self.outputs(node.id), key=lambda item: -item.port):
            target = self.node(link.target)
            if isinstance(target, ColorMixer) and self.split_point(target.id) == source:
                links = _drop_input(links, target.id, link.port)              # la branche n'a plus de nœud
            elif source is not None:
                links.append(NodeLink(source, link.target, link.port))
        nodes = [other for other in self.nodes if other.id != node.id]
        return _dissolve_mixers(nodes, links)

    def moved(self, node_id: str, index: int) -> "ColorNodeGraph":
        """Le correcteur déplacé au rang ``index`` (borné) de sa suite en série (:meth:`serial_run`)."""
        run = self.serial_run(node_id)
        order = [other for other in run if other != node_id]
        order.insert(max(0, min(len(order), int(index))), node_id)
        if order == run:
            return self
        inside = set(run)
        head_source = self.input_of(run[0])
        tail_links = self.outputs(run[-1])
        links = [link for link in self.links if link.target not in inside and link.source not in inside]
        if head_source is not None:
            links.append(NodeLink(head_source, order[0]))
        links += [NodeLink(first, second) for first, second in zip(order, order[1:])]
        links += [NodeLink(order[-1], link.target, link.port) for link in tail_links]
        return ColorNodeGraph(nodes=self.nodes, links=tuple(links))

    def with_grade(self, node_id: str, grade: ColorGrade) -> "ColorNodeGraph":
        return self.with_node(replace(self.corrector(node_id), grade=grade))

    def with_label(self, node_id: str, label: str) -> "ColorNodeGraph":
        return self.with_node(replace(self.corrector(node_id), label=label))

    def with_qualifier(self, node_id: str, qualifier: Qualifier | None) -> "ColorNodeGraph":
        return self.with_node(replace(self.corrector(node_id), qualifier=qualifier))


def _chain_links(nodes: tuple[ColorNode, ...]) -> tuple[NodeLink, ...]:
    return tuple(NodeLink(first.id, second.id) for first, second in zip(nodes, nodes[1:]))


def _drop_input(links: list[NodeLink], mixer_id: str, port: int) -> list[NodeLink]:
    """Retire l'entrée ``port`` d'un mélangeur ; les suivantes descendent d'un cran (0, 1, 2… sans trou)."""
    kept = []
    for link in links:
        if link.target == mixer_id and link.port == port:
            continue
        if link.target == mixer_id and link.port > port:
            link = NodeLink(link.source, link.target, link.port - 1)
        kept.append(link)
    return kept


def _dissolve_mixers(nodes: list[AnyNode], links: list[NodeLink]) -> ColorNodeGraph:
    """Un mélangeur à une seule branche n'en est plus un : sa branche alimente directement ce qu'il alimentait."""
    while True:
        lonely = next((node for node in nodes if isinstance(node, ColorMixer)
                       and sum(1 for link in links if link.target == node.id) < 2), None)
        if lonely is None:
            return ColorNodeGraph(nodes=tuple(nodes), links=tuple(links))
        inputs = [link for link in links if link.target == lonely.id]
        rest = [link for link in links if lonely.id not in (link.source, link.target)]
        if inputs:
            rest += [NodeLink(inputs[0].source, link.target, link.port) for link in links if link.source == lonely.id]
        nodes = [node for node in nodes if node.id != lonely.id]
        links = rest


def as_graph(value: object) -> ColorNodeGraph:
    """Le graphe d'un clip : le sien, ou un nœud ``n1`` portant son ``ColorGrade`` (neutre s'il n'en a pas)."""
    if isinstance(value, ColorNodeGraph):
        return value
    grade = value if isinstance(value, ColorGrade) else ColorGrade()
    return ColorNodeGraph.serial((ColorNode(FIRST_NODE_ID, grade),))


def simplify(graph: ColorNodeGraph) -> ColorGrade | ColorNodeGraph:
    """La forme la plus simple : un seul nœud sans nom ni qualifieur redevient son ``ColorGrade`` (fichier identique
    à avant)."""
    if len(graph.nodes) == 1:
        only = graph.correctors[0]
        if not only.label and only.qualifier is None:
            return only.grade
    return graph


def grades_of(value: object) -> tuple[ColorGrade, ...]:
    """Tous les réglages d'un clip, dans l'ordre des nœuds."""
    if isinstance(value, ColorNodeGraph):
        return tuple(node.grade for node in value.correctors)
    return (value,) if isinstance(value, ColorGrade) else ()


def map_grades(value: object, change: Callable[[ColorGrade], ColorGrade]) -> object:
    """``value`` avec ``change`` appliqué à chacun de ses réglages (LUT rendues portables, relinkées…)."""
    if isinstance(value, ColorNodeGraph):
        return replace(value, nodes=tuple(
            replace(node, grade=change(node.grade)) if isinstance(node, ColorNode) else node for node in value.nodes
        ))
    return change(value) if isinstance(value, ColorGrade) else value


def luts_of(value: object) -> tuple[LUTResource, ...]:
    return tuple(grade.lut for grade in grades_of(value) if grade.lut is not None)


# ---------------------------------------------------------------------------
# Fichier
# ---------------------------------------------------------------------------


def graph_to_dict(graph: ColorNodeGraph, grade_to_dict: Callable[[ColorGrade], object]) -> dict[str, object]:
    """``{"nodes": [{id, label?, grade, qualifier?} | {id, mixer}], "links": [[source, cible, entrée]]}`` ;
    ``grade_to_dict`` : le codec du fichier (chemins de LUT relatifs au projet, ou absolus pour un preset)."""
    entries: list[dict[str, object]] = []
    for node in graph.nodes:
        if isinstance(node, ColorMixer):
            entries.append({"id": node.id, "mixer": node.kind.value})
            continue
        entry: dict[str, object] = {"id": node.id}
        if node.label:
            entry["label"] = node.label
        entry["grade"] = grade_to_dict(node.grade)
        if node.qualifier is not None:
            entry["qualifier"] = node.qualifier.to_dict()
        entries.append(entry)
    return {"nodes": entries, "links": [[link.source, link.target, link.port] for link in graph.links]}


def _items(value: object) -> list | tuple:
    """Une liste du fichier, ou rien (un nombre, un texte, un objet ne se parcourent pas comme des nœuds)."""
    return value if isinstance(value, (list, tuple)) else ()


def graph_from_dict(raw: dict, grade_from_dict: Callable[[object], ColorGrade | None]) -> ColorNodeGraph | None:
    """Relit :func:`graph_to_dict` ; un nœud illisible est neutre, un graphe sans correcteur valide donne ``None``.

    ``nodes`` / ``links`` qui ne sont pas des listes (fichier abîmé ou modifié à la main) comptent comme vides : le
    projet s'ouvre, le clip sans étalonnage plutôt que pas du tout. Un graphe invalide (liens incohérents, forme
    d'une version future) : ses correcteurs sont remis en série dans l'ordre du fichier, avec un avertissement,
    plutôt que de perdre l'étalonnage.
    """
    nodes: list[AnyNode] = []
    for item in _items(raw.get("nodes")):
        if not isinstance(item, dict):
            continue
        try:
            if "mixer" in item:
                nodes.append(ColorMixer(str(item.get("id", "")), item.get("mixer")))  # type: ignore[arg-type]
            else:
                nodes.append(ColorNode(str(item.get("id", "")), grade_from_dict(item.get("grade")) or ColorGrade(),
                                       str(item.get("label", "")), Qualifier.from_dict(item.get("qualifier"))))
        except NodeGraphError:
            continue
    correctors = [node for node in nodes if isinstance(node, ColorNode)]
    if not correctors:
        return None
    links = []
    for entry in _items(raw.get("links")):
        if isinstance(entry, (list, tuple)) and len(entry) == 3:
            links.append(NodeLink(str(entry[0]), str(entry[1]), int(entry[2]) if str(entry[2]).isdigit() else 0))
    try:
        return ColorNodeGraph(nodes=tuple(nodes), links=tuple(links))
    except NodeGraphError as error:
        LOGGER.warning("Nœuds d'étalonnage remis en série : %s", error)
    try:
        return ColorNodeGraph.serial(correctors)
    except NodeGraphError:                                  # identifiants en double : on renumérote
        return ColorNodeGraph.serial(replace(node, id=f"n{index + 1}") for index, node in enumerate(correctors))
