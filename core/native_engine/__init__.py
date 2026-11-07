"""Moteur vidéo natif (C++/Metal), chargé à la demande par ``ctypes`` : point d'entrée de l'intégration (jalon M4).

Le moteur natif vit dans un dépôt séparé ; sa bibliothèque ``libkutengine.dylib`` n'a ni décodeur ni FFmpeg et expose l'ABI 1
(``docs/native-engine.md``). **Rien n'importe ce paquet au démarrage et rien n'est branché dans le moniteur** : il prépare le point
d'intégration. ``available()`` dit si la bibliothèque est chargeable ici, ``unavailable_reason()`` pourquoi elle ne l'est pas ; dans ce
cas l'application continue sur le moniteur QRhi existant (le repli est le comportement par défaut).
"""

from __future__ import annotations

from . import abi
from .engine import FrameResult, NativeEngine, NativeSession, SourceDescription
from .errors import (
    BadFrameError,
    BufferTooSmallError,
    DeviceError,
    InternalError,
    InvalidArgumentError,
    InvalidHandleError,
    InvalidPlanError,
    InvalidStateError,
    NativeEngineError,
    NotFoundError,
    QueueFullError,
    SeekRequiredError,
    UnsupportedError,
)
from .loader import NativeEngineUnavailable, NativeLibrary, Reason, available, load_library, probe, reset, unavailable_reason

__all__ = [
    "BadFrameError", "BufferTooSmallError", "DeviceError", "FrameResult", "InternalError", "InvalidArgumentError", "InvalidHandleError",
    "InvalidPlanError", "InvalidStateError", "NativeEngine", "NativeEngineError", "NativeEngineUnavailable", "NativeLibrary",
    "NativeSession", "NotFoundError", "QueueFullError", "Reason", "SeekRequiredError", "SourceDescription", "UnsupportedError",
    "abi", "available", "load_library", "probe", "reset", "unavailable_reason",
]
