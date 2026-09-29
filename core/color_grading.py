"""Étalonnage couleur non destructif et grades prêts à l'emploi (tâche 29).

Ce module définit toute la logique métier de l'étalonnage couleur,
séparée des effets visuels (cf. :mod:`core.effects_model`) pour
permettre une palette de réglages dédiée : exposition, contraste,
saturation, température, teinte, ombres, hautes lumières, courbes
par canal et LUT ``.cube``.

Architecture :

- :class:`ColorCurve` — une courbe de transfert à 16 points pour un
  canal ``"master"`` / ``"red"`` / ``"green"`` / ``"blue"`` ;
- :class:`ColorCurves` — collection ordonnée des 4 courbes (master +
  R/V/B) ;
- :class:`LUTResource` — référence à un fichier ``.cube`` (chemin,
  nom, hash SHA‑1) ;
- :class:`ColorGrade` — état complet de l'étalonnage appliqué à un
  clip (les paramètres simples + les courbes + le LUT optionnel) ;
- :class:`ColorPreset` — preset exportable / partageable
  (``name`` + ``category`` + ``ColorGrade`` + flag ``builtin``) ;
- :class:`ColorPresetCategory` — taxonomie stable pour la
  bibliothèque (Cinéma / Teal & Orange / Chaud / Froid / N&B
  contrasté / Vintage) ;
- :class:`ColorPresetStore` — gestion en mémoire des favoris /
  presets utilisateur, persistance JSON analogue aux transitions.

Aucune dépendance à ``PySide6`` ni à FFmpeg : les filtres sont
émis par :mod:`core.export_engine`. Le format JSON de persistance
est partagé avec les autres modules (file ``color_presets.json``
distinct des fichiers transitions / audio_effects).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Iterable, Sequence


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------


# Bornes des paramètres simples. On s'aligne sur l'usage de logiciels
# de référence (DaVinci, Premiere) : exposition en stops, saturation
# en pourcentage, température / teinte en delta.
EXPOSURE_MIN: float = -2.0
EXPOSURE_MAX: float = 2.0
CONTRAST_MIN: float = -1.0
CONTRAST_MAX: float = 1.0
SATURATION_MIN: float = 0.0
SATURATION_MAX: float = 2.0
TEMPERATURE_MIN: float = -100.0
TEMPERATURE_MAX: float = 100.0
HUE_MIN: float = -180.0
HUE_MAX: float = 180.0
SHADOWS_MIN: float = -1.0
SHADOWS_MAX: float = 1.0
HIGHLIGHTS_MIN: float = -1.0
HIGHLIGHTS_MAX: float = 1.0

# Bornes d'un point de courbe : valeur du transfert (0-1) pour une
# abscisse (0-1) donnée. La résolution native est 16 points (101
# valeurs sont interpolées par FFmpeg ``curves``).
MIN_CURVE_POINTS: int = 2
MAX_CURVE_POINTS: int = 16
MIN_CURVE_VALUE: float = 0.0
MAX_CURVE_VALUE: float = 1.0


CHANNELS: tuple[str, ...] = ("master", "red", "green", "blue")
"""Canaux reconnus par :class:`ColorCurves`."""


# Longueur max d'un nom (preset ou projet utilisateur).
MAX_NAME_LENGTH: int = 64


_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:\-]+$")


# ---------------------------------------------------------------------------
# Erreurs
# ---------------------------------------------------------------------------


class ColorGradingError(ValueError):
    """Erreur métier de l'étalonnage couleur."""


class ColorGradingRangeError(ColorGradingError):
    """Une valeur numérique est hors bornes."""


class ColorGradingNameError(ColorGradingError):
    """Le nom / identifiant fourni est vide ou trop long."""


class ColorGradingUnknownChannelError(ColorGradingError):
    """Le canal de courbe demandé n'existe pas."""


class ColorGradingCycleError(ColorGradingError):
    """Une dépendance cyclique est détectée (présets qui se référencent)."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _new_id(prefix: str) -> str:
    """Génère un identifiant court, lisible et unique."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _require_finite(value: float, name: str) -> float:
    """Convertit en flottant fini ou lève :class:`ColorGradingRangeError`."""
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ColorGradingRangeError(
            f"Valeur non numérique pour {name!r} : {value!r}."
        ) from exc
    if number != number or number in (float("inf"), float("-inf")):
        raise ColorGradingRangeError(
            f"Valeur non finie pour {name!r} : {number!r}."
        )
    return number


def _coerce_range(
    value: float, name: str, *, minimum: float, maximum: float
) -> float:
    number = _require_finite(value, name)
    if number < minimum or number > maximum:
        raise ColorGradingRangeError(
            f"Valeur {name!r} hors bornes [{minimum}, {maximum}] : {number}."
        )
    return number


def _require_id(value: str, kind: str) -> str:
    if not isinstance(value, str) or not value:
        raise ColorGradingNameError(
            f"L'identifiant du {kind} ne peut pas être vide."
        )
    if not _ID_PATTERN.match(value):
        raise ColorGradingNameError(
            f"Identifiant de {kind} invalide : {value!r}."
        )
    return value


