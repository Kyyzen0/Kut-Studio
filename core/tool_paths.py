"""Découverte portable des outils FFmpeg, installés ou embarqués."""

from __future__ import annotations

import logging
import os
import re
import shutil
import sys
from collections.abc import Iterable, Mapping, MutableMapping
from pathlib import Path

from .build_flavor import is_store

LOGGER = logging.getLogger(__name__)


def _tool_filename(name: str, platform_name: str) -> str:
    return f"{name}.exe" if platform_name.startswith("win") else name


def bundled_tool_path(
    name: str,
    *,
    platform_name: str | None = None,
    environment: Mapping[str, str] | None = None,
    bundle_root: str | os.PathLike[str] | None = None,
) -> str | None:
    """Trouve un binaire fourni dans ``bin/`` ou via une variable explicite.

    **Version Store** (ADR-0013, étape 4) : seul le bundle compte (``bundle_root``, ``sys._MEIPASS``, dossier de
    l'exécutable). Ni ``KUT_STUDIO_FFMPEG``, ``KUT_STUDIO_FFPROBE``, ``KUT_STUDIO_FFMPEG_DIR`` ni autre variable ne
    sont lues : un binaire tiers (sous licence GPL, ou non signé) ne doit jamais être exécuté par la version Store.
    """
    platform_name = platform_name or sys.platform
    env = environment if environment is not None else os.environ
    filename = _tool_filename(name, platform_name)
    store = is_store()
    explicit = None if store else env.get(f"KUT_STUDIO_{name.upper()}")
    if explicit:
        candidate = Path(explicit).expanduser()
        if candidate.is_file():
            return str(candidate.resolve())
    roots: list[Path] = []
    configured_dir = None if store else env.get("KUT_STUDIO_FFMPEG_DIR")
    if configured_dir:
        roots.append(Path(configured_dir).expanduser())
    if bundle_root is not None:
        roots.append(Path(bundle_root))
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        roots.append(Path(frozen_root))
    if getattr(sys, "frozen", False):
        roots.append(Path(sys.executable).resolve().parent)
    for root in roots:
        for candidate in (root / "bin" / filename, root / filename):
            if candidate.is_file():
                return str(candidate.resolve())
    return None


_CONVENTIONAL_TOOL_DIRS: Mapping[str, tuple[str, ...]] = {
    "darwin": ("/opt/homebrew/bin", "/usr/local/bin", "/opt/local/bin"),
    "linux": ("/usr/local/bin", "/usr/bin", "/snap/bin", "/home/linuxbrew/.linuxbrew/bin"),
}
"""Dossiers où les gestionnaires de paquets posent FFmpeg : Homebrew (Apple Silicon, puis Intel), MacPorts ;
sous Linux, le dossier local, Snap et Linuxbrew. Windows n'en a pas besoin : son ``PATH`` est celui du registre."""


def _conventional_tool(name: str, directories: Iterable[str]) -> str | None:
    """Premier exécutable ``name`` des ``directories`` (chemin absolu), sans lever d'exception.

    Un nom portant un séparateur de chemin ou ``..`` est refusé : il sortirait du dossier. Un dossier absent
    ou illisible, un fichier non exécutable, un lien symbolique cassé ou un dossier du même nom sont ignorés."""
    separators = {"/", "\\", os.sep, os.altsep or "/"}
    if not name or name in {".", ".."} or any(sep in name for sep in separators):
        return None
    for directory in directories:
        candidate = os.path.join(directory, name)
        try:
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
        except (OSError, ValueError):
            continue
    return None


def find_media_tool(
    name: str,
    *,
    platform_name: str | None = None,
    fallback_dirs: Iterable[str] | None = None,
) -> str | None:
    """Résout le binaire embarqué, puis le ``PATH`` système, puis les dossiers conventionnels.

    Le ``PATH`` de l'utilisateur garde la priorité : le repli (Homebrew, MacPorts, Linuxbrew…) n'est essayé que
    si ni l'embarqué ni ``which`` ne trouvent l'outil. Aucun repli sous Windows. ``fallback_dirs`` remplace la
    table des dossiers conventionnels (tests)."""
    if is_store():
        # Version Store : le bundle seulement. Ni PATH, ni Homebrew, ni MacPorts (hors bac à sable et sous licence
        # inconnue), et pas de repli silencieux : l'absence se dit (voir :func:`require_media_tool`).
        return bundled_tool_path(name)
    found = bundled_tool_path(name) or shutil.which(name)
    if found:
        return found
    platform_name = platform_name or sys.platform
    if fallback_dirs is None:
        fallback_dirs = _CONVENTIONAL_TOOL_DIRS.get(platform_name, ())
    if platform_name.startswith("win"):
        return None
    return _conventional_tool(name, fallback_dirs)


