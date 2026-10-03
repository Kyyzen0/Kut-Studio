"""Boîtes de dialogue, messages d'erreur et messages de la barre d'état (fusionnés dans :mod:`ui.i18n`).

Titres et textes des ``QMessageBox`` / ``QInputDialog`` / ``QFileDialog`` de la fenêtre principale, refus d'opérations
et confirmations affichés dans la barre d'état. Les messages venus du cœur (``str(exc)``) restent ceux de
l'exception : voir la dette « erreurs du cœur » dans ``docs/i18n.md``.
"""

from __future__ import annotations


def _t(fr: str, en: str, es: str) -> dict[str, str]:
    return {"fr": fr, "en": en, "es": es}


DIALOGS_TRANSLATIONS: dict[str, dict[str, str]] = {
    # --- Projet : enregistrer, ouvrir, récupérer ---------------------------------------------------------------------
    "dialog.save.failed_title": _t("Enregistrement impossible", "Cannot save", "No se pudo guardar"),
    "dialog.save.failed_text": _t(
        "Impossible d'enregistrer le projet :\n\n{error}",
        "Could not save the project:\n\n{error}",
        "No se pudo guardar el proyecto:\n\n{error}",
    ),
    "dialog.save.as_title": _t("Enregistrer le projet sous...", "Save project as...", "Guardar el proyecto como..."),
    "dialog.filter.project": _t(
        "Projets Kut-Studio (*.kut)",
        "Kut-Studio projects (*.kut)",
        "Proyectos de Kut-Studio (*.kut)",
    ),
    "dialog.open.title": _t(
        "Ouvrir un projet Kut-Studio",
        "Open a Kut-Studio project",
        "Abrir un proyecto de Kut-Studio",
    ),
    "dialog.open.failed_title": _t(
        "Impossible d'ouvrir le projet",
        "Cannot open the project",
        "No se pudo abrir el proyecto",
    ),
    "dialog.open.failed_text": _t(
        "Le fichier {path} n'a pas pu être ouvert :\n\n{error}",
        "The file {path} could not be opened:\n\n{error}",
        "No se pudo abrir el archivo {path}:\n\n{error}",
    ),
    "dialog.recover.title": _t("Récupération", "Recovery", "Recuperación"),
    "dialog.recover.text": _t(
        "Une sauvegarde automatique plus récente que ce projet a été trouvée.\n\nVoulez-vous la restaurer ?",
        "An automatic backup more recent than this project was found.\n\nDo you want to restore it?",
        "Se encontró una copia de seguridad automática más reciente que este proyecto.\n\n¿Quiere restaurarla?",
    ),
    "dialog.recover.failed_title": _t("Récupération impossible", "Recovery failed", "No se pudo recuperar"),
    "dialog.recover.failed_text": _t(
        "La sauvegarde automatique n'a pas pu être lue :\n\n{error}",
        "The automatic backup could not be read:\n\n{error}",
        "No se pudo leer la copia de seguridad automática:\n\n{error}",
    ),
    "dialog.filter.video": _t("Vidéos (*.{ext})", "Videos (*.{ext})", "Vídeos (*.{ext})"),
    # --- Import de médias et ajout à la timeline ---------------------------------------------------------------------
    "dialog.import.title": _t("Importer des médias", "Import media", "Importar medios"),
    "dialog.filter.media": _t(
        "Médias (*.mp4 *.mov *.avi *.mkv *.webm *.mp3 *.wav *.m4a *.aac *.flac *.ogg)",
        "Media (*.mp4 *.mov *.avi *.mkv *.webm *.mp3 *.wav *.m4a *.aac *.flac *.ogg)",
        "Medios (*.mp4 *.mov *.avi *.mkv *.webm *.mp3 *.wav *.m4a *.aac *.flac *.ogg)",
    ),
    "dialog.import.failed_title": _t("Import impossible", "Import failed", "No se pudo importar"),
    "dialog.import.failed_text": _t(
        "Impossible d'importer le média :\n\n{path}\n\n{error}",
        "Could not import the media:\n\n{path}\n\n{error}",
        "No se pudo importar el medio:\n\n{path}\n\n{error}",
    ),
    "dialog.add.failed_title": _t("Ajout impossible", "Cannot add", "No se pudo añadir"),
    "dialog.add.asset_missing": _t(
        "Média '{asset}' introuvable dans le projet.",
        "Media '{asset}' not found in the project.",
        "Medio '{asset}' no encontrado en el proyecto.",
    ),
    "dialog.add.bad_type": _t(
        "Le type de média '{media_type}' ne peut pas être ajouté à la timeline depuis le panneau de bibliothèque.",
        "The media type '{media_type}' cannot be added to the timeline from the library panel.",
        "El tipo de medio '{media_type}' no se puede añadir a la línea de tiempo desde el panel de la biblioteca.",
    ),
    "dialog.add.track_missing": _t(
        "La piste '{track}' est absente du projet courant.",
        "Track '{track}' is not in the current project.",
        "La pista '{track}' no está en el proyecto actual.",
    ),
    "dialog.add.failed_text": _t(
        "Impossible d'ajouter le média à la timeline :\n\n{error}",
        "Could not add the media to the timeline:\n\n{error}",
        "No se pudo añadir el medio a la línea de tiempo:\n\n{error}",
    ),
    # --- Bibliothèque : dossiers, tags, médias -----------------------------------------------------------------------
    "dialog.title.folder": _t("Dossier", "Folder", "Carpeta"),
    "dialog.folder.delete_title": _t("Supprimer le dossier", "Delete folder", "Eliminar carpeta"),
    "dialog.folder.delete_text": _t(
        "Supprimer le dossier « {name} » et ramener ses médias à la racine ?",
        "Delete folder “{name}” and move its media back to the root?",
        "¿Eliminar la carpeta «{name}» y devolver sus medios a la raíz?",
    ),
    "dialog.relink.title": _t("Relier le média…", "Relink media…", "Reenlazar el medio…"),
    "dialog.filter.media_images": _t(
        "Médias (*.mp4 *.mov *.avi *.mkv *.webm *.mp3 *.wav *.m4a *.aac *.flac *.ogg *.png *.jpg *.jpeg)",
        "Media (*.mp4 *.mov *.avi *.mkv *.webm *.mp3 *.wav *.m4a *.aac *.flac *.ogg *.png *.jpg *.jpeg)",
        "Medios (*.mp4 *.mov *.avi *.mkv *.webm *.mp3 *.wav *.m4a *.aac *.flac *.ogg *.png *.jpg *.jpeg)",
    ),
    "status.relink.differs": _t(
        "Média relié, mais le fichier est différent : ",
        "Media relinked, but the file is different: ",
        "Medio reenlazado, pero el archivo es distinto: ",
    ),
    "dialog.media.delete_title": _t("Supprimer le média", "Delete media", "Eliminar medio"),
    "dialog.media.delete_text": _t(
        "Supprimer « {name} » de la bibliothèque ?\nLes clips qui l'utilisent resteront sur la timeline (ils pointeront vers un média absent).",
        "Delete “{name}” from the library?\nClips using it will stay on the timeline (they will point to a missing media).",
        "¿Eliminar «{name}» de la biblioteca?\nLos clips que lo usan seguirán en la línea de tiempo (apuntarán a un medio ausente).",
    ),
    "dialog.tags.title": _t("Gestionnaire de tags", "Tag manager", "Gestor de etiquetas"),
    "dialog.tags.name_empty": _t(
        "Le nom du tag ne peut pas être vide.",
        "The tag name cannot be empty.",
        "El nombre de la etiqueta no puede estar vacío.",
    ),
    "dialog.tags.rename_title": _t("Renommer le tag", "Rename tag", "Renombrar etiqueta"),
    "dialog.new_name_label": _t("Nouveau nom :", "New name:", "Nuevo nombre:"),
    "dialog.title.tags": _t("Tags", "Tags", "Etiquetas"),
    "dialog.folder.name_empty": _t(
        "Le nom du dossier ne peut pas être vide.",
        "The folder name cannot be empty.",
        "El nombre de la carpeta no puede estar vacío.",
    ),
    "dialog.folder.name_too_long": _t(
        "Le nom du dossier est trop long (max 48 caractères).",
        "The folder name is too long (max. 48 characters).",
        "El nombre de la carpeta es demasiado largo (máx. 48 caracteres).",
    ),
    "dialog.folder.new_title": _t("Nouveau dossier", "New folder", "Nueva carpeta"),
    "dialog.folder.name_label": _t("Nom du dossier :", "Folder name:", "Nombre de la carpeta:"),
    # --- Mixage, effets et presets -----------------------------------------------------------------------------------
    "dialog.title.mix": _t("Mixage", "Mixing", "Mezcla"),
    "dialog.title.audio_effect": _t("Effet audio", "Audio effect", "Efecto de audio"),
    "dialog.title.effects_preset": _t("Preset d'effets", "Effects preset", "Preset de efectos"),
    "dialog.preset.no_audio_effect": _t(
        "Ce clip n'a aucun effet audio à enregistrer.",
        "This clip has no audio effect to save.",
        "Este clip no tiene ningún efecto de audio que guardar.",
    ),
    "dialog.preset.save_audio_title": _t(
        "Enregistrer un preset audio",
        "Save an audio preset",
        "Guardar un preset de audio",
    ),
    "dialog.preset.name_label": _t("Nom du preset :", "Preset name:", "Nombre del preset:"),
    "dialog.preset.delete_title": _t("Supprimer le preset", "Delete preset", "Eliminar preset"),
    "dialog.preset.delete_text": _t(
        "Supprimer le preset « {name} » ?",
        "Delete preset “{name}”?",
        "¿Eliminar el preset «{name}»?",
    ),
    "status.transition.same_track": _t(
        "Les clips doivent être placés sur la même piste.",
        "Clips must be on the same track.",
        "Los clips deben estar en la misma pista.",
    ),
    "status.transition.refused": _t(
        "Transition refusée : {error}",
        "Transition refused: {error}",
        "Transición rechazada: {error}",
    ),
    "status.transition.added_named": _t(
        "Transition « {name} » ajoutée.",
        "Transition “{name}” added.",
        "Transición «{name}» añadida.",
    ),
    "status.transition.added": _t("Transition ajoutée.", "Transition added.", "Transición añadida."),
    "status.template.saved": _t(
        "Modèle « {name} » enregistré.",
        "Template “{name}” saved.",
        "Plantilla «{name}» guardada.",
    ),
    "dialog.title.color_preset": _t("Preset couleur", "Color preset", "Preset de color"),
    "dialog.lut.invalid": _t("LUT invalide : {error}", "Invalid LUT: {error}", "LUT no válida: {error}"),
    "dialog.lut.import_title": _t("Importer une LUT", "Import a LUT", "Importar una LUT"),
    "dialog.title.effects": _t("Effets", "Effects", "Efectos"),
    # --- Enregistrement audio, sous-titres et graphiques -------------------------------------------------------------
    "dialog.title.recording": _t("Enregistrement", "Recording", "Grabación"),
    "dialog.recording.arm_first": _t(
        "Armez une piste audio avant d'enregistrer.",
        "Arm an audio track before recording.",
        "Arme una pista de audio antes de grabar.",
    ),
    "dialog.recording.too_short": _t(
        "L'enregistrement est trop court pour devenir un clip.",
        "The recording is too short to become a clip.",
        "La grabación es demasiado corta para convertirse en un clip.",
    ),
    "dialog.subtitle.add_failed_title": _t(
        "Sous-titre impossible",
        "Cannot add subtitle",
        "No se pudo añadir el subtítulo",
    ),
    "dialog.subtitle.add_failed_text": _t(
        "Impossible d'ajouter le sous-titre :\n\n{error}",
        "Could not add the subtitle:\n\n{error}",
        "No se pudo añadir el subtítulo:\n\n{error}",
    ),
    "dialog.title.graphic": _t("Graphique", "Graphic", "Gráfico"),
    "dialog.filter.images": _t(
        "Images (*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff)",
        "Images (*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff)",
        "Imágenes (*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff)",
    ),
    "status.graphic.refused": _t(
        "Modification graphique refusée : {error}",
        "Graphic edit refused: {error}",
        "Modificación gráfica rechazada: {error}",
    ),
    "dialog.subtitle.import_title": _t("Importer des sous-titres", "Import subtitles", "Importar subtítulos"),
    "dialog.filter.subtitles": _t("Sous-titres (*.srt)", "Subtitles (*.srt)", "Subtítulos (*.srt)"),
    "dialog.subtitle.import_failed_title": _t(
        "Import SRT impossible",
        "SRT import failed",
        "No se pudo importar el SRT",
    ),
    "dialog.subtitle.import_failed_text": _t(
        "Impossible d'importer les sous-titres :\n\n{error}",
        "Could not import the subtitles:\n\n{error}",
        "No se pudieron importar los subtítulos:\n\n{error}",
    ),
    "dialog.subtitle.export_title": _t("Enregistrer les sous-titres", "Save subtitles", "Guardar subtítulos"),
    "dialog.subtitle.export_failed_title": _t(
        "Export SRT impossible",
        "SRT export failed",
        "No se pudo exportar el SRT",
    ),
    "dialog.subtitle.export_failed_text": _t(
        "Impossible d'enregistrer les sous-titres :\n\n{error}",
        "Could not save the subtitles:\n\n{error}",
        "No se pudieron guardar los subtítulos:\n\n{error}",
    ),
    # --- Montage : clips, transitions, marqueurs ---------------------------------------------------------------------
    "dialog.clip.duplicate_failed_title": _t("Duplication impossible", "Cannot duplicate", "No se pudo duplicar"),
    "dialog.clip.duplicate_failed_text": _t(
        "Impossible de dupliquer le clip :\n\n{error}",
        "Could not duplicate the clip:\n\n{error}",
        "No se pudo duplicar el clip:\n\n{error}",
    ),
    "dialog.clip.delete_failed_title": _t("Suppression impossible", "Cannot delete", "No se pudo eliminar"),
    "dialog.clip.delete_failed_text": _t(
        "Impossible de supprimer le clip :\n\n{error}",
        "Could not delete the clip:\n\n{error}",
        "No se pudo eliminar el clip:\n\n{error}",
    ),
    "status.clip.cut_tracking": _t(
        "Ce clip porte le tracking de {count} autre(s) clip(s) : leur suivi s'arrête à la coupe. Reliez-les à la partie droite pour qu'il continue.",
        "This clip carries the tracking of {count} other clip(s): their tracking stops at the cut. Link them to the right part so it continues.",
        "Este clip lleva el seguimiento de {count} otro(s) clip(s): su seguimiento se detiene en el corte. Vincúlelos a la parte derecha para que continúe.",
    ),
    "status.transition.edit_refused": _t(
        "Modification refusée : {error}",
        "Edit refused: {error}",
        "Modificación rechazada: {error}",
    ),
    "status.transition.deleted": _t("Transition supprimée.", "Transition deleted.", "Transición eliminada."),
    "dialog.marker.name_label": _t("Nom du marqueur", "Marker name", "Nombre del marcador"),
    "status.clip.cut_none": _t(
        "Aucun clip sélectionné à couper",
        "No clip selected to cut",
        "Ningún clip seleccionado para cortar",
    ),
    # --- Motion graphics : presets, attributs, flou de mouvement -----------------------------------------------------
    "dialog.layers.attr_transform": _t("Transform", "Transform", "Transformación"),
    "status.layers.group_select": _t(
        "Sélectionnez au moins un calque à grouper.",
        "Select at least one layer to group.",
        "Seleccione al menos una capa para agrupar.",
    ),
    "status.layers.copied": _t(
        "{count} calque(s) copié(s).",
        "{count} layer(s) copied.",
        "{count} capa(s) copiada(s).",
    ),
    "status.layers.attributes_copied": _t(
        "Transform, masques, effets et animation copiés.",
        "Transform, masks, effects and animation copied.",
        "Transformación, máscaras, efectos y animación copiados.",
    ),
    "status.layers.copy_first": _t(
        "Copiez d'abord les attributs d'un calque.",
        "Copy a layer's attributes first.",
        "Copie primero los atributos de una capa.",
    ),
    "dialog.layers.attr_effects": _t("Effets et couleur", "Effects and color", "Efectos y color"),
    "dialog.layers.attr_masks": _t("Masques", "Masks", "Máscaras"),
    "dialog.layers.attr_keyframes": _t(
        "Animation (images-clés)",
        "Animation (keyframes)",
        "Animación (fotogramas clave)",
    ),
    "dialog.layers.paste_label": _t("Attributs à coller :", "Attributes to paste:", "Atributos que pegar:"),
    "dialog.layers.attr_all": _t("Tout", "All", "Todo"),
    "status.layers.save_select": _t(
        "Sélectionnez les calques à enregistrer.",
        "Select the layers to save.",
        "Seleccione las capas que guardar.",
    ),
    "status.preset.saved": _t("Preset « {name} » enregistré.", "Preset “{name}” saved.", "Preset «{name}» guardado."),
    "dialog.motion_blur.shutter": _t("Angle d'obturation (°) :", "Shutter angle (°):", "Ángulo de obturación (°):"),
    "dialog.motion_blur.samples": _t(
        "Échantillons à l'export (2–32) :",
        "Samples at export (2–32):",
        "Muestras en la exportación (2–32):",
    ),
    "status.preset.not_found": _t("Preset introuvable", "Preset not found", "Preset no encontrado"),
    "status.preset.audio_not_found": _t(
        "Preset audio introuvable",
        "Audio preset not found",
        "Preset de audio no encontrado",
    ),
    "status.color.invalid": _t("Étalonnage invalide", "Invalid color grading", "Corrección de color no válida"),
}

__all__ = ["DIALOGS_TRANSLATIONS"]
