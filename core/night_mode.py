"""Mode nuit de l'interface : réglages, horaire et état, sans Qt, sans horloge système.

Le mode nuit adoucit la lumière bleue des **couleurs de l'interface** (fonds, panneaux, textes, bordures, accents). Il ne touche
jamais une image du montage : moniteur, rendu d'export, scopes, page Couleur et miniatures gardent leurs octets (voir
``docs/ui-layout.md``). Ce module ne connaît que l'heure et les réglages ; la transformation des couleurs est dans
:mod:`ui.night_palette`.

* ``enabled`` : bascule manuelle ; ``auto`` : actif aussi entre ``start`` et ``end`` (heures locales ``HH:MM``) ;
* début == fin : la plage est vide (jamais actif par l'horaire) ; début > fin : la plage passe minuit ;
* l'horloge est **injectée** (``now: datetime`` locale), jamais lue ici (même patron que :mod:`core.entitlements`) ;
* ``override`` : un « éteindre » manuel pendant la plage horaire dure jusqu'à la **fin de cette plage** (instant mémorisé : une veille
  d'une nuit à la suivante ne le prolonge pas) ; une horloge qui recule avant sa pose l'annule aussi. Allumer à la main est un
  réglage (``enabled``), sans expiration.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

DEFAULT_INTENSITY: int = 60
INTENSITY_RANGE: tuple[int, int] = (0, 100)
DEFAULT_START: str = "21:00"
DEFAULT_END: str = "07:00"
CHECK_INTERVAL_MS: int = 60_000
"""Période de la vérification de l'horaire (un ``QTimer`` léger, arrêté à la fermeture)."""

_TIME = re.compile(r"^(\d{1,2}):(\d{2})$")


def coerce_intensity(value: object) -> int:
    """Intensité 0 à 100 ; valeur invalide (booléen, texte, NaN, infini) : défaut. Une valeur hors bornes est ramenée à la borne."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        return DEFAULT_INTENSITY
    if value in (float("inf"), float("-inf")):
        return DEFAULT_INTENSITY
    return max(INTENSITY_RANGE[0], min(INTENSITY_RANGE[1], round(value)))


def parse_time(value: object) -> int | None:
    """``"HH:MM"`` (24 h) en minutes depuis minuit ; ``None`` si ce n'est pas une heure valide (jamais d'exception)."""
    if not isinstance(value, str) or len(value) > 5:
        return None
    match = _TIME.match(value.strip())
    if match is None:
        return None
    hours, minutes = int(match.group(1)), int(match.group(2))
    if hours > 23 or minutes > 59:
        return None
    return hours * 60 + minutes


def format_time(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def coerce_time(value: object, default: str) -> str:
    """Heure ``HH:MM`` normalisée ; invalide : ``default``."""
    minutes = parse_time(value)
    return default if minutes is None else format_time(minutes)


def in_window(start: str, end: str, now: datetime) -> bool:
    """``now`` est-il dans la plage ``[start, end)`` ? Passe minuit si ``start > end`` ; ``start == end`` ou heure invalide : jamais."""
    first, last = parse_time(start), parse_time(end)
    if first is None or last is None or first == last:
        return False
    current = now.hour * 60 + now.minute
    if first < last:
        return first <= current < last
    return current >= first or current < last


@dataclass
class NightModeState:
    """Réglages et état de session du mode nuit (le modèle ; l'interface l'applique)."""

    enabled: bool = False
    intensity: int = DEFAULT_INTENSITY
    auto: bool = False
    start: str = DEFAULT_START
    end: str = DEFAULT_END
    override: bool | None = None
    override_since: datetime | None = None
    override_until: datetime | None = None

    def scheduled(self, now: datetime) -> bool:
        return self.auto and in_window(self.start, self.end, now)

    def _override_value(self, now: datetime) -> bool | None:
        """L'``override`` s'il vaut encore à ``now`` : posé à ``override_since``, expiré à ``override_until`` ou si l'horloge a reculé."""
        if self.override is None:
            return None
        since, until = self.override_since, self.override_until
        if since is not None and until is not None and since <= now < until:
            return self.override
        return None

    def active(self, now: datetime) -> bool:
        held = self._override_value(now)
        if held is not None:
            return held
        return self.enabled or self.scheduled(now)

    def level(self, now: datetime) -> int:
        """Intensité à appliquer maintenant : 0 si éteint (la palette de base, intacte)."""
        return self.intensity if self.active(now) else 0

    def toggle(self, now: datetime) -> bool:
        """Bascule à la main ; renvoie le nouvel état. Éteindre pendant la plage horaire dure jusqu'à la fin de la plage."""
        turn_on = not self.active(now)
        self.enabled = turn_on
        self.clear_override()
        if not turn_on and self.scheduled(now):
            self.override, self.override_since, self.override_until = False, now, self._next_end(now)
        return turn_on

    def _next_end(self, now: datetime) -> datetime:
        """Prochaine fin de plage strictement après ``now`` (``now`` est dans la plage : c'est la fin de cette plage)."""
        minutes = parse_time(self.end) or 0
        end = now.replace(hour=minutes // 60, minute=minutes % 60, second=0, microsecond=0)
        return end if end > now else end + timedelta(days=1)

    def clear_override(self) -> None:
        self.override = self.override_since = self.override_until = None

    def sync(self, now: datetime) -> None:
        """À appeler à chaque vérification : purge un ``override`` expiré, quels qu'aient été les échantillons précédents."""
        if self.override is not None and self._override_value(now) is None:
            self.clear_override()

    def reset_override(self, now: datetime) -> None:
        """Un réglage modifié (horaire, option automatique) repart de zéro."""
        self.clear_override()


__all__ = [
    "CHECK_INTERVAL_MS", "DEFAULT_END", "DEFAULT_INTENSITY", "DEFAULT_START", "INTENSITY_RANGE", "NightModeState",
    "coerce_intensity", "coerce_time", "format_time", "in_window", "parse_time",
]
