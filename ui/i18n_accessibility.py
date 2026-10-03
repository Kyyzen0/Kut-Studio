"""Noms accessibles des contrôles sans texte (fusionnés dans :mod:`ui.i18n`).

Un bouton-icône n'a que son infobulle : une technologie d'assistance (lecteur d'écran) et la navigation au clavier
ont besoin d'un **nom accessible** (``setAccessibleName``), traduit comme le reste de l'interface.
"""

from __future__ import annotations


def _t(fr: str, en: str, es: str) -> dict[str, str]:
    return {"fr": fr, "en": en, "es": es}


ACCESSIBILITY_TRANSLATIONS: dict[str, dict[str, str]] = {
    # Gestionnaire de tags : les trois boutons-icônes de chaque ligne.
    "a11y.tag.rename": _t("Renommer le tag", "Rename tag", "Renombrar la etiqueta"),
    "a11y.tag.recolor": _t("Changer la couleur du tag", "Change tag colour", "Cambiar el color de la etiqueta"),
    "a11y.tag.delete": _t("Supprimer le tag", "Delete tag", "Eliminar la etiqueta"),
    # Page d'export : la croix de fermeture.
    "a11y.export.close": _t("Fermer l'export", "Close export", "Cerrar la exportación"),
    # Inspecteur : navigation et bascule des images-clés d'une propriété animable.
    "a11y.keyframe.previous": _t(
        "{property} : image-clé précédente", "{property}: previous keyframe", "{property}: fotograma clave anterior"
    ),
    "a11y.keyframe.next": _t(
        "{property} : image-clé suivante", "{property}: next keyframe", "{property}: fotograma clave siguiente"
    ),
    "a11y.keyframe.toggle": _t(
        "{property} : ajouter ou retirer une image-clé",
        "{property}: add or remove a keyframe",
        "{property}: añadir o quitar un fotograma clave",
    ),
    # Inspecteur : grille d'alignement 3 × 3 du texte.
    "a11y.align.top_left": _t("Alignement : en haut à gauche", "Alignment: top left", "Alineación: arriba a la izquierda"),
    "a11y.align.top_center": _t("Alignement : en haut au centre", "Alignment: top centre", "Alineación: arriba al centro"),
    "a11y.align.top_right": _t("Alignement : en haut à droite", "Alignment: top right", "Alineación: arriba a la derecha"),
    "a11y.align.middle_left": _t("Alignement : au milieu à gauche", "Alignment: middle left", "Alineación: centro a la izquierda"),
    "a11y.align.middle_center": _t("Alignement : au centre", "Alignment: centre", "Alineación: centro"),
    "a11y.align.middle_right": _t("Alignement : au milieu à droite", "Alignment: middle right", "Alineación: centro a la derecha"),
    "a11y.align.bottom_left": _t("Alignement : en bas à gauche", "Alignment: bottom left", "Alineación: abajo a la izquierda"),
    "a11y.align.bottom_center": _t("Alignement : en bas au centre", "Alignment: bottom centre", "Alineación: abajo al centro"),
    "a11y.align.bottom_right": _t("Alignement : en bas à droite", "Alignment: bottom right", "Alineación: abajo a la derecha"),
}
