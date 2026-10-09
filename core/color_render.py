"""Rendu d'un étalonnage par nœuds : le graphe de filtres FFmpeg de l'export, que le moniteur cuit en LUT 3D.

Un graphe sans mélangeur ni qualifieur est une chaîne : les filtres de chaque nœud actif, l'un après l'autre (étape 1).
Sinon, le texte rendu reste **insérable dans une chaîne à virgules** comme avant (``null[e0];…;[sortie]null``) : la
chaîne d'un clip, celle d'un calque, la cuisson du moniteur et l'empreinte du cache n'ont rien à changer. Chaque
appelant donne un préfixe ``tag`` unique dans son graphe (les labels internes en dérivent).

Opérations, toutes en ``gbrp`` 8 bits (les trois plans R, V, B ; mélanges exacts au niveau près, mesurés) :

- correcteur : ``O = G(I)`` ; qualifié : ``O = maskedmerge(I, G(I), K(I))``, soit ``I + K·(G − I)`` arrondi ; ``K`` est
  la LUT 3D de la clé (:mod:`core.color_qualifier`) ;
- mélangeur parallèle (branches ``B₁…Bₙ`` séparées en ``S``) : ``mix`` aux poids ``1 … 1 −(n−1)``, soit
  ``S + Σ(Bᵢ − S)`` écrêté une seule fois, à la fin ;
- mélangeur de calques : ``B₀``, puis chaque branche plus haute par-dessus. Une branche dont le dernier nœud est
  qualifié pose **sa correction** ``G`` selon sa clé : ``maskedmerge(dessous, G, K)`` (prendre sa sortie, déjà mélangée
  à son entrée par la clé, adoucirait deux fois le bord) ; sans clé, elle recouvre.

:class:`Highlight` (moniteur seulement) montre la sélection d'un nœud qualifié : sa sortie là où la clé le choisit,
le reste en gris assombri.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

from .color_grading import ColorGrade
from .color_nodes import ColorMixer, ColorNode, ColorNodeGraph, MixerKind
from .color_qualifier import key_cube_path

GREY_HIGHLIGHT = "colorchannelmixer=.3:.59:.11:0:.3:.59:.11:0:.3:.59:.11,lutrgb=r='val*0.45+20':g='val*0.45+20':b='val*0.45+20'"
"""Hors sélection (mode « afficher la sélection ») : la luminance, assombrie, en gris."""


@dataclass(frozen=True)
class Highlight:
    """Le graphe d'un clip, sa sélection montrée sur le nœud ``node_id`` (moniteur ; jamais écrit ni exporté)."""

    graph: ColorNodeGraph
    node_id: str

    # Comme un étalonnage actif pour le moniteur (:func:`core.gpu_grade.grade_is_active`).
    enabled = True

    def is_identity(self) -> bool:
        return False


class _Streams:
    """Les images intermédiaires (flux numérotés, 0 : l'entrée) et les filtres qui les produisent ; :meth:`text`
    insère un ``split`` derrière toute image utilisée plusieurs fois."""

    def __init__(self, tag: str) -> None:
        self.tag = tag
        self.ops: list[tuple[tuple[int, ...], str, int]] = []
        self.count = 1

    def op(self, inputs: tuple[int, ...], filters: str) -> int:
        output = self.count
        self.count += 1
        self.ops.append((inputs, filters, output))
        return output

    def text(self, result: int) -> str:
        # Seules les opérations qui mènent au résultat (un calque sans clé rend inutile ce qu'il recouvre) : une
        # sortie que rien ne lit ferait échouer FFmpeg.
        live = {result}
        ops: list[tuple[tuple[int, ...], str, int]] = []
        for inputs, filters, output in reversed(self.ops):
            if output in live:
                ops.insert(0, (inputs, filters, output))
                live.update(inputs)
        uses = Counter(stream for inputs, _filters, _output in ops for stream in inputs)
        uses[result] += 1
        labels: dict[int, list[str]] = {}

        def produce(stream: int) -> str:
            """Labels de sortie du flux (un par usage, ``split`` si besoin)."""
            names = [f"{self.tag}{stream}" + (f"_{use}" if uses[stream] > 1 else "") for use in range(uses[stream])]
            labels[stream] = names
            outputs = "".join(f"[{name}]" for name in names)
            return f",split={len(names)}{outputs}" if len(names) > 1 else outputs

        chains = ["null" + produce(0)]
        for inputs, filters, output in ops:
            sources = "".join(f"[{labels[stream].pop(0)}]" for stream in inputs)
            chains.append(f"{sources}{filters}{produce(output)}")
        chains.append(f"[{labels[result].pop(0)}]null")
        return ";".join(chains)


