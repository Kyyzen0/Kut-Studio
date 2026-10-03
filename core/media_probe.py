"""Sonde générique ``ffprobe`` pour les médias Kut-Studio.

Ce module encapsule l'appel à ``ffprobe`` pour récupérer les
métadonnées d'un fichier média (vidéo, audio seul, vidéo sans piste
son…) et construire un :class:`~core.project_model.MediaAsset`
correctement configuré :

- un fichier contenant au moins un flux **vidéo** est rapporté comme
  ``media_type="video"`` avec sa largeur / hauteur / fps ;
- un fichier contenant uniquement des flux **audio** est rapporté
  comme ``media_type="audio"`` avec ``width=0``, ``height=0`` et
  ``fps=0.0`` ;
- le booléen ``has_audio`` est positionné selon la présence d'un flux
  audio exploitable (toujours ``True`` pour un asset ``audio``).
- tout autre cas (image fixe, sous-titres seuls, fichier corrompu)
  lève :class:`MediaProbeError`.

Les **métadonnées de tournage** (timecode, ``time_reference`` BWF, nom de bobine, modèle de caméra, date de création)
sont lues par :func:`extract_media_metadata` dans la même sortie JSON : jamais d'appel supplémentaire. Elles sont
informatives : une balise absente, mal formée ou d'un type inattendu donne la valeur par défaut, jamais une erreur.

Aucune dépendance à PySide6 : ``ffprobe`` est lancé par
:func:`core.process_supervisor.supervised_run` (bibliothèque standard
seulement), ce qui rend la sonde utilisable hors contexte Qt (tests,
scripts).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .process_supervisor import supervised_run
from .project_model import MediaAsset
from .timecode import SECONDS_PER_DAY, FrameRate, normalize_timecode_text
from .tool_paths import bundled_tool_path


class MediaProbeError(Exception):
    """Erreur levée par les sondes ``probe_media`` / ``probe_video``."""

    pass


# ---------------------------------------------------------------------------
# API publique
# ---------------------------------------------------------------------------


def probe_media(path: str) -> MediaAsset:
    """Analyse ``path`` via ``ffprobe`` et retourne un :class:`MediaAsset`.

    Le résultat distingue :

    - **vidéo** : au moins un flux vidéo exploitable, ``has_audio``
      reflète la présence d'un flux audio séparé ;
    - **audio seul** : uniquement des flux audio, ``width=0``,
      ``height=0``, ``fps=0.0`` et ``has_audio=True``.

    Raises:
        MediaProbeError: si le fichier est introuvable, si ``ffprobe``
            n'est pas disponible dans le ``PATH``, si ``ffprobe``
            renvoie un code de sortie non nul, si sa sortie JSON est
            invalide, ou si le média ne contient ni flux vidéo ni
            flux audio exploitable.
    """
    if not os.path.isfile(path):
        raise MediaProbeError(f"Fichier média introuvable : {path}")

    ffprobe_path = bundled_tool_path("ffprobe") or shutil.which("ffprobe")
    if ffprobe_path is None:
        raise MediaProbeError(
            "ffprobe est introuvable dans le PATH. "
            "Installez FFmpeg pour pouvoir importer des médias."
        )

    try:
        completed = supervised_run(
            [
                ffprobe_path,
                "-v",
                "error",
                "-show_format",
                "-show_streams",
                "-print_format",
                "json",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except subprocess.TimeoutExpired as exc:
        raise MediaProbeError(
            f"ffprobe a expiré en analysant {path} (>{exc.timeout}s)."
        ) from exc
    except OSError as exc:
        raise MediaProbeError(
            f"Impossible d'exécuter ffprobe sur {path} : {exc}"
        ) from exc

    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        message = stderr or f"ffprobe a échoué (code {completed.returncode})"
        raise MediaProbeError(f"{message} ({path}).")

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise MediaProbeError(
            f"Sortie ffprobe invalide pour {path} : {exc.msg}."
        ) from exc

    streams = payload.get("streams") or []
    format_data = payload.get("format") or {}
    duration = _parse_duration(format_data.get("duration"))

    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]

    metadata = extract_media_metadata(payload)
    if audio_streams and not video_streams:
        return _build_audio_asset(path, duration, metadata)
    if video_streams:
        return _build_video_asset(
            path, video_streams[0], duration, metadata, has_audio=bool(audio_streams)
        )
    raise MediaProbeError(
        f"Aucun flux vidéo ni audio exploitable détecté dans {path}."
    )


def probe_video(path: str) -> MediaAsset:
    """Compatibilité historique : sonde vidéo stricte.

    Délègue à :func:`probe_media` et ne retourne que les médias
    contenant un flux vidéo ; un fichier audio seul lève une
    :class:`MediaProbeError` explicite. Conservée pour les appels
    existants ; les nouveaux imports doivent utiliser
    :func:`probe_media`.
    """
    asset = probe_media(path)
    if asset.media_type != "video":
        raise MediaProbeError(
            f"Aucun flux vidéo détecté dans {path}."
        )
    return asset


# ---------------------------------------------------------------------------
# Construction des MediaAsset par type
# ---------------------------------------------------------------------------


def _build_video_asset(
    path: str,
    stream: dict,
    duration: float,
    metadata: MediaMetadata,
    *,
    has_audio: bool,
) -> MediaAsset:
    """Construit un :class:`MediaAsset` de type ``video``."""
    width = _safe_int(stream.get("width"))
    height = _safe_int(stream.get("height"))
    if width <= 0 or height <= 0:
        raise MediaProbeError(
            f"Dimensions vidéo invalides pour {path} ({width}x{height})."
        )

    fps = _parse_frame_rate(
        stream.get("avg_frame_rate") or stream.get("r_frame_rate") or ""
    )
    if fps <= 0.0:
        raise MediaProbeError(
            f"Fréquence d'images invalide pour {path} (fps={fps})."
        )

    stream_duration = _parse_duration(stream.get("duration"))
    if duration <= 0.0:
        duration = stream_duration
    if duration <= 0.0:
        raise MediaProbeError(
            f"Durée vidéo invalide pour {path} (durée={duration}s)."
        )

    return MediaAsset(
        id=f"asset-{uuid.uuid4().hex[:12]}",
        path=path,
        name=Path(path).name,
        duration=duration,
        width=width,
        height=height,
        fps=fps,
        media_type="video",
        has_audio=has_audio,
        **metadata.asset_fields(),
    )


def _build_audio_asset(path: str, duration: float, metadata: MediaMetadata) -> MediaAsset:
    """Construit un :class:`MediaAsset` de type ``audio``."""
    if duration <= 0.0:
        raise MediaProbeError(
            f"Durée audio invalide pour {path} (durée={duration}s)."
        )
    return MediaAsset(
        id=f"asset-{uuid.uuid4().hex[:12]}",
        path=path,
        name=Path(path).name,
        duration=duration,
        width=0,
        height=0,
        fps=0.0,
        media_type="audio",
        has_audio=True,
        **metadata.asset_fields(),
    )


# ---------------------------------------------------------------------------
# Métadonnées de tournage (timecode, BWF, bobine, caméra, création)
# ---------------------------------------------------------------------------

_MAX_TEXT = 120
"""Longueur maximale d'une balise texte conservée (une balise démesurée n'est pas une métadonnée utile)."""


@dataclass(frozen=True)
class MediaMetadata:
    """Métadonnées de tournage d'un fichier, telles que la sonde les lit (toutes optionnelles).

    Attributes:
        timecode: Timecode SMPTE de la première image sous sa forme canonique (``HH:MM:SS:FF``, ``;`` en
            *drop-frame*, voir :func:`core.timecode.normalize_timecode_text`), ``""`` s'il n'y en a pas.
        timecode_fps: Cadence de la piste timecode quand elle diffère de celle de l'image (vidéo en 59,94 dont le
            timecode compte à 29,97), ``0.0`` sinon.
        time_reference: Heure de début d'un fichier BWF, en secondes depuis minuit, ``None`` s'il n'y en a pas.
            Un ``time_reference`` nul est ignoré : c'est la valeur écrite par défaut par FFmpeg et la plupart des
            logiciels qui ne connaissent pas l'heure, et le prendre pour minuit décalerait l'enregistreur de plusieurs
            heures lors d'une synchronisation par timecode.
        reel: Nom de bobine (``reel_name``).
        camera: Modèle de caméra (``make`` et ``model`` réunis quand le modèle ne répète pas la marque).
        creation_time: Date de création ISO 8601 (``2026-03-14T09:26:53Z``), ``""`` si elle n'est pas lisible.
    """

    timecode: str = ""
    timecode_fps: float = 0.0
    time_reference: float | None = None
    reel: str = ""
    camera: str = ""
    creation_time: str = ""

    def asset_fields(self) -> dict[str, Any]:
        """Champs à passer à :class:`~core.project_model.MediaAsset`."""
        return {
            "timecode": self.timecode,
            "timecode_fps": self.timecode_fps,
            "time_reference": self.time_reference,
            "reel": self.reel,
            "camera": self.camera,
            "creation_time": self.creation_time,
        }


def extract_media_metadata(payload: Mapping[str, Any]) -> MediaMetadata:
    """Lit les métadonnées de tournage dans la sortie JSON de ``ffprobe -show_format -show_streams``.

    Les balises sont cherchées **sans tenir compte de la casse** (Matroska écrit ``TIMECODE``) dans les balises du
    conteneur, de chaque flux et de la piste de données ``tmcd`` de QuickTime / MP4 (qui porte aussi ``reel_name``).
    Ordre de priorité : flux vidéo, piste de données, conteneur, autres flux. Ne lève jamais : une entrée absente,
    mal formée ou d'un type inattendu donne la valeur par défaut.
    """
    format_data = payload.get("format") if isinstance(payload, Mapping) else None
    raw_streams = payload.get("streams") if isinstance(payload, Mapping) else None
    streams = [item for item in raw_streams if isinstance(item, Mapping)] if isinstance(raw_streams, list) else []
    videos = [item for item in streams if item.get("codec_type") == "video"]
    data = [item for item in streams if item.get("codec_type") == "data"]
    others = [item for item in streams if item.get("codec_type") not in ("video", "data")]
    container = _tags(format_data)
    ordered = [_tags(item) for item in videos + data] + [container] + [_tags(item) for item in others]

    timecode = _first(ordered, ("timecode",), normalize_timecode_text)
    return MediaMetadata(
        timecode=timecode,
        timecode_fps=_timecode_rate(videos, data) if timecode else 0.0,
        time_reference=_time_reference(container, streams),
        reel=_first(ordered, ("reel_name", "com.apple.quicktime.reelname"), _clean_text),
        camera=_camera([container] + [_tags(item) for item in streams]),
        creation_time=_first(
            [container] + [_tags(item) for item in videos + others + data],
            ("creation_time",),
            lambda value: _creation_time(value, container.get("date", "")),
        ),
    )


def _tags(node: object) -> dict[str, str]:
    """Balises d'un objet ffprobe (format ou flux) : clés en minuscules, valeurs texte uniquement."""
    tags = node.get("tags") if isinstance(node, Mapping) else None
    if not isinstance(tags, Mapping):
        return {}
    return {key.lower(): value for key, value in tags.items() if isinstance(key, str) and isinstance(value, str)}


