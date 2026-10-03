"""Textes du Multicam (fusionnés dans :mod:`ui.i18n`) : raccourcis, menus, historique, messages.

Un libellé d'historique est écrit dans la langue courante **au moment de l'enregistrement** (voir ``i18n_history``).
Le nom d'un angle est une donnée de l'utilisateur : il n'est jamais traduit.
"""

from __future__ import annotations


def _t(fr: str, en: str, es: str) -> dict[str, str]:
    return {"fr": fr, "en": en, "es": es}


MULTICAM_TRANSLATIONS: dict[str, dict[str, str]] = {
    # --- Raccourcis ---------------------------------------------------------------------------------------------------
    "shortcuts.category.multicam": _t("Multicam", "Multicam", "Multicámara"),
    "shortcuts.command.multicam_angle_1": _t("Angle 1", "Angle 1", "Ángulo 1"),
    "shortcuts.command.multicam_angle_2": _t("Angle 2", "Angle 2", "Ángulo 2"),
    "shortcuts.command.multicam_angle_3": _t("Angle 3", "Angle 3", "Ángulo 3"),
    "shortcuts.command.multicam_angle_4": _t("Angle 4", "Angle 4", "Ángulo 4"),
    "shortcuts.command.multicam_angle_5": _t("Angle 5", "Angle 5", "Ángulo 5"),
    "shortcuts.command.multicam_angle_6": _t("Angle 6", "Angle 6", "Ángulo 6"),
    "shortcuts.command.multicam_angle_7": _t("Angle 7", "Angle 7", "Ángulo 7"),
    "shortcuts.command.multicam_angle_8": _t("Angle 8", "Angle 8", "Ángulo 8"),
    "shortcuts.command.multicam_angle_9": _t("Angle 9", "Angle 9", "Ángulo 9"),
    "shortcuts.command.multicam_viewer": _t(
        "Afficher / masquer le moniteur Multicam", "Show / hide the Multicam monitor",
        "Mostrar / ocultar el monitor multicámara",
    ),
    "shortcuts.command.multicam_create": _t(
        "Créer une séquence Multicam…", "Create a Multicam sequence…", "Crear una secuencia multicámara…",
    ),
    "shortcuts.command.multicam_open_source": _t(
        "Ouvrir la source Multicam", "Open the Multicam source", "Abrir la fuente multicámara",
    ),
    "shortcuts.command.multicam_flatten": _t(
        "Aplatir le segment Multicam", "Flatten the Multicam segment", "Aplanar el segmento multicámara",
    ),
    # --- Menus ----------------------------------------------------------------------------------------------------------
    "multicam.menu.create": _t(
        "Créer une séquence Multicam…", "Create a Multicam sequence…", "Crear una secuencia multicámara…",
    ),
    "multicam.menu.open_source": _t(
        "Ouvrir la source Multicam", "Open the Multicam source", "Abrir la fuente multicámara",
    ),
    "multicam.menu.replace_with": _t(
        "Remplacer par l'angle", "Replace with angle", "Reemplazar por el ángulo",
    ),
    "multicam.menu.flatten": _t(
        "Aplatir le segment Multicam", "Flatten the Multicam segment", "Aplanar el segmento multicámara",
    ),
    "multicam.menu.angle_item": _t("{number}. {name}", "{number}. {name}", "{number}. {name}"),
    # --- Historique Annuler / Rétablir ------------------------------------------------------------------------------------
    "multicam.history.switch": _t("Angle {number} : {name}", "Angle {number}: {name}", "Ángulo {number}: {name}"),
    "multicam.history.replace": _t(
        "Remplacer par l'angle {number} : {name}", "Replace with angle {number}: {name}",
        "Reemplazar por el ángulo {number}: {name}",
    ),
    "multicam.history.flatten": _t(
        "Aplatir le segment Multicam", "Flatten the Multicam segment", "Aplanar el segmento multicámara",
    ),
    "multicam.history.create": _t(
        "Créer la séquence Multicam « {name} »", "Create the Multicam sequence “{name}”",
        "Crear la secuencia multicámara «{name}»",
    ),
    "multicam.history.offset": _t(
        "Décaler l'angle « {name} »", "Shift angle “{name}”", "Desplazar el ángulo «{name}»",
    ),
    "multicam.history.sync": _t(
        "Synchroniser les angles de « {name} »", "Synchronize the angles of “{name}”",
        "Sincronizar los ángulos de «{name}»",
    ),
    "multicam.history.audio_policy": _t(
        "Changer la politique audio Multicam", "Change the Multicam audio policy",
        "Cambiar la política de audio multicámara",
    ),
    "multicam.history.rename_angle": _t(
        "Renommer l'angle « {name} »", "Rename angle “{name}”", "Renombrar el ángulo «{name}»",
    ),
    "multicam.history.angle_color": _t(
        "Changer la couleur de l'angle « {name} »", "Change the colour of angle “{name}”",
        "Cambiar el color del ángulo «{name}»",
    ),
    "multicam.history.add_angle": _t(
        "Ajouter l'angle « {name} »", "Add angle “{name}”", "Añadir el ángulo «{name}»",
    ),
    "multicam.history.remove_angle": _t(
        "Supprimer l'angle « {name} »", "Delete angle “{name}”", "Eliminar el ángulo «{name}»",
    ),
    # --- Messages --------------------------------------------------------------------------------------------------------
    "multicam.message.no_segment": _t(
        "Aucun segment Multicam sous la tête de lecture.", "No Multicam segment under the playhead.",
        "Ningún segmento multicámara bajo el cabezal.",
    ),
    "multicam.message.no_such_angle": _t(
        "Cette source Multicam n'a que {count} angle(s).", "This Multicam source only has {count} angle(s).",
        "Esta fuente multicámara solo tiene {count} ángulo(s).",
    ),
    "multicam.message.not_multicam": _t(
        "Ce clip n'est pas un segment Multicam.", "This clip is not a Multicam segment.",
        "Este clip no es un segmento multicámara.",
    ),
    "multicam.message.select_two": _t(
        "Sélectionnez au moins deux clips pour créer une séquence Multicam.",
        "Select at least two clips to create a Multicam sequence.",
        "Seleccione al menos dos clips para crear una secuencia multicámara.",
    ),
    "multicam.status.angle_missing": _t(
        "Angle introuvable : le segment est rendu vide", "Angle not found: the segment renders empty",
        "Ángulo no encontrado: el segmento se muestra vacío",
    ),
    "multicam.tooltip.segment": _t(
        "{label} — angle {number} : {name}", "{label} — angle {number}: {name}", "{label} — ángulo {number}: {name}",
    ),
    "multicam.menu.viewer": _t(
        "Moniteur Multicam", "Multicam monitor", "Monitor multicámara",
    ),
    "multicam.dialog.create_title": _t(
        "Créer une séquence Multicam", "Create a Multicam sequence", "Crear una secuencia multicámara",
    ),
    "multicam.message.dialog_title": _t("Multicam", "Multicam", "Multicámara"),
}
