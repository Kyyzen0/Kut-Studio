"""Graphe d'étalonnage spatial dans le moniteur GPU : des passes LUT, clé, flou, netteté et mélange.

Un graphe dont chaque nœud ne dépend que de la couleur du pixel est cuit en **une** LUT 3D (:mod:`core.gpu_grade`).
Dès qu'un nœud a une fenêtre, un flou ou une netteté (:meth:`~core.color_nodes.ColorNodeGraph.is_spatial`), ce n'est
plus possible : :func:`compile_program` décrit alors le graphe nœud par nœud, comme le sous-graphe de l'export
(:mod:`core.color_render`), en opérations sur des images numérotées (« emplacements », 0 : le calque après ses
effets) :

- ``lut`` : une chaîne de filtres de l'export (le réglage d'un nœud, le gris de *Afficher la sélection*), cuite en LUT
  par FFmpeg, comme pour un graphe sans fenêtre — l'étalonnage reste celui de l'export ;
- ``key`` : la clé d'un nœud, LUT de son qualifieur (cuite de la même table ``.cube`` que l'export) × matte de ses
  fenêtres (rastérisée par le code de l'export, :mod:`core.color_windows`) ;
- ``blur`` / ``sharpen`` : les passes de voisinage des effets, sur les trois canaux RVB ;
- ``mix`` : ``A + K·(B − A)`` (``maskedmerge``) ; ``add`` : ``A + (B − S)``, écrêté à la dernière branche (``mix`` aux
  poids ``1 … 1 −(n−1)``).

Ce module ne parle à aucun GPU et ne cuit rien : :func:`core.gpu_composite.plan_frame` traduit les opérations en
passes, une fois les LUT cuites et les mattes rastérisées par l'interface (:class:`GpuColorPass`).
"""

from __future__ import annotations

from dataclasses import dataclass

from .color_grading import ColorGrade
from .color_nodes import ColorMixer, ColorNode, ColorNodeGraph, MixerKind
from .color_render import GREY_HIGHLIGHT, FilterChain, Highlight, _cube

INPUT = 0
"""L'emplacement de l'image d'entrée : le calque après ses effets (YUV pour une vidéo YUV)."""


@dataclass(frozen=True)
class ColorStep:
    """Une opération du graphe : ``op`` sur les emplacements ``inputs``, résultat dans ``target``.

    ``bake`` : la chaîne à cuire (``lut``, et ``key`` avec qualifieur) ; ``window`` : le nœud dont la matte des fenêtres
    multiplie la clé ; ``amount`` : σ du flou (pixels de la séquence) ou force de la netteté ; ``clamp`` : écrêter le
    résultat d'une somme de branches.
    """

    op: str
    target: int
    inputs: tuple[int, ...]
    bake: FilterChain | None = None
    window: str = ""
    amount: float = 0.0
    clamp: bool = False


@dataclass(frozen=True)
class ColorProgram:
    steps: tuple[ColorStep, ...]
    result: int

    def bakes(self) -> tuple[FilterChain, ...]:
        """Les chaînes à cuire (une LUT chacune), sans doublon."""
        return tuple(dict.fromkeys(step.bake for step in self.steps if step.bake is not None))

    def windows(self) -> tuple[str, ...]:
        """Les nœuds dont la matte des fenêtres sert."""
        return tuple(dict.fromkeys(step.window for step in self.steps if step.window))


@dataclass(frozen=True)
class GpuColorPass:
    """Une opération prête pour le GPU : ses textures résolues (atlas cuit ``lut``, matte ``window``)."""

    op: str
    target: int
    inputs: tuple[int, ...]
    lut: str = ""
    window: str = ""
    amount: float = 0.0
    clamp: bool = False


def _chain(text: str, grade: ColorGrade | None = None) -> FilterChain:
    """Une chaîne entourée de ``format=gbrp`` comme dans le sous-graphe de l'export (l'entrée y est convertie en RVB
    planaire, la sortie aussi) : la LUT cuite fait les mêmes arrondis."""
    luts = (grade.lut,) if grade is not None and grade.lut is not None else ()
    return FilterChain(f"format=gbrp,{text},format=gbrp", luts=luts)


class _Builder:
    def __init__(self) -> None:
        self.steps: list[ColorStep] = []
        self.count = 1

    def add(self, op: str, inputs: tuple[int, ...], **options) -> int:
        target = self.count
        self.count += 1
        self.steps.append(ColorStep(op, target, inputs, **options))
        return target


def compile_program(value, linear) -> ColorProgram | None:
    """Les opérations du graphe ``value`` (graphe ou :class:`Highlight`) ; ``None`` s'il tient dans une LUT unique.

    ``linear`` : la chaîne exacte d'un ``ColorGrade`` (:func:`core.export_engine._build_color_grade_filters`).
    """
    highlight = value.node_id if isinstance(value, Highlight) else None
    graph = value.graph if isinstance(value, Highlight) else value
    if not isinstance(graph, ColorNodeGraph) or not graph.is_spatial():
        return None
    build = _Builder()
    outputs: dict[str, int] = {}
    layers: dict[str, tuple[int, int]] = {}
    for node in graph.order():
        if isinstance(node, ColorNode):
            image = outputs[upstream] if (upstream := graph.input_of(node.id)) is not None else INPUT
            outputs[node.id], layer = _corrector(build, node, image, linear, highlight == node.id)
            if layer is not None:
                layers[node.id] = layer
        else:
            outputs[node.id] = _mixer(build, graph, node, outputs, layers)
    return ColorProgram(tuple(build.steps), outputs[graph.sink.id])


def _corrector(build: _Builder, node: ColorNode, image: int, linear, show_key: bool):
    graded = image
    if node.grades() and (text := linear(node.grade)):
        graded = build.add("lut", (image,), bake=_chain(text, node.grade))
    if node.enabled and node.blur > 0.0:
        graded = build.add("blur", (graded,), amount=node.blur)
    if node.enabled and node.sharpen > 0.0:
        graded = build.add("sharpen", (graded,), amount=node.sharpen)
    if graded == image and not show_key:
        return image, None
    qualifier = node.qualifier
    use_key = qualifier is not None and (qualifier.restricts() or show_key)
    if not use_key and not node.windows:
        return graded, None
    key = build.add("key", (image,), bake=_chain(f"lut3d=file='{_cube(qualifier)}':interp=tetrahedral")
                    if use_key else None, window=node.id if node.windows else "")
    base = build.add("lut", (image,), bake=_chain(GREY_HIGHLIGHT)) if show_key else image
    return build.add("mix", (base, graded, key)), (graded, key)


def _mixer(build: _Builder, graph: ColorNodeGraph, mixer: ColorMixer, outputs: dict[str, int],
           layers: dict[str, tuple[int, int]]) -> int:
    branches = [link.source for link in graph.inputs(mixer.id)]
    if mixer.kind is MixerKind.PARALLEL:
        split = graph.split_point(mixer.id)
        base = outputs[split] if split is not None else INPUT
        result = outputs[branches[0]]
        for number, branch in enumerate(branches[1:], start=2):
            result = build.add("add", (result, outputs[branch], base), clamp=number == len(branches))
        return result
    result = outputs[branches[0]]
    for branch in branches[1:]:
        if branch in layers:
            graded, key = layers[branch]
            result = build.add("mix", (result, graded, key))
        else:
            result = outputs[branch]
    return result
