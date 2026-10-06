"""Synchronisation automatique de plusieurs sources par leur son (locale, NumPy seul, décodage en flux).

Cas classique : plusieurs caméras et un enregistreur captent le même événement sonore, mais ne démarrent pas ensemble.
On mesure, pour chaque source, **où** son média doit commencer sur une timeline commune pour que le son coïncide.

Méthode en deux passes, du grossier au précis :

1. **Passe grossière** (toute la plage, mémoire minuscule) : le son est décodé en flux (mono, 8 kHz) et réduit à une
   *enveloppe d'énergie* à 100 Hz (logarithme de l'énergie d'un signal pré-accentué, privé de sa moyenne locale : un
   écart de volume ou une dérive lente disparaissent). Deux heures de son tiennent en 3 Mo. Les enveloppes sont corrélées
   par FFT (corrélation **normalisée**, donc insensible au recouvrement partiel) : on obtient le décalage à 10 ms près.
2. **Passe fine** : une fenêtre d'une vingtaine de secondes, choisie là où le son est le plus actif, est décodée dans les
   deux sources et corrélée par **GCC-PHAT** (corrélation généralisée à pondération de phase, bande 100 Hz – 3,5 kHz),
   puis affinée par interpolation parabolique : précision sous la milliseconde.

La **confiance** (0 à 1) tient compte de la netteté du pic, de son écart au second pic et de l'accord des deux passes ;
elle ne dit jamais « bonne » quand l'algorithme ne peut pas le déterminer (silence, musique périodique, sources sans
rapport) : le résultat est alors ``UNCERTAIN`` ou ``FAILED`` (``core.multicam_model.SyncStatus``).

NumPy n'est importé qu'à l'appel (comme le suivi) ; aucune dépendance supplémentaire. Les enveloppes sont mises en cache
(:mod:`core.audio_sync_cache`) ; l'analyse ne modifie jamais le projet : l'appelant applique le résultat
(``core.multicam_ops.apply_sync``).
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .audio_sync_cache import AudioSyncCache, cache_key
from .multicam_model import SyncStatus
from .process_supervisor import default_supervisor
from .task_queue import CancelToken
from .tool_paths import find_media_tool

if TYPE_CHECKING:
    import numpy as np
    import numpy.typing as npt

    FloatArray = npt.NDArray[np.float32]

LOGGER = logging.getLogger("kut_studio.multicam")

SAMPLE_RATE = 8000
"""Fréquence de décodage (Hz) : le son utile à la synchronisation (voix, bruits, musique) y tient largement."""
ENVELOPE_RATE = 100
"""Cadence de l'enveloppe d'énergie (Hz) : un pas de 10 ms."""
_BLOCK = SAMPLE_RATE // ENVELOPE_RATE
_PRE_EMPHASIS = 0.95
_LOCAL_MEAN_FRAMES = 200
"""Fenêtre (2 s) de la moyenne locale retranchée à l'enveloppe."""

MIN_OVERLAP_SECONDS = 4.0
"""Recouvrement minimal entre deux sources pour qu'une mesure ait un sens."""
FINE_WINDOW_SECONDS = 24.0
FINE_MARGIN_SECONDS = 0.35
"""Marge de la passe fine autour du décalage grossier (la passe grossière est précise à ±10 ms)."""
_MAIN_LOBE_FRAMES = 30
"""Largeur (0,3 s) d'un pic de corrélation d'enveloppes : les pics plus éloignés comptent comme des rivaux."""
_BAND = (100.0, 3500.0)

EXCELLENT_THRESHOLD = 0.80
GOOD_THRESHOLD = 0.55
UNCERTAIN_THRESHOLD = 0.30

_CHUNK_SECONDS = 4.0
_PARAMS: dict[str, object] = {
    "rate": SAMPLE_RATE, "envelope": ENVELOPE_RATE, "emphasis": _PRE_EMPHASIS, "mean": _LOCAL_MEAN_FRAMES,
}


class AudioSyncError(RuntimeError):
    """Décodage ou analyse impossible pour une source (la synchronisation des autres continue)."""


class SyncCancelled(Exception):
    """Analyse interrompue par l'utilisateur."""


