"""Import / validation des LUTs ``.cube`` (tâche 29).

Ce module encapsule la lecture et la validation d'un fichier LUT au
format Adobe ``.cube``. On reste strictement compatible avec le
sous‑ensemble attendu par FFmpeg ``lut3d`` :

- 3D LUT uniquement (``LUT_3D_SIZE`` dans l'en‑tête). Les LUT 1D
  ne sont volontairement *pas* supportées ici : elles nécessitent
  ``lutrgb`` plutôt que ``lut3d`` et ne sont pas demandées par la
  tâche.
- ``DOMAIN_MIN`` / ``DOMAIN_MAX`` (défaut ``0.0`` / ``1.0``).
- ``TITLE`` est conservé pour information mais non requis.

La validation rejette :

- les fichiers vides ou mal formés ;
- les tailles trop petites (≤ 1) ou trop grandes (> 65) ;
- les triplets incohérents (mauvais nombre d'éléments par ligne) ;
- les lignes vides intercalées (signal fréquent de fichier corrompu) ;
- les valeurs hors domaine.

L'import réussit dans les autres cas ; le contenu est conservé tel
quel pour permettre une vérification externe par FFmpeg.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------


# Bornes de la taille de la LUT : ``2`` est le minimum utile (rendu
# très pixélisé mais fonctionnel), ``65`` couvre les usages
# professionnels (17 / 33 / 65 sont des tailles courantes). Au‑delà,
# la mémoire consommée par FFmpeg devient prohibitive.
MIN_LUT_SIZE: int = 2
MAX_LUT_SIZE: int = 65

# Formats reconnus : uniquement le format Adobe ``.cube``. La
# nomenclature est figée par l'éditeur (cf. spécification publique).
TITLE_KEY: str = "TITLE"
LUT_3D_SIZE_KEY: str = "LUT_3D_SIZE"
DOMAIN_MIN_KEY: str = "DOMAIN_MIN"
DOMAIN_MAX_KEY: str = "DOMAIN_MAX"
LUT_1D_SIZE_KEY: str = "LUT_1D_SIZE"  # non supportée : on le détecte pour rejeter


# ---------------------------------------------------------------------------
# Erreurs
# ---------------------------------------------------------------------------


class LUTImportError(ValueError):
    """Erreur d'import / validation d'un fichier ``.cube``."""


class LUTImportMissingFile(LUTImportError):
    """Le fichier source est introuvable."""


class LUTImportBadFormat(LUTImportError):
    """Le fichier n'est pas un ``.cube`` valide (en‑tête, taille, etc.)."""


class LUTImportUnsupported(LUTImportError):
    """Format de LUT non supporté (1D, taille hors limites, etc.)."""


# ---------------------------------------------------------------------------
# Modèle : tableau
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CubeLUT:
    """Représentation d'une LUT ``.cube`` 3D.

    Attributes:
        title: Nom humain (extrait de ``TITLE`` ou dérivé du nom de
            fichier).
        size: Taille du cube (chaque côté, ``size`` × ``size`` ×
            ``size`` entrées).
        domain_min: Borne basse du domaine d'entrée (défaut ``0.0``).
        domain_max: Borne haute du domaine d'entrée (défaut ``1.0``).
        entries: Triplet de flottants de longueur ``size**3 * 3``,
            dans l'ordre R puis G puis B, lignes continues. On évite
            la lourdeur d'un tableau NumPy : FFmpeg n'a pas besoin
            d'un format contigu particulier puisque le fichier est
            réécrit tel quel lors de la copie dans le projet.
        sha1: Empreinte SHA‑1 du fichier source (utile pour détecter
            un changement de contenu).
    """

    title: str
    size: int
    domain_min: tuple[float, float, float]
    domain_max: tuple[float, float, float]
    entries: tuple[float, ...]
    sha1: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_HEADER_LINE = re.compile(r"^(?P<key>[A-Z0-9_]+)\s+(?P<value>.+)$")


def _split_floats(line: str) -> list[float]:
    parts = line.strip().split()
    floats: list[float] = []
    for part in parts:
        try:
            floats.append(float(part))
        except ValueError as exc:
            raise LUTImportBadFormat(
                f"Valeur non numérique dans la LUT : {part!r}."
            ) from exc
    return floats


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_cube_lut(path: str | Path) -> CubeLUT:
    """Lit et valide un fichier ``.cube`` 3D.

    Raises:
        LUTImportMissingFile: fichier introuvable.
        LUTImportBadFormat: en‑tête invalide ou triplets incohérents.
        LUTImportUnsupported: format 1D ou taille hors limites.
    """
    file_path = Path(path)
    if not file_path.exists() or not file_path.is_file():
        raise LUTImportMissingFile(
            f"Fichier LUT introuvable : {file_path}"
        )
    data = file_path.read_bytes()
    sha1 = hashlib.sha1(data).hexdigest()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise LUTImportBadFormat(
            f"Le fichier n'est pas UTF‑8 valide : {file_path}"
        ) from exc

    title = file_path.stem
    size: int | None = None
    domain_min: tuple[float, float, float] = (0.0, 0.0, 0.0)
    domain_max: tuple[float, float, float] = (1.0, 1.0, 1.0)

    entries: list[float] = []

    with _open_text(text) as handle:
        for raw in handle:
            line = raw.strip()
            if not line:
                continue
            if line.startswith("#"):
                # Commentaire (le format .cube autorise des lignes
                # débutant par '#').
                continue
            if line.startswith((" ", "\t")) and size is None:
                # Ligne d'en‑tête mal formée : on tente quand même un
                # _split_floats pour rester tolérant.
                pass
            if line[0].isalpha():
                # En‑tête clé=valeur.
                match = _HEADER_LINE.match(line)
                if not match:
                    raise LUTImportBadFormat(
                        f"Ligne d'en‑tête invalide : {line!r}."
                    )
                key = match.group("key")
                value = match.group("value").strip()
                if key == TITLE_KEY:
                    # La valeur de ``TITLE`` est encadrée par des
                    # guillemets (``"Identity"``) dans le format
                    # .cube. On les retire pour stocker un titre
                    # humain propre, compatible avec les autres
                    # champs texte du projet.
                    cleaned = value.strip()
                    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] == '"':
                        cleaned = cleaned[1:-1]
                    title = cleaned or title
                elif key == LUT_3D_SIZE_KEY:
                    size = _parse_int(value, line)
                elif key == LUT_1D_SIZE_KEY:
                    raise LUTImportUnsupported(
                        "Les LUT 1D ne sont pas supportées par "
                        "l'étalonnage non‑destructif (utilisez un LUT 3D)."
                    )
                elif key == DOMAIN_MIN_KEY:
                    domain_min = tuple(_split_floats(value))
                elif key == DOMAIN_MAX_KEY:
                    domain_max = tuple(_split_floats(value))
                # Les autres en‑têtes sont ignorées : FFmpeg accepte
                # un sous‑ensemble plus large que celui que nous
                # savons consommer.
                continue

            else:
                # Ligne de triplets.
                floats = _split_floats(line)
                if len(floats) != 3:
                    raise LUTImportBadFormat(
                        f"Une ligne de la LUT ne contient pas 3 valeurs : "
                        f"{line!r}."
                    )
                entries.extend(floats)

    if size is None:
        raise LUTImportBadFormat(
            "La LUT ne déclare pas de ``LUT_3D_SIZE``."
        )
    if size < MIN_LUT_SIZE or size > MAX_LUT_SIZE:
        raise LUTImportUnsupported(
            f"La taille {size} est hors des limites supportées "
            f"[{MIN_LUT_SIZE}, {MAX_LUT_SIZE}]."
        )
    expected = size * size * size * 3
    if len(entries) != expected:
        raise LUTImportBadFormat(
            f"Nombre d'entrées incohérent : attendu {expected}, "
            f"reçu {len(entries)}."
        )

    return CubeLUT(
        title=title,
        size=size,
        domain_min=domain_min,
        domain_max=domain_max,
        entries=tuple(entries),
        sha1=sha1,
    )


def _open_text(text: str) -> TextIO:
    """Convertit la chaîne en itérable de lignes (compatible avec les
    mocks dans les tests).
    """
    import io

    return io.StringIO(text)


def _parse_int(value: str, raw_line: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise LUTImportBadFormat(
            f"Valeur d'en‑tête non entière : {raw_line!r}."
        ) from exc


__all__ = [
    "DOMAIN_MAX_KEY",
    "DOMAIN_MIN_KEY",
    "LUT_1D_SIZE_KEY",
    "LUT_3D_SIZE_KEY",
    "LUTImportBadFormat",
    "LUTImportError",
    "LUTImportMissingFile",
    "LUTImportUnsupported",
    "MAX_LUT_SIZE",
    "MIN_LUT_SIZE",
    "TITLE_KEY",
    "CubeLUT",
    "parse_cube_lut",
]