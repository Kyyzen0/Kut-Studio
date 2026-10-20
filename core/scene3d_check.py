"""Vérification préalable à l'export des clips « scène 3D » (ADR-0009) : **strict à l'export**.

``core.scene3d.problem`` (appelé par ``build_render_plan``) ne fait que LIRE la scène : un fichier manquant ou mal formé donne une
carte à l'écran et ``missing_media``. Une dépendance qui existe mais ne se **rend** pas (texture PNG tronquée, scène hors des
bornes du moteur) n'y apparaît pas ; à l'écran la scène reste alors vide, mais un fichier exporté ne doit pas l'omettre en silence.

:func:`export_problems` décode donc les dépendances de rendu et fait un rendu d'essai minuscule de chaque scène distincte du plan.
Coût : une lecture par texture distincte (le cache de ``kut3d.render.images`` évite de la refaire au vrai rendu) et un rendu
de :data:`PROBE_SIZE` pixels par scène et instant distincts ; jamais appelé par ``build_render_plan`` (rafraîchi à chaque
changement de l'aperçu) mais par ``core.export_engine.check_export_request``, point d'entrée commun de l'export de l'application,
de ``run_export`` (ligne de commande, scripts, ``Film.export``).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .scene3d import Scene3DError, load_scene, render_failures, scene_time_at

PROBE_SIZE = (16, 16)
"""Taille du rendu d'essai : les bornes de ``kut3d`` qui dépendent de la scène (triangles, instances) y sont déjà éprouvées."""

MAX_PROBES = 64
"""Au plus ce nombre de rendus d'essai par export (scènes et instants distincts) ; les suivants ne sont pas éprouvés."""

MAX_REPORTED = 5
"""Au plus ce nombre de scènes nommées dans le message de refus."""

_TEXTURE_FIELDS = (
    "base_color_texture", "normal_texture", "metallic_roughness_texture", "emissive_texture", "occlusion_texture",
)


def _plans(plan: Any) -> Iterator[Any]:
    yield plan
    for entry in getattr(plan, "nested_sequences", ()) or ():
        nested = getattr(entry, "plan", None)
        if nested is not None:
            yield nested


def _scene_layers(plan: Any) -> Iterator[Any]:
    for current in _plans(plan):
        for layer in getattr(current, "graphics_layers", ()) or ():
            graphic = getattr(layer, "graphic", None)
            if getattr(graphic, "type", None) == "scene3d" and getattr(layer, "role", "draw") == "draw":
                yield layer


def _decode_textures(scene: Any) -> None:
    """Décode chaque texture distincte des modèles de la scène (lève ``ImageDecodeError`` à la première illisible)."""
    from kut3d.errors import RenderError
    from kut3d.render.images import load_texture_image

    seen: set[int] = set()
    for instance in scene.instances:
        for material in instance.model.materials:
            for field in _TEXTURE_FIELDS:
                ref = getattr(material, field, None)
                if ref is not None and id(ref) not in seen:
                    seen.add(id(ref))
                    try:
                        load_texture_image(ref)
                    except RenderError as exc:
                        label = str(getattr(ref, "name", "") or field)
                        raise Scene3DError(f"texture « {label[:60]} » : {exc}") from exc


def _probe(path: str, source_in: float) -> None:
    """Décode les textures puis rend la scène en miniature (lève une erreur de rendu)."""
    import kut3d

    scene, _animated = load_scene(path)
    _decode_textures(scene)
    kut3d.render_frame(
        scene, t=scene_time_at(path, source_in, 0.0), size=PROBE_SIZE, backend="numpy", samples=1,
    )


def _label(layer: Any) -> str:
    name = Path(str(layer.graphic.source_path)).name
    name = name if len(name) <= 80 else name[:79] + "…"
    clip = str(getattr(layer, "label", "") or getattr(layer, "clip_id", ""))
    clip = clip if len(clip) <= 60 else clip[:59] + "…"
    return f"« {name} » (clip « {clip} »)"


def export_problems(plan: Any) -> list[str]:
    """Les scènes 3D du plan (imbriquées comprises) qui ne se rendront pas : une phrase en français chacune, bornée.

    Liste vide si tout se rend. Ne lève jamais pour une scène : l'erreur de rendu devient la phrase.
    """
    problems: list[str] = []
    probed: set[tuple[str, float]] = set()
    failures = render_failures()
    for layer in _scene_layers(plan):
        path = str(layer.graphic.source_path)
        source_in = round(float(getattr(layer, "source_in", 0.0)), 4)
        key = (path, source_in)
        if key in probed or len(probed) >= MAX_PROBES:
            continue
        probed.add(key)
        try:
            _probe(path, source_in)
        except failures as exc:
            cause = " ".join(str(exc).split()).rstrip(".") or type(exc).__name__
            cause = cause if len(cause) <= 160 else cause[:159] + "…"
            text = f"{_label(layer)} : {cause}"
            if text not in problems:
                problems.append(text)
    return problems


def check_export_scenes(plan: Any) -> None:
    """Refuse l'export (:class:`~core.scene3d.Scene3DError`, une ``ValueError``) si une scène 3D du plan ne se rend pas."""
    problems = export_problems(plan)
    if not problems:
        return
    shown = problems[:MAX_REPORTED]
    more = f" (et {len(problems) - len(shown)} autre(s))" if len(problems) > len(shown) else ""
    raise Scene3DError(
        "Scène 3D non rendable : " + " ; ".join(shown) + more + ". Corrigez ou supprimez les clips concernés avant d'exporter."
    )


__all__ = ["MAX_PROBES", "MAX_REPORTED", "PROBE_SIZE", "check_export_scenes", "export_problems"]