# ---------------------------------------------------------------------------
# Courbes de transfert
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ColorCurve:
    """Une courbe de transfert à ``n`` points ``(input, output)``.

    Les abscisses et ordonnées sont normalisées dans ``[0, 1]``. Une
    courbe par défaut est la diagonale (``f(x) = x``) : la rendre
    inactive est aussi simple que de réinitialiser les points.

    Les points sont stockés sous la forme d'une liste de paires
    ``(input, output)``. On impose un nombre minimal et un nombre
    maximal pour rester compatible avec la sortie ``curves`` de
    FFmpeg (interpolation linéaire entre points).
    """

    points: tuple[tuple[float, float], ...] = field(
        default_factory=lambda: (
            (0.0, 0.0),
            (0.06666666666666667, 0.06666666666666667),
            (0.13333333333333333, 0.13333333333333333),
            (0.2, 0.2),
            (0.26666666666666666, 0.26666666666666666),
            (0.3333333333333333, 0.3333333333333333),
            (0.4, 0.4),
            (0.4666666666666667, 0.4666666666666667),
            (0.5333333333333333, 0.5333333333333333),
            (0.6, 0.6),
            (0.6666666666666666, 0.6666666666666666),
            (0.7333333333333333, 0.7333333333333333),
            (0.8, 0.8),
            (0.8666666666666667, 0.8666666666666667),
            (0.9333333333333333, 0.9333333333333333),
            (1.0, 1.0),
        )
    )

    def __post_init__(self) -> None:
        # Normalisation : on sérialise en tuple de tuples pour respecter
        # l'immutabilité.
        normalized: list[tuple[float, float]] = []
        for item in self.points:
            if not isinstance(item, (tuple, list)) or len(item) != 2:
                raise ColorGradingRangeError(
                    f"Point de courbe invalide : {item!r}."
                )
            x = _coerce_range(
                float(item[0]), "curve input",
                minimum=MIN_CURVE_VALUE, maximum=MAX_CURVE_VALUE,
            )
            y = _coerce_range(
                float(item[1]), "curve output",
                minimum=MIN_CURVE_VALUE, maximum=MAX_CURVE_VALUE,
            )
            normalized.append((x, y))
        if len(normalized) < MIN_CURVE_POINTS:
            raise ColorGradingRangeError(
                f"Une courbe nécessite au moins {MIN_CURVE_POINTS} points."
            )
        if len(normalized) > MAX_CURVE_POINTS:
            raise ColorGradingRangeError(
                f"Une courbe accepte au plus {MAX_CURVE_POINTS} points."
            )
        # On s'assure que les abscisses sont strictement croissantes :
        # sinon l'interpolation linéaire devient ambiguë.
        for previous, current in zip(normalized, normalized[1:]):
            if current[0] <= previous[0]:
                raise ColorGradingRangeError(
                    f"Abscisses non croissantes : "
                    f"{previous[0]} >= {current[0]}."
                )
        object.__setattr__(self, "points", tuple(normalized))

    @classmethod
    def identity(cls) -> "ColorCurve":
        """Construit la courbe identité (``f(x) = x``) à 16 points."""
        return cls()

    def is_identity(self, *, tolerance: float = 1e-6) -> bool:
        """``True`` si la courbe est numériquement égale à l'identité."""
        if len(self.points) != 16:
            return False
        for index, (x, y) in enumerate(self.points):
            expected = index / (len(self.points) - 1)
            if abs(x - expected) > tolerance or abs(y - expected) > tolerance:
                return False
        return True


@dataclass(frozen=True)
class ColorCurves:
    """Les 4 courbes d'un étalonnage : master + R / V / B.

    Une :class:`ColorCurves` est immuable. Pour modifier un point, on
    reconstruit l'instance avec :meth:`with_point` ou :meth:`reset`.
    """

    master: ColorCurve = field(default_factory=ColorCurve.identity)
    red: ColorCurve = field(default_factory=ColorCurve.identity)
    green: ColorCurve = field(default_factory=ColorCurve.identity)
    blue: ColorCurve = field(default_factory=ColorCurve.identity)

    def curve(self, channel: str) -> ColorCurve:
        if channel not in CHANNELS:
            raise ColorGradingUnknownChannelError(
                f"Canal inconnu : {channel!r}. Attendu : {CHANNELS}."
            )
        return getattr(self, channel)

    def with_point(
        self, channel: str, index: int, output: float,
    ) -> "ColorCurves":
        """Retourne une nouvelle instance avec un point modifié.

        ``index`` est la position dans ``curve.points`` ; on remplace
        la sortie ``output`` (l'abscisse reste inchangée). La
        courbe doit rester croissante ; on valide l'entrée.
        """
        current = self.curve(channel)
        new_output = _coerce_range(
            float(output), "curve output",
            minimum=MIN_CURVE_VALUE, maximum=MAX_CURVE_VALUE,
        )
        if not (0 <= index < len(current.points)):
            raise ColorGradingRangeError(
                f"Index de point invalide : {index}."
            )
        new_points = list(current.points)
        new_points[index] = (new_points[index][0], new_output)
        new_curve = ColorCurve(points=tuple(new_points))
        return self._replace(channel, new_curve)

    def _replace(self, channel: str, curve: ColorCurve) -> "ColorCurves":
        if channel == "master":
            return ColorCurves(master=curve, red=self.red,
                               green=self.green, blue=self.blue)
        if channel == "red":
            return ColorCurves(master=self.master, red=curve,
                               green=self.green, blue=self.blue)
        if channel == "green":
            return ColorCurves(master=self.master, red=self.red,
                               green=curve, blue=self.blue)
        if channel == "blue":
            return ColorCurves(master=self.master, red=self.red,
                               green=self.green, blue=curve)
        raise ColorGradingUnknownChannelError(channel)

    def is_identity(self, *, tolerance: float = 1e-6) -> bool:
        return all(
            self.curve(c).is_identity(tolerance=tolerance) for c in CHANNELS
        )


