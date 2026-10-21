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

**Taille des images en file (mesurée).** C'est l'image **conformée au cadre de la composition**, ``largeur × hauteur × 4`` octets,
quelle que soit la transformation du clip : la file se forme avant ``scale`` / ``rotate`` de la transformation. Mesuré
(480×270, coupe à 15 s, surcoût de FFmpeg 9.0 : 196 Mo sans transformation, 191 Mo à l'échelle 0,5, 209 Mo à l'échelle 3,
199 Mo à 45°, 200 Mo en cadrage « remplir » ; échelle animée 1 → 3 : 240 Mo) et sur FFmpeg 6.1 et 7.1 (une échelle 3, dont
les images seraient de 1,7 Go à la taille agrandie, tient dans 700 Mo). L'échelle < 1 ne réduit donc PAS la file.

Le correctif de fond (donner à chaque clip son propre décodeur, ou composer par tranches de temps) change la structure du
graphe, donc le rendu à valider image par image : c'est la phase 2 (moteur). En attendant, :func:`check_export_memory` refuse
**avant FFmpeg** un export qui ne peut pas tenir, au lieu de remplir le swap puis le disque.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .render_plan import RenderLayer

LOGGER = logging.getLogger("kut_studio.export_memory")

MEMORY_SHARE = 0.85
"""Part de la mémoire disponible au-delà de laquelle l'export est refusé : au-dessus, le système écrit en swap et l'export ne
finit pas (le premier essai du banc a rempli le disque). La file estimée est seule : FFmpeg ajoute ~1 Go de décodeurs."""

MEMORY_FLOOR = 0.25
"""Part de la mémoire installée sous laquelle la référence ne descend jamais (stabilité d'un lancement à l'autre)."""

UNKNOWN_MEMORY_BYTES = 16 * 1024**3
"""Référence quand le système ne dit ni la mémoire installée ni la disponible : seuls les montages gigantesques sont refusés."""

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


def _countable(layer) -> bool:
    """Un calque qui lit une fenêtre ``trim`` d'un flux partagé, avec la correspondance simple source = timeline × vitesse."""
    if getattr(layer, "still", False) or getattr(layer, "time_map", None) is not None:
        return False
    if layer.timeline_end <= layer.timeline_start or layer.source_out <= layer.source_in:
        return False
    return bool(layer.nested_key or layer.source_path)


def _estimate(chains) -> QueueEstimate:
    """File d'attente de plusieurs plans qui composent leur propre chaîne d'``overlay`` : ``chains`` = ``[(plan, largeur,
    hauteur, cadence), ...]``.

    Les familles se forment sur **toute la hiérarchie**, avec la clé de déduplication des entrées (``_build_input_list`` :
    le chemin du fichier, racine et séquences imbriquées confondues) ou, pour une séquence imbriquée, sa clé (un seul
    ``split`` pour toutes ses instances, quel que soit le plan qui les porte). Le décodage avance dans le temps du
    **fichier** : le décalage d'imbrication et la vitesse ne déplacent pas ``M`` ; ils ne changent que le nombre
    d'images (durée sur la timeline du calque), déjà dans la formule. Chaque image est comptée à la taille de la composition
    du plan qui porte le calque."""
    families: dict[tuple[str, str], list[tuple[RenderLayer, float, float]]] = {}
    for plan, width, height, fps in chains:
        frame_bytes = float(max(1, int(width)) * max(1, int(height)) * _BYTES_PER_PIXEL)
        for layer in plan.video_layers:
            if _countable(layer):
                key = ("nested", layer.nested_key) if layer.nested_key else ("file", layer.source_path)
                families.setdefault(key, []).append((layer, frame_bytes, max(1.0, float(fps))))
    total_bytes, total_frames, holding = 0.0, 0.0, 0
    for members in families.values():
        if len(members) < 2:
            continue
        latest = max(layer.source_in for layer, _bytes, _fps in members)
        size = frames = 0.0
        for layer, frame_bytes, fps in members:
            held = min(layer.source_out, latest) - layer.source_in
            if held <= 0:
                continue
            count = held / (layer.source_out - layer.source_in) * (layer.timeline_end - layer.timeline_start) * fps
            frames += count
            size += count * frame_bytes
        if size > 0:
            holding += 1
            total_bytes += size
            total_frames += frames
    return QueueEstimate(int(total_bytes), total_frames, holding)


def estimate_plan_queue(plan, width: int, height: int, fps: float) -> QueueEstimate:
    """File d'attente d'**un** plan isolé, composé à ``width × height`` et ``fps`` (pas de partage avec d'autres plans :
    pour un export, :func:`estimate_export_queue`)."""
    return _estimate([(plan, width, height, fps)])


