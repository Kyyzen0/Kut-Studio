"""Lecteur du mixage en direct : le flux de :mod:`core.live_mix` vers la sortie audio, calé sur la tête de lecture.

Un fil de travail lit le flux FFmpeg bloc par bloc, en tire les vumètres (:class:`core.live_mix.MixMeter`) et range le
son à jouer dans un tampon borné (:data:`MAX_AHEAD_SECONDS` d'avance : FFmpeg attend quand il est plein). Un minuteur de
l'interface pousse ce tampon dans un ``QAudioSink`` en mode « push » : une seule sortie, sans trou entre les blocs.

**Calage.** Au premier envoi, les échantillons déjà dépassés par la tête de lecture (le temps de lancer FFmpeg) sont
sautés, plus la latence de la sortie : le premier échantillon entendu est celui de la tête. Ensuite le son avance au
rythme de la carte son, la tête au rythme de l'horloge : :attr:`LiveMixPlayer.heard_time` permet à la fenêtre de
relancer le flux si l'écart grandit (gel de l'interface, saut).

**Fils et Qt.** Le fil de travail ne crée ni ne détruit aucun objet Qt (un ``QObject`` détruit hors du fil de
l'interface peut l'interbloquer avec le GIL, voir l'analyse des scopes) : il ne manipule que des octets, numpy et le
processus FFmpeg. La sortie audio et le minuteur vivent sur le fil de l'interface.
"""

from __future__ import annotations

import logging
import subprocess
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, QTimer, Signal

from core.live_mix import SAMPLE_RATE, LiveMixCommand, MeterFrame, MixMeter
from core.process_supervisor import supervised_popen

LOGGER = logging.getLogger(__name__)

MAX_AHEAD_SECONDS = 2.0
"""Son décodé d'avance au plus : assez pour ne jamais manquer, assez peu pour qu'un arrêt soit immédiat."""
SINK_BUFFER_SECONDS = 0.1
"""Tampon de la sortie audio : sa latence, compensée au calage."""
PUMP_INTERVAL_MS = 10
READ_FRAMES = 1024


def _qt_sink(sample_format: str):
    """Sortie audio par défaut, 48 kHz stéréo, au format demandé (``float`` ou ``int16``)."""
    from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices

    fmt = QAudioFormat()
    fmt.setSampleRate(SAMPLE_RATE)
    fmt.setChannelCount(2)
    fmt.setSampleFormat(QAudioFormat.SampleFormat.Float if sample_format == "float"
                        else QAudioFormat.SampleFormat.Int16)
    return QAudioSink(QMediaDevices.defaultAudioOutput(), fmt)


def output_available() -> bool:
    """Une sortie audio existe (une machine sans carte son, une CI : le moniteur garde son propre son)."""
    try:
        from PySide6.QtMultimedia import QMediaDevices

        return not QMediaDevices.defaultAudioOutput().isNull()
    except Exception:
        LOGGER.debug("Sorties audio non listées : pas de mixage en direct", exc_info=True)
        return False


def _qt_float_supported() -> bool:
    from PySide6.QtMultimedia import QAudioFormat, QMediaDevices

    fmt = QAudioFormat()
    fmt.setSampleRate(SAMPLE_RATE)
    fmt.setChannelCount(2)
    fmt.setSampleFormat(QAudioFormat.SampleFormat.Float)
    return bool(QMediaDevices.defaultAudioOutput().isFormatSupported(fmt))


@dataclass
class _Session:
    """Une lecture : le flux, son tampon, ses vumètres. Partagé entre le fil de travail et l'interface (``lock``)."""

    live: LiveMixCommand
    sample_format: str
    lock: threading.Condition = field(default_factory=threading.Condition)
    buffer: bytearray = field(default_factory=bytearray)
    buffered_from: int = 0                                  # image (depuis ``live.start``) du premier octet du tampon
    meters: deque = field(default_factory=deque)
    stopped: bool = False
    finished: bool = False
    error: str = ""
    process: object = None

    @property
    def frame_bytes(self) -> int:
        return 8 if self.sample_format == "float" else 4