# ---------------------------------------------------------------------------
# LUT
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LUTResource:
    """Référence à un fichier ``.cube`` importé dans le projet.

    Le champ ``path`` est un chemin relatif au projet : on stocke
    uniquement le nom de fichier et un hash SHA‑1 du contenu. Le
    fichier lui-même est copié dans le dossier ``luts/`` du projet
    lors de la sauvegarde (cf. :func:`copy_lut_into_project`). Si le
    fichier est absent à l'ouverture, :attr:`missing` est mis à
    ``True`` et le preset continue d'apparaître dans la bibliothèque
    avec un avertissement visible.

    Attributes:
        path: Chemin relatif (par rapport au dossier du projet) du
            fichier ``.cube``.
        title: Nom humain (par défaut, le nom de fichier sans
            extension).
        sha1: Empreinte SHA‑1 du contenu (utilisée pour détecter un
            changement accidentel et pour la rétro-compatibilité).
        size: Taille du fichier en octets.
        missing: ``True`` si le fichier source est introuvable.
    """

    path: str
    title: str
    sha1: str
    size: int = 0
    missing: bool = False
    # Chemin absolu de travail, volontairement non sérialisé. Il permet
    # de retrouver le fichier source lors d'un « Enregistrer sous… » alors
    # que ``path`` reste portable et relatif au projet.
    source_path: str | None = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.path, str) or not self.path:
            raise ColorGradingError("Le chemin du LUT ne peut pas être vide.")
        if not isinstance(self.title, str) or not self.title.strip():
            raise ColorGradingError("Le titre du LUT ne peut pas être vide.")
        if len(self.title) > MAX_NAME_LENGTH:
            raise ColorGradingError(
                f"Le titre du LUT est trop long "
                f"({len(self.title)} > {MAX_NAME_LENGTH})."
            )
        if not isinstance(self.sha1, str) or len(self.sha1) != 40:
            raise ColorGradingError(
                f"Empreinte SHA‑1 invalide : {self.sha1!r}."
            )
        if int(self.size) < 0:
            raise ColorGradingError("La taille du LUT ne peut pas être négative.")

    @classmethod
    def from_path(
        cls, path: str | os.PathLike[str], *, title: str | None = None,
        project_root: str | os.PathLike[str] | None = None,
    ) -> "LUTResource":
        """Construit une :class:`LUTResource` à partir d'un chemin absolu.

        Le ``path`` stocké est **relatif** à ``project_root`` si
        fourni, sinon identique à l'absolu. Le SHA‑1 est calculé en
        lisant le fichier. Le fichier doit exister au moment de
        l'appel (sinon :class:`ColorGradingError`).
        """
        file_path = Path(path)
        if not file_path.exists() or not file_path.is_file():
            raise ColorGradingError(
                f"Fichier LUT introuvable : {file_path}"
            )
        data = file_path.read_bytes()
        digest = hashlib.sha1(data).hexdigest()
        if project_root is not None:
            try:
                relative = file_path.resolve().relative_to(
                    Path(project_root).resolve()
                )
                stored_path = str(relative).replace(os.sep, "/")
            except ValueError:
                stored_path = str(file_path.resolve())
        else:
            stored_path = str(file_path.resolve())
        return cls(
            path=stored_path,
            title=title or file_path.stem,
            sha1=digest,
            size=len(data),
            missing=False,
            source_path=str(file_path.resolve()),
        )


def copy_lut_into_project(
    lut: LUTResource,
    project_root: str | os.PathLike[str],
) -> LUTResource:
    """Copie un LUT dans ``luts/`` et retourne une référence portable.

    Le nom inclut le début du hash afin d'éviter qu'un autre LUT portant le
    même nom écrase silencieusement le premier. Une référence déjà copiée est
    réutilisée. Si la source est absente, aucune copie factice n'est créée et
    la ressource retournée est marquée manquante.
    """
    root = Path(project_root).resolve()
    candidates: list[Path] = []
    if lut.source_path:
        candidates.append(Path(lut.source_path))
    stored = Path(lut.path)
    if stored.is_absolute():
        candidates.append(stored)
    else:
        candidates.append(root / stored)
    source = next((path for path in candidates if path.is_file()), None)
    if source is None:
        return LUTResource(
            path=lut.path,
            title=lut.title,
            sha1=lut.sha1,
            size=lut.size,
            missing=True,
            source_path=lut.source_path,
        )

    if not stored.is_absolute() and (root / stored).resolve() == source.resolve():
        relative = stored
    else:
        suffix = source.suffix.lower() or ".cube"
        safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "-", source.stem).strip("-._")
        safe_stem = safe_stem or "lut"
        relative = Path("luts") / f"{lut.sha1[:12]}-{safe_stem}{suffix}"
    destination = root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() != destination.resolve():
        shutil.copy2(source, destination)
    return LUTResource(
        path=relative.as_posix(),
        title=lut.title,
        sha1=lut.sha1,
        size=destination.stat().st_size,
        missing=False,
        source_path=str(destination.resolve()),
    )


