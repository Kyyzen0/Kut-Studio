"""Encodeurs, décodeurs, muxers et accélérations que l'application emploie, et ce qu'une construction **LGPL** en offre (ADR-0013).

Complète :mod:`tools.ffmpeg_license_audit` (filtres) : ``python -m tools.ffmpeg_license_audit --codecs [--ffmpeg CHEMIN]`` imprime un
tableau Markdown, une ligne par élément. Deux sources, réunies :

* les littéraux du code (``"-c:v", "libx264"``, ``"-vcodec", "png"``, ``"-f", "matroska"``…) lus à l'arbre syntaxique, avec leur fichier ;
* une liste **déclarée** (:data:`DECLARED`) de ce que l'application demande à FFmpeg sans l'écrire en dur : les décodeurs des médias que
  l'utilisateur importe (le code ne les nomme pas, FFmpeg les choisit), les formats de sortie des préréglages et des tests de rendu.

Chaque élément est classé : ``présent`` ; ``absent, GPL`` (``libx264``, ``libx265``… n'existent que dans une construction GPL) ;
``absent`` (une bibliothèque LGPL ou permissive non embarquée, ou rien). Les éléments GPL encore écrits dans le code ne sont pas
des erreurs de cet outil : l'étape 3 de l'ADR-0013 (``libx264`` / ``libx265`` retirés du chemin Store) les traite ; ``--strict`` fait
échouer (code 1) l'absence de tout élément **non GPL**, utile pour garder une construction complète.
"""

from __future__ import annotations

import ast
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from tools.ffmpeg_license_audit import GPL_FILTERS, ROOT, available_filters, python_files, scan_tree

GPL_CODECS: frozenset[str] = frozenset({"libx264", "libx264rgb", "libx265", "libxvid", "libxavs", "libxavs2", "libdavs2", "libvidstab", "libfdk_aac"})
"""Encodeurs / décodeurs absents d'un FFmpeg sans ``--enable-gpl`` (GPL, ou ``--enable-nonfree`` pour ``libfdk_aac``)."""

# (catégorie, nom) → rôle. Ce que l'application demande sans l'écrire dans un littéral de commande.
DECLARED: dict[tuple[str, str], str] = {
    **{("decoder", name): "décodage des médias importés" for name in (
        "h264", "hevc", "prores", "mpeg4", "mjpeg", "vp8", "vp9", "av1", "aac", "mp3float", "flac", "opus", "vorbis", "alac",
        "pcm_s16le", "pcm_s24le", "pcm_f32le", "png", "gif", "webp", "ffv1", "utvideo", "rawvideo")},
    **{("encoder", name): role for name, role in (
        ("h264_videotoolbox", "export H.264 (matériel Apple)"), ("hevc_videotoolbox", "export HEVC (matériel Apple)"),
        ("prores_ks", "export ProRes (préréglage « ProRes Master »)"), ("aac", "audio des exports MP4 / MOV"),
        ("aac_at", "audio AAC par AudioToolbox (alternative)"), ("pcm_s16le", "audio brut, transcription"),
        ("pcm_f32le", "mixage direct"), ("png", "images de l'aperçu"), ("ffv1", "flux intermédiaires sans perte"),
        ("utvideo", "flux intermédiaires sans perte"), ("rawvideo", "images brutes"))},
    **{("muxer", name): "sortie" for name in (
        "mp4", "mov", "matroska", "null", "rawvideo", "s16le", "f32le", "image2pipe", "framemd5", "md5", "wav")},
    **{("demuxer", name): "entrée" for name in (
        "mov", "matroska", "avi", "mp3", "wav", "flac", "ogg", "gif", "image2", "mpegts", "concat", "lavfi", "rawvideo", "s16le", "f32le")},
    ("hwaccel", "videotoolbox"): "décodage matériel Apple",
}

_CODEC_FLAG = re.compile(r"^\s*(?:-c:v|-c:a|-vcodec|-acodec|-codec:v|-codec:a)$")
_ENCODER_LINE = re.compile(r"^ [A-Z.]{6} (\S+)")
_FORMAT_LINE = re.compile(r"^ [A-Z. ]{2,3} (\S+)")


@dataclass(frozen=True)
class Item:
    category: str
    name: str
    role: str
    where: tuple[str, ...]          # fichiers du code qui le citent ("" : déclaré seulement)


