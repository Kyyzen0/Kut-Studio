"""Noms initiaux des angles d'une source Multicam, déduits des médias (fonctions pures, sans Qt ni E/S).

Un angle créé depuis des fichiers ``C0012.mp4`` / ``C0007.mp4`` ne doit pas s'appeler « C0012 » : le monteur choisit
ses angles par leur nom. Les noms sont cherchés dans cet ordre :

1. un **mot de rôle ou de position** dans le titre du média, son nom de fichier ou son dossier (``wide``, ``close-up``,
   ``gros plan``, ``plan large``, ``handheld``, ``épaule``, ``drone``, ``cam A``, ``camera 2``, ``cam_3``, ``cam1``…),
   en français comme en anglais, normalisé (``Wide``, ``Close-up``, ``Handheld``, ``Drone``, ``Camera A``, ``Camera 2``) ;
   pour un enregistreur : ``Boom``, ``Lavalier``, ``Recorder``, ``Mix`` ;
2. le **nom de bobine** lu dans le fichier ;
3. le **modèle de caméra** (``Sony A7S III``) ;
4. le **nom de fichier nettoyé** : extension, compteurs de caméra (``C0012``, ``MVI_1234``, ``IMG_0042``, ``DSC00123``,
   ``GX010123``, ``A001_C002``), dates et grands nombres retirés, ``_`` devenus des espaces, première lettre en majuscule ;
5. enfin ``Angle N`` (``Audio N`` pour un enregistreur sans autre indice).

Les noms produits ici sont des **chaînes d'affichage créées par le cœur** : ils restent neutres quant à la langue (noms
propres, ``Wide``, ``Drone``…). Les seuls mots génériques, ``Angle`` et ``Audio``, sont des paramètres
(``angle_word`` / ``audio_word``) pour que l'interface passe les mots traduits.

:func:`suggest_angle_names` garantit des noms **distincts** dans un lot : d'abord en essayant, pour les angles qui
partagent un nom, leur indice suivant (``Wide`` / ``Wide`` avec ``cam 1`` / ``cam 2`` devient ``Camera 1`` / ``Camera 2``),
ensuite en ajoutant `` 2``, `` 3``… au besoin.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Sequence
from pathlib import PureWindowsPath

from .project_model import MediaAsset

MAX_NAME_LENGTH = 40
"""Longueur maximale d'un nom proposé (les noms de fichier démesurés sont coupés à un mot)."""

_EXTENSIONS = frozenset({
    "mp4", "mov", "mxf", "mkv", "avi", "m4v", "mts", "m2ts", "ts", "webm", "wmv", "mpg", "mpeg", "3gp", "r3d", "braw",
    "wav", "bwf", "aif", "aiff", "mp3", "m4a", "aac", "flac", "ogg", "opus", "caf",
})


# ---------------------------------------------------------------------------
# Mots de rôle
# ---------------------------------------------------------------------------


def _roles(*pairs: tuple[str, str]) -> tuple[tuple[re.Pattern[str], str], ...]:
    return tuple(
        (re.compile(rf"(?<![a-z0-9])(?:{pattern})(?![a-z0-9])"), display) for pattern, display in pairs
    )


# Motifs sur un texte « replié » : minuscules, sans accents, séparateurs réduits à une espace (voir ``_fold``).
_VIDEO_ROLES = _roles(
    ("wide(?: angle| shot)?|plan large|plan d ensemble|plan general|grand angle", "Wide"),
    ("medium(?: shot)?|plan moyen", "Medium"),
    ("close(?: ?up)?|gros plan|plan serre|plan rapproche", "Close-up"),
    ("handheld|hand held|epaule|shoulder", "Handheld"),
    ("drone|aerial|aerien|aerienne", "Drone"),
    ("gimbal", "Gimbal"),
    ("steadicam", "Steadicam"),
    ("overhead|zenithal", "Overhead"),
    ("gopro|go pro", "GoPro"),
)
_AUDIO_ROLES = _roles(
    ("boom|perche", "Boom"),
    ("lavalier|lapel|cravate|lav", "Lavalier"),
    ("recorder|enregistreur", "Recorder"),
    ("mixdown|soundboard|mix", "Mix"),
)
_CAMERA_LABEL = re.compile(r"(?<![a-z0-9])cam(?:era)?\s+([a-z]|\d{1,2})(?![a-z0-9])")


