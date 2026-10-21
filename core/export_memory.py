"""Mémoire d'un export : estimation de ce que FFmpeg retient, et refus avant de le lancer (défaut D-002 de la recette).

**Le mécanisme (mesuré).** Les clips d'un même média partagent une seule entrée FFmpeg (``_build_input_list``), et les
instances d'une même séquence imbriquée (les segments d'un montage multicam) partagent un seul rendu (``split``). Chaque clip
est une fenêtre ``trim`` de ce flux, empilée dans la chaîne d'``overlay`` du plan. Or ``overlay`` ne produit pas son
premier cadre tant que **chaque** étage n'a pas reçu la première image de son calque : FFmpeg doit donc décoder le flux
partagé jusqu'au début du clip qui commence le plus tard dans la source (``M``), et tout ce que les autres clips de la
famille lisent avant ``M`` attend, en RGBA pleine trame, dans les files d'attente des étages. Mesuré sur FFmpeg 6.1, 7.1 et 9.0 :
la sortie reste à 0 s pendant que la mémoire monte, puis le montage s'exporte. Un seul clip n'a pas cette file d'attente
(1 Go pour 20 min) ; deux morceaux d'un même fichier la créent (une coupe à 20 s : 4 Go ; à 60 s : plus de 7 Go).

    mémoire retenue  ≈  Σ  max(0, min(fin_k, M) − début_k) / (fin_k − début_k) × durée_k × cadence × largeur × hauteur × 4

(somme sur les clips ``k`` d'une famille, ``début``/``fin`` dans la source ; le cadre est celui de la composition). Mesuré :
exact pour un média coupé (tests/test_export_memory.py), de −20 % à +40 % pour un multicam. Cette file ne dépend pas du nombre de segments mais de la **durée de média décodée
avant le clip qui commence le plus tard** : 20 min de multicam à trois angles retiennent environ 200 Go.

Le correctif de fond (donner à chaque clip son propre décodeur, ou composer par tranches de temps) change la structure du
graphe, donc le rendu à valider image par image : c'est la phase 2 (moteur). En attendant, :func:`check_export_memory` refuse
**avant FFmpeg** un export qui ne peut pas tenir, au lieu de remplir le swap puis le disque.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

LOGGER = logging.getLogger("kut_studio.export_memory")

MEMORY_SHARE = 0.85
"""Part de la mémoire physique au-delà de laquelle l'export est refusé : au-dessus, le système écrit en swap et l'export ne
finit pas (le premier essai du banc a rempli le disque). La file estimée est seule : FFmpeg ajoute ~1 Go de décodeurs."""

UNKNOWN_MEMORY_BYTES = 32 * 1024**3
"""Mémoire supposée quand le système ne la dit pas : seuls les montages gigantesques sont alors refusés."""

_BYTES_PER_PIXEL = 4
"""Les calques sont des images RGBA (``format=rgba`` au bout de chaque chaîne de calque)."""

_GIB = 1024**3


@dataclass(frozen=True)
class QueueEstimate:
    """Ce que FFmpeg garde en attente avant son premier cadre de sortie."""

    bytes: int = 0
    frames: float = 0.0
    families: int = 0
    """Nombre de flux partagés (un fichier, une séquence imbriquée) qui retiennent des images."""


def _frame_factor(layer) -> float:
    """Taille relative du cadre d'un calque dans la chaîne (1.0 : plein cadre). Jamais au-dessus de 1 (sous-estimer évite un
    refus à tort) ; un calque animé (images clés) compte plein cadre, il peut grandir."""
    if getattr(layer, "transform_keyframes", ()):
        return 1.0
    transform = layer.transform
    scale_x = max(0.0, min(1.0, float(transform.scale) * float(transform.scale_x)))
    scale_y = max(0.0, min(1.0, float(transform.scale) * float(transform.scale_y)))
    return scale_x * scale_y


def _countable(layer) -> bool:
    """Un calque qui lit une fenêtre ``trim`` d'un flux partagé, avec la correspondance simple source = timeline × vitesse."""
    if getattr(layer, "still", False) or getattr(layer, "time_map", None) is not None:
        return False
    if layer.timeline_end <= layer.timeline_start or layer.source_out <= layer.source_in:
        return False
    return bool(layer.nested_key or layer.source_path)


