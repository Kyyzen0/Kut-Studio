"""Classes Python minimales au-dessus de l'ABI 1 : un moteur (``NativeEngine``) et ses sessions de lecture (``NativeSession``).

Rien ici n'est branché dans le moniteur (docs/native-engine.md dit où le faire). Règles :

* ``close()`` est **idempotent** ; ``with`` ferme à la sortie ; fermer un moteur ferme d'abord ses sessions (l'ABI refuse de détruire un
  moteur qui en a encore) ; fermer une session après son moteur ne fait rien.
* Un objet fermé lève :class:`InvalidHandleError` **sans appeler la bibliothèque** (un handle détruit ne part jamais vers le natif).
* Les entrées sont bornées avant de franchir la frontière (plan, identifiants) ; un refus local est une :class:`InvalidArgumentError`.
* Les images décodées (``CVPixelBufferRef``) sont poussées par l'hôte sous forme d'entier (adresse) : la bibliothèque les retient
  (``CFRetain``) ; l'hôte garde sa propre référence et la libère après l'appel (règle de propriété de ``docs/ABI_V1.md``).
"""

from __future__ import annotations

import ctypes
import threading
from dataclasses import dataclass
from typing import Any

from . import abi
from .errors import InvalidArgumentError, InvalidHandleError, NotFoundError, error_for
from .loader import NativeLibrary, load_library


BYTES_PER_PIXEL = 4


@dataclass(frozen=True)
class SourceDescription:
    """Une source du plan : à alimenter par ``submit_pixel_buffer`` (vidéo) ou ``set_static_image`` (image)."""

    id: str
    kind: int
    layout: str
    width: int
    height: int


@dataclass(frozen=True)
class FrameResult:
    position_us: int
    rendered: bool
    stale: bool
    sources_ready: int
    sources_missing: int
    cpu_ms: float


def _source_id(source_id: str) -> bytes:
    try:
        raw = source_id.encode("utf-8")
    except (AttributeError, UnicodeEncodeError) as error:
        raise InvalidArgumentError(abi.ERR_INVALID_ARGUMENT, "identifiant de source invalide") from error
    if not 1 <= len(raw) <= abi.SOURCE_ID_MAX or b"\x00" in raw:
        raise InvalidArgumentError(abi.ERR_INVALID_ARGUMENT, "identifiant de source vide, trop long ou avec un octet nul")
    return raw


def check(library: NativeLibrary, status: int) -> None:
    """Convertit un code d'erreur de l'ABI en exception typée (message demandé à la bibliothèque, borné)."""
    if status == abi.OK:
        return
    buffer = ctypes.create_string_buffer(abi.MAX_ERROR_MESSAGE_BYTES)
    library.cdll.kut_engine_last_error_message(buffer, len(buffer), None)
    raise error_for(status, buffer.value.decode("utf-8", "replace"))


class _Handle:
    """Un handle natif avec fermeture idempotente et sûre entre threads."""

    def __init__(self, library: NativeLibrary, handle: int) -> None:
        self._lib = library
        self._handle = handle
        self._lock = threading.RLock()
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def _check(self, status: int) -> None:
        check(self._lib, status)

    def _live(self) -> int:
        if self._closed:
            raise InvalidHandleError(abi.ERR_INVALID_HANDLE, "objet fermé")
        return self._handle

    def __enter__(self):
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        raise NotImplementedError