class LiveMixPlayer(QObject):
    """Joue une :class:`~core.live_mix.LiveMixCommand` ; ``failed`` : FFmpeg n'a pas pu fournir le flux."""

    failed = Signal(str)

    def __init__(self, parent=None, *, sink_factory=None, float_supported=None, popen=None) -> None:
        super().__init__(parent)
        self._sink_factory = sink_factory or _qt_sink
        self._float_supported = float_supported or _qt_float_supported
        self._popen = popen or supervised_popen
        self._session: _Session | None = None
        self._thread: threading.Thread | None = None
        self._sink = None
        self._device = None
        self._origin_frame: int | None = None               # image jouée par le premier octet envoyé à la sortie
        self._pushed_frames = 0
        self._clock: Callable[[], float] | None = None
        self._volume = 1.0
        self._timer = QTimer(self)
        self._timer.setInterval(PUMP_INTERVAL_MS)
        self._timer.timeout.connect(self._pump)

    # -- commandes ----------------------------------------------------------------------------------------------------

    @property
    def active(self) -> bool:
        return self._session is not None

    def start(self, live: LiveMixCommand, *, clock: Callable[[], float]) -> None:
        """Lance le flux ; ``clock()`` donne la tête de lecture (secondes de timeline) au moment du calage."""
        self.stop()
        session = _Session(live, "float" if self._float_supported() else "int16")
        self._session = session
        self._clock = clock
        self._origin_frame = None
        self._pushed_frames = 0
        self._thread = threading.Thread(target=self._read, args=(session,), name="kut-live-mix", daemon=True)
        self._thread.start()
        self._timer.start()

    def stop(self) -> None:
        session, self._session = self._session, None
        self._timer.stop()
        if session is not None:
            with session.lock:
                session.stopped = True
                session.lock.notify_all()
            process = session.process
            if process is not None:
                try:
                    process.kill()                          # type: ignore[attr-defined]
                except OSError:
                    pass
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=2.0)
        if self._sink is not None:
            self._sink.stop()                               # sur le fil de l'interface, comme sa création
        self._sink = None
        self._device = None

    @property
    def track_ids(self) -> tuple[str, ...]:
        """Les pistes mesurées du flux en cours, dans l'ordre de ``MeterFrame.track_peaks_db``."""
        session = self._session
        return session.live.track_ids if session is not None else ()

    def set_volume(self, volume: float) -> None:
        self._volume = max(0.0, min(1.0, float(volume)))
        if self._sink is not None:
            self._sink.setVolume(self._volume)

    # -- lecture ------------------------------------------------------------------------------------------------------

    @property
    def heard_time(self) -> float | None:
        """Instant de la timeline qu'on entend en ce moment (``None`` : rien n'est encore parti vers la sortie).

        D'après ce que la sortie a **joué** (``processedUSecs``), pas ce qu'on lui a envoyé : le premier échantillon
        envoyé visait la tête plus la latence de la sortie. Mesuré sur une vraie sortie (macOS) : 3 ms de la tête,
        contre 103 ms en comptant les octets envoyés."""
        session = self._session
        if session is None or self._origin_frame is None or self._sink is None:
            return None
        played = int(self._sink.processedUSecs()) / 1e6
        return session.live.start + self._origin_frame / SAMPLE_RATE + played - SINK_BUFFER_SECONDS

    def meter(self) -> MeterFrame | None:
        """Le dernier relevé des vumètres arrivé à l'oreille (``None`` : aucun)."""
        session = self._session
        heard = self.heard_time
        if session is None or heard is None:
            return None
        with session.lock:
            latest = None
            while session.meters and session.meters[0].time <= heard:
                latest = session.meters.popleft()
            if latest is not None:
                session.meters.appendleft(latest)            # gardé : il reste le relevé courant jusqu'au suivant
            return latest

    # -- fil de travail -----------------------------------------------------------------------------------------------

    def _read(self, session: _Session) -> None:
        """Lit le flux, mesure, range le son à jouer ; s'arrête à la fin du flux ou sur ``stop``."""
        import numpy as np

        live = session.live
        read_bytes = READ_FRAMES * live.frame_bytes
        ahead = int(MAX_AHEAD_SECONDS * SAMPLE_RATE) * session.frame_bytes
        meter = MixMeter(live.channels, live.start)
        preroll = live.preroll_frames
        pending = b""
        try:
            with self._popen(list(live.command), stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
                with session.lock:
                    session.process = process
                    if session.stopped:
                        process.kill()
                        return
                while True:
                    block = process.stdout.read(read_bytes)
                    if not block:
                        break
                    data = pending + block
                    usable = len(data) - len(data) % live.frame_bytes
                    pending = data[usable:]
                    frames = np.frombuffer(data[:usable], dtype="<f4").reshape(-1, live.channels)
                    if preroll:
                        head, frames = frames[:preroll], frames[preroll:]
                        preroll -= len(head)
                        meter.feed(head, played=False)
                    if not len(frames):
                        continue
                    readings = meter.feed(frames)
                    heard = frames[:, 0:2]
                    if session.sample_format == "float":
                        payload = np.ascontiguousarray(heard, dtype="<f4").tobytes()
                    else:
                        payload = (np.clip(heard, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
                    with session.lock:
                        while len(session.buffer) >= ahead and not session.stopped:
                            session.lock.wait(0.05)
                        if session.stopped:
                            process.kill()
                            return
                        session.buffer += payload
                        session.meters.extend(readings)
                errors = process.stderr.read().decode("utf-8", "replace").strip() if process.stderr else ""
                code = process.wait()
        except OSError as error:
            code, errors = -1, str(error)
        with session.lock:
            session.finished = True
            if code != 0 and not session.stopped:
                exited = f"FFmpeg a quitté avec le code {code}"  # i18n-ignore: diagnostic du journal, jamais affiché
                session.error = errors.splitlines()[-1] if errors else exited

    # -- interface ----------------------------------------------------------------------------------------------------

    def _pump(self) -> None:
        """Pousse vers la sortie ce qu'elle peut prendre ; au premier envoi, cale le son sur la tête de lecture."""
        session = self._session
        if session is None:
            return
        with session.lock:
            error = session.error
        if error:
            LOGGER.warning("Mixage en direct interrompu : %s", error)
            self.stop()
            self.failed.emit(error)
            return
        if self._sink is None:
            self._sink = self._sink_factory(session.sample_format)
            self._sink.setBufferSize(int(SINK_BUFFER_SECONDS * SAMPLE_RATE) * session.frame_bytes)
            self._sink.setVolume(self._volume)
            self._device = self._sink.start()
        with session.lock:
            if self._origin_frame is None:
                if not session.buffer:
                    return
                # Le premier échantillon envoyé sera entendu après la latence de la sortie : on vise la tête à ce moment.
                playhead = float(self._clock()) if self._clock is not None else session.live.start
                target = int(round((playhead + SINK_BUFFER_SECONDS - session.live.start) * SAMPLE_RATE))
                skip = max(0, target - session.buffered_from)
                available = len(session.buffer) // session.frame_bytes
                dropped = min(skip, available)
                del session.buffer[:dropped * session.frame_bytes]
                session.buffered_from += dropped
                if dropped < skip:                          # pas encore assez de son pour rattraper la tête
                    session.lock.notify_all()
                    return
                self._origin_frame = session.buffered_from
            free = int(self._sink.bytesFree())
            count = min(free, len(session.buffer))
            count -= count % session.frame_bytes
            if count <= 0:
                return
            chunk = bytes(session.buffer[:count])
            del session.buffer[:count]
            session.buffered_from += count // session.frame_bytes
            session.lock.notify_all()
        written = int(self._device.write(chunk)) if self._device is not None else 0
        self._pushed_frames += max(0, written) // session.frame_bytes
        if written < len(chunk):                            # la sortie a pris moins qu'annoncé : le reste repart devant
            with session.lock:
                rest = chunk[max(0, written):]
                session.buffer[:0] = rest
                session.buffered_from -= len(rest) // session.frame_bytes


__all__ = ["LiveMixPlayer", "MAX_AHEAD_SECONDS", "SINK_BUFFER_SECONDS", "output_available"]
