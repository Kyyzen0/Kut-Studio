"""Mixage en direct : le son de l'export, calculé à partir de la tête de lecture et joué pendant la lecture.

Le moniteur temps réel n'a qu'un lecteur : il ne fait entendre que le son du clip qu'il montre. Ni les pistes audio,
ni le mixage (volume, pan, mute, solo, automation, effets, ducking, Master) ne s'entendaient en lecture. Le mixage
en direct les fait entendre tels que l'export les produira.

**Un flux par lecture.** Au lancement de la lecture, un processus FFmpeg rend le graphe audio de l'export
(:meth:`core.export_engine.ExportEngine._build_filter_complex`, ``audio_only``) à partir d'une **origine** prise un peu
avant la tête (``audio_origin`` : le mixage ne se calcule plus depuis le début de la timeline, son coût ne dépend plus
de la position), en PCM flottant sur sa sortie standard. Un flux continu, et non des morceaux : les effets qui ont une
mémoire (compresseurs, ducking, écho) ne sont jamais coupés à une jonction. La **pré-lecture** (:data:`PREROLL_SECONDS`)
est calculée puis jetée : compresseurs, ducking et sonie à court terme partent avec leur historique.

**Canaux du flux**, par image : 0-1 le mixage entendu (Master compris) ; 2-3 le même, pondéré K (BS.1770 à 48 kHz, pour
la sonie) ; puis deux par piste mesurée (son sous-mixage après fader et ducking, :data:`LiveMixCommand.track_ids`).
:class:`MixMeter` en tire les vumètres, sur exactement ce qui est joué.

Rien ici n'importe Qt ; numpy n'est importé que par les mesures (jamais par l'export).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

from .render_plan import RenderPlan

SAMPLE_RATE = 48000
PREROLL_SECONDS = 3.0
"""Calculé puis jeté avant la tête : le relâchement d'un ducking et la fenêtre de la sonie à court terme (3 s)."""
MAX_METERED_TRACKS = 24
"""Au-delà, seul le Master est mesuré (un flux FFmpeg a au plus 64 canaux)."""

# Pondération K de BS.1770-4 à 48 kHz : étage « tête » (plateau haut) puis passe-haut RLB.
K_WEIGHTING = (
    "biquad=b0=1.53512485958697:b1=-2.69169618940638:b2=1.19839281085285:a0=1:a1=-1.69065929318241:a2=0.73248077421585,"
    "biquad=b0=1:b1=-2:b2=1:a0=1:a1=-1.99004745483398:a2=0.99007225036621"
)


@dataclass(frozen=True)
class LiveMixCommand:
    """La commande FFmpeg d'une lecture, et comment lire son flux."""

    command: tuple[str, ...]
    start: float
    """Instant de la timeline du premier échantillon gardé (la tête de lecture au lancement)."""
    preroll_frames: int
    """Images (échantillons par canal) à jeter avant ``start`` : la pré-lecture."""
    track_ids: tuple[str, ...]
    """Pistes mesurées : la piste ``k`` occupe les canaux ``4 + 2k`` et ``5 + 2k``."""

    @property
    def channels(self) -> int:
        return 4 + 2 * len(self.track_ids)

    @property
    def frame_bytes(self) -> int:
        return 4 * self.channels


def _without_video(plan: RenderPlan) -> RenderPlan:
    """Le plan sans image : aucun fichier vidéo seul n'est ouvert, aucune composition d'image n'est construite."""
    return replace(
        plan, video_layers=(), graphics_layers=(), subtitle_cues=(), transitions=(),
        nested_sequences=tuple(replace(entry, plan=_without_video(entry.plan))
                               for entry in getattr(plan, "nested_sequences", ()) or ()),
    )


def live_mix_plan(
    project, start: float, *, master_gain_db: float = 0.0, master_muted: bool = False, resolver=None,
    timeline_index=None,
) -> tuple[RenderPlan, float]:
    """Le plan du mixage d'une lecture qui part de ``start`` : ``(plan, origine)``. Fenêtré de l'origine (``start``
    moins la pré-lecture) à la fin de la timeline, proxys compris, Master compris, sans image."""
    from .preview_segments import segment_plan
    from .timeline_evaluator import timeline_duration

    end = float(timeline_duration(project))
    origin = max(0.0, min(float(start), end) - PREROLL_SECONDS)
    plan = segment_plan(project, origin, max(end, origin), master_gain_db=master_gain_db, master_muted=master_muted,
                        resolver=resolver, timeline_index=timeline_index)
    return _without_video(plan), origin