@dataclass(frozen=True)
class SyncSource:
    """Une source à synchroniser.

    ``key`` : identifiant choisi par l'appelant (angle, média). ``start`` / ``duration`` : plage analysée dans le média
    (temps du média ; ``None`` = jusqu'à la fin) ; le décalage rendu désigne l'instant ``start`` de la source.
    """

    key: str
    path: str
    start: float = 0.0
    duration: float | None = None


@dataclass(frozen=True)
class SourceSync:
    """Résultat pour une source : où son instant ``start`` se place sur la timeline commune, et avec quelle confiance."""

    key: str
    offset: float | None
    confidence: float
    status: SyncStatus
    reference_key: str = ""
    detail: str = ""


@dataclass(frozen=True)
class SyncResult:
    """Résultat d'une analyse : une entrée par source, dans l'ordre d'appel. Le plus petit décalage placé vaut 0."""

    reference: str
    sources: tuple[SourceSync, ...]
    elapsed: float
    cancelled: bool = False

    def by_key(self) -> dict[str, SourceSync]:
        return {item.key: item for item in self.sources}


def status_for(confidence: float) -> SyncStatus:
    """Étiquette de fiabilité d'une confiance (0 à 1)."""
    if confidence >= EXCELLENT_THRESHOLD:
        return SyncStatus.EXCELLENT
    if confidence >= GOOD_THRESHOLD:
        return SyncStatus.GOOD
    if confidence >= UNCERTAIN_THRESHOLD:
        return SyncStatus.UNCERTAIN
    return SyncStatus.FAILED


def _numpy() -> Any:
    import numpy as np

    return np


# ---------------------------------------------------------------------------
# Décodage en flux
# ---------------------------------------------------------------------------


def decode_command(path: str, start: float, duration: float | None) -> list[str]:
    """Commande FFmpeg : PCM 16 bits mono à 8 kHz sur la sortie standard, calé sur le temps du **média**.

    ``aresample=async=1:first_pts=0`` comble par du silence le retard initial d'un flux audio qui ne commence pas à
    ``start`` (flux démarrant à 0,5 s, audio AAC seul dont la première trame tombe quelques ms plus tard) : l'échantillon 0
    est toujours l'instant ``start`` du média, comme pour ``atrim`` dans le graphe d'export.
    """
    ffmpeg = find_media_tool("ffmpeg")
    if ffmpeg is None:
        raise AudioSyncError("FFmpeg est introuvable : synchronisation audio impossible.")
    command = [ffmpeg, "-nostdin", "-hide_banner", "-v", "error"]
    if start > 0:
        command += ["-ss", f"{start:.6f}"]
    if duration is not None:
        command += ["-t", f"{duration:.6f}"]
    command += [
        "-i", path, "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE),
        "-af", "aresample=async=1:first_pts=0", "-f", "s16le", "pipe:1",
    ]
    return command