def estimate_export_queue(request) -> QueueEstimate:
    """File d'attente de tout l'export : plan racine et séquences imbriquées, familles communes à toute la hiérarchie."""
    from .export_engine import composition_size, nested_geometry

    plan = request.render_plan
    out_width, out_height = request.preset.resolution
    width, height = composition_size(plan.width, plan.height, out_width, out_height)
    chains = [(plan, width, height, float(request.fps))]
    for entry in getattr(plan, "nested_sequences", ()) or ():
        if getattr(entry, "composition", None) is not None:
            continue
        inner_width, inner_height, inner_fps = nested_geometry(entry.plan, out_width, out_height, plan)
        chains.append((entry.plan, inner_width, inner_height, inner_fps))
    return _estimate(chains)


def memory_snapshot() -> tuple[int | None, int | None]:
    """``(mémoire physique, mémoire disponible)`` de la machine, ``None`` pour ce que le système ne dit pas.

    Disponible : ce que le système peut donner sans écrire en swap (Linux ``MemAvailable``, Windows ``AvailPhys``, macOS le
    pourcentage libre du noyau, mémoire récupérable comprise), :mod:`core.memory_monitor`."""
    from .memory_monitor import read_memory_status

    try:
        status = read_memory_status()
    except Exception:  # noqa: BLE001 - une lecture système ratée ne doit jamais bloquer un export
        LOGGER.debug("Mémoire illisible", exc_info=True)
        return None, None
    return status.total_bytes, status.available_bytes


def physical_memory_bytes() -> int | None:
    """Mémoire physique installée (``None`` : le système ne la dit pas)."""
    return memory_snapshot()[0]


def memory_budget(total_bytes: int | None, available_bytes: int | None) -> tuple[int, str]:
    """``(octets autorisés pour la file, ce qui a été mesuré)``.

    Référence = la mémoire **disponible** (pas l'installée : d'autres processus en occupent déjà une part), mais jamais sous
    ``MEMORY_FLOOR`` de la mémoire installée : la mesure est un instantané qui bouge d'un lancement à l'autre, et un export
    qui passait deux minutes plus tôt ne doit pas être refusé parce qu'un navigateur vient de grossir. Seuls les montages
    nettement trop gros (la file de D-002 vaut dix fois la machine) sont donc refusés. Disponible inconnue : la moitié de
    l'installée ; les deux inconnues : ``UNKNOWN_MEMORY_BYTES``. Puis ``MEMORY_SHARE`` de la référence."""
    if available_bytes is not None and available_bytes >= 0:
        reference, measured = available_bytes, "disponible"
    elif total_bytes:
        reference, measured = total_bytes // 2, "estimée disponible (mesure indisponible, moitié de la mémoire installée)"
    else:
        reference, measured = UNKNOWN_MEMORY_BYTES, "supposée (mesure indisponible)"
    if total_bytes:
        reference = max(reference, int(total_bytes * MEMORY_FLOOR))
    return int(reference * MEMORY_SHARE), measured


def check_export_memory(request, *, total_bytes: int | None = None, available_bytes: int | None = None) -> None:
    """Refuse (``ValueError``, message lisible) un export dont la file d'attente FFmpeg dépasserait la mémoire disponible.

    Calculé avant tout lancement de FFmpeg, sur le plan seul. ``total_bytes`` / ``available_bytes`` : mémoire à considérer
    (par défaut celle de la machine, :func:`memory_snapshot`) ; donner seulement ``total_bytes`` laisse la mémoire disponible
    inconnue. Un montage sans média découpé en morceaux éloignés (le cas courant) a une estimation nulle et n'est jamais refusé.
    """
    estimate = estimate_export_queue(request)
    if estimate.bytes <= 0:
        return
    if total_bytes is None and available_bytes is None:
        total, available = memory_snapshot()
    else:
        total, available = total_bytes, available_bytes
    limit, measured = memory_budget(total, available)
    if estimate.bytes <= limit:
        return
    LOGGER.warning(
        "Export refusé : file d'attente estimée %.1f Go pour une limite de %.1f Go (mémoire %s)", estimate.bytes / _GIB, limit / _GIB, measured,
    )
    machine = ""
    if available is not None:
        machine = f", mémoire disponible sur cette machine ≈ {available / _GIB:.0f} Go" + (f" sur {total / _GIB:.0f} Go" if total else "")
    elif total:
        machine = f", mémoire de cette machine : {total / _GIB:.0f} Go (disponible non mesurée)"
    raise ValueError(
        f"Ce montage est trop complexe pour être exporté d'un seul tenant : mémoire estimée {estimate.bytes / _GIB:.0f} Go"
        f"{machine}. Plusieurs morceaux d'un même média (ou d'un même angle multicam) sont séparés par un long passage, "
        "et FFmpeg devrait garder en mémoire tout ce qui les sépare. Réduisez le nombre de coupes d'un même fichier, ou "
        "exportez le montage en plusieurs parties plus courtes."
    )
