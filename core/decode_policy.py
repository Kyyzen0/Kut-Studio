"""Choix du décodeur vidéo (CPU ou matériel), repli et mesures réelles.

Un seul endroit décide **comment décoder** un média, pour chaque usage
(:class:`DecodePurpose`) :

========================  ===========================================================
Usage                     Décodage
========================  ===========================================================
``REALTIME`` (moniteur)   Qt Multimedia (FFmpeg intégré) : backends validés,
                          transmis par ``QT_FFMPEG_DECODING_HW_DEVICE_TYPES``
``SEGMENT`` (aperçu       ``-hwaccel`` FFmpeg selon le mode ; repli CPU automatique
fidèle)
``PROXY``                 idem (la source 4K/HEVC est le coût dominant)
``ANALYSIS`` (tracking)   idem
``THUMBNAIL``             toujours CPU : une seule image, l'initialisation
                          matérielle coûte plus qu'elle ne rapporte
``EXPORT``                **toujours CPU** : résultat déterministe, indépendant
                          de la machine et du pilote
========================  ===========================================================

Les images décodées par le matériel sont **téléchargées** en mémoire système
(NV12/P010) par FFmpeg : le graphe de filtres CPU existant les reçoit sans
changement. Aucune image ne fait l'aller-retour CPU → GPU → CPU dans
Kut-Studio (voir ``docs/gpu-preview.md`` pour la carte des conversions).

Trois règles :

1. **Rien n'est supposé** : Auto ne choisit que des couples *(backend, famille)*
   validés par un décodage strict (:mod:`core.hardware_decoding`), et un média
   dont le profil n'est couvert par aucune famille (H.264 4:4:4, 10 bits…) est
   décodé en CPU — FFmpeg y retomberait sinon **sans le dire**.
2. **Mesurer avant d'optimiser** : un décodeur matériel n'est pas toujours plus
   rapide (mesuré sur Apple M4 : H.264 4K 214 i/s en CPU contre 88 i/s avec
   VideoToolbox, mais 20 ms de CPU par image contre 2,9 ms). :class:`DecodeProfile`
   garde les débits mesurés sur les vrais médias. Auto garde le matériel tant
   qu'il n'est **pas le goulot** — au moins 85 % du débit CPU, ou assez rapide
   pour tenir :data:`HW_HEADROOM_FPS` — car il libère le processeur pour les
   filtres et l'encodage du même rendu ; sinon le CPU.
3. **Repli transparent mais visible** : un décodage matériel qui échoue est
   relancé en CPU ; :class:`DecodeHealth` compte les replis, bloque un couple
   qui échoue à répétition pour la session et l'expose aux diagnostics.

Le module est pur (aucun Qt) ; les processus passent par des fonctions
injectables.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .hardware_decoding import (
    CODEC_BY_ID,
    DECODE_LABELS,
    HWACCEL_NAMES,
    DecodeCodec,
    DecodeMode,
    codec_class,
    coerce_decode_mode,
    decode_input_args,
)
from .process_supervisor import default_supervisor, supervised_run

LOGGER = logging.getLogger("kut_studio.decode")

HW_SPEED_TOLERANCE = 0.85
"""Auto garde le matériel tant qu'il atteint 85 % du débit CPU mesuré (il libère le CPU)."""

HW_HEADROOM_FPS = 60.0
"""… ou tant qu'il décode au moins 60 i/s : il n'est alors pas le goulot d'un aperçu."""

FAILURES_BEFORE_BLOCK = 2
"""Échecs d'exécution d'un couple *(backend, famille)* avant de le bannir pour la session."""

PROFILE_FILE_NAME = "decode-profile.json"
PROFILE_SCHEMA = 1


class DecodePurpose(str, Enum):
    REALTIME = "realtime"
    SEGMENT = "segment"
    PROXY = "proxy"
    ANALYSIS = "analysis"
    THUMBNAIL = "thumbnail"
    EXPORT = "export"


_CPU_ONLY_PURPOSES = {DecodePurpose.EXPORT, DecodePurpose.THUMBNAIL}


