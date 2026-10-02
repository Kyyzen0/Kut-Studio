"""Graphe orienté : accessibilité et détection de cycles.

Source unique de la logique de cycles de Kut-Studio. Les séquences
imbriquées (``A`` contient ``B``) et le parentage des calques (``A`` suit
``B``) sont deux graphes de dépendances : les mêmes fonctions empêchent
``A → B → A`` ou ``A → B → C → A`` dans les deux cas.

Un graphe est un ``dict`` ``{nœud: nœuds référencés directement}``. Les
références vers un nœud absent du graphe sont tolérées (référence cassée,
traitée par l'appelant) : aucun parcours ne lève ni ne boucle.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

Graph = Mapping[str, Iterable[str]]


def reachable(graph: Graph, start: str) -> set[str]:
    """Nœuds atteignables depuis ``start`` (``start`` exclu sauf cycle).

    Parcours itératif avec ensemble des visités : termine toujours, même
    sur un graphe cyclique ou des références inconnues.
    """
    seen: set[str] = set()
    stack = list(graph.get(start, ()))
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(graph.get(current, ()))
    return seen


def would_create_cycle(graph: Graph, source: str, target: str) -> bool:
    """Ajouter l'arête ``source → target`` créerait-il un cycle ?

    Vrai si ``source == target`` ou si ``source`` est déjà atteignable
    depuis ``target``.
    """
    if source == target:
        return True
    return source in reachable(graph, target)


def find_cycles(graph: Graph) -> list[tuple[str, ...]]:
    """Cycles du graphe (composantes fortement connexes).

    Chaque cycle est un tuple d'identifiants trié. Une auto-référence
    (A → A) est un cycle d'un élément. Algorithme de Tarjan en version
    itérative : aucune limite de récursion Python à craindre.
    """
    index_of: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    cycles: list[tuple[str, ...]] = []
    counter = 0
    for root in graph:
        if root in index_of:
            continue
        work = [(root, iter(sorted(graph.get(root, ()))))]
        index_of[root] = lowlink[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, children = work[-1]
            advanced = False
            for child in children:
                if child not in graph:
                    continue  # référence cassée : traitée ailleurs
                if child not in index_of:
                    index_of[child] = lowlink[child] = counter
                    counter += 1
                    stack.append(child)
                    on_stack.add(child)
                    work.append((child, iter(sorted(graph.get(child, ())))))
                    advanced = True
                    break
                if child in on_stack:
                    lowlink[node] = min(lowlink[node], index_of[child])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                lowlink[parent] = min(lowlink[parent], lowlink[node])
            if lowlink[node] == index_of[node]:
                component = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                if len(component) > 1 or node in graph.get(node, ()):
                    cycles.append(tuple(sorted(component)))
    return cycles


__all__ = ["Graph", "find_cycles", "reachable", "would_create_cycle"]