class MissingBundledToolError(RuntimeError):
    """Version Store : le binaire attendu dans le bundle est absent (jamais remplacé par un autre)."""


def missing_tool_message(name: str, default: str) -> str:
    """Message d'un outil introuvable : ``default`` hors Store ; en Store, il dit que le bundle est incomplet."""
    if not is_store():
        return default
    return (f"{name} est introuvable dans l'application (installation incomplète ou endommagée) : "
            f"réinstallez Kut-Studio depuis le Mac App Store. Aucun {name} externe n'est utilisé.")


def require_media_tool(name: str) -> str:
    """Chemin de ``name`` ; lève :class:`MissingBundledToolError` (Store) ou :class:`FileNotFoundError` s'il manque."""
    found = find_media_tool(name)
    if found is None:
        message = missing_tool_message(name, f"{name} est introuvable (ni embarqué ni dans le PATH).")
        raise MissingBundledToolError(message) if is_store() else FileNotFoundError(message)
    return found


def extend_search_path(
    *,
    platform_name: str | None = None,
    environment: MutableMapping[str, str] | None = None,
) -> tuple[str, ...]:
    """Ajoute à la fin du ``PATH`` les dossiers usuels des outils FFmpeg ; renvoie ceux réellement ajoutés.

    Une application lancée depuis le Dock ou le Finder ne reçoit pas le ``PATH`` du shell de l'utilisateur,
    mais celui de ``launchd`` (``/usr/bin:/bin:/usr/sbin:/sbin``) : un FFmpeg installé avec Homebrew
    (``/opt/homebrew/bin``) y reste invisible alors qu'il répond dans le Terminal. Les dossiers n'étant
    ajoutés qu'à la suite, un ``PATH`` déjà complet (lancement depuis un terminal) garde sa priorité, et
    seuls les dossiers existants sont retenus. Les processus lancés ensuite héritent du ``PATH`` étendu.
    """
    if is_store():
        return ()           # version Store : le PATH n'est jamais étendu vers Homebrew / MacPorts (le bundle seul compte)
    platform_name = platform_name or sys.platform
    env = environment if environment is not None else os.environ
    current = env.get("PATH", "")
    # Les composants existants restent tels quels, vides compris : sous POSIX, un composant vide désigne le
    # dossier courant, et le retirer changerait ce que trouvent le processus et ses enfants.
    present = set(current.split(os.pathsep))
    added = tuple(
        directory
        for directory in _CONVENTIONAL_TOOL_DIRS.get(platform_name, ())
        if directory not in present and os.path.isdir(directory)
    )
    if added:
        env["PATH"] = os.pathsep.join([current, *added] if current else added)
    return added


_PERSONAL_DIR = re.compile(r"(?i)((?:[a-z]:)?[\\/](?:users|home)[\\/])[^\\/:]+")


def _anonymised(text: str) -> str:
    """Remplace le nom d'utilisateur des dossiers personnels (``/Users/<nom>``, ``/home/<nom>``) par ``<…>``."""
    return _PERSONAL_DIR.sub(lambda m: m.group(1) + "<…>", text)


def tool_search_report(added: Iterable[str] = ()) -> dict[str, str]:
    """État de la recherche de FFmpeg, sans lancer de sous-processus : pour le journal de démarrage."""
    return {
        "ajoutes": ", ".join(added) or "aucun",
        "PATH": _anonymised(os.environ.get("PATH", "")),
        "ffmpeg": _anonymised(find_media_tool("ffmpeg") or "introuvable"),
        "ffprobe": _anonymised(find_media_tool("ffprobe") or "introuvable"),
        "store": str(is_store()),
        "frozen": str(bool(getattr(sys, "frozen", False))),
        "platform": sys.platform,
    }


def log_tool_search(added: Iterable[str] = ()) -> None:
    """Consigne (INFO) la recherche de FFmpeg ; ne lève jamais : le démarrage ne doit pas en dépendre."""
    try:
        from .app_version import APP_VERSION

        report = tool_search_report(added)
        LOGGER.info(
            "Recherche de FFmpeg — version %s, frozen=%s, plateforme=%s, dossiers ajoutés au PATH : %s, "
            "ffmpeg : %s, ffprobe : %s, PATH : %s",
            APP_VERSION, report["frozen"], report["platform"], report["ajoutes"],
            report["ffmpeg"], report["ffprobe"], report["PATH"],
        )
    except Exception:  # noqa: BLE001 - un diagnostic ne doit jamais empêcher de démarrer
        LOGGER.debug("Journal de recherche de FFmpeg indisponible", exc_info=True)


__all__ = [
    "MissingBundledToolError", "bundled_tool_path", "extend_search_path", "find_media_tool", "log_tool_search",
    "missing_tool_message", "require_media_tool", "tool_search_report",
]