# --- Description d'un flux ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StreamInfo:
    """Ce qui décide du décodeur : codec, format de pixels, taille."""

    codec_name: str = ""
    pix_fmt: str = ""
    width: int = 0
    height: int = 0

    @property
    def codec(self) -> DecodeCodec | None:
        return codec_class(self.codec_name, self.pix_fmt)

    @property
    def megapixels(self) -> float:
        return max(0, self.width) * max(0, self.height) / 1e6

    @property
    def resolution_class(self) -> str:
        """``sd`` / ``hd`` / ``fhd`` / ``uhd`` : les mesures sont rangées par classe."""
        pixels = self.width * self.height
        if pixels >= 3840 * 2160 * 0.75:
            return "uhd"
        if pixels >= 1920 * 1080 * 0.75:
            return "fhd"
        if pixels >= 1280 * 720 * 0.75:
            return "hd"
        return "sd"


def parse_stream_json(text: str) -> StreamInfo | None:
    """Premier flux vidéo d'une sortie ``ffprobe -of json -show_streams``."""
    try:
        data = json.loads(text or "{}")
    except ValueError:
        return None
    for stream in data.get("streams") or []:
        if stream.get("codec_type", "video") != "video":
            continue
        try:
            return StreamInfo(
                codec_name=str(stream.get("codec_name") or ""),
                pix_fmt=str(stream.get("pix_fmt") or ""),
                width=int(stream.get("width") or 0),
                height=int(stream.get("height") or 0),
            )
        except (TypeError, ValueError):
            return None
    return None


