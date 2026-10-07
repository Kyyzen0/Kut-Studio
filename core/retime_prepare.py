"""Préparation des images intermédiaires d'un clip remappé : mélange d'images et flux optique.

Le graphe FFmpeg sait *choisir* une image source, pas en *fabriquer* une. Quand un clip demande ``BLENDING`` ou ``OPTICAL_FLOW``,
ce module produit en amont les images qui manquent et les range dans **un fichier vidéo sans perte** que le graphe relit comme
n'importe quelle entrée (``-i``) : l'export, l'aperçu fidèle et les scopes lisent donc exactement les mêmes images.

**Fenêtre (aperçu).** L'aperçu fidèle garde la couche entière et ne découpe qu'à la sortie (``-ss`` / ``-t``) : préparer un
clip de dix minutes pour un segment de deux secondes serait absurde. Avec ``window`` (ticks locaux au clip), seuls les ticks de la
fenêtre sont fabriqués ; le reste du run est rempli de noir (``lead`` / ``trail``), sans coût, et jeté par la découpe. Un export
n'a pas de fenêtre : tout le clip est préparé.

**Seulement ce qui est nécessaire.** Le plan (:mod:`core.frame_interpolation`) dit, run par run, quelles images sont
intermédiaires. Un run dont toutes les images tombent sur une image source (200 %, arrêt, retournement sans ralenti…) n'est **pas**
préparé : il reste dans le graphe d'échantillonnage, qui produit alors exactement les mêmes images. Un run qui en demande est
préparé en entier (ses images exactes aussi, décodées par le même chemin), image par image, dans l'ordre des ticks de sortie ; un run
en lecture arrière est écrit dans l'ordre de la source et inversé par le graphe (``reverse``).

**Le mouvement est celui du média, pas du clip.** Les flux sont calculés par paire d'images consécutives et rangés dans le cache
(:mod:`core.flow_cache`), clé = média réellement décodé + paire + grille + moteur. Changer la vitesse, déplacer un point de la
courbe, couper ou déplacer le clip ne recalcule aucune paire : seules les images à fabriquer changent. Le flux préparé lui-même est
aussi en cache, sous une clé qui contient le plan des images (il ne se refabrique que s'il a changé).

**Déterministe.** Les images fabriquées dérivent de la forme stockée des analyses (:class:`~core.optical_flow.PairAnalysis`) :
un calcul à froid et une relecture du cache donnent les mêmes octets. L'image exacte d'un tick (``t = 0``) est l'image décodée
telle quelle.

**Fiable.** Chaque image dit ce qu'elle est (:class:`~core.optical_flow.Fallback`) ; le :class:`PrepareReport` en tient le compte,
l'interface l'affiche. Un décodage qui ne rend pas exactement les images demandées est une erreur, jamais une approximation.
L'écriture est atomique (``.tmp`` puis ``os.replace``) et annulable à tout moment (``cancelled``), les processus FFmpeg sont
supervisés (:mod:`core.process_supervisor`).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import threading
import time
from functools import lru_cache
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .cache_keys import SignatureMemo, source_signature
from .flow_cache import FlowCache, cache_key
from .frame_interpolation import FrameSample, InterpolationPlan, plan_interpolation
from .optical_flow import (
    BackendPreference,
    Fallback,
    FlowCancelled,
    OpticalFlowEngine,
    PairAnalysis,
    blend_frames,
)
from .process_supervisor import default_supervisor, supervised_run
from .retime_graph import TRIM_END_MARGIN, TRIM_START_MARGIN, PreparedRun, RetimeError
from .time_map import RunKind, TimeMap
from .time_remapping import FlowQuality, TimeInterpolation
from .tool_paths import find_media_tool

if TYPE_CHECKING:
    import numpy as np
    import numpy.typing as npt

    Frame8 = npt.NDArray[np.uint8]
    FrameF = npt.NDArray[np.float32]

LOGGER = logging.getLogger("kut_studio.flow")

PREPARE_VERSION = 2
"""À incrémenter quand un flux préparé ne serait plus celui que le code produirait (format, ordre, arrondi)."""

_WINDOW = 4
"""Images décodées gardées en mémoire (une paire en cours, plus la marge d'un run qui revient sur ses pas d'une image)."""
_SEEK_MARGIN_FRAMES = 2
_EXACT_SEEK_SUFFIXES = frozenset({".mp4", ".m4v", ".mov", ".qt", ".mkv", ".webm"})
"""Conteneurs dont le saut ``-ss`` tombe sur l'image clé *précédente* (puis FFmpeg écarte jusqu'à la cible). Les autres (MPEG-TS,
MPEG-PS, AVI…) peuvent tomber sur la suivante : des images manqueraient, ou aucune ne viendrait (un TS à une seule image clé) ;
ils sont donc décodés depuis le début, plus lentement mais exacts."""


def seeks_exactly(media_path: str) -> bool:
    """Le décodage peut-il démarrer au milieu du fichier sans perdre d'image ? (liste blanche : l'inconnu se décode depuis zéro)"""
    return os.path.splitext(media_path)[1].lower() in _EXACT_SEEK_SUFFIXES
_FRESH_SIGNATURES = SignatureMemo(ttl=0.0)


class PrepareCancelled(Exception):
    """La préparation a été interrompue à la demande de l'appelant ; rien n'a été conservé."""


class PrepareError(RetimeError):
    """Les images intermédiaires n'ont pas pu être produites exactement (message à montrer tel quel)."""