def estimate_plan_queue(plan, width: int, height: int, fps: float) -> QueueEstimate:
    """File d'attente d'**un** plan (racine ou séquence imbriquée) composé à ``width × height`` et ``fps``."""
    families: dict[tuple[str, str], list] = {}
    for layer in plan.video_layers:
        if _countable(layer):
            families.setdefault(("nested" if layer.nested_key else "file", layer.nested_key or layer.source_path), []).append(layer)
    total_bytes, total_frames, holding = 0.0, 0.0, 0
    frame_bytes = float(max(1, width) * max(1, height) * _BYTES_PER_PIXEL)
    for layers in families.values():
        if len(layers) < 2:
            continue
        latest = max(layer.source_in for layer in layers)
        frames, size = 0.0, 0.0
        for layer in layers:
            held = min(layer.source_out, latest) - layer.source_in
            if held <= 0:
                continue
            seconds = held / (layer.source_out - layer.source_in) * (layer.timeline_end - layer.timeline_start)
            count = seconds * max(1.0, float(fps))
            frames += count
            size += count * frame_bytes * _frame_factor(layer)
        if size > 0:
            holding += 1
            total_bytes += size
            total_frames += frames
    return QueueEstimate(int(total_bytes), total_frames, holding)


def estimate_export_queue(request) -> QueueEstimate:
    """File d'attente de tout l'export : plan racine et séquences imbriquées (chacune compose sa propre chaîne)."""
    from .export_engine import composition_size, nested_geometry

    plan = request.render_plan
    out_width, out_height = request.preset.resolution
    width, height = composition_size(plan.width, plan.height, out_width, out_height)
    parts = [estimate_plan_queue(plan, width, height, float(request.fps))]
    for entry in getattr(plan, "nested_sequences", ()) or ():
        if getattr(entry, "composition", None) is not None:
            continue
        inner_width, inner_height, inner_fps = nested_geometry(entry.plan, out_width, out_height, plan)
        parts.append(estimate_plan_queue(entry.plan, inner_width, inner_height, inner_fps))
    return QueueEstimate(
        sum(part.bytes for part in parts), sum(part.frames for part in parts), sum(part.families for part in parts),
    )


def physical_memory_bytes() -> int | None:
    """Mémoire physique de la machine (``None`` : le système ne la dit pas)."""
    from .memory_monitor import read_memory_status

    try:
        return read_memory_status().total_bytes
    except Exception:  # noqa: BLE001 - une lecture système ratée ne doit jamais bloquer un export
        LOGGER.debug("Mémoire physique illisible", exc_info=True)
        return None


def check_export_memory(request, *, total_bytes: int | None = None) -> None:
    """Refuse (``ValueError``, message lisible) un export dont la file d'attente FFmpeg dépasserait la mémoire de la machine.

    Calculé avant tout lancement de FFmpeg, sur le plan seul. ``total_bytes`` : mémoire à considérer (la machine par défaut).
    Un montage sans clip partagé en plusieurs morceaux éloignés (le cas courant) a une estimation nulle et n'est jamais refusé.
    """
    estimate = estimate_export_queue(request)
    if estimate.bytes <= 0:
        return
    total = total_bytes if total_bytes else physical_memory_bytes()
    limit = int((total if total else UNKNOWN_MEMORY_BYTES) * MEMORY_SHARE)
    if estimate.bytes <= limit:
        return
    LOGGER.warning("Export refusé : file d'attente estimée %.1f Go pour une limite de %.1f Go", estimate.bytes / _GIB, limit / _GIB)
    available = f" (cette machine : {total / _GIB:.0f} Go)" if total else ""
    raise ValueError(
        f"Ce montage est trop complexe pour être exporté d'un seul tenant : mémoire estimée {estimate.bytes / _GIB:.0f} Go"
        f"{available}. Plusieurs morceaux d'un même média (ou d'un même angle multicam) sont séparés par un long passage, "
        "et FFmpeg devrait garder en mémoire tout ce qui les sépare. Réduisez le nombre de coupes d'un même fichier, ou "
        "exportez le montage en plusieurs parties plus courtes."
    )
