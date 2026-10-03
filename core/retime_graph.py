"""Le temps d'un clip dans le graphe FFmpeg : de la :class:`~core.time_map.TimeMap` aux filtres.

Ce module ne décide de **rien** : le mapping (quel instant source à quel instant de la timeline) vient de
:mod:`core.time_map`. Il le traduit en filtres, de la même façon pour l'export, l'aperçu fidèle et les scopes (un seul
graphe, :func:`core.export_engine._build_filter_complex`).

**Échantillonnage exact.** ``setpts`` + ``fps`` n'est pas « l'image source la plus proche » : ``fps`` est un
échantillonneur-bloqueur décalé d'une demi-image de sortie (à 2× il montre les images 1, 3, 5… au lieu de 0, 2, 4…, il saute
l'image 0 dès qu'on accélère). On ne lui laisse donc plus arrondir : chaque image source reçoit l'instant **exact du premier
tick où elle doit apparaître** (``ceil`` calculé sur le compteur d'images ``N``) ; ``fps`` n'a plus qu'à la tenir jusqu'au
tick suivant. Règle de sélection, la même pour tous les modes : l'image montrée à l'instant ``T`` est la plus proche de
``M(T)`` (à égalité, la suivante).

**Runs et morceaux.** Le mapping est découpé en *runs* monotones (avant, arrière, arrêt). Un run avant est une branche
``trim → setpts → fps`` ; un run arrière passe d'abord par ``reverse`` (mémoire bornée par le run, pas par le clip) ; un
arrêt tient une image. Dans un run, la vitesse est approchée par des morceaux à vitesse constante (écart ≤ 2 % d'image) dont
les extrémités sont exactes. Les runs sont ensuite mis bout à bout (``concat``), chacun couvrant un nombre entier d'images :
aucun cumul d'erreur d'arrondi d'un run à l'autre.

Fonctions pures qui produisent des chaînes : testables sans FFmpeg, vérifiées avec le vrai FFmpeg dans
``tests/test_retime_export_real.py``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

from .time_map import Piece, Run, RunKind, TimeMap

TICK_EPSILON = 1e-6
"""Tolérance (en ticks) des arrondis de ``ceil`` / ``floor`` : une égalité exacte ne bascule pas sur le bruit flottant."""

PTS_BIAS = 1e-9
"""Secondes ajoutées avant la conversion en unités de la base de temps. ``setpts`` **tronque** : ``246 / 30 / (1/30)`` vaut
``245,99999999999997`` et donnait le tick 245. Un nanoseconde est très en deçà de tout tick et très au-delà de l'erreur flottante."""

FINE_TIMEBASE = "1/1000000"
"""Base de temps imposée avant ``setpts`` (la microseconde). La base d'un fichier peut être grossière (``1/25`` pour une source
à 25 i/s, ``1/1000`` en MKV) : l'instant d'un tick d'une sortie à une autre cadence (``k/24 s``) y serait tronqué à un multiple
de la base et ``fps`` choisirait une autre image. À la microseconde l'erreur est ≪ un tick, quelle que soit la source."""

TRIM_START_MARGIN = 0.4
TRIM_END_MARGIN = 0.6
"""Marges (en images) des bornes de ``trim``. Des bornes à mi-image tombent sur un milieu d'arrondi quand la base de temps
vaut exactement ``1/fps`` (``trim`` les tronque au microsecond puis les arrondit à la base de temps). ``trim`` garde les
images dont l'horodatage est ``≥ start`` et **strictement** ``< end`` : un début à −0,4 image s'arrondit à l'image visée
(incluse), une fin à +0,6 s'arrondit à l'image suivante (donc l'image visée est incluse, sa voisine exclue). Même avec une
petite gigue d'horodatage dans le fichier, la fenêtre garde exactement ``m_lo … m_hi``."""

MAX_REVERSE_BYTES = 4 * 1024**3
"""Mémoire maximale d'un run en lecture inverse : ``reverse`` garde **toutes** ses images décodées (4 Gio ; ~45 s en 1080p30)."""

