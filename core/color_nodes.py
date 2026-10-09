"""Étalonnage par nœuds d'un clip (Couleur, étape 1 : nœuds en série).

``clip.color_grade`` porte soit un :class:`~core.color_grading.ColorGrade` (un seul réglage : tous les projets d'avant
les nœuds, et tout clip qu'on n'a pas découpé), soit un :class:`ColorNodeGraph` : des nœuds, chacun un ``ColorGrade``
complet (roues, courbes, LUT…), appliqués l'un après l'autre. Les deux formes ne se mélangent pas :

- :func:`as_graph` lit n'importe quelle valeur comme un graphe (un ``ColorGrade`` est un graphe d'un nœud, ``n1``) ;
- :func:`simplify` rend la forme la plus simple : un graphe d'un seul nœud sans nom redevient ce ``ColorGrade``, donc
  un clip qu'on n'a pas découpé s'écrit dans le fichier exactement comme avant.

L'export enchaîne les étalonnages des nœuds actifs dans l'ordre du graphe
(:func:`core.export_engine._build_color_grade_filters`) ; un graphe d'un nœud rend donc les mêmes pixels que son
``ColorGrade``. Le moniteur GPU cuit cette chaîne entière dans sa LUT 3D unique : autant de nœuds qu'on veut, le coût
de lecture ne change pas.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace

from .color_grading import ColorGrade, LUTResource
from .node_graph import NodeGraph, NodeGraphError, NodeLink

LOGGER = logging.getLogger(__name__)

MAX_LABEL_LENGTH = 32
FIRST_NODE_ID = "n1"


@dataclass(frozen=True)
class ColorNode:
    """Un nœud d'étalonnage : un réglage complet, un nom facultatif ; ``grade.enabled`` l'active ou le contourne."""

    id: str
    grade: ColorGrade = field(default_factory=ColorGrade)
    label: str = ""

    def __post_init__(self) -> None:
        if not self.id or not isinstance(self.grade, ColorGrade):
            raise NodeGraphError(f"Nœud d'étalonnage invalide : {self.id!r}.")
        object.__setattr__(self, "label", str(self.label).strip()[:MAX_LABEL_LENGTH])

    @property
    def enabled(self) -> bool:
        return self.grade.enabled

    def is_active(self) -> bool:
        """Change-t-il l'image ? (activé et pas neutre)"""
        return self.grade.enabled and not self.grade.is_identity()


@dataclass(frozen=True)
class ColorNodeGraph(NodeGraph[ColorNode]):
    """Les nœuds d'étalonnage d'un clip, en série : ``nodes`` est la chaîne, de l'entrée vers la sortie.

    Étape 1 : chaque nœud alimente le suivant, et seulement lui (les liens sont ceux de la chaîne). Les nœuds
    parallèles et les nœuds de calque viendront à l'étape 2, sur le même graphe.
    """

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.nodes:
            raise NodeGraphError("Un étalonnage par nœuds a au moins un nœud.")
        if set(self.links) != set(_chain_links(self.nodes)) or len(self.links) != len(self.nodes) - 1:
            raise NodeGraphError("Les nœuds d'étalonnage sont en série (étape 1).")

    @classmethod
    def serial(cls, nodes: Iterable[ColorNode]) -> "ColorNodeGraph":
        chain = tuple(nodes)
        return cls(nodes=chain, links=_chain_links(chain))

    # Comme un ``ColorGrade`` pour le moniteur (:func:`core.gpu_grade.grade_is_active`).
    @property
    def enabled(self) -> bool:
        return any(node.enabled for node in self.nodes)

    def is_identity(self) -> bool:
        return not any(node.is_active() for node in self.nodes)

    def node_or_first(self, node_id: str | None) -> ColorNode:
        """Le nœud ``node_id`` s'il existe, sinon le premier (le nœud courant d'un autre clip n'existe pas forcément ici)."""
        return self.node(node_id) if node_id and self.has_node(node_id) else self.nodes[0]

    def position(self, node_id: str) -> int:
        """Rang du nœud dans la chaîne (0 : le premier)."""
        return [node.id for node in self.nodes].index(self.node(node_id).id)

    def with_node_after(self, after_id: str | None, grade: ColorGrade | None = None) -> tuple["ColorNodeGraph", str]:
        """Un nœud de plus, juste après ``after_id`` (``None`` : en fin de chaîne) ; rend le graphe et son identifiant."""
        new = ColorNode(self.next_id(), grade or ColorGrade())
        chain = list(self.nodes)
        index = len(chain) if after_id is None else self.position(after_id) + 1
        chain.insert(index, new)
        return ColorNodeGraph.serial(chain), new.id

    def without(self, node_id: str) -> "ColorNodeGraph":
        """La chaîne sans ce nœud, refermée (le précédent alimente le suivant) ; le dernier nœud ne se supprime pas."""
        if len(self.nodes) == 1:
            raise NodeGraphError("Le dernier nœud d'un clip ne se supprime pas : on le réinitialise.")
        self.node(node_id)
        return ColorNodeGraph.serial(node for node in self.nodes if node.id != node_id)

    def moved(self, node_id: str, index: int) -> "ColorNodeGraph":
        """Le nœud déplacé au rang ``index`` de la chaîne (borné)."""
        node = self.node(node_id)
        chain = [existing for existing in self.nodes if existing.id != node_id]
        chain.insert(max(0, min(len(chain), int(index))), node)
        return ColorNodeGraph.serial(chain)

    def with_grade(self, node_id: str, grade: ColorGrade) -> "ColorNodeGraph":
        return self.with_node(replace(self.node(node_id), grade=grade))

    def with_label(self, node_id: str, label: str) -> "ColorNodeGraph":
        return self.with_node(replace(self.node(node_id), label=label))


