"""Rendu d'un étalonnage par nœuds : le graphe de filtres FFmpeg de l'export, que le moniteur cuit en LUT 3D.

Un graphe sans mélangeur ni qualifieur est une chaîne : les filtres de chaque nœud actif, l'un après l'autre (étape 1).
Sinon, le texte rendu reste **insérable dans une chaîne à virgules** comme avant (il commence par un filtre et finit
par un filtre) : la chaîne d'un clip, celle d'un calque, la cuisson du moniteur et l'empreinte du cache n'ont rien à
changer. Chaque appelant donne un préfixe ``tag`` unique dans son graphe (les labels internes en dérivent).

L'alpha passe à côté des mélanges (``alphaextract`` à l'entrée, ``alphamerge`` à la sortie) : un clip masqué, ou dont
une rotation laisse des coins transparents, le reste après un étalonnage à branches, comme après une chaîne (``eq``,
``colorbalance``, ``lutrgb``, ``lut3d`` gardent l'alpha).

Opérations, toutes en ``gbrp`` 8 bits (les trois plans R, V, B ; mélanges exacts au niveau près, mesurés) :

- correcteur : ``O = D(G(I))`` (``D`` : flou ``gblur`` puis netteté, une ``convolution`` 5×5 exacte) ; qualifié ou
  fenêtré : ``O = maskedmerge(I, D(G(I)), K)``, soit ``I + K·(D(G) − I)`` arrondi ; ``K`` est le produit (``blend``
  multiply) de la LUT 3D de la clé du qualifieur (:mod:`core.color_qualifier`) et de la matte des fenêtres ;
- mélangeur parallèle (branches ``B₁…Bₙ`` séparées en ``S``) : ``mix`` aux poids ``1 … 1 −(n−1)``, soit
  ``S + Σ(Bᵢ − S)`` écrêté une seule fois, à la fin ;
- mélangeur de calques : ``B₀``, puis chaque branche plus haute par-dessus. Une branche dont le dernier nœud est
  qualifié pose **sa correction** ``G`` selon sa clé : ``maskedmerge(dessous, G, K)`` (prendre sa sortie, déjà mélangée
  à son entrée par la clé, adoucirait deux fois le bord) ; sans clé, elle recouvre.

:class:`Highlight` montre la sélection d'un nœud qualifié : sa sortie là où la clé le choisit, le reste en gris
assombri. :class:`Compare` montre l'image sans étalonnage à gauche d'une part de la largeur du clip. Tous deux ne
servent qu'à l'aperçu (moniteur GPU, ou segments fidèles sans lui), jamais à l'export.

Fenêtres : l'appelant donne, pour chaque nœud de :func:`windowed_nodes`, la source de sa matte (``windows``) — le label
d'un flux ``gbrp`` (la couverture dans les trois plans) déjà aligné sur l'image que reçoit l'étalonnage, ou une
couverture constante (la pipette, qui ne lit qu'un point). Un flux qui ne sert finalement pas (branche recouverte) est
consommé par ``nullsink`` : FFmpeg refuse une sortie que rien ne lit.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from .color_grading import ColorGrade
from .color_nodes import ColorMixer, ColorNode, ColorNodeGraph, MixerKind
from .color_qualifier import key_cube_path

BINOMIAL = (1, 4, 6, 4, 1)
"""Le noyau de netteté (le flou binomial 5×5, comme ``unsharp`` et la passe ``sharpen`` du moniteur)."""

WindowSource = str | float
"""Label d'un flux de matte, ou couverture constante (0..1)."""


class WindowSourceMissing(ValueError):
    """Un nœud fenêtré sans source de matte : l'appelant n'a pas rastérisé ses fenêtres."""


GREY_HIGHLIGHT = ("colorchannelmixer=.3:.59:.11:0:.3:.59:.11:0:.3:.59:.11,"
                  "lutrgb=r='val*0.45+0.08*maxval':g='val*0.45+0.08*maxval':b='val*0.45+0.08*maxval'")
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


@dataclass(frozen=True)
class FilterChain:
    """Une chaîne de filtres déjà écrite, que le moniteur cuit telle quelle en LUT (passes d'un graphe spatial,
    :mod:`core.gpu_color_graph`) ; ``luts`` : les fichiers ``.cube`` qu'elle lit (identité de la LUT cuite)."""

    text: str
    luts: tuple = ()

    enabled = True

    def is_identity(self) -> bool:
        return False