AUDIO_RATE = 48000
AUDIO_CROSSFADE = 0.005
"""Durée (s) du fondu enchaîné entre deux morceaux audio : assez court pour ne pas s'entendre, assez long pour supprimer le
clic d'un changement brutal de tempo."""
AUDIO_TOLERANCE = 0.005
"""Écart toléré (s de source) de l'approximation audio par morceaux : l'oreille ne distingue pas 5 ms, la vidéo exige 20 fois moins."""
MIN_AUDIO_SPEED = 0.05
"""En deçà (ralenti à plus de 20×), le son n'a plus de sens : le morceau devient un silence de la bonne durée."""

_MIN_FPS = 1.0


class RetimeError(ValueError):
    """Un temps qu'on ne sait pas rendre exactement : refusé avec la cause, jamais approché en silence."""


def _fmt(value: float) -> str:
    """Nombre pour une expression FFmpeg (``setpts``…) : 12 chiffres significatifs, jamais de séparateur de filtre."""
    return f"{float(value):.12g}"


def ticks_in(duration: float, fps: float) -> int:
    """Nombre d'images de sortie d'une durée : les ticks ``k / fps`` situés dans ``[0, duration)``."""
    return max(1, math.ceil(duration * fps - TICK_EPSILON))


@dataclass(frozen=True)
class VideoStage:
    """Chaînes qui produisent le flux remappé d'un clip.

    Attributes:
        chains: chaînes de filtres complètes (``[entrée]f1,f2[sortie]``), dans l'ordre où le graphe les écrit.
        label: étiquette de sortie : flux à la cadence du projet, horodatages repartant de 0, de durée exacte.
    """

    chains: tuple[str, ...]
    label: str


def nearest_frame(source_seconds: float, source_fps: float, last_frame: int | None = None) -> int:
    """Indice de l'image source la plus proche de ``source_seconds`` (à égalité, la suivante).

    ``last_frame`` : indice de la dernière image du média. Le plus proche de la **fin** du média (``source_out`` d'un clip
    entier) est souvent une image qui n'existe pas (60 images : l'instant 2,0 s est plus près de « 60 » que de 59) ;
    ``reverse`` renvoie en premier la dernière image *existante*, donc sans cette borne tout un run arrière serait décalé.
    """
    frame = max(0, math.floor(source_seconds * source_fps + 0.5 + 1e-9))
    return frame if last_frame is None else min(frame, max(0, last_frame))


def frame_for_tick(
    time_map: TimeMap, tick: int, fps: float, source_fps: float, last_frame: int | None = None
) -> int:
    """L'image source montrée au tick ``tick`` de la sortie : la règle de sélection, côté modèle (référence des tests)."""
    return nearest_frame(time_map.source_time(tick / fps), source_fps, last_frame)


# ---------------------------------------------------------------------------
# Horodatages exacts d'un run
# ---------------------------------------------------------------------------


def _forward_timestamps(pieces: tuple[Piece, ...], m_lo: int, fs: float, fps: float, first_tick: int) -> str:
    """Expression ``setpts`` (secondes) d'un run avant : premier tick d'apparition de chaque image, relatif au run.

    L'image ``N`` du flux rogné est l'image source ``m_lo + N`` ; elle devient la plus proche dès que la source atteint la
    frontière ``(m − ½) / fs``. Dans le morceau ``i`` (``s_i → s_{i+1}`` en ``t_i → t_{i+1}``), cet instant est
    ``α_i + β_i·N`` ; le premier tick d'apparition est le plafond de ``instant × fps``, ramené au run (``first_tick``) et borné
    à 0 (les images dont la frontière précède le run sont toutes à 0 : la dernière gagne, c'est la bonne).
    """
    terms: list[tuple[int, float, float]] = []
    for index, piece in enumerate(pieces):
        speed = piece.speed
        alpha = piece.t0 + (((m_lo - 0.5) / fs) - piece.s0) / speed
        beta = 1.0 / (fs * speed)
        start = 0 if index == 0 else math.ceil(piece.s0 * fs + 0.5 - m_lo - 1e-9)
        terms.append((start, alpha, beta))
    return _nest(terms, lambda a, b: (
        f"max(0,ceil(({_fmt(a)}+{_fmt(b)}*N)*{_fmt(fps)}-{TICK_EPSILON:g})-{first_tick})/{_fmt(fps)}"
    ))


