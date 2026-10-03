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
    "multicam.viewer.title": _t("MULTICAM", "MULTICAM", "MULTICÁMARA"),
    "multicam.viewer.empty": _t(
        "Placez la tête de lecture sur un segment Multicam pour voir tous ses angles.",
        "Move the playhead onto a Multicam segment to see all its angles.",
        "Sitúe el cabezal sobre un segmento multicámara para ver todos sus ángulos.",
    ),
    "multicam.viewer.offline": _t("MÉDIA HORS LIGNE", "MEDIA OFFLINE", "MEDIO SIN CONEXIÓN"),
    "multicam.viewer.no_signal": _t("PAS DE SIGNAL", "NO SIGNAL", "SIN SEÑAL"),
    "multicam.viewer.audio_only": _t("AUDIO", "AUDIO", "AUDIO"),
    "multicam.viewer.program": _t("PROGRAMME", "PROGRAM", "PROGRAMA"),
    "multicam.viewer.page": _t("{page}/{pages}", "{page}/{pages}", "{page}/{pages}"),
    "multicam.viewer.page_previous": _t("Page précédente", "Previous page", "Página anterior"),
    "multicam.viewer.page_next": _t("Page suivante", "Next page", "Página siguiente"),
    "multicam.message.no_source": _t(
        "Ce projet ne contient aucune source Multicam.", "This project has no Multicam source.",
        "Este proyecto no tiene ninguna fuente multicámara.",
    ),
    "multicam.message.dialog_title": _t("Multicam", "Multicam", "Multicámara"),
}


