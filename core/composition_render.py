"""Rendu d'une composition nodale : son graphe de nœuds devient un sous-graphe FFmpeg de l'export (donc de l'aperçu
fidèle), exact, fait des briques de l'export lui-même.

Chaque image du graphe est un flux RVBA **de la taille du cadre**, sur toute la durée de la composition (temps de la
composition : 0 au début), transparent là où rien n'est posé :

- **média**, **calque graphique** : un sous-plan d'un seul clip (:class:`core.render_plan.CompositionRender`), composé
  comme une séquence imbriquée (fond transparent) — mêmes adaptation au cadre, rastérisation et proxies que la
  timeline ;
- **couleur unie** : ``color`` ;
- **transformation** : l'image posée comme un clip l'est sur sa piste, par les mêmes filtres
  (:func:`core.export_engine._build_layer_filter` : échelle, miroirs, rotation, opacité animés ; puis ``overlay`` à sa
  position, l'ancrage compris) ;
- **fusion** : :func:`core.mograph_ffmpeg.blend_onto` (formules W3C, fond transparent compris), l'opacité du premier
  plan multipliant son alpha ;
- **masque** : la matte rastérisée comme celle des masques d'un clip (:func:`core.mograph_ffmpeg.video_matte_label`,
  animation ``mask.<id>.*`` de la composition), multipliée à l'alpha ;
- **incrustation** : ``chromakey`` / ``despill`` (:func:`core.compositing.chroma_key_filters`) ;
- **effets**, **étalonnage** : la chaîne des calques graphiques (:func:`core.mograph_ffmpeg._effect_chain`, alpha
  gardé).

Une image lue par plusieurs nœuds passe par un ``split`` ; un nœud sans entrée rend le cadre transparent ; un nœud
que rien ne relie à la sortie n'est pas compilé.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable

from .composition import (
    BACKGROUND_PORT,
    FOREGROUND_PORT,
    EffectsNode,
    GradeNode,
    GraphicNode,
    KeyNode,
    MaskNode,
    MediaNode,
    MergeNode,
    OutputNode,
    SolidNode,
    TransformNode,
)


def compile_composition(
    parts: list[str], entry, width: int, height: int, fps: float, take_video: Callable[[str], str],
    add_input, *, tag: str, pixel_scale: float = 1.0,
) -> str:
    """Ajoute à ``parts`` le sous-graphe de la composition ``entry`` (:class:`core.render_plan.NestedSequencePlan`) et
    rend le label de son image. ``take_video(clé)`` : le label du rendu d'une source (lu une fois)."""
    from .compositing import Compositing, chroma_key_filters
    from .export_engine import _build_layer_filter, _build_overlay_args, _format_seconds, ffmpeg_rate
    from .mograph_ffmpeg import _apply_chain, _effect_chain, blend_onto, video_matte_label
    from .render_plan import RenderLayer

    composition = entry.composition
    graph = composition.graph
    duration = max(float(entry.plan.duration), 1.0 / float(fps or 30))
    rate = ffmpeg_rate(fps)
    seconds = _format_seconds(duration)
    sink = composition.sink if composition.sink and graph.has_node(composition.sink) else graph.output.id
    nodes = graph.rendered(sink)
    ids = {node.id for node in nodes}
    uses = Counter(link.source for link in graph.links if link.target in ids and link.source in ids)
    labels: dict[str, list[str]] = {}
    counter = iter(range(1_000_000))

    def canvas(name: str, color: str = "black@0") -> str:
        parts.append(f"color=c={color}:s={width}x{height}:r={rate}:d={seconds},format=rgba[{name}]")
        return name

    def publish(node_id: str, label: str) -> None:
        """Le label de la sortie d'un nœud, dédoublé pour chacun de ses lecteurs."""
        count = uses[node_id]
        if count <= 1:
            labels[node_id] = [label]
            return
        names = [f"{label}_{index}" for index in range(count)]
        parts.append(f"[{label}]split={count}" + "".join(f"[{name}]" for name in names))
        labels[node_id] = names

    def read(node_id: str, port: int = 0) -> str | None:
        source = graph.input_of(node_id, port)
        if source is None or source not in labels or not labels[source]:
            return None
        return labels[source].pop(0)

    def fresh(stem: str) -> str:
        return f"{tag}{stem}{next(counter)}"

    def finish(label: str) -> str:
        final = fresh("out")
        parts.append(f"[{label}]format=rgba,setsar=1[{final}]")
        return final

    def _produce(node) -> str:
        """Le label de l'image du nœud (ses entrées déjà lues)."""
        out = fresh(f"{node.id}_")
        if isinstance(node, (MediaNode, GraphicNode)):
            key = composition.source_key(node.id)
            if key is None:
                return canvas(out)
            parts.append(f"[{take_video(key)}]null[{out}]")
            return out
        if isinstance(node, SolidNode):
            return canvas(out, f"0x{node.color[1:]}")
        if isinstance(node, MergeNode):
            background = read(node.id, BACKGROUND_PORT) or canvas(fresh("bg"))
            foreground = read(node.id, FOREGROUND_PORT)
            if foreground is None:
                parts.append(f"[{background}]null[{out}]")
                return out
            if node.opacity < 1.0:
                faded = fresh("fade")
                parts.append(f"[{foreground}]format=rgba,colorchannelmixer=aa={_format_seconds(node.opacity)}"
                             f"[{faded}]")
                foreground = faded
            blend_onto(parts, background, foreground, node.blend, out, fresh("blend"), transparent_bottom=True)
            return out
        image = read(node.id)
        if image is None:                                      # rien en entrée : rien en sortie
            return canvas(out)
        if isinstance(node, TransformNode):
            placed = layer(node.id, node.transform, node.keyframes)
            moved = fresh("moved")
            parts.append(_build_layer_filter(0, placed, None, width, height, fps, source=image, label=moved,
                                             pad_color="black@0", add_input=add_input, pixel_scale=pixel_scale))
            base = canvas(fresh("place"))
            parts.append(f"[{base}][{moved}]overlay={_build_overlay_args(placed, width, height)}:format=rgb[{out}]")
        elif isinstance(node, MaskNode):
            if not node.masks:
                parts.append(f"[{image}]null[{out}]")
            else:
                matte_parts: list[str] = []
                matte = video_matte_label(matte_parts, layer(node.id, masks=node.masks), width, height, fps,
                                          add_input, fresh("matte"))
                parts.extend(matte_parts)
                t = fresh("m")
                parts.append(
                    f"[{image}]format=rgba,split[{t}c][{t}a];[{t}a]alphaextract[{t}al];"
                    f"[{t}al][{matte}]blend=all_mode=multiply:shortest=0:repeatlast=1[{t}na];"
                    f"[{t}c][{t}na]alphamerge[{out}]"
                )
        elif isinstance(node, KeyNode):
            filters = chroma_key_filters(Compositing(chroma_key=node.key))
            parts.append(f"[{image}]{','.join(['format=rgba', *filters, 'format=rgba'])}[{out}]")
        elif isinstance(node, EffectsNode):
            chain = _effect_chain(node.effects, None, preserve_alpha=True, pixel_scale=pixel_scale,
                                  label=fresh("fx"))
            parts.append(f"[{_apply_chain(parts, image, chain, fresh('fx'))}]null[{out}]")
        elif isinstance(node, GradeNode):
            chain = _effect_chain((), node.grade, preserve_alpha=True, pixel_scale=pixel_scale, label=fresh("cg"))
            parts.append(f"[{_apply_chain(parts, image, chain, fresh('cg'))}]null[{out}]")
        else:                                                  # pragma: no cover - type connu du modèle
            raise ValueError(f"Nœud de composition inconnu : {node!r}.")
        return out

    def layer(node_id: str, transform=None, keyframes=(), *, masks=()):
        """Le « clip » d'une étape : toute la composition, le transform du nœud, ses masques et leur animation."""
        from .visual_effects import ClipTransform

        return RenderLayer(
            clip_id=f"{tag}{node_id}", asset_id="", track_id="", track_index=0, source_path="", source_in=0.0,
            source_out=duration, timeline_start=0.0, timeline_end=duration, source_fps=float(fps),
            transform=transform or ClipTransform(), transform_keyframes=tuple(keyframes),
            source_frames=max(1, int(round(duration * float(fps)))), source_width=width, source_height=height,
            compositing=Compositing(masks=tuple(masks)), animation=tuple(composition.animation),
        )

    for node in nodes:
        if isinstance(node, OutputNode):
            image = read(node.id)
            return finish(image if image is not None else canvas(fresh("empty")))
        produced = _produce(node)
        if node.id == sink:                                    # aperçu d'un nœud : son image est celle du clip
            return finish(produced)
        publish(node.id, produced)
    raise ValueError("Composition sans sortie.")                # pragma: no cover - le graphe en a toujours une


__all__ = ["compile_composition"]