def used_items(root: Path = ROOT) -> list[Item]:
    """Éléments cités par le code (paires ``-c:v``/``-c:a``/``-vcodec`` + valeur, ``-f`` + valeur, ``-hwaccel`` + valeur) et la liste déclarée."""
    cited: dict[tuple[str, str], set[str]] = {}
    for path in python_files(root):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        relative = path.relative_to(root).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.List, ast.Tuple)):
                continue
            values = [element.value if isinstance(element, ast.Constant) and isinstance(element.value, str) else None for element in node.elts]
            for flag, value in zip(values, values[1:], strict=False):
                if flag is None or value is None or not re.fullmatch(r"[a-z0-9_.-]+", value):
                    continue
                if _CODEC_FLAG.match(flag):
                    cited.setdefault(("encoder", value), set()).add(relative)
                elif flag == "-f" and value not in {"lavfi", "concat"}:
                    cited.setdefault(("muxer", value), set()).add(relative)
                elif flag == "-f" and value in {"lavfi", "concat"}:
                    cited.setdefault(("demuxer", value), set()).add(relative)
                elif flag == "-hwaccel":
                    cited.setdefault(("hwaccel", value), set()).add(relative)
    items: dict[tuple[str, str], Item] = {}
    for key, files in cited.items():
        items[key] = Item(key[0], key[1], "cité par le code", tuple(sorted(files)))
    for key, role in DECLARED.items():
        old = items.get(key)
        items[key] = Item(key[0], key[1], role, old.where if old else ())
    return sorted(items.values(), key=lambda item: (item.category, item.name))


def _listing(ffmpeg: str, option: str) -> str:
    try:
        return subprocess.run([ffmpeg, "-hide_banner", option], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def available(ffmpeg: str) -> dict[str, set[str]]:
    """``{catégorie: noms}`` d'après ``-encoders``, ``-decoders``, ``-muxers``, ``-demuxers`` et ``-hwaccels``."""
    result: dict[str, set[str]] = {"encoder": set(), "decoder": set(), "muxer": set(), "demuxer": set(), "hwaccel": set()}
    for category, option in (("encoder", "-encoders"), ("decoder", "-decoders")):
        for line in _listing(ffmpeg, option).splitlines():
            match = _ENCODER_LINE.match(line)
            if match:
                result[category].add(match.group(1))
    for category, option in (("muxer", "-muxers"), ("demuxer", "-demuxers")):
        for line in _listing(ffmpeg, option).splitlines():
            match = _FORMAT_LINE.match(line)
            if match:
                # Un muxer ou démuxeur peut porter plusieurs noms (« mov,mp4,m4a,… ») : tous sont acceptés.
                result[category].update(match.group(1).split(","))
    # ``-f lavfi`` est un périphérique d'entrée (``-devices``), pas un démuxeur.
    for line in _listing(ffmpeg, "-devices").splitlines():
        match = _FORMAT_LINE.match(line)
        if match:
            result["demuxer"].update(match.group(1).split(","))
    result["hwaccel"].update(line.strip() for line in _listing(ffmpeg, "-hwaccels").splitlines()[1:] if line.strip())
    # Un encodeur écrit dans le code ("-c:v", "x") peut aussi être le nom d'un décodeur ; le « -f » d'un démuxeur est dans ``demuxer``.
    return result


def classify(item: Item, offered: dict[str, set[str]]) -> str:
    """``présent``, ``absent, GPL`` ou ``absent``."""
    names = offered.get(item.category, set())
    if item.name in names:
        return "présent"
    if item.category == "muxer" and item.name in offered.get("demuxer", set()):      # ``-f s16le`` est aussi un format d'entrée
        return "présent"
    return "absent, GPL" if item.name in GPL_CODECS else "absent"


def table(ffmpeg: str, root: Path = ROOT) -> tuple[str, list[Item]]:
    """``(tableau Markdown, éléments absents non GPL)`` pour ``ffmpeg``."""
    offered = available(ffmpeg)
    rows, missing = ["| catégorie | nom | rôle | fichiers | LGPL |", "| --- | --- | --- | --- | --- |"], []
    for item in used_items(root):
        state = classify(item, offered)
        if state == "absent":
            missing.append(item)
        where = ", ".join(item.where[:3]) + (" …" if len(item.where) > 3 else "")
        rows.append(f"| {item.category} | `{item.name}` | {item.role} | {where or '—'} | {state} |")
    # Filtres : tout nom lu dans le code qui est un filtre de ce FFmpeg ou un filtre GPL connu (même lecture que ``ffmpeg_license_audit``).
    filters = available_filters(ffmpeg)
    for name, places in sorted(scan_tree(root).items()):
        if name in filters or name in GPL_FILTERS:
            state = "présent" if name in filters else "absent, GPL"
            rows.append(f"| filter | `{name}` | filtre du graphe | {', '.join(sorted(places)[:3])} | {state} |")
    return "\n".join(rows), missing
