"""Sauvegarde et chargement des projets Kut-Studio au format ``.kut``.

Le format est un fichier JSON UTF-8 lisible, versionné, indépendant de
tout framework graphique. Seule la bibliothèque standard Python est
utilisée : aucune dépendance PySide6, aucun pickle.

Structure du fichier (version 1) :

    {
        "format": "kut-studio-project",
        "version": 1,
        "project": {
            "name": "...",
            "width": 1920,
            "height": 1080,
            "fps": 30.0,
            "media_assets": [ ... ],
            "tracks": [ ... ]
        }
    }

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

from .project_model import Clip, MediaAsset, Project, Track


# ---------------------------------------------------------------------------
# Constantes du format
# ---------------------------------------------------------------------------

FORMAT_NAME = "kut-studio-project"
"""Identifiant de format écrit à la racine de chaque fichier ``.kut``."""

CURRENT_VERSION = 1
"""Version courante du format. À incrémenter lors de changements incompatibles."""

SUPPORTED_VERSIONS: frozenset[int] = frozenset({1})
"""Ensemble des versions que cette version de Kut-Studio sait lire."""

_FORMAT_KEY = "format"
_VERSION_KEY = "version"
_PROJECT_KEY = "project"

_PROJECT_FIELDS = frozenset({"name", "width", "height", "fps"})
_TRACK_FIELDS = frozenset({"id", "name", "type"})


# ---------------------------------------------------------------------------
# API publique
# ---------------------------------------------------------------------------


def save_project(project: Project, file_path: str) -> None:
    """Sérialise un ``Project`` dans un fichier ``.kut`` JSON UTF-8.

    Le dossier parent est créé si besoin. L'écriture est atomique :
    un échec (disque plein, permissions, JSON non sérialisable...) ne
    laisse ni fichier cible tronqué, ni fichier temporaire résiduel.
    """
    payload = _build_payload(project)
    target = Path(file_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(payload, target)


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
                }
                for asset in project.media_assets
            ],
            "tracks": [
                {
                    "id": track.id,
                    "name": track.name,
                    "type": track.type,
                    "clips": [
                        {
                            "id": clip.id,
                            "asset_id": clip.asset_id,
                            "track_id": clip.track_id,
                            "timeline_start": clip.timeline_start,
                            "source_in": clip.source_in,
                            "source_out": clip.source_out,
                            "enabled": clip.enabled,
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
        **{key: value for key, value in data.items() if key in _PROJECT_FIELDS},
    )


def _deserialize_track(data: dict[str, Any]) -> Track:
    """Reconstruit une ``Track`` (et ses ``Clip``) à partir d'un dict JSON."""
    return Track(
        clips=[Clip(**item) for item in data.get("clips", [])],
        **{key: value for key, value in data.items() if key in _TRACK_FIELDS},
    )


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