# ---------------------------------------------------------------------------
# ColorGrade (état complet appliqué à un clip)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ColorGrade:
    """État complet d'un étalonnage couleur appliqué à un clip.

    Les valeurs simples (exposition, contraste, etc.) sont des
    delta / pourcentages bornés ; les courbes et le LUT sont des
    objets dédiés. Un :class:`ColorGrade` est immuable ; les
    modifications passent par :meth:`with_*` qui retournent une
    nouvelle instance.

    Attributes:
        exposure: Ajustement d'exposition en stops (par défaut
            ``0.0`` = neutre, ``+1.0`` = +1 stop).
        contrast: Ajustement de contraste (``-1`` = gris plat,
            ``+1`` = contraste maximal).
        saturation: Multiplicateur de saturation (``0.0`` = noir et
            blanc, ``1.0`` = neutre, ``2.0`` = saturé à fond).
        temperature: Décalage de température en delta (``-100`` =
            très froid, ``+100`` = très chaud).
        hue: Rotation de teinte en degrés (``-180`` à ``180``).
        shadows: Ajustement des ombres (``-1`` = noirs écrasés,
            ``+1`` = ombres relevées).
        highlights: Ajustement des hautes lumières (``-1`` = blancs
            brûlés, ``+1`` = hautes lumières relevées).
        curves: Courbes par canal.
        lut: LUT optionnel ; ``None`` = aucun LUT appliqué.
        enabled: ``False`` neutralise tout l'étalonnage sans le
            supprimer.
    """

    exposure: float = 0.0
    contrast: float = 0.0
    saturation: float = 1.0
    temperature: float = 0.0
    hue: float = 0.0
    shadows: float = 0.0
    highlights: float = 0.0
    curves: ColorCurves = field(default_factory=ColorCurves)
    lut: LUTResource | None = None
    enabled: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "exposure", _coerce_range(
            float(self.exposure), "exposure",
            minimum=EXPOSURE_MIN, maximum=EXPOSURE_MAX,
        ))
        object.__setattr__(self, "contrast", _coerce_range(
            float(self.contrast), "contrast",
            minimum=CONTRAST_MIN, maximum=CONTRAST_MAX,
        ))
        object.__setattr__(self, "saturation", _coerce_range(
            float(self.saturation), "saturation",
            minimum=SATURATION_MIN, maximum=SATURATION_MAX,
        ))
        object.__setattr__(self, "temperature", _coerce_range(
            float(self.temperature), "temperature",
            minimum=TEMPERATURE_MIN, maximum=TEMPERATURE_MAX,
        ))
        object.__setattr__(self, "hue", _coerce_range(
            float(self.hue), "hue",
            minimum=HUE_MIN, maximum=HUE_MAX,
        ))
        object.__setattr__(self, "shadows", _coerce_range(
            float(self.shadows), "shadows",
            minimum=SHADOWS_MIN, maximum=SHADOWS_MAX,
        ))
        object.__setattr__(self, "highlights", _coerce_range(
            float(self.highlights), "highlights",
            minimum=HIGHLIGHTS_MIN, maximum=HIGHLIGHTS_MAX,
        ))

    @classmethod
    def identity(cls) -> "ColorGrade":
        """Construit un :class:`ColorGrade` neutre (aucun effet)."""
        return cls()

    def is_identity(self) -> bool:
        """``True`` si l'étalonnage est numériquement neutre."""
        return (
            self.exposure == 0.0
            and self.contrast == 0.0
            and self.saturation == 1.0
            and self.temperature == 0.0
            and self.hue == 0.0
            and self.shadows == 0.0
            and self.highlights == 0.0
            and self.curves.is_identity()
            and self.lut is None
        )

    def with_field(self, name: str, value: float) -> "ColorGrade":
        """Retourne un clone avec un champ simple mis à jour."""
        if name not in {
            "exposure", "contrast", "saturation", "temperature",
            "hue", "shadows", "highlights",
        }:
            raise ColorGradingError(f"Champ inconnu : {name!r}.")
        return ColorGrade(**{**self.__dict__, name: float(value)})

    def with_curves(self, curves: ColorCurves) -> "ColorGrade":
        return ColorGrade(**{**self.__dict__, "curves": curves})

    def with_lut(self, lut: LUTResource | None) -> "ColorGrade":
        return ColorGrade(**{**self.__dict__, "lut": lut})

    def with_enabled(self, enabled: bool) -> "ColorGrade":
        return ColorGrade(**{**self.__dict__, "enabled": bool(enabled)})


# ---------------------------------------------------------------------------
# Presets de couleur
# ---------------------------------------------------------------------------


class ColorPresetCategory(str, Enum):
    """Catégories de la bibliothèque d'étalonnage couleur.

    Les six catégories natives correspondent aux six préréglages
    livrés avec l'application (cf. :func:`builtin_color_presets`).
    """

    CINEMA = "cinema"
    TEAL_ORANGE = "teal_orange"
    WARM = "warm"
    COOL = "cool"
    BLACK_AND_WHITE = "black_and_white"
    VINTAGE = "vintage"


CATEGORY_LABELS: dict[ColorPresetCategory, str] = {
    ColorPresetCategory.CINEMA: "Cinéma",
    ColorPresetCategory.TEAL_ORANGE: "Teal & Orange",
    ColorPresetCategory.WARM: "Chaud",
    ColorPresetCategory.COOL: "Froid",
    ColorPresetCategory.BLACK_AND_WHITE: "Noir et blanc contrasté",
    ColorPresetCategory.VINTAGE: "Vintage",
}


CATEGORY_DESCRIPTIONS: dict[ColorPresetCategory, str] = {
    ColorPresetCategory.CINEMA: (
        "Contraste cinéma avec noirs profonds et blancs légèrement "
        "chauds."
    ),
    ColorPresetCategory.TEAL_ORANGE: (
        "Look hollywoodien : ombres bleu‑vert, hautes lumières orangées."
    ),
    ColorPresetCategory.WARM: (
        "Teinte chaude globale, idéale pour ambiances estivales."
    ),
    ColorPresetCategory.COOL: (
        "Teinte froide globale, idéale pour scènes nocturnes ou "
        "hivernales."
    ),
    ColorPresetCategory.BLACK_AND_WHITE: (
        "Saturation à zéro, contraste élevé pour un noir et blanc "
        "contrasté."
    ),
    ColorPresetCategory.VINTAGE: (
        "Look vintage : teinte orangée, contraste adouci, ombres "
        "légèrement bleutées."
    ),
}


@dataclass(frozen=True)
class ColorPreset:
    """Un preset d'étalonnage prêt à l'emploi.

    Attributes:
        id: Identifiant unique et stable.
        name: Libellé court.
        description: Description longue (FR par défaut).
        category: Catégorie de la bibliothèque.
        grade: :class:`ColorGrade` capturé par le preset.
        builtin: ``True`` pour les préréglages livrés avec l'app.
    """

    id: str
    name: str
    description: str
    category: ColorPresetCategory
    grade: ColorGrade
    builtin: bool

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ColorGradingNameError("Le nom du preset est obligatoire.")
        if len(self.name) > MAX_NAME_LENGTH:
            raise ColorGradingNameError(
                f"Le nom du preset est trop long : {len(self.name)} > "
                f"{MAX_NAME_LENGTH}."
            )


def _require_text(name: str, value: str) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        raise ColorGradingNameError(f"Le {name} ne peut pas être vide.")
    if len(cleaned) > MAX_NAME_LENGTH:
        raise ColorGradingNameError(
            f"Le {name} est trop long ({len(cleaned)} > {MAX_NAME_LENGTH})."
        )
    return cleaned


