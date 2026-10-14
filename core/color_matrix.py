"""Matrice YUV → RVB d'un média : **la** règle, une seule pour tous les chemins de rendu.

Un flux vidéo dont l'espace colorimétrique n'est pas étiqueté n'a pas de matrice « vraie » : l'usage de l'industrie est le
BT.709 pour la HD et l'UHD, le BT.601 pour la définition standard. Jusqu'ici Kut-Studio lisait tout flux non étiqueté en
BT.601 (c'est ce que fait ``swscale``), même en HD. La règle :

- un espace **étiqueté** (``bt709``, ``bt601`` / ``smpte170m`` / ``bt470bg``, ``bt2020``) est respecté, sauf choix de
  l'utilisateur ;
- un espace **inconnu** : BT.709 si ``height >= 720 or width >= 1280``, sinon BT.601 ;
- la plage inconnue reste « limitée » (``tv``), comme avant ;
- le choix par média ``auto`` (défaut) applique ce qui précède ; ``bt601`` ou ``bt709`` le force, étiquette comprise.

Les chemins qui lisent un média (moniteur GPU, aperçu fidèle, export, miniatures, flux de remappage, cuisson des LUT
d'étalonnage, pipette) passent tous par :func:`effective_matrix` (la matrice) et :func:`ffmpeg_override` (ce qu'il faut
dire à FFmpeg quand sa propre lecture de l'étiquette ne donnerait pas cette matrice) : la parité entre eux est
l'invariant.

Module sans dépendance (ni Qt, ni modèle) : il est importé par le cœur GPU comme par l'export.
"""

from __future__ import annotations

import math
from typing import Any

MATRIX_AUTO = "auto"
MATRIX_BT601 = "bt601"
MATRIX_BT709 = "bt709"
MATRIX_CHOICES: tuple[str, ...] = (MATRIX_AUTO, MATRIX_BT601, MATRIX_BT709)
"""Les choix par média, dans l'ordre d'affichage."""

RULE_VERSION = 2
"""Numéro de la règle : 1 = BT.601 partout (jusqu'à la 0.6.1), 2 = la règle de ce module. Il entre dans les clés des
caches qui contiennent une image décodée (LUT cuites, vignettes) : un résultat obtenu avec l'ancienne règle n'est plus
resservi."""

HD_MIN_HEIGHT = 720
HD_MIN_WIDTH = 1280

LABEL_NONE = "none"
"""Étiquette lue : la sonde a été faite et le flux n'est pas étiqueté (``""`` : pas encore sondé)."""
LABEL_OTHER = "other"
"""Étiquette lue mais que Kut-Studio ne sait pas convertir lui-même (``smpte240m``, ``fcc``, ``ycgco``, ``gbr``…) :
FFmpeg la gère et le moniteur retombe sur le BT.601 comme avant."""
KNOWN_LABELS = frozenset({MATRIX_BT601, MATRIX_BT709, "bt2020"})
LABELS = frozenset({"", LABEL_NONE, LABEL_OTHER, *KNOWN_LABELS})

_FFMPEG_TO_LABEL = {
    "bt709": MATRIX_BT709,
    "bt470bg": MATRIX_BT601,
    "smpte170m": MATRIX_BT601,
    "bt2020nc": "bt2020",
    "bt2020c": "bt2020",
}
_UNSET_WORDS = frozenset({"", "unknown", "unspecified", "reserved", "n/a", "none"})
_MAX_LABEL_LENGTH = 32

_FFMPEG_NAMES = {MATRIX_BT601: "bt470bg", MATRIX_BT709: "bt709"}
"""Nom ``setparams=colorspace=`` d'une matrice que ce module sait imposer (l'idiome déjà employé par la cuisson des LUT)."""


def normalize_choice(value: Any) -> str:
    """Le choix par média valide, ou ``ValueError`` (texte inconnu, mauvais type) : jamais ramené en silence."""
    if isinstance(value, str) and value in MATRIX_CHOICES:
        return value
    shown = value if isinstance(value, str) and len(value) <= 40 else type(value).__name__
    raise ValueError(f"Espace colorimétrique de la source inconnu : {shown!r} (attendu : {', '.join(MATRIX_CHOICES)}).")


def clean_label(value: Any) -> str:
    """Une étiquette déjà normalisée (``bt709``…) ou ``""`` : la valeur d'exécution d'un média, jamais écrite dans un projet."""
    return value if isinstance(value, str) and value in LABELS else ""


def label_from_ffprobe(name: Any) -> str:
    """Étiquette normalisée du champ ``color_space`` de ``ffprobe`` : ``bt601`` / ``bt709`` / ``bt2020``, ``none`` (pas
    d'étiquette), ``other`` (étiquette que l'on ne convertit pas). Un texte démesuré ou d'un autre type est ``other``/``none``."""
    if not isinstance(name, str):
        return LABEL_NONE
    text = name.strip().lower()
    if text in _UNSET_WORDS:
        return LABEL_NONE
    if len(text) > _MAX_LABEL_LENGTH:
        return LABEL_OTHER
    return _FFMPEG_TO_LABEL.get(text, LABEL_OTHER)


