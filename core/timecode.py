"""Timecode professionnel SMPTE : l'unique endroit où vivent ses mathématiques.

Un timecode est une **étiquette** ``HH:MM:SS:FF`` posée sur chaque image par la caméra ou l'enregistreur ; deux
appareils calés sur la même horloge portent la même étiquette au même instant, c'est ce qui permet de synchroniser
des angles Multicam sans écouter le son. Tout calcul sur ces étiquettes passe par ce module (jamais de ``fps``
tronqué ni de ``h * 3600 + m * 60 + s + f / fps`` recopié ailleurs : cadences fractionnaires et *drop-frame* y
produiraient une dérive d'image à image).

Cadences prises en charge (``SUPPORTED_FRAME_RATES``) : 23,976, 24, 25, 29,97 (sans saut), 29,97 *drop-frame*, 30, 50,
59,94 (sans saut et *drop-frame*, 4 étiquettes sautées par minute) et 60. Une cadence inconnue **n'est jamais arrondie
au plus proche entier** (l'ancien défaut : 29,97 lu comme 29) : ``FrameRate.from_fps`` lève ``UnsupportedFrameRate`` et
``FrameRate.try_from_fps`` rend ``None``. Convention du module : ``from_*`` / ``parse`` lèvent une ``ValueError``,
``try_*`` rendent ``None``.

Conventions
-----------
* **Étiquette ↔ numéro d'image** : ``Timecode.to_frames`` compte les images écoulées depuis ``00:00:00:00``. En
  *drop-frame* (SMPTE 12M), les étiquettes ``:00`` et ``:01`` (``:00`` à ``:03`` à 59,94) sont **sautées** au début de
  chaque minute, sauf toutes les dixièmes : aucune image n'est supprimée, seule la numérotation saute, pour que
  l'étiquette suive l'horloge murale (``01:00:00;00`` = 107 892 images = 3 599,9964 s). Une étiquette qui n'existe pas
  (``00:01:00;00`` à 29,97 DF) est refusée.
* **Secondes** : ``images / cadence`` exacte (``Fraction``). Sans saut à 29,97, ``01:00:00:00`` vaut donc 3 603,6 s :
  c'est le temps *réellement écoulé* depuis l'étiquette zéro, la seule grandeur qui compte pour un décalage entre deux
  appareils (``seconds_between``).
* **Séparateur** : ``:`` avant les images = sans saut, ``;`` = *drop-frame* ; ``.`` est lu comme ``;`` (ffprobe l'écrit
  ainsi pour certains fichiers). Le formatage rend toujours ``:`` ou ``;``.
* **24 h** : les étiquettes bouclent à minuit (``from_frames`` / ``from_seconds`` prennent le modulo d'un jour ; en
  *drop-frame*, 24 h contiennent exactement 2 589 408 images à 29,97, car 24 h est un multiple de 10 minutes).
  ``seconds_between`` ne boucle **pas** : un tournage à cheval sur minuit donne un écart d'environ 24 h, à traiter par
  l'appelant.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from fractions import Fraction
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .project_model import MediaAsset

SECONDS_PER_DAY = 86400
"""Un jour : le timecode (et le ``time_reference`` BWF) boucle à minuit."""

_SNAP_TOLERANCE = 2e-4
"""Écart relatif toléré pour reconnaître une cadence (29,97 ~ 30000/1001 mais ≠ 30 : l'écart 29,97 / 30 vaut 1e-3)."""

_ONE_FRAME_EPSILON = Fraction(1, 1_000_000)
"""Marge (en images) qui absorbe l'erreur d'un ``float`` quand on convertit des secondes en numéro d'image."""


class UnsupportedFrameRate(ValueError):
    """La cadence n'est pas l'une de ``SUPPORTED_FRAME_RATES`` (ou le *drop-frame* n'existe pas à cette cadence)."""


class InvalidTimecode(ValueError):
    """Texte mal formé, ou étiquette qui n'existe pas (hors bornes, ou numéro d'image sauté en *drop-frame*)."""


# ---------------------------------------------------------------------------
# Cadences
# ---------------------------------------------------------------------------

_BASE_RATES: tuple[Fraction, ...] = (
    Fraction(24000, 1001),
    Fraction(24),
    Fraction(25),
    Fraction(30000, 1001),
    Fraction(30),
    Fraction(50),
    Fraction(60000, 1001),
    Fraction(60),
)
_DROP_FRAME_CAPABLE: dict[Fraction, int] = {Fraction(30000, 1001): 2, Fraction(60000, 1001): 4}
"""Cadences qui existent en *drop-frame*, avec le nombre d'étiquettes sautées chaque minute."""
_LABELS: dict[Fraction, str] = {
    Fraction(24000, 1001): "23.976",
    Fraction(30000, 1001): "29.97",
    Fraction(60000, 1001): "59.94",
}


@dataclass(frozen=True)
class FrameRate:
    """Cadence d'un timecode : fréquence exacte (rationnelle) et variante *drop-frame*.

    Attributes:
        fps: Fréquence exacte (``Fraction(30000, 1001)`` pour 29,97). Doit être l'une des cadences prises en charge.
        drop_frame: ``True`` pour la numérotation *drop-frame* (29,97 et 59,94 seulement).
    """

    fps: Fraction
    drop_frame: bool = False

    def __post_init__(self) -> None:
        if self.fps not in _BASE_RATES:
            raise UnsupportedFrameRate(f"cadence de timecode non prise en charge : {self.fps}")
        if self.drop_frame and self.fps not in _DROP_FRAME_CAPABLE:
            raise UnsupportedFrameRate(f"le drop-frame n'existe pas à {float(self.fps):g} i/s (29,97 et 59,94 seulement)")

    @classmethod
    def from_fps(cls, fps: float | Fraction, *, drop_frame: bool = False) -> FrameRate:
        """Reconnaît une cadence à partir d'un nombre approximatif (``29.97``, ``29.970029…``, ``Fraction(30000, 1001)``).

        Raises:
            UnsupportedFrameRate: cadence inconnue, nulle, négative ou non finie ; ou ``drop_frame`` demandé à une
                cadence qui n'en a pas.
        """
        return cls(_snap(fps), drop_frame)

    @classmethod
    def try_from_fps(cls, fps: float | Fraction, *, drop_frame: bool = False) -> FrameRate | None:
        """Comme :meth:`from_fps`, ``None`` si la cadence n'est pas reconnue."""
        try:
            return cls.from_fps(fps, drop_frame=drop_frame)
        except (UnsupportedFrameRate, TypeError):
            return None

    @property
    def nominal(self) -> int:
        """Images par seconde *comptées par l'étiquette* (24 pour 23,976 ; 30 pour 29,97 ; 60 pour 59,94)."""
        return round(self.fps)

    @property
    def drop_per_minute(self) -> int:
        """Étiquettes sautées au début de chaque minute (hors dixièmes) : 0 sans saut, 2 à 29,97 DF, 4 à 59,94 DF."""
        return _DROP_FRAME_CAPABLE[self.fps] if self.drop_frame else 0

    @property
    def frames_per_day(self) -> int:
        """Images d'une journée d'étiquettes (86 400 s nominales moins les étiquettes sautées : 54 minutes par heure)."""
        return self.nominal * SECONDS_PER_DAY - self.drop_per_minute * 54 * 24

    @property
    def label(self) -> str:
        """Libellé court sans décimale tronquée : ``23.976``, ``29.97 DF``, ``25``."""
        text = _LABELS.get(self.fps, str(self.fps.numerator))
        return f"{text} DF" if self.drop_frame else text

    @property
    def supports_drop_frame(self) -> bool:
        """``True`` à 29,97 et 59,94, les seules cadences qui existent en *drop-frame*."""
        return self.fps in _DROP_FRAME_CAPABLE

    def with_drop_frame(self, drop_frame: bool) -> FrameRate:
        """Même cadence, autre numérotation (lève ``UnsupportedFrameRate`` si le *drop-frame* n'y existe pas)."""
        return FrameRate(self.fps, drop_frame)


SUPPORTED_FRAME_RATES: tuple[FrameRate, ...] = (
    FrameRate(Fraction(24000, 1001)),
    FrameRate(Fraction(24)),
    FrameRate(Fraction(25)),
    FrameRate(Fraction(30000, 1001)),
    FrameRate(Fraction(30000, 1001), drop_frame=True),
    FrameRate(Fraction(30)),
    FrameRate(Fraction(50)),
    FrameRate(Fraction(60000, 1001)),
    FrameRate(Fraction(60000, 1001), drop_frame=True),
    FrameRate(Fraction(60)),
)


def _snap(fps: float | Fraction) -> Fraction:
    """Cadence prise en charge la plus proche de ``fps`` (à ``_SNAP_TOLERANCE`` près), sinon ``UnsupportedFrameRate``."""
    if isinstance(fps, bool) or not isinstance(fps, (int, float, Fraction)):
        raise UnsupportedFrameRate(f"cadence illisible : {fps!r}")
    if isinstance(fps, float) and not math.isfinite(fps):
        raise UnsupportedFrameRate(f"cadence non finie : {fps}")
    if fps <= 0:
        raise UnsupportedFrameRate(f"cadence non positive : {fps}")
    value = Fraction(fps)
    best = min(_BASE_RATES, key=lambda rate: abs(rate - value) / rate)
    if abs(best - value) / best > Fraction(_SNAP_TOLERANCE):
        raise UnsupportedFrameRate(f"cadence de timecode non prise en charge : {float(value):g} i/s")
    return best


def format_fps(fps: float, decimal: str = ".") -> str:
    """Cadence lisible, jamais tronquée : ``29.97`` (pas ``29``), ``23.976``, ``25``. ``decimal`` : séparateur décimal.

    Une cadence hors liste est écrite avec au plus trois décimales utiles (``47.952``) ; une valeur nulle ou illisible
    donne une chaîne vide.
    """
    if isinstance(fps, bool) or not isinstance(fps, (int, float)):
        return ""
    rate = FrameRate.try_from_fps(fps)
    if rate is not None:
        text = rate.label
    elif math.isfinite(fps) and fps > 0:
        text = f"{fps:.3f}".rstrip("0").rstrip(".")
    else:
        return ""
    return text.replace(".", decimal)


# ---------------------------------------------------------------------------
# Texte
# ---------------------------------------------------------------------------

_TEXT = re.compile(r"(\d{1,2}):(\d{1,2}):(\d{1,2})([:;.])(\d{1,3})", re.ASCII)   # ASCII : pas de chiffres arabes


def normalize_timecode_text(text: object) -> str | None:
    """Forme canonique ``HH:MM:SS:FF`` (ou ``;``) d'un timecode lu dans un fichier, ``None`` s'il est mal formé.

    Ne dépend d'aucune cadence : vérifie la structure (quatre nombres, séparateur avant les images) et les bornes
    ``HH < 24``, ``MM < 60``, ``SS < 60``. ``.`` devient ``;`` ; les nombres sont complétés à deux chiffres ; les espaces
    autour sont ignorés. Ne lève jamais.
    """
    if not isinstance(text, str):
        return None
    match = _TEXT.fullmatch(text.strip())
    if match is None:
        return None
    hours, minutes, seconds, separator, frames = match.groups()
    if int(hours) >= 24 or int(minutes) >= 60 or int(seconds) >= 60:
        return None
    return f"{int(hours):02d}:{int(minutes):02d}:{int(seconds):02d}{':' if separator == ':' else ';'}{int(frames):02d}"


# ---------------------------------------------------------------------------
# Timecode
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Timecode:
    """Étiquette ``HH:MM:SS:FF`` d'une image, valide pour sa cadence.

    La construction refuse ce qui n'existe pas : ``HH >= 24``, ``MM`` / ``SS`` ≥ 60, ``FF`` ≥ cadence nominale, et en
    *drop-frame* les étiquettes sautées (``InvalidTimecode``).
    """

    hours: int
    minutes: int
    seconds: int
    frames: int
    rate: FrameRate

    def __post_init__(self) -> None:
        for name in ("hours", "minutes", "seconds", "frames"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise InvalidTimecode(f"{name} doit être un entier : {value!r}")
        if not 0 <= self.hours < 24:
            raise InvalidTimecode(f"heures hors de 0-23 : {self.hours}")
        if not 0 <= self.minutes < 60:
            raise InvalidTimecode(f"minutes hors de 0-59 : {self.minutes}")
        if not 0 <= self.seconds < 60:
            raise InvalidTimecode(f"secondes hors de 0-59 : {self.seconds}")
        if not 0 <= self.frames < self.rate.nominal:
            raise InvalidTimecode(f"image {self.frames} hors de 0-{self.rate.nominal - 1} à {self.rate.label} i/s")
        if self.rate.drop_frame and self.seconds == 0 and self.minutes % 10 != 0 and self.frames < self.rate.drop_per_minute:
            raise InvalidTimecode(
                f"étiquette inexistante en drop-frame : {self} (les images 0 à {self.rate.drop_per_minute - 1} "
                "sont sautées au début de chaque minute, sauf toutes les dixièmes)"
            )

    # -- Texte ------------------------------------------------------------------------------------------------

    def __str__(self) -> str:
        separator = ";" if self.rate.drop_frame else ":"
        return f"{self.hours:02d}:{self.minutes:02d}:{self.seconds:02d}{separator}{self.frames:02d}"

    @classmethod
    def parse(cls, text: str, rate: FrameRate | float | Fraction) -> Timecode:
        """Lit ``HH:MM:SS:FF`` (sans saut) ou ``HH:MM:SS;FF`` / ``HH:MM:SS.FF`` (*drop-frame*).

        Stricte sur la structure (quatre nombres, un séparateur ``:`` ``;`` ou ``.`` avant les images), tolérante sur
        les espaces de début et de fin. ``rate`` : une :class:`FrameRate` ou une cadence approximative. La numérotation
        est *drop-frame* si la cadence l'est **ou** si le séparateur l'indique ; à une cadence qui n'a pas de
        *drop-frame* (25 i/s…), un ``;`` est ignoré (étiquette lue sans saut).

        Raises:
            InvalidTimecode: texte mal formé ou étiquette inexistante.
            UnsupportedFrameRate: cadence non prise en charge.
        """
        if not isinstance(text, str):
            raise InvalidTimecode(f"timecode illisible : {text!r}")
        match = _TEXT.fullmatch(text.strip())
        if match is None:
            raise InvalidTimecode(f"timecode mal formé : {text!r} (attendu HH:MM:SS:FF ou HH:MM:SS;FF)")
        hours, minutes, seconds, separator, frames = match.groups()
        base = rate if isinstance(rate, FrameRate) else FrameRate.from_fps(rate)
        if separator != ":" and base.supports_drop_frame and not base.drop_frame:
            base = base.with_drop_frame(True)
        return cls(int(hours), int(minutes), int(seconds), int(frames), base)

    @classmethod
    def try_parse(cls, text: object, rate: FrameRate | float | Fraction | None) -> Timecode | None:
        """Comme :meth:`parse`, ``None`` si le texte, l'étiquette ou la cadence sont invalides (ne lève jamais)."""
        if rate is None or not isinstance(text, str):
            return None
        try:
            return cls.parse(text, rate)
        except (InvalidTimecode, UnsupportedFrameRate, TypeError):
            return None

    # -- Images et secondes -----------------------------------------------------------------------------------

    def to_frames(self) -> int:
        """Images écoulées depuis ``00:00:00:00`` (compensation *drop-frame* SMPTE 12M comprise)."""
        rate = self.rate
        total_minutes = 60 * self.hours + self.minutes
        count = rate.nominal * (3600 * self.hours + 60 * self.minutes + self.seconds) + self.frames
        if rate.drop_frame:
            count -= rate.drop_per_minute * (total_minutes - total_minutes // 10)
        return count

    def to_seconds_exact(self) -> Fraction:
        """Secondes écoulées depuis l'étiquette zéro, exactes (``images / cadence``)."""
        return Fraction(self.to_frames()) / self.rate.fps

    def to_seconds(self) -> float:
        """Version ``float`` de :meth:`to_seconds_exact` (précise à ~1e-11 s sur 24 h)."""
        return float(self.to_seconds_exact())

    @classmethod
    def from_frames(cls, frames: int, rate: FrameRate) -> Timecode:
        """Étiquette de la ``frames``-ième image depuis minuit ; boucle à 24 h (``frames`` négatif : modulo d'un jour)."""
        count = frames % rate.frames_per_day
        if rate.drop_frame:
            drop = rate.drop_per_minute
            per_minute = rate.nominal * 60 - drop
            per_ten_minutes = rate.nominal * 600 - 9 * drop
            tens, remainder = divmod(count, per_ten_minutes)
            count += 9 * drop * tens
            if remainder >= drop:
                count += drop * ((remainder - drop) // per_minute)
        seconds_total, frame = divmod(count, rate.nominal)
        minutes_total, second = divmod(seconds_total, 60)
        hour, minute = divmod(minutes_total, 60)
        return cls(hour, minute, second, frame, rate)

    @classmethod
    def from_seconds(cls, seconds: float | Fraction, rate: FrameRate) -> Timecode:
        """Étiquette de l'image qui contient l'instant ``seconds`` (depuis minuit) ; boucle à 24 h.

        L'instant est arrondi vers le bas à l'image (avec une marge de 1e-6 image qui absorbe l'erreur d'un ``float`` :
        ``Timecode.from_seconds(tc.to_seconds(), rate) == tc`` pour toute étiquette).
        """
        exact = Fraction(seconds) * rate.fps
        return cls.from_frames(math.floor(exact + _ONE_FRAME_EPSILON), rate)


def seconds_between(first: Timecode, second: Timecode) -> float:
    """Temps écoulé de ``first`` à ``second`` en secondes (négatif si ``second`` précède ``first``).

    Exact même entre deux cadences différentes (la soustraction se fait sur des ``Fraction``). Ne boucle pas à minuit.
    """
    return float(second.to_seconds_exact() - first.to_seconds_exact())


# ---------------------------------------------------------------------------
# Départ d'un média
# ---------------------------------------------------------------------------


def asset_start_seconds(asset: MediaAsset) -> float | None:
    """Heure de début d'un média (secondes depuis minuit), ou ``None`` s'il n'en porte pas.

    Source, par ordre : le ``timecode`` SMPTE lu dans le fichier, interprété à ``asset.timecode_fps`` (cadence de la
    piste timecode quand elle diffère de l'image, par exemple 29,97 pour une vidéo en 59,94) sinon ``asset.fps`` ; à
    défaut, le ``time_reference`` d'un fichier audio BWF. C'est ce que la synchronisation par timecode compare : le
    décalage d'un angle est ``début_i - min(début)``. Un média sans timecode (ou dont l'étiquette est illisible, de
    cadence inconnue, ou sautée en *drop-frame*) rend ``None`` : jamais d'exception.
    """
    text = asset.timecode
    if text:
        rate = FrameRate.try_from_fps(asset.timecode_fps or asset.fps or 0.0)
        timecode = Timecode.try_parse(text, rate)
        if timecode is not None:
            return timecode.to_seconds()
    reference = asset.time_reference
    if reference is not None and math.isfinite(reference) and 0.0 <= reference < SECONDS_PER_DAY:
        return float(reference)
    return None
