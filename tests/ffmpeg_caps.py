"""Capacités du FFmpeg que l'application utilise réellement : libass (sous-titres incrustés).

Un seul endroit décide si un test de sous-titres tourne, se saute ou échoue, pour que tous
les tests concernés (marqués ``@pytest.mark.libass``, voir ``conftest.py``) se comportent
pareil :

* **libass présent** : le test tourne ;
* **libass absent** (développeur, job macOS principal dont le FFmpeg Homebrew n'a pas libass) :
  le test se **saute** en disant pourquoi ;
* **libass absent et** ``KUT_STUDIO_REQUIRE_LIBASS=1`` (job dédié de la CI) : le test **échoue**.
  Un job vert signifie alors « libass a été testé », jamais « tout a été sauté ».

Le FFmpeg interrogé est celui de l'application (``core.export_engine._ffmpeg_command_prefix`` :
``KUT_STUDIO_FFMPEG``, ``KUT_STUDIO_FFMPEG_DIR``, binaire embarqué, puis ``PATH``), pas le
premier ``ffmpeg`` du ``PATH``. Si les deux diffèrent, c'est le premier qui compte : c'est lui
qui incrustera les sous-titres de l'utilisateur.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache

import pytest

REQUIRE_LIBASS_VARIABLE = "KUT_STUDIO_REQUIRE_LIBASS"
"""Variable d'environnement qui transforme le saut en échec."""

_TRUTHY = frozenset({"1", "true", "yes", "on"})
_PROBE_TIMEOUT_SECONDS = 60
"""Large : le premier lancement d'un binaire fraîchement installé est lent (Gatekeeper, bibliothèques)."""

# Une ligne de ``ffmpeg -filters`` : drapeaux (« .. », « TS », parfois trois lettres), nom, entrées->sorties.
_SUBTITLES_FILTER = re.compile(r"^\s*\S{2,3}\s+subtitles\s+\S+->\S+", re.MULTILINE)


def require_libass_requested(environment: Mapping[str, str] | None = None) -> bool:
    """``True`` si l'environnement exige libass (le saut devient un échec)."""
    env = os.environ if environment is None else environment
    return env.get(REQUIRE_LIBASS_VARIABLE, "").strip().lower() in _TRUTHY


@dataclass(frozen=True)
class LibassStatus:
    """Ce que le FFmpeg de l'application dit de libass."""

    command: tuple[str, ...]
    version: str
    has_filter: bool
    """Le filtre ``subtitles`` figure dans ``ffmpeg -filters`` (ce dont l'application a besoin)."""
    configured: bool
    """``ffmpeg -buildconf`` contient ``--enable-libass`` (la preuve que c'est libass)."""
    error: str = ""
    """Raison pour laquelle le FFmpeg n'a pas pu être interrogé (vide si tout va bien)."""

    def problem(self, *, strict: bool) -> str:
        """Phrase qui dit ce qui manque, ou ``""`` si libass est bien disponible.

        ``strict`` demande aussi la preuve ``--enable-libass`` ; sinon le filtre seul suffit
        (c'est le critère historique de l'application et des tests).
        """
        if self.error:
            return f"FFmpeg inutilisable : {self.error}"
        which = f"{self.command[0]} ({self.version or 'version inconnue'})"
        hint = " Installez un FFmpeg avec libass (macOS : brew install ffmpeg-full, voir docs/ci-libass.md)."
        if not self.has_filter:
            return f"le FFmpeg de l'application, {which}, n'a pas le filtre « subtitles » (libass absent).{hint}"
        if strict and not self.configured:
            return (
                f"le FFmpeg de l'application, {which}, a un filtre « subtitles » mais sa configuration "
                f"(ffmpeg -buildconf) ne contient pas --enable-libass : build inattendue.{hint}"
            )
        return ""


def _run(command: tuple[str, ...], *arguments: str) -> str:
    completed = subprocess.run(
        [*command, "-hide_banner", *arguments],
        capture_output=True, text=True, errors="replace", timeout=_PROBE_TIMEOUT_SECONDS, check=False,
    )
    return completed.stdout


@lru_cache(maxsize=8)
def _probe(command: tuple[str, ...]) -> LibassStatus:
    try:
        version_text = _run(command, "-version")
        filters_text = _run(command, "-filters")
        buildconf_text = _run(command, "-buildconf")
    except (OSError, subprocess.SubprocessError) as error:
        return LibassStatus(command, "", False, False, error=f"{command[0]} : {error}")
    first_line = version_text.splitlines()[0] if version_text else ""
    version = first_line.removeprefix("ffmpeg version ").split(" Copyright")[0].strip()
    return LibassStatus(
        command=command,
        version=version,
        has_filter=bool(_SUBTITLES_FILTER.search(filters_text)),
        configured="--enable-libass" in buildconf_text.split(),
    )


def libass_status() -> LibassStatus:
    """État de libass dans le FFmpeg de l'application (mis en cache par commande)."""
    from core.export_engine import _ffmpeg_command_prefix

    try:
        command = tuple(_ffmpeg_command_prefix())
    except ImportError as error:
        return LibassStatus((), "", False, False, error=str(error))
    return _probe(command)


def has_libass() -> bool:
    """Le FFmpeg de l'application sait-il incruster des sous-titres ? (critère du saut)."""
    return libass_status().problem(strict=False) == ""


def libass_skip_reason() -> str:
    """Raison du saut quand libass manque (chaîne vide si libass est présent)."""
    return libass_status().problem(strict=False)


def ensure_libass() -> None:
    """Garde des tests de sous-titres : continue, saute avec la raison, ou échoue.

    Sans ``KUT_STUDIO_REQUIRE_LIBASS`` un libass absent saute le test (comportement historique).
    Avec la variable, il l'échoue : la CI ne peut pas rester verte sans avoir testé libass.
    """
    strict = require_libass_requested()
    problem = libass_status().problem(strict=strict)
    if not problem:
        # La détection de l'application est mise en cache pour la durée du processus : un test précédent
        # qui a lancé un faux FFmpeg y a peut-être laissé un faux négatif. On l'oublie.
        from core import export_engine

        if hasattr(export_engine._ffmpeg_supports_subtitles, "_cached"):
            del export_engine._ffmpeg_supports_subtitles._cached  # type: ignore[attr-defined]
        return
    if strict:
        pytest.fail(f"{REQUIRE_LIBASS_VARIABLE}=1 : libass est exigé mais {problem}", pytrace=False)
    pytest.skip(problem)