def _backward_timestamps(pieces: tuple[Piece, ...], m_hi: int, fs: float, fps: float, first_tick: int) -> str:
    """Comme :func:`_forward_timestamps` pour un run arrière, après ``reverse`` : l'image ``N`` est ``m_hi − N``.

    L'image ``m`` n'est la plus proche qu'une fois la source **passée sous** ``(m + ½) / fs`` (à égalité c'est la suivante,
    ``m + 1``) : le premier tick d'apparition est donc strictement après cet instant.
    """
    terms: list[tuple[int, float, float]] = []
    for index, piece in enumerate(pieces):
        speed = abs(piece.speed)
        alpha = piece.t0 + (piece.s0 - (m_hi + 0.5) / fs) / speed
        beta = 1.0 / (fs * speed)
        start = 0 if index == 0 else math.ceil(m_hi + 0.5 - piece.s0 * fs - 1e-9)
        terms.append((start, alpha, beta))
    return _nest(terms, lambda a, b: (
        f"max(0,floor(({_fmt(a)}+{_fmt(b)}*N)*{_fmt(fps)}+{TICK_EPSILON:g})+1-{first_tick})/{_fmt(fps)}"
    ))


def _nest(terms: list[tuple[int, float, float]], make) -> str:
    """``if(lt(N, n_i), expr_{i-1}, …)`` : une expression par morceau, choisie par le rang de l'image."""
    unique: list[tuple[int, float, float]] = []
    for term in terms:
        if unique and term[0] <= unique[-1][0]:
            unique[-1] = term                      # un morceau plus court qu'une image n'en laisse aucune à lui seul
        else:
            unique.append(term)
    expression = make(unique[-1][1], unique[-1][2])
    for index in range(len(unique) - 2, -1, -1):
        expression = f"if(lt(N,{unique[index + 1][0]}),{make(unique[index][1], unique[index][2])},{expression})"
    return expression


# ---------------------------------------------------------------------------
# Un run -> une chaîne
# ---------------------------------------------------------------------------


def _run_chain(
    run: Run, pieces: tuple[Piece, ...], *, fs: float, fps: float, first_tick: int, ticks: int, frame_bytes: int,
    last_frame: int | None = None,
) -> str:
    """Filtres d'un run (sans étiquettes) : flux de ``ticks`` images exactement, horodatées à partir de 0."""
    hold = f"tpad=stop_mode=clone:stop_duration={_fmt(min(ticks / fps + 1.0, 3600.0))},trim=end_frame={ticks},setpts=PTS-STARTPTS"
    if run.kind is RunKind.HOLD:
        frame = nearest_frame(run.s0, fs, last_frame)
        return (
            f"trim=start={_fmt((frame - TRIM_START_MARGIN) / fs)}:end={_fmt((frame + TRIM_END_MARGIN) / fs)},setpts=PTS-STARTPTS,"
            f"tpad=stop_mode=clone:stop_duration={_fmt(min(ticks / fps + 1.0, 3600.0))},fps={_fmt(fps)},"
            f"trim=end_frame={ticks},setpts=PTS-STARTPTS"
        )
    low, high = sorted((run.s0, run.s1))
    m_lo, m_hi = nearest_frame(low, fs, last_frame), nearest_frame(high, fs, last_frame)
    window = f"trim=start={_fmt((m_lo - TRIM_START_MARGIN) / fs)}:end={_fmt((m_hi + TRIM_END_MARGIN) / fs)}"
    if run.kind is RunKind.FORWARD:
        timestamps = _forward_timestamps(pieces, m_lo, fs, fps, first_tick)
        return f"{window},settb={FINE_TIMEBASE},setpts='({timestamps}+{PTS_BIAS:g})/TB',fps={_fmt(fps)}:start_time=0,{hold}"
    held = (m_hi - m_lo + 1) * frame_bytes
    if held > MAX_REVERSE_BYTES:
        raise RetimeError(
            f"Lecture inverse impossible sur {run.duration:.1f} s de ce clip : FFmpeg garderait {held / 1024**3:.1f} Gio "
            f"d'images décodées (limite : {MAX_REVERSE_BYTES / 1024**3:.0f} Gio). Coupez le clip en morceaux plus courts."
        )
    timestamps = _backward_timestamps(pieces, m_hi, fs, fps, first_tick)
    return f"{window},reverse,settb={FINE_TIMEBASE},setpts='({timestamps}+{PTS_BIAS:g})/TB',fps={_fmt(fps)}:start_time=0,{hold}"


