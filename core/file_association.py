"""Association des projets ``.kut`` à l'application, sous Windows et Linux.

Sous macOS, l'association vient de ``Info.plist`` (``build.py``). Ailleurs, rien n'est écrit à l'installation (il n'y a
pas d'installeur) : c'est une action explicite de l'utilisateur, qui n'écrit que dans son propre profil.

* Windows : ``HKEY_CURRENT_USER\\Software\\Classes`` (aucun droit administrateur).
* Linux : ``$XDG_DATA_HOME`` (ou ``~/.local/share``) : un type MIME, un fichier ``.desktop``, puis les bases de
  données rafraîchies si les outils sont installés.

Les écritures passent par des paramètres injectables : les tests n'écrivent ni dans le registre ni dans le profil.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .process_supervisor import supervised_run

PROJECT_EXTENSION = ".kut"
APP_NAME = "Kut-Studio"
WINDOWS_PROGID = "KutStudio.Projet"
MIME_TYPE = "application/x-kut-studio"
MIME_FILE = "kut-studio.xml"
DESKTOP_FILE = "kut-studio.desktop"
_PROJECT_LABEL = "Projet Kut-Studio"


def file_association_supported(platform: str | None = None) -> bool:
    """Vrai sous Windows et Linux, les deux systèmes où l'association n'est pas faite par le paquet."""
    return (platform or sys.platform) in ("win32", "linux")


def application_command() -> list[str]:
    """Commande qui lance l'application : l'exécutable gelé, ou ``python main.py`` depuis les sources."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    script = Path(__file__).resolve().parent.parent / "main.py"
    return [sys.executable, str(script)]


# --- Windows -------------------------------------------------------------------------------------------------------


def _windows_quote(argument: str) -> str:
    return '"' + argument.replace('"', '\\"') + '"'


def windows_entries(command: Sequence[str]) -> list[tuple[str, str]]:
    """``(clé sous Software\\Classes, valeur par défaut)`` : ce que l'association écrit dans le registre."""
    launch = " ".join(_windows_quote(part) for part in command)
    entries = [
        (PROJECT_EXTENSION, WINDOWS_PROGID),
        (WINDOWS_PROGID, _PROJECT_LABEL),
        (f"{WINDOWS_PROGID}\\shell\\open\\command", f'{launch} "%1"'),
    ]
    if getattr(sys, "frozen", False):
        # L'icône de l'exécutable n'existe que pour l'application construite.
        entries.append((f"{WINDOWS_PROGID}\\DefaultIcon", f"{_windows_quote(command[0])},0"))
    return entries


class RegistryWriter(Protocol):
    def set_default(self, key: str, value: str) -> None:
        """Écrit la valeur par défaut de ``key``, sous ``HKEY_CURRENT_USER\\Software\\Classes``."""


class _WindowsRegistry:
    def set_default(self, key: str, value: str) -> None:  # pragma: no cover - Windows seulement
        if sys.platform != "win32":  # mypy ne contrôle pas le module ``winreg`` hors Windows
            raise OSError("le registre Windows n'existe pas sur ce système")
        import winreg

        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, f"Software\\Classes\\{key}", 0, winreg.KEY_SET_VALUE) as h:
            winreg.SetValueEx(h, "", 0, winreg.REG_SZ, value)


# --- Linux ---------------------------------------------------------------------------------------------------------


_DESKTOP_SPECIAL = set(" \t\n\"'\\><~|&;$*?#()`")


def _desktop_quote(argument: str) -> str:
    """Un argument de ``Exec=`` : entre guillemets s'il contient un caractère spécial, ``%`` doublé."""
    if any(char in _DESKTOP_SPECIAL for char in argument):
        escaped = "".join("\\" + char if char in '"`$\\' else char for char in argument)
        argument = f'"{escaped}"'
    return argument.replace("%", "%%")


def desktop_entry(command: Sequence[str]) -> str:
    exec_line = " ".join(_desktop_quote(part) for part in command) + " %f"
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={APP_NAME}\n"
        f"Exec={exec_line}\n"
        f"MimeType={MIME_TYPE};\n"
        "Categories=AudioVideo;Video;\n"
        "Terminal=false\n"
    )


def mime_definition() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<mime-info xmlns="http://www.freedesktop.org/standards/shared-mime-info">\n'
        f'  <mime-type type="{MIME_TYPE}">\n'
        f"    <comment>{_PROJECT_LABEL}</comment>\n"
        f'    <glob pattern="*{PROJECT_EXTENSION}"/>\n'
        "  </mime-type>\n"
        "</mime-info>\n"
    )


def linux_files(command: Sequence[str], data_home: Path) -> dict[Path, str]:
    """Fichiers écrits sous ``data_home`` (``$XDG_DATA_HOME``) : le type MIME et le lanceur."""
    return {
        data_home / "mime" / "packages" / MIME_FILE: mime_definition(),
        data_home / "applications" / DESKTOP_FILE: desktop_entry(command),
    }


def default_data_home() -> Path:
    base = os.environ.get("XDG_DATA_HOME", "").strip()
    return Path(base) if base else Path.home() / ".local" / "share"


# --- Application ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class AssociationResult:
    """Issue de l'association : ``associated``, ``unsupported`` ou ``failed`` (``detail`` : le message technique)."""

    status: str
    detail: str = ""


Runner = Callable[..., Any]


def associate_project_files(
    *,
    platform: str | None = None,
    command: Sequence[str] | None = None,
    registry: RegistryWriter | None = None,
    data_home: Path | None = None,
    runner: Runner = supervised_run,
) -> AssociationResult:
    """Associe ``.kut`` à l'application pour l'utilisateur courant, selon le système."""
    target = platform or sys.platform
    if not file_association_supported(target):
        return AssociationResult("unsupported")
    launch = list(command) if command is not None else application_command()
    try:
        if target == "win32":
            writer = registry if registry is not None else _WindowsRegistry()
            for key, value in windows_entries(launch):
                writer.set_default(key, value)
            return AssociationResult("associated")
        home = data_home if data_home is not None else default_data_home()
        for path, text in linux_files(launch, home).items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        _refresh_linux_databases(home, runner)
        return AssociationResult("associated")
    except OSError as error:
        return AssociationResult("failed", str(error) or type(error).__name__)


def _refresh_linux_databases(home: Path, runner: Runner) -> None:
    """Rafraîchit les bases MIME et d'applications quand les outils sont là ; sans eux, rien ne casse."""
    for tool, directory in (
        ("update-mime-database", home / "mime"),
        ("update-desktop-database", home / "applications"),
    ):
        found = shutil.which(tool)
        if found:
            runner([found, str(directory)], check=False, capture_output=True, timeout=30)
    xdg_mime = shutil.which("xdg-mime")
    if xdg_mime:
        runner([xdg_mime, "default", DESKTOP_FILE, MIME_TYPE], check=False, capture_output=True, timeout=30)