# --- Boîtes de dialogue : création, résultat de synchronisation, progression ------------------------------------------------
_DIALOGS: dict[str, dict[str, str]] = {
    "multicam.kind.video": _t("VIDÉO", "VIDEO", "VÍDEO"),
    "multicam.kind.audio": _t("AUDIO", "AUDIO", "AUDIO"),
    "multicam.dialog.name": _t("Nom", "Name", "Nombre"),
    "multicam.dialog.angle_name": _t("Nom de l'angle", "Angle name", "Nombre del ángulo"),
    "multicam.dialog.sources": _t("Sources", "Sources", "Fuentes"),
    "multicam.dialog.add_source": _t("Ajouter une source…", "Add a source…", "Añadir una fuente…"),
    "multicam.dialog.remove": _t("Retirer", "Remove", "Quitar"),
    "multicam.dialog.method": _t("Synchronisation", "Synchronization", "Sincronización"),
    "multicam.dialog.create": _t("Créer", "Create", "Crear"),
    "multicam.dialog.cancel": _t("Annuler", "Cancel", "Cancelar"),
    "multicam.method.audio": _t("Par le son", "By sound", "Por el sonido"),
    "multicam.method.audio.tip": _t(
        "Compare les formes d'onde et aligne les sources sur le même événement sonore.",
        "Compares the waveforms and aligns the sources on the same sound event.",
        "Compara las formas de onda y alinea las fuentes sobre el mismo evento sonoro.",
    ),
    "multicam.method.audio.unavailable": _t(
        "Il faut au moins deux sources avec du son.", "At least two sources with sound are needed.",
        "Se necesitan al menos dos fuentes con sonido.",
    ),
    "multicam.method.timecode": _t("Par le timecode", "By timecode", "Por el timecode"),
    "multicam.method.timecode.tip": _t(
        "Aligne les sources sur l'heure de début inscrite dans leurs métadonnées ; une source sans timecode est placée au début.",
        "Aligns the sources on the start time stored in their metadata; a source without timecode is placed at the start.",
        "Alinea las fuentes según la hora de inicio de sus metadatos; una fuente sin timecode se coloca al principio.",
    ),
    "multicam.method.timecode.unavailable": _t(
        "Au moins deux sources doivent porter un timecode.", "At least two sources must carry a timecode.",
        "Al menos dos fuentes deben tener timecode.",
    ),
    "multicam.method.marker": _t("Par les repères", "By markers", "Por los marcadores"),
    "multicam.method.marker.tip": _t(
        "Aligne les clips sur le repère posé dans chacun d'eux.", "Aligns the clips on the marker placed in each of them.",
        "Alinea los clips según el marcador colocado en cada uno.",
    ),
    "multicam.method.marker.unavailable": _t(
        "Chaque clip de la timeline doit contenir un repère.", "Each timeline clip must contain a marker.",
        "Cada clip de la línea de tiempo debe contener un marcador.",
    ),
    "multicam.method.positions": _t(
        "Positions actuelles", "Current positions", "Posiciones actuales",
    ),
    "multicam.method.positions.tip": _t(
        "Garde l'alignement actuel des clips sur la timeline.", "Keeps the clips' current alignment on the timeline.",
        "Mantiene la alineación actual de los clips en la línea de tiempo.",
    ),
    "multicam.method.positions.unavailable": _t(
        "Disponible depuis des clips de la timeline.", "Available from timeline clips.",
        "Disponible desde clips de la línea de tiempo.",
    ),
    "multicam.method.start": _t("Début des clips", "Clip starts", "Inicio de los clips"),
    "multicam.method.start.tip": _t(
        "Fait commencer toutes les sources en même temps.", "Starts all the sources at the same time.",
        "Hace que todas las fuentes empiecen al mismo tiempo.",
    ),
    "multicam.method.manual": _t(
        "Manuelle (j'ajusterai moi-même)", "Manual (I will adjust it myself)", "Manual (lo ajustaré yo)",
    ),
    "multicam.method.manual.tip": _t(
        "Crée la source sans synchroniser : décalez ensuite chaque angle à la main.",
        "Creates the source without synchronizing: shift each angle by hand afterwards.",
        "Crea la fuente sin sincronizar: desplace después cada ángulo a mano.",
    ),
    "multicam.summary.title": _t("Résultat de la synchronisation", "Synchronization result", "Resultado de la sincronización"),
    "multicam.summary.hint": _t(
        "Les angles non synchronisés gardent le début de leurs clips ; vous pourrez les décaler à la main dans la source.",
        "Unsynchronized angles keep the start of their clips; you can shift them by hand in the source.",
        "Los ángulos sin sincronizar conservan el inicio de sus clips; podrá desplazarlos a mano en la fuente.",
    ),
    "multicam.summary.audio": _t("Son", "Sound", "Sonido"),
    "multicam.summary.offset": _t("décalage {seconds} s", "offset {seconds} s", "desfase {seconds} s"),
    "multicam.sync.excellent": _t("Synchronisation excellente", "Excellent synchronization", "Sincronización excelente"),
    "multicam.sync.good": _t("Synchronisation bonne", "Good synchronization", "Sincronización buena"),
    "multicam.sync.uncertain": _t("Synchronisation incertaine", "Uncertain synchronization", "Sincronización incierta"),
    "multicam.sync.failed": _t("Échec de la synchronisation", "Synchronization failed", "Falló la sincronización"),
    "multicam.sync.manual": _t("Réglée à la main", "Set by hand", "Ajustada a mano"),
    "multicam.sync.none": _t("Non mesurée", "Not measured", "Sin medir"),
    "multicam.sync.reference": _t("Référence", "Reference", "Referencia"),
    "multicam.audio.follow": _t("Le son suit l'image", "Sound follows the picture", "El sonido sigue a la imagen"),
    "multicam.audio.fixed": _t("Son de « {name} » en continu", "Sound of “{name}” throughout", "Sonido de «{name}» continuo"),
    "multicam.audio.mix": _t(
        "Mixer toutes les sources sonores", "Mix all the sound sources", "Mezclar todas las fuentes sonoras",
    ),
    "multicam.progress.title": _t("Synchronisation en cours", "Synchronization in progress", "Sincronización en curso"),
    "multicam.progress.envelope": _t("Analyse du son : {name}", "Analyzing sound: {name}", "Analizando el sonido: {name}"),
    "multicam.progress.measure": _t("Mesure du décalage : {name}", "Measuring offset: {name}", "Midiendo el desfase: {name}"),
    "multicam.progress.cancel": _t("Annuler", "Cancel", "Cancelar"),
    "multicam.message.need_two": _t(
        "Choisissez au moins deux sources.", "Choose at least two sources.", "Elija al menos dos fuentes.",
    ),
    "multicam.message.created": _t(
        "Source Multicam « {name} » créée ({summary}).", "Multicam source “{name}” created ({summary}).",
        "Fuente multicámara «{name}» creada ({summary}).",
    ),
    "multicam.message.synced": _t(
        "{count} angle(s) synchronisé(s) sur {total}", "{count} of {total} angle(s) synchronized",
        "{count} de {total} ángulo(s) sincronizado(s)",
    ),
    "multicam.message.no_timecode": _t(
        "{count} source(s) sans timecode, placée(s) au début", "{count} source(s) without timecode, placed at the start",
        "{count} fuente(s) sin timecode, colocada(s) al principio",
    ),
    "multicam.message.sync_cancelled": _t(
        "Synchronisation annulée.", "Synchronization cancelled.", "Sincronización cancelada.",
    ),
    "multicam.message.sync_failed": _t(
        "La synchronisation a échoué (voir le journal).", "Synchronization failed (see the log).",
        "Falló la sincronización (consulte el registro).",
    ),
    "multicam.message.positions": _t("positions actuelles", "current positions", "posiciones actuales"),
    "multicam.message.starts": _t("début des clips", "clip starts", "inicio de los clips"),
    "multicam.message.manual": _t("à régler à la main", "to adjust by hand", "por ajustar a mano"),
    "multicam.message.markers": _t("repères", "markers", "marcadores"),
    "multicam.message.timecode": _t("timecode", "timecode", "timecode"),
    "multicam.info.file": _t("Fichier", "File", "Archivo"),
    "multicam.info.resolution": _t("Résolution", "Resolution", "Resolución"),
    "multicam.info.fps": _t("Cadence", "Frame rate", "Cadencia"),
    "multicam.info.camera": _t("Caméra", "Camera", "Cámara"),
    "multicam.info.reel": _t("Bobine", "Reel", "Bobina"),
    "multicam.info.timecode": _t("Timecode", "Timecode", "Timecode"),
    "multicam.info.time_reference": _t("Référence BWF", "BWF reference", "Referencia BWF"),
    "multicam.info.creation_time": _t("Création", "Created", "Creación"),
    "multicam.default_name": _t("Multicam", "Multicam", "Multicámara"),
    "multicam.angle_word": _t("Angle", "Angle", "Ángulo"),
    "multicam.audio_word": _t("Audio", "Audio", "Audio"),
}
MULTICAM_TRANSLATIONS.update(_DIALOGS)


