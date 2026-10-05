"""Préparation d'une release : contrôle du tag, archives conventionnelles, empreintes, vérification, notes.

Tout est déterministe et testable hors CI (``tests/test_release_tools.py``) ; seule l'archive macOS délègue à
``ditto``, l'outil d'Apple qui conserve les liens symboliques, les attributs étendus et la signature d'un ``.app``
(``zipfile`` suivrait les liens de ``Python.framework`` et casserait la signature).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from core.app_version import APP_NAME, APP_VERSION, GITHUB_REPOSITORY
from core.release_assets import (
    CHECKSUMS_FILE,
    RELEASE_TARGETS,
    OperatingSystem,
    Target,
    asset_name,
    checksums_for,
    format_checksums,
    parse_asset_name,
    parse_checksums,
    sha256_file,
)
from core.versioning import Version

SIGNING_STATUSES = ("notarized", "signed", "adhoc")
"""État de la signature macOS d'une release : notarisée, signée Developer ID sans notarisation, ou ad hoc."""


class ReleaseError(RuntimeError):
    """Release non conforme : on s'arrête avant de publier quoi que ce soit."""


@dataclass(frozen=True)
class TagCheck:
    version: str
    prerelease: bool
    publish: bool
    """``True`` seulement pour un tag ``v…`` poussé : un lancement manuel construit sans publier."""


def check_tag(ref: str, app_version: str = APP_VERSION) -> TagCheck:
    """Contrôle la référence Git du workflow contre la version de l'application (source unique).

    ``refs/tags/v1.2.3`` doit correspondre exactement à ``core/app_version.py`` ; toute autre référence
    (lancement manuel sur une branche) donne une construction d'essai, jamais publiée.
    """
    version = Version.parse(app_version)
    if version.build:
        raise ReleaseError(f"APP_VERSION ne doit pas porter de métadonnées de construction : {app_version}")
    if ref.startswith("refs/tags/"):
        tag = ref[len("refs/tags/"):]
        if tag != f"v{version}":
            raise ReleaseError(
                f"le tag {tag!r} ne correspond pas à APP_VERSION ({app_version}) : attendu 'v{version}'. "
                "Changez core/app_version.py, fusionnez, puis créez le tag."
            )
        return TagCheck(str(version), version.is_prerelease, publish=True)
    return TagCheck(str(version), version.is_prerelease, publish=False)


def expect_target(slug: str, detected: Target | None) -> Target:
    """La machine de construction est-elle bien la cible annoncée par la matrice ? (``macos-latest`` peut changer)."""
    expected = Target.from_slug(slug)
    if detected != expected:
        found = detected.slug if detected is not None else "inconnue"
        raise ReleaseError(f"cible de construction {found}, attendue {expected.slug}")
    return expected


def _bundle(dist: Path, target: Target) -> Path:
    path = dist / (f"{APP_NAME}.app" if target.system is OperatingSystem.MACOS else APP_NAME)
    if not path.is_dir():
        raise ReleaseError(f"application construite introuvable : {path}")
    return path


def create_archive(dist: Path, out: Path, version: str, target: Target) -> Path:
    """Archive conventionnelle de l'application construite dans ``dist`` ; renvoie son chemin."""
    source = _bundle(dist, target)
    out.mkdir(parents=True, exist_ok=True)
    archive = out / asset_name(version, target)
    if archive.exists():
        archive.unlink()
    if target.system is OperatingSystem.MACOS:
        ditto = shutil.which("ditto")
        if ditto is None:
            raise ReleaseError("ditto introuvable : l'archive macOS se construit sur macOS")
        subprocess.run([ditto, "-c", "-k", "--sequesterRsrc", "--keepParent", str(source), str(archive)], check=True)
    elif target.system is OperatingSystem.WINDOWS:
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
            for path in sorted(source.rglob("*")):
                if path.is_file():
                    bundle.write(path, Path(source.name) / path.relative_to(source))
    else:
        # tar conserve les droits d'exécution et les liens symboliques (une archive zip les perdrait).
        with tarfile.open(archive, "w:gz") as bundle:
            bundle.add(source, arcname=source.name)
    return archive


def write_checksums(directory: Path) -> Path:
    """Écrit ``SHA256SUMS.txt`` pour les paquets conventionnels du dossier."""
    packages = sorted(path for path in directory.iterdir() if path.is_file() and parse_asset_name(path.name))
    if not packages:
        raise ReleaseError(f"aucun paquet conventionnel dans {directory}")
    target = directory / CHECKSUMS_FILE
    target.write_text(format_checksums(checksums_for(packages)), encoding="utf-8")
    return target


