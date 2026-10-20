"""Clip « scène 3D » : lecture du fichier ``.kut3d`` qu'il référence (lot B2a) puis rendu de ses images (lot B2b, ADR-0009).

Un clip 3D est un calque graphique (:attr:`core.graphics.GraphicType.SCENE3D`) dont ``source_path`` désigne un fichier de
scène ``.kut3d`` (:mod:`kut3d.scene_io`). Ce module ne fait que trois choses, toutes en lecture :

- :func:`validate` : le fichier se lit-il (forme, bornes, modèles confinés sous son dossier) ? Appelé à la création du clip ;
- :func:`dependencies` : les fichiers dont dépend la scène (le ``.kut3d``, ses modèles, leurs textures) ;
- :func:`scene_signature` : une empreinte bon marché de ces fichiers, pour les clés de cache du rendu.

Le rendu (B2b) : :func:`scene_image` rend la scène à l'instant ``graphic.scene_time`` avec le moteur **numpy** (toujours : les
images du cache disque doivent être les mêmes partout, le moteur entre dans la clé :func:`render_identity`), à la taille de la
boîte du calque ; :func:`draw_scene` la dessine comme une image dans le rastériseur des calques (transformations, opacité,
images clés, effets, masques et fusion s'appliquent donc comme à un calque image). Une scène sans animation vaut toujours
l'instant 0 (:func:`scene_time_at`) : ses images identiques se rendent UNE fois. Coût : ≈ 550 ms par image en 720p (numpy).

Politique d'erreur : **tolérant à l'écran** (:func:`draw_scene` laisse la boîte vide et journalise), **strict à l'export**
(:func:`strict_rendering` fait lever :func:`draw_scene` ; :mod:`core.scene3d_check` éprouve les scènes avant l'export).

``import core.scene3d`` n'importe pas ``kut3d`` (ni numpy, ni Qt) : tout se charge à l'appel. Les fonctions de lecture n'ouvrent
aucune fenêtre ; seul :func:`scene_image` appelle le moteur de rendu.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger("kut_studio.scene3d")

SCENE_SUFFIX = ".kut3d"
"""Extension d'un fichier de scène (comparée sans tenir compte de la casse)."""

MAX_PATH_CHARS = 4096
"""Longueur maximale du chemin d'une scène : au-delà, il est refusé avant tout accès au disque."""

MISSING_SIGNATURE = "missing"
"""Signature d'une scène dont le fichier ``.kut3d`` n'existe pas (ou n'est pas lisible) : valeur fixe, jamais une exception.
Une fois le fichier recréé, la signature change : le cache de rendu se renouvelle tout seul."""

INVALID_PREFIX = "invalid:"
"""Préfixe de la signature d'une scène dont le ``.kut3d`` existe mais ne se lit pas (ou dont un modèle manque) : suivi de
l'empreinte du seul ``.kut3d``. Ces signatures ne sont pas mémorisées (le fichier manquant peut revenir sans que le ``.kut3d`` change)."""

FINGERPRINT_SPAN = 8192
"""Octets lus au début et à la fin d'un fichier pour son empreinte (même règle que ``kut3d.render.images``)."""

_MEMO_LIMIT = 256
_MEMO: OrderedDict[str, tuple[tuple[tuple[str, int, int], ...], str]] = OrderedDict()
_MEMO_LOCK = threading.Lock()


SCENE3D_RASTER_VERSION = 2
"""Version du rendu d'une scène 3D dans un calque : dans la clé de chaque image du cache et dans l'empreinte des segments d'aperçu
(calques 3D seulement). À incrémenter dès que la **même** scène à la même taille et au même instant se dessine autrement
(moteur ``kut3d``, conversion en ``QImage``, pose dans la boîte).

2 : retire du cache les images de la version 1. Un aperçu qui n'arrivait pas à rendre une scène animée y avait écrit une image
vide sous la clé d'une image réussie ; un export ultérieur (autre processus, ou après oubli des échecs) la resservait et
livrait un film sans la scène. Le dessin d'une scène qui se rend est inchangé."""

