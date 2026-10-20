"""Aperçu d'un clip « scène 3D » dans le moniteur : image figée et approximative (lot B3, ADR-0009).

Décision de l'utilisateur : le moniteur ne rend **jamais** la 3D en direct dans la boucle de lecture. Il montre une image
figée, rendue à l'arrêt ou au défilement (« scrub ») ; les segments d'aperçu fidèle (même graphe que l'export) prennent
ensuite le relais, comme pour les autres calques. Ce module en porte les règles, sans Qt-widgets ni fil de travail :

- ``interactive`` : rend (sur le fil appelant, qui doit être le fil principal pour le moteur GPU) à une taille réduite
  (:data:`core.scene3d.INTERACTIVE_SIDE` au plus) et garde l'image dans un petit cache LRU ;
- ``frozen`` (lecture en cours) : **aucun rendu** ; resert l'image figée de cet instant si elle existe, sinon la dernière image
  rendue pour ce fichier, sinon une carte « Scène 3D » ;
- un rendu qui échoue : carte « Scène 3D indisponible » + un avertissement dans le journal (une fois par image), jamais d'exception.

Le moteur et la taille viennent d'un fournisseur posé par l'interface (:func:`set_backend_provider`) : numpy en petit tant que
la sonde GPU (30 s au plus, en arrière-plan) n'a pas répondu, puis ``auto``. Sans fournisseur : numpy en petit.
"""

from __future__ import annotations

import logging
import os
import threading
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

from .scene3d import MODE_INTERACTIVE, Scene3DError, _short, render_identity, scene_image
from .scene3d_card import CARD_TEXT, PLACEHOLDER_TEXT, scene3d_card

LOGGER = logging.getLogger("kut_studio.scene3d")

MODE_FROZEN = "frozen"
"""Mode d'aperçu pendant la lecture : jamais de rendu (voir le module)."""

PREVIEW_MODES = (MODE_INTERACTIVE, MODE_FROZEN)

INTERIM_SIDE = 240
"""Plus grand côté de l'aperçu numpy, tant que le moteur GPU n'est pas confirmé (numpy rend ≈ 0,5 s en 720p)."""

FROZEN_LIMIT = 24
"""Images figées gardées (≤ 480 px chacune, ≈ 0,5 Mo) ; la plus ancienne sort la première."""

_LAST_LIMIT = 16
_FAILED_LIMIT = 256

_LOCK = threading.Lock()
_FROZEN: OrderedDict[tuple, tuple[str, Any]] = OrderedDict()      # identité du rendu -> (fichier, image)
_LAST: OrderedDict[str, Any] = OrderedDict()                      # fichier -> dernière image rendue
_FAILED: set[tuple] = set()

BackendChoice = tuple[str, int | None]
_provider: Callable[[], BackendChoice] | None = None


def set_backend_provider(provider: Callable[[], BackendChoice] | None) -> None:
    """Pose (ou retire) la fonction qui dit ``(moteur, plus grand côté)`` de l'aperçu ; appelée à chaque rendu, sans bloquer."""
    global _provider
    _provider = provider


def preview_backend() -> BackendChoice:
    """``(moteur, côté)`` du prochain rendu d'aperçu ; ne bloque jamais (le fournisseur lit un état déjà connu)."""
    if _provider is not None:
        try:
            engine, side = _provider()
            return str(engine), side
        except Exception:                      # un fournisseur fautif ne doit jamais priver le moniteur de son image
            LOGGER.debug("Fournisseur de moteur d'aperçu 3D en échec : numpy", exc_info=True)
    return "numpy", INTERIM_SIDE


def _remember(identity: tuple, path: str, image: Any) -> None:
    with _LOCK:
        _FROZEN[identity] = (path, image)
        _FROZEN.move_to_end(identity)
        while len(_FROZEN) > FROZEN_LIMIT:
            _FROZEN.popitem(last=False)
        _LAST[path] = image
        _LAST.move_to_end(path)
        while len(_LAST) > _LAST_LIMIT:
            _LAST.popitem(last=False)


def preview_image(graphic: Any, box: tuple[float, float], *, playing: bool = False) -> Any | None:
    """Image figée de ``graphic`` : ``QImage``, ou ``None`` si on lit et qu'aucune image n'existe encore pour ce fichier.

    ``playing`` : ne rend rien (voir le module). Lève si le rendu échoue (:func:`draw_preview` l'attrape) ; un échec déjà
    constaté pour cette image n'est pas retenté tant que le fichier ou la taille ne changent pas (ni journalisé deux fois).
    """
    engine, side = preview_backend()
    identity = render_identity(graphic, box, MODE_INTERACTIVE, side)
    path = str(graphic.source_path)
    with _LOCK:
        cached = _FROZEN.get(identity)
        if cached is not None:
            _FROZEN.move_to_end(identity)
            return cached[1]
        if playing:
            return _LAST.get(path)
        failed = identity in _FAILED
    if failed:
        raise Scene3DError("rendu déjà en échec pour cette image.")
    try:
        try:
            image = scene_image(graphic, box, MODE_INTERACTIVE, backend=engine, side=side)
        except Exception:
            if engine == "numpy":
                raise
            LOGGER.debug("Moteur %s en échec : repli sur numpy", engine, exc_info=True)
            image = scene_image(graphic, box, MODE_INTERACTIVE, backend="numpy", side=side)
    except Exception as exc:
        with _LOCK:
            if len(_FAILED) >= _FAILED_LIMIT:
                _FAILED.clear()
            _FAILED.add(identity)
        LOGGER.warning("Aperçu de la scène 3D %s non rendu : %s", _short(os.path.basename(path)), _short(str(exc), 160))
        raise
    _remember(identity, path, image)
    return image


def draw_preview(painter: Any, graphic: Any, width: float, height: float, mode: str) -> None:
    """Dessine l'aperçu dans la boîte ``(0, 0, width, height)`` ; sans image, une carte ; **ne lève jamais**."""
    from PySide6.QtCore import QRectF

    text = CARD_TEXT
    try:
        if mode not in PREVIEW_MODES:
            raise ValueError(f"Mode d'aperçu inconnu : {_short(mode)}.")
        image = preview_image(graphic, (width, height), playing=mode == MODE_FROZEN)
        if image is not None:
            painter.drawImage(QRectF(0, 0, width, height), image)
            return
        text = PLACEHOLDER_TEXT
    except Exception as exc:
        LOGGER.debug("Carte de substitution de l'aperçu 3D : %s", _short(str(exc), 120))
    from .mograph_raster import draw_content

    try:
        draw_content(painter, scene3d_card(graphic, text), (width, height))
    except Exception:
        LOGGER.debug("Carte de substitution de l'aperçu 3D non dessinée", exc_info=True)


def forget_preview(path: str | os.PathLike[str]) -> None:
    """Oublie les images figées de ``path`` et les échecs mémorisés (bouton « Recharger »)."""
    key = str(path)
    with _LOCK:
        for identity in [item for item, (name, _image) in _FROZEN.items() if name == key]:
            del _FROZEN[identity]
        _LAST.pop(key, None)
        _FAILED.clear()


def clear_preview_cache() -> None:
    """Vide les images figées et les échecs (tests, changement de projet)."""
    with _LOCK:
        _FROZEN.clear()
        _LAST.clear()
        _FAILED.clear()


__all__ = [
    "FROZEN_LIMIT", "INTERIM_SIDE", "MODE_FROZEN", "PREVIEW_MODES", "clear_preview_cache", "draw_preview", "forget_preview",
    "preview_backend", "preview_image", "set_backend_provider",
]