# ---------------------------------------------------------------------------
# Étage vidéo
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PreparedRun:
    """Un run dont les images ont été fabriquées en amont (:mod:`core.retime_prepare`) : ``frames`` images consécutives d'un
    fichier préparé, à partir de ``first_frame``. Un run arrière est écrit dans l'ordre de la source : le graphe l'inverse.

    ``lead`` / ``trail`` : ticks du run **avant / après** les images préparées, remplis de noir. Ils n'existent que pour un
    aperçu fenêtré (seuls les ticks du segment sont fabriqués ; ``-ss`` / ``-t`` jettent le reste, qui garde ainsi sa durée sans
    rien coûter) ; l'export prépare tout le run : ils valent 0.
    """

    run_index: int
    first_frame: int
    frames: int
    backward: bool
    lead: int = 0
    trail: int = 0

    @property
    def ticks(self) -> int:
        """Images du run dans le flux de sortie : celles du fichier et leurs marges."""
        return self.lead + self.frames + self.trail


@dataclass(frozen=True)
class TickRun:
    """Un run monotone du mapping, en images de sortie : ``ticks`` images à partir du tick ``first``."""

    run: Run
    pieces: tuple[Piece, ...]
    first: int
    ticks: int


def tick_runs(time_map: TimeMap, fps: float, tolerance: float | None = None) -> tuple[TickRun, ...]:
    """Découpage du clip en runs, en nombres **entiers** d'images (la somme est exactement ``ticks_in(durée)``).

    Une seule source pour l'étage d'échantillonnage (:func:`video_stage`) et la préparation des images intermédiaires
    (:mod:`core.retime_prepare`) : les deux voient les mêmes frontières de run, au tick près.
    """
    fps = max(float(fps), _MIN_FPS)
    plan = time_map.pieces() if tolerance is None else time_map.pieces(tolerance)
    total = ticks_in(time_map.duration, fps)
    starts = [min(total, max(0, round(run.t0 * fps))) for run, _pieces in plan]
    starts.append(total)
    runs = tuple(
        TickRun(run, pieces, starts[i], starts[i + 1] - starts[i])
        for i, (run, pieces) in enumerate(plan) if starts[i + 1] > starts[i]
    )
    if not runs:                                                  # durée sous une image : un seul run, une image
        run, pieces = plan[0]
        runs = (TickRun(run, pieces, 0, 1),)
    return runs