RENDER_BACKEND = "numpy"
"""Moteur de rendu des clips 3D, en dur : le cache disque doit donner les mêmes images partout (un moteur GPU les ferait varier)."""

RENDER_SAMPLES = 1
"""Sur-échantillonnage par axe des images d'un clip 3D (l'aperçu fidèle et l'export n'en demandent pas plus)."""

MODE_EXACT = "exact"
MODE_INTERACTIVE = "interactive"
"""Modes de :func:`scene_image` : ``exact`` (export, aperçu fidèle) rend à la taille de la boîte ; ``interactive`` (aperçu en
direct, lot B3) réduit le plus grand côté à :data:`INTERACTIVE_SIDE`."""

INTERACTIVE_SIDE = 480

_SCENES_LIMIT = 4
_IMAGES_LIMIT = 2
_SCENES: OrderedDict[str, tuple[str, Any, bool]] = OrderedDict()
_IMAGES: OrderedDict[tuple, Any] = OrderedDict()
_CACHE_LOCK = threading.Lock()

_STRICT: ContextVar[bool] = ContextVar("kut_scene3d_strict", default=False)
_TOLERATED: ContextVar[int] = ContextVar("kut_scene3d_tolerated", default=0)
_FAILED: set[tuple] = set()
_FAILED_LIMIT = 256


class Scene3DError(ValueError):
    """Un fichier de scène ``.kut3d`` est refusé (message en français, sans recopier l'entrée entière)."""


