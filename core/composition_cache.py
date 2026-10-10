"""Cache de rendu des compositions : l'image composée, lue en temps réel par le moniteur.

Le moniteur en direct n'a qu'un décodeur (un lecteur Qt) : il ne peut pas décoder et fusionner les sources d'une
composition pendant la lecture. Comme le cache « Fusion Output » de DaVinci Resolve, la **sortie** de chaque
composition est donc rendue d'avance, par morceaux de :data:`CHUNK_SECONDS` de son temps propre, avec le graphe compilé
de l'export (même chemin que les segments fidèles : :func:`core.preview_segments.build_segment_job`). Le moniteur lit
ensuite ces fichiers à la place de la source principale, et applique en direct les réglages du clip lui-même (transform,
effets, étalonnage), comme pour tout clip : le cache ne contient que la composition, sur un clip **neutre**.

Le cache ne dépend que du contenu de la composition (graphe, durée, animation, médias lus, qualité) : déplacer ou
couper le clip, ou modifier le reste du montage, le laisse valable. Les fichiers vivent dans le cache des segments
d'aperçu (budget, LRU, durée de vie), sous le propriétaire :func:`cache_owner` du clip ; ils ne sont jamais servis à
l'export.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from dataclasses import dataclass, replace

from .preview_cache import PreviewSegmentKey
from .preview_engine import PreviewJob
from .project_model import Clip, Project, Sequence, Track

CHUNK_SECONDS = 10.0
"""Longueur d'un morceau, en temps de la composition : assez long pour que la lecture ne change pas de fichier toutes
les deux secondes, assez court pour qu'un morceau soit prêt vite et que la tête de lecture serve la première."""

KEYFRAME_INTERVAL = 12
"""Images entre deux images clés d'un morceau (comme les proxys) : le moniteur s'y positionne vite partout, à chaque
changement de source et à chaque recalage de la lecture."""

OWNER_PREFIX = "comp-"


def cache_owner(clip_id: str) -> str:
    """Propriétaire des morceaux d'un clip de composition dans le moteur d'aperçu.

    Distinct de ``clip_id`` : invalider les segments du clip (déplacement, réglage du clip) ne jette pas sa
    composition, que ces changements ne touchent pas."""
    return OWNER_PREFIX + str(clip_id)


@dataclass(frozen=True)
class CompositionSite:
    """Un clip de composition et la séquence qui le porte (sa taille d'image et sa cadence)."""

    clip: Clip
    sequence: Sequence


def composition_sites(project: Project) -> Iterator[CompositionSite]:
    """Les clips de composition actifs de la séquence active et des séquences qu'elle imbrique (une fois chacun)."""
    seen: set[str] = set()

    def walk(sequence: Sequence, stack: tuple[str, ...]):
        for track in sequence.tracks:
            if not track.visible or track.type != "video":
                continue
            for clip in track.clips:
                if not clip.enabled:
                    continue
                if clip.composition is not None and clip.id not in seen:
                    seen.add(clip.id)
                    yield CompositionSite(clip, sequence)
                elif clip.sequence_id and clip.sequence_id not in stack:
                    child = project.get_sequence(clip.sequence_id)
                    if child is not None:
                        yield from walk(child, (*stack, child.id))

    active = project.active_sequence
    yield from walk(active, (active.id,))


def chunk_count(duration: float, chunk_seconds: float = CHUNK_SECONDS) -> int:
    return max(1, math.ceil(float(duration) / chunk_seconds - 1e-9))


def chunk_index(inner_time: float, chunk_seconds: float = CHUNK_SECONDS) -> int:
    return max(0, int(math.floor(float(inner_time) / chunk_seconds + 1e-9)))


def neutral_project(project: Project, site: CompositionSite) -> Project:
    """Un projet d'un seul clip : la composition du site, posée à 0, sans les réglages du clip (transform, effets,
    étalonnage, masques, vitesse), que le moniteur applique lui-même. Mêmes médias, même cadre, même cadence que la
    séquence qui porte le clip."""
    clip, sequence = site.clip, site.sequence
    composition = clip.composition
    if composition is None:
        raise ValueError(f"Le clip {clip.id} n'est pas une composition.")
    neutral = Clip(clip.id, "", "V1", 0.0, 0.0, float(composition.duration), label=clip.label)
    neutral.composition = composition
    return Project(
        f"composition {clip.id}", width=sequence.width, height=sequence.height, fps=sequence.fps,
        media_assets=list(project.media_assets), tracks=[Track("V1", "V1", "video", clips=[neutral])],
    )


def composition_chunk_job(
    project: Project, site: CompositionSite, index: int, *, quality: str, resolver=None,
    flow_preference: object = "auto", view: str = "", chunk_seconds: float = CHUNK_SECONDS,
) -> PreviewJob | None:
    """Rendu du morceau ``index`` de la composition du site, ou ``None`` s'il est hors de la composition.

    C'est un segment fidèle du projet neutre : même graphe que l'export, mêmes proxys, même empreinte (contenu et
    médias lus). ``view`` : le nœud montré à la place de la sortie (page Composition), qui entre dans l'empreinte. Le
    job n'est pas lié à la tête de lecture (``anchored=False``) : son début est un temps de la composition, pas de la
    timeline."""
    from .composition import CompositionView
    from .preview_segments import build_segment_job

    composition = site.clip.composition
    if composition is None or not 0 <= index < chunk_count(composition.duration, chunk_seconds):
        return None
    job = build_segment_job(
        neutral_project(project, site), index, quality=quality, resolver=resolver, segment_seconds=chunk_seconds,
        flow_preference=flow_preference, grade_overrides={site.clip.id: CompositionView(view)} if view else None,
    )
    if job is None:
        return None
    if not isinstance(job.key, PreviewSegmentKey):
        raise TypeError(f"Clé de segment inattendue : {job.key!r}")
    job.key = replace(job.key, clip_id=cache_owner(site.clip.id))
    job.keyframe_interval = KEYFRAME_INTERVAL
    job.anchored = False
    return job


__all__ = [
    "CHUNK_SECONDS",
    "KEYFRAME_INTERVAL",
    "CompositionSite",
    "cache_owner",
    "chunk_count",
    "chunk_index",
    "composition_chunk_job",
    "composition_sites",
    "neutral_project",
]