def _prepared_chain(prepared: PreparedRun, *, fps: float, frame_bytes: int) -> str:
    """Filtres d'un run lu dans un fichier préparé : exactement ``ticks`` images, horodatées à partir de 0 sur la grille des ticks."""
    last = prepared.first_frame + prepared.frames
    chain = [f"trim=start_frame={prepared.first_frame}:end_frame={last}", "setpts=PTS-STARTPTS"]
    if prepared.backward:
        held = prepared.frames * frame_bytes
        if held > MAX_REVERSE_BYTES:
            raise RetimeError(
                f"Lecture inverse impossible sur ce clip : FFmpeg garderait {held / 1024**3:.1f} Gio d'images décodées "
                f"(limite : {MAX_REVERSE_BYTES / 1024**3:.0f} Gio). Coupez le clip en morceaux plus courts."
            )
        chain.append("reverse")
    # Les horodatages du fichier sont ceux de son conteneur (arrondis à la milliseconde) : on les refait à partir du rang de l'image.
    chain += [f"settb={FINE_TIMEBASE}", f"setpts='(N/{_fmt(fps)}+{PTS_BIAS:g})/TB'", f"fps={_fmt(fps)}:start_time=0"]
    if prepared.lead:
        chain.append(f"tpad=start={prepared.lead}:start_mode=add:color=black")
    if prepared.trail:
        chain.append(f"tpad=stop={prepared.trail}:stop_mode=add:color=black")
    chain += [f"trim=end_frame={prepared.ticks}", "setpts=PTS-STARTPTS"]
    return ",".join(chain)


def video_stage(
    time_map: TimeMap,
    *,
    source_label: str,
    prefix: str,
    fps: float,
    source_fps: float,
    prepare: str,
    frame_bytes: int,
    tolerance: float | None = None,
    last_frame: int | None = None,
    prepared_label: str | None = None,
    prepared_runs: Mapping[int, PreparedRun] | None = None,
) -> VideoStage:
    """Le flux remappé d'un clip : échantillonné au plus proche, sauf les runs dont les images ont été préparées.

    Args:
        time_map: le mapping du clip.
        source_label: flux d'entrée (``N:v`` d'un fichier, ou étiquette d'une séquence imbriquée).
        prefix: préfixe des étiquettes intermédiaires (uniques dans le graphe).
        fps: cadence de sortie (celle du projet).
        source_fps: cadence du média source (``> 0`` ; 30 à défaut).
        prepare: filtres qui mettent l'entrée au cadre (``scale``, ``pad``…), appliqués **une fois** avant de répartir les runs.
        frame_bytes: octets d'une image décodée au cadre (borne de mémoire d'un run en lecture inverse).
        tolerance: écart toléré d'une approximation par morceaux (secondes de source) ; ``None`` : la valeur par défaut.
        last_frame: indice de la dernière image du média (``None`` : inconnu, aucune borne).
        prepared_label: flux du fichier d'images préparées (mélange, flux optique), ``None`` s'il n'y en a pas.
        prepared_runs: pour chaque indice de run **lu dans ce fichier** (:func:`tick_runs`), où il s'y trouve. Les autres
            runs gardent l'échantillonnage.

    Returns:
        Les chaînes à écrire dans le graphe et l'étiquette de sortie.
    """
    fs = source_fps if source_fps > 0 else 30.0
    fps = max(float(fps), _MIN_FPS)
    prepared_runs = prepared_runs or {}
    if prepared_runs and prepared_label is None:
        raise RetimeError("Des runs préparés sont demandés sans fichier d'images préparées.")
    runs = list(enumerate(tick_runs(time_map, fps, tolerance)))
    unknown = set(prepared_runs) - {index for index, _item in runs}
    if unknown:
        raise RetimeError(f"Le flux préparé décrit des runs que ce clip n'a plus ({sorted(unknown)}) : il est périmé.")
    from_media = [(index, item) for index, item in runs if index not in prepared_runs]
    from_file = [(index, item) for index, item in runs if index in prepared_runs]
    chains: list[str] = []
    base = f"{prefix}base"
    media = source_label
    if from_media and prepare:
        chains.append(f"[{source_label}]{prepare}[{base}]")
        media = base

    def feed(label: str, count: int, tag: str) -> list[str]:
        """``count`` étiquettes qui lisent ``label`` (``split`` s'il y en a plusieurs)."""
        if count <= 1:
            return [label]
        labels = [f"{prefix}{tag}{i}" for i in range(count)]
        chains.append(f"[{label}]split={count}" + "".join(f"[{item}]" for item in labels))
        return labels

    media_feeds = feed(media, len(from_media), "in") if from_media else []
    file_feeds = feed(prepared_label or "", len(from_file), "pf") if from_file else []
    single = len(runs) == 1
    outputs: dict[int, str] = {}
    for (index, item), label in zip(from_media, media_feeds):
        out = f"{prefix}out" if single else f"{prefix}run{index}"
        body = _run_chain(
            item.run, item.pieces, fs=fs, fps=fps, first_tick=item.first, ticks=item.ticks, frame_bytes=frame_bytes,
            last_frame=last_frame,
        )
        chains.append(f"[{label}]{body}[{out}]")
        outputs[index] = out
    for (index, _item), label in zip(from_file, file_feeds):
        out = f"{prefix}out" if single else f"{prefix}run{index}"
        chains.append(f"[{label}]{_prepared_chain(prepared_runs[index], fps=fps, frame_bytes=frame_bytes)}[{out}]")
        outputs[index] = out
    final = f"{prefix}out"
    if single:
        return VideoStage(tuple(chains), final)
    chains.append("".join(f"[{outputs[index]}]" for index, _item in runs) + f"concat=n={len(runs)}:v=1:a=0[{final}]")
    return VideoStage(tuple(chains), final)


