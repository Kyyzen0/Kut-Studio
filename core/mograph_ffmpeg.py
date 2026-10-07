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
"""

from __future__ import annotations

from collections.abc import Callable

from .blend_modes import BlendMode, coerce_blend_mode, ffmpeg_blend_mode
from .mograph_program import GraphicsElement, graphics_program
from .mograph_stream import ensure_qt_gui, stream_input_filter, write_stream

AddInput = Callable[[str], int]


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
    grade_text = _build_color_grade_filters(grade) if grade is not None else ""
    if grade_text:
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


def blend_onto(
    parts: list[str], bottom: str, top: str, mode: BlendMode, out: str, tag: str, *, transparent_bottom: bool = False
) -> None:
    """Fusionne le flux RGBA ``top`` (taille du cadre) sur ``bottom``.

    ``transparent_bottom`` : le dessous est le cadre **transparent** d'une séquence imbriquée. Un mode de fusion
    n'a rien à fusionner là où il n'y a rien : le calque s'y affiche tel quel (comme en mode Normal), et le
    mélange n'agit qu'à proportion de l'opacité du dessous (formule W3C : ``(1-αb)·Cs + αb·B(Cb, Cs)``).
    Sans cela, un Produit sur du vide donnait du noir.
    """
    mode = coerce_blend_mode(mode)
    if mode is BlendMode.NORMAL:
        parts.append(f"[{bottom}][{top}]overlay=0:0:eof_action=pass[{out}]")
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
            f"[{tag}b1][{tag}fa]overlay=0:0:eof_action=pass[{tag}mix];"      # résultat fusionné
            f"[{tag}b3][{tag}t3]overlay=0:0:eof_action=pass[{tag}plain];"    # résultat en mode Normal
            f"[{tag}b4]alphaextract,format=gbrp[{tag}ab];"                   # opacité du dessous, comme pondération
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
        f"[{tag}b1][{tag}fa]overlay=0:0:eof_action=pass[{out}]"
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
) -> str:
    """Compose la pile motion graphics de ``plan`` au-dessus de ``video_label``.

    ``nested`` : le dessous est le cadre **transparent** d'une séquence imbriquée (et non un fond opaque).
    ``origin`` : ``video_label`` ne commence qu'à cet instant (segment d'aperçu) ; aucune image de calque n'est
    rastérisée avant, et un élément terminé avant lui n'entre pas dans le graphe.
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
    current = video_label
    p = prefix
    for index, element in enumerate(program):
        tag = f"{p}mg{index}"
        out = f"{p}mgout{index}"
        if element.end <= origin:
            continue  # fini avant la première image utile : il n'en touche aucune
        start = max(element.start, origin)
        if element.kind == "adjustment":
            current = _compose_adjustment(
                parts, renderer, element, current, add_input, fps, duration, tag, out, nested=nested,
                pixel_scale=_pixel_scale(renderer, plan), start=start, origin=origin,
            )
            continue
        ids = element.layer_ids
        apply_blend = element.kind == "band"
        path = write_stream(
            width=width, height=height, fps=float(fps), duration=duration,
            start=start, end=element.end,
            frame_key=lambda t, ids=ids: renderer.frame_key(ids, t) if renderer.any_active(ids, t) else None,
            render=lambda t, ids=ids, blend=apply_blend: renderer.render(ids, t, blend_modes=blend),
            salt=element.kind,
        )
        label = _stream_label(parts, add_input, path, fps, duration, f"{tag}s", origin)
        chain = _effect_chain(element.effects, element.color_grade, preserve_alpha=True,
                              pixel_scale=_pixel_scale(renderer, plan), label=tag)
        label = _apply_chain(parts, label, chain, tag)
        blend_onto(parts, current, label, element.blend, out, tag, transparent_bottom=nested)
        current = out
    return current


def _pixel_scale(renderer, plan) -> float:
    """Pixels du rendu par pixel de la séquence (voir ``_build_clip_effect_filters``)."""
    return renderer.width / float(max(1, getattr(plan, "width", 0) or renderer.width))


def _compose_adjustment(
    parts, renderer, element: GraphicsElement, current: str, add_input, fps, duration, tag, out,
    *, nested: bool = False, pixel_scale: float = 1.0, start: float | None = None, origin: float = 0.0,
) -> str:
    clip_id = element.clip_id
    path = write_stream(
        width=renderer.width, height=renderer.height, fps=float(fps), duration=duration,
        start=element.start if start is None else start, end=element.end,
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
            f"[{tag}a][{tag}pa]overlay=0:0:eof_action=pass[{out}]"
        )
        return out
    parts.append(f"[{current}]split[{tag}a][{tag}b]")
    processed = _apply_chain(parts, f"{tag}b", chain, tag) if chain else f"{tag}b"
    parts.append(
        f"[{coverage}]alphaextract[{tag}ca];"
        f"[{processed}]format=rgba[{tag}pr];"
        f"[{tag}pr][{tag}ca]alphamerge[{tag}pa];"
        f"[{tag}a][{tag}pa]overlay=0:0:eof_action=pass[{out}]"
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
        return False


__all__ = [
    "blend_onto", "compose_graphics", "needs_graphics_preparation", "prepare_graphics_streams",
    "video_matte_label",
]