def render_filters(value, tag: str, linear: Callable[[ColorGrade], str]) -> str:
    """Filtres d'un graphe de nœuds (ou d'un :class:`Highlight`), insérables dans une chaîne ; vide : rien à faire.

    ``linear`` : la chaîne exacte d'un ``ColorGrade`` (:func:`core.export_engine._build_color_grade_filters`).
    """
    highlight = value.node_id if isinstance(value, Highlight) else None
    graph: ColorNodeGraph = value.graph if isinstance(value, Highlight) else value
    nodes = graph.order()
    if highlight is None and not graph.mixers and not any(node.restricted() for node in graph.correctors):
        return ",".join(text for node in nodes if isinstance(node, ColorNode) and node.is_active()
                        and (text := linear(node.grade)))
    if highlight is None and graph.is_identity():
        return ""
    streams = _Streams(tag)
    source = streams.op((0,), "format=gbrp")
    outputs: dict[str, int] = {}
    layers: dict[str, tuple[int, int]] = {}             # correcteur qualifié → (sa correction, sa clé)
    for node in nodes:
        if isinstance(node, ColorNode):
            image = outputs[upstream] if (upstream := graph.input_of(node.id)) is not None else source
            outputs[node.id], layer = _corrector(streams, node, image, linear, highlight == node.id)
            if layer is not None:
                layers[node.id] = layer
        else:
            outputs[node.id] = _mixer(streams, graph, node, outputs, layers, source)
    return streams.text(outputs[graph.sink.id])


def _corrector(streams: _Streams, node: ColorNode, image: int, linear,
               show_key: bool) -> tuple[int, tuple[int, int] | None]:
    """Sortie d'un correcteur ; s'il est qualifié, aussi sa correction et sa clé (pour un mélangeur de calques)."""
    chain = linear(node.grade) if node.is_active() else ""
    graded = streams.op((image,), f"{chain},format=gbrp") if chain else image
    qualifier = node.qualifier
    if qualifier is None or not (qualifier.restricts() or show_key):
        return graded, None
    if graded == image and not show_key:
        return image, None
    key = streams.op((image,), f"lut3d=file='{_cube(qualifier)}':interp=tetrahedral,format=gbrp")
    base = streams.op((image,), f"{GREY_HIGHLIGHT},format=gbrp") if show_key else image
    return streams.op((base, graded, key), "maskedmerge"), (graded, key)


def _mixer(streams: _Streams, graph: ColorNodeGraph, mixer: ColorMixer, outputs: dict[str, int],
           layers: dict[str, tuple[int, int]], source: int) -> int:
    branches = [link.source for link in graph.inputs(mixer.id)]
    if mixer.kind is MixerKind.PARALLEL:
        split = graph.split_point(mixer.id)
        base = outputs[split] if split is not None else source
        images = [outputs[branch] for branch in branches]
        weights = " ".join(["1"] * len(images) + [str(1 - len(images))])
        return streams.op((*images, base), f"mix=inputs={len(images) + 1}:weights='{weights}'")
    result = outputs[branches[0]]
    for branch in branches[1:]:
        if branch in layers:
            graded, key = layers[branch]
            result = streams.op((result, graded, key), "maskedmerge")
        else:
            result = outputs[branch]                      # sans clé, la branche du dessus recouvre
    return result


def _cube(qualifier) -> str:
    from .export_engine import _escape_filter_path

    return _escape_filter_path(str(key_cube_path(qualifier)))