def _fold(text: str) -> str:
    """Texte comparable : mots collés séparés (``CloseUp``, ``cam1``), sans accents, minuscules, séparateurs en espace."""
    text = re.sub(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Za-z])(?=[0-9])|(?<=[0-9])(?=[A-Za-z])", " ", text)
    text = "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def _role_name(texts: Sequence[str], *, audio: bool) -> str:
    """Nom normalisé du premier mot de rôle trouvé dans ``texts`` (par ordre de priorité), ``""`` sinon."""
    roles = _AUDIO_ROLES if audio else _VIDEO_ROLES
    for text in texts:
        folded = _fold(text)
        found = [(match.start(), display) for pattern, display in roles if (match := pattern.search(folded))]
        if found:
            return min(found)[1]
    return ""


def _camera_label(texts: Sequence[str]) -> str:
    """``Camera A`` / ``Camera 2`` pour ``cam a``, ``camera 2``, ``cam_3``, ``cam1``… ; ``""`` sinon."""
    for text in texts:
        match = _CAMERA_LABEL.search(_fold(text))
        if match:
            label = match.group(1)
            return f"Camera {int(label)}" if label.isdigit() else f"Camera {label.upper()}"
    return ""


# ---------------------------------------------------------------------------
# Nom de fichier nettoyé
# ---------------------------------------------------------------------------

_COUNTERS = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"(?:MVI|IMG|DSC|DSCN|DSCF|VID|PXL|MOV)[ _-]?\d{3,}"           # Canon, iPhone, Sony, Nikon, Android, Pixel
    r"|C\d{3,5}"                                                    # Sony / Canon Cinema : C0012
    r"|[A-Z]\d{3}[ _-]?C\d{3}(?:[ _-]\d{4,6}[A-Z0-9]{0,4})?"        # ARRI / RED : A001_C002, A001_C002_0101AB
    r"|(?:19|20)\d{6}(?:[ _-]\d{6,9})?"                             # date (et heure) : 20260314_093000
    r")(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_GOPRO_COUNTER = re.compile(r"(?<![A-Za-z0-9])(?:G[XH]\d{6}|GOPR\d{4}|GP\d{6})(?![A-Za-z0-9])", re.IGNORECASE)
_BIG_NUMBER = re.compile(r"(?<![A-Za-z0-9])\d{3,}(?![A-Za-z0-9])")
_GLUED_COUNTER = re.compile(r"(?<=[^\W\d_])\d{3,}$")


def _strip_extension(name: str) -> str:
    stem, dot, extension = name.rpartition(".")
    return stem if dot and stem and extension.lower() in _EXTENSIONS else name


def _clean_filename(name: str) -> str:
    """Nom lisible tiré d'un nom de fichier ou d'un titre, ``""`` s'il ne reste rien d'utile.

    ``Concert_C0012.mp4`` → ``Concert`` ; ``my_band_live_0042.mov`` → ``My band live`` ; ``interview 01.mov`` →
    ``Interview 1`` (les petits nombres, qui distinguent souvent les caméras, sont gardés) ; ``IMG_0042.MOV`` → ``""``.
    ``GX010123.MP4`` donne ``GoPro`` (le compteur trahit la caméra).
    """
    text = _strip_extension(name.strip())
    is_gopro = bool(_GOPRO_COUNTER.search(text))
    text = _GOPRO_COUNTER.sub(" ", _COUNTERS.sub(" ", text)).replace("_", " ")
    text = _BIG_NUMBER.sub(" ", text)
    text = " ".join(text.split()).strip(" -–—.,:;+")
    text = _GLUED_COUNTER.sub("", text).strip(" -–—.,:;+")
    text = re.sub(r"(?<=[ -])0+(\d)$", r"\1", text)                   # « interview 01 » → « interview 1 »
    if not any(ch.isalpha() for ch in text):
        return "GoPro" if is_gopro else ""
    if text.isupper() and len(text) > 4:
        text = text.capitalize()
    elif text[0].islower():
        text = text[0].upper() + text[1:]
    return _truncate(text)


def _truncate(text: str) -> str:
    if len(text) <= MAX_NAME_LENGTH:
        return text
    cut = text[:MAX_NAME_LENGTH]
    return (cut.rpartition(" ")[0] or cut).rstrip(" -–—.,:;+")


def _display(text: str) -> str:
    """Balise texte (bobine, modèle) mise en forme d'angle : espaces compactés, longueur bornée, ``""`` si vide."""
    text = " ".join(text.split())
    return _truncate(text) if any(ch.isalnum() for ch in text) else ""


# ---------------------------------------------------------------------------
# Propositions
# ---------------------------------------------------------------------------


def _is_audio_only(asset: MediaAsset) -> bool:
    return asset.media_type == "audio"


