"""Clip « scène 3D » : lecture du fichier ``.kut3d`` qu'il référence, sans rien rendre (lot B2a, ADR-0009).

Un clip 3D est un calque graphique (:attr:`core.graphics.GraphicType.SCENE3D`) dont ``source_path`` désigne un fichier de
scène ``.kut3d`` (:mod:`kut3d.scene_io`). Ce module ne fait que trois choses, toutes en lecture :

- :func:`validate` : le fichier se lit-il (forme, bornes, modèles confinés sous son dossier) ? Appelé à la création du clip ;
- :func:`dependencies` : les fichiers dont dépend la scène (le ``.kut3d``, ses modèles, leurs textures) ;
- :func:`scene_signature` : une empreinte bon marché de ces fichiers, pour les clés de cache du rendu (B2b).

``import core.scene3d`` n'importe pas ``kut3d`` (ni numpy, ni Qt) : tout se charge à l'appel. Aucune de ces fonctions n'ouvre de
fenêtre ni n'appelle le moteur de rendu.
"""

from __future__ import annotations

import hashlib
import os
import threading
from collections import OrderedDict
from pathlib import Path

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


def clear_signature_cache() -> None:
    """Oublie les signatures mémorisées (tests)."""
    with _MEMO_LOCK:
        _MEMO.clear()


__all__ = [
    "INVALID_PREFIX", "MISSING_SIGNATURE", "SCENE_SUFFIX", "Scene3DError", "clear_signature_cache", "dependencies",
    "ensure_fixed_speed", "has_scene_suffix", "is_scene3d_clip", "scene_signature", "validate",
]