def _first(tag_sets: list[dict[str, str]], keys: tuple[str, ...], clean: Callable[[str], str | None]) -> str:
    """Première valeur valide de l'une des ``keys`` dans ``tag_sets`` (par ordre de priorité), ``""`` sinon."""
    for tags in tag_sets:
        for key in keys:
            if key in tags:
                cleaned = clean(tags[key])
                if cleaned:
                    return cleaned
    return ""


def _clean_text(value: str) -> str:
    """Texte d'une balise : caractères imprimables, espaces compactés, longueur bornée."""
    text = " ".join("".join(ch for ch in value if ch.isprintable()).split())
    return text[:_MAX_TEXT]


def _camera(tag_sets: list[dict[str, str]]) -> str:
    """Modèle de caméra : ``model`` (ou ``com.apple.quicktime.model``), précédé de la marque si elle n'y figure pas."""
    model = _first(tag_sets, ("model", "com.apple.quicktime.model"), _clean_text)
    make = _first(tag_sets, ("make", "com.apple.quicktime.make"), _clean_text)
    if not model:
        return make
    if make and not model.lower().startswith(make.lower()):
        return f"{make} {model}"[:_MAX_TEXT]
    return model


_ISO_TIME = re.compile(
    r"(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:?\d{2})?", re.ASCII
)
_BWF_DATE = re.compile(r"(\d{4})[-:/](\d{2})[-:/](\d{2})", re.ASCII)
_BWF_TIME = re.compile(r"\d{2}:\d{2}:\d{2}", re.ASCII)


