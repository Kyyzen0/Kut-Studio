"""Traductions des poignées de masque du moniteur (fusionnées dans :mod:`ui.i18n`)."""

from __future__ import annotations


def _t(fr: str, en: str, es: str) -> dict[str, str]:
    return {"fr": fr, "en": en, "es": es}


MASK_EDIT_TRANSLATIONS: dict[str, dict[str, str]] = {
    "mask.edit.toggle": _t(
        "Poignées dans le moniteur", "Handles in the monitor", "Controles en el monitor",
    ),
    "mask.edit.toggle_tip": _t(
        "Affiche ce masque et ses poignées dans le moniteur, à la place de celles du clip. Décochez pour retrouver les poignées du clip.",
        "Shows this mask and its handles in the monitor instead of the clip's. Clear to get the clip's handles back.",
        "Muestra esta máscara y sus controles en el monitor en lugar de los del clip. Desmarque para recuperar los del clip.",
    ),
    "history.mask.drag": _t("Modifier le masque", "Edit mask", "Modificar la máscara"),
    "mask.edit.locked": _t(
        "Ce clip est verrouillé : le masque n'a pas été modifié.",
        "This clip is locked: the mask was not changed.",
        "Este clip está bloqueado: la máscara no se ha modificado.",
    ),
    "mask.edit.unavailable": _t(
        "Ce masque ne peut pas être modifié ici (clip absent de l'image, masqué ou hors de sa durée).",
        "This mask cannot be edited here (clip not in the picture, hidden or outside its duration).",
        "Esta máscara no se puede modificar aquí (clip ausente de la imagen, oculto o fuera de su duración).",
    ),
}

__all__ = ["MASK_EDIT_TRANSLATIONS"]