def _short(value: object, limit: int = 60) -> str:
    text = repr(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _checked_path(path: str | os.PathLike[str]) -> Path:
    text = os.fspath(path)
    if not isinstance(text, str) or not text.strip():
        raise Scene3DError("Une scène 3D doit référencer un fichier « .kut3d ».")
    if len(text) > MAX_PATH_CHARS or "\x00" in text:
        raise Scene3DError(f"Chemin de scène 3D invalide ({_short(text)}).")
    candidate = Path(text)
    if candidate.suffix.lower() != SCENE_SUFFIX:
        raise Scene3DError(f"{_short(candidate.name)} : une scène 3D est un fichier « {SCENE_SUFFIX} ».")
    return candidate


def has_scene_suffix(text: str) -> bool:
    """``True`` si ``text`` se termine par l'extension d'une scène (sans toucher au disque)."""
    return isinstance(text, str) and text.lower().endswith(SCENE_SUFFIX)


def is_scene3d_clip(clip: object) -> bool:
    """``True`` si ``clip`` est un calque « scène 3D » (``graphic.type == "scene3d"``, comparé sans importer :mod:`core.graphics`)."""
    return getattr(getattr(clip, "graphic", None), "type", None) == "scene3d"


def ensure_fixed_speed(clip: object) -> None:
    """Refuse (``ValueError``) toute variation de vitesse, de sens ou d'arrêt sur image d'un calque scène 3D.

    Le temps d'une scène est ``source_in + (t - timeline_start)`` : une scène se joue à sa vitesse, jamais ralentie, accélérée,
    inversée ni figée (les calques graphiques n'ont pas de média à rééchantillonner ; leur ``time_remapping`` est ignoré au rendu).
    Appelé par chaque fonction du modèle qui écrit ``time_remapping`` : l'inspecteur, le menu et les scripts y passent donc tous.
    """
    if is_scene3d_clip(clip):
        raise ValueError("Une scène 3D se joue à sa propre vitesse : elle ne se ralentit, ne s'accélère, ne s'inverse ni ne se fige.")


def dependencies(path: str | os.PathLike[str]) -> tuple[Path, ...]:
    """Le ``.kut3d`` puis ses fichiers de modèle, MTL / .bin et textures (chemins absolus, triés, sans doublon).

    Lève :class:`Scene3DError` si la scène est refusée (introuvable, trop grosse, mal formée, modèle hors du dossier...).
    Lit et analyse les modèles (comme le fait le chargeur de scène) : pas de rendu.
    """
    from kut3d.errors import SceneFileError
    from kut3d.scene_io import dependency_files

    candidate = _checked_path(path)
    try:
        return dependency_files(candidate, allow_outside=False)
    except SceneFileError as exc:
        raise Scene3DError(str(exc)) from exc


def validate(path: str | os.PathLike[str]) -> None:
    """Vérifie que ``path`` est une scène lisible (forme, bornes, modèles confinés) sans rien rendre.

    Lève :class:`Scene3DError` en français sinon ; ne renvoie rien.
    """
    from kut3d.errors import SceneFileError
    from kut3d.scene_io import load_scene

    candidate = _checked_path(path)
    try:
        load_scene(candidate, allow_outside=False)
    except SceneFileError as exc:
        raise Scene3DError(str(exc)) from exc


def _fingerprint(path: Path, size: int) -> str:
    digest = hashlib.blake2b(digest_size=16)
    with path.open("rb") as handle:
        digest.update(handle.read(FINGERPRINT_SPAN))
        if size > 2 * FINGERPRINT_SPAN:
            handle.seek(size - FINGERPRINT_SPAN)
            digest.update(handle.read(FINGERPRINT_SPAN))
        elif size > FINGERPRINT_SPAN:
            digest.update(handle.read(FINGERPRINT_SPAN))
    return digest.hexdigest()


def _stat_key(path: Path) -> tuple[str, int, int]:
    info = path.stat()
    return (str(path), info.st_mtime_ns, info.st_size)


def _stat_keys(files: tuple[Path, ...]) -> tuple[tuple[str, int, int], ...] | None:
    try:
        return tuple(_stat_key(item) for item in files)
    except (OSError, ValueError):
        return None


def scene_signature(path: str | os.PathLike[str]) -> str:
    """Empreinte des fichiers dont dépend la scène : change dès que l'un d'eux change (date, taille, début ou fin du contenu).

    Bon marché pour une clé de cache : le résultat est mémorisé par chemin et revalidé par un simple ``stat`` de chaque
    fichier ; l'analyse de la scène et les lectures de contenu n'ont lieu qu'au premier appel ou après un changement.
    Limite connue (celle de ``kut3d.render.images``) : une modification de même date et de même taille qui épargne le début
    et la fin d'un fichier passe inaperçue. Ne lève jamais : un fichier manquant donne :data:`MISSING_SIGNATURE`, une scène
    illisible ``"invalid:" + empreinte du .kut3d`` (non mémorisée).
    """
    try:
        candidate = _checked_path(path)
    except Scene3DError:
        return MISSING_SIGNATURE
    key = str(candidate)
    with _MEMO_LOCK:
        known = _MEMO.get(key)
    if known is not None:
        stats, signature = known
        if _stat_keys(tuple(Path(item[0]) for item in stats)) == stats:
            return signature
    try:
        own = candidate.stat()
    except (OSError, ValueError):
        return MISSING_SIGNATURE
    try:
        files = dependencies(candidate)
    except Scene3DError:
        try:
            return INVALID_PREFIX + _fingerprint(candidate, own.st_size)
        except OSError:
            return MISSING_SIGNATURE
    fresh = _stat_keys(files)
    if fresh is None:                          # un fichier a disparu entre la lecture et le stat
        return INVALID_PREFIX + "unstable"
    digest = hashlib.blake2b(digest_size=16)
    try:
        for (name, mtime_ns, size), item in zip(fresh, files):
            digest.update(f"{name}|{mtime_ns}|{size}|{_fingerprint(item, size)};".encode())
    except OSError:
        return INVALID_PREFIX + "unstable"
    signature = digest.hexdigest()
    with _MEMO_LOCK:
        _MEMO[key] = (fresh, signature)
        _MEMO.move_to_end(key)
        while len(_MEMO) > _MEMO_LIMIT:
            _MEMO.popitem(last=False)
    return signature


def tolerated_failures() -> int:
    """Échecs de rendu avalés par :func:`draw_scene` dans ce fil (l'écran ne lève pas).

    Le cache disque des calques (:mod:`core.mograph_stream`) compare ce compte avant et après chaque image : s'il a
    augmenté, l'image écrite est vide et ne doit pas être resservie à l'export.
    """
    return _TOLERATED.get()


def clear_signature_cache() -> None:
    """Oublie les signatures mémorisées, les scènes chargées et les dernières images rendues (tests)."""
    _TOLERATED.set(0)
    with _MEMO_LOCK:
        _MEMO.clear()
    with _CACHE_LOCK:
        _SCENES.clear()
        _IMAGES.clear()
        _FAILED.clear()


# --- Scène chargée (mémoire bornée, revalidée par la signature) -------------------------------------------------------------


def load_scene(path: str | os.PathLike[str]) -> tuple[Any, bool]:
    """``(scène, animée)`` du fichier ``path`` ; au plus :data:`_SCENES_LIMIT` scènes restent en mémoire.

    Une entrée n'est resservie que si :func:`scene_signature` n'a pas changé (un simple ``stat`` par fichier). Lève
    :class:`Scene3DError` si le fichier manque ou ne se lit pas. ``animée`` : une instance au moins joue une animation (sinon
    l'image ne dépend pas du temps).
    """
    from kut3d.errors import SceneFileError
    from kut3d.scene_io import load_scene as read_scene

    candidate = _checked_path(path)
    signature = scene_signature(candidate)
    if signature == MISSING_SIGNATURE:
        raise Scene3DError(f"{_short(candidate.name)} : fichier de scène introuvable.")
    if signature.startswith(INVALID_PREFIX):
        validate(candidate)                                # relève la raison précise (modèle manquant, forme...)
        raise Scene3DError(f"{_short(candidate.name)} : scène illisible.")
    key = str(candidate)
    with _CACHE_LOCK:
        known = _SCENES.get(key)
        if known is not None and known[0] == signature:
            _SCENES.move_to_end(key)
            return known[1], known[2]
    try:
        scene = read_scene(candidate, allow_outside=False)
    except SceneFileError as exc:
        raise Scene3DError(str(exc)) from exc
    animated = any(instance.clip is not None for instance in scene.instances)
    with _CACHE_LOCK:
        _SCENES[key] = (signature, scene, animated)
        _SCENES.move_to_end(key)
        while len(_SCENES) > _SCENES_LIMIT:
            _SCENES.popitem(last=False)
    return scene, animated


def is_animated(path: str | os.PathLike[str]) -> bool:
    """``True`` si la scène joue une animation (ou si elle ne se lit pas : on ne présume alors pas qu'elle soit fixe)."""
    try:
        return load_scene(path)[1]
    except (Scene3DError, OSError):
        return True


def scene_time_at(path: str | os.PathLike[str], source_in: float, local: float) -> float:
    """Instant de la scène : ``source_in + temps local du clip`` (au dix-millième), ou 0 pour une scène sans animation.

    Couper ou rogner un clip avance ``source_in`` : la seconde moitié reprend où la première s'arrête.
    """
    if not is_animated(path):
        return 0.0
    return round(max(0.0, float(source_in) + float(local)), 4)


def problem(path: str | os.PathLike[str]) -> str | None:
    """``None`` si la scène se lit ; sinon une phrase courte (fichier introuvable, scène illisible, modèle manquant...)."""
    try:
        signature = scene_signature(path)
    except (OSError, ValueError):
        return "scène illisible."
    if signature == MISSING_SIGNATURE:
        return "fichier de scène introuvable."
    if signature.startswith(INVALID_PREFIX):
        try:
            validate(path)
        except Scene3DError as exc:
            text = str(exc)
            return text if len(text) <= 160 else text[:159] + "…"
        return "scène illisible."
    return None


# --- Rendu ------------------------------------------------------------------------------------------------------------------


def render_size(box: tuple[float, float], mode: str = MODE_EXACT) -> tuple[int, int]:
    """Taille de l'image rendue : la boîte du calque (pixels du projet), bornée par les limites du moteur ``kut3d``.

    Au-delà de ``MAX_IMAGE_SIDE`` par côté ou de ``MAX_SAMPLED_PIXELS`` au total, l'image est réduite à proportions égales (le
    rastériseur la ramène à la boîte). ``interactive`` : plus grand côté ≤ :data:`INTERACTIVE_SIDE`.
    """
    from kut3d.render.limits import MAX_IMAGE_SIDE, MAX_SAMPLED_PIXELS

    width = max(1.0, float(box[0]))
    height = max(1.0, float(box[1]))
    scale = 1.0
    if mode == MODE_INTERACTIVE:
        scale = min(scale, INTERACTIVE_SIDE / max(width, height))
    scale = min(scale, MAX_IMAGE_SIDE / max(width, height))
    pixels = width * height * RENDER_SAMPLES * RENDER_SAMPLES
    if pixels * scale * scale > MAX_SAMPLED_PIXELS:
        scale = min(scale, (MAX_SAMPLED_PIXELS / pixels) ** 0.5)
    out_w = max(1, min(MAX_IMAGE_SIDE, int(width * scale)))
    out_h = max(1, min(MAX_IMAGE_SIDE, int(height * scale)))
    while out_w * out_h * RENDER_SAMPLES * RENDER_SAMPLES > MAX_SAMPLED_PIXELS:   # l'arrondi ne doit jamais dépasser la borne
        out_w, out_h = max(1, out_w - 1), max(1, out_h - 1)
    return out_w, out_h


def render_identity(graphic: Any, box: tuple[float, float], mode: str = MODE_EXACT) -> tuple:
    """Tout ce qui change l'image d'un clip 3D : ``(instant, signature, taille, moteur, échantillons, version)``.

    Entre dans la clé d'image (``layer_key`` / ``content_key``) : un fichier modifié (le ``.kut3d``, un modèle, une texture, un
    ``.mtl``) renomme les images du cache disque, qui se renouvellent seules.
    """
    base = (
        float(getattr(graphic, "scene_time", 0.0)), scene_signature(graphic.source_path), render_size(box, mode),
        RENDER_BACKEND, RENDER_SAMPLES, SCENE3D_RASTER_VERSION,
    )
    # Mémoire du processus seulement, et posée APRÈS le choix du nom de fichier : elle ne protège pas un autre processus,
    # ni les images déjà nommées. Le fichier vide porte une marque « .unusable » (core.mograph_stream), qui, elle, suit le disque.
    return base + ("failed",) if base in _FAILED else base


def scene_image(graphic: Any, size: tuple[float, float] | None = None, mode: str = MODE_EXACT) -> Any:
    """Image (``QImage`` ARGB32 prémultiplié, fond transparent sauf ``background`` de la scène) de ``graphic`` à ``graphic.scene_time``.

    ``size`` : boîte du calque (défaut : ``graphic.width`` x ``graphic.height``). Lève :class:`Scene3DError` (scène illisible)
    ou ``kut3d.RenderError``. Les deux dernières images sont gardées : un calque dessiné plusieurs fois à un instant (flou de
    mouvement) ne rend la scène qu'une fois.
    """
    box = size if size is not None else (graphic.width, graphic.height)
    if mode not in (MODE_EXACT, MODE_INTERACTIVE):
        raise ValueError(f"Mode de rendu inconnu : {_short(mode)}.")
    identity = render_identity(graphic, box, mode)
    with _CACHE_LOCK:
        cached = _IMAGES.get(identity)
        if cached is not None:
            _IMAGES.move_to_end(identity)
            return cached
    import kut3d
    from PySide6.QtGui import QImage

    scene, _animated = load_scene(graphic.source_path)
    width, height = identity[2]
    frame = kut3d.render_frame(scene, t=identity[0], size=(width, height), backend=RENDER_BACKEND, samples=RENDER_SAMPLES)
    data = frame.color.tobytes()                  # RGBA 8 bits, alpha droit ; Qt ne possède pas ce tampon : copie ci-dessous
    wrapped = QImage(data, width, height, width * 4, QImage.Format.Format_RGBA8888)
    image = wrapped.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)   # nouvelle mémoire, indépendante de `data`
    del wrapped
    with _CACHE_LOCK:
        _IMAGES[identity] = image
        while len(_IMAGES) > _IMAGES_LIMIT:
            _IMAGES.popitem(last=False)
    return image