def _iter_pcm(
    path: str, start: float, duration: float | None, cancelled: Callable[[], bool],
) -> Iterator[Any]:
    """Blocs de PCM (``float32`` dans [-1, 1]) lus en flux ; l'annulation tue FFmpeg aussitôt."""
    np = _numpy()
    supervisor = default_supervisor()
    process = supervisor.popen(decode_command(path, start, duration), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout_pipe, stderr_pipe = process.stdout, process.stderr
    if stdout_pipe is None or stderr_pipe is None:  # jamais : ``PIPE`` demandé ci-dessus
        raise AudioSyncError("FFmpeg a été lancé sans ses tubes de sortie.")
    errors: list[bytes] = []
    drain = threading.Thread(target=lambda: errors.append(stderr_pipe.read()), daemon=True)
    drain.start()
    chunk_bytes = int(SAMPLE_RATE * _CHUNK_SECONDS) * 2
    produced = 0
    leftover = b""
    try:
        while True:
            if cancelled():
                raise SyncCancelled()
            data = stdout_pipe.read(chunk_bytes)
            if not data:
                break
            data = leftover + data
            usable = len(data) - (len(data) % 2)
            leftover = data[usable:]
            if usable:
                produced += usable
                yield np.frombuffer(data[:usable], dtype="<i2").astype(np.float32) / 32768.0
    finally:
        if process.poll() is None:
            process.kill()
        try:
            stdout_pipe.close()
        except OSError:
            pass
        supervisor.finish(process)
        drain.join(timeout=2.0)
    if produced == 0:
        message = b"".join(errors).decode("utf-8", "replace").strip().splitlines()
        reason = message[-1] if message else "aucun flux audio"
        LOGGER.warning("Synchronisation audio : %s n'a produit aucun son (%s)", os.path.basename(path), reason)
        raise AudioSyncError(reason)


def read_window(path: str, start: float, duration: float, cancelled: Callable[[], bool]) -> FloatArray:
    np = _numpy()
    blocks = list(_iter_pcm(path, max(0.0, start), duration, cancelled))
    return np.concatenate(blocks) if blocks else np.zeros(0, dtype=np.float32)


# ---------------------------------------------------------------------------
# Enveloppe d'énergie (passe grossière)
# ---------------------------------------------------------------------------


def _envelope_blocks(samples: FloatArray, carry: FloatArray, last: float) -> tuple[FloatArray, FloatArray, float]:
    """Énergie par bloc de 10 ms d'un signal pré-accentué ; renvoie (énergies, reste non bloqué, dernier échantillon)."""
    np = _numpy()
    data = np.concatenate([carry, samples]) if carry.size else samples
    usable = (data.size // _BLOCK) * _BLOCK
    rest = data[usable:]
    if usable == 0:
        return np.zeros(0, dtype=np.float32), rest, last
    block = data[:usable]
    previous = np.empty_like(block)
    previous[0] = last
    previous[1:] = block[:-1]
    emphasised = block - _PRE_EMPHASIS * previous
    energy = np.sqrt((emphasised.reshape(-1, _BLOCK) ** 2).mean(axis=1))
    return energy.astype(np.float32), rest, float(block[-1])


def raw_envelope(
    path: str, start: float, duration: float | None, cancelled: Callable[[], bool],
    progress: Callable[[float], None] | None = None,
) -> FloatArray:
    """Énergie logarithmique par pas de 10 ms (brute : la normalisation vient après, hors du cache)."""
    np = _numpy()
    pieces: list[FloatArray] = []
    carry = np.zeros(0, dtype=np.float32)
    last = 0.0
    done = 0
    expected = None if duration is None else max(1, int(duration * ENVELOPE_RATE))
    for samples in _iter_pcm(path, start, duration, cancelled):
        energy, carry, last = _envelope_blocks(samples, carry, last)
        if energy.size:
            pieces.append(energy)
            done += energy.size
            if progress is not None and expected is not None:
                progress(min(1.0, done / expected))
    if not pieces:
        raise AudioSyncError("son trop court")
    return np.log(np.concatenate(pieces) + 1e-5).astype(np.float32)


def normalize_envelope(raw: FloatArray) -> FloatArray:
    """Retire la moyenne locale (volume, dérive lente) puis réduit à une variance unité."""
    np = _numpy()
    values = raw.astype(np.float64)
    window = min(_LOCAL_MEAN_FRAMES, max(3, values.size // 2))
    cumulative = np.concatenate([[0.0], np.cumsum(values)])
    index = np.arange(values.size)
    low = np.maximum(0, index - window // 2)
    high = np.minimum(values.size, index + window // 2 + 1)
    local_mean = (cumulative[high] - cumulative[low]) / (high - low)
    detail = values - local_mean
    spread = float(detail.std())
    if spread < 1e-4:
        raise AudioSyncError("signal plat (silence ou son constant)")
    return (detail / spread).astype(np.float32)


# ---------------------------------------------------------------------------
# Corrélations
# ---------------------------------------------------------------------------


def _fast_length(minimum: int) -> int:
    """Plus petite longueur ≥ ``minimum`` de la forme 2^a·3^b·5^c (les longueurs lisses sont ~10× plus rapides)."""
    best = 1 << max(1, (minimum - 1).bit_length())
    power5 = 1
    while power5 < best:
        power3 = power5
        while power3 < best:
            value = power3
            while value < minimum:
                value *= 2
            best = min(best, value)
            power3 *= 3
        power5 *= 5
    return best


@dataclass(frozen=True)
class _Coarse:
    lag: int
    peak: float
    rival: float
    confidence: float


def coarse_lag(reference: FloatArray, target: FloatArray, min_overlap: int) -> _Coarse | None:
    """Décalage (en pas d'enveloppe) tel que ``reference[t + lag] ≈ target[t]``, par corrélation normalisée.

    La corrélation brute est divisée par l'énergie des deux portions qui se recouvrent : elle ne favorise pas les grands
    recouvrements et supporte qu'une caméra démarre plus tard ou finisse plus tôt. ``None`` : aucun recouvrement
    suffisant.
    """
    np = _numpy()
    size_a, size_b = int(reference.size), int(target.size)
    n = _fast_length(size_a + size_b)
    spectrum = np.fft.rfft(reference, n) * np.conj(np.fft.rfft(target, n))
    circular = np.fft.irfft(spectrum, n)
    lags = np.arange(-(size_b - 1), size_a)
    raw = circular[lags % n].astype(np.float64)
    t0 = np.maximum(0, -lags)
    t1 = np.minimum(size_b, size_a - lags)
    overlap = t1 - t0
    valid = overlap >= min_overlap
    if not valid.any():
        return None
    cumulative_a = np.concatenate([[0.0], np.cumsum(reference.astype(np.float64) ** 2)])
    cumulative_b = np.concatenate([[0.0], np.cumsum(target.astype(np.float64) ** 2)])
    energy_a = cumulative_a[np.clip(t1 + lags, 0, size_a)] - cumulative_a[np.clip(t0 + lags, 0, size_a)]
    energy_b = cumulative_b[np.clip(t1, 0, size_b)] - cumulative_b[np.clip(t0, 0, size_b)]
    with np.errstate(divide="ignore", invalid="ignore"):
        normalised = raw / np.sqrt(energy_a * energy_b)
    normalised = np.where(valid & np.isfinite(normalised), normalised, -1.0)
    best = int(np.argmax(normalised))
    peak = float(normalised[best])
    keep = np.abs(lags - lags[best]) > _MAIN_LOBE_FRAMES
    rival = float(normalised[keep & valid].max()) if (keep & valid).any() else 0.0
    rival = max(rival, 0.0)
    strength = min(1.0, max(0.0, (peak - 0.08) / 0.32))
    separation = 0.0 if peak <= 1e-6 else min(1.0, max(0.0, (peak - rival) / peak))
    confidence = strength * min(1.0, separation / 0.45) if peak > 0 else 0.0
    return _Coarse(int(lags[best]), peak, rival, float(confidence))


@dataclass(frozen=True)
class _Fine:
    kappa: float
    psr: float
    confidence: float


def fine_lag(
    reference: FloatArray, target: FloatArray, expected: float, radius: float,
) -> _Fine | None:
    """Décalage fin (échantillons) par GCC-PHAT : ``reference[s + κ] ≈ target[s]`` avec κ ≈ ``expected`` ± ``radius``.

    La pondération de phase blanchit le spectre : seul le *retard* compte, pas le timbre ni le volume, ce qui rend le
    pic net même avec du bruit, de la réverbération ou deux micros différents. La bande 100 Hz – 3,5 kHz écarte les
    ronflements et le souffle.
    """
    np = _numpy()
    n = _fast_length(int(reference.size + target.size))
    spectrum = np.fft.rfft(reference, n) * np.conj(np.fft.rfft(target, n))
    magnitude = np.abs(spectrum)
    frequencies = np.fft.rfftfreq(n, 1.0 / SAMPLE_RATE)
    band = (frequencies >= _BAND[0]) & (frequencies <= _BAND[1])
    weighted = np.where(band, spectrum / np.maximum(magnitude, 1e-12), 0.0)
    correlation = np.fft.irfft(weighted, n)
    lags = np.arange(n)
    lags = np.where(lags >= n // 2, lags - n, lags)
    window = np.abs(lags - expected) <= radius
    if not window.any():
        return None
    scores = np.where(window, correlation, -np.inf)
    best = int(np.argmax(scores))
    peak = float(correlation[best])
    # Interpolation parabolique : précision sous l'échantillon.
    kappa = float(lags[best])
    if 0 < best < n - 1 and window[best - 1] and window[best + 1]:
        left, right = float(correlation[best - 1]), float(correlation[best + 1])
        denominator = left - 2.0 * peak + right
        if abs(denominator) > 1e-15:
            kappa += 0.5 * (left - right) / denominator
    outside = np.abs(lags - lags[best]) > 40
    noise = correlation[outside]
    spread = float(noise.std()) if noise.size else 0.0
    psr = (peak - float(noise.mean())) / spread if spread > 1e-15 else 0.0
    confidence = min(1.0, max(0.0, (psr - 4.0) / 8.0))
    return _Fine(kappa, psr, float(confidence))


# ---------------------------------------------------------------------------
# Mesure d'une paire et analyse de N sources
# ---------------------------------------------------------------------------


@dataclass
class _Prepared:
    """Une source dont l'enveloppe est prête."""

    source: SyncSource
    envelope: FloatArray | None = None
    error: str = ""

    @property
    def usable(self) -> bool:
        return self.envelope is not None


@dataclass(frozen=True)
class _Pair:
    offset: float
    confidence: float
    detail: str


def _pick_window(envelope: FloatArray, low: int, high: int, frames: int) -> int:
    """Début (en pas d'enveloppe) de la fenêtre de ``frames`` pas la plus active dans ``[low, high)``."""
    np = _numpy()
    span = max(0, high - low - frames)
    if span == 0:
        return low
    activity = np.abs(envelope[low:high].astype(np.float64))
    cumulative = np.concatenate([[0.0], np.cumsum(activity)])
    sums = cumulative[frames:frames + span + 1] - cumulative[:span + 1]
    return low + int(np.argmax(sums))


def measure_pair(
    reference: _Prepared, target: _Prepared, cancelled: Callable[[], bool],
) -> _Pair | None:
    """Décalage de ``target`` par rapport à ``reference`` (secondes) et sa confiance, ou ``None`` (aucun recouvrement)."""
    ref_env, tgt_env = reference.envelope, target.envelope
    if ref_env is None or tgt_env is None:
        return None
    overlap_needed = int(min(ref_env.size, tgt_env.size) * 0.1)
    min_overlap = max(int(MIN_OVERLAP_SECONDS * ENVELOPE_RATE), min(overlap_needed, 60 * ENVELOPE_RATE))
    min_overlap = min(min_overlap, int(min(ref_env.size, tgt_env.size) * 0.9))
    coarse = coarse_lag(ref_env, tgt_env, min_overlap)
    if coarse is None:
        return None
    lag_seconds = coarse.lag / ENVELOPE_RATE
    detail = f"grossier : pic {coarse.peak:.2f}, rival {coarse.rival:.2f}"
    if coarse.peak >= UNCERTAIN_THRESHOLD and coarse.rival >= 0.9 * coarse.peak:
        detail += " (plusieurs décalages équivalents : son périodique, ex. musique en boucle)"
    # Fenêtre active du recouvrement, côté référence.
    low = max(0, coarse.lag)
    high = min(ref_env.size, tgt_env.size + coarse.lag)
    frames = min(int(FINE_WINDOW_SECONDS * ENVELOPE_RATE), max(0, high - low))
    if frames < int(MIN_OVERLAP_SECONDS * ENVELOPE_RATE):
        return _Pair(lag_seconds, min(coarse.confidence, 0.45), detail + " ; recouvrement trop court pour l'affinage")
    window_start = _pick_window(ref_env, low, high, frames)
    ref_start = reference.source.start + window_start / ENVELOPE_RATE
    margin = FINE_MARGIN_SECONDS
    # Instant correspondant côté cible, reculé de la marge (borné à 0).
    tgt_start = target.source.start + (window_start - coarse.lag) / ENVELOPE_RATE - margin
    shortfall = max(0.0, target.source.start - tgt_start)
    tgt_start += shortfall
    window_seconds = frames / ENVELOPE_RATE
    ref_pcm = read_window(reference.source.path, ref_start, window_seconds, cancelled)
    tgt_pcm = read_window(target.source.path, tgt_start, window_seconds + 2 * margin, cancelled)
    if ref_pcm.size < SAMPLE_RATE or tgt_pcm.size < SAMPLE_RATE:
        return _Pair(lag_seconds, min(coarse.confidence, 0.45), detail + " ; fenêtre fine trop courte")
    # La fenêtre cible a été décodée ``margin`` plus tôt (moins la butée à 0) : à décalage vrai égal au décalage grossier,
    # ``reference[s + κ] ≈ target[s]`` donne κ = -shift_seconds · fréquence ; le vrai décalage est
    # ``lag + shift_seconds + κ / fréquence``.
    shift_seconds = margin - shortfall
    fine = fine_lag(ref_pcm, tgt_pcm, -shift_seconds * SAMPLE_RATE, (margin + 0.05) * SAMPLE_RATE)
    if fine is None:
        return _Pair(lag_seconds, min(coarse.confidence, 0.4), detail + " ; affinage impossible")
    true_lag = lag_seconds + shift_seconds + fine.kappa / SAMPLE_RATE
    agreement = abs(true_lag - lag_seconds)
    detail += f" ; fin : psr {fine.psr:.1f}"
    confidence = min(coarse.confidence, max(fine.confidence, 0.0)) if agreement <= margin else 0.0
    if agreement > margin:
        detail += f" ; désaccord {agreement:.2f} s entre les passes"
    return _Pair(true_lag, float(confidence), detail)


def _prepare(
    source: SyncSource, cache: AudioSyncCache | None, cancelled: Callable[[], bool],
    progress: Callable[[float], None],
) -> _Prepared:
    """Enveloppe normalisée d'une source (depuis le cache si possible) ; une erreur est notée, jamais levée."""
    prepared = _Prepared(source)
    if not source.path or not os.path.isfile(source.path):
        prepared.error = "média introuvable"
        return prepared
    key = cache_key(source.path, source.start, source.duration, _PARAMS)
    raw = cache.load(key) if cache is not None else None
    try:
        if raw is None:
            raw = raw_envelope(source.path, source.start, source.duration, cancelled, progress)
            if cache is not None:
                cache.store(key, raw)
        else:
            progress(1.0)
        prepared.envelope = normalize_envelope(raw)
    except SyncCancelled:
        raise
    except AudioSyncError as error:
        prepared.error = str(error)
    except Exception as error:  # noqa: BLE001 - une source illisible ne doit pas arrêter les autres
        LOGGER.exception("Synchronisation audio : analyse de %s impossible", os.path.basename(source.path))
        prepared.error = f"erreur : {error}"
    return prepared


def _usable_seconds(prepared: _Prepared) -> float:
    return 0.0 if prepared.envelope is None else prepared.envelope.size / ENVELOPE_RATE


def analyze_sync(
    sources: Sequence[SyncSource],
    *,
    reference: str | None = None,
    cancelled: Callable[[], bool] = lambda: False,
    progress: Callable[[float, str], None] = lambda fraction, stage: None,
    cache: AudioSyncCache | None = None,
) -> SyncResult:
    """Synchronise ``sources`` sur la source de référence ; ne lève pas (une source en échec est signalée ``FAILED``).

    La référence est ``reference`` si elle est donnée et exploitable, sinon la source utilisable la plus longue. Les
    autres sont mesurées contre elle ; une mesure peu fiable est retentée contre les sources déjà placées (chaînage), en
    gardant la meilleure. Décalages normalisés : le plus petit décalage placé vaut 0. L'annulation retourne un résultat
    partiel (``cancelled=True``) sans lever.
    """
    started = time.monotonic()
    keys = [source.key for source in sources]
    if len(set(keys)) != len(keys):
        raise ValueError("Les clés des sources doivent être uniques.")
    total = max(1, len(sources))
    prepared: list[_Prepared] = []

    def envelope_progress(index: int, stage: str) -> Callable[[float], None]:
        return lambda fraction: progress((index + fraction) / (total * 2), stage)

    try:
        for index, source in enumerate(sources):
            stage = f"enveloppe {source.key}"
            progress(index / (total * 2), stage)
            prepared.append(_prepare(source, cache, cancelled, envelope_progress(index, stage)))
        results, chosen = _measure_all(prepared, reference, cancelled, progress, total)
    except SyncCancelled:
        LOGGER.info("Synchronisation audio annulée")
        cancelled_result = tuple(
            SourceSync(source.key, None, 0.0, SyncStatus.FAILED, detail="annulée") for source in sources
        )
        return SyncResult("", cancelled_result, time.monotonic() - started, cancelled=True)
    elapsed = time.monotonic() - started
    summary = " ; ".join(
        f"{entry.key}: {entry.offset if entry.offset is None else round(entry.offset, 4)} s, "
        f"confiance {entry.confidence:.2f} ({entry.status.value})"
        + (f" vs {entry.reference_key}" if entry.reference_key else "")
        for entry in results
    )
    LOGGER.info("Synchronisation audio : %d source(s), référence %s, %.2f s — %s", len(sources), chosen or "aucune",
                elapsed, summary)
    return SyncResult(chosen, tuple(results), elapsed)


def _measure_all(
    prepared: list[_Prepared], reference: str | None, cancelled: Callable[[], bool],
    progress: Callable[[float, str], None], total: int,
) -> tuple[list[SourceSync], str]:
    usable = [item for item in prepared if item.usable]
    chosen = ""
    anchor: _Prepared | None = None
    if reference is not None:
        anchor = next((item for item in usable if item.source.key == reference), None)
        if anchor is None:
            LOGGER.warning("Synchronisation audio : la référence demandée (%s) est inutilisable, une autre est choisie", reference)
    if anchor is None and usable:
        anchor = max(usable, key=_usable_seconds)
    outcome: dict[str, SourceSync] = {}
    placed: dict[str, float] = {}          # décalage de chaque source placée, dans le repère de la référence
    if anchor is not None:
        chosen = anchor.source.key
        outcome[chosen] = SourceSync(chosen, 0.0, 1.0, SyncStatus.NONE, "", "référence")
        placed[chosen] = 0.0
    others = sorted((item for item in prepared if item is not anchor), key=lambda item: -_usable_seconds(item))
    for index, item in enumerate(others):
        key = item.source.key
        progress(0.5 + index / (total * 2), f"mesure {key}")
        if not item.usable:
            LOGGER.warning("Synchronisation audio : %s ignorée (%s)", key, item.error)
            outcome[key] = SourceSync(key, None, 0.0, SyncStatus.FAILED, detail=item.error)
            continue
        if anchor is None:
            outcome[key] = SourceSync(key, None, 0.0, SyncStatus.FAILED, detail="aucune référence exploitable")
            continue
        best: tuple[float, float, str, str] | None = None      # (confiance, décalage, référence, détail)
        candidates = [anchor] + [p for p in prepared if p.source.key in placed and p is not anchor]
        for candidate in candidates:
            pair = measure_pair(candidate, item, cancelled)
            if pair is None:
                continue
            offset = placed[candidate.source.key] + pair.offset
            if best is None or pair.confidence > best[0]:
                best = (pair.confidence, offset, candidate.source.key, pair.detail)
            if best[0] >= GOOD_THRESHOLD:
                break
        if best is None:
            outcome[key] = SourceSync(key, None, 0.0, SyncStatus.FAILED, anchor.source.key, "aucun recouvrement suffisant")
            continue
        confidence, offset, via, detail = best
        status = status_for(confidence)
        if status is SyncStatus.FAILED:
            LOGGER.warning("Synchronisation audio : %s non synchronisée (%s)", key, detail)
            outcome[key] = SourceSync(key, None, confidence, status, via, detail)
            continue
        if status is SyncStatus.UNCERTAIN:
            LOGGER.warning("Synchronisation audio : %s incertaine (confiance %.2f, %s)", key, confidence, detail)
        placed[key] = offset
        outcome[key] = SourceSync(key, offset, confidence, status, via, detail)
    low = min(placed.values()) if placed else 0.0
    ordered = []
    for item in prepared:
        entry = outcome[item.source.key]
        if entry.offset is not None:
            entry = SourceSync(entry.key, entry.offset - low, entry.confidence, entry.status, entry.reference_key, entry.detail)
        ordered.append(entry)
    return ordered, chosen


# ---------------------------------------------------------------------------
# Tâche d'arrière-plan (même contrat que ``TrackingJob``)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AudioSyncSnapshot:
    """État immuable d'une tâche de synchronisation, lisible depuis le thread de l'interface."""

    state: str                      # queued / running / finished / cancelled / failed
    progress: float
    stage: str
    result: SyncResult | None = None
    message: str = ""


@dataclass
class AudioSyncJob:
    """Synchronisation à lancer sur la file d'analyses (``runtime.schedule_analysis(clé, job.run)``).

    ``run`` ne lève jamais ; ``snapshot`` est sûr entre threads ; ``cancel`` arrête FFmpeg au prochain bloc.
    """

    sources: tuple[SyncSource, ...]
    reference: str | None = None
    cache: AudioSyncCache | None = None
    session_id: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _cancel: threading.Event = field(default_factory=threading.Event, repr=False)
    _state: str = "queued"
    _progress: float = 0.0
    _stage: str = ""
    _result: SyncResult | None = None
    _message: str = ""

    def cancel(self) -> None:
        self._cancel.set()
        with self._lock:
            if self._state == "queued":
                self._state = "cancelled"

    def is_cancelled(self, token: CancelToken | None = None) -> bool:
        return self._cancel.is_set() or bool(token is not None and token.cancelled)

    def snapshot(self) -> AudioSyncSnapshot:
        with self._lock:
            return AudioSyncSnapshot(self._state, self._progress, self._stage, self._result, self._message)

    def _report(self, fraction: float, stage: str) -> None:
        with self._lock:
            self._progress, self._stage = max(0.0, min(1.0, fraction)), stage

    def run(self, token: CancelToken | None = None) -> None:
        with self._lock:
            if self._state == "cancelled":
                return
            self._state = "running"
        try:
            result = analyze_sync(
                self.sources, reference=self.reference, cache=self.cache, progress=self._report,
                cancelled=lambda: self.is_cancelled(token),
            )
        except Exception as error:  # noqa: BLE001 - la tâche ne doit jamais faire tomber la file d'analyses
            LOGGER.exception("Synchronisation audio : l'analyse a échoué")
            with self._lock:
                self._state, self._message = "failed", f"error:{error}"
            return
        with self._lock:
            self._result = result
            self._state = "cancelled" if result.cancelled else "finished"
            self._progress = 1.0 if not result.cancelled else self._progress


def self_check() -> str | None:
    """Contrôle rapide (``main.py --smoke-test``) : deux signaux synthétiques de décalage connu, sans FFmpeg.

    Retourne ``None`` si tout va bien, sinon la description du problème (même contrat que les autres contrôles).
    """
    np = _numpy()
    generator = np.random.default_rng(7)
    base = generator.standard_normal(ENVELOPE_RATE * 40).astype(np.float32)
    kernel = np.ones(7, dtype=np.float32) / 7.0
    smooth = np.convolve(base, kernel, mode="same")
    reference = normalize_envelope(smooth)
    target = normalize_envelope(smooth[300:] + 0.1 * generator.standard_normal(smooth.size - 300).astype(np.float32))
    found = coarse_lag(reference, target, ENVELOPE_RATE * 4)
    if found is None or found.lag != 300:
        return "passe grossière : décalage de 300 pas non retrouvé"
    tone = generator.standard_normal(SAMPLE_RATE * 3).astype(np.float32)
    delayed = np.concatenate([np.zeros(240, dtype=np.float32), tone])
    fine = fine_lag(delayed[: SAMPLE_RATE * 2], tone, 240.0, 400.0)  # reference[s + 240] = target[s]
    if fine is None or abs(fine.kappa - 240.0) > 0.6:
        return "passe fine : décalage de 240 échantillons non retrouvé"
    return None


__all__ = [
    "AudioSyncError",
    "AudioSyncJob",
    "AudioSyncSnapshot",
    "SyncCancelled",
    "SyncResult",
    "SyncSource",
    "SourceSync",
    "analyze_sync",
    "self_check",
    "status_for",
]
