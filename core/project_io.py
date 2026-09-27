"""Sauvegarde et chargement des projets Kut-Studio au format ``.kut``.

Le format est un fichier JSON UTF-8 lisible, versionné, indépendant de
tout framework graphique. Seule la bibliothèque standard Python est
utilisée : aucune dépendance PySide6, aucun pickle.

Structure du fichier (version 4) :

    {
        "format": "kut-studio-project",
        "version": 4,
        "project": {
            "name": "...",
            "width": 1920,
            "height": 1080,
            "fps": 30.0,
            "media_assets": [ ... ],
            "tracks": [ ... ]
        }
    }

La version 5 ajoute les marqueurs de timeline et les états de piste
``solo``, ``armed``, ``height_mode`` et ``collapsed``. La version 6
ajoute le mixage audio non destructif : ``volume_db`` / ``pan`` sur les
pistes, ``gain_db`` / ``pan`` / ``fade_in`` / ``fade_out`` sur les clips.
Les versions précédentes restent lisibles : ces champs prennent leurs
valeurs par défaut. La version 4 avait ajouté ``locked`` /
``visible`` / ``muted``.

L'écriture est atomique : le payload est d'abord écrit dans un fichier
temporaire placé dans le même dossier que la cible, puis déplacé via
``os.replace`` une fois le flush et ``fsync`` réussis. En cas d'erreur
en cours d'écriture, le fichier cible n'est jamais tronqué ni
remplacé par un contenu partiel.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .project_model import Clip, Marker, MediaAsset, Project, Track
from .time_remapping import FreezeFrameMode, TimeRemapping
from .visual_effects import ClipTransform, TransformKeyframe


# ---------------------------------------------------------------------------
# Constantes du format
# ---------------------------------------------------------------------------

FORMAT_NAME = "kut-studio-project"
"""Identifiant de format écrit à la racine de chaque fichier ``.kut``."""

CURRENT_VERSION = 7
"""Version courante du format. À incrémenter lors de changements incompatibles."""

SUPPORTED_VERSIONS: frozenset[int] = frozenset({1, 2, 3, 4, 5, 6, 7})
"""Ensemble des versions que cette version de Kut-Studio sait lire.