def build_live_mix_command(
    project, start: float, *, master_gain_db: float = 0.0, master_muted: bool = False, resolver=None,
    timeline_index=None,
) -> LiveMixCommand | None:
    """La commande du mixage d'une lecture qui part de ``start``, ou ``None`` s'il n'y a plus rien à jouer.

    Sans clip son, le flux est un silence de la durée restante : les vumètres retombent, la lecture n'a pas de trou."""
    from .export_engine import ExportEngine, _ffmpeg_command_prefix, decoder_threads, filter_graph_arguments, \
        input_arguments

    plan, origin = live_mix_plan(project, start, master_gain_db=master_gain_db, master_muted=master_muted,
                                 resolver=resolver, timeline_index=timeline_index)
    if plan.duration - max(origin, float(start)) <= 1e-3:
        return None
    # Trop de pistes pour un flux : seul le Master est mesuré (aucun point de mesure n'est créé).
    taps: dict[str, str] | None = {} if len({layer.track_id for layer in plan.audio_layers}) <= MAX_METERED_TRACKS \
        else None
    graph, _video, audio, inputs = ExportEngine._build_filter_complex(
        plan, plan.width or 16, plan.height or 16, plan.fps or 30.0, None, audio_only=True, audio_origin=origin,
        track_taps=taps,
    )
    metered = list((taps or {}).items())
    graph += (
        f";[{audio}]asplit=2[lm_play][lm_k];[lm_k]{K_WEIGHTING},"
        f"aformat=sample_fmts=flt:channel_layouts=stereo:sample_rates={SAMPLE_RATE}[lm_kw]"
    )
    streams = ["[lm_play]", "[lm_kw]", *(f"[{label}]" for _track, label in metered)]
    graph += f";{''.join(streams)}amerge=inputs={len(streams)},aformat=sample_fmts=flt[lm_out]"
    command = [*_ffmpeg_command_prefix(), "-hide_banner", "-loglevel", "error", "-nostdin"]
    threads = decoder_threads(inputs)
    for path in inputs:
        command += input_arguments(path, threads=threads)
    command += filter_graph_arguments(graph, [])
    command += ["-map", "[lm_out]", "-c:a", "pcm_f32le", "-f", "f32le", "pipe:1"]
    return LiveMixCommand(
        command=tuple(command), start=float(start), preroll_frames=int(round((float(start) - origin) * SAMPLE_RATE)),
        track_ids=tuple(track for track, _label in metered),
    )


# --- vumètres --------------------------------------------------------------------------------------------------------

SILENCE_DB = -120.0
BLOCK_FRAMES = SAMPLE_RATE // 20
"""Un relevé toutes les 50 ms : la sonie momentanée en moyenne 8 (400 ms), la sonie à court terme 60 (3 s)."""
_MOMENTARY_BLOCKS = 8
_SHORT_TERM_BLOCKS = 60


def _db(value: float) -> float:
    return 20.0 * math.log10(value) if value > 1e-6 else SILENCE_DB


def _lufs(mean_square: float) -> float:
    return -0.691 + 10.0 * math.log10(mean_square) if mean_square > 1e-12 else SILENCE_DB


@dataclass(frozen=True)
class MeterFrame:
    """Les mesures d'un bloc de 50 ms, à l'instant ``time`` de la timeline (fin du bloc)."""

    time: float
    peak_db: float
    """Crête du mixage entendu (le plus fort des deux canaux), dBFS."""
    rms_db: float
    momentary_lufs: float
    short_term_lufs: float
    track_peaks_db: tuple[float, ...]
    """Crête de chaque piste mesurée, dans l'ordre de :attr:`LiveMixCommand.track_ids`."""


class MixMeter:
    """Mesures du flux, bloc par bloc, sur ce qui est joué (après la pré-lecture, qui ne fait que les amorcer)."""

    def __init__(self, channels: int, start: float) -> None:
        import numpy as np

        self._np = np
        self.channels = int(channels)
        self.start = float(start)
        self._pending = np.zeros((0, self.channels), dtype=np.float32)
        self._played = 0                                    # images jouées mesurées (la pré-lecture n'en est pas)
        self._history: list[float] = []                     # puissance K de chaque bloc (somme des deux canaux)

    def feed(self, frames, *, played: bool = True) -> list[MeterFrame]:
        """Ajoute des images ``(n, canaux)`` ; renvoie les relevés des blocs terminés. ``played=False`` : la
        pré-lecture, qui nourrit l'historique de la sonie sans produire de relevé."""
        np = self._np
        data = np.concatenate([self._pending, frames]) if len(self._pending) else frames
        complete = len(data) - len(data) % BLOCK_FRAMES
        self._pending = data[complete:].copy()
        out: list[MeterFrame] = []
        for low in range(0, complete, BLOCK_FRAMES):
            block = data[low:low + BLOCK_FRAMES]
            weighted = block[:, 2:4].astype(np.float64)
            self._history.append(float((weighted ** 2).mean(axis=0).sum()))
            del self._history[:-_SHORT_TERM_BLOCKS]
            if not played:
                continue
            self._played += BLOCK_FRAMES
            heard = block[:, 0:2].astype(np.float64)
            peaks = np.abs(block).max(axis=0)
            tracks = tuple(_db(float(max(peaks[c], peaks[c + 1]))) for c in range(4, self.channels - 1, 2))
            out.append(MeterFrame(
                time=self.start + self._played / SAMPLE_RATE,
                peak_db=_db(float(max(peaks[0], peaks[1]))),
                rms_db=_db(math.sqrt(float((heard ** 2).mean()))),
                momentary_lufs=_lufs(sum(self._history[-_MOMENTARY_BLOCKS:]) / min(len(self._history),
                                                                                   _MOMENTARY_BLOCKS)),
                short_term_lufs=_lufs(sum(self._history) / len(self._history)),
                track_peaks_db=tracks,
            ))
        return out


__all__ = [
    "BLOCK_FRAMES",
    "K_WEIGHTING",
    "LiveMixCommand",
    "MAX_METERED_TRACKS",
    "MeterFrame",
    "MixMeter",
    "PREROLL_SECONDS",
    "SAMPLE_RATE",
    "SILENCE_DB",
    "build_live_mix_command",
    "live_mix_plan",
]