def _candidates(asset: MediaAsset, *, audio: bool) -> list[str]:
    """Noms informatifs possibles d'un média, du plus au moins précis (sans doublon, sans le repli ``Angle N``)."""
    path = PureWindowsPath(asset.path) if asset.path else None
    file_name = path.name if path else ""
    title = asset.name or ""
    texts = [_strip_extension(title), _strip_extension(file_name), path.parent.name if path else ""]
    texts = [text for text in texts if text]
    found = [
        _role_name(texts, audio=audio),
        _camera_label(texts),
        _display(asset.reel),
        _display(asset.camera),
        _clean_filename(title) or _clean_filename(file_name),
    ]
    unique: list[str] = []
    for name in found:
        if name and name.casefold() not in {item.casefold() for item in unique}:
            unique.append(name)
    return unique


def _fallback(audio: bool, rank: int, angle_word: str, audio_word: str) -> str:
    return f"{audio_word if audio else angle_word} {max(rank, 0) + 1}"


def suggest_angle_name(
    asset: MediaAsset, index: int, *, angle_word: str = "Angle", audio_word: str = "Audio"
) -> str:
    """Nom initial proposé pour l'angle fait de ``asset`` (voir le module pour l'ordre des indices).

    ``index`` est le rang de départ (0 = premier) utilisé par le repli ``Angle N`` / ``Audio N`` ; avec
    :func:`suggest_angle_names`, c'est le rang parmi les angles de même nature (vidéo ou audio).
    """
    audio = _is_audio_only(asset)
    candidates = _candidates(asset, audio=audio)
    return candidates[0] if candidates else _fallback(audio, index, angle_word, audio_word)


def suggest_angle_names(
    assets: Sequence[MediaAsset], *, angle_word: str = "Angle", audio_word: str = "Audio"
) -> list[str]:
    """Noms initiaux **distincts** (sans tenir compte de la casse) pour un lot de médias, dans l'ordre donné.

    Les replis sont numérotés par nature : ``Angle 1``, ``Audio 1``, ``Angle 2`` pour une caméra, un enregistreur et
    une seconde caméra sans indice. Deux angles qui reçoivent le même nom essaient d'abord leur indice suivant
    (par exemple ``Wide`` / ``Wide`` portant ``cam 1`` et ``cam 2`` donne ``Camera 1`` / ``Camera 2``) ; s'ils restent
    identiques, `` 2``, `` 3``… sont ajoutés (``Sony A7S III``, ``Sony A7S III 2``).
    """
    ranks = {False: 0, True: 0}
    options: list[list[str]] = []
    for asset in assets:
        audio = _is_audio_only(asset)
        informative = _candidates(asset, audio=audio)
        options.append(informative + [_fallback(audio, ranks[audio], angle_word, audio_word)])
        ranks[audio] += 1
    return _unique(_disambiguate(options))


def _disambiguate(options: list[list[str]]) -> list[str]:
    """Choisit un nom par média ; fait avancer à leur indice suivant les groupes que cela rend tous distincts."""
    tier = [0] * len(options)
    changed = True
    while changed:
        changed = False
        groups: dict[str, list[int]] = defaultdict(list)
        for position, choices in enumerate(options):
            groups[choices[tier[position]].casefold()].append(position)
        for members in groups.values():
            if len(members) < 2:
                continue
            # le dernier élément de chaque liste est le repli générique : on ne s'y replie pas ici (le suffixe numérique
            # garde l'information d'un nom de caméra mieux qu'un « Angle N » anonyme)
            if any(tier[position] + 1 >= len(options[position]) - 1 for position in members):
                continue
            proposed = {options[position][tier[position] + 1].casefold() for position in members}
            if len(proposed) == len(members):
                for position in members:
                    tier[position] += 1
                changed = True
    return [choices[tier[position]] for position, choices in enumerate(options)]


def _unique(names: list[str]) -> list[str]:
    """Ajoute `` 2``, `` 3``… aux répétitions, sans jamais en créer une nouvelle (``Wide 2`` déjà pris : ``Wide 3``)."""
    reserved = {name.casefold() for name in names}
    seen: set[str] = set()
    result: list[str] = []
    for name in names:
        candidate = name
        if name.casefold() in seen:
            number = 2
            while f"{name} {number}".casefold() in reserved:
                number += 1
            candidate = f"{name} {number}"
            reserved.add(candidate.casefold())
        seen.add(candidate.casefold())
        result.append(candidate)
    return result


__all__ = ["MAX_NAME_LENGTH", "suggest_angle_name", "suggest_angle_names"]