class NativeEngine(_Handle):
    """Un moteur natif (contexte du processus). Se crée par :meth:`open`."""

    def __init__(self, library: NativeLibrary, handle: int) -> None:
        super().__init__(library, handle)
        # Propriété FORTE : une session non fermée reste référencée ici jusqu'à sa destruction native réussie, même si l'appelant l'a lâchée ;
        # ``close()`` du moteur la ferme donc toujours (sinon l'ABI refuserait de détruire un moteur qui a encore des sessions).
        self._sessions: set[NativeSession] = set()
        self._sessions_lock = threading.Lock()                # ne protège que l'ensemble (jamais tenu pendant un appel natif)

    @classmethod
    def open(cls, library: NativeLibrary | None = None) -> NativeEngine:
        """Charge la bibliothèque si besoin (:class:`NativeEngineUnavailable` si elle n'est pas disponible) et crée le moteur."""
        lib = library if library is not None else load_library()
        handle = ctypes.c_uint64(0)
        status = lib.cdll.kut_engine_create(None, ctypes.byref(handle))
        check(lib, status)
        return cls(lib, handle.value)

    @property
    def library(self) -> NativeLibrary:
        return self._lib

    def device_name(self) -> str:
        with self._lock:
            handle = self._live()
            needed = ctypes.c_size_t(0)
            buffer = ctypes.create_string_buffer(256)
            self._check(self._lib.cdll.kut_engine_device_name(handle, buffer, len(buffer), ctypes.byref(needed)))
            return buffer.value.decode("utf-8", "replace")

    def create_session(
        self, plan: bytes | bytearray | memoryview, *, present: bool = True, queue_depth: int = 0, max_in_flight: int = 0
    ) -> NativeSession:
        """Crée une session de lecture pour un plan KFPL (1 octet à 16 Mio)."""
        size = len(plan)
        if not 1 <= size <= abi.MAX_PLAN_BYTES:
            raise InvalidArgumentError(abi.ERR_INVALID_ARGUMENT, "taille de plan hors limites")
        if not (0 <= queue_depth <= 32 and 0 <= max_in_flight <= 8):
            raise InvalidArgumentError(abi.ERR_INVALID_ARGUMENT, "profondeur de file ou images en vol hors limites")
        config = abi.SessionConfig()
        config.struct_size = ctypes.sizeof(abi.SessionConfig)
        config.flags = 0 if present else abi.SESSION_FLAG_NO_PRESENT
        config.source_queue_depth = queue_depth
        config.max_in_flight = max_in_flight
        data = (ctypes.c_uint8 * size).from_buffer_copy(plan)
        with self._lock:
            handle = self._live()
            session = ctypes.c_uint64(0)
            self._check(self._lib.cdll.kut_engine_session_create(handle, data, size, ctypes.byref(config), ctypes.byref(session)))
            created = NativeSession(self._lib, session.value, self)
            with self._sessions_lock:
                self._sessions.add(created)
            return created

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            with self._sessions_lock:
                pending = list(self._sessions)
            for session in pending:
                session.close()                               # une erreur remonte : le moteur reste ouvert et close() peut être relancé
            self._check(self._lib.cdll.kut_engine_destroy(self._handle))
            self._closed = True                               # posé seulement après la destruction réussie

    def _forget(self, session: NativeSession) -> None:
        with self._sessions_lock:
            self._sessions.discard(session)


