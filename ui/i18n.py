"""Système de localisation FR/EN/ES de Kut-Studio.

Les traductions sont centralisées dans le dictionnaire :data:`_TRANSLATIONS`.
Elles couvrent les principaux textes de l'interface : menus, panneaux,
dialogues, actions de pistes, statuts Undo/Redo, etc.

L'API publique est volontairement minimale et stable :

- :func:`translate(key, **values)` : récupère une traduction ;
- :func:`set_language(code)` : change la langue courante ;
- :func:`current_language()` : retourne la langue active ;
- :func:`available_languages()` : codes valides ;
- :func:`subscribe(callback)` : permet à n'importe quel widget de se
  rafraîchir après un changement de langue.

Les clés de traduction sont en anglais. Une clé inconnue renvoie
``"[{key}]"`` pour la repérer rapidement. Les valeurs de substitution
supportent ``{name}`` (format PEP 3101).
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------


DEFAULT_LANGUAGE: str = "fr"
AVAILABLE_LANGUAGES: tuple[str, ...] = ("fr", "en", "es")


# ---------------------------------------------------------------------------
# Dictionnaires
# ---------------------------------------------------------------------------


_TRANSLATIONS: dict[str, dict[str, str]] = {
    "app.title": {
        "fr": "Kut-Studio",
        "en": "Kut-Studio",
        "es": "Kut-Studio",
    },
    "menu.file": {"fr": "Fichier", "en": "File", "es": "Archivo"},
    "menu.edit": {"fr": "Édition", "en": "Edit", "es": "Edición"},
    "menu.view": {"fr": "Affichage", "en": "View", "es": "Ver"},
    "menu.timeline": {"fr": "Séquence", "en": "Timeline", "es": "Secuencia"},
    "menu.help": {"fr": "Aide", "en": "Help", "es": "Ayuda"},
    "action.new_project": {"fr": "Nouveau projet", "en": "New project", "es": "Nuevo proyecto"},
    "action.open": {"fr": "Ouvrir…", "en": "Open…", "es": "Abrir…"},
    "action.save": {"fr": "Enregistrer", "en": "Save", "es": "Guardar"},
    "action.save_as": {"fr": "Enregistrer sous…", "en": "Save as…", "es": "Guardar como…"},
    "action.import_media": {"fr": "Importer un média…", "en": "Import media…", "es": "Importar medios…"},
    "action.import_srt": {"fr": "Importer un SRT…", "en": "Import SRT…", "es": "Importar SRT…"},
    "action.export": {"fr": "Exporter…", "en": "Export…", "es": "Exportar…"},
    "action.quit": {"fr": "Quitter", "en": "Quit", "es": "Salir"},
    "action.undo": {"fr": "Annuler", "en": "Undo", "es": "Deshacer"},
    "action.redo": {"fr": "Rétablir", "en": "Redo", "es": "Rehacer"},
    "action.cut": {"fr": "Couper", "en": "Cut", "es": "Cortar"},
    "action.delete": {"fr": "Supprimer", "en": "Delete", "es": "Eliminar"},
    "action.duplicate": {"fr": "Dupliquer", "en": "Duplicate", "es": "Duplicar"},
    "action.ripple_delete": {"fr": "Supprimer avec ripple", "en": "Ripple delete", "es": "Eliminar con ripple"},
    "action.toggle_clip": {"fr": "Activer / Désactiver le clip", "en": "Enable / Disable clip", "es": "Activar / Desactivar clip"},
    "action.preferences": {"fr": "Préférences…", "en": "Preferences…", "es": "Preferencias…"},
    "tracks.add_video": {"fr": "+ Vidéo", "en": "+ Video", "es": "+ Vídeo"},
    "tracks.add_audio": {"fr": "+ Audio", "en": "+ Audio", "es": "+ Audio"},
    "tracks.add_subtitle": {"fr": "+ Sous-titres", "en": "+ Subtitles", "es": "+ Subtítulos"},
    "tracks.add_video_long": {"fr": "Ajouter une piste vidéo", "en": "Add a video track", "es": "Añadir pista de vídeo"},
    "tracks.add_audio_long": {"fr": "Ajouter une piste audio", "en": "Add an audio track", "es": "Añadir pista de audio"},
    "tracks.add_subtitle_long": {"fr": "Ajouter une piste de sous-titres", "en": "Add a subtitle track", "es": "Añadir pista de subtítulos"},
    "tracks.remove": {"fr": "Supprimer la piste", "en": "Remove track", "es": "Eliminar pista"},
    "tracks.rename": {"fr": "Renommer la piste", "en": "Rename track", "es": "Renombrar pista"},
    "tracks.move_up": {"fr": "Monter la piste", "en": "Move track up", "es": "Subir pista"},
    "tracks.move_down": {"fr": "Descendre la piste", "en": "Move track down", "es": "Bajar pista"},
    "tracks.toggle_lock": {"fr": "Verrouiller la piste", "en": "Lock track", "es": "Bloquear pista"},
    "tracks.toggle_visible": {"fr": "Afficher / masquer la piste", "en": "Show / hide track", "es": "Mostrar / ocultar pista"},
    "tracks.toggle_mute": {"fr": "Muet / actif", "en": "Mute / unmute", "es": "Silenciar / activar"},
    "tracks.add_tooltip": {"fr": "Ajouter une piste", "en": "Add a track", "es": "Añadir una pista"},
    "tracks.lock_tooltip": {"fr": "Verrouiller / déverrouiller", "en": "Lock / unlock", "es": "Bloquear / desbloquear"},
    "tracks.visible_tooltip": {"fr": "Afficher / masquer", "en": "Show / hide", "es": "Mostrar / ocultar"},
    "tracks.mute_tooltip": {"fr": "Muet / actif", "en": "Mute / unmute", "es": "Silenciar / activar"},
    "tracks.delete_tooltip": {"fr": "Supprimer", "en": "Delete", "es": "Eliminar"},
    "tracks.rename_tooltip": {"fr": "Renommer", "en": "Rename", "es": "Renombrar"},
    "tracks.up_tooltip": {"fr": "Monter", "en": "Move up", "es": "Subir"},
    "tracks.down_tooltip": {"fr": "Descendre", "en": "Move down", "es": "Bajar"},
    "tracks.snap_tooltip": {"fr": "Aimant — snapping magnétique", "en": "Magnet — magnetic snapping", "es": "Imán — snapping magnético"},
    "panel.preview": {"fr": "PREVIEW", "en": "PREVIEW", "es": "PREVIEW"},
    "panel.properties": {"fr": "Propriétés", "en": "Properties", "es": "Propiedades"},
    "panel.timeline": {"fr": "Timeline", "en": "Timeline", "es": "Línea de tiempo"},
    "panel.project": {"fr": "Projet", "en": "Project", "es": "Proyecto"},
    "panel.library": {"fr": "Bibliothèque", "en": "Library", "es": "Biblioteca"},
    "panel.export": {"fr": "Export", "en": "Export", "es": "Exportación"},
    "group.timeline_settings": {"fr": "Paramètres timeline", "en": "Timeline settings", "es": "Ajustes de la línea"},
    "group.project": {"fr": "Paramètres du projet", "en": "Project settings", "es": "Ajustes del proyecto"},
    "group.audio": {"fr": "Audio", "en": "Audio", "es": "Audio"},
    "group.color": {"fr": "Couleur", "en": "Color", "es": "Color"},
    "group.movement": {"fr": "Mouvement", "en": "Movement", "es": "Movimiento"},
    "field.brightness": {"fr": "Luminosité", "en": "Brightness", "es": "Brillo"},
    "field.contrast": {"fr": "Contraste", "en": "Contrast", "es": "Contraste"},
    "field.saturation": {"fr": "Saturation", "en": "Saturation", "es": "Saturación"},
    "field.volume": {"fr": "Volume", "en": "Volume", "es": "Volumen"},
    "field.zoom": {"fr": "Zoom", "en": "Zoom", "es": "Zoom"},
    "field.snap": {"fr": "Aimant", "en": "Snap", "es": "Imán"},
    "field.scale": {"fr": "Échelle", "en": "Scale", "es": "Escala"},
    "field.rotation": {"fr": "Rotation", "en": "Rotation", "es": "Rotación"},
    "field.opacity": {"fr": "Opacité", "en": "Opacity", "es": "Opacidad"},
    "field.position_x": {"fr": "Position X", "en": "Position X", "es": "Posición X"},
    "field.position_y": {"fr": "Position Y", "en": "Position Y", "es": "Posición Y"},
    "action.reset_movement": {"fr": "Réinitialiser le mouvement", "en": "Reset movement", "es": "Restablecer movimiento"},
    "field.name": {"fr": "Nom", "en": "Name", "es": "Nombre"},
    "field.duration": {"fr": "Durée", "en": "Duration", "es": "Duración"},
    "field.position": {"fr": "Position", "en": "Position", "es": "Posición"},
    "field.enabled_clip": {"fr": "Clip activé", "en": "Clip enabled", "es": "Clip activado"},
    "field.resolution": {"fr": "Résolution", "en": "Resolution", "es": "Resolución"},
    "field.fps": {"fr": "Fréquence", "en": "Frame rate", "es": "Frecuencia"},
    "field.background": {"fr": "Fond", "en": "Background", "es": "Fondo"},
    "group.speed_and_duration": {"fr": "Vitesse et durée", "en": "Speed and duration", "es": "Velocidad y duración"},
    "field.speed": {"fr": "Vitesse", "en": "Speed", "es": "Velocidad"},
    "field.reverse": {"fr": "Reverse", "en": "Reverse", "es": "Inverso"},
    "field.freeze_frame": {"fr": "Arrêt sur image", "en": "Freeze frame", "es": "Fotograma fijo"},
    "field.freeze_duration": {"fr": "Durée", "en": "Duration", "es": "Duración"},
    "action.reset_speed": {"fr": "Réinitialiser", "en": "Reset", "es": "Restablecer"},
    "action.speed_0.25x": {"fr": "0,25x", "en": "0.25x", "es": "0,25x"},
    "action.speed_0.5x": {"fr": "0,5x", "en": "0.5x", "es": "0,5x"},
    "action.speed_1x": {"fr": "1x", "en": "1x", "es": "1x"},
    "action.speed_2x": {"fr": "2x", "en": "2x", "es": "2x"},
    "action.speed_4x": {"fr": "4x", "en": "4x", "es": "4x"},
    "tooltip.speed_0.25x": {"fr": "Vitesse 0,25x", "en": "Speed 0.25x", "es": "Velocidad 0,25x"},
    "tooltip.speed_0.5x": {"fr": "Vitesse 0,5x", "en": "Speed 0.5x", "es": "Velocidad 0,5x"},
    "tooltip.speed_1x": {"fr": "Vitesse normale", "en": "Normal speed", "es": "Velocidad normal"},
    "tooltip.speed_2x": {"fr": "Vitesse 2x", "en": "Speed 2x", "es": "Velocidad 2x"},
    "tooltip.speed_4x": {"fr": "Vitesse 4x", "en": "Speed 4x", "es": "Velocidad 4x"},
    "tooltip.reverse": {"fr": "Inverser la lecture", "en": "Reverse playback", "es": "Invertir reproducción"},
    "tooltip.freeze_frame": {"fr": "Créer un arrêt sur image", "en": "Create freeze frame", "es": "Crear fotograma fijo"},
    "tooltip.reset_speed": {"fr": "Réinitialiser la vitesse et le remappage temporel", "en": "Reset speed and time remapping", "es": "Restablecer velocidad y mapeo temporal"},
    "library.title": {"fr": "MÉDIAS", "en": "MEDIA", "es": "MEDIOS"},
    "library.subtitle": {"fr": "Bibliothèque", "en": "Library", "es": "Biblioteca"},
    "library.empty": {"fr": "Aucun média importé.", "en": "No media imported.", "es": "Ningún medio importado."},
    "library.import": {"fr": "Importer", "en": "Import", "es": "Importar"},
    "library.import_srt": {"fr": "Importer un SRT", "en": "Import an SRT", "es": "Importar un SRT"},
    "library.export_subtitles": {"fr": "Exporter les sous-titres", "en": "Export subtitles", "es": "Exportar subtítulos"},
    "library.add_subtitle": {"fr": "Ajouter sous-titre", "en": "Add subtitle", "es": "Añadir subtítulo"},
    "library.refresh": {"fr": "Rafraîchir", "en": "Refresh", "es": "Actualizar"},
    "preview.empty": {
        "fr": "Votre histoire commence ici\n\nImportez vos médias, puis déposez-les sur la timeline.",
        "en": "Your story starts here\n\nImport your media, then drop them on the timeline.",
        "es": "Tu historia empieza aquí\n\nImporta tus medios y colócalos en la línea de tiempo.",
    },
    "preview.fade": {"fr": "Fondu enchaîné · {seconds}s", "en": "Crossfade · {seconds}s", "es": "Fundido encadenado · {seconds}s"},
    "status.saved": {"fr": "Enregistré", "en": "Saved", "es": "Guardado"},
    "status.dirty": {"fr": "Non enregistré", "en": "Unsaved", "es": "Sin guardar"},
    "status.cancelling": {"fr": "Annulation…", "en": "Cancelling…", "es": "Cancelando…"},
    "status.cancelled": {"fr": "Annulé", "en": "Cancelled", "es": "Cancelado"},
    "status.export_done": {"fr": "Export terminé", "en": "Export completed", "es": "Exportación completada"},
    "status.export_failed": {"fr": "Export échoué", "en": "Export failed", "es": "Exportación fallida"},
    "tooltip.import": {"fr": "Importer une vidéo ou un son", "en": "Import a video or audio", "es": "Importar un vídeo o audio"},
    "tooltip.export": {"fr": "Exporter le montage", "en": "Export the project", "es": "Exportar el montaje"},
    "tooltip.cut": {"fr": "Couper le clip à la tête de lecture", "en": "Cut clip at playhead", "es": "Cortar el clip en el cursor"},
    "tooltip.snap": {"fr": "Activer le snapping magnétique", "en": "Enable magnetic snapping", "es": "Activar snapping magnético"},
    "tooltip.add_video": {"fr": "Créer une nouvelle piste vidéo", "en": "Create a new video track", "es": "Crear una nueva pista de vídeo"},
    "tooltip.add_audio": {"fr": "Créer une nouvelle piste audio", "en": "Create a new audio track", "es": "Crear una nueva pista de audio"},
    "tooltip.add_subtitle": {"fr": "Créer une nouvelle piste de sous-titres", "en": "Create a new subtitle track", "es": "Crear una nueva pista de subtítulos"},
    "tooltip.zoom_in": {"fr": "Zoom +", "en": "Zoom in", "es": "Acercar"},
    "tooltip.zoom_out": {"fr": "Zoom −", "en": "Zoom out", "es": "Alejar"},
    "tooltip.record_undo_label": {
        "fr": "{label} (modification : {seconds}s)",
        "en": "{label} (modified: {seconds}s)",
        "es": "{label} (modificado: {seconds}s)",
    },
    "prefs.title": {"fr": "Préférences", "en": "Preferences", "es": "Preferencias"},
    "prefs.appearance": {"fr": "Apparence", "en": "Appearance", "es": "Apariencia"},
    "prefs.theme.dark": {"fr": "Sombre", "en": "Dark", "es": "Oscuro"},
    "prefs.theme.light": {"fr": "Clair", "en": "Light", "es": "Claro"},
    "prefs.theme.system": {"fr": "Système", "en": "System", "es": "Sistema"},
    "prefs.language": {"fr": "Langue", "en": "Language", "es": "Idioma"},
    "prefs.language.fr": {"fr": "Français", "en": "French", "es": "Francés"},
    "prefs.language.en": {"fr": "Anglais", "en": "English", "es": "Inglés"},
    "prefs.language.es": {"fr": "Espagnol", "en": "Spanish", "es": "Español"},
    "prefs.restore_defaults": {"fr": "Restaurer les réglages par défaut", "en": "Restore default settings", "es": "Restablecer ajustes por defecto"},
    "prefs.performance": {"fr": "Performance", "en": "Performance", "es": "Rendimiento"},
    "prefs.performance.auto": {"fr": "Auto (selon la machine)", "en": "Auto (this computer)", "es": "Auto (este equipo)"},
    "prefs.performance.low": {"fr": "Léger", "en": "Light", "es": "Ligero"},
    "prefs.performance.balanced": {"fr": "Équilibré", "en": "Balanced", "es": "Equilibrado"},
    "prefs.performance.high": {"fr": "Puissant", "en": "Powerful", "es": "Potente"},
    "prefs.preview": {"fr": "Qualité d'aperçu", "en": "Preview quality", "es": "Calidad de vista previa"},
    "prefs.preview.auto": {"fr": "Auto", "en": "Auto", "es": "Auto"},
    "prefs.preview.full": {"fr": "Plein", "en": "Full", "es": "Completa"},
    "prefs.preview.half": {"fr": "1/2", "en": "1/2", "es": "1/2"},
    "prefs.preview.quarter": {"fr": "1/4", "en": "1/4", "es": "1/4"},
    "prefs.preview.eighth": {"fr": "1/8", "en": "1/8", "es": "1/8"},
    "debug.toggle": {"fr": "Diagnostics de performance", "en": "Performance diagnostics", "es": "Diagnóstico de rendimiento"},
    "prefs.applied": {"fr": "Préférences appliquées", "en": "Preferences applied", "es": "Preferencias aplicadas"},
    "subtitle.edit": {"fr": "Sous-titre S1", "en": "Subtitle S1", "es": "Subtítulo S1"},
    "subtitle.placeholder": {"fr": "Texte affiché sur le preview...", "en": "Text displayed on the preview...", "es": "Texto mostrado en el preview..."},
    "no_clip_selected": {"fr": "Aucun clip sélectionné", "en": "No clip selected", "es": "Ningún clip seleccionado"},
    "track.video_default": {"fr": "V{n}", "en": "V{n}", "es": "V{n}"},
    "track.audio_default": {"fr": "A{n}", "en": "A{n}", "es": "A{n}"},
    "track.subtitle_default": {"fr": "S{n}", "en": "S{n}", "es": "S{n}"},

    # --- Mixeur audio -------------------------------------------------
    "mixer.title": {"fr": "MIXEUR", "en": "MIXER", "es": "MEZCLADOR"},
    "mixer.master": {"fr": "Master", "en": "Master", "es": "Master"},
    "mixer.output": {"fr": "Sortie", "en": "Output", "es": "Salida"},
    "mixer.volume": {"fr": "Volume", "en": "Volume", "es": "Volumen"},
    "mixer.pan": {"fr": "Panoramique", "en": "Pan", "es": "Pan"},
    "mixer.pan_center": {"fr": "Centré", "en": "Center", "es": "Centrado"},
    "mixer.pan_left": {"fr": "Gauche", "en": "Left", "es": "Izquierda"},
    "mixer.pan_right": {"fr": "Droite", "en": "Right", "es": "Derecha"},
    "mixer.mute": {"fr": "Muet", "en": "Mute", "es": "Silenciar"},
    "mixer.solo": {"fr": "Solo", "en": "Solo", "es": "Solo"},
    "mixer.arm": {"fr": "Armé", "en": "Arm", "es": "Armar"},
    "mixer.reset": {"fr": "Réinitialiser le volume et le panoramique", "en": "Reset volume and pan", "es": "Restablecer volumen y paneo"},
    "mixer.master_mute": {"fr": "Couper la sortie principale", "en": "Mute master output", "es": "Silenciar la salida principal"},
    "mixer.master_reset": {"fr": "Réinitialiser la sortie principale", "en": "Reset master output", "es": "Restablecer la salida principal"},
    "mixer.locked": {"fr": "Piste verrouillée", "en": "Locked track", "es": "Pista bloqueada"},
    "mixer.no_tracks": {
        "fr": "Aucune piste audio dans ce projet.",
        "en": "This project has no audio track.",
        "es": "Este proyecto no tiene pista de audio.",
    },
    "mixer.track_volume": {
        "fr": "Volume de la piste {name}",
        "en": "Track volume for {name}",
        "es": "Volumen de la pista {name}",
    },
    "mixer.track_pan": {
        "fr": "Panoramique de la piste {name}",
        "en": "Pan for track {name}",
        "es": "Paneo de la pista {name}",
    },
    "mixer.track_mute": {
        "fr": "Couper la piste {name}",
        "en": "Mute track {name}",
        "es": "Silenciar la pista {name}",
    },
    "mixer.track_solo": {
        "fr": "Solo sur la piste {name}",
        "en": "Solo track {name}",
        "es": "Solo en la pista {name}",
    },
    "mixer.track_arm": {
        "fr": "Armer la piste {name} pour l'enregistrement",
        "en": "Arm track {name} for recording",
        "es": "Armar la pista {name} para grabar",
    },
    "mixer.solo_active": {
        "fr": "Solo actif : seules les pistes solo sont entendues.",
        "en": "Solo active: only soloed tracks are heard.",
        "es": "Solo activo: solo se escuchan las pistas con solo.",
    },

    # --- Réglages audio d'un clip -------------------------------------
    "audio.gain": {"fr": "Gain", "en": "Gain", "es": "Ganancia"},
    "audio.fade_in": {"fr": "Fondu d'entrée", "en": "Fade in", "es": "Fundido de entrada"},
    "audio.fade_out": {"fr": "Fondu de sortie", "en": "Fade out", "es": "Fundido de salida"},
    "audio.reset_fades": {"fr": "Retirer les fondus", "en": "Remove fades", "es": "Quitar fundidos"},
    "audio.no_audio": {
        "fr": "Ce clip ne porte pas de réglages audio.",
        "en": "This clip has no audio settings.",
        "es": "Este clip no tiene ajustes de audio.",
    },
    "audio.fade_too_long": {
        "fr": "Fondu trop long pour un clip de {duration}s.",
        "en": "Fade too long for a {duration}s clip.",
        "es": "Fundido demasiado largo para un clip de {duration}s.",
    },

    # --- Enregistrement audio -----------------------------------------
    "record.start": {"fr": "Enregistrer", "en": "Record", "es": "Grabar"},
    "record.stop": {"fr": "Arrêter l'enregistrement", "en": "Stop recording", "es": "Detener la grabación"},
    "record.no_armed_track": {
        "fr": "Aucune piste audio armée. Armez-en une pour enregistrer.",
        "en": "No armed audio track. Arm one to record.",
        "es": "Ninguna pista de audio armada. Arma una para grabar.",
    },
    "record.failed": {
        "fr": "L'enregistrement a échoué : {error}",
        "en": "Recording failed: {error}",
        "es": "La grabación falló: {error}",
    },
    "record.timed_out": {
        "fr": "L'enregistrement a expiré.",
        "en": "Recording timed out.",
        "es": "La grabación expiró.",
    },
    "record.cancelled": {
        "fr": "Enregistrement annulé.",
        "en": "Recording cancelled.",
        "es": "Grabación cancelada.",
    },
    "record.placed": {
        "fr": "Prise enregistrée sur {track}.",
        "en": "Take recorded on {track}.",
        "es": "Toma grabada en {track}.",
    },
    "record.too_short": {
        "fr": "Prise trop courte pour être placée.",
        "en": "Take too short to be placed.",
        "es": "Toma demasiado corta para colocarse.",
    },

    # --- Actions audio dans la timeline -------------------------------
    "audio.action.reset_fades": {
        "fr": "Retirer les fondus",
        "en": "Remove fades",
        "es": "Quitar fundidos",
    },
    "audio.action.gain": {
        "fr": "Régler le gain",
        "en": "Adjust gain",
        "es": "Ajustar ganancia",
    },

    # --- Effets visuels d'un clip (tâche 21) --------------------------
    "effects.section": {
        "fr": "Effets du clip",
        "en": "Clip effects",
        "es": "Efectos del clip",
    },
    "effects.no_clip": {
        "fr": "Sélectionnez un clip vidéo pour gérer ses effets.",
        "en": "Select a video clip to manage its effects.",
        "es": "Selecciona un clip de vídeo para gestionar sus efectos.",
    },
    "effects.video_only": {
        "fr": "Les effets ne s'appliquent qu'aux clips vidéo.",
        "en": "Effects only apply to video clips.",
        "es": "Los efectos solo se aplican a los clips de vídeo.",
    },
    "effects.empty": {
        "fr": "Aucun effet sur ce clip.",
        "en": "No effect on this clip.",
        "es": "Ningún efecto en este clip.",
    },
    "effects.enable": {"fr": "Activer", "en": "Enable", "es": "Activar"},
    "effects.disable": {"fr": "Désactiver", "en": "Disable", "es": "Desactivar"},
    "effects.remove": {"fr": "Supprimer", "en": "Remove", "es": "Eliminar"},
    "effects.move_up": {"fr": "Monter", "en": "Move up", "es": "Subir"},
    "effects.move_down": {"fr": "Descendre", "en": "Move down", "es": "Bajar"},
    "effects.disabled_suffix": {
        "fr": "désactivé",
        "en": "disabled",
        "es": "desactivado",
    },
    "effects.no_parameters": {
        "fr": "Aucun réglage pour cet effet.",
        "en": "No setting for this effect.",
        "es": "Sin ajustes para este efecto.",
    },
    "effects.library_hint": {
        "fr": "Sélectionnez un clip vidéo, puis ajoutez un effet.",
        "en": "Select a video clip, then add an effect.",
        "es": "Selecciona un clip de vídeo y añade un efecto.",
    },
    "effects.add": {"fr": "Ajouter", "en": "Add", "es": "Añadir"},
    "effects.already_added": {
        "fr": "Déjà présent sur le clip",
        "en": "Already on the clip",
        "es": "Ya está en el clip",
    },
    "effects.badge_tooltip": {
        "fr": "Effets actifs : {names}",
        "en": "Active effects: {names}",
        "es": "Efectos activos: {names}",
    },

    # --- Noms et descriptions des effets ------------------------------
    "effects.name.color_correction": {
        "fr": "Correction couleur",
        "en": "Color correction",
        "es": "Corrección de color",
    },
    "effects.name.blur": {"fr": "Flou", "en": "Blur", "es": "Desenfoque"},
    "effects.name.sharpen": {
        "fr": "Netteté",
        "en": "Sharpen",
        "es": "Nitidez",
    },
    "effects.name.vignette": {"fr": "Vignette", "en": "Vignette", "es": "Viñeta"},
    "effects.name.black_and_white": {
        "fr": "Noir et blanc",
        "en": "Black and white",
        "es": "Blanco y negro",
    },
    "effects.name.sepia": {"fr": "Sépia", "en": "Sepia", "es": "Sepia"},
    "effects.desc.color_correction": {
        "fr": "Règle luminosité, contraste et saturation.",
        "en": "Adjusts brightness, contrast and saturation.",
        "es": "Ajusta brillo, contraste y saturación.",
    },
    "effects.desc.blur": {
        "fr": "Adoucit l'image avec un flou gaussien.",
        "en": "Softens the image with a gaussian blur.",
        "es": "Suaviza la imagen con un desenfoque gaussiano.",
    },
    "effects.desc.sharpen": {
        "fr": "Accentue les contours pour plus de netteté.",
        "en": "Accentuates edges for more sharpness.",
        "es": "Acentúa los bordes para más nitidez.",
    },
    "effects.desc.vignette": {
        "fr": "Assombrit les bords de l'image.",
        "en": "Darkens the edges of the image.",
        "es": "Oscurece los bordes de la imagen.",
    },
    "effects.desc.black_and_white": {
        "fr": "Convertit l'image en niveaux de gris.",
        "en": "Converts the image to greyscale.",
        "es": "Convierte la imagen a escala de grises.",
    },
    "effects.desc.sepia": {
        "fr": "Applique un virage sépia chaleureux.",
        "en": "Applies a warm sepia tone.",
        "es": "Aplica un tono sepia cálido.",
    },

    # --- Paramètres d'effet -------------------------------------------
    "effects.param.brightness": {
        "fr": "Luminosité",
        "en": "Brightness",
        "es": "Brillo",
    },
    "effects.param.contrast": {
        "fr": "Contraste",
        "en": "Contrast",
        "es": "Contraste",
    },
    "effects.param.saturation": {
        "fr": "Saturation",
        "en": "Saturation",
        "es": "Saturación",
    },
    "effects.param.intensity": {
        "fr": "Intensité",
        "en": "Intensity",
        "es": "Intensidad",
    },
}


# ---------------------------------------------------------------------------
# État global et abonnements
# ---------------------------------------------------------------------------


_current_language: str = DEFAULT_LANGUAGE
"""Langue active (par défaut : français)."""

_subscribers: list = []
"""Callbacks appelés sur changement de langue."""


def available_languages() -> tuple[str, ...]:
    """Retourne les codes de langue disponibles (``"fr"``, ``"en"``, ``"es"``)."""
    return AVAILABLE_LANGUAGES


def current_language() -> str:
    """Retourne le code langue courant (par défaut ``"fr"``)."""
    return _current_language


def set_language(code: str) -> bool:
    """Change la langue active.

    Args:
        code: ``"fr"``, ``"en"`` ou ``"es"``. Une valeur invalide est
            ignorée (la langue active reste inchangée).

    Returns:
        ``True`` si la langue a effectivement changé, ``False`` sinon
        (code invalide ou identique à la langue courante).
    """
    global _current_language
    if code not in AVAILABLE_LANGUAGES:
        return False
    if code == _current_language:
        return False
    _current_language = code
    for callback in list(_subscribers):
        try:
            callback(code)
        except Exception:
            # On ne casse pas le changement pour un subscriber fautif.
            pass
    return True


def subscribe(callback) -> None:
    """Inscrit ``callback`` aux changements de langue.

    ``callback(code)`` est invoqué après chaque :func:`set_language`.
    Le callback doit être idempotent : ``set_language`` peut être
    appelé avec le même code sans le notifier, mais lors d'un vrai
    changement tous les abonnés sont appelés une fois.
    """
    if callback not in _subscribers:
        _subscribers.append(callback)


def unsubscribe(callback) -> None:
    """Désabonne un callback ajouté par :func:`subscribe`."""
    try:
        _subscribers.remove(callback)
    except ValueError:
        pass


def reset_for_tests() -> None:
    """Remet l'état interne à sa valeur par défaut (usage de test).

    Les abonnés sont supprimés et la langue revient à :data:`DEFAULT_LANGUAGE`.
    """
    global _current_language, _subscribers
    _current_language = DEFAULT_LANGUAGE
    _subscribers = []


# ---------------------------------------------------------------------------
# API de traduction
# ---------------------------------------------------------------------------


def _format(template: str, values: dict) -> str:
    if not values:
        return template
    try:
        return template.format(**values)
    except (KeyError, IndexError):
        return template


def translate(key: str, **values) -> str:
    """Retourne la traduction de ``key`` dans la langue courante.

    Args:
        key: clé de traduction (chaînes ASCII stables).
        **values: substitutions PEP 3101 (``{name}``).

    Returns:
        La traduction, formatée avec ``values`` si fournis. Si la clé
        est inconnue, renvoie ``"[{key}]"`` pour la repérer facilement
        en debug.
    """
    entries = _TRANSLATIONS.get(key)
    if not entries:
        return f"[{key}]"
    template = entries.get(_current_language)
    if template is None:
        template = entries.get(DEFAULT_LANGUAGE) or key
    return _format(template, values)


__all__ = [
    "DEFAULT_LANGUAGE",
    "AVAILABLE_LANGUAGES",
    "available_languages",
    "current_language",
    "reset_for_tests",
    "set_language",
    "subscribe",
    "translate",
    "unsubscribe",
]