def verify_release_directory(
    directory: Path,
    version: str,
    targets: tuple[Target, ...] = RELEASE_TARGETS,
) -> list[str]:
    """Problèmes qui interdisent de publier (liste vide : conforme).

    Exactement un paquet par cible, à la bonne version ; ``SHA256SUMS.txt`` présent, lisible, complet et exact ;
    aucun fichier étranger (il serait publié sans empreinte).
    """
    problems: list[str] = []
    expected_version = Version.parse(version)
    files = {path.name: path for path in directory.iterdir() if path.is_file()}
    found: dict[Target, str] = {}
    for name in sorted(files):
        if name == CHECKSUMS_FILE:
            continue
        parsed = parse_asset_name(name)
        if parsed is None:
            problems.append(f"fichier hors convention : {name}")
            continue
        if parsed.version != expected_version:
            problems.append(f"{name} : version {parsed.version}, attendue {expected_version}")
        if parsed.target in found:
            problems.append(f"deux paquets pour {parsed.target.slug} : {found[parsed.target]}, {name}")
        found[parsed.target] = name
    for target in targets:
        if target not in found:
            problems.append(f"paquet manquant pour {target.slug}")
    for target in found:
        if target not in targets:
            problems.append(f"cible inattendue : {target.slug}")
    checksums = files.get(CHECKSUMS_FILE)
    if checksums is None:
        problems.append(f"{CHECKSUMS_FILE} manquant")
        return problems
    try:
        entries = parse_checksums(checksums.read_text(encoding="utf-8"))
    except ValueError as exc:
        return problems + [f"{CHECKSUMS_FILE} illisible : {exc}"]
    for name in found.values():
        digest = entries.get(name)
        if digest is None:
            problems.append(f"{name} absent de {CHECKSUMS_FILE}")
        elif digest != sha256_file(files[name]):
            problems.append(f"empreinte fausse pour {name}")
    for name in entries:
        if name not in found.values():
            problems.append(f"{CHECKSUMS_FILE} cite un fichier absent : {name}")
    return problems


_MACOS_STATUS = {
    "notarized": (
        "signé Developer ID et notarisé par Apple.",
        "signed with a Developer ID and notarized by Apple.",
    ),
    "signed": (
        "signé Developer ID mais **non notarisé** : macOS bloquera la première ouverture.",
        "signed with a Developer ID but **not notarized**: macOS will block the first launch.",
    ),
    "adhoc": (
        "**non signé et non notarisé** (signature ad hoc) : macOS bloquera la première ouverture. Ne l'autorisez "
        "(Réglages Système › Confidentialité et sécurité) que si vous faites confiance à sa provenance.",
        "**not signed and not notarized** (ad-hoc signature): macOS will block the first launch. Only allow it "
        "(System Settings › Privacy & Security) if you trust where it comes from.",
    ),
}


def release_notes(version: str, macos_signing: str, generated: str = "",
                  repository: str = GITHUB_REPOSITORY) -> str:
    """Notes de release : paquets, état de signature (bilingue), vérification, puis les notes générées par GitHub."""
    if macos_signing not in _MACOS_STATUS:
        raise ReleaseError(f"état de signature inconnu : {macos_signing!r} (attendu : {', '.join(SIGNING_STATUSES)})")
    rows = {
        "macos-arm64": "macOS · Apple Silicon (arm64)",
        "windows-x64": "Windows · x64",
        "linux-x64": "Linux · x86-64 (glibc ≥ 2.39 : Ubuntu 24.04 ou plus récent)",
    }
    status_fr, status_en = _MACOS_STATUS[macos_signing]
    lines = [
        f"## {APP_NAME} {version}",
        "",
        "| Paquet / Package | Système / System |",
        "| --- | --- |",
    ]
    for target in RELEASE_TARGETS:
        lines.append(f"| `{asset_name(version, target)}` | {rows.get(target.slug, target.display_name)} |")
    lines += [
        "",
        f"**macOS** : {status_fr}  ",
        f"_{status_en}_",
        "",
        "**Windows** : non signé (Authenticode), SmartScreen peut afficher « Éditeur inconnu ».  ",
        "_Not signed (Authenticode); SmartScreen may show “Unknown publisher”._",
        "",
        f"**Vérifier / Verify** : `shasum -a 256 -c {CHECKSUMS_FILE} --ignore-missing` (macOS, Linux) · "
        "`Get-FileHash <fichier> -Algorithm SHA256` (Windows). L'empreinte prouve que le fichier est intact, pas "
        "qui l'a publié. _The checksum proves the file is intact, not who published it._",
        "",
        "FFmpeg et ffprobe restent à installer séparément. _FFmpeg and ffprobe must still be installed separately._",
        "",
        f"Mise à jour depuis l'application : Aide › Rechercher des mises à jour… — "
        f"[toutes les versions](https://github.com/{repository}/releases)",
    ]
    text = "\n".join(lines) + "\n"
    if generated.strip():
        text += "\n" + generated.strip() + "\n"
    return text


def write_github_output(path: str | os.PathLike[str] | None, values: dict[str, str]) -> None:
    """Ajoute ``clé=valeur`` au fichier ``$GITHUB_OUTPUT`` (ou les affiche hors CI)."""
    text = "".join(f"{key}={value}\n" for key, value in values.items())
    if path:
        with open(path, "a", encoding="utf-8") as stream:
            stream.write(text)
    else:
        print(text, end="")


_SAFE_VALUE = re.compile(r"[0-9A-Za-z.+-]+")


def safe_output_value(value: str) -> str:
    """Valeur écrite dans ``$GITHUB_OUTPUT`` : version ou booléen, jamais de saut de ligne."""
    if not _SAFE_VALUE.fullmatch(value):
        raise ReleaseError(f"valeur refusée pour GITHUB_OUTPUT : {value!r}")
    return value


__all__ = [
    "ReleaseError",
    "SIGNING_STATUSES",
    "TagCheck",
    "check_tag",
    "create_archive",
    "expect_target",
    "release_notes",
    "safe_output_value",
    "verify_release_directory",
    "write_checksums",
    "write_github_output",
]
