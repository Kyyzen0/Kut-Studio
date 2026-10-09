"""Graphe de nœuds : nœuds identifiés, liens orientés, ordre de calcul.

Socle commun des deux modèles nodaux de Kut-Studio (ADR-0002) : l'étalonnage par nœuds d'un clip
(:mod:`core.color_nodes`) et, ensuite, la composition nodale. Le graphe est **immuable** (chaque modification rend un
nouveau graphe, l'historique d'annulation garde les anciens tels quels) et **toujours valide** : identifiants uniques,
liens entre nœuds existants, une seule source par entrée, aucun cycle (:mod:`core.graph_cycles`, la logique de cycles
des séquences imbriquées et du parentage des calques).

Un lien relie la sortie d'un nœud à une **entrée** (``port``) d'un autre : un nœud d'étalonnage n'en a qu'une, une
fusion de composition en aura deux (fond, premier plan).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Generic, Protocol, Self, TypeVar

from .graph_cycles import find_cycles


class NodeGraphError(ValueError):
    """Graphe invalide : identifiant en double, lien vers un nœud absent, entrée doublée, cycle."""


class _Identified(Protocol):
    @property
    def id(self) -> str: ...


N = TypeVar("N", bound=_Identified)


@dataclass(frozen=True)
class NodeLink:
    """La sortie de ``source`` alimente l'entrée ``port`` de ``target``."""

    source: str
    target: str
    port: int = 0


@dataclass(frozen=True)
class NodeGraph(Generic[N]):
    """Nœuds (dans l'ordre de création, qui départage l'ordre de calcul) et liens."""

    nodes: tuple[N, ...] = ()
    links: tuple[NodeLink, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "nodes", tuple(self.nodes))
        object.__setattr__(self, "links", tuple(self.links))
        ids = [node.id for node in self.nodes]
        if len(set(ids)) != len(ids):
            raise NodeGraphError(f"Identifiants de nœuds en double : {ids}.")
        known = set(ids)
        fed: set[tuple[str, int]] = set()
        for link in self.links:
            if link.source not in known or link.target not in known:
                raise NodeGraphError(f"Lien vers un nœud absent : {link}.")
            if (link.target, link.port) in fed:
                raise NodeGraphError(f"Deux liens sur la même entrée : {link}.")
            fed.add((link.target, link.port))
        if find_cycles(self._edges()):
            raise NodeGraphError("Le graphe de nœuds contient un cycle.")

    def _edges(self) -> dict[str, list[str]]:
        edges: dict[str, list[str]] = {node.id: [] for node in self.nodes}
        for link in self.links:
            edges[link.source].append(link.target)
        return edges

    def node(self, node_id: str) -> N:
        for node in self.nodes:
            if node.id == node_id:
                return node
        raise NodeGraphError(f"Nœud introuvable : {node_id!r}.")

    def has_node(self, node_id: str) -> bool:
        return any(node.id == node_id for node in self.nodes)

    def inputs(self, node_id: str) -> tuple[NodeLink, ...]:
        """Liens qui arrivent sur ``node_id``, par entrée."""
        return tuple(sorted((link for link in self.links if link.target == node_id), key=lambda link: link.port))

    def order(self) -> tuple[N, ...]:
        """Ordre de calcul : chaque nœud après ceux qui l'alimentent ; à égalité, l'ordre de création (stable)."""
        position = {node.id: index for index, node in enumerate(self.nodes)}
        waiting = {node.id: len(self.inputs(node.id)) for node in self.nodes}
        edges = self._edges()
        ready = sorted((node_id for node_id, count in waiting.items() if count == 0), key=position.__getitem__)
        ordered: list[N] = []
        while ready:
            current = ready.pop(0)
            ordered.append(self.node(current))
            for target in edges[current]:
                waiting[target] -= 1
                if waiting[target] == 0:
                    ready.append(target)
                    ready.sort(key=position.__getitem__)
        return tuple(ordered)

    def next_id(self, prefix: str = "n") -> str:
        """Identifiant libre ``{prefix}{k}`` : le plus grand numéro présent, plus un."""
        pattern = re.compile(rf"^{re.escape(prefix)}(\d+)$")
        numbers = [int(match.group(1)) for node in self.nodes if (match := pattern.match(node.id))]
        return f"{prefix}{max(numbers, default=0) + 1}"

    def with_node(self, node: N) -> Self:
        """Le graphe avec ``node`` à la place du nœud de même identifiant (liens inchangés)."""
        self.node(node.id)
        return replace(self, nodes=tuple(node if existing.id == node.id else existing for existing in self.nodes))