class NativeSession(_Handle):
    """Une session de lecture (un plan, ses files de sources, son horloge). Créée par :meth:`NativeEngine.create_session`."""

    def __init__(self, library: NativeLibrary, handle: int, engine: NativeEngine) -> None:
        super().__init__(library, handle)
        self._engine = engine

    def _call(self, name: str, *args: Any) -> None:
        with self._lock:
            self._check(getattr(self._lib.cdll, name)(self._live(), *args))

    def sources(self) -> list[SourceDescription]:
        with self._lock:
            handle = self._live()
            count = ctypes.c_uint32(0)
            self._check(self._lib.cdll.kut_engine_session_source_count(handle, ctypes.byref(count)))
            found = []
            for index in range(count.value):
                info = abi.SourceInfo()
                info.struct_size = ctypes.sizeof(abi.SourceInfo)
                self._check(self._lib.cdll.kut_engine_session_source_info(handle, index, ctypes.byref(info)))
                found.append(
                    SourceDescription(info.id.decode("utf-8", "replace"), info.kind, info.layout.decode("ascii", "replace"), info.width, info.height)
                )
            return found

    def submit_pixel_buffer(self, source_id: str, pts_us: int, pixel_buffer: int) -> None:
        """Pousse une image décodée (``CVPixelBufferRef`` sous forme d'entier non nul). Lève :class:`QueueFullError` ou
        :class:`SeekRequiredError` (l'image n'est alors pas retenue)."""
        if not isinstance(pixel_buffer, int) or pixel_buffer == 0:
            raise InvalidArgumentError(abi.ERR_INVALID_ARGUMENT, "image nulle")
        self._call("kut_engine_session_submit_pixel_buffer", _source_id(source_id), pts_us, ctypes.c_void_p(pixel_buffer))

    def end_of_stream(self, source_id: str) -> None:
        self._call("kut_engine_session_end_of_stream", _source_id(source_id))

    def poll_seek(self, source_id: str) -> int | None:
        """La position source à reprendre si le moteur a demandé un saut depuis le dernier appel (il est alors acquitté), sinon ``None``."""
        target, pending = ctypes.c_int64(0), ctypes.c_int32(0)
        self._call("kut_engine_session_poll_seek", _source_id(source_id), ctypes.byref(target), ctypes.byref(pending))
        return target.value if pending.value else None

    def set_mapping(self, source_id: str, *, start_us: int = 0, end_us: int | None = None, in_us: int = 0, speed: float = 1.0, frame_us: int = 33333) -> None:
        mapping = abi.SourceMapping()
        mapping.struct_size = ctypes.sizeof(abi.SourceMapping)
        mapping.timeline_start_us = start_us
        mapping.timeline_end_us = (1 << 63) - 1 if end_us is None else end_us
        mapping.source_in_us = in_us
        mapping.speed = speed
        mapping.frame_us = frame_us
        self._call("kut_engine_session_set_mapping", _source_id(source_id), ctypes.byref(mapping))

    def set_static_image(self, source_id: str, data: bytes, stride: int) -> None:
        """Image fixe d'une source rgba/bgra. Les bornes sont vérifiées ICI, avant l'appel natif, qui ne reçoit qu'un pointeur et un pas :
        ``stride >= largeur * 4`` et ``len(data) >= stride * hauteur`` (la bibliothèque copie ``stride * hauteur`` octets)."""
        raw_id = _source_id(source_id)
        source = next((item for item in self.sources() if item.id == source_id), None)
        if source is None or source.kind != abi.SOURCE_KIND_IMAGE:
            raise NotFoundError(abi.ERR_NOT_FOUND, "pas de source image de ce nom")
        row = source.width * BYTES_PER_PIXEL
        if not isinstance(stride, int) or isinstance(stride, bool) or not row <= stride <= abi.MAX_IMAGE_STRIDE:
            raise InvalidArgumentError(abi.ERR_INVALID_ARGUMENT, "pas de ligne (stride) invalide")
        required = stride * source.height
        if required > abi.MAX_IMAGE_BYTES:
            raise InvalidArgumentError(abi.ERR_INVALID_ARGUMENT, "image trop grande")
        if len(data) < required:
            raise InvalidArgumentError(abi.ERR_INVALID_ARGUMENT, "image trop courte pour la largeur, la hauteur et le pas")
        raw = (ctypes.c_uint8 * required).from_buffer_copy(data, 0)
        self._call("kut_engine_session_set_static_image", raw_id, raw, stride)

    def play(self) -> None:
        self._call("kut_engine_session_play")

    def pause(self) -> None:
        self._call("kut_engine_session_pause")

    def seek(self, position_us: int) -> None:
        self._call("kut_engine_session_seek", position_us)

    def set_speed(self, speed: float) -> None:
        self._call("kut_engine_session_set_speed", speed)

    def report_audio_position(self, played_us: int, host_time_us: int) -> None:
        self._call("kut_engine_session_report_audio_position", played_us, host_time_us)

    def position(self) -> tuple[int, bool]:
        position, playing = ctypes.c_int64(0), ctypes.c_int32(0)
        self._call("kut_engine_session_position", ctypes.byref(position), ctypes.byref(playing))
        return position.value, bool(playing.value)

    def _frame(self, name: str, *args: Any) -> FrameResult:
        info = abi.FrameInfo()
        info.struct_size = ctypes.sizeof(abi.FrameInfo)
        self._call(name, *args, ctypes.byref(info))
        return FrameResult(
            info.position_us, bool(info.flags & abi.FRAME_RENDERED), bool(info.flags & abi.FRAME_STALE), info.sources_ready,
            info.sources_missing, info.cpu_ms,
        )

    def render(self) -> FrameResult:
        """Rend l'image de la position de l'horloge (un appel par tick d'affichage)."""
        return self._frame("kut_engine_session_render")

    def render_at(self, position_us: int) -> FrameResult:
        return self._frame("kut_engine_session_render_at", position_us)

    def wait_idle(self) -> None:
        self._call("kut_engine_session_wait_idle")

    def stats(self) -> dict[str, float]:
        stats = abi.Stats()
        stats.struct_size = ctypes.sizeof(abi.Stats)
        self._call("kut_engine_session_get_stats", ctypes.byref(stats))
        return {name: getattr(stats, name) for name in (*abi.STATS_COUNTERS, *abi.STATS_FIGURES)}

    def reset_stats(self) -> None:
        self._call("kut_engine_session_reset_stats")

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._check(self._lib.cdll.kut_engine_session_destroy(self._handle))
            self._closed = True                               # seulement après succès : un échec laisse close() ré-essayable
            self._engine._forget(self)
