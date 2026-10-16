"""Chrominance 4:2:0 de l'export : co-sitée en haut à gauche, interpolée (bilinéaire), comme AVFoundation.

Une image 4:2:0 n'a qu'un échantillon de chroma par bloc 2×2 de pixels. La conversion vers le RVB de swscale
(``format=rgba`` seul) le **recopie** sur tout le bloc. La version App Store (AVFoundation) interpole au contraire la
chroma, avec l'échantillon posé sur le pixel en haut à gauche du bloc (« co-sité »). Mesuré sur H.264 et HEVC 10 bits
en 4K contre le rendu d'AVFoundation (pixels à plus d'un niveau d'écart) : recopie 21 % et 90 %, cette règle 10 % (le
plancher d'AVFoundation lui-même en 8 bits) et 0 %. Détail et mesures : ``docs/gpu-preview.md``, « Chroma 4:2:0 ».

La conversion passe par un **format intermédiaire de 16 bits** : ``scale`` (co-sité, bilinéaire) vers
``yuva444p16le``, puis ``format=rgba``. ``yuva`` et non ``yuv`` : sans canal alpha, un calque RVBA (fond transparent,
composition vide) perdait son alpha et sortait en noir opaque. ``bilinear`` sert au suréchantillonnage,
``full_chroma_int`` pour qu'il ait lieu avant la matrice, et ``accurate_rnd`` sans lequel swscale ignore
``full_chroma_int`` sur les sources 8 bits (octets identiques à la recopie). Les trois sont nécessaires.
``in_h_chr_pos`` / ``in_v_chr_pos`` = 0 posent la chroma au coin haut gauche, quelle que soit l'étiquette
``chroma_location`` du flux.

Pourquoi le 16 bits. Mesuré sur la même entrée déterministe (320×180 yuv420p, bruit graine 7) contre une référence
flottante co-sitée BT.601 : la chaîne directe (``scale=…,format=rgba``) donne un écart max de 1 (moyenne 0,036) avec
FFmpeg 9.0.2, mais **un écart max de 196 sur 0,9 % des pixels** avec FFmpeg 6.1.1 et 7.1.5, y compris avec
``-cpuflags 0`` : le défaut est dans swscale de ces versions, pas dans le SIMD. Avec ``yuv444p16le`` entre les deux,
l'écart max est de 1 (moyenne 0,036) sur les trois versions ; ``format=gbrp`` donne la même chose ; ``yuv444p`` en
8 bits ne suffit pas (écart max 2, moyenne 0,25). ``yuva444p16le`` (variante retenue) n'a été mesurée que sur
FFmpeg 9.0.2 (octets identiques à ``yuv444p16le``), pas sur 6.1 / 7.1. Le coût est dans ``docs/gpu-preview.md``.

Cette chaîne ne sert **qu'ici** : ``accurate_rnd`` modifie l'arrondi de toute conversion YUV vers RVB qu'il touche.
"""

from __future__ import annotations

import re

CHROMA_SITING_SCALE = "scale=flags=bilinear+full_chroma_int+accurate_rnd:in_h_chr_pos=0:in_v_chr_pos=0"
"""Le filtre ``scale`` à taille égale qui suréchantillonne la chroma (co-sitée, bilinéaire). Seul, il ne suffit pas
vers du RVB : voir :data:`CHROMA_SITING_444` et :data:`CHROMA_SITING_TO_RGBA`. Le heat haze (sortie ``yuva444p``,
sans conversion en RVB) l'emploie tel quel."""

CHROMA_SITING_444 = f"{CHROMA_SITING_SCALE},format=yuva444p16le"
"""``scale`` co-sité puis passage en 4:4:4 de 16 bits ; à faire suivre de ``format=rgba``."""

CHROMA_SITING_TO_RGBA = f"{CHROMA_SITING_444},format=rgba"
"""La conversion complète, 4:2:0 vers RVB : un seul ``yuva444p16le``, jamais un ``scale`` seul devant ``format=rgba``."""

_RGB_BOUNDARY = re.compile(
    r"(?:^|(?<=[,;\]]))(?P<name>format=rgba\b|colorbalance=|colorchannelmixer=|lutrgb=|curves=|colorlevels=|"
    r"lut3d=|colortemperature=)"
)
"""Premier filtre d'une chaîne qui impose du RVB : FFmpeg y insère sa conversion, avec ses réglages par défaut
(recopie de la chroma). Les autres filtres de la chaîne (``eq``, ``hue``, ``gblur``…) travaillent en YUV."""


def siting_before_rgb(chain: str) -> str:
    """``chain`` avec la conversion co-sitée posée juste avant son premier filtre RVB.

    Rien n'est changé s'il n'y en a pas. Un seul passage est inséré : la suite de la chaîne est déjà en RVB (ou en
    4:4:4 après un ``format=yuva444p`` d'effet), où ``accurate_rnd`` changerait l'arrondi sans raison.
    """
    match = _RGB_BOUNDARY.search(chain)
    if match is None:
        return chain
    # Devant un ``format=rgba`` existant, c'est lui qui termine la conversion : pas de second ``format=rgba``.
    prefix = CHROMA_SITING_444 + "," if match.group("name").startswith("format=rgba") else CHROMA_SITING_TO_RGBA + ","
    return chain[:match.start()] + prefix + chain[match.start():]