def make_color_preset(
    *,
    preset_id: str,
    name: str,
    description: str,
    category: ColorPresetCategory | str,
    grade: ColorGrade,
    builtin: bool = False,
) -> ColorPreset:
    """Fabrique un :class:`ColorPreset` en validant ses arguments."""
    return ColorPreset(
        id=_require_id(preset_id, "preset"),
        name=_require_text("nom", name),
        description=(description or "").strip(),
        category=ColorPresetCategory(category),
        grade=grade,
        builtin=bool(builtin),
    )


# ---------------------------------------------------------------------------
# Presets natifs (tâche 29) — 6 préréglages prêts à l'emploi
# ---------------------------------------------------------------------------


def _cinema_preset() -> ColorPreset:
    return make_color_preset(
        preset_id="cinema",
        name="Cinéma",
        description=CATEGORY_DESCRIPTIONS[ColorPresetCategory.CINEMA],
        category=ColorPresetCategory.CINEMA,
        grade=ColorGrade(
            exposure=-0.05,
            contrast=0.35,
            saturation=0.85,
            temperature=10.0,
            hue=2.0,
            shadows=-0.15,
            highlights=0.05,
        ),
        builtin=True,
    )


def _teal_orange_preset() -> ColorPreset:
    # Teinte orangée sur les hautes lumières, ombres tirant vers le
    # cyan via les courbes rouge/bleu.
    red_curve = ColorCurve.identity()
    red_curve = _s_curve(red_curve, lift=0.05, gain=0.05)
    blue_curve = ColorCurve.identity()
    blue_curve = _s_curve(blue_curve, lift=-0.05, gain=-0.05)
    return make_color_preset(
        preset_id="teal_orange",
        name="Teal & Orange",
        description=CATEGORY_DESCRIPTIONS[ColorPresetCategory.TEAL_ORANGE],
        category=ColorPresetCategory.TEAL_ORANGE,
        grade=ColorGrade(
            contrast=0.4,
            saturation=1.15,
            temperature=15.0,
            shadows=-0.2,
            highlights=0.2,
            curves=ColorCurves(red=red_curve, blue=blue_curve),
        ),
        builtin=True,
    )


def _warm_preset() -> ColorPreset:
    return make_color_preset(
        preset_id="warm",
        name="Chaud",
        description=CATEGORY_DESCRIPTIONS[ColorPresetCategory.WARM],
        category=ColorPresetCategory.WARM,
        grade=ColorGrade(
            temperature=25.0,
            saturation=1.05,
            highlights=0.1,
        ),
        builtin=True,
    )


def _cool_preset() -> ColorPreset:
    return make_color_preset(
        preset_id="cool",
        name="Froid",
        description=CATEGORY_DESCRIPTIONS[ColorPresetCategory.COOL],
        category=ColorPresetCategory.COOL,
        grade=ColorGrade(
            temperature=-25.0,
            saturation=1.05,
            shadows=-0.05,
        ),
        builtin=True,
    )


def _black_and_white_preset() -> ColorPreset:
    return make_color_preset(
        preset_id="black_and_white",
        name="Noir et blanc contrasté",
        description=CATEGORY_DESCRIPTIONS[
            ColorPresetCategory.BLACK_AND_WHITE
        ],
        category=ColorPresetCategory.BLACK_AND_WHITE,
        grade=ColorGrade(
            saturation=0.0,
            contrast=0.55,
            exposure=0.05,
            shadows=-0.2,
            highlights=0.15,
        ),
        builtin=True,
    )


def _vintage_preset() -> ColorPreset:
    # Vintage : ombres bleutées, hautes lumières orangées, saturation
    # modérément diminuée, contraste adouci.
    red_curve = ColorCurve.identity()
    red_curve = _s_curve(red_curve, lift=0.03, gain=-0.05)
    blue_curve = ColorCurve.identity()
    blue_curve = _s_curve(blue_curve, lift=-0.08, gain=0.05)
    return make_color_preset(
        preset_id="vintage",
        name="Vintage",
        description=CATEGORY_DESCRIPTIONS[ColorPresetCategory.VINTAGE],
        category=ColorPresetCategory.VINTAGE,
        grade=ColorGrade(
            contrast=-0.15,
            saturation=0.75,
            temperature=20.0,
            shadows=-0.1,
            highlights=-0.05,
            curves=ColorCurves(red=red_curve, blue=blue_curve),
        ),
        builtin=True,
    )


def _s_curve(
    base: ColorCurve, *, lift: float, gain: float,
) -> ColorCurve:
    """Applique un lift (ombres) + gain (hautes lumières) à une courbe.

    C'est une opération courante en étalonnage : ``lift > 0`` tire les
    ombres vers le haut ; ``gain < 0`` descend les hautes lumières.
    La courbe reste une diagonale modulo ces ajustements.
    """
    new_points = []
    for x, y in base.points:
        # Distance à 0.5 : on amplifie le lift sur les ombres et le
        # gain sur les hautes lumières. La transition est douce grâce
        # à une interpolation linéaire entre les deux extrêmes.
        weight_shadow = max(0.0, 1.0 - 2 * x)  # 1 à x=0, 0 à x=0.5
        weight_highlight = max(0.0, 2 * x - 1.0)  # 0 à x=0.5, 1 à x=1
        weight_mid = max(
            0.0, 1.0 - weight_shadow - weight_highlight
        )
        new_y = y + lift * weight_shadow + gain * weight_highlight
        new_y = max(MIN_CURVE_VALUE, min(MAX_CURVE_VALUE, new_y))
        new_points.append((x, new_y))
    return ColorCurve(points=tuple(new_points))


BUILTIN_COLOR_PRESETS_COUNT: int = 6