def _chain_links(nodes: tuple[ColorNode, ...]) -> tuple[NodeLink, ...]:
    return tuple(NodeLink(first.id, second.id) for first, second in zip(nodes, nodes[1:]))


def as_graph(value: object) -> ColorNodeGraph:
    """Le graphe d'un clip : le sien, ou un nœud ``n1`` portant son ``ColorGrade`` (neutre s'il n'en a pas)."""
    if isinstance(value, ColorNodeGraph):
        return value
    grade = value if isinstance(value, ColorGrade) else ColorGrade()
    return ColorNodeGraph.serial((ColorNode(FIRST_NODE_ID, grade),))


def simplify(graph: ColorNodeGraph) -> ColorGrade | ColorNodeGraph:
    """La forme la plus simple : un seul nœud sans nom redevient son ``ColorGrade`` (fichier identique à avant)."""
    if len(graph.nodes) == 1 and not graph.nodes[0].label:
        return graph.nodes[0].grade
    return graph


def grades_of(value: object) -> tuple[ColorGrade, ...]:
    """Tous les réglages d'un clip, dans l'ordre de la chaîne."""
    if isinstance(value, ColorNodeGraph):
        return tuple(node.grade for node in value.nodes)
    return (value,) if isinstance(value, ColorGrade) else ()


def map_grades(value: object, change: Callable[[ColorGrade], ColorGrade]) -> object:
    """``value`` avec ``change`` appliqué à chacun de ses réglages (LUT rendues portables, relinkées…)."""
    if isinstance(value, ColorNodeGraph):
        return ColorNodeGraph.serial(replace(node, grade=change(node.grade)) for node in value.nodes)
    return change(value) if isinstance(value, ColorGrade) else value


def luts_of(value: object) -> tuple[LUTResource, ...]:
    return tuple(grade.lut for grade in grades_of(value) if grade.lut is not None)


def graph_to_dict(graph: ColorNodeGraph, grade_to_dict: Callable[[ColorGrade], object]) -> dict[str, object]:
    """``{"nodes": [{id, label, grade}], "links": [[source, cible, entrée]]}`` ; ``grade_to_dict`` : le codec du
    fichier (chemins de LUT relatifs au projet, ou absolus pour un preset)."""
    return {
        "nodes": [
            {"id": node.id, **({"label": node.label} if node.label else {}), "grade": grade_to_dict(node.grade)}
            for node in graph.nodes
        ],
        "links": [[link.source, link.target, link.port] for link in graph.links],
    }


def _items(value: object) -> list | tuple:
    """Une liste du fichier, ou rien (un nombre, un texte, un objet ne se parcourent pas comme des nœuds)."""
    return value if isinstance(value, (list, tuple)) else ()


def graph_from_dict(raw: dict, grade_from_dict: Callable[[object], ColorGrade | None]) -> ColorNodeGraph | None:
    """Relit :func:`graph_to_dict` ; un nœud illisible est neutre, un graphe sans nœud valide donne ``None``.

    ``nodes`` / ``links`` qui ne sont pas des listes (fichier abîmé ou modifié à la main) comptent comme vides : le
    projet s'ouvre, le clip sans étalonnage plutôt que pas du tout. Des liens qui ne forment pas la chaîne (un fichier d'une version future, avec des nœuds parallèles) : la chaîne
    est refaite dans l'ordre du fichier, avec un avertissement, plutôt que de perdre l'étalonnage.
    """
    nodes: list[ColorNode] = []
    for item in _items(raw.get("nodes")):
        if not isinstance(item, dict):
            continue
        try:
            nodes.append(ColorNode(str(item.get("id", "")), grade_from_dict(item.get("grade")) or ColorGrade(),
                                   str(item.get("label", ""))))
        except NodeGraphError:
            continue
    if not nodes:
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
        return ColorNodeGraph.serial(nodes)
    except NodeGraphError:                                  # identifiants en double : on renumérote
        return ColorNodeGraph.serial(replace(node, id=f"n{index + 1}") for index, node in enumerate(nodes))