Les versions 1 à 6 restent prises en charge ; les champs spécifiques
(transform, keyframes, états de piste, mixage audio, remappage temporel) y sont
comblés par des valeurs par défaut conservatives.
"""

_VERSION_ADDED_AUDIO: int = 6
"""Première version sérialisant le mixage audio non destructif."""

_VERSION_ADDED_TIME_REMAPPING: int = 7
"""Première version sérialisant le remappage temporel (vitesse, reverse, freeze)."""

_FORMAT_KEY = "format"
_VERSION_KEY = "version"
_PROJECT_KEY = "project"

_PROJECT_FIELDS = frozenset({"name", "width", "height", "fps"})
_TRACK_FIELDS = frozenset(
    {
        "id",
        "name",
        "type",
        "locked",
        "visible",
        "muted",
        "solo",
        "armed",
        "height_mode",
        "collapsed",
        "volume_db",
        "pan",
    }
)
_CLIP_AUDIO_FIELDS = frozenset({"gain_db", "pan", "fade_in", "fade_out"})
_CLIP_TIME_REMAPPING_FIELDS = frozenset(
    {"speed", "reverse", "freeze_mode", "freeze_source_time", "freeze_duration"}
)
_CLIP_KNOWN_FIELDS = frozenset(
    {
        "id",
        "asset_id",
        "track_id",
        "timeline_start",
        "source_in",
        "source_out",
        "enabled",
        "label",
        "text",
    }
) | _CLIP_AUDIO_FIELDS | _CLIP_TIME_REMAPPING_FIELDS


# ---------------------------------------------------------------------------
# API publique
# ---------------------------------------------------------------------------


def project_payload(project: Project) -> dict[str, Any]:
    """Photographie sérialisable de ``project``.

    Le dictionnaire est détaché de l'objet vivant : un thread d'écriture
    peut le poser sur disque pendant que l'interface continue d'éditer.
    """
    return _build_payload(project)


def write_project_payload(payload: dict[str, Any], file_path: str) -> None:
    """Écrit un payload déjà construit, de façon atomique."""
    target = Path(file_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(payload, target)


def save_project(project: Project, file_path: str) -> None:
    """Sérialise un ``Project`` dans un fichier ``.kut`` JSON UTF-8.

    Le dossier parent est créé si besoin. L'écriture est atomique :
    un échec (disque plein, permissions, JSON non sérialisable...) ne
    laisse ni fichier cible tronqué, ni fichier temporaire résiduel.
    """
    write_project_payload(project_payload(project), file_path)


def load_project(file_path: str) -> Project:
    """Charge un fichier ``.kut`` et reconstruit le ``Project`` correspondant.

    Lève :
        FileNotFoundError : si le fichier n'existe pas.
        ValueError : si le JSON est invalide, si la racine n'est pas un
            objet, ou si le couple ``format`` / ``version`` n'est pas
            reconnu.
        TypeError / ValueError : si les dataclasses détectent des champs
            manquants, des types incohérents ou des valeurs invalides
            (durée négative, ``source_out <= source_in``...).
    """
    source = Path(file_path)
    try:
        raw = source.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise FileNotFoundError(
            f"Fichier projet Kut-Studio introuvable : {source}"
        ) from error

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError(
            f"Le fichier {source} n'est pas un JSON valide ({error.msg})."
        ) from error

    if not isinstance(data, dict):
        raise ValueError(
            f"Le fichier {source} doit contenir un objet JSON à la racine."
        )

    _validate_envelope(data, source)

    project_data = data.get(_PROJECT_KEY)
    if not isinstance(project_data, dict):
        raise ValueError(
            f"Section '{_PROJECT_KEY}' manquante ou invalide dans {source}."
        )

    return _deserialize_project(project_data)


# ---------------------------------------------------------------------------
# Sérialisation
# ---------------------------------------------------------------------------


def _build_payload(project: Project) -> dict[str, Any]:
    """Construit la structure JSON-sérialisable représentant un projet."""
    return {
        _FORMAT_KEY: FORMAT_NAME,
        _VERSION_KEY: CURRENT_VERSION,
        _PROJECT_KEY: {
            "name": project.name,
            "width": project.width,
            "height": project.height,
            "fps": project.fps,
            "media_assets": [
                {
                    "id": asset.id,
                    "path": asset.path,
                    "name": asset.name,
                    "duration": asset.duration,
                    "width": asset.width,
                    "height": asset.height,
                    "fps": asset.fps,
                    "media_type": asset.media_type,
                    "has_audio": asset.has_audio,
                }
                for asset in project.media_assets
            ],
            "markers": [
                {
                    "id": marker.id,
                    "time_seconds": marker.time_seconds,
                    "name": marker.name,
                    "category": marker.category,
                }
                for marker in project.markers
            ],
            "tracks": [
                {
                    "id": track.id,
                    "name": track.name,
                    "type": track.type,
                    "locked": track.locked,
                    "visible": track.visible,
                    "muted": track.muted,
                    "solo": track.solo,
                    "armed": track.armed,
                    "height_mode": track.height_mode,
                    "collapsed": track.collapsed,
                    "volume_db": float(track.volume_db),
                    "pan": float(track.pan),
                    "clips": [
                        {
                            "id": clip.id,
                            "asset_id": clip.asset_id,
                            "track_id": clip.track_id,
                            "timeline_start": clip.timeline_start,
                            "source_in": clip.source_in,
                            "source_out": clip.source_out,
                            "enabled": clip.enabled,
                            "label": clip.label,
                            "text": clip.text,
                            "gain_db": float(clip.gain_db),
                            "pan": float(clip.pan),
                            "fade_in": float(clip.fade_in),
                            "fade_out": float(clip.fade_out),
                            "transform": _transform_to_dict(clip.transform),
                            "transform_keyframes": [
                                _keyframe_to_dict(kf) for kf in clip.transform_keyframes
                            ],
                            "time_remapping": _time_remapping_to_dict(clip.time_remapping),
                        }
                        for clip in track.clips
                    ],
                }
                for track in project.tracks
            ],
        },
    }


# ---------------------------------------------------------------------------
# Désérialisation
# ---------------------------------------------------------------------------


def _validate_envelope(data: dict[str, Any], source: Path) -> None:
    """Vérifie que ``format`` et ``version`` correspondent au format attendu."""
    fmt = data.get(_FORMAT_KEY)
    if fmt != FORMAT_NAME:
        raise ValueError(
            f"Format de fichier Kut-Studio invalide dans {source} : "
            f"attendu '{FORMAT_NAME}', reçu '{fmt}'."
        )
    version = data.get(_VERSION_KEY)
    if version not in SUPPORTED_VERSIONS:
        raise ValueError(
            f"Version de fichier Kut-Studio non supportée dans {source} : "
            f"{version!r}. Versions acceptées : {sorted(SUPPORTED_VERSIONS)}."
        )


def _deserialize_project(data: dict[str, Any]) -> Project:
    """Reconstruit un ``Project`` à partir de la section ``project`` du JSON."""
    assets = [
        MediaAsset(**item)
        for item in data.get("media_assets", [])
    ]
    tracks = [
        _deserialize_track(item)
        for item in data.get("tracks", [])
    ]
    return Project(
        media_assets=assets,
        tracks=tracks,
        markers=[_deserialize_marker(item) for item in data.get("markers", [])],
        **{key: value for key, value in data.items() if key in _PROJECT_FIELDS},
    )


def _deserialize_marker(data: dict[str, Any]) -> Marker:
    """Reconstruit un marqueur. Une catégorie inconnue redevient standard."""
    return Marker(
        id=str(data["id"]),
        time_seconds=float(data.get("time_seconds", 0.0)),
        name=str(data.get("name", "")),
        category=str(data.get("category", "standard")),
    )


def _deserialize_track(data: dict[str, Any]) -> Track:
    """Reconstruit une ``Track`` (et ses ``Clip``) à partir d'un dict JSON."""
    clips = []
    for raw_clip in data.get("clips", []):
        clip_kwargs = {
            key: value
            for key, value in raw_clip.items()
            if key not in {"transform", "transform_keyframes", "time_remapping"}
            and key in _CLIP_KNOWN_FIELDS
        }
        clip_kwargs["transform"] = _dict_to_transform(
            raw_clip.get("transform")
        )
        clip_kwargs["transform_keyframes"] = [
            _dict_to_keyframe(raw) for raw in raw_clip.get("transform_keyframes", [])
        ]
        # Gérer le time_remapping (version 7+)
        if "time_remapping" in raw_clip:
            clip_kwargs["time_remapping"] = _dict_to_time_remapping(
                raw_clip.get("time_remapping")
            )
        else:
            # Version antérieure à 7 : utiliser les valeurs par défaut
            clip_kwargs["time_remapping"] = TimeRemapping()
        clips.append(Clip(**clip_kwargs))
    track_kwargs = {
        key: value for key, value in data.items() if key in _TRACK_FIELDS
    }
    # Une version antérieure à 6 ne connaît pas le mixage : on force les
    # valeurs neutres plutôt que de laisser un champ absent وغير défini.
    if "volume_db" not in track_kwargs:
        track_kwargs["volume_db"] = 0.0
    if "pan" not in track_kwargs:
        track_kwargs["pan"] = 0.0
    return Track(clips=clips, **track_kwargs)