# --- Réglages d'une source Multicam -----------------------------------------------------------------------------------------
_SETTINGS: dict[str, dict[str, str]] = {
    "shortcuts.command.multicam_settings": _t(
        "Réglages Multicam…", "Multicam settings…", "Ajustes multicámara…",
    ),
    "multicam.menu.settings": _t("Réglages Multicam…", "Multicam settings…", "Ajustes multicámara…"),
    "multicam.settings.open": _t("Réglages", "Settings", "Ajustes"),
    "multicam.settings.title": _t("Réglages Multicam", "Multicam settings", "Ajustes multicámara"),
    "multicam.settings.angles": _t("Angles", "Angles", "Ángulos"),
    "multicam.settings.color": _t("Changer la couleur", "Change the colour", "Cambiar el color"),
    "multicam.settings.color_of": _t(
        "Couleur de l'angle {name}", "Colour of angle {name}", "Color del ángulo {name}",
    ),
    "multicam.settings.offset": _t("Décalage de synchronisation", "Synchronization offset", "Desfase de sincronización"),
    "multicam.settings.seconds_suffix": _t(" s", " s", " s"),
    "multicam.settings.relink": _t("Relier…", "Relink…", "Reconectar…"),
    "multicam.settings.resync": _t("Synchroniser par le son", "Synchronize by sound", "Sincronizar por el sonido"),
    "multicam.settings.close": _t("Fermer", "Close", "Cerrar"),
    "multicam.settings.replace_title": _t("Supprimer l'angle", "Delete the angle", "Eliminar el ángulo"),
    "multicam.settings.replace_label": _t(
        "Des segments montrent « {name} » : quel angle les remplace ?",
        "Some segments show “{name}”: which angle replaces it?",
        "Algunos segmentos muestran «{name}»: ¿qué ángulo lo reemplaza?",
    ),
    "multicam.message.resync_partial": _t(
        "{count} angle(s) n'ont pas pu être synchronisés : ils gardent leur place.",
        "{count} angle(s) could not be synchronized: they stay where they are.",
        "{count} ángulo(s) no se pudieron sincronizar: conservan su posición.",
    ),
}
MULTICAM_TRANSLATIONS.update(_SETTINGS)
