"""Libellés de l'historique Annuler / Rétablir (fusionnés dans :mod:`ui.i18n`).

Chaque action d'édition enregistre un libellé dans l'historique (« Annuler : Dupliquer le clip »). Le libellé est
écrit dans la langue courante **au moment de l'enregistrement** : changer de langue ensuite ne réécrit pas les entrées
déjà enregistrées.
"""

from __future__ import annotations


def _t(fr: str, en: str, es: str) -> dict[str, str]:
    return {"fr": fr, "en": en, "es": es}


HISTORY_TRANSLATIONS: dict[str, dict[str, str]] = {
    # --- Images-clés et courbes d'animation --------------------------------------------------------------------------
    "history.keyframes.edit": _t("Modifier les images-clés", "Edit keyframes", "Modificar fotogramas clave"),
    "history.keyframes.tangents": _t("Modifier les tangentes", "Edit tangents", "Modificar tangentes"),
    "history.keyframes.add": _t("Ajouter une image-clé", "Add a keyframe", "Añadir un fotograma clave"),
    "history.keyframes.move_one": _t("Déplacer une image-clé", "Move a keyframe", "Mover un fotograma clave"),
    "history.keyframes.edit_one": _t("Modifier une image-clé", "Edit a keyframe", "Modificar un fotograma clave"),
    "history.keyframes.interpolation": _t(
        "Changer l'interpolation",
        "Change interpolation",
        "Cambiar la interpolación",
    ),
    "history.keyframes.remove": _t("Supprimer une image-clé", "Delete a keyframe", "Eliminar un fotograma clave"),
    "history.keyframes.move": _t("Déplacer des images-clés", "Move keyframes", "Mover fotogramas clave"),
    "history.keyframes.paste": _t("Coller des images-clés", "Paste keyframes", "Pegar fotogramas clave"),
    # --- Mouvement, vitesse et effets du clip ------------------------------------------------------------------------
    "history.speed.edit": _t("Modifier la vitesse", "Change speed", "Cambiar la velocidad"),
    "history.freeze.remove": _t("Supprimer l'arrêt sur image", "Delete freeze frame", "Eliminar el fotograma fijo"),
    "history.freeze.duration": _t(
        "Modifier la durée de l'arrêt sur image",
        "Change freeze frame duration",
        "Cambiar la duración del fotograma fijo",
    ),
    "history.effect.add": _t("Ajouter un effet", "Add an effect", "Añadir un efecto"),
    "history.effect.remove": _t("Supprimer un effet", "Delete an effect", "Eliminar un efecto"),
    "history.effect.move": _t("Réordonner un effet", "Reorder an effect", "Reordenar un efecto"),
    "history.effect.edit": _t("Modifier un effet", "Edit an effect", "Modificar un efecto"),
    "history.motion.edit": _t("Modifier le mouvement", "Edit motion", "Modificar el movimiento"),
    "history.motion.reset": _t("Réinitialiser le mouvement", "Reset motion", "Restablecer el movimiento"),
    "history.clip.reverse": _t("Inverser le clip", "Reverse clip", "Invertir el clip"),
    "history.clip.unreverse": _t("Désinverser le clip", "Un-reverse clip", "Quitar la inversión del clip"),
    "history.effect.enable": _t("Activer un effet", "Enable an effect", "Activar un efecto"),
    "history.effect.disable": _t("Désactiver un effet", "Disable an effect", "Desactivar un efecto"),
    # --- Audio : pistes, automation, ducking et effets ---------------------------------------------------------------
    "history.audio.track_role": _t("Modifier le rôle de la piste", "Change track role", "Cambiar el rol de la pista"),
    "history.audio.automation_add": _t(
        "Ajouter un point d'automation",
        "Add an automation point",
        "Añadir un punto de automatización",
    ),
    "history.audio.automation_remove": _t(
        "Supprimer un point d'automation",
        "Delete an automation point",
        "Eliminar un punto de automatización",
    ),
    "history.audio.automation_edit": _t(
        "Modifier un point d'automation",
        "Edit an automation point",
        "Modificar un punto de automatización",
    ),
    "history.audio.ducking_add": _t("Ajouter un ducking", "Add ducking", "Añadir ducking"),
    "history.audio.ducking_remove": _t("Supprimer un ducking", "Delete ducking", "Eliminar ducking"),
    "history.audio.automation_clear": _t(
        "Effacer la courbe de volume",
        "Clear the volume curve",
        "Borrar la curva de volumen",
    ),
    "history.audio.effect_add": _t("Ajouter un effet audio", "Add an audio effect", "Añadir un efecto de audio"),
    "history.audio.effect_remove": _t(
        "Supprimer un effet audio",
        "Delete an audio effect",
        "Eliminar un efecto de audio",
    ),
    "history.audio.effect_move": _t(
        "Réordonner un effet audio",
        "Reorder an audio effect",
        "Reordenar un efecto de audio",
    ),
    "history.audio.effect_edit": _t(
        "Modifier un effet audio",
        "Edit an audio effect",
        "Modificar un efecto de audio",
    ),
    # --- Étalonnage couleur ------------------------------------------------------------------------------------------
    "history.color.default": _t("Étalonnage", "Color grading", "Corrección de color"),
    "history.color.field": _t("Couleur : {field}", "Color: {field}", "Color: {field}"),
    "history.color.enable": _t("Activer l’étalonnage", "Enable color grading", "Activar la corrección de color"),
    "history.color.curve": _t("Courbe {channel}", "Curve {channel}", "Curva {channel}"),
    "history.color.grade": _t("Étalonner le clip", "Grade the clip", "Corregir el color del clip"),
    "history.color.reset": _t(
        "Réinitialiser l'étalonnage",
        "Reset color grading",
        "Restablecer la corrección de color",
    ),
    "history.color.apply_preset": _t(
        "Appliquer un preset d'étalonnage",
        "Apply a color grading preset",
        "Aplicar un preset de corrección de color",
    ),
    "history.color.save_preset": _t(
        "Enregistrer un preset couleur",
        "Save a color preset",
        "Guardar un preset de color",
    ),
    "history.color.import_lut": _t("Importer un LUT", "Import a LUT", "Importar un LUT"),
    "history.color.remove_lut": _t("Retirer le LUT", "Remove the LUT", "Quitar el LUT"),
    # --- Bibliothèque : dossiers, tags et médias ---------------------------------------------------------------------
    "history.library.folder_create": _t(
        "Créer le dossier « {name} »",
        "Create folder “{name}”",
        "Crear la carpeta «{name}»",
    ),
    "history.library.folder_rename": _t(
        "Renommer le dossier en « {name} »",
        "Rename folder to “{name}”",
        "Renombrar la carpeta a «{name}»",
    ),
    "history.library.folder_delete": _t(
        "Supprimer le dossier « {name} »",
        "Delete folder “{name}”",
        "Eliminar la carpeta «{name}»",
    ),
    "history.library.asset_move": _t(
        "Déplacer le média dans un dossier",
        "Move media to a folder",
        "Mover el medio a una carpeta",
    ),
    "history.library.asset_relink": _t(
        "Relier le fichier d'un média",
        "Relink a media file",
        "Reenlazar el archivo de un medio",
    ),
    "history.library.asset_rename": _t("Renommer un média", "Rename a media", "Renombrar un medio"),
    "history.library.asset_delete": _t("Supprimer un média", "Delete a media", "Eliminar un medio"),
    "history.library.tags_edit": _t(
        "Modifier les tags de la bibliothèque",
        "Edit library tags",
        "Modificar las etiquetas de la biblioteca",
    ),
    "history.library.asset_tag": _t("Tagger un média", "Tag a media", "Etiquetar un medio"),
    "history.library.asset_untag": _t(
        "Retirer un tag d'un média",
        "Remove a tag from a media",
        "Quitar una etiqueta de un medio",
    ),
    "history.media.drop": _t(
        "Déposer « {name} » sur {track}",
        "Drop “{name}” on {track}",
        "Soltar «{name}» en {track}",
    ),
    "history.media.import": _t("Importer le média « {name} »", "Import media “{name}”", "Importar el medio «{name}»"),
    "history.media.add_clip": _t("Ajouter le clip « {name} »", "Add clip “{name}”", "Añadir el clip «{name}»"),
    "history.guides.add": _t("Ajouter un guide", "Add a guide", "Añadir una guía"),
    "history.layer.advanced_keyframe": _t(
        "Image-clé (transformation avancée)",
        "Keyframe (advanced transform)",
        "Fotograma clave (transformación avanzada)",
    ),
    "history.layer.compositing": _t("Modifier le compositing", "Edit compositing", "Modificar el compositing"),
    "history.layer.add": _t("Ajouter un calque", "Add a layer", "Añadir una capa"),
    "history.layer.rename": _t("Renommer le calque", "Rename layer", "Renombrar la capa"),
    "history.layer.motion_blur": _t(
        "Flou de mouvement du calque",
        "Layer motion blur",
        "Desenfoque de movimiento de la capa",
    ),
    "history.layer.reorder": _t("Réordonner les calques", "Reorder layers", "Reordenar las capas"),
    "history.layer.duplicate": _t("Dupliquer les calques", "Duplicate layers", "Duplicar las capas"),
    "history.layer.delete": _t("Supprimer les calques", "Delete layers", "Eliminar las capas"),
    "history.layer.paste": _t("Coller les calques", "Paste layers", "Pegar las capas"),
    "history.layer.preset": _t("Preset « {name} »", "Preset “{name}”", "Preset «{name}»"),
    "history.layer.sequence_motion_blur": _t(
        "Flou de mouvement de la séquence",
        "Sequence motion blur",
        "Desenfoque de movimiento de la secuencia",
    ),
    "history.layer.motion_blur_settings": _t(
        "Réglages du flou de mouvement",
        "Motion blur settings",
        "Ajustes del desenfoque de movimiento",
    ),
    "history.layer.advanced_transform": _t(
        "Modifier la transformation avancée",
        "Edit advanced transform",
        "Modificar la transformación avanzada",
    ),
    "history.layer.show": _t("Afficher le calque", "Show layer", "Mostrar la capa"),
    "history.layer.hide": _t("Masquer le calque", "Hide layer", "Ocultar la capa"),
    "history.layer.lock": _t("Verrouiller le calque", "Lock layer", "Bloquear la capa"),
    "history.layer.unlock": _t("Déverrouiller le calque", "Unlock layer", "Desbloquear la capa"),
    "history.layer.reparent": _t("Changer le parent", "Change parent", "Cambiar el padre"),
    "history.layer.unparent": _t("Détacher du parent", "Detach from parent", "Separar del padre"),
    # --- Motion graphics : guides et calques -------------------------------------------------------------------------
    "history.guides.move": _t("Déplacer un guide", "Move a guide", "Mover una guía"),
    # --- Presets, transitions et sous-titres -------------------------------------------------------------------------
    "history.preset.effects_apply": _t(
        "Appliquer un preset d'effets",
        "Apply an effects preset",
        "Aplicar un preset de efectos",
    ),
    "history.preset.audio_apply": _t(
        "Appliquer un preset d'effet audio",
        "Apply an audio effect preset",
        "Aplicar un preset de efecto de audio",
    ),
    "history.transition.add": _t("Ajouter une transition", "Add a transition", "Añadir una transición"),
    "history.subtitle.apply_template": _t(
        "Appliquer un modèle de sous-titre",
        "Apply a subtitle template",
        "Aplicar una plantilla de subtítulo",
    ),
    "history.recording.take": _t("Enregistrer une prise", "Record a take", "Grabar una toma"),
    "history.subtitle.edit": _t("Modifier un sous-titre", "Edit a subtitle", "Modificar un subtítulo"),
    "history.subtitle.import_srt": _t(
        "Importer le SRT ({count} sous-titres)",
        "Import SRT ({count} subtitles)",
        "Importar SRT ({count} subtítulos)",
    ),
    "history.subtitle.add": _t("Ajouter un sous-titre", "Add a subtitle", "Añadir un subtítulo"),
    "history.graphic.add": _t("Ajouter un calque graphique", "Add a graphic layer", "Añadir una capa gráfica"),
    "history.graphic.import_image": _t(
        "Importer une image graphique",
        "Import a graphic image",
        "Importar una imagen gráfica",
    ),
    "history.graphic.edit": _t("Modifier un calque graphique", "Edit a graphic layer", "Modificar una capa gráfica"),
    "history.subtitle.style": _t(
        "Modifier le style du sous-titre",
        "Edit subtitle style",
        "Modificar el estilo del subtítulo",
    ),
    "history.subtitle.style_reset": _t(
        "Réinitialiser le style du sous-titre",
        "Reset subtitle style",
        "Restablecer el estilo del subtítulo",
    ),
    # --- Montage : clips, transitions, marqueurs et pistes -----------------------------------------------------------
    "history.clip.delete_selection": _t("Supprimer la sélection", "Delete selection", "Eliminar la selección"),
    "history.clip.move": _t("Déplacer le clip", "Move clip", "Mover el clip"),
    "history.clip.trim_left": _t("Trim gauche", "Trim left", "Recortar a la izquierda"),
    "history.clip.trim_right": _t("Trim droit", "Trim right", "Recortar a la derecha"),
    "history.clip.cut": _t("Couper le clip", "Cut clip", "Cortar el clip"),
    "history.clip.scene_cut": _t("Découper aux changements de plan", "Cut at scene changes", "Cortar en cambios de plan"),
    "history.transition.edit": _t("Modifier une transition", "Edit a transition", "Modificar una transición"),
    "history.transition.delete": _t("Supprimer une transition", "Delete a transition", "Eliminar una transición"),
    "history.tool.slip": _t("Slip", "Slip", "Slip"),
    "history.tool.slide": _t("Slide", "Slide", "Slide"),
    "history.tool.roll": _t("Roll", "Roll", "Roll"),
    "history.clip.move_many": _t("Déplacer les clips", "Move clips", "Mover los clips"),
    "history.marker.rename": _t("Renommer un marqueur", "Rename a marker", "Renombrar un marcador"),
    "history.clip.disable": _t("Désactiver le clip", "Disable clip", "Desactivar el clip"),
    "history.clip.enable": _t("Activer le clip", "Enable clip", "Activar el clip"),
    "history.track.solo": _t("Solo de piste", "Track solo", "Solo de pista"),
    "history.track.arm": _t("Armer la piste", "Arm track", "Armar la pista"),
    "history.track.height": _t("Hauteur de piste", "Track height", "Altura de la pista"),
    "history.track.collapse": _t("Réduire la piste", "Collapse track", "Contraer la pista"),
    "history.animation.disable": _t("Désactiver l'animation", "Disable animation", "Desactivar animación"),
}

__all__ = ["HISTORY_TRANSLATIONS"]