@dataclass(frozen=True)
class PrepareRequest:
    """Tout ce qui détermine les images à fabriquer pour un clip.

    Attributes:
        media_path: fichier **réellement décodé** (l'original à l'export ; un proxy peut servir l'aperçu : sa clé de cache est
            alors distincte, et un flux calculé sur un proxy ne sert jamais l'export).
        time_map: le mapping du clip (:mod:`core.time_map`).
        interpolation: ``BLENDING`` ou ``OPTICAL_FLOW``.
        quality: qualité du flux optique (ignorée par le mélange).
        fps: cadence de sortie du projet.
        source_fps: cadence du média.
        source_frames: nombre d'images du média (``0`` : inconnu).
        width: largeur des images de travail (la sortie du cadrage).
        height: hauteur des images de travail.
        conform: filtres qui mettent le média au cadre (``scale``, ``pad``…) : **ceux du graphe**, pour que l'image exacte d'un
            tick soit identique à celle que donnerait l'échantillonnage.
        preference: backend demandé pour le flux optique.
        window: ticks locaux au clip ``[début, fin[`` à fabriquer (aperçu fenêtré) ; ``None`` : tout le clip (export).
    """

    media_path: str
    time_map: TimeMap
    interpolation: TimeInterpolation
    quality: FlowQuality
    fps: float
    source_fps: float
    source_frames: int
    width: int
    height: int
    conform: str
    preference: BackendPreference = BackendPreference.AUTO
    window: tuple[int, int] | None = None

    def plan(self) -> InterpolationPlan:
        return plan_interpolation(
            self.time_map,
            fps=self.fps,
            source_fps=self.source_fps if self.source_fps > 0 else 30.0,
            last_frame=self.source_frames - 1 if self.source_frames > 0 else None,
            interpolation=self.interpolation,
        )


def _as_int(value: object) -> int:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def _as_float(value: object) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