def builtin_color_presets() -> tuple[ColorPreset, ...]:
    """Retourne les 6 préréglages natifs de l'application."""
    return (
        _cinema_preset(),
        _teal_orange_preset(),
        _warm_preset(),
        _cool_preset(),
        _black_and_white_preset(),
        _vintage_preset(),
    )


def builtin_color_preset_ids() -> frozenset[str]:
    """Ensemble des identifiants des préréglages natifs."""
    return frozenset(p.id for p in builtin_color_presets())


def make_user_color_preset(
    name: str,
    description: str,
    grade: ColorGrade,
    *,
    category: ColorPresetCategory | str | None = None,
) -> ColorPreset:
    """Crée un préréglage utilisateur à partir d'un :class:`ColorGrade`.

    Si ``category`` est omise, on infère ``VINTAGE`` par défaut :
    un preset personnel commence souvent par retoucher un look
    vintage. On laisse l'utilisateur réorganiser ensuite.
    """
    chosen = (
        ColorPresetCategory(category)
        if category is not None
        else ColorPresetCategory.VINTAGE
    )
    return make_color_preset(
        preset_id=f"user-color-{uuid.uuid4().hex[:12]}",
        name=name,
        description=description,
        category=chosen,
        grade=grade,
        builtin=False,
    )


# ---------------------------------------------------------------------------
# Filtrage
# ---------------------------------------------------------------------------


def filter_color_presets(
    presets: Iterable[ColorPreset],
    *,
    search: str = "",
    category: ColorPresetCategory | str | None = None,
    favorites: Sequence[str] | None = None,
    favorites_only: bool = False,
) -> list[ColorPreset]:
    """Filtre les préréglages couleur (recherche, catégorie, favoris)."""
    needle = (search or "").strip().lower()
    wanted_category = (
        ColorPresetCategory(category) if category is not None else None
    )
    favorite_set = set(favorites or ())
    result: list[ColorPreset] = []
    for preset in presets:
        if wanted_category is not None and preset.category != wanted_category:
            continue
        if favorites_only and preset.id not in favorite_set:
            continue
        if (
            needle
            and needle not in preset.id.lower()
            and needle not in preset.name.lower()
        ):
            continue
        result.append(preset)
    return result


# ---------------------------------------------------------------------------
# Persistance : favoris + préréglages utilisateur
# ---------------------------------------------------------------------------


COLOR_PRESETS_FILE: str = "color_presets.json"
"""Nom du fichier JSON de favoris / presets utilisateur couleur."""


def _user_presets_dir(
    settings_dir: str | os.PathLike[str] | None = None,
) -> Path:
    """Répertoire de stockage des préférences utilisateur."""
    if settings_dir is not None:
        return Path(settings_dir)
    env = os.environ.get("KUT_STUDIO_CONFIG_DIR")
    if env:
        return Path(env)
    home = Path.home()
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "Kut-Studio"
        return home / "Kut-Studio"
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "Kut-Studio"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "kut-studio"
    return home / ".config" / "kut-studio"


def color_presets_path(
    settings_dir: str | os.PathLike[str] | None = None,
) -> Path:
    """Chemin du fichier JSON des favoris / presets utilisateur couleur."""
    return _user_presets_dir(settings_dir) / COLOR_PRESETS_FILE


def _curve_to_dict(curve: ColorCurve) -> list[list[float]]:
    return [[float(x), float(y)] for x, y in curve.points]


def _dict_to_curve(raw: object) -> ColorCurve:
    if not isinstance(raw, list):
        return ColorCurve.identity()
    points: list[tuple[float, float]] = []
    for item in raw:
        if (
            not isinstance(item, (list, tuple))
            or len(item) != 2
        ):
            continue
        try:
            points.append((float(item[0]), float(item[1])))
        except (TypeError, ValueError):
            continue
    if not points:
        return ColorCurve.identity()
    try:
        return ColorCurve(points=tuple(points))
    except ColorGradingError:
        return ColorCurve.identity()


def _grade_to_dict(grade: ColorGrade) -> dict[str, object]:
    return {
        "exposure": float(grade.exposure),
        "contrast": float(grade.contrast),
        "saturation": float(grade.saturation),
        "temperature": float(grade.temperature),
        "hue": float(grade.hue),
        "shadows": float(grade.shadows),
        "highlights": float(grade.highlights),
        "curves": {
            "master": _curve_to_dict(grade.curves.master),
            "red": _curve_to_dict(grade.curves.red),
            "green": _curve_to_dict(grade.curves.green),
            "blue": _curve_to_dict(grade.curves.blue),
        },
        "lut": (
            {
                # Les presets globaux ne connaissent pas le dossier d'un
                # projet : conserver le chemin de travail absolu évite une
                # référence relative ambiguë lors d'une prochaine session.
                "path": grade.lut.source_path or grade.lut.path,
                "title": grade.lut.title,
                "sha1": grade.lut.sha1,
                "size": int(grade.lut.size),
                "missing": bool(grade.lut.missing),
            }
            if grade.lut is not None
            else None
        ),
        "enabled": bool(grade.enabled),
    }


def _dict_to_grade(raw: object) -> ColorGrade:
    if not isinstance(raw, dict):
        return ColorGrade.identity()
    curves_raw = raw.get("curves")
    if not isinstance(curves_raw, dict):
        curves = ColorCurves()
    else:
        curves = ColorCurves(
            master=_dict_to_curve(curves_raw.get("master")),
            red=_dict_to_curve(curves_raw.get("red")),
            green=_dict_to_curve(curves_raw.get("green")),
            blue=_dict_to_curve(curves_raw.get("blue")),
        )
    lut_raw = raw.get("lut")
    lut: LUTResource | None = None
    if isinstance(lut_raw, dict):
        try:
            path = str(lut_raw.get("path", ""))
            exists = bool(path) and Path(path).is_file()
            lut = LUTResource(
                path=path,
                title=str(lut_raw.get("title", "") or "LUT"),
                sha1=str(lut_raw.get("sha1", "")),
                size=int(lut_raw.get("size", 0)),
                missing=bool(lut_raw.get("missing", False)) or not exists,
                source_path=str(Path(path).resolve()) if exists else None,
            )
        except (ColorGradingError, ValueError, TypeError):
            lut = None
    try:
        return ColorGrade(
            exposure=float(raw.get("exposure", 0.0)),
            contrast=float(raw.get("contrast", 0.0)),
            saturation=float(raw.get("saturation", 1.0)),
            temperature=float(raw.get("temperature", 0.0)),
            hue=float(raw.get("hue", 0.0)),
            shadows=float(raw.get("shadows", 0.0)),
            highlights=float(raw.get("highlights", 0.0)),
            curves=curves,
            lut=lut,
            enabled=bool(raw.get("enabled", True)),
        )
    except ColorGradingError:
        return ColorGrade.identity()