def _creation_time(value: str, date: str) -> str:
    """Date de création ISO 8601 lisible, ``""`` sinon.

    ``ffprobe`` duplique parfois la valeur du conteneur (``a;a``) : la première est gardée. Une partie fractionnaire
    nulle (``.000000``) est retirée. Une heure seule (``08:15:30``, champ BWF ``origination time``) n'est gardée que si
    la balise ``date`` du fichier la complète.
    """
    text = value.split(";")[0].strip()
    match = _ISO_TIME.fullmatch(text)
    if match is not None:
        day, clock, fraction, zone = match.groups()
        stamp = f"{day}T{clock}"
        if fraction and fraction.strip("0"):
            stamp += f".{fraction}"
        return stamp + (zone or "")
    if _BWF_TIME.fullmatch(text):
        day_match = _BWF_DATE.fullmatch(date.strip())
        if day_match is not None:
            return f"{'-'.join(day_match.groups())}T{text}"
    return ""


def _time_reference(container: dict[str, str], streams: list[Mapping[str, Any]]) -> float | None:
    """``time_reference`` BWF (échantillons depuis minuit) converti en secondes avec la fréquence d'échantillonnage."""
    raw = _first([container] + [_tags(item) for item in streams], ("time_reference",), lambda value: value.strip())
    if not (raw.isascii() and raw.isdigit()):
        return None
    sample_rate = next(
        (
            _safe_int(item.get("sample_rate"))
            for item in streams
            if item.get("codec_type") == "audio" and _safe_int(item.get("sample_rate")) > 0
        ),
        0,
    )
    if sample_rate <= 0:
        return None
    seconds = int(raw) / sample_rate
    return seconds if 0.0 < seconds < SECONDS_PER_DAY else None


