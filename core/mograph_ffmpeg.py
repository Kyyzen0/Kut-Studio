"""Composition FFmpeg des éléments motion graphics et des masques vidéo.

Appelé par :func:`core.export_engine._compose_plan_graph` : l'export et les
segments d'aperçu fidèles (même graphe) composent donc les calques de la
même façon. Voir ``docs/motion-graphics.md`` pour l'ordre complet.

- ``band`` : flux RGBA posé par ``overlay`` ;
- ``layer`` : flux RGBA → effets du calque → fusion (:func:`blend_onto`) ;
- ``adjustment`` : la composition en dessous est dupliquée, les effets
  s'appliquent à la copie, reposée à travers la couverture du calque ;
- masques d'un clip vidéo : matte rastérisée multipliée à l'alpha du clip.

Fusion
------

``blend`` de FFmpeg ignore l'alpha et exige deux flux de même taille. Le
calque est donc d'abord posé sur un cadre transparent, puis ::

    f = blend(calque, fond)              # en RVB planaire, calque en 1er flux
    sortie = overlay(fond, f avec l'alpha du calque)
           = fond·(1 − α) + f·α          # formule séparable du W3C, comme Qt

Chaque ``overlay`` sort en RVBA (``format=rgb``), comme la composition des pistes : le fond arrive en RVBA, et son
passage en RVB planaire (``gbrp``) pour ``blend`` n'est qu'une recopie, sans conversion ni arrondi de swscale.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from .blend_modes import BlendMode, coerce_blend_mode, ffmpeg_blend_mode
from .mograph_program import GraphicsElement, graphics_program
from .mograph_stream import (
    ensure_qt_gui, frame_grid, span_input_filter, stream_input_filter, write_span_stream, write_stream,
)
from .timecode import ffmpeg_rate

LOGGER = logging.getLogger(__name__)

AddInput = Callable[[str], int]

LIMIT_TO_VISIBLE_SPAN = True
"""Un élément graphique n'entre dans le graphe que pendant ses images visibles (:func:`write_span_stream`). Faux : le
flux couvre toute la durée du rendu, transparent hors de l'élément ; c'est la référence des tests de parité (mêmes
images au bit près)."""


def _fmt(value: float) -> str:
    from .export_engine import _format_seconds

    return _format_seconds(float(value))


def _effect_chain(effects, grade, *, preserve_alpha: bool, pixel_scale: float = 1.0, label: str = "fx") -> list[str]:
    """Filtres des effets puis de l'étalonnage, dans l'ordre de l'export vidéo.

    ``preserve_alpha`` : flux RGBA d'un calque. Couleur en ``yuva444p``
    (l'alpha traverse ``eq``, ``hue``…) ; flou et netteté en alpha
    prémultiplié (pas de franges sombres) ; ``vignette`` (qui perd l'alpha)
    est appliquée à la couleur seule.
    """
    from .effects_model import EffectType
    from .export_engine import _build_clip_effect_filters, _build_color_grade_filters

    chain: list[str] = []
    for index, effect in enumerate(effects):
        text = _build_clip_effect_filters((effect,), pixel_scale, label=f"{label}e{index}")
        if not text:
            continue
        if not preserve_alpha:
            chain.append(text)
        elif effect.type in (EffectType.BLUR, EffectType.SHARPEN):
            chain.append(f"format=yuva444p,premultiply=inplace=1,{text},unpremultiply=inplace=1")
        elif effect.type is EffectType.VIGNETTE:
            chain.append(f"__ALPHA_SAFE__{text}")
        else:
            chain.append(f"format=yuva444p,{text}")
    grade_text = _build_color_grade_filters(grade, tag=f"{label}cg") if grade is not None else ""
    if grade_text and preserve_alpha and ";" in grade_text:
        # Nœuds à branches : leurs mélanges travaillent en RVB sans alpha ; l'alpha du calque passe à côté.
        chain.append(f"__ALPHA_SAFE__{grade_text}")
    elif grade_text:
        chain.append(grade_text if not preserve_alpha else f"format=yuva444p,{grade_text}")
    return chain


def _apply_chain(parts: list[str], source: str, chain: list[str], tag: str) -> str:
    """Applique ``chain`` à ``source`` ; retourne le label résultant."""
    current = source
    for index, item in enumerate(chain):
        out = f"{tag}fx{index}"
        if item.startswith("__ALPHA_SAFE__"):
            text = item[len("__ALPHA_SAFE__"):]
            parts.append(
                f"[{current}]format=rgba,split[{out}c][{out}a];"
                f"[{out}a]alphaextract[{out}al];"
                f"[{out}c]{text},format=rgba[{out}x];"
                f"[{out}x][{out}al]alphamerge[{out}]"
            )
        else:
            parts.append(f"[{current}]{item},format=rgba[{out}]")
        current = out
    return current


@dataclass(frozen=True)
class SpanWindow:
    """Place d'un calque limité à ses images visibles (:class:`~core.mograph_stream.SpanStream`) dans la composition.

    Rangs d'images sur la grille du rendu : la composition va de ``first`` à ``count`` (exclu), le calque de ``start`` à
    ``end`` (exclu), avec ``first <= start < end <= count``."""

    first: int
    start: int
    end: int
    count: int
    width: int
    height: int
    fps: float

    def seconds(self, frame: int) -> str:
        """Instant à mi-chemin entre l'image ``frame - 1`` et ``frame`` : une borne de ``trim`` ou de ``between``
        sans ambiguïté d'arrondi."""
        return _fmt((frame - 0.5) / float(self.fps))


def blend_onto(
    parts: list[str], bottom: str, top: str, mode: BlendMode, out: str, tag: str, *, transparent_bottom: bool = False,
    window: SpanWindow | None = None,
) -> None:
    """Fusionne le flux RGBA ``top`` (taille du cadre) sur ``bottom``.

    ``transparent_bottom`` : le dessous est le cadre **transparent** d'une séquence imbriquée. Un mode de fusion
    n'a rien à fusionner là où il n'y a rien : le calque s'y affiche tel quel (comme en mode Normal), et le
    mélange n'agit qu'à proportion de l'opacité du dessous (formule W3C : ``(1-αb)·Cs + αb·B(Cb, Cs)``).
    Sans cela, un Produit sur du vide donnait du noir.

    ``window`` : ``top`` ne couvre que les images où le calque est visible (voir :func:`_blend_window`). Pas de fusion
    d'un tel calque sur un fond transparent (mode Normal excepté).
    """
    mode = coerce_blend_mode(mode)
    if mode is BlendMode.NORMAL:
        # Hors de ``top`` (avant sa première image, après sa fin), ``overlay`` laisse passer le dessous.
        parts.append(f"[{bottom}][{top}]overlay=0:0:eof_action=pass:format=rgb[{out}]")
        return
    if window is not None:
        if transparent_bottom:
            raise ValueError("Un calque limité à sa durée ne se fusionne pas sur un fond transparent.")
        _blend_window(parts, bottom, top, mode, out, tag, window)
        return
    if transparent_bottom:
        parts.append(
            f"[{top}]format=rgba,split=3[{tag}t1][{tag}t2][{tag}t3];"
            f"[{tag}t2]alphaextract[{tag}ta];"
            f"[{bottom}]split=4[{tag}b1][{tag}b2][{tag}b3][{tag}b4];"
            f"[{tag}b2]format=gbrp[{tag}bp];"
            f"[{tag}t1]format=gbrp[{tag}tp];"
            f"[{tag}tp][{tag}bp]blend=all_mode={ffmpeg_blend_mode(mode)}:shortest=0:repeatlast=1,"
            f"format=gbrap[{tag}f];"
            f"[{tag}f][{tag}ta]alphamerge[{tag}fa];"
            f"[{tag}b1][{tag}fa]overlay=0:0:eof_action=pass:format=rgb[{tag}mix];"    # résultat fusionné
            f"[{tag}b3][{tag}t3]overlay=0:0:eof_action=pass:format=rgb[{tag}plain];"  # résultat en mode Normal
            f"[{tag}b4]alphaextract,format=gbrp[{tag}ab];"                            # opacité du dessous, comme pondération
            f"[{tag}plain]split[{tag}n1][{tag}n2];"
            f"[{tag}n2]alphaextract[{tag}na];"
            f"[{tag}n1]format=gbrp[{tag}np];"
            f"[{tag}mix]format=gbrp[{tag}mp];"
            f"[{tag}np][{tag}mp][{tag}ab]maskedmerge,format=gbrp[{tag}mm];"
            f"[{tag}mm][{tag}na]alphamerge[{out}]"
        )
        return
    parts.append(
        f"[{top}]format=rgba,split[{tag}t1][{tag}t2];"
        f"[{tag}t2]alphaextract[{tag}ta];"
        f"[{bottom}]split[{tag}b1][{tag}b2];"
        f"[{tag}b2]format=gbrp[{tag}bp];"
        f"[{tag}t1]format=gbrp[{tag}tp];"
        f"[{tag}tp][{tag}bp]blend=all_mode={ffmpeg_blend_mode(mode)}:shortest=0:repeatlast=1,"
        f"format=gbrap[{tag}f];"
        f"[{tag}f][{tag}ta]alphamerge[{tag}fa];"
        f"[{tag}b1][{tag}fa]overlay=0:0:eof_action=pass:format=rgb[{out}]"
    )


def _blend_window(parts: list[str], bottom: str, top: str, mode: BlendMode, out: str, tag: str,
                  window: SpanWindow) -> None:
    """Fusion d'un calque limité à ses images visibles : la chaîne de fusion ne travaille que sur elles.

    La fusion se calcule à partir du dessous ; elle n'existe donc qu'une fois le dessous arrivé à sa première image.
    Sans précaution, ``overlay`` attendrait cette image en retenant toutes celles du dessous (8 Mo l'image en 1080p).
    Le résultat de la fusion est donc complété, avant et après, par des images transparentes d'une source indépendante
    (``color``, dessinée une fois) : ``concat`` les lit l'une après l'autre, sans rien attendre. ``concat`` réécrit les
    horodatages selon les versions de FFmpeg (base 1/1 000 000 en 7.1) : ils sont refaits d'après le rang de chaque image
    (``setpts=N+first``), exact puisque chaque tronçon a son nombre d'images. Hors de la fenêtre, ``overlay`` est
    désactivé (``enable``) : il laisse passer le dessous sans le parcourir.

    Images identiques, au bit près, à celles de la fusion du flux complet (``tests/test_mograph_span.py``)."""
    rate = ffmpeg_rate(window.fps)
    size = f"{window.width}x{window.height}"
    start, end = window.seconds(window.start), window.seconds(window.end)
    # Le dessous est coupé au rang d'image, compté depuis la première image de la composition : une coupe en
    # secondes (``trim=end=``) perdait la dernière image de la composition.
    cut = [f"start_frame={window.start - window.first}"] if window.start > window.first else []
    if window.end < window.count:
        cut.append(f"end_frame={window.end - window.first}")
    trim = f"trim={':'.join(cut)}," if cut else ""
    parts.append(
        f"[{top}]format=rgba,split[{tag}t1][{tag}t2];"
        f"[{tag}t2]alphaextract[{tag}ta];"
        f"[{bottom}]split[{tag}b1][{tag}b2];"
        f"[{tag}b2]{trim}format=gbrp[{tag}bp];"
        f"[{tag}t1]format=gbrp[{tag}tp];"
        f"[{tag}tp][{tag}bp]blend=all_mode={ffmpeg_blend_mode(mode)}:shortest=0:repeatlast=1,"
        f"format=gbrap[{tag}f];"
        f"[{tag}f][{tag}ta]alphamerge,format=rgba,setsar=1[{tag}mid]"
    )
    pieces: list[str] = []
    for name, frames in (("pre", window.start - window.first), ("mid", 0), ("post", window.count - window.end)):
        if name == "mid":
            pieces.append(f"[{tag}mid]")
        elif frames > 0:
            parts.append(f"color=c=black@0:s={size}:r={rate},format=rgba,trim=end_frame={frames},setsar=1[{tag}{name}]")
            pieces.append(f"[{tag}{name}]")
    merged = f"{tag}mid"
    if len(pieces) > 1:
        merged = f"{tag}fa"
        parts.append(
            f"{''.join(pieces)}concat=n={len(pieces)}:v=1:a=0,settb=expr=1/({rate}),setpts=N+{window.first}[{merged}]"
        )
    parts.append(
        f"[{tag}b1][{merged}]overlay=0:0:eof_action=pass:format=rgb:enable='between(t,{start},{end})'[{out}]"
    )


def _stream_label(parts, add_input, path, fps, duration, label, origin: float = 0.0) -> str:
    index = add_input(path)
    parts.append(f"[{index}:v]{stream_input_filter(fps, duration, origin)}[{label}]")
    return label


def compose_graphics(
    parts: list[str],
    plan,
    width: int,
    height: int,
    fps,
    video_label: str,
    add_input: AddInput,
    *,
    prefix: str = "",
    quality: str = "export",
    duration: float,
    nested: bool = False,
    origin: float = 0.0,
    horizon: float | None = None,
) -> str:
    """Compose la pile motion graphics de ``plan`` au-dessus de ``video_label``.

    ``nested`` : le dessous est le cadre **transparent** d'une séquence imbriquée (et non un fond opaque).
    ``origin`` : ``video_label`` ne commence qu'à cet instant (segment d'aperçu) ; aucune image de calque n'est
    rastérisée avant, et un élément terminé avant lui n'entre pas dans le graphe.
    ``horizon`` : dernier instant utile (extraction d'une image) ; rien n'est rastérisé au-delà, et un élément qui
    commence après n'entre pas dans le graphe.
    """
    layers = getattr(plan, "graphics_layers", ()) or ()
    if not any(getattr(layer, "role", "draw") == "draw" for layer in layers):
        return video_label
    ensure_qt_gui()
    from .mograph_raster import MographRenderer, scene_for_plan

    scene = scene_for_plan(plan)
    renderer = MographRenderer(
        scene, width, height, fps=float(fps), quality=quality,
        motion_blur=getattr(plan, "motion_blur", None),
    )
    program = graphics_program(scene)
    first, count = frame_grid(float(fps), duration, origin)
    current = video_label
    p = prefix
    for index, element in enumerate(program):
        tag = f"{p}mg{index}"
        out = f"{p}mgout{index}"
        if element.end <= origin:
            continue  # fini avant la première image utile : il n'en touche aucune
        start = max(element.start, origin)
        end = element.end if horizon is None else min(element.end, horizon)
        if end <= start:
            continue  # commence après la dernière image utile
        if element.kind == "adjustment":
            current = _compose_adjustment(
                parts, renderer, element, current, add_input, fps, duration, tag, out, nested=nested,
                pixel_scale=_pixel_scale(renderer, plan), start=start, end=end, origin=origin,
            )
            continue
        ids = element.layer_ids
        apply_blend = element.kind == "band"
        stream = dict(
            width=width, height=height, fps=float(fps), duration=duration,
            start=start, end=end,
            frame_key=lambda t, ids=ids: renderer.frame_key(ids, t) if renderer.any_active(ids, t) else None,
            render=lambda t, ids=ids, blend=apply_blend: renderer.render(ids, t, blend_modes=blend),
            salt=element.kind,
        )
        # Limité à ses images visibles, un élément ne coûte rien ailleurs (les effets du calque ne dépendent que de
        # l'image et de son instant ``T``, gardé tel quel). Seule la fusion sur fond transparent garde le flux complet.
        window = None
        if LIMIT_TO_VISIBLE_SPAN and (not nested or coerce_blend_mode(element.blend) is BlendMode.NORMAL):
            span = write_span_stream(**stream, first_frame=first)
            if span is None:
                continue  # jamais visible : le dessous passe tel quel
            window = SpanWindow(first, span.first_frame, span.first_frame + span.frames, count, width, height,
                                float(fps))
            label = f"{tag}s"
            parts.append(f"[{add_input(span.playlist)}:v]{span_input_filter(fps, span)}[{label}]")
        else:
            label = _stream_label(parts, add_input, write_stream(**stream), fps, duration, f"{tag}s", origin)
        chain = _effect_chain(element.effects, element.color_grade, preserve_alpha=True,
                              pixel_scale=_pixel_scale(renderer, plan), label=tag)
        label = _apply_chain(parts, label, chain, tag)
        blend_onto(parts, current, label, element.blend, out, tag, transparent_bottom=nested, window=window)
        current = out
    return current


def _pixel_scale(renderer, plan) -> float:
    """Pixels du rendu par pixel de la séquence (voir ``_build_clip_effect_filters``)."""
    return renderer.width / float(max(1, getattr(plan, "width", 0) or renderer.width))


def _compose_adjustment(
    parts, renderer, element: GraphicsElement, current: str, add_input, fps, duration, tag, out,
    *, nested: bool = False, pixel_scale: float = 1.0, start: float | None = None, end: float | None = None,
    origin: float = 0.0,
) -> str:
    clip_id = element.clip_id
    path = write_stream(
        width=renderer.width, height=renderer.height, fps=float(fps), duration=duration,
        start=element.start if start is None else start, end=element.end if end is None else end,
        frame_key=lambda t: renderer.coverage_key(clip_id, t),
        render=lambda t: renderer.render_coverage(clip_id, t),
        salt="adjustment",
    )
    coverage = _stream_label(parts, add_input, path, fps, duration, f"{tag}cov", origin)
    chain = _effect_chain(element.effects, element.color_grade, preserve_alpha=False, pixel_scale=pixel_scale,
                          label=tag)
    if nested:
        # Le dessous est transparent là où la séquence imbriquée est vide : l'ajustement ne doit rien créer
        # à cet endroit (sinon le noir des effets opaques masquerait la piste parente). Sa couverture est
        # donc multipliée par l'alpha du dessous.
        parts.append(f"[{current}]split=3[{tag}a][{tag}b][{tag}c]")
        parts.append(f"[{tag}c]alphaextract[{tag}ba]")
        processed = _apply_chain(parts, f"{tag}b", chain, tag) if chain else f"{tag}b"
        parts.append(
            f"[{coverage}]alphaextract[{tag}ca];"
            f"[{tag}ca][{tag}ba]blend=all_mode=multiply:shortest=0:repeatlast=1[{tag}al];"
            f"[{processed}]format=rgba[{tag}pr];"
            f"[{tag}pr][{tag}al]alphamerge[{tag}pa];"
            f"[{tag}a][{tag}pa]overlay=0:0:eof_action=pass:format=rgb[{out}]"
        )
        return out
    parts.append(f"[{current}]split[{tag}a][{tag}b]")
    processed = _apply_chain(parts, f"{tag}b", chain, tag) if chain else f"{tag}b"
    parts.append(
        f"[{coverage}]alphaextract[{tag}ca];"
        f"[{processed}]format=rgba[{tag}pr];"
        f"[{tag}pr][{tag}ca]alphamerge[{tag}pa];"
        f"[{tag}a][{tag}pa]overlay=0:0:eof_action=pass:format=rgb[{out}]"
    )
    return out


def video_matte_label(
    parts: list[str], layer, width: int, height: int, fps, add_input: AddInput, label: str
) -> str | None:
    """Matte (niveaux de gris) des masques d'un clip vidéo, en temps local.

    ``None`` sans masque. Le flux commence au début du clip et dure ce que
    dure le clip ; FFmpeg l'applique en espace calque (avant échelle et
    rotation).
    """
    compositing = getattr(layer, "compositing", None)
    if compositing is None or not getattr(compositing, "masks", ()):
        return None
    ensure_qt_gui()
    from .mograph_raster import render_layer_matte
    from .mograph_scene import GraphicsScene
    from .render_plan import GraphicLayer

    duration = max(1.0 / float(fps or 30), layer.timeline_end - layer.timeline_start)
    rig = GraphicLayer(
        clip_id=layer.clip_id, track_id=layer.track_id, track_index=layer.track_index,
        timeline_start=0.0, timeline_end=duration, graphic=None,
        animation=tuple(getattr(layer, "animation", ()) or ()), compositing=compositing,
    )
    scene = GraphicsScene((rig,), width, height)

    def key(t: float):
        return ("matte", repr(scene.evaluate(layer.clip_id, t).masks))

    path = write_stream(
        width=width, height=height, fps=float(fps), duration=duration, start=0.0, end=duration,
        frame_key=key, render=lambda t: render_layer_matte(scene, layer.clip_id, t, width, height),
        salt="matte",
    )
    index = add_input(path)
    parts.append(f"[{index}:v]{stream_input_filter(fps, duration)},alphaextract[{label}]")
    return label


def needs_graphics_preparation(plan) -> bool:
    """Le plan contient-il des calques ou masques à rastériser avant FFmpeg ?"""
    plans = [plan] + [entry.plan for entry in getattr(plan, "nested_sequences", ()) or ()]
    for current in plans:
        if any(getattr(layer, "role", "draw") == "draw" for layer in getattr(current, "graphics_layers", ()) or ()):
            return True
        for layer in current.video_layers:
            if getattr(getattr(layer, "compositing", None), "masks", ()):
                return True
    return False


def prepare_graphics_streams(plan, width: int, height: int, fps) -> bool:
    """Rend à l'avance les images des calques (cache disque) ; sûr dans un fil.

    Construit le graphe d'export une première fois : toutes les images
    nécessaires sont écrites dans le cache, le lancement de FFmpeg n'a
    ensuite plus qu'à relire les listes ``.ffconcat``. Une erreur ici n'est
    pas fatale : elle se reproduira, et sera signalée, au lancement.
    """
    try:
        from .export_engine import ExportEngine

        ExportEngine._build_filter_complex(plan, width, height, fps, None)
        return True
    except Exception:
        LOGGER.debug("Graphe d'export non construit : flux motion graphics non préparés", exc_info=True)
        return False


__all__ = [
    "blend_onto", "compose_graphics", "needs_graphics_preparation", "prepare_graphics_streams",
    "video_matte_label",
]