def _default_probe(path: str) -> StreamInfo | None:
    from .tool_paths import find_media_tool

    ffprobe = find_media_tool("ffprobe")
    if not ffprobe:
        return None
    try:
        completed = supervised_run(
            [ffprobe, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_type,codec_name,pix_fmt,width,height",
             "-of", "json", path],
            capture_output=True, text=True, timeout=15, encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return parse_stream_json(completed.stdout)


class StreamProbe:
    """Sonde ``ffprobe`` mémorisée par signature de fichier (taille + date).

    ``peek`` ne lance jamais de processus (usage depuis l'interface) ; ``get``
    sonde si nécessaire (threads de travail).
    """

    def __init__(self, probe: Callable[[str], StreamInfo | None] | None = None, size: int = 256) -> None:
        self._probe = probe or _default_probe
        self._items: OrderedDict[str, StreamInfo | None] = OrderedDict()
        self._size = size
        self._lock = threading.Lock()

    @staticmethod
    def _key(path: str) -> str | None:
        try:
            stat = os.stat(path)
        except OSError:
            return None
        return f"{path}|{stat.st_mtime_ns}|{stat.st_size}"

    def peek(self, path: str) -> StreamInfo | None:
        key = self._key(path) if path else None
        if key is None:
            return None
        with self._lock:
            return self._items.get(key)

    def get(self, path: str) -> StreamInfo | None:
        key = self._key(path) if path else None
        if key is None:
            return None
        with self._lock:
            if key in self._items:
                self._items.move_to_end(key)
                return self._items[key]
        info = self._probe(path)
        with self._lock:
            self._items[key] = info
            while len(self._items) > self._size:
                self._items.popitem(last=False)
        return info

    def put(self, path: str, info: StreamInfo | None) -> None:
        key = self._key(path)
        if key is not None:
            with self._lock:
                self._items[key] = info


# --- Choix ------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DecodeChoice:
    """Décodeur retenu pour un média et un usage.

    Attributes:
        requested: mode demandé (préférences).
        used: ``CPU`` ou le backend matériel réellement utilisé.
        codec: famille de codec (``""`` si inconnue ou non couverte).
        input_args: options FFmpeg à placer juste avant ``-i``.
        reason: pourquoi ce choix (clé courte, diagnostics et tests).
        fallback_reason: pourquoi ``used`` diffère d'un backend **explicitement**
            demandé (``None`` si le choix est conforme).
    """

    requested: DecodeMode
    used: DecodeMode
    codec: str = ""
    input_args: tuple[str, ...] = ()
    reason: str = ""
    fallback_reason: str | None = None

    @property
    def is_hardware(self) -> bool:
        return self.used not in (DecodeMode.CPU, DecodeMode.AUTO)

    @property
    def label(self) -> str:
        return DECODE_LABELS.get(self.used, self.used.value)


def _cpu(requested: DecodeMode, codec: str, reason: str, fallback: str | None = None) -> DecodeChoice:
    return DecodeChoice(requested, DecodeMode.CPU, codec, (), reason, fallback)


def choose_decoder(
    mode: object,
    *,
    capabilities,
    stream: StreamInfo | None,
    purpose: DecodePurpose = DecodePurpose.SEGMENT,
    health: DecodeHealth | None = None,
    profile: DecodeProfile | None = None,
) -> DecodeChoice:
    """Décodeur à utiliser (jamais d'exception ; le CPU est toujours possible).

    Args:
        mode: préférence de l'utilisateur (:class:`DecodeMode` ou sa valeur).
        capabilities: :class:`~core.hardware_encoding.HardwareCapabilities` ou ``None``
            (détection pas encore terminée : CPU).
        stream: flux à décoder (``None`` : inconnu, CPU).
        purpose: usage ; ``EXPORT`` et ``THUMBNAIL`` sont toujours CPU.
        health: échecs d'exécution de la session (couples bannis).
        profile: débits mesurés (départage CPU / matériel en Auto).
    """
    requested = coerce_decode_mode(mode)
    if purpose in _CPU_ONLY_PURPOSES:
        return _cpu(requested, "", "export_cpu" if purpose is DecodePurpose.EXPORT else "single_frame")
    if requested is DecodeMode.CPU:
        return _cpu(requested, "", "requested_cpu")
    if capabilities is None:
        return _cpu(requested, "", "capabilities_unknown")
    if getattr(capabilities, "decoding_disabled", False) or not getattr(capabilities, "ffmpeg_available", True):
        return _cpu(requested, "", "decoding_disabled")
    codec = stream.codec if stream is not None else None
    if stream is None or codec is None:
        reason = "stream_unknown" if stream is None else "profile_unsupported"
        fallback = None
        if requested is not DecodeMode.AUTO:
            fallback = "Profil vidéo non pris en charge par le décodage matériel (décodé en CPU)."
        return _cpu(requested, "", reason, fallback)
    blocked = health.blocked if health is not None else (lambda _b, _c: False)

    if requested is not DecodeMode.AUTO:
        if capabilities.is_decode_usable(codec.id, requested) and not blocked(requested, codec.id):
            return DecodeChoice(
                requested, requested, codec.id, decode_input_args(requested, codec), "requested_backend"
            )
        if blocked(requested, codec.id):
            why = f"{DECODE_LABELS[requested]} a échoué pendant la session : décodage CPU."
        else:
            why = capabilities.decode_unavailable_reason(codec.id, requested)
        return _cpu(requested, codec.id, "backend_unavailable", why)

    candidates = [b for b in capabilities.usable_decode_backends(codec.id) if not blocked(b, codec.id)]
    if not candidates:
        return _cpu(requested, codec.id, "no_validated_backend")
    backend = candidates[0]
    if profile is not None:
        verdict = profile.compare(backend, codec.id, stream.resolution_class)
        if verdict is False:
            return _cpu(requested, codec.id, "cpu_measured_faster")
        if verdict is True:
            return DecodeChoice(
                requested, backend, codec.id, decode_input_args(backend, codec), "hardware_measured"
            )
    if _hardware_prior(codec, stream):
        return DecodeChoice(requested, backend, codec.id, decode_input_args(backend, codec), "hardware_prior")
    return _cpu(requested, codec.id, "cpu_prior")


def _hardware_prior(codec: DecodeCodec, stream: StreamInfo) -> bool:
    """Choix **provisoire** tant qu'aucune mesure n'existe pour cette classe.

    Le matériel pour ce qui pèse sur un CPU (10 bits, HEVC/AV1/VP9 dès le Full
    HD, tout flux UHD) ; le CPU pour le H.264 léger, où l'initialisation du
    décodeur matériel ne se rentabilise pas. Une mesure remplace ce choix.
    """
    if codec.bit_depth > 8 or stream.resolution_class == "uhd":
        return True
    return codec.id != "h264" and stream.resolution_class == "fhd"


# --- Qt Multimedia (moniteur temps réel) -------------------------------------------------------------------

QT_DEVICE_VARIABLE = "QT_FFMPEG_DECODING_HW_DEVICE_TYPES"


def qt_hw_device_types(mode: object, capabilities) -> str | None:
    """Valeur de ``QT_FFMPEG_DECODING_HW_DEVICE_TYPES`` pour le lecteur Qt.

    ``None`` : ne rien imposer (Qt essaie ses backends et retombe seul sur le
    logiciel) — utilisé en Auto tant que la détection n'est pas connue.
    ``"none"`` : décodage logiciel (aucun nom de périphérique valide).
    Qt lit la variable **une fois par processus** : un changement s'applique au
    prochain démarrage.
    """
    requested = coerce_decode_mode(mode)
    if requested is DecodeMode.CPU:
        return "none"
    if capabilities is None:
        return None
    if getattr(capabilities, "decoding_disabled", False):
        return "none"
    if requested is DecodeMode.AUTO:
        backends = capabilities.decode_backends()
        return ",".join(HWACCEL_NAMES[b] for b in backends) if backends else "none"
    if requested in capabilities.decode_backends():
        return HWACCEL_NAMES[requested]
    return "none"


_qt_value_set_by_kut: str | None = None


def configure_qt_decoding(mode: object, capabilities, environ=None) -> tuple[str | None, str]:
    """Positionne ``QT_FFMPEG_DECODING_HW_DEVICE_TYPES`` **avant** le premier lecteur Qt.

    Une valeur posée par l'utilisateur (variable déjà présente, pas par nous)
    est respectée. Retourne ``(valeur effective, origine)`` avec l'origine
    ``"user"``, ``"kut"`` ou ``"qt-default"``.
    """
    global _qt_value_set_by_kut
    env = os.environ if environ is None else environ
    current = env.get(QT_DEVICE_VARIABLE)
    if current is not None and current != _qt_value_set_by_kut:
        return current, "user"
    value = qt_hw_device_types(mode, capabilities)
    if value is None:
        if current is not None:
            env.pop(QT_DEVICE_VARIABLE, None)
        _qt_value_set_by_kut = None
        return None, "qt-default"
    env[QT_DEVICE_VARIABLE] = value
    _qt_value_set_by_kut = value
    return value, "kut"


# --- Santé d'exécution (repli) ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DecodeEvent:
    when: float
    backend: str
    codec: str
    purpose: str
    detail: str


_HW_FAILURE_MARKERS = (
    "hwaccel", "hardware device", "device creation failed", "failed setup for format",
    "no device available", "hwdownload", "videotoolbox", "cuda", "nvdec", "cuvid", "qsv",
    "d3d11", "dxva", "vaapi", "vt decoder", "hw frames",
)


def looks_like_decode_failure(stderr: str) -> bool:
    """La sortie d'erreur évoque-t-elle le décodage matériel ?"""
    text = (stderr or "").lower()
    return any(marker in text for marker in _HW_FAILURE_MARKERS)


class DecodeHealth:
    """Échecs et replis de décodage matériel pendant la session (sûr entre threads).

    Un couple *(backend, famille)* qui échoue :data:`FAILURES_BEFORE_BLOCK` fois
    est banni jusqu'à la fin de la session (ou :meth:`reset`) : le choix suivant
    est CPU d'office, sans relancer un décodage voué à l'échec. Le premier échec
    d'un couple est journalisé en avertissement, les suivants en débogage (pas
    de spam).
    """

    def __init__(self, clock: Callable[[], float] = time.time, history: int = 20) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._failures: dict[tuple[str, str], int] = {}
        self._events: deque[DecodeEvent] = deque(maxlen=history)
        self.fallbacks = 0
        self.hardware_runs = 0

    def blocked(self, backend: DecodeMode, codec: str) -> bool:
        with self._lock:
            return self._failures.get((backend.value, codec), 0) >= FAILURES_BEFORE_BLOCK

    def record_success(self, choice: DecodeChoice) -> None:
        if choice.is_hardware:
            with self._lock:
                self.hardware_runs += 1

    def record_failure(self, choice: DecodeChoice, purpose: DecodePurpose | str, detail: str) -> None:
        """Un décodage matériel a échoué et va être relancé en CPU."""
        if not choice.is_hardware:
            return
        key = (choice.used.value, choice.codec)
        purpose_value = purpose.value if isinstance(purpose, DecodePurpose) else str(purpose)
        short = (detail or "").strip().splitlines()[-1][:200] if (detail or "").strip() else "échec"
        with self._lock:
            count = self._failures.get(key, 0) + 1
            self._failures[key] = count
            self.fallbacks += 1
            self._events.append(DecodeEvent(self._clock(), key[0], key[1], purpose_value, short))
        log = LOGGER.warning if count == 1 else LOGGER.debug
        log("Décodage %s/%s en échec (%s) : repli CPU — %s", key[0], key[1] or "?", purpose_value, short)
        if count == FAILURES_BEFORE_BLOCK:
            LOGGER.warning("Décodage %s/%s banni pour la session après %d échecs", key[0], key[1], count)

    def events(self) -> tuple[DecodeEvent, ...]:
        with self._lock:
            return tuple(self._events)

    def blocked_pairs(self) -> tuple[tuple[str, str], ...]:
        with self._lock:
            return tuple(sorted(k for k, n in self._failures.items() if n >= FAILURES_BEFORE_BLOCK))

    def reset(self) -> None:
        with self._lock:
            self._failures.clear()
            self._events.clear()
            self.fallbacks = 0
            self.hardware_runs = 0


# --- Mesures réelles -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DecodeMeasurement:
    """Débit mesuré d'un décodage complet (démuxage + décodage + téléchargement)."""

    fps: float
    cpu_seconds_per_frame: float | None = None
    frames: int = 0
    measured_at: float = 0.0

    def to_dict(self) -> dict:
        return {"fps": self.fps, "cpu": self.cpu_seconds_per_frame, "frames": self.frames,
                "at": self.measured_at}

    @classmethod
    def from_dict(cls, data: object) -> DecodeMeasurement | None:
        if not isinstance(data, dict):
            return None
        try:
            fps = float(data.get("fps") or 0.0)
            cpu = data.get("cpu")
            return cls(fps, None if cpu is None else float(cpu), int(data.get("frames") or 0),
                       float(data.get("at") or 0.0))
        except (TypeError, ValueError):
            return None


def profile_key(backend: DecodeMode, codec: str, resolution_class: str) -> str:
    return f"{backend.value}/{codec}/{resolution_class}"


class DecodeProfile:
    """Débits mesurés sur cette installation, par *(backend, famille, classe de taille)*.

    Persistés à côté du cache des capacités et liés à la même empreinte
    d'installation : changer de FFmpeg ou de machine efface les mesures.
    """

    def __init__(self, path: str | os.PathLike[str] | None = None, fingerprint: str = "") -> None:
        self._path = Path(path) if path is not None else None
        self._fingerprint = fingerprint
        self._lock = threading.Lock()
        self._items: dict[str, DecodeMeasurement] = {}
        self._load()

    @property
    def path(self) -> Path:
        if self._path is not None:
            return self._path
        from .platform_paths import user_cache_dir

        return user_cache_dir() / PROFILE_FILE_NAME

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict) or data.get("schema") != PROFILE_SCHEMA:
            return
        if self._fingerprint and data.get("fingerprint") != self._fingerprint:
            return
        for key, raw in (data.get("items") or {}).items():
            item = DecodeMeasurement.from_dict(raw)
            if item is not None and item.fps > 0:
                self._items[str(key)] = item

    def _save(self) -> None:
        with self._lock:
            payload = {
                "schema": PROFILE_SCHEMA,
                "fingerprint": self._fingerprint,
                "items": {k: v.to_dict() for k, v in self._items.items()},
            }
        path = self.path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(payload, indent=1), encoding="utf-8")
            os.replace(temporary, path)
        except OSError as error:
            LOGGER.debug("Mesures de décodage non écrites : %s", error)

    def get(self, backend: DecodeMode, codec: str, resolution_class: str) -> DecodeMeasurement | None:
        with self._lock:
            return self._items.get(profile_key(backend, codec, resolution_class))

    def put(self, backend: DecodeMode, codec: str, resolution_class: str,
            measurement: DecodeMeasurement, *, save: bool = True) -> None:
        with self._lock:
            self._items[profile_key(backend, codec, resolution_class)] = measurement
        if save:
            self._save()

    def compare(self, backend: DecodeMode, codec: str, resolution_class: str) -> bool | None:
        """``True`` : garder le matériel ; ``False`` : il serait le goulot ; ``None`` : pas mesuré."""
        hardware = self.get(backend, codec, resolution_class)
        software = self.get(DecodeMode.CPU, codec, resolution_class)
        if hardware is None or software is None or software.fps <= 0:
            return None
        return hardware.fps >= HW_SPEED_TOLERANCE * software.fps or hardware.fps >= HW_HEADROOM_FPS

    def measured(self, backend: DecodeMode, codec: str, resolution_class: str) -> bool:
        """CPU **et** ``backend`` mesurés pour cette classe (plus rien à mesurer)."""
        with self._lock:
            return (profile_key(DecodeMode.CPU, codec, resolution_class) in self._items
                    and profile_key(backend, codec, resolution_class) in self._items)

    def items(self) -> dict[str, DecodeMeasurement]:
        with self._lock:
            return dict(self._items)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
        self._save()


