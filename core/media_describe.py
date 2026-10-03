"""Description lisible d'un média : la matière des infobulles et des avertissements, sans aucun libellé.

``describe_media`` rend des couples ``(clé, valeur affichable)`` ; l'interface traduit les **clés** (``"camera"``,
``"reel"``…) dans la langue de l'utilisateur. Aucun mot français ni anglais ici : seulement des valeurs (noms propres,
nombres, timecodes). Une valeur vide est omise, un média sans métadonnée donne donc une description courte.
"""

from __future__ import annotations

from pathlib import PureWindowsPath

from .project_model import MediaAsset
from .timecode import SECONDS_PER_DAY, format_fps

MEDIA_DESCRIPTION_KEYS: tuple[str, ...] = (
    "file", "resolution", "fps", "camera", "reel", "timecode", "time_reference", "creation_time",
)
"""Clés possibles, dans l'ordre d'affichage (l'interface y associe ses libellés traduits)."""


def describe_media(asset: MediaAsset, *, decimal: str = ".") -> tuple[tuple[str, str], ...]:
    """Couples ``(clé, valeur)`` décrivant ``asset``, dans l'ordre de :data:`MEDIA_DESCRIPTION_KEYS`.

    * ``file`` : nom du fichier source (le chemin peut venir d'une autre machine : ``/`` et ``\\`` séparent) ;
    * ``resolution`` : ``1920×1080`` (média sans image : omise) ;
    * ``fps`` : cadence **non tronquée** (``29.97``, ``23.976``, ``25``), ``decimal`` donne le séparateur décimal
      de la langue de l'interface (``","`` en français) ;
    * ``camera``, ``reel`` : lus dans le fichier ;
    * ``timecode`` : timecode SMPTE de la première image (``01:02:03;04`` en *drop-frame*) ;
    * ``time_reference`` : heure de début d'un fichier BWF, ``HH:MM:SS.mmm`` ;
    * ``creation_time`` : date de création ISO 8601 lue dans le fichier.
    """
    values = {
        "file": PureWindowsPath(asset.path).name if asset.path else "",
        "resolution": f"{asset.width}×{asset.height}" if asset.width > 0 and asset.height > 0 else "",
        "fps": format_fps(asset.fps, decimal) if asset.fps > 0 else "",
        "camera": asset.camera,
        "reel": asset.reel,
        "timecode": asset.timecode,
        "time_reference": _time_of_day(asset.time_reference),
        "creation_time": asset.creation_time,
    }
    return tuple((key, values[key]) for key in MEDIA_DESCRIPTION_KEYS if values[key])


def _time_of_day(seconds: float | None) -> str:
    """``HH:MM:SS.mmm`` d'un instant de la journée (secondes depuis minuit), ``""`` s'il n'y en a pas."""
    if seconds is None:
        return ""
    millis = int(seconds * 1000 + 0.5) % (SECONDS_PER_DAY * 1000)
    total_seconds, milli = divmod(millis, 1000)
    minutes, second = divmod(total_seconds, 60)
    hour, minute = divmod(minutes, 60)
    return f"{hour:02d}:{minute:02d}:{second:02d}.{milli:03d}"