def _timecode_rate(videos: list[Mapping[str, Any]], data: list[Mapping[str, Any]]) -> float:
    """Cadence de la piste timecode si elle diffère de celle de l'image (sinon ``0.0``).

    Une vidéo en 59,94 i/s peut porter un timecode à 29,97 : lue à 59,94, l'étiquette serait fausse de plusieurs
    dizaines de millisecondes par image. La piste ``tmcd`` donne la vraie cadence.
    """
    track = next(
        (
            item
            for item in data
            if item.get("codec_tag_string") == "tmcd" or "timecode" in _tags(item)
        ),
        None,
    )
    if track is None:
        return 0.0
    track_rate = FrameRate.try_from_fps(_parse_frame_rate(track.get("avg_frame_rate") or track.get("r_frame_rate")))
    if track_rate is None:
        return 0.0
    video: Mapping[str, Any] = videos[0] if videos else {}
    video_rate = FrameRate.try_from_fps(
        _parse_frame_rate(video.get("avg_frame_rate") or video.get("r_frame_rate") or "")
    )
    if video_rate is not None and video_rate.fps == track_rate.fps:
        return 0.0
    return float(track_rate.fps)


# ---------------------------------------------------------------------------
# Helpers internes
# ---------------------------------------------------------------------------


def _safe_int(value) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _parse_frame_rate(value) -> float:
    """Convertit un rationnel ffprobe ``"N/D"`` en fps ``float``."""
    if not value or not isinstance(value, str):
        return 0.0
    try:
        numerator, denominator = value.split("/", 1)
        num = float(numerator)
        den = float(denominator)
    except (ValueError, ZeroDivisionError):
        return 0.0
    if den == 0:
        return 0.0
    return num / den


def _parse_duration(value) -> float:
    """Convertit une durée ffprobe en secondes ``float``."""
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