def measurement_command(command: Sequence[str], path: str, choice_args: Sequence[str], frames: int) -> list[str]:
    return [
        *command, "-hide_banner", "-loglevel", "error", "-nostdin",
        *choice_args, "-i", path, "-map", "0:v:0", "-an", "-sn", "-dn",
        "-frames:v", str(int(frames)), "-f", "null", "-",
    ]


def run_measured(command: Sequence[str], timeout: float = 60.0) -> tuple[int, float, float | None]:
    """``(code, durée murale, temps CPU)`` d'une commande ; le temps CPU vient de ``wait4``.

    ``wait4`` donne la consommation **de ce processus seul** (les autres threads
    de Kut-Studio n'y sont pas mêlés). Indisponible sous Windows : ``None``.
    """
    supervisor = default_supervisor()
    started = time.perf_counter()
    try:
        process = supervisor.popen(list(command), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return -1, 0.0, None
    cpu = None
    try:
        if hasattr(os, "wait4"):
            deadline = started + timeout
            while True:
                pid, status, usage = os.wait4(process.pid, os.WNOHANG)
                if pid:
                    process.returncode = os.waitstatus_to_exitcode(status)
                    cpu = usage.ru_utime + usage.ru_stime
                    break
                if time.perf_counter() > deadline:
                    process.kill()
                    process.wait()
                    return -1, time.perf_counter() - started, None
                time.sleep(0.005)
        else:
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                return -1, time.perf_counter() - started, None
    finally:
        supervisor.finish(process)  # déjà récolté par wait4 : seulement désenregistré
    return int(process.returncode or 0), time.perf_counter() - started, cpu


def measure_decode(
    command: Sequence[str],
    path: str,
    backend: DecodeMode,
    codec: DecodeCodec,
    *,
    frames: int = 90,
    runner: Callable[[Sequence[str]], tuple[int, float, float | None]] = run_measured,
    clock: Callable[[], float] = time.time,
) -> DecodeMeasurement | None:
    """Décode ``frames`` images du **vrai** média et mesure le débit (``None`` en cas d'échec)."""
    args = decode_input_args(backend, codec) if backend is not DecodeMode.CPU else ()
    code, wall, cpu = runner(measurement_command(command, path, args, frames))
    if code != 0 or wall <= 0:
        return None
    return DecodeMeasurement(
        fps=frames / wall,
        cpu_seconds_per_frame=None if cpu is None else cpu / frames,
        frames=frames,
        measured_at=clock(),
    )


# --- Choix de la source d'aperçu (original / proxy) ---------------------------------------------------------

_PRIOR_MS_PER_MEGAPIXEL = {
    # Ordres de grandeur prudents (un flux, décodage + copie) quand rien n'est mesuré.
    ("cpu", "h264"): 1.2, ("cpu", "hevc"): 1.6, ("cpu", "hevc10"): 2.8, ("cpu", "prores"): 0.9,
    ("cpu", "vp9"): 1.6, ("cpu", "av1"): 2.4, ("hw", "*"): 1.0,
}


@dataclass(frozen=True)
class SourceOption:
    """Un fichier lisible pour l'aperçu (l'original ou un proxy)."""

    path: str
    stream: StreamInfo | None
    is_proxy: bool = False


@dataclass(frozen=True)
class SourceDecision:
    path: str
    is_proxy: bool
    decoder: DecodeChoice
    estimated_ms: float | None
    reason: str


def estimate_frame_ms(stream: StreamInfo | None, choice: DecodeChoice, profile: DecodeProfile | None) -> float | None:
    """Coût estimé d'une image : mesure réelle si elle existe, sinon ordre de grandeur."""
    if stream is None or stream.width <= 0 or stream.height <= 0:
        return None
    codec = stream.codec
    codec_id = codec.id if codec is not None else ""
    if profile is not None and codec_id:
        measured = profile.get(choice.used, codec_id, stream.resolution_class)
        if measured is not None and measured.fps > 0:
            return 1000.0 / measured.fps
    if choice.is_hardware:
        rate = _PRIOR_MS_PER_MEGAPIXEL[("hw", "*")]
    else:
        rate = _PRIOR_MS_PER_MEGAPIXEL.get(("cpu", codec_id), 2.0)
    return rate * stream.megapixels


def choose_preview_source(
    original: SourceOption,
    proxy: SourceOption | None,
    *,
    needed_height: int,
    mode: object,
    capabilities,
    purpose: DecodePurpose = DecodePurpose.SEGMENT,
    health: DecodeHealth | None = None,
    profile: DecodeProfile | None = None,
    proxy_margin: float = 0.8,
) -> SourceDecision:
    """Original ou proxy, et avec quel décodeur — sans supposer que le proxy gagne.

    Les quatre combinaisons (original/proxy × matériel/CPU) sont évaluées par
    leur coût de décodage estimé (:func:`estimate_frame_ms`). Un proxy trop petit
    pour la qualité demandée (``needed_height``) n'est pris que s'il est
    nettement moins cher ; un proxy assez net est pris dès qu'il n'est pas plus
    cher que l'original (``proxy_margin`` près : à coût égal, l'original évite
    toute perte).
    """
    def decide(option: SourceOption) -> tuple[DecodeChoice, float | None]:
        choice = choose_decoder(mode, capabilities=capabilities, stream=option.stream,
                                purpose=purpose, health=health, profile=profile)
        return choice, estimate_frame_ms(option.stream, choice, profile)

    original_choice, original_ms = decide(original)
    if proxy is None or not proxy.path:
        return SourceDecision(original.path, False, original_choice, original_ms, "no_proxy")
    proxy_choice, proxy_ms = decide(proxy)
    sharp_enough = proxy.stream is None or proxy.stream.height <= 0 or proxy.stream.height >= needed_height
    if original_ms is None or proxy_ms is None:
        # Rien à comparer : comportement historique (le proxy prêt est pris).
        return SourceDecision(proxy.path, True, proxy_choice, proxy_ms, "proxy_unmeasured")
    if sharp_enough and proxy_ms <= original_ms / proxy_margin:
        return SourceDecision(proxy.path, True, proxy_choice, proxy_ms, "proxy_cheaper")
    if not sharp_enough and proxy_ms * 2.0 <= original_ms:
        return SourceDecision(proxy.path, True, proxy_choice, proxy_ms, "proxy_much_cheaper")
    return SourceDecision(original.path, False, original_choice, original_ms,
                          "original_sharper" if not sharp_enough else "original_cheaper")


# --- Contexte global (configuré par l'interface) ------------------------------------------------------------


@dataclass
class DecodeContext:
    """Préférence, capacités, santé et mesures partagées par tous les consommateurs.

    Par défaut (aucune interface : tests, scripts), le mode est ``CPU`` : rien ne
    touche au matériel sans configuration explicite.
    """

    mode: DecodeMode = DecodeMode.CPU
    capabilities_provider: Callable[[], object] = field(default=lambda: None)
    health: DecodeHealth = field(default_factory=DecodeHealth)
    profile: DecodeProfile | None = None
    probe: StreamProbe = field(default_factory=StreamProbe)

    def choice_for(self, path: str, purpose: DecodePurpose) -> DecodeChoice:
        """Décodeur pour ``path`` (sonde le flux si besoin : à appeler hors interface)."""
        if purpose in _CPU_ONLY_PURPOSES or self.mode is DecodeMode.CPU:
            return choose_decoder(self.mode, capabilities=None, stream=None, purpose=purpose)
        try:
            capabilities = self.capabilities_provider()
        except Exception:
            capabilities = None
        stream = self.probe.get(path) if capabilities is not None else None
        return choose_decoder(self.mode, capabilities=capabilities, stream=stream, purpose=purpose,
                              health=self.health, profile=self.profile)

    def input_args(self, path: str, purpose: DecodePurpose) -> tuple[DecodeChoice, tuple[str, ...]]:
        choice = self.choice_for(path, purpose)
        return choice, choice.input_args


_context = DecodeContext()
_context_lock = threading.Lock()


def default_context() -> DecodeContext:
    with _context_lock:
        return _context


def set_default_context(context: DecodeContext | None) -> None:
    """Remplace le contexte global (``None`` : retour au CPU par défaut)."""
    global _context
    with _context_lock:
        _context = context or DecodeContext()


def run_with_decode_fallback(
    build: Callable[[Callable[[str], tuple[str, ...]]], list[str]],
    run: Callable[[list[str]], tuple[int, str]],
    *,
    paths: Sequence[str],
    purpose: DecodePurpose,
    context: DecodeContext | None = None,
) -> tuple[int, str, bool]:
    """Exécute une commande FFmpeg avec décodage matériel, relancée en CPU en cas d'échec.

    Args:
        build: construit la commande à partir d'une fonction ``chemin -> options d'entrée``.
        run: exécute la commande, retourne ``(code, stderr)``.
        paths: médias d'entrée concernés (les autres entrées n'ont jamais d'option).

    Returns:
        ``(code, stderr, repli_effectué)``.
    """
    ctx = context or default_context()
    choices = {path: ctx.choice_for(path, purpose) for path in dict.fromkeys(paths) if path}
    hardware = [c for c in choices.values() if c.is_hardware]
    code, stderr = run(build(lambda path: choices[path].input_args if path in choices else ()))
    if code == 0 or not hardware:
        for choice in hardware:
            ctx.health.record_success(choice)
        return code, stderr, False
    hardware_stderr = stderr
    code, stderr = run(build(lambda _path: ()))
    # Le matériel n'est fautif que si le même rendu réussit sans lui, ou si l'erreur parle de décodage matériel.
    # Avant, tout code de retour non nul le bannissait pour la session : un filtre invalide ou un second média
    # hors ligne suffisait à désactiver un décodeur pourtant sain.
    if code == 0 or looks_like_decode_failure(hardware_stderr):
        for choice in hardware:
            ctx.health.record_failure(choice, purpose, hardware_stderr)
    return code, stderr, True


__all__ = [
    "DecodeChoice",
    "DecodeContext",
    "DecodeEvent",
    "DecodeHealth",
    "DecodeMeasurement",
    "DecodeProfile",
    "DecodePurpose",
    "HW_HEADROOM_FPS",
    "HW_SPEED_TOLERANCE",
    "QT_DEVICE_VARIABLE",
    "SourceDecision",
    "SourceOption",
    "StreamInfo",
    "StreamProbe",
    "choose_decoder",
    "choose_preview_source",
    "configure_qt_decoding",
    "default_context",
    "estimate_frame_ms",
    "looks_like_decode_failure",
    "measure_decode",
    "measurement_command",
    "parse_stream_json",
    "profile_key",
    "qt_hw_device_types",
    "run_measured",
    "run_with_decode_fallback",
    "set_default_context",
]

# Les familles sont exposées pour les diagnostics (évite un import croisé côté UI).
CODEC_LABELS = {codec_id: codec.label for codec_id, codec in CODEC_BY_ID.items()}
