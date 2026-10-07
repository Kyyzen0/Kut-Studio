"""Décodage tolérant des fichiers texte importés (SRT, LUT ``.cube``…).

Les fichiers produits par d'autres outils ne sont pas toujours en UTF-8 « nu » : le Bloc-notes Windows et Subtitle
Edit ajoutent souvent une marque d'ordre des octets (BOM), proposent l'UTF-16 (« Unicode ») et les vieux SRT sont
fréquemment en Windows-1252. :func:`decode_text_bytes` essaie, dans l'ordre :

1. une BOM explicite (UTF-8, UTF-16 LE/BE), retirée du texte ;
2. l'UTF-8 strict : un fichier Windows-1252 accentué n'est presque jamais de l'UTF-8 valide, l'ordre est donc sûr ;
3. Windows-1252 ;
4. Latin-1, qui ne peut pas échouer (Windows-1252 laisse 5 octets indéfinis).

Le contenu binaire n'est pas détecté ici : c'est au parseur appelant de refuser un texte qui n'a pas la bonne forme.
"""

from __future__ import annotations

import codecs

_BOMS: tuple[tuple[bytes, str], ...] = (
    (codecs.BOM_UTF8, "utf-8"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
)


def decode_text_bytes(data: bytes) -> str:
    """Décode le contenu d'un fichier texte importé, BOM retirée (voir le docstring du module)."""
    for bom, encoding in _BOMS:
        if data.startswith(bom):
            return data[len(bom):].decode(encoding, errors="replace")
    for encoding in ("utf-8", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")