@dataclass
class PrepareReport:
    """Ce que la préparation a fait, agrégé (jamais une ligne par image) : journal et indicateur de l'interface."""

    backend: str = ""
    images: int = 0
    synthesized: int = 0
    pairs_computed: int = 0
    pairs_cached: int = 0
    fallbacks: dict[str, int] = field(default_factory=dict)
    confidence_sum: float = 0.0
    confidence_count: int = 0
    seconds: float = 0.0
    reused: bool = False
    """Le flux était déjà en cache : rien n'a été recalculé."""

    @property
    def mean_confidence(self) -> float:
        """Confiance moyenne mesurée sur les images fabriquées : celles du flux, **y compris** celles qui sont retombées sur un
        mélange faute de confiance (leur score bas compte), le mélange simple (exact par définition : 1,0) ; ni les coupures (rien
        à mesurer) ni un clip sans image fabriquée. 1,0 quand rien n'était mesurable : ``degraded`` dit alors ce qui s'est passé."""
        return self.confidence_sum / self.confidence_count if self.confidence_count else 1.0

    @property
    def degraded(self) -> int:
        """Images qui ne sont pas issues du flux alors qu'il était demandé (coupure ou confiance trop basse)."""
        return sum(
            count for name, count in self.fallbacks.items() if name in (Fallback.SCENE_CUT.value, Fallback.LOW_CONFIDENCE.value)
        )

    def add(self, fallback: Fallback, confidence: float | None) -> None:
        self.synthesized += 1
        self.fallbacks[fallback.value] = self.fallbacks.get(fallback.value, 0) + 1
        if confidence is not None:
            self.confidence_sum += confidence
            self.confidence_count += 1

    def to_dict(self) -> dict[str, object]:
        return {
            "backend": self.backend, "images": self.images, "synthesized": self.synthesized,
            "pairs_computed": self.pairs_computed, "pairs_cached": self.pairs_cached, "fallbacks": dict(self.fallbacks),
            "confidence_sum": self.confidence_sum, "confidence_count": self.confidence_count, "seconds": self.seconds,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> PrepareReport:
        """Rapport relu du ``.json`` d'un flux en cache ; un fichier illisible donne un rapport vide (le flux reste valable)."""
        fallbacks = data.get("fallbacks", {})
        return cls(
            backend=str(data.get("backend", "")),
            images=_as_int(data.get("images")),
            synthesized=_as_int(data.get("synthesized")),
            pairs_computed=_as_int(data.get("pairs_computed")),
            pairs_cached=_as_int(data.get("pairs_cached")),
            fallbacks={str(name): _as_int(count) for name, count in fallbacks.items()} if isinstance(fallbacks, dict) else {},
            confidence_sum=_as_float(data.get("confidence_sum")),
            confidence_count=_as_int(data.get("confidence_count")),
            seconds=_as_float(data.get("seconds")),
        )

    def summary(self) -> str:
        """Une ligne pour le journal."""
        return (
            f"{self.synthesized}/{self.images} images fabriquées ({self.backend or 'mélange'}), "
            f"{self.pairs_computed} paires calculées, {self.pairs_cached} relues du cache, "
            f"confiance moyenne {self.mean_confidence:.2f}, {self.degraded} en repli, {self.seconds:.1f} s"
            + (" (flux réutilisé)" if self.reused else "")
        )


@dataclass(frozen=True)
class PreparedStream:
    """Le flux d'images préparé d'un clip, tel que le graphe le relit."""

    path: str
    runs: tuple[PreparedRun, ...]
    frames: int
    report: PrepareReport

    def run_for(self, run_index: int) -> PreparedRun | None:
        for run in self.runs:
            if run.run_index == run_index:
                return run
        return None


@dataclass(frozen=True)
class WindowedRun:
    """Un run à préparer : ses images dans l'ordre des ticks (celles de la fenêtre), et les ticks de noir qui l'entourent."""

    run_index: int
    backward: bool
    samples: tuple[FrameSample, ...]
    lead: int
    trail: int


def windowed_runs(request: PrepareRequest, plan: InterpolationPlan) -> tuple[WindowedRun, ...]:
    """Les runs à préparer et, dans chacun, la part de la fenêtre. Un run que la fenêtre n'atteint pas garde **une** image
    (la première, exacte) tenue sur tout le run : un flux préparé n'est jamais vide, et la durée du run est conservée."""
    result: list[WindowedRun] = []
    for index in plan.runs_to_synthesize():
        run = plan.runs[index]
        low, high = run.first, run.first + run.ticks
        if request.window is not None:
            low, high = max(low, request.window[0]), min(high, request.window[1])
        backward = run.kind is RunKind.BACKWARD
        if low >= high:
            filler = FrameSample(run.samples[0].a)
            result.append(WindowedRun(index, backward, (filler,), 0, run.ticks - 1))
            continue
        samples = run.samples[low - run.first: high - run.first]
        result.append(WindowedRun(index, backward, samples, low - run.first, run.first + run.ticks - high))
    return tuple(result)


def needs_preparation(request: PrepareRequest) -> bool:
    """Le clip a-t-il au moins une image à fabriquer ? Sinon l'échantillonnage suffit, quel que soit le mode demandé."""
    return request.interpolation is not TimeInterpolation.SAMPLING and request.plan().needs_synthesis


def stream_key(request: PrepareRequest, plan: InterpolationPlan, engine_identity: tuple[object, ...]) -> str:
    """Clé d'un flux préparé : le média décodé, le cadrage, les images à fabriquer (fenêtre comprise) et, pour le flux, le moteur."""
    signature = source_signature(request.media_path, _FRESH_SIGNATURES)
    layout = [
        [item.run_index, item.backward, item.lead, item.trail, [(s.a, round(s.t, 6)) for s in item.samples]]
        for item in windowed_runs(request, plan)
    ]
    payload = {
        "version": PREPARE_VERSION,
        "path": os.path.abspath(request.media_path),
        "signature": signature.token if signature is not None else "missing",
        "grid": [request.width, request.height],
        "conform": request.conform,
        "fps": request.fps,
        "source_fps": request.source_fps,
        "mode": request.interpolation.value,
        "engine": list(engine_identity) if request.interpolation is TimeInterpolation.OPTICAL_FLOW else [],
        "layout": layout,
    }
    return hashlib.sha1(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Entrée et sortie FFmpeg
# ---------------------------------------------------------------------------


def _ffmpeg() -> str:
    tool = find_media_tool("ffmpeg")
    if not tool:
        raise PrepareError("FFmpeg est introuvable.")
    return tool


def _seconds(value: float) -> str:
    return f"{max(0.0, float(value)):.9f}".rstrip("0").rstrip(".") or "0"


_ALPHA_FORMATS = ("yuva", "gbrap", "rgba", "bgra", "argb", "abgr", "ya8", "ya16", "ayuv")
"""Préfixes des formats de pixels qui portent un canal alpha."""


def _has_alpha(path: str) -> bool:
    """Le média porte-t-il de la transparence ? (sonde ``ffprobe`` mémorisée ; en cas de doute : non.)"""
    from .decode_policy import default_context

    try:
        info = default_context().probe.get(path)
    except Exception:  # noqa: BLE001 - une sonde qui échoue ne doit pas empêcher la préparation
        LOGGER.debug("Sonde de transparence en échec pour %s : média traité comme opaque", path, exc_info=True)
        return False
    return info is not None and info.pix_fmt.startswith(_ALPHA_FORMATS)


def _require_opaque(path: str) -> None:
    """Refuse un média transparent : l'étage de préparation travaille en RVB opaque et en aplatirait le canal alpha."""
    if _has_alpha(path):
        raise PrepareError(
            f"{os.path.basename(path)} a un canal alpha (transparence) : le mélange d'images et le flux optique le feraient "
            "disparaître. Choisissez « Échantillonnage » pour ce clip."
        )


_LOSSLESS_CODECS = (("utvideo", "gbrp"), ("ffv1", "bgr0"))
"""Codecs sans perte acceptés pour les images préparées, du préféré (le plus rapide) au repli : ``(encodeur, format de pixel)``."""


@lru_cache(maxsize=1)
def _lossless_codec() -> tuple[str, str]:
    """Le premier codec sans perte que cette build de FFmpeg sait **encoder** ; ``PrepareError`` s'il n'y en a aucun.

    Un codec absent d'une build minimale donnerait « Unknown encoder » au milieu d'un export ; on le sait avant de commencer.
    """
    try:
        listing = supervised_run(
            [_ffmpeg(), "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=30, check=False
        ).stdout
    except (OSError, subprocess.SubprocessError):
        listing = ""
    names = {line.split()[1] for line in listing.splitlines() if len(line.split()) >= 2 and line.startswith(" V")}
    for codec, pixel_format in _LOSSLESS_CODECS:
        if codec in names or not names:                          # liste illisible : on tente le préféré, FFmpeg dira l'erreur
            return codec, pixel_format
    raise PrepareError(
        "Cette version de FFmpeg ne sait écrire aucun codec sans perte (Ut Video, FFV1) : les images intermédiaires "
        "ne peuvent pas être préparées. Installez un FFmpeg complet."
    )


def _read_exactly(pipe: Any, size: int) -> bytes | None:
    """``size`` octets du tube, ou ``None`` à la fin du flux (un reste partiel vaut une fin)."""
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        data = pipe.read(remaining)
        if not data:
            return None
        chunks.append(data)
        remaining -= len(data)
    return chunks[0] if len(chunks) == 1 else b"".join(chunks)


class _Decoder:
    """Les images ``first … last`` du média, au cadre de travail, en ``uint8`` ``(h, w, 3)``, dans l'ordre.

    Même décodage logiciel et mêmes filtres de cadrage que le graphe : l'image exacte d'un tick est identique à celle de
    l'échantillonnage. Le ``trim`` utilise les marges d'arrondi du graphe (:data:`core.retime_graph.TRIM_START_MARGIN`).
    """

    def __init__(self, request: PrepareRequest, first: int, last: int, cancelled: Callable[[], bool]) -> None:
        self.request = request
        self.first = first
        self.last = last
        self.cancelled = cancelled
        self.stderr = ""

    def command(self) -> list[str]:
        request = self.request
        fs = request.source_fps if request.source_fps > 0 else 30.0
        seek = max(0.0, (self.first - _SEEK_MARGIN_FRAMES) / fs) if seeks_exactly(request.media_path) else 0.0
        window = f"trim=start={_seconds((self.first - TRIM_START_MARGIN) / fs)}:end={_seconds((self.last + TRIM_END_MARGIN) / fs)}"
        chain = [window, "setpts=PTS-STARTPTS"]
        if request.conform:
            chain.append(request.conform)
        # ``gbrp`` puis ``-pix_fmt rgb24`` (simple réagencement des plans) : la conversion directe YUV → rgb24 de swscale
        # passe sous FFmpeg 7.x (arm64) par un chemin imprécis, deux niveaux trop sombre ; YUV → gbrp est exacte partout.
        chain.append("format=gbrp")
        return [
            _ffmpeg(), "-nostdin", "-hide_banner", "-v", "error",
            # ``-copyts`` garde les horodatages du média après le saut (le ``trim`` ci-dessous les attend absolus) ; ``-start_at_zero``
            # les compte depuis le début du *flux*, comme le graphe principal, et non depuis l'horloge du conteneur (MPEG-TS : 1,4 s).
            *(["-ss", _seconds(seek)] if seek > 0 else []), "-copyts", "-start_at_zero", "-i", request.media_path,
            "-map", "0:v:0", "-an", "-sn", "-dn", "-vf", ",".join(chain),
            # Une image par image décodée : sans cadence déclarée (``setpts`` l'efface sous FFmpeg 7.x), une sortie
            # ``rawvideo`` serait remise à 25 i/s, avec des images dupliquées ou perdues.
            "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
        ]

    def frames(self) -> Generator[tuple[int, Frame8], None, None]:
        import numpy as np

        request = self.request
        size = request.width * request.height * 3
        expected = self.last - self.first + 1
        supervisor = default_supervisor()
        process = supervisor.popen(self.command(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = process.stdout, process.stderr
        if stdout is None or stderr is None:                        # jamais : ``PIPE`` demandé ci-dessus
            raise PrepareError("FFmpeg a été lancé sans ses tubes de sortie.")
        errors: list[bytes] = []
        drain = threading.Thread(target=lambda: errors.append(stderr.read()), daemon=True)
        drain.start()
        produced = 0
        previous: Frame8 | None = None
        try:
            while produced < expected:
                if self.cancelled():
                    raise PrepareCancelled
                buffer = _read_exactly(stdout, size)
                if buffer is None:
                    try:
                        process.wait(timeout=5.0)                   # fin de flux : le code de sortie dit si c'est la fin du média
                    except subprocess.TimeoutExpired:
                        pass
                    break
                previous = np.frombuffer(buffer, dtype=np.uint8).reshape(request.height, request.width, 3)
                yield self.first + produced, previous
                produced += 1
        finally:
            if process.poll() is None:
                process.kill()
            try:
                stdout.close()
            except OSError:
                pass
            supervisor.finish(process)
            drain.join(timeout=2.0)
            self.stderr = b"".join(item for item in errors if item).decode("utf-8", "replace")[-1500:]
        if produced < expected and previous is not None and process.returncode == 0:
            # La durée du média est celle de son *conteneur* : un son plus long que l'image annonce des images qui n'existent pas.
            # Le graphe d'échantillonnage répète alors la dernière (``tpad``) ; le flux préparé fait de même, jamais une erreur.
            LOGGER.info("Fin du média après %d images sur %d annoncées : la dernière est répétée (%s)",
                        produced, expected, self.request.media_path)
            for index in range(self.first + produced, self.last + 1):
                yield index, previous
            return
        if produced < expected:
            detail = self.stderr.strip().splitlines()[-1] if self.stderr.strip() else "fin du média atteinte"
            raise PrepareError(
                f"Décodage de {self.request.media_path} : {produced} images lues au lieu de {expected} "
                f"(images {self.first} à {self.last}) — {detail}."
            )


class _Encoder:
    """Écrit des images ``uint8`` ``(h, w, 3)`` dans un fichier Matroska sans perte (Ut Video, RVB planaire)."""

    def __init__(self, request: PrepareRequest, target: str) -> None:
        self.request = request
        self.target = target
        self._process: subprocess.Popen[bytes] | None = None
        self._supervisor = default_supervisor()
        self._errors: list[bytes] = []
        self._drain: threading.Thread | None = None
        self.stderr = ""

    def command(self) -> list[str]:
        request = self.request
        codec, pixel_format = _lossless_codec()
        return [
            _ffmpeg(), "-nostdin", "-hide_banner", "-v", "error", "-y",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{request.width}x{request.height}", "-r", f"{request.fps:.9g}",
            "-i", "pipe:0", "-an", "-c:v", codec, "-pix_fmt", pixel_format, "-f", "matroska", self.target,
        ]

    def start(self) -> None:
        process = self._supervisor.popen(self.command(), stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self._process = process
        stderr = process.stderr
        if stderr is None or process.stdin is None:                # jamais : tubes demandés ci-dessus
            raise PrepareError("FFmpeg a été lancé sans ses tubes d'entrée.")
        self._drain = threading.Thread(target=lambda: self._errors.append(stderr.read()), daemon=True)
        self._drain.start()

    def write(self, frame: Frame8) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise PrepareError("L'encodeur n'est pas démarré.")
        try:
            process.stdin.write(frame.tobytes())
        except (BrokenPipeError, OSError) as error:
            self._collect()
            raise PrepareError(f"L'encodeur s'est arrêté : {self.stderr.strip().splitlines()[-1] if self.stderr.strip() else error}") from error

    def _collect(self) -> None:
        if self._drain is not None:
            self._drain.join(timeout=2.0)
        self.stderr = b"".join(item for item in self._errors if item).decode("utf-8", "replace")[-1500:]

    def finish(self) -> None:
        """Ferme l'entrée et attend la fin ; lève ``PrepareError`` si FFmpeg a échoué."""
        process = self._process
        if process is None:
            return
        try:
            if process.stdin is not None:
                process.stdin.close()
        except OSError:
            pass
        code = process.wait()
        self._supervisor.finish(process)
        self._process = None
        self._collect()
        if code != 0:
            raise PrepareError(f"Encodage des images intermédiaires : {self.stderr.strip().splitlines()[-1] if self.stderr.strip() else code}")

    def abort(self) -> None:
        """Interrompt l'encodeur sans rien garder."""
        process = self._process
        if process is None:
            return
        if process.poll() is None:
            process.kill()
        try:
            if process.stdin is not None:
                process.stdin.close()
        except OSError:
            pass
        self._supervisor.finish(process)
        self._process = None
        self._collect()


class _FrameWindow:
    """Fenêtre glissante sur les images décodées : on ne demande jamais une image plus ancienne que la fenêtre."""

    def __init__(self, source: Generator[tuple[int, Frame8], None, None]) -> None:
        self._source = source
        self._frames: dict[int, Frame8] = {}
        self._last = -1

    def close(self) -> None:
        """Arrête le décodage (FFmpeg est tué et attendu) sans attendre que le run ait lu toutes ses images."""
        self._source.close()

    def get(self, index: int) -> Frame8:
        while self._last < index:
            try:
                number, frame = next(self._source)
            except StopIteration as stop:
                raise PrepareError(f"L'image {index} du média n'a pas été décodée.") from stop
            self._frames[number] = frame
            self._last = number
            for old in [key for key in self._frames if key <= number - _WINDOW]:
                del self._frames[old]
        if index not in self._frames:
            raise PrepareError(f"L'image {index} a déjà quitté la fenêtre de décodage (plan non monotone).")
        return self._frames[index]


def _to_float(frame: Frame8) -> FrameF:
    import numpy as np

    return (frame.astype(np.float32) * np.float32(1.0 / 255.0)).astype(np.float32, copy=False)


def _to_bytes(frame: FrameF) -> Frame8:
    import numpy as np

    return np.clip(frame * 255.0 + 0.5, 0.0, 255.0).astype(np.uint8)


# ---------------------------------------------------------------------------
# Préparation
# ---------------------------------------------------------------------------


def _needed_frames(samples: tuple[FrameSample, ...]) -> tuple[int, int]:
    low = min(sample.a for sample in samples)
    high = max(sample.b if not sample.exact else sample.a for sample in samples)
    return low, high


def prepare(
    request: PrepareRequest,
    cache: FlowCache,
    *,
    progress: Callable[[int, int], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> PreparedStream | None:
    """Produit (ou retrouve) le flux d'images intermédiaires du clip ; ``None`` si aucune image n'est à fabriquer.

    Args:
        request: le clip.
        cache: le cache des vecteurs et des flux préparés.
        progress: ``progress(images_faites, images_à_faire)`` après chaque image.
        cancelled: interrogé entre deux images ; vrai interrompt, supprime tout fichier partiel et lève
            :class:`PrepareCancelled`.

    Raises:
        PrepareCancelled: annulé.
        PrepareError: décodage ou encodage impossible (message lisible).
    """
    if request.interpolation is TimeInterpolation.SAMPLING:
        return None
    plan = request.plan()
    runs = windowed_runs(request, plan)
    if not runs:
        return None
    if not os.path.isfile(request.media_path):
        raise PrepareError(f"Média introuvable : {request.media_path}. Reconnectez-le avant de calculer les images intermédiaires.")
    _require_opaque(request.media_path)
    check = cancelled or (lambda: False)
    engine = OpticalFlowEngine(request.quality, request.preference) if request.interpolation is TimeInterpolation.OPTICAL_FLOW else None
    identity = engine.identity if engine is not None else ()
    key = stream_key(request, plan, identity)
    layout: list[PreparedRun] = []
    offset = 0
    for item in runs:
        layout.append(PreparedRun(item.run_index, offset, len(item.samples), item.backward, item.lead, item.trail))
        offset += len(item.samples)
    total = offset
    if cache.has_stream(key):
        report = PrepareReport.from_dict(cache.stream_report(key))
        report.reused = True
        return PreparedStream(str(cache.stream_path(key)), tuple(layout), total, report)

    report = PrepareReport(backend=engine.backend.name if engine is not None else "blending", images=total)
    started = time.perf_counter()
    temporary = cache.stream_temporary(key)
    encoder = _Encoder(request, str(temporary))
    done = 0
    try:
        encoder.start()
        for item in runs:
            samples = tuple(reversed(item.samples)) if item.backward else item.samples
            low, high = _needed_frames(samples)
            window = _FrameWindow(_Decoder(request, low, high, check).frames())
            pairs: dict[int, PairAnalysis] = {}
            floats: dict[int, FrameF] = {}
            try:
                for sample in samples:
                    if check():
                        raise PrepareCancelled
                    if sample.exact:
                        encoder.write(window.get(sample.a))
                    else:
                        first, second = window.get(sample.a), window.get(sample.b)
                        if request.interpolation is TimeInterpolation.BLENDING or engine is None:
                            mixed = blend_frames(_to_float(first), _to_float(second), sample.t)
                            encoder.write(_to_bytes(mixed))
                            report.add(Fallback.NONE, 1.0)                  # le mélange demandé est exactement ce qui est fait
                        else:
                            pair = _analysis(request, engine, cache, sample.a, first, second, pairs, floats, report, check)
                            result = engine.interpolator.interpolate(floats[sample.a], floats[sample.a + 1], pair, sample.t)
                            encoder.write(_to_bytes(result.pixels))
                            report.add(result.fallback, None if result.fallback is Fallback.SCENE_CUT else result.confidence)
                    done += 1
                    if progress is not None:
                        progress(done, total)
            finally:
                window.close()
        encoder.finish()
    except FlowCancelled as stop:
        encoder.abort()
        _discard(temporary)
        raise PrepareCancelled from stop
    except BaseException:
        encoder.abort()
        _discard(temporary)
        raise
    report.seconds = time.perf_counter() - started
    path = cache.promote_stream(temporary, key, report.to_dict())
    LOGGER.info("Images intermédiaires : %s", report.summary())
    return PreparedStream(str(path), tuple(layout), total, report)


def analyze_pairs(
    request: PrepareRequest,
    cache: FlowCache,
    *,
    progress: Callable[[int, int], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> PrepareReport:
    """Calcule et range les vecteurs de mouvement de toutes les paires que le clip demande, **sans fabriquer d'image**.

    C'est le pré-calcul « Analyser le flux optique » : il rend l'aperçu fidèle et l'export suivants rapides (ils relisent les
    paires du cache). Il lit les mêmes images, au même cadre, que la préparation : les clés sont celles que l'export réclamera.
    Rien à faire pour le mélange d'images ou l'échantillonnage (aucun mouvement à estimer) : le bilan est alors vide.

    Raises:
        PrepareCancelled: annulé (les paires déjà rangées restent valables : elles ne dépendent que du média).
        PrepareError: décodage impossible.
    """
    report = PrepareReport(backend="")
    if request.interpolation is not TimeInterpolation.OPTICAL_FLOW:
        return report
    plan = request.plan()
    pairs = plan.pairs()
    if not pairs:
        return report
    if not os.path.isfile(request.media_path):
        raise PrepareError(f"Média introuvable : {request.media_path}. Reconnectez-le avant d'analyser le flux optique.")
    _require_opaque(request.media_path)
    check = cancelled or (lambda: False)
    engine = OpticalFlowEngine(request.quality, request.preference)
    report.backend = engine.backend.name
    report.images = len(pairs)
    started = time.perf_counter()
    done = 0
    groups: list[list[int]] = []
    for index in pairs:
        if groups and index == groups[-1][-1] + 1:
            groups[-1].append(index)
        else:
            groups.append([index])
    try:
        for group in groups:
            window = _FrameWindow(_Decoder(request, group[0], group[-1] + 1, check).frames())
            known: dict[int, PairAnalysis] = {}
            floats: dict[int, FrameF] = {}
            try:
                for index in group:
                    if check():
                        raise PrepareCancelled
                    _analysis(request, engine, cache, index, window.get(index), window.get(index + 1), known, floats, report, check)
                    done += 1
                    if progress is not None:
                        progress(done, len(pairs))
            finally:
                window.close()
    except FlowCancelled as stop:
        raise PrepareCancelled from stop
    report.synthesized = 0
    report.seconds = time.perf_counter() - started
    LOGGER.info("Analyse du flux optique : %s", report.summary())
    return report


def _analysis(
    request: PrepareRequest,
    engine: OpticalFlowEngine,
    cache: FlowCache,
    index: int,
    first: Frame8,
    second: Frame8,
    pairs: dict[int, PairAnalysis],
    floats: dict[int, FrameF],
    report: PrepareReport,
    cancelled: Callable[[], bool],
) -> PairAnalysis:
    """L'analyse de la paire ``(index, index + 1)`` : mémoire de ce run, puis cache disque, puis calcul (et rangement)."""
    for stale in [number for number in floats if number < index]:
        del floats[stale]
    floats.setdefault(index, _to_float(first))
    floats.setdefault(index + 1, _to_float(second))
    known = pairs.get(index)
    if known is not None:
        return known
    key = cache_key(
        request.media_path, index, width=request.width, height=request.height, conformation=request.conform, identity=engine.identity
    )
    pair = cache.load(key)
    if pair is None:
        pair = engine.estimator.analyze(floats[index], floats[index + 1], cancelled)
        cache.store(key, pair)
        report.pairs_computed += 1
    else:
        report.pairs_cached += 1
    pairs[index] = pair
    for stale_pair in [number for number in pairs if number < index - 1]:
        del pairs[stale_pair]
    return pair


def _discard(path: Any) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


__all__ = [
    "PREPARE_VERSION",
    "PrepareCancelled",
    "PrepareError",
    "PrepareReport",
    "PrepareRequest",
    "PreparedRun",
    "PreparedStream",
    "WindowedRun",
    "analyze_pairs",
    "needs_preparation",
    "prepare",
    "stream_key",
    "windowed_runs",
]