# ---------------------------------------------------------------------------
# Étage audio
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AudioStage:
    """Chaînes qui produisent le son remappé d'un clip (stéréo 48 kHz, de durée exacte, horodaté à partir de 0)."""

    chains: tuple[str, ...]
    label: str


def tempo_filters(speed: float, *, preserve_pitch: bool) -> list[str]:
    """Filtres qui jouent l'audio à ``speed`` fois sa vitesse.

    ``preserve_pitch`` : ``atempo`` (la voix garde sa hauteur), en étapes de [0,5 ; 2] (plage garantie par toutes les
    versions de FFmpeg). Sinon ``asetrate`` + ``aresample`` : effet bande, la hauteur suit la vitesse.
    """
    if abs(speed - 1.0) < 1e-9:
        return []
    if preserve_pitch:
        from .time_remapping import get_ffmpeg_speed_filter

        return list(get_ffmpeg_speed_filter(speed))
    return [f"asetrate={max(1, round(AUDIO_RATE * speed))}", f"aresample={AUDIO_RATE}"]


_AUDIO_FORMAT = f"aformat=channel_layouts=stereo:sample_rates={AUDIO_RATE}"


def _silence(duration: float) -> str:
    return (
        f"anullsrc=r={AUDIO_RATE}:cl=stereo,atrim=duration={_fmt(duration)},asetpts=PTS-STARTPTS"
    )


def _audio_piece_chain(piece: Piece, kind: RunKind, *, extend: float, preserve_pitch: bool) -> str:
    """Un morceau à vitesse constante : portion de source, sens, tempo. ``extend`` : recouvrement du fondu (secondes de timeline)."""
    speed = abs(piece.speed)
    length = piece.t1 - piece.t0
    if kind is RunKind.HOLD or speed < MIN_AUDIO_SPEED:
        return _silence(length + extend)
    low, high = sorted((piece.s0, piece.s1))
    if kind is RunKind.FORWARD:
        high += extend * speed
    else:
        low = max(0.0, low - extend * speed)
    steps = [
        f"atrim=start={_fmt(max(0.0, low))}:end={_fmt(high)}", "asetpts=PTS-STARTPTS", _AUDIO_FORMAT,
    ]
    if kind is RunKind.BACKWARD:
        steps.append("areverse")
    steps.extend(tempo_filters(speed, preserve_pitch=preserve_pitch))
    return ",".join(steps)