def _coerce_user_preset(payload: object) -> ColorPreset | None:
    if not isinstance(payload, dict):
        return None
    name = str(payload.get("name", "")).strip()
    if not name:
        return None
    description = payload.get("description", "")
    if not isinstance(description, str):
        description = ""
    grade = _dict_to_grade(payload.get("grade"))
    category_raw = payload.get("category")
    try:
        category = ColorPresetCategory(category_raw)
    except ValueError:
        category = ColorPresetCategory.VINTAGE
    preset_id = payload.get("id")
    if not isinstance(preset_id, str) or not preset_id:
        return None
    try:
        return make_color_preset(
            preset_id=preset_id,
            name=name,
            description=description,
            category=category,
            grade=grade,
            builtin=False,
        )
    except ColorGradingError:
        return None


def _coerce_favorites(payload: object) -> list[str]:
    if not isinstance(payload, list):
        return []
    return [
        entry for entry in payload
        if isinstance(entry, str) and entry
    ]


def load_color_preset_data(
    settings_dir: str | os.PathLike[str] | None = None,
) -> tuple[list[ColorPreset], list[str]]:
    """Charge favoris et presets utilisateur depuis le disque."""
    path = color_presets_path(settings_dir)
    if not path.exists():
        return [], []
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return [], []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return [], []
    if not isinstance(data, dict):
        return [], []
    raw_user_presets = data.get("user_presets", [])
    raw_favorites = data.get("favorites", [])
    user_presets = [
        preset
        for preset in (
            _coerce_user_preset(entry) for entry in raw_user_presets
        )
        if preset is not None
    ]
    favorites = _coerce_favorites(raw_favorites)
    return user_presets, favorites


def save_color_preset_data(
    user_presets: Sequence[ColorPreset],
    favorites: Sequence[str],
    settings_dir: str | os.PathLike[str] | None = None,
) -> Path:
    """Persiste favoris et presets utilisateur (écriture atomique)."""
    base = _user_presets_dir(settings_dir)
    base.mkdir(parents=True, exist_ok=True)
    target = base / COLOR_PRESETS_FILE
    payload = {
        "version": 1,
        "favorites": [
            fav for fav in favorites
            if isinstance(fav, str) and fav
        ],
        "user_presets": [
            {
                "id": preset.id,
                "name": preset.name,
                "description": preset.description,
                "category": preset.category.value,
                "grade": _grade_to_dict(preset.grade),
            }
            for preset in user_presets
            if not preset.builtin
        ],
    }
    fd, tmp_name = tempfile.mkstemp(
        prefix=f"{target.name}.",
        suffix=".tmp",
        dir=str(base),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            json.dump(payload, tmp_file, indent=2, ensure_ascii=False)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_path, target)
    except Exception:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise
    return target


class ColorPresetStore:
    """Gestionnaire en mémoire des favoris et presets utilisateur couleur."""

    def __init__(
        self,
        user_presets: Sequence[ColorPreset] | None = None,
        favorites: Sequence[str] | None = None,
        settings_dir: str | os.PathLike[str] | None = None,
    ) -> None:
        self._settings_dir = settings_dir
        if user_presets is None or favorites is None:
            loaded_user, loaded_favorites = load_color_preset_data(
                settings_dir
            )
            if user_presets is None:
                user_presets = loaded_user
            if favorites is None:
                favorites = loaded_favorites
        self._user_presets: dict[str, ColorPreset] = {
            preset.id: preset
            for preset in user_presets
            if not preset.builtin
        }
        self._favorites: set[str] = {fav for fav in favorites if fav}
        self._listeners: list = []

    # ----- API publique --------------------------------------------------

    def all_user_presets(self) -> list[ColorPreset]:
        return list(self._user_presets.values())

    def favorites(self) -> list[str]:
        return list(self._favorites)

    def is_favorite(self, preset_id: str) -> bool:
        return preset_id in self._favorites

    def get_preset(self, preset_id: str) -> ColorPreset | None:
        for preset in builtin_color_presets():
            if preset.id == preset_id:
                return preset
        return self._user_presets.get(preset_id)

    def all_presets(self) -> list[ColorPreset]:
        library = list(builtin_color_presets())
        library.extend(self._user_presets.values())
        return library

    def merge_user_presets(self, presets: Sequence[ColorPreset]) -> None:
        """Ajoute des presets embarqués sans écrire la configuration globale."""
        changed = False
        for preset in presets:
            if preset.builtin or preset.id in self._user_presets:
                continue
            self._user_presets[preset.id] = preset
            changed = True
        if changed:
            self._notify_changed()

    def add_user_preset(self, preset: ColorPreset) -> ColorPreset:
        if preset.builtin:
            raise ValueError("Les préréglages intégrés ne peuvent pas être ajoutés.")
        if preset.id in self._user_presets:
            raise ValueError(f"Un préréglage '{preset.id}' existe déjà.")
        self._user_presets[preset.id] = preset
        self._persist()
        self._notify_changed()
        return preset

    def remove_user_preset(self, preset_id: str) -> ColorPreset:
        if preset_id not in self._user_presets:
            raise KeyError(f"Préréglage '{preset_id}' introuvable.")
        removed = self._user_presets.pop(preset_id)
        self._favorites.discard(preset_id)
        self._persist()
        self._notify_changed()
        return removed

    def set_favorite(self, preset_id: str, favorite: bool) -> bool:
        if favorite:
            if not self._preset_exists(preset_id):
                raise KeyError(f"Préréglage '{preset_id}' introuvable.")
            if preset_id in self._favorites:
                return False
            self._favorites.add(preset_id)
        else:
            if preset_id not in self._favorites:
                return False
            self._favorites.remove(preset_id)
        self._persist()
        self._notify_changed()
        return True

    def toggle_favorite(self, preset_id: str) -> bool:
        new_state = preset_id not in self._favorites
        self.set_favorite(preset_id, new_state)
        return new_state

    def subscribe(self, callback) -> None:
        if callback not in self._listeners:
            self._listeners.append(callback)

    # ----- Persistance ---------------------------------------------------

    def _persist(self) -> None:
        try:
            save_color_preset_data(
                list(self._user_presets.values()),
                list(self._favorites),
                self._settings_dir,
            )
        except OSError:
            # Un environnement lecture seule ne doit pas empêcher l'édition
            # en mémoire (tests sandboxés, poste géré, volume démonté).
            pass

    def _notify_changed(self) -> None:
        for callback in list(self._listeners):
            try:
                callback()
            except Exception:  # pragma: no cover - tolérance listener
                    pass

    def _preset_exists(self, preset_id: str) -> bool:
        if preset_id in builtin_color_preset_ids():
            return True
        return preset_id in self._user_presets