def _transform_to_dict(transform: ClipTransform) -> dict[str, float]:
    return {
        "position_x": float(transform.position_x),
        "position_y": float(transform.position_y),
        "scale": float(transform.scale),
        "rotation": float(transform.rotation),
        "opacity": float(transform.opacity),
    }


def _dict_to_transform(raw: dict[str, Any] | None) -> ClipTransform:
    """Désérialise un :class:`ClipTransform` (défaut si absent / invalide)."""
    if not isinstance(raw, dict):
        return ClipTransform()
    return ClipTransform(
        position_x=float(raw.get("position_x", 0.0)),
        position_y=float(raw.get("position_y", 0.0)),
        scale=float(raw.get("scale", 1.0)),
        rotation=float(raw.get("rotation", 0.0)),
        opacity=float(raw.get("opacity", 1.0)),
    )


def _keyframe_to_dict(keyframe: TransformKeyframe) -> dict[str, Any]:
    return {
        "property_name": keyframe.property_name,
        "time_seconds": float(keyframe.time_seconds),
        "value": float(keyframe.value),
    }


def _dict_to_keyframe(raw: dict[str, Any]) -> TransformKeyframe:
    return TransformKeyframe(
        property_name=str(raw["property_name"]),
        time_seconds=float(raw["time_seconds"]),
        value=float(raw["value"]),
    )


def _time_remapping_to_dict(time_remapping: TimeRemapping) -> dict[str, Any]:
    """Sérialise un TimeRemapping en dict."""
    return {
        "speed": float(time_remapping.speed),
        "reverse": bool(time_remapping.reverse),
        "freeze_mode": str(time_remapping.freeze_mode.value),
        "freeze_source_time": float(time_remapping.freeze_source_time),
        "freeze_duration": float(time_remapping.freeze_duration),
    }


def _dict_to_time_remapping(raw: dict[str, Any] | None) -> TimeRemapping:
    """Désérialise un TimeRemapping à partir d'un dict.

    Retourne un TimeRemapping par défaut si le dict est None ou invalide.
    """
    if not isinstance(raw, dict):
        return TimeRemapping()

    try:
        return TimeRemapping(
            speed=float(raw.get("speed", 1.0)),
            reverse=bool(raw.get("reverse", False)),
            freeze_mode=FreezeFrameMode(raw.get("freeze_mode", "none")),
            freeze_source_time=float(raw.get("freeze_source_time", 0.0)),
            freeze_duration=float(raw.get("freeze_duration", 1.0)),
        )
    except (ValueError, TypeError, KeyError):
        # Si la désérialisation échoue, retourner les valeurs par défaut
        return TimeRemapping()


# ---------------------------------------------------------------------------
# Écriture atomique
# ---------------------------------------------------------------------------


def _atomic_write_json(payload: dict[str, Any], target: Path) -> None:
    """Écrit ``payload`` dans ``target`` de manière atomique.

    Le payload est d'abord écrit dans un fichier temporaire situé dans
    le même répertoire que la cible, puis flush + fsync sont effectués
    avant un ``os.replace`` final. Si une étape échoue, le fichier
    temporaire résiduel est nettoyé et la cible reste intacte.
    """
    fd, tmp_name = tempfile.mkstemp(
        prefix=f"{target.name}.",
        suffix=".tmp",
        dir=str(target.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            json.dump(payload, tmp_file, indent=2, ensure_ascii=False)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_path, target)
    except Exception:
        # Best-effort cleanup : on ne veut pas masquer l'erreur d'origine.
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise
