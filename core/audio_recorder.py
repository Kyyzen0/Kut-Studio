"""Enregistrement du microphone vers un fichier WAV.

L'enregistrement ne démarre que sur demande, et seulement s'il existe
une entrée audio. Le tampon est lu par petits blocs pour laisser
l'interface respirer. À l'arrêt, les octets PCM deviennent un WAV
classique que le projet peut importer comme n'importe quel média.
"""

from __future__ import annotations

import wave
from pathlib import Path


class AudioRecorderError(RuntimeError):
    """Le microphone est absent ou refuse le format demandé."""


class AudioRecorder:
    """Capture mono 16 bits via Qt Multimedia."""

    def __init__(self) -> None:
        self.sample_rate = 48000
        self._buffer = bytearray()
        self._source = None
        self._io = None
        self._timer = None
        self._channels = 1

    @property
    def is_recording(self) -> bool:
        return self._source is not None

    def start(self) -> None:
        """Ouvre le microphone par défaut. Lève si aucun n'est disponible."""
        if self._source is not None:
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
            self._source.stop()
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