# ---------------------------------------------------------------------------
# Opérations au niveau projet
# ---------------------------------------------------------------------------


def _color_grade_or_default(value: object) -> ColorGrade:
    if isinstance(value, ColorGrade):
        return value
    if value is None:
        return ColorGrade.identity()
    return ColorGrade.identity()


@dataclass
class ColorGradingService:
    """Service haut‑niveau : opérations CRUD sur les :class:`ColorGrade`.

    Le service manipule ``clip.color_grade`` directement. Chaque
    mutation retourne le nouveau :class:`ColorGrade` pour permettre
    à l'appelant de chaîner des vérifications.
    """

    def __init__(self) -> None:
        pass

    def get_grade(self, project, clip_id: str) -> ColorGrade:
        """Retourne l'étalonnage du clip (neutre si manquant)."""
        clip = self._find_clip(project, clip_id)
        if clip is None:
            raise ColorGradingError(f"Clip '{clip_id}' introuvable.")
        return _color_grade_or_default(getattr(clip, "color_grade", None))

    def set_grade(
        self, project, clip_id: str, grade: ColorGrade | None,
    ) -> ColorGrade:
        """Définit l'étalonnage d'un clip."""
        clip = self._find_clip(project, clip_id)
        if clip is None:
            raise ColorGradingError(f"Clip '{clip_id}' introuvable.")
        if getattr(clip, "locked", False):
            raise ColorGradingError(
                f"Le clip '{clip_id}' est verrouillé."
            )
        object.__setattr__(clip, "color_grade", grade or ColorGrade.identity())
        return clip.color_grade

    def reset_grade(self, project, clip_id: str) -> ColorGrade:
        """Réinitialise l'étalonnage d'un clip à l'identité."""
        return self.set_grade(project, clip_id, ColorGrade.identity())

    def apply_preset(
        self, project, clip_id: str, preset: ColorPreset,
    ) -> ColorGrade:
        """Applique un preset au clip (en gardant le LUT s'il y en a un)."""
        if preset is None:
            raise ColorGradingError("Le preset est obligatoire.")
        # Les presets intégrés n'embarquent pas de LUT et préservent donc
        # celui du clip. Un preset utilisateur qui en contient un l'applique.
        current = self.get_grade(project, clip_id)
        merged = preset.grade.with_lut(preset.grade.lut or current.lut)
        return self.set_grade(project, clip_id, merged)

    def _find_clip(self, project, clip_id: str):
        # Petite méthode utilitaire : on cherche le clip dans toutes
        # les pistes du projet. ``project`` peut être un Project ou un
        # Project-compatible (sous‑ensemble).
        if hasattr(project, "tracks"):
            for track in project.tracks:
                for clip in track.clips:
                    if clip.id == clip_id:
                        return clip
        return None


__all__ = [
    # Erreurs
    "ColorGradingError",
    "ColorGradingRangeError",
    "ColorGradingNameError",
    "ColorGradingUnknownChannelError",
    # Constantes
    "BUILTIN_COLOR_PRESETS_COUNT",
    "CATEGORY_DESCRIPTIONS",
    "CATEGORY_LABELS",
    "CHANNELS",
    "COLOR_PRESETS_FILE",
    "CONTRAST_MAX",
    "CONTRAST_MIN",
    "EXPOSURE_MAX",
    "EXPOSURE_MIN",
    "HIGHLIGHTS_MAX",
    "HIGHLIGHTS_MIN",
    "HUE_MAX",
    "HUE_MIN",
    "MAX_CURVE_POINTS",
    "MAX_NAME_LENGTH",
    "MIN_CURVE_POINTS",
    "SATURATION_MAX",
    "SATURATION_MIN",
    "SHADOWS_MAX",
    "SHADOWS_MIN",
    "TEMPERATURE_MAX",
    "TEMPERATURE_MIN",
    # Modèles
    "ColorCurve",
    "ColorCurves",
    "ColorGrade",
    "ColorPreset",
    "ColorPresetCategory",
    "ColorPresetStore",
    "LUTResource",
    # Helpers
    "builtin_color_preset_ids",
    "builtin_color_presets",
    "color_presets_path",
    "copy_lut_into_project",
    "filter_color_presets",
    "load_color_preset_data",
    "make_color_preset",
    "make_user_color_preset",
    "save_color_preset_data",
    # Service
    "ColorGradingService",
]
