"""Chrominance 4:2:0 de l'export : co-sitée en haut à gauche, interpolée (bilinéaire), comme AVFoundation.

Une image 4:2:0 n'a qu'un échantillon de chroma par bloc 2×2 de pixels. La conversion vers le RVB de swscale
(``format=rgba`` seul) le **recopie** sur tout le bloc. La version App Store (AVFoundation) interpole au contraire la
chroma, avec l'échantillon posé sur le pixel en haut à gauche du bloc (« co-sité »). Mesuré sur H.264 et HEVC 10 bits
en 4K contre le rendu d'AVFoundation (pixels à plus d'un niveau d'écart) : recopie 21 % et 90 %, cette règle 10 % (le
plancher d'AVFoundation lui-même en 8 bits) et 0 %. Détail et mesures : ``docs/gpu-preview.md``, « Chroma 4:2:0 ».

La conversion s'écrit en **une** étape, sans format intermédiaire de 16 bits (17,7 ms de CPU par image 1080p contre
8 ms) : ``bilinear`` pour le suréchantillonnage, ``full_chroma_int`` pour qu'il ait lieu avant la matrice, et
``accurate_rnd`` sans lequel swscale ignore ``full_chroma_int`` sur les sources 8 bits (octets identiques à la
recopie). Les trois sont nécessaires. ``in_h_chr_pos`` / ``in_v_chr_pos`` = 0 posent la chroma au coin haut gauche, quelle
que soit l'étiquette ``chroma_location`` du flux. Mesuré identique avec FFmpeg 9.0, 7.1 et le code C de swscale sans SIMD.

Cette chaîne ne sert **qu'ici** : ``accurate_rnd`` modifie l'arrondi de toute conversion YUV vers RVB qu'il touche.
"""

from __future__ import annotations

import re

CHROMA_SITING_SCALE = "scale=flags=bilinear+full_chroma_int+accurate_rnd:in_h_chr_pos=0:in_v_chr_pos=0"
"""Le filtre ``scale`` à taille égale qui convertit (suivi de ``format=rgba`` ou ``format=yuva444p``)."""

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
    prefix = CHROMA_SITING_SCALE + ("," if match.group("name").startswith("format=rgba") else ",format=rgba,")
    return chain[:match.start()] + prefix + chain[match.start():]