def _positive(value: Any) -> int:
    """Une dimension utilisable : entier fini ≥ 0 (le reste vaut 0, donc « pas HD »)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    if isinstance(value, float) and not math.isfinite(value):
        return 0
    return max(0, int(value))


def is_hd(width: Any, height: Any) -> bool:
    """La règle de taille : ``height >= 720 or width >= 1280`` (dimensions illisibles, nulles ou négatives : non)."""
    return _positive(height) >= HD_MIN_HEIGHT or _positive(width) >= HD_MIN_WIDTH


def default_for_size(width: Any, height: Any) -> str:
    """La matrice d'un flux non étiqueté de cette taille."""
    return MATRIX_BT709 if is_hd(width, height) else MATRIX_BT601


def effective_matrix(label: Any = "", width: Any = 0, height: Any = 0, choice: Any = MATRIX_AUTO) -> str:
    """La matrice effective : ``bt601``, ``bt709`` ou ``bt2020``.

    Le choix forcé gagne ; sinon l'étiquette si elle est connue ; sinon la règle de taille. ``choice`` inconnu vaut ``auto``
    (les appelants qui reçoivent une valeur de l'utilisateur la valident avant, par :func:`normalize_choice`)."""
    if choice in (MATRIX_BT601, MATRIX_BT709):
        return str(choice)
    if label in KNOWN_LABELS:
        return str(label)
    if label == LABEL_OTHER:
        return MATRIX_BT601                      # comme avant : le moniteur ne sait pas mieux ; FFmpeg lit l'étiquette
    return default_for_size(width, height)


def ffmpeg_override(label: Any = "", width: Any = 0, height: Any = 0, choice: Any = MATRIX_AUTO) -> str:
    """Valeur de ``setparams=colorspace=`` à poser en tête de chaîne, ou ``""`` quand FFmpeg donne déjà la bonne matrice.

    FFmpeg lit l'étiquette du flux et, sans étiquette, prend le BT.601. Il faut donc intervenir seulement pour un flux
    non étiqueté que la règle veut en BT.709, ou quand l'utilisateur force une matrice que l'étiquette ne donne pas."""
    wanted = effective_matrix(label, width, height, choice)
    if choice in (MATRIX_BT601, MATRIX_BT709):
        return "" if label == wanted else _FFMPEG_NAMES[wanted]
    if label in KNOWN_LABELS or label == LABEL_OTHER:
        return ""
    return _FFMPEG_NAMES[MATRIX_BT709] if wanted == MATRIX_BT709 else ""


def setparams_filter(override: str) -> str:
    """Le filtre à insérer en tête de chaîne pour ``override`` (``""`` : aucun, y compris pour un nom que ce module n'a pas produit)."""
    return f"setparams=colorspace={override}" if override in _FFMPEG_NAMES.values() else ""


def cache_token(label: Any = "", width: Any = 0, height: Any = 0, choice: Any = MATRIX_AUTO) -> str:
    """Suffixe de clé de cache d'une image décodée : vide quand la lecture ne change pas par rapport à la règle 1 (pas de
    nouvelle clé, donc pas de recalcul inutile), sinon la matrice et le numéro de règle."""
    override = ffmpeg_override(label, width, height, choice)
    if not override:
        return ""
    return f"m{RULE_VERSION}-{override}"


def asset_matrix_inputs(asset: Any, probe: bool = True) -> tuple[str, int, int, str]:
    """``(étiquette, largeur, hauteur, choix)`` d'un média, l'étiquette étant sondée à la demande (et mémorisée) si elle
    n'est pas connue : un projet ouvert depuis un fichier n'en porte pas. ``probe=False`` (chemins de dessin, qui ne doivent
    jamais attendre ``ffprobe``) : une étiquette inconnue reste ``""``, donc « non étiqueté »."""
    if asset is None:
        return "", 0, 0, MATRIX_AUTO
    label = clean_label(getattr(asset, "color_space", ""))
    width, height = _positive(getattr(asset, "width", 0)), _positive(getattr(asset, "height", 0))
    choice = getattr(asset, "color_matrix", MATRIX_AUTO)
    choice = choice if choice in MATRIX_CHOICES else MATRIX_AUTO
    media_type = getattr(asset, "media_type", "video")
    if media_type != "video":
        return "", width, height, choice
    # La sonde n'est utile que si la taille ou le choix peuvent changer le résultat : SD en auto, c'est toujours ce que FFmpeg fait.
    if probe and not label and choice == MATRIX_AUTO and is_hd(width, height):
        label = _lazy_label(asset)
    return label, width, height, choice


def _lazy_label(asset: Any) -> str:
    from .media_probe import probe_color_label

    label = probe_color_label(str(getattr(asset, "path", "") or ""))
    if label:
        try:
            asset.color_space = label
        except AttributeError:
            pass
    return label


def asset_effective_matrix(asset: Any) -> str:
    """La matrice effective d'un média (``""`` pour un média qui n'est pas une vidéo)."""
    if asset is None or getattr(asset, "media_type", "video") != "video":
        return ""
    label, width, height, choice = asset_matrix_inputs(asset)
    return effective_matrix(label, width, height, choice)


def asset_ffmpeg_override(asset: Any) -> str:
    """Le ``setparams=colorspace=`` à poser sur un média (``""`` : rien ; image fixe et audio : jamais)."""
    if asset is None or getattr(asset, "media_type", "video") != "video":
        return ""
    return ffmpeg_override(*asset_matrix_inputs(asset))
