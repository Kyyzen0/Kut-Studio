"""Enregistrement du microphone vers un fichier WAV.

L'enregistrement ne démarre que sur demande, et seulement s'il existe
une entrée audio. Le tampon est lu par petits blocs pour laisser
l'interface respirer. À l'arrêt, les octets PCM deviennent un WAV
classique que le projet peut importer comme n'importe quel média.

Pour rendre la prise testable **sans matériel**, l'enregistreur accepte
une source injectable (:class:`AudioSource`) : la source Qt.Multimedia
en production, une source synthétique en test. L'API publique de
:class:`AudioRecorder` ne change pas.
"""

from __future__ import annotations

import logging
import math
import wave
from pathlib import Path
from typing import Protocol

LOGGER = logging.getLogger(__name__)


class AudioRecorderError(RuntimeError):
    """Le microphone est absent ou refuse le format demandé."""


class AudioSource(Protocol):
    """Source audio minimale consommée par :class:`AudioRecorder`."""

    def start(self) -> "AudioChunkReader": ...

    def stop(self) -> None: ...


class AudioChunkReader(Protocol):
    """Lecteur de blocs PCM renvoyé par une source."""

    def readAll(self) -> bytes: ...


class ToneSource:
    """Source synthétique : sinus de fréquence donnée.

    Sert aux tests et au réglage : elle produit un signal réel, au sens
    du format, sans matériel. Elle permet notamment de vérifier que la
    priseplacement produit un clip exploitable par le mixeur.
    """

    def __init__(
        self,
        sample_rate: int = 48000,
        channels: int = 1,
        frequency: float = 440.0,
        block_frames: int = 2048,
    ) -> None:
        self.sample_rate = max(1, int(sample_rate))
        self.channels = max(1, int(channels))
        self.frequency = float(frequency)
        self.block_frames = max(1, int(block_frames))
        self._position = 0
        self._reader: "_ToneReader | None" = None

    def start(self) -> "_ToneReader":
        self._position = 0
        self._reader = _ToneReader(self)
        return self._reader

    def stop(self) -> None:
        self._reader = None

    def _next_block(self) -> bytes:
        import struct

        samples = []
        for _ in range(self.block_frames):
            phase = 2.0 * math.pi * self.frequency * self._position / self.sample_rate
            value = int(20000 * math.sin(phase))
            samples.extend([value] * self.channels)
            self._position += 1
        return struct.pack(f"<{len(samples)}h", *samples)


class _ToneReader:
    """Lecteur de blocs pour :class:`ToneSource`."""

    def __init__(self, source: ToneSource) -> None:
        self._source = source

    def readAll(self) -> bytes:
        return self._source._next_block()


class AudioRecorder:
    """Capture mono 16 bits via Qt Multimedia."""

    def __init__(self, source: AudioSource | None = None) -> None:
        self.sample_rate = 48000
        self._buffer = bytearray()
        self._source = None
        self._io = None
        self._timer = None
        self._channels = 1
        #: Source injectée (tests). ``None`` = matériel par défaut.
        self._injected: AudioSource | None = source

    @property
    def is_recording(self) -> bool:
        return self._source is not None

    def start(self) -> None:
        """Ouvre la source d'audio. Lève si aucune n'est disponible."""
        if self._source is not None:
            return
        if self._injected is not None:
            self._source = self._injected
            # Une source injectée peut imposer son propre format : on
            # le reprend, sinon le WAV produit serait étiqueté faux.
            rate = getattr(self._injected, "sample_rate", None)
            if rate:
                self.sample_rate = max(1, int(rate))
            channels = getattr(self._injected, "channels", None)
            if channels:
                self._channels = max(1, int(channels))
            self._io = self._source.start()
            if self._io is None:
                self._source = None
                raise AudioRecorderError("La source audio n'a pas pu démarrer.")
            self._buffer = bytearray()
            self._start_timer()
            return
        try:
            from PySide6.QtCore import QTimer
            from PySide6.QtMultimedia import (
                QAudioFormat,
                QAudioSource,
                QMediaDevices,
            )
        except ImportError as exc:  # pragma: no cover
            raise AudioRecorderError("Qt Multimedia n'est pas disponible.") from exc

        device = QMediaDevices.defaultAudioInput()
        if device.isNull():
            raise AudioRecorderError("Aucun microphone n'est disponible.")
        audio_format = QAudioFormat()
        audio_format.setSampleRate(self.sample_rate)
        audio_format.setChannelCount(1)
        audio_format.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        if not device.isFormatSupported(audio_format):
            audio_format = device.preferredFormat()
        self.sample_rate = max(1, int(audio_format.sampleRate()))
        self._channels = max(1, int(audio_format.channelCount()))
        self._buffer = bytearray()
        self._source = QAudioSource(device, audio_format)
        self._io = self._source.start()
        if self._io is None:
            self._source = None
            raise AudioRecorderError("Le microphone n'a pas pu démarrer.")
        self._start_timer()

    def _start_timer(self) -> None:
        from PySide6.QtCore import QTimer

        self._timer = QTimer()
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._pull)
        self._timer.start()

    def stop(self) -> tuple[bytes, int, int]:
        """Arrête la capture et retourne ``(pcm, fréquence, canaux)``."""
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        self._pull()
        if self._source is not None:
            try:
                self._source.stop()
            except Exception:  # pragma: no cover - arrêt déjà partiel
                LOGGER.debug(
                    "Arrêt de la source micro en échec : capture déjà arrêtée, le son lu jusque-là est rendu",
                    exc_info=True,
                )
            self._source = None
        self._io = None
        pcm = bytes(self._buffer)
        self._buffer = bytearray()
        return pcm, self.sample_rate, self._channels

    def _pull(self) -> None:
        if self._io is None:
            return
        chunk = self._io.readAll()
        if chunk:
            self._buffer.extend(bytes(chunk))


def write_wav(path: str, pcm: bytes, sample_rate: int, channels: int = 1) -> None:
    """Écrit un WAV 16 bits. Le dossier parent est créé si besoin."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(target), "wb") as handle:
        handle.setnchannels(max(1, int(channels)))
        handle.setsampwidth(2)
        handle.setframerate(max(1, int(sample_rate)))
        handle.writeframes(pcm)


def pcm_duration(pcm: bytes, sample_rate: int, channels: int = 1) -> float:
    """Durée en secondes d'un tampon 16 bits."""
    frame = max(1, int(channels)) * 2
    if sample_rate <= 0 or not pcm:
        return 0.0
    return len(pcm) / (frame * int(sample_rate))
