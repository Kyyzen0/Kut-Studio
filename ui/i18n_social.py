"""Textes de la vidéo sociale (fusionnés dans :mod:`ui.i18n`) : formats verticaux, cadrage, grille rythmique, texte
animé, effets lumineux, SFX, templates « Night » et export pour les réseaux.

Les identifiants techniques (formats, plateformes, presets) viennent de ``core`` ; seuls leurs libellés vivent ici.
"""

from __future__ import annotations


def _t(fr: str, en: str, es: str) -> dict[str, str]:
    return {"fr": fr, "en": en, "es": es}


SOCIAL_TRANSLATIONS: dict[str, dict[str, str]] = {
    # --- Cadrage d'un clip vidéo (inspecteur, transformation avancée) ---------------------------------------------
    "animation.property.fill": _t("Remplir le cadre", "Fill the frame", "Llenar el cuadro"),
    "animation.property.pan_x": _t("Cadrage X", "Framing X", "Encuadre X"),
    "animation.property.pan_y": _t("Cadrage Y", "Framing Y", "Encuadre Y"),
}
