"""Numéros de version SemVer 2.0 : lecture stricte et ordre exact.

Une comparaison de chaînes classe ``0.10.0`` avant ``0.9.0`` et ``1.0.0`` avant ``1.0.0-beta`` : les deux erreurs
feraient proposer une version plus ancienne. :class:`Version` compare des nombres, et suit la règle 11 de SemVer pour
les préversions :

* ``1.0.0-alpha < 1.0.0-alpha.1 < 1.0.0-alpha.beta < 1.0.0-beta < 1.0.0-beta.2 < 1.0.0-beta.11 < 1.0.0-rc.1 < 1.0.0`` ;
* un identifiant numérique se compare numériquement et passe avant un identifiant alphanumérique ;
* les métadonnées de construction (``+build.5``) sont conservées mais ignorées par la comparaison.

Le ``v`` d'un tag (``v1.2.3``) est accepté. Tout le reste (``1.2``, ``01.2.3``, ``1.2.3-``, espaces) est refusé :
une release au tag mal formé est ignorée plutôt que devinée.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import total_ordering

MAX_LENGTH = 128
"""Longueur maximale acceptée : un tag n'a aucune raison d'être plus long, et la borne coupe court aux entrées absurdes."""

_IDENTIFIER = r"[0-9A-Za-z-]+"
_PATTERN = re.compile(
    r"v?(?P<major>0|[1-9][0-9]*)\.(?P<minor>0|[1-9][0-9]*)\.(?P<patch>0|[1-9][0-9]*)"
    rf"(?:-(?P<pre>{_IDENTIFIER}(?:\.{_IDENTIFIER})*))?"
    rf"(?:\+(?P<build>{_IDENTIFIER}(?:\.{_IDENTIFIER})*))?"
)


class InvalidVersion(ValueError):
    """Texte qui n'est pas une version SemVer valide."""


def _identifier_key(identifier: str) -> tuple[int, int, str]:
    """Clé d'un identifiant de préversion : un nombre passe avant un mot, et se compare numériquement."""
    if identifier.isdigit():
        return (0, int(identifier), "")
    return (1, 0, identifier)


@total_ordering
@dataclass(frozen=True)
class Version:
    """Version SemVer ; l'ordre et l'égalité ignorent les métadonnées de construction."""

    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...] = ()
    build: tuple[str, ...] = field(default=(), compare=False)

    @classmethod
    def parse(cls, text: str) -> Version:
        """Lit ``text`` (``1.2.3``, ``v1.2.3-beta.1+sha.5``) ; :class:`InvalidVersion` sinon."""
        if not isinstance(text, str) or len(text) > MAX_LENGTH:
            raise InvalidVersion(f"version illisible : {text!r}")
        match = _PATTERN.fullmatch(text)
        if match is None:
            raise InvalidVersion(f"version illisible : {text!r}")
        prerelease = tuple(match.group("pre").split(".")) if match.group("pre") else ()
        for identifier in prerelease:
            if identifier.isdigit() and len(identifier) > 1 and identifier.startswith("0"):
                raise InvalidVersion(f"identifiant numérique avec un zéro initial : {text!r}")
        build = tuple(match.group("build").split(".")) if match.group("build") else ()
        return cls(int(match.group("major")), int(match.group("minor")), int(match.group("patch")), prerelease, build)

    @classmethod
    def try_parse(cls, text: object) -> Version | None:
        """Comme :meth:`parse`, mais ``None`` au lieu d'une exception (tags publiés par d'autres)."""
        if not isinstance(text, str):
            return None
        try:
            return cls.parse(text)
        except InvalidVersion:
            return None

    @property
    def is_prerelease(self) -> bool:
        return bool(self.prerelease)

    @property
    def core(self) -> str:
        """``MAJEUR.MINEUR.CORRECTIF`` seul (``CFBundleVersion`` macOS n'accepte que des nombres)."""
        return f"{self.major}.{self.minor}.{self.patch}"

    def _key(self) -> tuple[int, int, int, tuple[int, tuple[tuple[int, int, str], ...]]]:
        # Une version finale (1) passe après toutes ses préversions (0) ; entre préversions, l'ordre des tuples
        # applique la règle « à préfixe égal, la liste la plus courte est la plus petite ».
        if not self.prerelease:
            return (self.major, self.minor, self.patch, (1, ()))
        return (self.major, self.minor, self.patch, (0, tuple(_identifier_key(part) for part in self.prerelease)))

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return self._key() < other._key()

    def __str__(self) -> str:
        text = self.core
        if self.prerelease:
            text += "-" + ".".join(self.prerelease)
        if self.build:
            text += "+" + ".".join(self.build)
        return text


__all__ = ["InvalidVersion", "MAX_LENGTH", "Version"]