@dataclass(frozen=True)
class Compare:
    """Avant / après dans l'aperçu fidèle (sans moniteur GPU) : à gauche de ``split`` (part de la largeur de l'image du
    clip), l'image sans étalonnage ; ``value`` : l'étalonnage montré à droite (le clip, ou sa sélection montrée)."""

    value: object
    split: float

    enabled = True

    def is_identity(self) -> bool:
        return False


def compare_filters(graded: str, split: float, tag: str) -> str:
    """``graded`` (la chaîne ou le sous-graphe de l'étalonnage) à droite, l'image d'origine à gauche de ``split`` ;
    insérable dans une chaîne à virgules comme le reste."""
    share = min(1.0, max(0.0, float(split)))
    if not graded or share <= 0.0:
        return graded
    return (
        f"split=2[{tag}o][{tag}i];[{tag}i]null,{graded}[{tag}g];"
        f"[{tag}o]format=rgba,crop=w='max(2,trunc(iw*{share:.6f}/2)*2)':h=ih:x=0:y=0[{tag}l];"
        f"[{tag}g]format=rgba[{tag}r];[{tag}r][{tag}l]overlay=x=0:y=0:format=rgb"
    )


def detail_filters(node: ColorNode, pixel_scale: float = 1.0) -> str:
    """Flou puis netteté d'un nœud, en ``gbrp`` (vide : aucun). ``pixel_scale`` : pixels de sortie par pixel de la
    séquence (le σ est réglé à la taille de la séquence, comme l'effet Flou)."""
    from .export_engine import _format_seconds

    filters = []
    if node.blur > 0.0:
        filters.append(f"gblur=sigma={_format_seconds(node.blur * pixel_scale)}")
    if node.sharpen > 0.0:
        # ``I + a·(I − B∗I)`` : ``unsharp`` ne lit pas le RVB planaire (FFmpeg passerait en YUV, donc arrondirait) ;
        # ``convolution`` l'applique aux trois plans, en flottant, arrondi une fois. Coefficients entiers : ``a`` au
        # millième, noyau ×256 000.
        amount = int(round(node.sharpen * 1000))
        weights = [-amount * a * b for a in BINOMIAL for b in BINOMIAL]
        weights[12] += 256 * (1000 + amount)
        matrix = " ".join(str(weight) for weight in weights)
        filters.append("convolution=" + ":".join(
            f"{plane}m='{matrix}':{plane}rdiv=1/256000" for plane in range(3)))
    return ",".join(filters)


def graph_of(value) -> ColorNodeGraph | None:
    """Le graphe de nœuds derrière ``value`` (graphe, :class:`Highlight`, :class:`Compare`), ``None`` pour un réglage
    simple."""
    if isinstance(value, Compare):
        return graph_of(value.value)
    if isinstance(value, Highlight):
        return value.graph
    return value if isinstance(value, ColorNodeGraph) else None


def windowed_nodes(value) -> tuple[str, ...]:
    """Nœuds dont la matte de fenêtres sert au rendu de ``value`` (graphe, :class:`Highlight`, :class:`Compare`) : à
    l'appelant d'en donner la source."""
    if isinstance(value, Compare):
        return windowed_nodes(value.value)
    highlight = value.node_id if isinstance(value, Highlight) else None
    graph = value.graph if isinstance(value, Highlight) else value
    if not isinstance(graph, ColorNodeGraph) or (highlight is None and graph.is_identity()):
        return ()
    return tuple(node.id for node in graph.correctors
                 if node.windows and node.enabled and (node.is_active() or node.id == highlight))


class _Streams:
    """Les images intermédiaires (flux numérotés, 0 : l'entrée) et les filtres qui les produisent ; :meth:`text`
    insère un ``split`` derrière toute image utilisée plusieurs fois."""

    def __init__(self, tag: str) -> None:
        self.tag = tag
        self.ops: list[tuple[tuple[int, ...], str, int]] = []
        self.external: dict[int, str] = {}
        self.count = 1

    def op(self, inputs: tuple[int, ...], filters: str) -> int:
        output = self.count
        self.count += 1
        self.ops.append((inputs, filters, output))
        return output

    def label(self, name: str) -> int:
        """Un flux venu d'ailleurs dans le graphe de l'appelant (matte de fenêtres)."""
        stream = self.count
        self.count += 1
        self.external[stream] = name
        return stream

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

        tag = self.tag
        chains = [f"format=rgba,split=2[{tag}c][{tag}al]", f"[{tag}al]alphaextract[{tag}a]", f"[{tag}c]null" + produce(0)]
        for stream, name in self.external.items():
            chains.append(f"[{name}]null" + produce(stream) if uses[stream] else f"[{name}]nullsink")
        for inputs, filters, output in ops:
            sources = "".join(f"[{labels[stream].pop(0)}]" for stream in inputs)
            chains.append(f"{sources}{filters}{produce(output)}")
        chains.append(f"[{labels[result].pop(0)}]format=rgba[{tag}x]")
        chains.append(f"[{tag}x][{tag}a]alphamerge")
        return ";".join(chains)