def audio_stage(
    time_map: TimeMap,
    *,
    source_label: str,
    prefix: str,
    preserve_pitch: bool,
    remap_audio: bool,
    source_in: float,
) -> AudioStage:
    """Le son remappé d'un clip, morceau par morceau, avec un très court fondu enchaîné entre deux morceaux.

    Aucune continuité parfaite n'est simulée : FFmpeg ne sait pas faire varier le tempo en continu, la courbe est donc
    approchée par des morceaux à vitesse constante (extrémités exactes). Chaque morceau est prolongé de la durée du fondu et
    recouvre le début du suivant : la durée totale reste celle du mapping. Un arrêt (ou un ralenti extrême) est un silence de
    la bonne durée ; un run en sens inverse passe par ``areverse`` (mémoire bornée par le morceau, pas par le clip).

    ``remap_audio=False`` : l'audio garde son temps (1×, vers l'avant, depuis ``source_in``) pendant la durée du clip.
    """
    duration = time_map.duration
    tail = f"apad=whole_dur={_fmt(duration)},atrim=end={_fmt(duration)},asetpts=PTS-STARTPTS"
    out = f"{prefix}out"
    if not remap_audio:
        body = (
            f"atrim=start={_fmt(source_in)}:duration={_fmt(duration)},asetpts=PTS-STARTPTS,{_AUDIO_FORMAT},{tail}"
        )
        return AudioStage((f"[{source_label}]{body}[{out}]",), out)
    segments: list[tuple[Piece, RunKind]] = []
    for run, pieces in time_map.pieces(AUDIO_TOLERANCE):
        segments.extend((piece, run.kind) for piece in pieces if piece.t1 - piece.t0 > 1e-4)
    if not segments:
        return AudioStage((f"{_silence(duration)},{tail}[{out}]",), out)
    count = len(segments)
    chains: list[str] = []
    # Seuls les morceaux qui jouent de la source la consomment (un silence n'en lit rien) : ``asplit`` n'a que ces sorties,
    # et une entrée que personne ne lit (arrêt sur image sur une séquence imbriquée) est refermée par ``anullsink``.
    consuming = [i for i, (piece, kind) in enumerate(segments) if kind is not RunKind.HOLD and abs(piece.speed) >= MIN_AUDIO_SPEED]
    entries: dict[int, str] = {}
    if len(consuming) == 0:
        chains.append(f"[{source_label}]anullsink")
    elif len(consuming) == 1:
        entries[consuming[0]] = source_label
    else:
        labels = [f"{prefix}s{i}" for i in consuming]
        chains.append(f"[{source_label}]asplit={len(consuming)}" + "".join(f"[{label}]" for label in labels))
        entries = dict(zip(consuming, labels))
    pieces_out: list[str] = []
    for index, (piece, kind) in enumerate(segments):
        last = index == count - 1
        joined = not last and (piece.t1 - piece.t0) > 3 * AUDIO_CROSSFADE and (
            segments[index + 1][0].t1 - segments[index + 1][0].t0
        ) > 3 * AUDIO_CROSSFADE
        body = _audio_piece_chain(piece, kind, extend=AUDIO_CROSSFADE if joined else 0.0, preserve_pitch=preserve_pitch)
        label = f"{prefix}p{index}"
        if index in entries:
            chains.append(f"[{entries[index]}]{body}[{label}]")
        else:
            chains.append(f"{body}[{label}]")
        pieces_out.append(label)
    current = pieces_out[0]
    for index in range(1, count):
        fade = (
            (segments[index - 1][0].t1 - segments[index - 1][0].t0) > 3 * AUDIO_CROSSFADE
            and (segments[index][0].t1 - segments[index][0].t0) > 3 * AUDIO_CROSSFADE
        )
        merged = f"{prefix}m{index}"
        if fade:
            chains.append(f"[{current}][{pieces_out[index]}]acrossfade=d={_fmt(AUDIO_CROSSFADE)}:c1=tri:c2=tri[{merged}]")
        else:
            chains.append(f"[{current}][{pieces_out[index]}]concat=n=2:v=0:a=1[{merged}]")
        current = merged
    chains.append(f"[{current}]{tail}[{out}]")
    return AudioStage(tuple(chains), out)


__all__ = [
    "AUDIO_CROSSFADE",
    "AUDIO_RATE",
    "MAX_REVERSE_BYTES",
    "AudioStage",
    "RetimeError",
    "VideoStage",
    "audio_stage",
    "tempo_filters",
    "frame_for_tick",
    "nearest_frame",
    "ticks_in",
    "video_stage",
]
