"""Graphe de nœuds : nœuds identifiés, liens orientés, ordre de calcul.

Socle commun des deux modèles nodaux de Kut-Studio (ADR-0002) : l'étalonnage par nœuds d'un clip
(:mod:`core.color_nodes`) et, ensuite, la composition nodale. Le graphe est **immuable** (chaque modification rend un
nouveau graphe, l'historique d'annulation garde les anciens tels quels) et **toujours valide** : identifiants uniques et sûrs
(:func:`is_safe_id`), liens entre nœuds existants, une seule source par entrée, aucun cycle (:mod:`core.graph_cycles`, la logique de cycles
des séquences imbriquées et du parentage des calques).

Un lien relie la sortie d'un nœud à une **entrée** (``port``) d'un autre : un nœud d'étalonnage n'en a qu'une, une
fusion de composition en aura deux (fond, premier plan).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Generic, Protocol, Self, TypeVar

from .graph_cycles import find_cycles


class NodeGraphError(ValueError):
    """Graphe invalide : identifiant en double, lien vers un nœud absent, entrée doublée, cycle."""


MAX_ID_LENGTH = 64
_SAFE_ID = re.compile(r"[A-Za-z0-9_]+")


def is_safe_id(value: object) -> bool:
    """Un identifiant de nœud que l'export peut écrire tel quel dans un graphe de filtres FFmpeg.

    Les identifiants d'un projet viennent d'un fichier ``.kut`` (du JSON, donc n'importe quelle chaîne) et deviennent
    des étiquettes de ``-filter_complex`` : un ``]``, une virgule, un ``;`` ou une apostrophe y ouvriraient un autre
    filtre (``movie``, ``subtitles``…). L'éditeur n'en fabrique que de la forme ``n1``, ``f2`` : ASCII, chiffres et
    ``_`` suffisent, ``fullmatch`` (et non ``$``, qui laisse passer un saut de ligne final) et la longueur bornée."""
    return isinstance(value, str) and len(value) <= MAX_ID_LENGTH and _SAFE_ID.fullmatch(value) is not None


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
        if unsafe := [node_id for node_id in ids if not is_safe_id(node_id)]:
            raise NodeGraphError(f"Identifiants de nœuds non admis : {[str(item)[:40] for item in unsafe]}.")
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

    def outputs(self, node_id: str) -> tuple[NodeLink, ...]:
        """Liens qui partent de ``node_id`` (un nœud peut alimenter plusieurs branches)."""
        return tuple(link for link in self.links if link.source == node_id)

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

    def next_id(self, prefix: str = "n", *, reserved: Iterable[str] = ()) -> str:
        """Identifiant libre ``{prefix}{k}`` : le plus grand numéro présent, plus un.

        ``reserved`` : identifiants pas encore dans ``self.nodes`` mais déjà promis (ex. le correcteur
        qu'on vient d'allouer avant de créer son mélangeur) : ils comptent comme pris.
        """
        pattern = re.compile(rf"^{re.escape(prefix)}(\d+)$")
        numbers = [int(match.group(1)) for node in self.nodes if (match := pattern.match(node.id))]
        numbers += [int(match.group(1)) for item in reserved if (match := pattern.match(item))]
        return f"{prefix}{max(numbers, default=0) + 1}"

    def with_node(self, node: N) -> Self:
        """Le graphe avec ``node`` à la place du nœud de même identifiant (liens inchangés)."""
        self.node(node.id)
        return replace(self, nodes=tuple(node if existing.id == node.id else existing for existing in self.nodes))