def render_filters(value, tag: str, linear: Callable[[ColorGrade], str], *,
                   windows: Mapping[str, WindowSource] | None = None, pixel_scale: float = 1.0) -> str:
    """Filtres d'un graphe de nœuds (ou d'un :class:`Highlight`), insérables dans une chaîne ; vide : rien à faire.

    ``linear`` : la chaîne exacte d'un ``ColorGrade`` (:func:`core.export_engine._build_color_grade_filters`) ;
    ``windows`` : la source de la matte de chaque nœud de :func:`windowed_nodes` ; ``pixel_scale`` : pixels de sortie
    par pixel de la séquence (σ des flous).
    """
    highlight = value.node_id if isinstance(value, Highlight) else None
    graph: ColorNodeGraph = value.graph if isinstance(value, Highlight) else value
    nodes = graph.order()
    if highlight is None and not graph.mixers and not any(
            node.restricted() or node.filters() for node in graph.correctors):
        return ",".join(text for node in nodes if isinstance(node, ColorNode) and node.grades()
                        and (text := linear(node.grade)))
    if highlight is None and graph.is_identity():
        return ""
    sources = dict(windows or {})
    missing = [node_id for node_id in windowed_nodes(value) if node_id not in sources]
    if missing:
        raise WindowSourceMissing(f"Matte des fenêtres absente pour les nœuds {missing}.")
    streams = _Streams(tag)
    source = streams.op((0,), "format=gbrp")
    outputs: dict[str, int] = {}
    layers: dict[str, tuple[int, int]] = {}             # correcteur qualifié → (sa correction, sa clé)
    for node in nodes:
        if isinstance(node, ColorNode):
            image = outputs[upstream] if (upstream := graph.input_of(node.id)) is not None else source
            outputs[node.id], layer = _corrector(streams, node, image, linear, highlight == node.id,
                                                 sources.get(node.id), pixel_scale)
            if layer is not None:
                layers[node.id] = layer
        else:
            outputs[node.id] = _mixer(streams, graph, node, outputs, layers, source)
    return streams.text(outputs[graph.sink.id])


def _corrector(streams: _Streams, node: ColorNode, image: int, linear, show_key: bool,
               window: WindowSource | None, pixel_scale: float) -> tuple[int, tuple[int, int] | None]:
    """Sortie d'un correcteur ; s'il est qualifié ou fenêtré, aussi sa correction et sa clé (pour un mélangeur de
    calques)."""
    chain = linear(node.grade) if node.grades() else ""
    graded = streams.op((image,), f"{chain},format=gbrp") if chain else image
    if node.filters():
        graded = streams.op((graded,), f"{detail_filters(node, pixel_scale)},format=gbrp")
    if graded == image and not show_key:
        if isinstance(window, str):
            streams.label(window)                        # nœud neutre : sa matte ne sert pas
        return image, None
    key = _key(streams, node, image, show_key, window)
    if key is None:
        return graded, None
    base = streams.op((image,), f"{GREY_HIGHLIGHT},format=gbrp") if show_key else image
    return streams.op((base, graded, key), "maskedmerge"), (graded, key)


def _key(streams: _Streams, node: ColorNode, image: int, show_key: bool, window: WindowSource | None) -> int | None:
    """La clé du nœud (``gbrp``, la même valeur dans les trois plans) : qualifieur × fenêtres ; ``None`` sans l'un ni
    l'autre."""
    qualifier = node.qualifier
    key = None
    if qualifier is not None and (qualifier.restricts() or show_key):
        key = streams.op((image,), f"lut3d=file='{_cube(qualifier)}':interp=tetrahedral,format=gbrp")
    if not node.windows or window is None:
        return key
    if isinstance(window, str):
        matte = streams.label(window)
        return matte if key is None else streams.op((key, matte), "blend=all_mode=multiply,format=gbrp")
    coverage = min(1.0, max(0.0, float(window)))
    if key is None:
        level = int(round(coverage * 255))
        return streams.op((image,), f"lutrgb=r={level}:g={level}:b={level},format=gbrp")
    scaled = f"'val*{coverage:.6f}'"
    return streams.op((key,), f"lutrgb=r={scaled}:g={scaled}:b={scaled},format=gbrp")


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