@contextmanager
def strict_rendering() -> Iterator[None]:
    """Dans ce bloc, :func:`draw_scene` **lève** :class:`Scene3DError` au lieu de laisser la boîte vide (export).

    Les aperçus (moniteur, segments d'aperçu fidèle, scopes) n'y entrent jamais : ils restent tolérants. Propre au fil
    appelant (``ContextVar``) : le dessin des images se fait dans le fil qui construit le graphe d'export.
    """
    token = _STRICT.set(True)
    try:
        yield
    finally:
        _STRICT.reset(token)


def render_failures() -> tuple[type[BaseException], ...]:
    """Les erreurs d'un rendu de scène (illisible, hors bornes, texture, disque, mémoire)."""
    from kut3d.errors import RenderError

    return (Scene3DError, RenderError, OSError, MemoryError)


def draw_scene(painter: Any, graphic: Any, width: float, height: float) -> None:
    """Dessine la scène de ``graphic`` dans la boîte ``(0, 0, width, height)`` (espace calque, transformation déjà posée).

    À l'écran, une scène qui ne se rend pas laisse la boîte vide et le dit dans le journal (l'aperçu ne lève jamais). Dans
    :func:`strict_rendering` (export) elle lève :class:`Scene3DError` : un fichier exporté ne perd jamais une scène en silence.
    """
    from PySide6.QtCore import QRectF

    try:
        image = scene_image(graphic, (width, height))
    except render_failures() as exc:
        name = _short(Path(graphic.source_path).name)
        if _STRICT.get():
            raise Scene3DError(f"Scène 3D {name} non rendue : {_short(str(exc), 160)}") from exc
        LOGGER.warning("Scène 3D non rendue (%s) : %s", name, _short(str(exc), 160))
        _TOLERATED.set(_TOLERATED.get() + 1)
        with _CACHE_LOCK:
            if len(_FAILED) >= _FAILED_LIMIT:
                _FAILED.clear()
            _FAILED.add(render_identity(graphic, (width, height))[:6])
        return
    painter.drawImage(QRectF(0, 0, width, height), image)


__all__ = [
    "INTERACTIVE_SIDE", "INVALID_PREFIX", "MISSING_SIGNATURE", "MODE_EXACT", "MODE_INTERACTIVE", "RENDER_BACKEND",
    "RENDER_SAMPLES", "SCENE3D_RASTER_VERSION", "SCENE_SUFFIX", "Scene3DError", "clear_signature_cache", "dependencies",
    "draw_scene", "ensure_fixed_speed", "has_scene_suffix", "is_animated", "is_scene3d_clip", "load_scene", "problem",
    "tolerated_failures",
    "render_failures", "render_identity", "render_size", "scene_image", "scene_signature", "scene_time_at", "strict_rendering",
    "validate",
]
