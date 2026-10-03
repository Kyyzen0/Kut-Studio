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
    # Titres des menus : « & » marque la lettre mnémonique (Alt + lettre sous Windows / Linux, retirée sous macOS).
    # Une seule lettre par menu et par langue, sur tous les menus possibles de la barre (voir test_menu_mnemonics).
    "menu.file": {"fr": "&Fichier", "en": "&File", "es": "&Archivo"},
    "menu.edit": {"fr": "É&dition", "en": "&Edit", "es": "&Edición"},
    "menu.view": {"fr": "Affic&hage", "en": "&View", "es": "&Ver"},
    "menu.timeline": {"fr": "&Séquence", "en": "&Timeline", "es": "&Secuencia"},
    "menu.help": {"fr": "&Aide", "en": "&Help", "es": "A&yuda"},
    "proxy.menu.title": {"fr": "Proxy", "en": "Proxy", "es": "Proxy"},
    "proxy.state.none": {"fr": "Aucun proxy", "en": "No proxy", "es": "Sin proxy"},
    "proxy.state.pending": {"fr": "Proxy en attente", "en": "Proxy queued", "es": "Proxy en espera"},
    "proxy.state.generating": {"fr": "Proxy en cours : {progress} %", "en": "Generating proxy: {progress}%", "es": "Generando proxy: {progress} %"},
    "proxy.state.ready": {"fr": "Proxy prêt", "en": "Proxy ready", "es": "Proxy listo"},
    "proxy.state.error": {"fr": "Erreur de proxy", "en": "Proxy error", "es": "Error de proxy"},
    "proxy.state.stale": {"fr": "Proxy obsolète (le média a changé)", "en": "Proxy out of date (media changed)", "es": "Proxy obsoleto (el medio cambió)"},
    "proxy.action.generate": {"fr": "Générer le proxy", "en": "Generate proxy", "es": "Generar proxy"},
    "proxy.action.regenerate": {"fr": "Régénérer le proxy", "en": "Regenerate proxy", "es": "Regenerar proxy"},
    "proxy.action.delete": {"fr": "Supprimer le proxy", "en": "Delete proxy", "es": "Eliminar proxy"},
    "proxy.action.cancel": {"fr": "Annuler la génération", "en": "Cancel generation", "es": "Cancelar la generación"},
    "proxy.action.generate_project": {"fr": "Générer les proxies du projet", "en": "Generate proxies for the project", "es": "Generar proxies del proyecto"},
    "proxy.action.generate_selection": {"fr": "Générer les proxies des clips sélectionnés", "en": "Generate proxies for selected clips", "es": "Generar proxies de los clips seleccionados"},
    "proxy.profile.low": {"fr": "Faible (480p)", "en": "Low (480p)", "es": "Bajo (480p)"},
    "proxy.profile.medium": {"fr": "Moyen (720p)", "en": "Medium (720p)", "es": "Medio (720p)"},
    "proxy.profile.high": {"fr": "Élevé (1080p)", "en": "High (1080p)", "es": "Alto (1080p)"},
    "proxy.error.ffmpeg": {"fr": "FFmpeg est introuvable : les proxies ne peuvent pas être générés.", "en": "FFmpeg was not found: proxies cannot be generated.", "es": "No se encontró FFmpeg: no se pueden generar proxies."},
    "proxy.done": {"fr": "{count} proxy(ies) prêt(s)", "en": "{count} proxy(ies) ready", "es": "{count} proxy(ies) listo(s)"},
    "proxy.none_needed": {"fr": "Aucun média vidéo à convertir.", "en": "No video media to convert.", "es": "No hay medios de vídeo que convertir."},
    "perf.tab": {"fr": "Performance", "en": "Performance", "es": "Rendimiento"},
    "perf.proxies.title": {"fr": "Proxies", "en": "Proxies", "es": "Proxies"},
    "perf.use_proxies": {"fr": "Utiliser les proxies pour l'aperçu (l'export utilise toujours les originaux)", "en": "Use proxies for preview (export always uses the originals)", "es": "Usar proxies en la vista previa (la exportación usa siempre los originales)"},
    "perf.profile": {"fr": "Profil de proxy", "en": "Proxy profile", "es": "Perfil de proxy"},
    "perf.proxies.generate_project": {"fr": "Générer les proxies du projet", "en": "Generate project proxies", "es": "Generar proxies del proyecto"},
    "perf.proxies.cancel_all": {"fr": "Annuler les générations", "en": "Cancel generations", "es": "Cancelar generaciones"},
    "perf.cache.title": {"fr": "Cache", "en": "Cache", "es": "Caché"},
    "perf.cache.max": {"fr": "Taille maximale du cache disque (Go)", "en": "Maximum disk cache size (GB)", "es": "Tamaño máximo de la caché en disco (GB)"},
    "perf.cache.usage": {"fr": "Utilisation : aperçus {preview} · proxies {proxy} · mémoire {memory}", "en": "Usage: previews {preview} · proxies {proxy} · memory {memory}", "es": "Uso: vistas previas {preview} · proxies {proxy} · memoria {memory}"},
    "perf.cache.purge_previews": {"fr": "Purger les aperçus", "en": "Purge previews", "es": "Purgar vistas previas"},
    "perf.cache.purge_proxies": {"fr": "Supprimer tous les proxies", "en": "Delete all proxies", "es": "Eliminar todos los proxies"},
    "perf.cache.purge_all": {"fr": "Purger tous les caches", "en": "Purge all caches", "es": "Purgar todas las cachés"},
    "perf.cache.purge_project": {"fr": "Purger le cache du projet", "en": "Purge project cache", "es": "Purgar la caché del proyecto"},
    "perf.cache.confirm": {"fr": "Supprimer ces fichiers de cache ? Ils seront recalculés à la demande ; aucun projet n'est modifié.", "en": "Delete these cache files? They will be recomputed on demand; no project is modified.", "es": "¿Eliminar estos archivos de caché? Se recalcularán cuando haga falta; ningún proyecto se modifica."},
    "perf.cache.freed": {"fr": "{size} libérés", "en": "{size} freed", "es": "{size} liberados"},
    "perf.quality_hint": {"fr": "La qualité d'aperçu se règle dans l'onglet Général. En mode Auto, elle baisse temporairement quand la lecture ne tient pas sa cadence, puis remonte progressivement.", "en": "Preview quality is set in the General tab. In Auto mode it drops temporarily when playback can't keep up, then recovers gradually.", "es": "La calidad de la vista previa se ajusta en la pestaña General. En modo Auto baja temporalmente si la reproducción no mantiene su cadencia y luego sube poco a poco."},
    "perf.ffmpeg_missing": {"fr": "FFmpeg est introuvable : proxies indisponibles.", "en": "FFmpeg not found: proxies unavailable.", "es": "FFmpeg no encontrado: proxies no disponibles."},
    "menu.item.use_proxies": {"fr": "Utiliser les proxies (aperçu)", "en": "Use proxies (preview)", "es": "Usar proxies (vista previa)"},
    "preview.quality_reduced": {"fr": "Aperçu réduit ({quality}) · charge élevée", "en": "Preview reduced ({quality}) · high load", "es": "Vista reducida ({quality}) · carga alta"},
    "render.title": {"fr": "FILE DE RENDU", "en": "RENDER QUEUE", "es": "COLA DE RENDER"},
    "render.btn.start_all": {"fr": "Tout lancer", "en": "Start all", "es": "Iniciar todo"},
    "render.btn.start": {"fr": "Lancer", "en": "Start", "es": "Iniciar"},
    "render.btn.stop": {"fr": "Arrêter la file", "en": "Stop queue", "es": "Detener la cola"},
    "render.btn.cancel": {"fr": "Annuler le job", "en": "Cancel job", "es": "Cancelar"},
    "render.btn.retry": {"fr": "Relancer", "en": "Retry", "es": "Reintentar"},
    "render.btn.remove": {"fr": "Supprimer", "en": "Remove", "es": "Eliminar"},
    "render.btn.clear": {"fr": "Nettoyer", "en": "Clear finished", "es": "Limpiar"},
    "render.btn.up": {"fr": "Monter", "en": "Move up", "es": "Subir"},
    "render.btn.down": {"fr": "Descendre", "en": "Move down", "es": "Bajar"},
    "render.btn.open_file": {"fr": "Ouvrir le fichier", "en": "Open file", "es": "Abrir archivo"},
    "render.btn.open_folder": {"fr": "Ouvrir le dossier", "en": "Open folder", "es": "Abrir carpeta"},
    "render.btn.copy_error": {"fr": "Copier l'erreur", "en": "Copy error", "es": "Copiar error"},
    "render.col.name": {"fr": "Export", "en": "Export", "es": "Exportación"},
    "render.col.preset": {"fr": "Preset", "en": "Preset", "es": "Preset"},
    "render.col.status": {"fr": "Statut", "en": "Status", "es": "Estado"},
    "render.col.progress": {"fr": "Progression", "en": "Progress", "es": "Progreso"},
    "render.col.output": {"fr": "Fichier", "en": "File", "es": "Archivo"},
    "render.status.waiting": {"fr": "En attente", "en": "Waiting", "es": "En espera"},
    "render.status.rendering": {"fr": "Rendu en cours", "en": "Rendering", "es": "Renderizando"},
    "render.status.completed": {"fr": "Terminé", "en": "Completed", "es": "Completado"},
    "render.status.failed": {"fr": "Échec", "en": "Failed", "es": "Error"},
    "render.status.cancelled": {"fr": "Annulé", "en": "Cancelled", "es": "Cancelado"},
    "render.overall": {"fr": "File : {done}/{total} terminé(s) · {percent} %", "en": "Queue: {done}/{total} done · {percent}%", "es": "Cola: {done}/{total} completado(s) · {percent} %"},
    "render.overall.idle": {"fr": "File inactive", "en": "Queue idle", "es": "Cola inactiva"},
    "render.empty": {"fr": "Aucun export dans la file. Choisissez un preset puis « Ajouter à la file ».", "en": "No exports in the queue. Pick a preset, then “Add to queue”.", "es": "No hay exportaciones en la cola. Elija un preset y pulse «Añadir a la cola»."},
    "render.ffmpeg_missing": {"fr": "FFmpeg est introuvable : les exports sont impossibles. Installez FFmpeg, puis relancez les jobs en échec.", "en": "FFmpeg was not found: exports are unavailable. Install FFmpeg, then retry failed jobs.", "es": "No se encontró FFmpeg: las exportaciones no están disponibles. Instale FFmpeg y reintente los trabajos fallidos."},
    "render.detail.none": {"fr": "Sélectionnez un export pour voir ses détails.", "en": "Select an export to see its details.", "es": "Seleccione una exportación para ver sus detalles."},
    "render.detail.output": {"fr": "Fichier : {path}", "en": "File: {path}", "es": "Archivo: {path}"},
    "render.detail.render_time": {"fr": "Durée du rendu : {time}", "en": "Render time: {time}", "es": "Duración del render: {time}"},
    "render.detail.size": {"fr": "Taille : {size}", "en": "Size: {size}", "es": "Tamaño: {size}"},
    "render.detail.encoder": {"fr": "Encodeur : {encoder}", "en": "Encoder: {encoder}", "es": "Codificador: {encoder}"},
    "render.detail.missing_file": {"fr": "Le fichier exporté n'existe plus.", "en": "The exported file no longer exists.", "es": "El archivo exportado ya no existe."},
    "render.kind.ffmpeg": {"fr": "Erreur FFmpeg", "en": "FFmpeg error", "es": "Error de FFmpeg"},
    "render.kind.ffmpeg_missing": {"fr": "FFmpeg introuvable", "en": "FFmpeg not found", "es": "FFmpeg no encontrado"},
    "render.kind.invalid": {"fr": "Projet ou réglages invalides", "en": "Invalid project or settings", "es": "Proyecto o ajustes no válidos"},
    "render.kind.interrupted": {"fr": "Rendu interrompu", "en": "Render interrupted", "es": "Render interrumpido"},
    "render.kind.snapshot_missing": {"fr": "Projet introuvable", "en": "Project not found", "es": "Proyecto no encontrado"},
    "render.kind.encoder": {"fr": "Encodeur indisponible", "en": "Encoder unavailable", "es": "Codificador no disponible"},
    "render.col.encoder": {"fr": "Encodeur", "en": "Encoder", "es": "Codificador"},
    "render.btn.retry_cpu": {"fr": "Relancer en CPU", "en": "Retry on CPU", "es": "Reintentar con CPU"},
    "render.export.encoder": {"fr": "Encodeur", "en": "Encoder", "es": "Codificador"},
    "render.encoder.auto": {"fr": "Automatique", "en": "Automatic", "es": "Automático"},
    "encoding.detecting": {"fr": "Détection des capacités matérielles en cours…", "en": "Detecting hardware capabilities…", "es": "Detectando capacidades de hardware…"},
    "encoding.fallback_notice": {"fr": "Encodage matériel indisponible, bascule sur le CPU. {reason}", "en": "Hardware encoding unavailable, switching to CPU. {reason}", "es": "Codificación por hardware no disponible, cambiando a CPU. {reason}"},
    "perf.encoding.title": {"fr": "Encodage matériel (export)", "en": "Hardware encoding (export)", "es": "Codificación por hardware (exportación)"},
    "perf.encoding.default": {"fr": "Encodeur par défaut", "en": "Default encoder", "es": "Codificador predeterminado"},
    "perf.encoding.redetect": {"fr": "Redétecter les capacités matérielles", "en": "Re-detect hardware capabilities", "es": "Volver a detectar capacidades de hardware"},
    "perf.encoding.copy": {"fr": "Copier le diagnostic", "en": "Copy diagnostics", "es": "Copiar diagnóstico"},
    "render.kind.io": {"fr": "Écriture impossible", "en": "Could not write file", "es": "No se pudo escribir"},
    "render.confirm.rerender": {"fr": "Le fichier existant sera remplacé par le nouveau rendu. Continuer ?", "en": "The existing file will be replaced by the new render. Continue?", "es": "El archivo existente será reemplazado por el nuevo render. ¿Continuar?"},
    "render.confirm.title": {"fr": "File de rendu", "en": "Render queue", "es": "Cola de render"},
    "status.unavailable": {
        "fr": "Cette fonction n'est pas encore disponible.",
        "en": "This feature is not available yet.",
        "es": "Esta función aún no está disponible.",
    },
    "graphics.color.pick": {"fr": "Choisir une couleur", "en": "Pick a colour", "es": "Elegir un color"},
    "project.untitled": {"fr": "Sans titre", "en": "Untitled", "es": "Sin título"},
    "project.unsaved.title": {"fr": "Projet non enregistré", "en": "Unsaved project", "es": "Proyecto sin guardar"},
    "project.unsaved.text": {
        "fr": "Le projet « {name} » a des modifications non enregistrées.\n\nVoulez-vous les enregistrer ?",
        "en": "The project “{name}” has unsaved changes.\n\nDo you want to save them?",
        "es": "El proyecto «{name}» tiene cambios sin guardar.\n\n¿Quieres guardarlos?",
    },
    "render.close.title": {"fr": "Rendu en cours", "en": "Render in progress", "es": "Render en curso"},
    "sequence.nav.back": {"fr": "Séquence précédente", "en": "Previous sequence", "es": "Secuencia anterior"},
    "sequence.nav.forward": {"fr": "Séquence suivante", "en": "Next sequence", "es": "Secuencia siguiente"},
    "sequence.nav.parent": {"fr": "Revenir à la séquence parente", "en": "Go to parent sequence", "es": "Volver a la secuencia principal"},
    "sequence.nav.caption": {"fr": "SÉQUENCE", "en": "SEQUENCE", "es": "SECUENCIA"},
    "sequence.nav.menu": {"fr": "Séquences ▾", "en": "Sequences ▾", "es": "Secuencias ▾"},
    "sequence.nav.active": {"fr": "Séquence en cours d'édition", "en": "Sequence being edited", "es": "Secuencia en edición"},
    "sequence.nav.open_level": {"fr": "Ouvrir ce niveau", "en": "Open this level", "es": "Abrir este nivel"},
    "sequence.status.ok": {"fr": "Séquence imbriquée — double-clic pour l'ouvrir", "en": "Nested sequence — double-click to open", "es": "Secuencia anidada — doble clic para abrirla"},
    "sequence.status.missing": {"fr": "Hors ligne : la séquence référencée a été supprimée", "en": "Offline: the referenced sequence was deleted", "es": "Sin conexión: la secuencia referenciada fue eliminada"},
    "sequence.status.cycle": {"fr": "Imbrication circulaire : ce clip est rendu vide", "en": "Circular nesting: this clip renders empty", "es": "Anidamiento circular: este clip se muestra vacío"},
    "sequence.status.overflow": {"fr": "Le clip dépasse la fin de sa séquence : la zone hachurée est vide", "en": "The clip runs past the end of its sequence: the hatched part is empty", "es": "El clip supera el final de su secuencia: la zona rayada está vacía"},
    "sequence.action.new": {"fr": "Nouvelle séquence", "en": "New sequence", "es": "Nueva secuencia"},
    "sequence.action.nest_selection": {"fr": "Créer une séquence à partir de la sélection", "en": "Nest selection into a sequence", "es": "Crear una secuencia a partir de la selección"},
    "sequence.action.open_nested": {"fr": "Ouvrir la séquence imbriquée", "en": "Open nested sequence", "es": "Abrir la secuencia anidada"},
    "sequence.action.open": {"fr": "Ouvrir la séquence", "en": "Open sequence", "es": "Abrir la secuencia"},
    "sequence.action.insert": {"fr": "Insérer dans la séquence active (tête de lecture)", "en": "Insert into active sequence (playhead)", "es": "Insertar en la secuencia activa (cabezal)"},
    "sequence.action.rename": {"fr": "Renommer la séquence", "en": "Rename sequence", "es": "Renombrar la secuencia"},
    "sequence.action.duplicate": {"fr": "Dupliquer la séquence", "en": "Duplicate sequence", "es": "Duplicar la secuencia"},
    "sequence.action.delete": {"fr": "Supprimer la séquence", "en": "Delete sequence", "es": "Eliminar la secuencia"},
    "sequence.action.parent": {"fr": "Revenir à la séquence parente", "en": "Go to parent sequence", "es": "Volver a la secuencia principal"},
    "sequence.action.back": {"fr": "Séquence précédente", "en": "Previous sequence", "es": "Secuencia anterior"},
    "sequence.action.forward": {"fr": "Séquence suivante", "en": "Next sequence", "es": "Secuencia siguiente"},
    "sequence.library.title": {"fr": "SÉQUENCES DU PROJET", "en": "PROJECT SEQUENCES", "es": "SECUENCIAS DEL PROYECTO"},
    "sequence.library.hint": {"fr": "Double-clic pour ouvrir. Glissez une séquence sur une piste pour l'imbriquer.", "en": "Double-click to open. Drag a sequence onto a track to nest it.", "es": "Doble clic para abrir. Arrastre una secuencia a una pista para anidarla."},
    "sequence.library.usage": {"fr": "utilisée {count} fois", "en": "used {count} times", "es": "usada {count} veces"},
    "sequence.library.drag_hint": {"fr": "Glisser sur la timeline pour l'utiliser comme clip", "en": "Drag onto the timeline to use it as a clip", "es": "Arrastrar a la línea de tiempo para usarla como clip"},
    "sequence.dialog.new_title": {"fr": "Nouvelle séquence", "en": "New sequence", "es": "Nueva secuencia"},
    "sequence.dialog.name_label": {"fr": "Nom de la séquence :", "en": "Sequence name:", "es": "Nombre de la secuencia:"},
    "sequence.dialog.nest_title": {"fr": "Créer une séquence à partir de la sélection", "en": "Nest selection into a sequence", "es": "Crear una secuencia a partir de la selección"},
    "sequence.dialog.rename_title": {"fr": "Renommer la séquence", "en": "Rename sequence", "es": "Renombrar la secuencia"},
    "sequence.dialog.delete_title": {"fr": "Supprimer la séquence", "en": "Delete sequence", "es": "Eliminar la secuencia"},
    "sequence.dialog.delete_confirm": {"fr": "Supprimer la séquence « {name} » ? L'opération peut être annulée.", "en": "Delete sequence “{name}”? This can be undone.", "es": "¿Eliminar la secuencia «{name}»? Se puede deshacer."},
    "sequence.dialog.delete_in_use": {"fr": "La séquence « {name} » est utilisée {count} fois :\n\n{places}\n\nSi vous la supprimez, ces clips deviendront hors ligne (rendus vides). L'opération peut être annulée.", "en": "Sequence “{name}” is used {count} times:\n\n{places}\n\nIf you delete it, these clips go offline (render empty). This can be undone.", "es": "La secuencia «{name}» se usa {count} veces:\n\n{places}\n\nSi la elimina, esos clips quedarán sin conexión (vacíos). Se puede deshacer."},
    "sequence.dialog.delete_force": {"fr": "Supprimer quand même", "en": "Delete anyway", "es": "Eliminar de todos modos"},
    "sequence.dialog.error_title": {"fr": "Séquences", "en": "Sequences", "es": "Secuencias"},
    "sequence.history.new": {"fr": "Nouvelle séquence « {name} »", "en": "New sequence “{name}”", "es": "Nueva secuencia «{name}»"},
    "sequence.history.nest": {"fr": "Créer la séquence imbriquée « {name} »", "en": "Nest into “{name}”", "es": "Anidar en «{name}»"},
    "sequence.history.insert": {"fr": "Insérer la séquence « {name} »", "en": "Insert sequence “{name}”", "es": "Insertar la secuencia «{name}»"},
    "sequence.history.rename": {"fr": "Renommer la séquence en « {name} »", "en": "Rename sequence to “{name}”", "es": "Renombrar la secuencia a «{name}»"},
    "sequence.history.duplicate": {"fr": "Dupliquer la séquence « {name} »", "en": "Duplicate sequence “{name}”", "es": "Duplicar la secuencia «{name}»"},
    "sequence.history.delete": {"fr": "Supprimer la séquence « {name} »", "en": "Delete sequence “{name}”", "es": "Eliminar la secuencia «{name}»"},
    "sequence.history.clamped": {"fr": "{count} clip(s) imbriqué(s) ajusté(s)", "en": "{count} nested clip(s) adjusted", "es": "{count} clip(s) anidado(s) ajustado(s)"},
    "sequence.message.no_selection": {"fr": "Sélectionnez au moins un clip à imbriquer.", "en": "Select at least one clip to nest.", "es": "Seleccione al menos un clip para anidar."},
    "sequence.message.missing": {"fr": "La séquence de ce clip est introuvable (supprimée) : le clip est hors ligne.", "en": "This clip's sequence is missing (deleted): the clip is offline.", "es": "La secuencia de este clip no existe (eliminada): el clip está sin conexión."},
    "sequence.message.clamped": {"fr": "{count} clip(s) imbriqué(s) raccourci(s) : la séquence « {name} » est plus courte.", "en": "{count} nested clip(s) shortened: sequence “{name}” got shorter.", "es": "{count} clip(s) anidado(s) acortado(s): la secuencia «{name}» es más corta."},
    "sequence.message.load_issues": {"fr": "Séquences : {count} problème(s) détecté(s) à l'ouverture.\n\n{details}", "en": "Sequences: {count} issue(s) found on load.\n\n{details}", "es": "Secuencias: {count} problema(s) al abrir.\n\n{details}"},
    "sequence.message.no_parent": {"fr": "Cette séquence n'est utilisée dans aucune autre.", "en": "This sequence is not used in any other.", "es": "Esta secuencia no se usa en ninguna otra."},
    "sequence.message.no_track": {"fr": "Aucune piste vidéo ou audio déverrouillée pour insérer la séquence.", "en": "No unlocked video or audio track to insert the sequence.", "es": "No hay pista de vídeo o audio desbloqueada para insertar la secuencia."},
    "render.close.text": {"fr": "Un rendu est en cours. Quitter Kut-Studio l'annulera.", "en": "A render is in progress. Quitting Kut-Studio will cancel it.", "es": "Hay un render en curso. Salir de Kut-Studio lo cancelará."},
    "render.close.waiting": {"fr": "\n\n{count} export(s) en attente seront conservés pour la prochaine ouverture.", "en": "\n\n{count} queued export(s) will be kept for next launch.", "es": "\n\n{count} exportación(es) en espera se conservarán para la próxima vez."},
    "render.close.question": {"fr": "\n\nQuitter et arrêter le rendu ?", "en": "\n\nQuit and stop the render?", "es": "\n\n¿Salir y detener el render?"},
    "render.added": {"fr": "Ajouté à la file : {name}", "en": "Added to queue: {name}", "es": "Añadido a la cola: {name}"},
    "render.summary.one": {"fr": "L'export est terminé avec succès.\n\nFichier : {path}", "en": "The export finished successfully.\n\nFile: {path}", "es": "La exportación terminó correctamente.\n\nArchivo: {path}"},
    "render.summary.many": {"fr": "File de rendu terminée : {completed} réussi(s), {failed} échec(s), {cancelled} annulé(s).", "en": "Render queue finished: {completed} completed, {failed} failed, {cancelled} cancelled.", "es": "Cola de render terminada: {completed} completado(s), {failed} con error, {cancelled} cancelado(s)."},
    "render.summary.title": {"fr": "Export terminé", "en": "Export finished", "es": "Exportación terminada"},
    "render.export.preset": {"fr": "Preset", "en": "Preset", "es": "Preset"},
    "render.export.add": {"fr": "Ajouter à la file", "en": "Add to queue", "es": "Añadir a la cola"},
    "render.export.launch": {"fr": "LANCER L'EXPORT", "en": "START EXPORT", "es": "INICIAR EXPORTACIÓN"},
    "render.export.cancel": {"fr": "Annuler", "en": "Cancel", "es": "Cancelar"},
    "render.export.ready": {"fr": "Prêt à exporter", "en": "Ready to export", "es": "Listo para exportar"},
    "render.export.running": {"fr": "Rendu en cours : {name}", "en": "Rendering: {name}", "es": "Renderizando: {name}"},
    "render.export.done": {"fr": "Export terminé", "en": "Export finished", "es": "Exportación terminada"},
    "render.export.cancelled": {"fr": "Export annulé", "en": "Export cancelled", "es": "Exportación cancelada"},
    "render.export.failed": {"fr": "Échec : {name}", "en": "Failed: {name}", "es": "Error: {name}"},
    "render.export.save_title": {"fr": "Enregistrer l'export", "en": "Save export", "es": "Guardar exportación"},
    "render.export.invalid": {"fr": "Paramètres invalides : {error}", "en": "Invalid settings: {error}", "es": "Ajustes no válidos: {error}"},
    "render.preset.h264_1080p": {"fr": "H.264 1080p", "en": "H.264 1080p", "es": "H.264 1080p"},
    "render.preset.h264_1440p": {"fr": "H.264 1440p", "en": "H.264 1440p", "es": "H.264 1440p"},
    "render.preset.h264_4k": {"fr": "H.264 4K", "en": "H.264 4K", "es": "H.264 4K"},
    "render.preset.youtube": {"fr": "YouTube", "en": "YouTube", "es": "YouTube"},
    "render.preset.tiktok": {"fr": "TikTok / Vertical 1080×1920", "en": "TikTok / Vertical 1080×1920", "es": "TikTok / Vertical 1080×1920"},
    "render.preset.prores_master": {"fr": "ProRes Master", "en": "ProRes Master", "es": "ProRes Master"},
    "render.preset.custom": {"fr": "Custom (personnalisé)", "en": "Custom", "es": "Personalizado"},
    "render.preset.desc.h264_1080p": {"fr": "Full HD, bon équilibre taille / qualité.", "en": "Full HD, good size / quality balance.", "es": "Full HD, buen equilibrio tamaño / calidad."},
    "render.preset.desc.h264_1440p": {"fr": "QHD pour les écrans haute résolution.", "en": "QHD for high-resolution screens.", "es": "QHD para pantallas de alta resolución."},
    "render.preset.desc.h264_4k": {"fr": "UHD ; rendu long et fichier volumineux.", "en": "UHD; long render and large file.", "es": "UHD; render largo y archivo grande."},
    "render.preset.desc.youtube": {"fr": "MP4 H.264 1080p, qualité élevée, démarrage rapide.", "en": "MP4 H.264 1080p, high quality, fast start.", "es": "MP4 H.264 1080p, alta calidad, inicio rápido."},
    "render.preset.desc.tiktok": {"fr": "Vidéo verticale 1080×1920.", "en": "Vertical video 1080×1920.", "es": "Vídeo vertical 1080×1920."},
    "render.preset.desc.prores_master": {"fr": "Intermédiaire de qualité maximale (ProRes 422 HQ).", "en": "Maximum-quality intermediate (ProRes 422 HQ).", "es": "Intermedio de máxima calidad (ProRes 422 HQ)."},
    "render.preset.desc.custom": {"fr": "Réglages libres : format, résolution, qualité, images/seconde.", "en": "Free settings: format, resolution, quality, frame rate.", "es": "Ajustes libres: formato, resolución, calidad, fotogramas por segundo."},
    "render.export.format": {"fr": "Format", "en": "Format", "es": "Formato"},
    "render.export.resolution": {"fr": "Résolution", "en": "Resolution", "es": "Resolución"},
    "render.export.quality": {"fr": "Qualité", "en": "Quality", "es": "Calidad"},
    "render.export.fps": {"fr": "Images/seconde", "en": "Frames/second", "es": "Fotogramas/segundo"},
    "menu.window": {"fr": "Fe&nêtre", "en": "&Window", "es": "Ve&ntana"},
    "menu.item.new": {"fr": "Nouveau", "en": "New", "es": "Nuevo"},
    "menu.item.open": {"fr": "Ouvrir...", "en": "Open...", "es": "Abrir..."},
    "menu.item.save": {"fr": "Enregistrer", "en": "Save", "es": "Guardar"},
    "menu.item.save_as": {"fr": "Enregistrer sous...", "en": "Save as...", "es": "Guardar como..."},
    "menu.item.import_srt": {"fr": "Importer des sous-titres SRT…", "en": "Import SRT subtitles…", "es": "Importar subtítulos SRT…"},
    "menu.item.export_srt": {"fr": "Exporter les sous-titres SRT…", "en": "Export SRT subtitles…", "es": "Exportar subtítulos SRT…"},
    "menu.item.duplicate_clip": {"fr": "Dupliquer le clip", "en": "Duplicate clip", "es": "Duplicar clip"},
    "menu.item.delete_clip": {"fr": "Supprimer le clip", "en": "Delete clip", "es": "Eliminar clip"},
    "menu.item.copy": {"fr": "Copier", "en": "Copy", "es": "Copiar"},
    "menu.item.paste": {"fr": "Coller", "en": "Paste", "es": "Pegar"},
    "menu.item.reset_layout": {"fr": "Réinitialiser la disposition", "en": "Reset layout", "es": "Restablecer la disposición"},
    "menu.item.show_scopes": {"fr": "Afficher les scopes", "en": "Show scopes", "es": "Mostrar scopes"},
    "menu.item.add_clip": {"fr": "Ajouter un clip", "en": "Add clip", "es": "Añadir clip"},
    "menu.item.trim": {"fr": "Couper / Réduire", "en": "Cut / Trim", "es": "Cortar / Recortar"},
    "menu.item.marker": {"fr": "Marqueur", "en": "Marker", "es": "Marcador"},
    "menu.item.panels": {"fr": "Panneaux", "en": "Panels", "es": "Paneles"},
    "menu.item.restore_layout": {"fr": "Restaurer la disposition", "en": "Restore layout", "es": "Restaurar la disposición"},
    "menu.item.workspaces": {"fr": "Espaces de travail", "en": "Workspaces", "es": "Espacios de trabajo"},
    "menu.item.save_layout_as": {"fr": "Enregistrer la disposition sous…", "en": "Save layout as…", "es": "Guardar la disposición como…"},
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
    "tracks.add_video": {"fr": "Vidéo", "en": "Video", "es": "Vídeo"},
    "tracks.add_audio": {"fr": "Audio", "en": "Audio", "es": "Audio"},
    "tracks.add_subtitle": {"fr": "Sous-titres", "en": "Subtitles", "es": "Subtítulos"},
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
    "prefs.close": {"fr": "Fermer", "en": "Close", "es": "Cerrar"},
    "shortcuts.category.playback": {"fr": "Lecture", "en": "Playback", "es": "Reproducción"},
    "shortcuts.category.timeline": {"fr": "Timeline", "en": "Timeline", "es": "Línea de tiempo"},
    "shortcuts.category.tools": {"fr": "Outils", "en": "Tools", "es": "Herramientas"},
    "shortcuts.category.edit": {"fr": "Édition", "en": "Edit", "es": "Edición"},
    "shortcuts.category.project": {"fr": "Projet", "en": "Project", "es": "Proyecto"},
    "shortcuts.category.navigation": {"fr": "Navigation", "en": "Navigation", "es": "Navegación"},
    "shortcuts.category.view": {"fr": "Affichage", "en": "View", "es": "Vista"},
    "shortcuts.category.audio": {"fr": "Audio", "en": "Audio", "es": "Audio"},
    "shortcuts.category.markers": {"fr": "Marqueurs", "en": "Markers", "es": "Marcadores"},
    "shortcuts.command.play_pause": {"fr": "Lecture / Pause", "en": "Play / Pause", "es": "Reproducir / Pausa"},
    "shortcuts.command.shuttle_back": {"fr": "Reculer (×2 s)", "en": "Shuttle back (2 s)", "es": "Retroceder (2 s)"},
    "shortcuts.command.shuttle_forward": {"fr": "Avancer (×2 s)", "en": "Shuttle forward (2 s)", "es": "Avanzar (2 s)"},
    "shortcuts.command.toggle_snap": {"fr": "Activer / désactiver l'aimantation", "en": "Toggle snapping", "es": "Activar / desactivar el imán"},
    "shortcuts.command.toggle_ripple": {"fr": "Activer / désactiver le ripple", "en": "Toggle ripple", "es": "Activar / desactivar el ripple"},
    "shortcuts.command.tool_select": {"fr": "Outil Sélection", "en": "Selection tool", "es": "Herramienta Selección"},
    "shortcuts.command.tool_blade": {"fr": "Outil Lame", "en": "Blade tool", "es": "Herramienta Cuchilla"},
    "shortcuts.command.tool_roll": {"fr": "Outil Roll", "en": "Roll tool", "es": "Herramienta Roll"},
    "shortcuts.command.tool_slip": {"fr": "Outil Slip", "en": "Slip tool", "es": "Herramienta Slip"},
    "shortcuts.command.tool_slide": {"fr": "Outil Slide", "en": "Slide tool", "es": "Herramienta Slide"},
    "shortcuts.command.undo": {"fr": "Annuler", "en": "Undo", "es": "Deshacer"},
    "shortcuts.command.redo": {"fr": "Rétablir", "en": "Redo", "es": "Rehacer"},
    "shortcuts.command.duplicate_clip": {"fr": "Dupliquer le clip", "en": "Duplicate clip", "es": "Duplicar clip"},
    "shortcuts.command.delete_clip": {"fr": "Supprimer le clip", "en": "Delete clip", "es": "Eliminar clip"},
    "shortcuts.command.ripple_delete": {"fr": "Supprimer avec ripple", "en": "Ripple delete", "es": "Eliminar con ripple"},
    "shortcuts.command.toggle_clip_enabled": {"fr": "Activer / désactiver le clip", "en": "Enable / disable clip", "es": "Activar / desactivar clip"},
    "shortcuts.command.cut_at_playhead": {"fr": "Couper à la tête de lecture", "en": "Cut at playhead", "es": "Cortar en el cabezal"},
    "shortcuts.command.select_all": {"fr": "Tout sélectionner", "en": "Select all", "es": "Seleccionar todo"},
    "shortcuts.command.project_new": {"fr": "Nouveau projet", "en": "New project", "es": "Nuevo proyecto"},
    "shortcuts.command.project_open": {"fr": "Ouvrir un projet", "en": "Open project", "es": "Abrir proyecto"},
    "shortcuts.command.project_save": {"fr": "Enregistrer", "en": "Save", "es": "Guardar"},
    "shortcuts.command.project_save_as": {"fr": "Enregistrer sous…", "en": "Save as…", "es": "Guardar como…"},
    "shortcuts.command.quit": {"fr": "Quitter", "en": "Quit", "es": "Salir"},
    "shortcuts.command.frame_back": {"fr": "Image précédente", "en": "Previous frame", "es": "Fotograma anterior"},
    "shortcuts.command.frame_forward": {"fr": "Image suivante", "en": "Next frame", "es": "Fotograma siguiente"},
    "shortcuts.command.second_back": {"fr": "Reculer d'une seconde", "en": "Back one second", "es": "Retroceder un segundo"},
    "shortcuts.command.second_forward": {"fr": "Avancer d'une seconde", "en": "Forward one second", "es": "Avanzar un segundo"},
    "shortcuts.command.zoom_in": {"fr": "Zoom avant", "en": "Zoom in", "es": "Acercar"},
    "shortcuts.command.zoom_out": {"fr": "Zoom arrière", "en": "Zoom out", "es": "Alejar"},
    "shortcuts.command.zoom_fit": {"fr": "Ajuster la timeline", "en": "Fit timeline", "es": "Ajustar la línea de tiempo"},
    "shortcuts.command.toggle_scopes": {"fr": "Afficher / masquer les scopes", "en": "Show / hide scopes", "es": "Mostrar / ocultar scopes"},
    "shortcuts.command.preferences": {"fr": "Préférences", "en": "Preferences", "es": "Preferencias"},
    "shortcuts.command.audio_record_toggle": {"fr": "Démarrer / arrêter l'enregistrement", "en": "Start / stop recording", "es": "Iniciar / detener grabación"},
    "shortcuts.command.track_toggle_mute": {"fr": "Couper / rétablir la piste sélectionnée", "en": "Mute / unmute selected track", "es": "Silenciar / activar la pista seleccionada"},
    "shortcuts.command.audio_master_mute": {"fr": "Couper / rétablir le Master", "en": "Mute / unmute Master", "es": "Silenciar / activar Master"},
    "shortcuts.command.marker_add": {"fr": "Ajouter un marqueur", "en": "Add marker", "es": "Añadir marcador"},
    "shortcuts.command.marker_previous": {"fr": "Marqueur précédent", "en": "Previous marker", "es": "Marcador anterior"},
    "shortcuts.command.marker_next": {"fr": "Marqueur suivant", "en": "Next marker", "es": "Marcador siguiente"},
    "shortcuts.category.animation": {"fr": "Animation", "en": "Animation", "es": "Animación"},
    "shortcuts.category.sequences": {"fr": "Séquences", "en": "Sequences", "es": "Secuencias"},
    "shortcuts.command.sequence_new": {"fr": "Nouvelle séquence", "en": "New sequence", "es": "Nueva secuencia"},
    "shortcuts.command.sequence_nest_selection": {"fr": "Créer une séquence à partir de la sélection", "en": "Nest selection into a sequence", "es": "Crear una secuencia a partir de la selección"},
    "shortcuts.command.sequence_open_nested": {"fr": "Ouvrir la séquence imbriquée", "en": "Open nested sequence", "es": "Abrir la secuencia anidada"},
    "shortcuts.command.sequence_parent": {"fr": "Revenir à la séquence parente", "en": "Go to parent sequence", "es": "Volver a la secuencia principal"},
    "shortcuts.command.sequence_back": {"fr": "Séquence précédente", "en": "Previous sequence", "es": "Secuencia anterior"},
    "shortcuts.command.sequence_forward": {"fr": "Séquence suivante", "en": "Next sequence", "es": "Secuencia siguiente"},
    "shortcuts.command.keyframe_add": {"fr": "Ajouter une image-clé à la tête de lecture", "en": "Add keyframe at playhead", "es": "Añadir fotograma clave en el cabezal"},
    "shortcuts.command.keyframe_remove": {"fr": "Supprimer l'image-clé", "en": "Delete keyframe", "es": "Eliminar fotograma clave"},
    "shortcuts.command.keyframe_previous": {"fr": "Image-clé précédente", "en": "Previous keyframe", "es": "Fotograma clave anterior"},
    "shortcuts.command.keyframe_next": {"fr": "Image-clé suivante", "en": "Next keyframe", "es": "Fotograma clave siguiente"},
    "shortcuts.command.keyframe_select_all": {"fr": "Sélectionner toutes les images-clés", "en": "Select all keyframes", "es": "Seleccionar todos los fotogramas clave"},
    "shortcuts.command.keyframe_copy": {"fr": "Copier les images-clés", "en": "Copy keyframes", "es": "Copiar fotogramas clave"},
    "shortcuts.command.keyframe_paste": {"fr": "Coller les images-clés", "en": "Paste keyframes", "es": "Pegar fotogramas clave"},
    "shortcuts.command.keyframe_interpolation_hold": {"fr": "Interpolation : maintien", "en": "Interpolation: hold", "es": "Interpolación: mantener"},
    "shortcuts.command.keyframe_interpolation_linear": {"fr": "Interpolation : linéaire", "en": "Interpolation: linear", "es": "Interpolación: lineal"},
    "shortcuts.command.keyframe_interpolation_ease_in": {"fr": "Interpolation : accélération", "en": "Interpolation: ease in", "es": "Interpolación: aceleración"},
    "shortcuts.command.keyframe_interpolation_ease_out": {"fr": "Interpolation : décélération", "en": "Interpolation: ease out", "es": "Interpolación: desaceleración"},
    "shortcuts.command.keyframe_interpolation_ease_in_out": {"fr": "Interpolation : accélération et décélération", "en": "Interpolation: ease in-out", "es": "Interpolación: suave"},
    "shortcuts.command.keyframe_interpolation_bezier": {"fr": "Interpolation : Bézier", "en": "Interpolation: Bézier", "es": "Interpolación: Bézier"},
    "shortcuts.command.graph_editor": {"fr": "Éditeur de courbes", "en": "Graph Editor", "es": "Editor de curvas"},
    "animation.property.position_x": {"fr": "Position X", "en": "Position X", "es": "Posición X"},
    "animation.property.position_y": {"fr": "Position Y", "en": "Position Y", "es": "Posición Y"},
    "animation.property.scale": {"fr": "Échelle", "en": "Scale", "es": "Escala"},
    "animation.property.rotation": {"fr": "Rotation", "en": "Rotation", "es": "Rotación"},
    "animation.property.opacity": {"fr": "Opacité", "en": "Opacity", "es": "Opacidad"},
    "animation.interpolation.hold": {"fr": "Maintien", "en": "Hold", "es": "Mantener"},
    "animation.interpolation.linear": {"fr": "Linéaire", "en": "Linear", "es": "Lineal"},
    "animation.interpolation.ease_in": {"fr": "Accélération (Ease In)", "en": "Ease In", "es": "Aceleración (Ease In)"},
    "animation.interpolation.ease_out": {"fr": "Décélération (Ease Out)", "en": "Ease Out", "es": "Desaceleración (Ease Out)"},
    "animation.interpolation.ease_in_out": {"fr": "Ease In-Out", "en": "Ease In-Out", "es": "Ease In-Out"},
    "animation.interpolation.bezier": {"fr": "Bézier", "en": "Bézier", "es": "Bézier"},
    "animation.diamond.tooltip": {"fr": "{name} : clic pour ajouter ou retirer l'image-clé sous la tête de lecture ; clic droit pour plus d'options", "en": "{name}: click to add or remove the keyframe at the playhead; right-click for more", "es": "{name}: clic para añadir o quitar el fotograma clave en el cabezal; clic derecho para más"},
    "animation.previous": {"fr": "Image-clé précédente", "en": "Previous keyframe", "es": "Fotograma clave anterior"},
    "animation.next": {"fr": "Image-clé suivante", "en": "Next keyframe", "es": "Fotograma clave siguiente"},
    "animation.menu.enable": {"fr": "Activer l'animation", "en": "Enable animation", "es": "Activar animación"},
    "animation.menu.disable": {"fr": "Désactiver l'animation (garder la valeur actuelle)", "en": "Disable animation (keep current value)", "es": "Desactivar animación (mantener el valor)"},
    "animation.menu.interpolation": {"fr": "Interpolation", "en": "Interpolation", "es": "Interpolación"},
    "animation.menu.copy": {"fr": "Copier l'animation", "en": "Copy animation", "es": "Copiar animación"},
    "animation.menu.paste": {"fr": "Coller l'animation", "en": "Paste animation", "es": "Pegar animación"},
    "animation.menu.graph": {"fr": "Ouvrir dans l'éditeur de courbes", "en": "Open in Graph Editor", "es": "Abrir en el editor de curvas"},
    "animation.paste_incompatible": {"fr": "Ces images-clés ne sont pas compatibles avec cette propriété.", "en": "These keyframes are not compatible with this property.", "es": "Estos fotogramas clave no son compatibles con esta propiedad."},
    "graph.title": {"fr": "Éditeur de courbes", "en": "Graph Editor", "es": "Editor de curvas"},
    "graph.property": {"fr": "Propriété", "en": "Property", "es": "Propiedad"},
    "graph.no_clip": {"fr": "Sélectionnez un clip vidéo ou graphique.", "en": "Select a video or graphics clip.", "es": "Seleccione un clip de vídeo o gráfico."},
    "graph.not_animated": {"fr": "Propriété non animée : cliquez sur son losange dans l'inspecteur.", "en": "Not animated: click its diamond in the inspector.", "es": "No animada: haga clic en su rombo en el inspector."},
    "graph.frame_all": {"fr": "Tout cadrer", "en": "Frame all", "es": "Encuadrar todo"},
    "graph.frame_selected": {"fr": "Cadrer la sélection", "en": "Frame selected", "es": "Encuadrar selección"},
    "graph.time": {"fr": "Temps (s)", "en": "Time (s)", "es": "Tiempo (s)"},
    "graph.value": {"fr": "Valeur", "en": "Value", "es": "Valor"},
    "graph.interpolation": {"fr": "Interpolation", "en": "Interpolation", "es": "Interpolación"},
    "graph.tangents": {"fr": "Tangentes", "en": "Tangents", "es": "Tangentes"},
    "graph.tangents.linked": {"fr": "Liées", "en": "Linked", "es": "Vinculadas"},
    "graph.tangents.broken": {"fr": "Séparées", "en": "Broken", "es": "Separadas"},
    "graph.tangents.auto": {"fr": "Automatiques", "en": "Auto", "es": "Automáticas"},
    "graph.hint": {"fr": "Molette : zoom horizontal · Maj+molette : zoom vertical · clic milieu ou Alt+glisser : déplacer la vue", "en": "Wheel: horizontal zoom · Shift+wheel: vertical zoom · middle click or Alt+drag: pan", "es": "Rueda: zoom horizontal · Mayús+rueda: zoom vertical · clic central o Alt+arrastrar: desplazar"},
    "menu.item.graph_editor": {"fr": "Éditeur de courbes", "en": "Graph Editor", "es": "Editor de curvas"},
    "shortcuts.tab.general": {"fr": "Général", "en": "General", "es": "General"},
    "shortcuts.tab.shortcuts": {"fr": "Raccourcis", "en": "Shortcuts", "es": "Atajos"},
    "shortcuts.search": {"fr": "Rechercher une commande…", "en": "Search commands…", "es": "Buscar comandos…"},
    "shortcuts.all_categories": {"fr": "Toutes les catégories", "en": "All categories", "es": "Todas las categorías"},
    "shortcuts.col.command": {"fr": "Commande", "en": "Command", "es": "Comando"},
    "shortcuts.col.category": {"fr": "Catégorie", "en": "Category", "es": "Categoría"},
    "shortcuts.col.primary": {"fr": "Raccourci", "en": "Shortcut", "es": "Atajo"},
    "shortcuts.col.secondary": {"fr": "Secondaire", "en": "Secondary", "es": "Secundario"},
    "shortcuts.primary": {"fr": "Raccourci principal", "en": "Primary shortcut", "es": "Atajo principal"},
    "shortcuts.secondary": {"fr": "Raccourci secondaire", "en": "Secondary shortcut", "es": "Atajo secundario"},
    "shortcuts.clear": {"fr": "Retirer", "en": "Remove", "es": "Quitar"},
    "shortcuts.reset_command": {"fr": "Rétablir la valeur par défaut", "en": "Restore default", "es": "Restablecer valor"},
    "shortcuts.reset_all": {"fr": "Tout réinitialiser", "en": "Reset all", "es": "Restablecer todo"},
    "shortcuts.reset_all_confirm": {"fr": "Rétablir tous les raccourcis par défaut ?", "en": "Restore every shortcut to its default?", "es": "¿Restablecer todos los atajos?"},
    "shortcuts.reassign": {"fr": "Réassigner", "en": "Reassign", "es": "Reasignar"},
    "shortcuts.select_prompt": {"fr": "Sélectionnez une commande pour modifier son raccourci.", "en": "Select a command to change its shortcut.", "es": "Seleccione un comando para cambiar su atajo."},
    "shortcuts.none": {"fr": "—", "en": "—", "es": "—"},
    "shortcuts.press_keys": {"fr": "Appuyez sur la combinaison…", "en": "Press the key combination…", "es": "Pulse la combinación…"},
    "shortcuts.msg.conflict": {"fr": "{sequence} est déjà utilisé par « {command} ».", "en": "{sequence} is already used by “{command}”.", "es": "{sequence} ya lo usa «{command}»."},
    "shortcuts.msg.reassigned": {"fr": "{sequence} retiré à « {command} ».", "en": "{sequence} removed from “{command}”.", "es": "{sequence} quitado de «{command}»."},
    "shortcuts.msg.reserved.clipboard": {"fr": "{sequence} est réservé au copier / couper / coller.", "en": "{sequence} is reserved for copy / cut / paste.", "es": "{sequence} está reservado para copiar / cortar / pegar."},
    "shortcuts.msg.reserved.escape": {"fr": "{sequence} est réservé à la fermeture des dialogues.", "en": "{sequence} is reserved for closing dialogs.", "es": "{sequence} está reservado para cerrar diálogos."},
    "shortcuts.msg.reserved.focus": {"fr": "{sequence} est réservé à la navigation au clavier.", "en": "{sequence} is reserved for keyboard navigation.", "es": "{sequence} está reservado para la navegación con teclado."},
    "shortcuts.msg.reserved.system": {"fr": "{sequence} est réservé par le système.", "en": "{sequence} is reserved by the operating system.", "es": "{sequence} está reservado por el sistema."},
    "shortcuts.msg.invalid": {"fr": "Raccourci non valide.", "en": "Invalid shortcut.", "es": "Atajo no válido."},
    "shortcuts.msg.duplicate": {"fr": "{sequence} est déjà le raccourci de cette commande.", "en": "{sequence} is already assigned to this command.", "es": "{sequence} ya está asignado a este comando."},
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
    "prefs.render": {"fr": "Rendu d'aperçu", "en": "Preview rendering", "es": "Renderizado de vista previa"},
    "prefs.render.draft": {"fr": "Brouillon", "en": "Draft", "es": "Borrador"},
    "prefs.render.standard": {"fr": "Standard", "en": "Standard", "es": "Estándar"},
    "prefs.render.high": {"fr": "Haute", "en": "High", "es": "Alta"},
    "preview.computing": {"fr": "Calcul de l'aperçu…", "en": "Computing preview…", "es": "Calculando vista previa…"},
    "preview.cached": {"fr": "Aperçu en cache", "en": "Cached preview", "es": "Vista previa en caché"},
    "preview.source_fallback": {"fr": "Source (aperçu en cours de calcul)", "en": "Source (preview rendering)", "es": "Fuente (renderizando vista previa)"},
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

    # --- Effets audio non destructifs (tâche 27) -----------------------
    "audio_effects.section": {
        "fr": "Effets audio",
        "en": "Audio effects",
        "es": "Efectos de audio",
    },
    "audio_effects.no_clip": {
        "fr": "Sélectionnez un clip audio ou vidéo pour gérer ses effets.",
        "en": "Select an audio or video clip to manage its effects.",
        "es": "Selecciona un clip de audio o vídeo para gestionar sus efectos.",
    },
    "audio_effects.empty": {
        "fr": "Aucun effet audio sur ce clip.",
        "en": "No audio effects on this clip.",
        "es": "Este clip no tiene efectos de audio.",
    },
    "audio_effects.add": {
        "fr": "Ajouter",
        "en": "Add",
        "es": "Añadir",
    },
    "audio_effects.disable": {
        "fr": "Désactiver",
        "en": "Disable",
        "es": "Desactivar",
    },
    "audio_effects.enable": {
        "fr": "Activer",
        "en": "Enable",
        "es": "Activar",
    },
    "audio_effects.remove": {
        "fr": "Supprimer",
        "en": "Remove",
        "es": "Eliminar",
    },
    "audio_effects.move_up": {
        "fr": "Monter",
        "en": "Move up",
        "es": "Subir",
    },
    "audio_effects.move_down": {
        "fr": "Descendre",
        "en": "Move down",
        "es": "Bajar",
    },
    # Noms courts des presets (utilisés dans le combo d'ajout).
    "audio_effects.preset.normalize.name": {
        "fr": "Normalisation",
        "en": "Normalize",
        "es": "Normalización",
    },
    "audio_effects.preset.voice_enhance.name": {
        "fr": "Amélioration de voix",
        "en": "Voice enhance",
        "es": "Mejora de voz",
    },
    "audio_effects.preset.noise_reduce.name": {
        "fr": "Réduction de bruit",
        "en": "Noise reduction",
        "es": "Reducción de ruido",
    },
    "audio_effects.preset.compressor.name": {
        "fr": "Compresseur",
        "en": "Compressor",
        "es": "Compresor",
    },
    "audio_effects.preset.limiter.name": {
        "fr": "Limiteur anti-saturation",
        "en": "Limiter",
        "es": "Limitador",
    },
    "audio_effects.preset.bass_boost.name": {
        "fr": "Renforcement des basses",
        "en": "Bass boost",
        "es": "Refuerzo de graves",
    },
    "audio_effects.preset.treble_boost.name": {
        "fr": "Clarté des aigus",
        "en": "Treble boost",
        "es": "Claridad de agudos",
    },
    "audio_effects.preset.phone_effect.name": {
        "fr": "Effet téléphone",
        "en": "Phone effect",
        "es": "Efecto teléfono",
    },
    "audio_effects.preset.reverb_light.name": {
        "fr": "Réverbération légère",
        "en": "Light reverb",
        "es": "Reverberación ligera",
    },
    "audio_effects.preset.echo_light.name": {
        "fr": "Écho léger",
        "en": "Light echo",
        "es": "Eco ligero",
    },
    # Paramètres (utilisés dans le rack).
    "audio_effects.param.integrated_loudness": {
        "fr": "Loudness cible (LUFS)",
        "en": "Target loudness (LUFS)",
        "es": "Loudness objetivo (LUFS)",
    },
    "audio_effects.param.loudness_range": {
        "fr": "Plage dynamique (LU)",
        "en": "Loudness range (LU)",
        "es": "Rango dinámico (LU)",
    },
    "audio_effects.param.true_peak": {
        "fr": "Plafond crête (dB)",
        "en": "True peak (dB)",
        "es": "Pico real (dB)",
    },
    "audio_effects.param.frequency": {
        "fr": "Fréquence (Hz)",
        "en": "Frequency (Hz)",
        "es": "Frecuencia (Hz)",
    },
    "audio_effects.param.intensity": {
        "fr": "Intensité",
        "en": "Intensity",
        "es": "Intensidad",
    },
    "audio_effects.param.noise_floor_db": {
        "fr": "Plancher de bruit (dB)",
        "en": "Noise floor (dB)",
        "es": "Piso de ruido (dB)",
    },
    "audio_effects.param.strength": {
        "fr": "Force",
        "en": "Strength",
        "es": "Fuerza",
    },
    "audio_effects.param.threshold_db": {
        "fr": "Seuil (dB)",
        "en": "Threshold (dB)",
        "es": "Umbral (dB)",
    },
    "audio_effects.param.ratio": {
        "fr": "Ratio",
        "en": "Ratio",
        "es": "Ratio",
    },
    "audio_effects.param.attack_ms": {
        "fr": "Attaque (ms)",
        "en": "Attack (ms)",
        "es": "Ataque (ms)",
    },
    "audio_effects.param.release_ms": {
        "fr": "Relâchement (ms)",
        "en": "Release (ms)",
        "es": "Soltura (ms)",
    },
    "audio_effects.param.makeup_db": {
        "fr": "Maquillage (dB)",
        "en": "Make-up (dB)",
        "es": "Maquillaje (dB)",
    },
    "audio_effects.param.limit_db": {
        "fr": "Plafond (dB)",
        "en": "Limit (dB)",
        "es": "Límite (dB)",
    },
    "audio_effects.param.gain_db": {
        "fr": "Gain (dB)",
        "en": "Gain (dB)",
        "es": "Ganancia (dB)",
    },
    "audio_effects.param.frequency_hz": {
        "fr": "Fréquence centrale (Hz)",
        "en": "Center frequency (Hz)",
        "es": "Frecuencia central (Hz)",
    },
    "audio_effects.param.center_hz": {
        "fr": "Fréquence centrale (Hz)",
        "en": "Center frequency (Hz)",
        "es": "Frecuencia central (Hz)",
    },
    "audio_effects.param.bandwidth_hz": {
        "fr": "Largeur de bande (Hz)",
        "en": "Bandwidth (Hz)",
        "es": "Ancho de banda (Hz)",
    },
    "audio_effects.param.mix": {
        "fr": "Mix",
        "en": "Mix",
        "es": "Mezcla",
    },
    "audio_effects.param.in_gain": {
        "fr": "Gain d'entrée",
        "en": "Input gain",
        "es": "Ganancia de entrada",
    },
    "audio_effects.param.out_gain": {
        "fr": "Gain de sortie",
        "en": "Output gain",
        "es": "Ganancia de salida",
    },
    "audio_effects.param.delays_ms": {
        "fr": "Retards (ms)",
        "en": "Delays (ms)",
        "es": "Retardos (ms)",
    },
    "audio_effects.param.decays": {
        "fr": "Décroissance",
        "en": "Decay",
        "es": "Decaimiento",
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

    # --- Catégories de la bibliothèque d'effets (tâche 22) -------------
    "effects.category.color": {
        "fr": "Couleur",
        "en": "Color",
        "es": "Color",
    },
    "effects.category.creative": {
        "fr": "Créatif",
        "en": "Creative",
        "es": "Creativo",
    },
    "effects.category.stylized": {
        "fr": "Stylisé",
        "en": "Stylized",
        "es": "Estilizado",
    },
    "effects.category.look": {
        "fr": "Look",
        "en": "Look",
        "es": "Look",
    },
    "effects.category.all": {
        "fr": "Toutes catégories",
        "en": "All categories",
        "es": "Todas las categorías",
    },

    # --- Présets intégrés (tâche 22) ------------------------------------
    "effects.preset.cinema.name": {
        "fr": "Cinéma",
        "en": "Cinema",
        "es": "Cine",
    },
    "effects.preset.cinema.description": {
        "fr": "Contraste poussé, saturation contenue, vignette douce.",
        "en": "Pushed contrast, contained saturation, soft vignette.",
        "es": "Contraste marcado, saturación contenida, viñeta suave.",
    },
    "effects.preset.black_and_white.name": {
        "fr": "Noir et blanc",
        "en": "Black & white",
        "es": "Blanco y negro",
    },
    "effects.preset.black_and_white.description": {
        "fr": "Conversion en niveaux de gris avec regain de contraste.",
        "en": "Grayscale conversion with a contrast boost.",
        "es": "Conversión a escala de grises con contraste reforzado.",
    },
    "effects.preset.vintage.name": {
        "fr": "Vintage",
        "en": "Vintage",
        "es": "Vintage",
    },
    "effects.preset.vintage.description": {
        "fr": "Virage sépia, contraste léger et vignette marquée.",
        "en": "Sepia tone, slight contrast and strong vignette.",
        "es": "Tono sepia, contraste ligero y viñeta marcada.",
    },
    "effects.preset.sharp.name": {
        "fr": "Net",
        "en": "Sharp",
        "es": "Nítido",
    },
    "effects.preset.sharp.description": {
        "fr": "Accentue les contours et booste le contraste.",
        "en": "Accentuates edges and boosts contrast.",
        "es": "Acentúa los bordes y aumenta el contraste.",
    },
    "effects.preset.blur.name": {
        "fr": "Flou",
        "en": "Blur",
        "es": "Desenfoque",
    },
    "effects.preset.blur.description": {
        "fr": "Adoucit l'image avec un flou gaussien maîtrisé.",
        "en": "Softens the image with a controlled gaussian blur.",
        "es": "Suaviza la imagen con un desenfoque gaussiano controlado.",
    },
    "effects.preset.cool_grade.name": {
        "fr": "Étalonnage froid",
        "en": "Cool grade",
        "es": "Etalonaje frío",
    },
    "effects.preset.cool_grade.description": {
        "fr": "Saturation réduite et luminosité légèrement poussée.",
        "en": "Reduced saturation and a slight brightness boost.",
        "es": "Saturación reducida y brillo ligeramente aumentado.",
    },
    "effects.preset.warm_grade.name": {
        "fr": "Étalonnage chaud",
        "en": "Warm grade",
        "es": "Etalonaje cálido",
    },
    "effects.preset.warm_grade.description": {
        "fr": "Saturation renforcée et contraste légèrement poussé.",
        "en": "Boosted saturation and slightly pushed contrast.",
        "es": "Saturación reforzada y contraste ligeramente marcado.",
    },
    "effects.preset.noir.name": {
        "fr": "Film noir",
        "en": "Film noir",
        "es": "Cine negro",
    },
    "effects.preset.noir.description": {
        "fr": "Noir et blanc contrasté et vignette intense.",
        "en": "High-contrast black & white with deep vignette.",
        "es": "Blanco y negro contrastado con viñeta intensa.",
    },

    # --- Bibliothèque d'effets : actions & libellés UI (tâche 22) -------
    "effects.library.title": {
        "fr": "Bibliothèque d'effets",
        "en": "Effects library",
        "es": "Biblioteca de efectos",
    },
    "effects.library.search": {
        "fr": "Rechercher un effet ou un preset…",
        "en": "Search an effect or preset…",
        "es": "Buscar un efecto o preset…",
    },
    "effects.library.section.builtin": {
        "fr": "Préréglages",
        "en": "Presets",
        "es": "Preajustes",
    },
    "effects.library.section.user": {
        "fr": "Mes presets",
        "en": "My presets",
        "es": "Mis presets",
    },
    "effects.library.apply": {
        "fr": "Appliquer au clip",
        "en": "Apply to clip",
        "es": "Aplicar al clip",
    },
    "effects.library.apply_hint": {
        "fr": "Sélectionnez un clip vidéo dans la timeline pour appliquer un preset.",
        "en": "Select a video clip in the timeline to apply a preset.",
        "es": "Selecciona un clip de vídeo en la línea de tiempo para aplicar un preset.",
    },
    "effects.library.no_results": {
        "fr": "Aucun preset ne correspond à votre recherche.",
        "en": "No preset matches your search.",
        "es": "Ningún preset coincide con tu búsqueda.",
    },
    "effects.library.save": {
        "fr": "Enregistrer comme preset",
        "en": "Save as preset",
        "es": "Guardar como preset",
    },
    "effects.library.delete": {
        "fr": "Supprimer ce preset",
        "en": "Delete this preset",
        "es": "Eliminar este preset",
    },
    "effects.library.user_empty": {
        "fr": "Aucun preset enregistré. Sélectionnez un clip, appliquez des effets puis cliquez sur « Enregistrer comme preset ».",
        "en": "No preset saved yet. Select a clip, apply effects then click “Save as preset”.",
        "es": "Aún no hay presets guardados. Selecciona un clip, aplica efectos y luego pulsa «Guardar como preset».",
    },
    "effects.library.user_builtin_lock": {
        "fr": "Les préréglages ne peuvent pas être supprimés.",
        "en": "Built-in presets cannot be deleted.",
        "es": "Los preajustes no se pueden eliminar.",
    },
    "effects.library.dialog.title": {
        "fr": "Enregistrer un preset",
        "en": "Save preset",
        "es": "Guardar preset",
    },
    "effects.library.dialog.name": {
        "fr": "Nom du preset",
        "en": "Preset name",
        "es": "Nombre del preset",
    },
    "effects.library.dialog.description": {
        "fr": "Description (optionnelle)",
        "en": "Description (optional)",
        "es": "Descripción (opcional)",
    },
    "effects.library.dialog.category": {
        "fr": "Catégorie",
        "en": "Category",
        "es": "Categoría",
    },
    "effects.library.dialog.save": {
        "fr": "Enregistrer",
        "en": "Save",
        "es": "Guardar",
    },
    "effects.library.dialog.cancel": {
        "fr": "Annuler",
        "en": "Cancel",
        "es": "Cancelar",
    },
    "effects.library.no_effects_to_save": {
        "fr": "Le clip sélectionné n'a aucun effet à enregistrer.",
        "en": "The selected clip has no effect to save.",
        "es": "El clip seleccionado no tiene efectos para guardar.",
    },
    "effects.library.delete_confirm": {
        "fr": "Supprimer le preset « {name} » ?",
        "en": "Delete preset “{name}”?",
        "es": "¿Eliminar el preset «{name}»?",
    },

    # --- Présets de transitions (tâche 23) -------------------------------
    "transitions.preset.crossfade.name": {
        "fr": "Fondu enchaîné",
        "en": "Crossfade",
        "es": "Encadenado",
    },
    "transitions.preset.crossfade.description": {
        "fr": "Mixe les deux clips : sortie qui s'estompe, entrée qui apparaît.",
        "en": "Blends the two clips: fading out while fading in.",
        "es": "Mezcla los dos clips: salida que se desvanece, entrada que aparece.",
    },
    "transitions.preset.fade_black.name": {
        "fr": "Fondu au noir",
        "en": "Fade to black",
        "es": "Fundido a negro",
    },
    "transitions.preset.fade_black.description": {
        "fr": "Bascule via un écran noir entre les deux clips.",
        "en": "Switches through a black screen between the two clips.",
        "es": "Cambia a través de una pantalla negra entre los dos clips.",
    },
    "transitions.preset.wipe_left.name": {
        "fr": "Balayage gauche",
        "en": "Wipe left",
        "es": "Barrido a la izquierda",
    },
    "transitions.preset.wipe_left.description": {
        "fr": "Le nouveau clip balaie l'ancien vers la gauche.",
        "en": "The new clip wipes the old one to the left.",
        "es": "El clip nuevo barre el antiguo hacia la izquierda.",
    },
    "transitions.preset.wipe_right.name": {
        "fr": "Balayage droite",
        "en": "Wipe right",
        "es": "Barrido a la derecha",
    },
    "transitions.preset.wipe_right.description": {
        "fr": "Le nouveau clip balaie l'ancien vers la droite.",
        "en": "The new clip wipes the old one to the right.",
        "es": "El clip nuevo barre el antiguo hacia la derecha.",
    },

    # --- Présets de transitions : catalogue étendu (tâche 26) -----------
    "transitions.preset.wipe_up.name": {
        "fr": "Balayage haut",
        "en": "Wipe up",
        "es": "Barrido hacia arriba",
    },
    "transitions.preset.wipe_up.description": {
        "fr": "Le nouveau clip balaie l'ancien vers le haut.",
        "en": "The new clip wipes the old one upward.",
        "es": "El clip nuevo barre el antiguo hacia arriba.",
    },
    "transitions.preset.wipe_down.name": {
        "fr": "Balayage bas",
        "en": "Wipe down",
        "es": "Barrido hacia abajo",
    },
    "transitions.preset.wipe_down.description": {
        "fr": "Le nouveau clip balaie l'ancien vers le bas.",
        "en": "The new clip wipes the old one downward.",
        "es": "El clip nuevo barre el antiguo hacia abajo.",
    },
    "transitions.preset.slide_left.name": {
        "fr": "Glissement gauche",
        "en": "Slide left",
        "es": "Deslizamiento a la izquierda",
    },
    "transitions.preset.slide_left.description": {
        "fr": "Le nouveau clip glisse depuis la droite et pousse l'ancien.",
        "en": "The new clip slides in from the right, pushing the old one out.",
        "es": "El clip nuevo entra deslizándose desde la derecha y empuja al antiguo.",
    },
    "transitions.preset.slide_right.name": {
        "fr": "Glissement droite",
        "en": "Slide right",
        "es": "Deslizamiento a la derecha",
    },
    "transitions.preset.slide_right.description": {
        "fr": "Le nouveau clip glisse depuis la gauche et pousse l'ancien.",
        "en": "The new clip slides in from the left, pushing the old one out.",
        "es": "El clip nuevo entra deslizándose desde la izquierda y empuja al antiguo.",
    },
    "transitions.preset.slide_up.name": {
        "fr": "Glissement haut",
        "en": "Slide up",
        "es": "Deslizamiento hacia arriba",
    },
    "transitions.preset.slide_up.description": {
        "fr": "Le nouveau clip glisse depuis le bas et pousse l'ancien.",
        "en": "The new clip slides in from the bottom, pushing the old one up.",
        "es": "El clip nuevo entra deslizándose desde abajo y empuja al antiguo hacia arriba.",
    },
    "transitions.preset.slide_down.name": {
        "fr": "Glissement bas",
        "en": "Slide down",
        "es": "Deslizamiento hacia abajo",
    },
    "transitions.preset.slide_down.description": {
        "fr": "Le nouveau clip glisse depuis le haut et pousse l'ancien.",
        "en": "The new clip slides in from the top, pushing the old one down.",
        "es": "El clip nuevo entra deslizándose desde arriba y empuja al antiguo hacia abajo.",
    },
    "transitions.preset.circle_open.name": {
        "fr": "Cercle ouverture",
        "en": "Circle open",
        "es": "Apertura circular",
    },
    "transitions.preset.circle_open.description": {
        "fr": "Un cercle s'ouvre depuis le centre vers les bords.",
        "en": "A circle opens from the center to the edges.",
        "es": "Un círculo se abre desde el centro hacia los bordes.",
    },
    "transitions.preset.circle_close.name": {
        "fr": "Cercle fermeture",
        "en": "Circle close",
        "es": "Cierre circular",
    },
    "transitions.preset.circle_close.description": {
        "fr": "Un cercle se referme depuis les bords vers le centre.",
        "en": "A circle closes from the edges to the center.",
        "es": "Un círculo se cierra desde los bordes hacia el centro.",
    },
    "transitions.preset.dissolve.name": {
        "fr": "Dissolution",
        "en": "Dissolve",
        "es": "Disolución",
    },
    "transitions.preset.dissolve.description": {
        "fr": "Le clip sortant se décompose en particules pour révéler l'entrant.",
        "en": "The outgoing clip dissolves into particles to reveal the incoming one.",
        "es": "El clip saliente se disuelve en partículas para revelar al entrante.",
    },
    "transitions.preset.pixelize.name": {
        "fr": "Pixellisation",
        "en": "Pixelize",
        "es": "Pixelización",
    },
    "transitions.preset.pixelize.description": {
        "fr": "L'image se pixellise puis se recompose avec le clip entrant.",
        "en": "The image pixelizes and re-composes with the incoming clip.",
        "es": "La imagen se pixela y se recompone con el clip entrante.",
    },
    "transitions.preset.radial.name": {
        "fr": "Transition radiale",
        "en": "Radial transition",
        "es": "Transición radial",
    },
    "transitions.preset.radial.description": {
        "fr": "Balayage circulaire du centre vers les bords du cadre.",
        "en": "A radial sweep from the center toward the edges of the frame.",
        "es": "Barrido circular del centro hacia los bordes del cuadro.",
    },
    "transitions.preset.fade_white.name": {
        "fr": "Fondu au blanc",
        "en": "Fade to white",
        "es": "Fundido a blanco",
    },
    "transitions.preset.fade_white.description": {
        "fr": "Bascule via un écran blanc entre les deux clips.",
        "en": "Switches through a white screen between the two clips.",
        "es": "Cambia a través de una pantalla blanca entre los dos clips.",
    },
    "transitions.preset.smooth_left.name": {
        "fr": "Glissement fluide gauche",
        "en": "Smooth slide left",
        "es": "Deslizamiento suave a la izquierda",
    },
    "transitions.preset.smooth_left.description": {
        "fr": "Glissement doux vers la gauche avec un léger fondu.",
        "en": "Soft slide to the left with a gentle dissolve.",
        "es": "Deslizamiento suave hacia la izquierda con un ligero fundido.",
    },
    "transitions.preset.smooth_right.name": {
        "fr": "Glissement fluide droite",
        "en": "Smooth slide right",
        "es": "Deslizamiento suave a la derecha",
    },
    "transitions.preset.smooth_right.description": {
        "fr": "Glissement doux vers la droite avec un léger fondu.",
        "en": "Soft slide to the right with a gentle dissolve.",
        "es": "Deslizamiento suave hacia la derecha con un ligero fundido.",
    },

    # --- Catégories de la bibliothèque de transitions --------------------
    "transitions.category.fade": {
        "fr": "Fondus",
        "en": "Fades",
        "es": "Fundidos",
    },
    "transitions.category.wipe": {
        "fr": "Balayages",
        "en": "Wipes",
        "es": "Barridos",
    },
    "transitions.category.shape": {
        "fr": "Formes",
        "en": "Shapes",
        "es": "Formas",
    },
    "transitions.category.dissolve": {
        "fr": "Dissolutions",
        "en": "Dissolves",
        "es": "Disoluciones",
    },
    "transitions.category.smooth": {
        "fr": "Glissements fluides",
        "en": "Smooth slides",
        "es": "Deslizamientos suaves",
    },
    "transitions.category.all": {
        "fr": "Toutes catégories",
        "en": "All categories",
        "es": "Todas las categorías",
    },
    "transitions.category.favorites": {
        "fr": "Favoris",
        "en": "Favorites",
        "es": "Favoritos",
    },

    # --- Bibliothèque de transitions : actions & libellés UI -------------
    "transitions.library.title": {
        "fr": "Bibliothèque de transitions",
        "en": "Transitions library",
        "es": "Biblioteca de transiciones",
    },
    "transitions.library.search": {
        "fr": "Rechercher une transition…",
        "en": "Search a transition…",
        "es": "Buscar una transición…",
    },
    "transitions.library.section.builtin": {
        "fr": "Préréglages",
        "en": "Presets",
        "es": "Preajustes",
    },
    "transitions.library.section.user": {
        "fr": "Mes transitions",
        "en": "My transitions",
        "es": "Mis transiciones",
    },
    "transitions.library.apply": {
        "fr": "Ajouter la transition",
        "en": "Add transition",
        "es": "Añadir transición",
    },
    "transitions.library.apply_hint": {
        "fr": "Sélectionnez deux clips vidéo consécutifs, puis choisissez un preset.",
        "en": "Select two consecutive video clips, then pick a preset.",
        "es": "Selecciona dos clips de vídeo consecutivos y elige un preset.",
    },
    "transitions.library.no_results": {
        "fr": "Aucun preset ne correspond à votre recherche.",
        "en": "No preset matches your search.",
        "es": "Ningún preset coincide con tu búsqueda.",
    },
    "transitions.library.user_empty": {
        "fr": "Aucune transition personnalisée. Cliquez sur « Enregistrer comme preset » pour capturer la sélection actuelle.",
        "en": "No custom transition. Click “Save as preset” to capture the current selection.",
        "es": "Sin transiciones personalizadas. Pulsa «Guardar como preset» para capturar la selección actual.",
    },
    "transitions.library.favorites_empty": {
        "fr": "Aucun favori. Cliquez sur l'étoile d'un preset pour le retrouver ici.",
        "en": "No favorite yet. Click the star on a preset to add it here.",
        "es": "Sin favoritos. Pulsa la estrella de un preset para añadirlo aquí.",
    },
    "transitions.library.favorite_add": {
        "fr": "Ajouter aux favoris",
        "en": "Add to favorites",
        "es": "Añadir a favoritos",
    },
    "transitions.library.favorite_remove": {
        "fr": "Retirer des favoris",
        "en": "Remove from favorites",
        "es": "Quitar de favoritos",
    },
    "transitions.library.save": {
        "fr": "Enregistrer comme preset",
        "en": "Save as preset",
        "es": "Guardar como preset",
    },
    "transitions.library.delete": {
        "fr": "Supprimer ce preset",
        "en": "Delete this preset",
        "es": "Eliminar este preset",
    },
    "transitions.library.save_dialog.title": {
        "fr": "Enregistrer une transition",
        "en": "Save transition",
        "es": "Guardar transición",
    },
    "transitions.library.save_dialog.name": {
        "fr": "Nom de la transition",
        "en": "Transition name",
        "es": "Nombre de la transición",
    },
    "transitions.library.save_dialog.description": {
        "fr": "Description (optionnelle)",
        "en": "Description (optional)",
        "es": "Descripción (opcional)",
    },
    "transitions.library.save_dialog.type": {
        "fr": "Type",
        "en": "Type",
        "es": "Tipo",
    },
    "transitions.library.save_dialog.duration": {
        "fr": "Durée par défaut",
        "en": "Default duration",
        "es": "Duración por defecto",
    },
    "transitions.library.save_dialog.save": {
        "fr": "Enregistrer",
        "en": "Save",
        "es": "Guardar",
    },
    "transitions.library.save_dialog.cancel": {
        "fr": "Annuler",
        "en": "Cancel",
        "es": "Cancelar",
    },
    "transitions.library.delete_confirm": {
        "fr": "Supprimer la transition « {name} » ?",
        "en": "Delete transition “{name}”?",
        "es": "¿Eliminar la transición «{name}»?",
    },
    "transitions.library.duration_label": {
        "fr": "Durée",
        "en": "Duration",
        "es": "Duración",
    },

    # --- Scopes vidéo / monitoring couleur (tâche 31) ------------------
    "scopes.placeholder": {
        "fr": "Aucune image analysée",
        "en": "No frame analyzed",
        "es": "Ningún fotograma analizado",
    },
    "scopes.layout.quad": {
        "fr": "4 vues",
        "en": "4 views",
        "es": "4 vistas",
    },
    "scopes.layout.single": {
        "fr": "Vue unique",
        "en": "Single view",
        "es": "Vista única",
    },
    "scopes.view.histogram": {
        "fr": "Histogramme",
        "en": "Histogram",
        "es": "Histograma",
    },
    "scopes.view.waveform": {
        "fr": "Waveform",
        "en": "Waveform",
        "es": "Forma de onda",
    },
    "scopes.view.parade": {
        "fr": "Parade",
        "en": "Parade",
        "es": "Parada",
    },
    "scopes.view.vectorscope": {
        "fr": "Vectorscope",
        "en": "Vectorscope",
        "es": "Vectorscopio",
    },
    "scopes.levels.video": {
        "fr": "Niveaux vidéo (16‑235)",
        "en": "Video levels (16‑235)",
        "es": "Niveles de vídeo (16‑235)",
    },
    "scopes.levels.full": {
        "fr": "Niveaux complets (0‑255)",
        "en": "Full levels (0‑255)",
        "es": "Niveles completos (0‑255)",
    },
    "scopes.refresh": {
        "fr": "Analyser",
        "en": "Analyze",
        "es": "Analizar",
    },
    "scopes.alert.black": {
        "fr": "⚠ Noirs écrêtés",
        "en": "⚠ Blacks clipped",
        "es": "⚠ Negros recortados",
    },
    "scopes.alert.white": {
        "fr": "⚠ Hautes lumières écrêtées",
        "en": "⚠ Highlights clipped",
        "es": "⚠ Altas luces recortadas",
    },
    "transitions.library.user_builtin_lock": {
        "fr": "Les préréglages ne peuvent pas être supprimés.",
        "en": "Built-in presets cannot be deleted.",
        "es": "Los preajustes no se pueden eliminar.",
    },
    "transitions.library.two_clips_required": {
        "fr": "Sélectionnez deux clips vidéo pour appliquer une transition.",
        "en": "Select two video clips to apply a transition.",
        "es": "Selecciona dos clips de vídeo para aplicar una transición.",
    },

    # --- Bibliothèque de modèles de texte (tâche 24) -----------------
    "text.library.search": {
        "fr": "Rechercher un modèle…",
        "en": "Search a preset…",
        "es": "Buscar un modelo…",
    },
    "text.library.section.builtin": {
        "fr": "Préréglages",
        "en": "Presets",
        "es": "Preajustes",
    },
    "text.library.section.user": {
        "fr": "Mes modèles",
        "en": "My presets",
        "es": "Mis modelos",
    },
    "text.library.no_results": {
        "fr": "Aucun modèle ne correspond à votre recherche.",
        "en": "No preset matches your search.",
        "es": "Ningún modelo coincide con tu búsqueda.",
    },
    "text.library.user_empty": {
        "fr": "Aucun modèle enregistré. Cliquez sur « Enregistrer comme modèle » après avoir appliqué des effets.",
        "en": "No preset saved yet. Click “Save as preset” after applying effects.",
        "es": "Sin modelos guardados. Pulsa «Guardar como modelo» tras aplicar efectos.",
    },
    "text.library.apply_to_clip": {
        "fr": "Appliquer au clip",
        "en": "Apply to clip",
        "es": "Aplicar al clip",
    },
    "text.library.new_clip": {
        "fr": "Nouveau clip au playhead",
        "en": "New clip at playhead",
        "es": "Nuevo clip en el cabezal",
    },
    "text.library.save": {
        "fr": "Enregistrer comme modèle",
        "en": "Save as preset",
        "es": "Guardar como modelo",
    },
    "text.library.delete": {
        "fr": "Supprimer ce modèle",
        "en": "Delete this preset",
        "es": "Eliminar este modelo",
    },
    "text.library.new_subtitle": {
        "fr": "Nouveau sous-titre",
        "en": "New subtitle",
        "es": "Nuevo subtítulo",
    },
    "text.library.user_builtin_lock": {
        "fr": "Les préréglages ne peuvent pas être supprimés.",
        "en": "Built-in presets cannot be deleted.",
        "es": "Los preajustes no se pueden eliminar.",
    },
    "text.library.save_dialog.title": {
        "fr": "Enregistrer un modèle de texte",
        "en": "Save text preset",
        "es": "Guardar modelo de texto",
    },
    "text.library.save_dialog.name": {
        "fr": "Nom du modèle",
        "en": "Preset name",
        "es": "Nombre del modelo",
    },
    "text.library.save_dialog.description": {
        "fr": "Description (optionnelle)",
        "en": "Description (optional)",
        "es": "Descripción (opcional)",
    },
    "text.library.save_dialog.text": {
        "fr": "Texte par défaut",
        "en": "Default text",
        "es": "Texto por defecto",
    },
    "text.library.delete_confirm": {
        "fr": "Supprimer le modèle « {name} » ?",
        "en": "Delete preset “{name}”?",
        "es": "¿Eliminar el modelo «{name}»?",
    },
    "text.library.reset_style": {
        "fr": "Style réinitialisé.",
        "en": "Style reset.",
        "es": "Estilo reiniciado.",
    },

    # --- Présets de texte intégrés (tâche 24) -------------------------
    "text.preset.standard_subtitle.name": {
        "fr": "Sous-titre standard",
        "en": "Standard subtitle",
        "es": "Subtítulo estándar",
    },
    "text.preset.standard_subtitle.description": {
        "fr": "Sous-titre par défaut en bas, centré, lisible sur tout fond.",
        "en": "Default bottom-centered subtitle, readable on any background.",
        "es": "Subtítulo por defecto abajo, centrado, legible en cualquier fondo.",
    },
    "text.preset.title.name": {
        "fr": "Titre",
        "en": "Title",
        "es": "Título",
    },
    "text.preset.title.description": {
        "fr": "Titre d'ouverture haut centré avec fond sombre translucide.",
        "en": "Top-centered opening title with dark translucent background.",
        "es": "Título superior centrado con fondo oscuro translúcido.",
    },
    "text.preset.centered_title.name": {
        "fr": "Titre centré",
        "en": "Centered title",
        "es": "Título centrado",
    },
    "text.preset.centered_title.description": {
        "fr": "Titre plein cadre, contour prononcé pour ressortir.",
        "en": "Full-frame title with strong outline.",
        "es": "Título a pantalla completa con contorno marcado.",
    },
    "text.preset.lower_third.name": {
        "fr": "Carton inférieur",
        "en": "Lower third",
        "es": "Cartel inferior",
    },
    "text.preset.lower_third.description": {
        "fr": "Carton bas-gauche typique des reportages, fond coloré.",
        "en": "Lower-left banner typical of news reports, colored background.",
        "es": "Cartel inferior izquierdo típico de reportajes, fondo de color.",
    },
    "text.preset.quote.name": {
        "fr": "Citation",
        "en": "Quote",
        "es": "Cita",
    },
    "text.preset.quote.description": {
        "fr": "Citation milieu-centré avec fond doux translucide.",
        "en": "Centered quote with a soft translucent background.",
        "es": "Cita centrada con fondo suave translúcido.",
    },
    "text.preset.credits_simple.name": {
        "fr": "Générique simple",
        "en": "Simple credits",
        "es": "Créditos simples",
    },
    "text.preset.credits_simple.description": {
        "fr": "Bloc générique haut centré, petit, marges généreuses.",
        "en": "Top-centered small credits block with generous margins.",
        "es": "Bloque de créditos superior centrado, pequeño, márgenes amplios.",
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


# Motion graphics : traductions tenues à part, fusionnées ici.
from ui.i18n_mograph import MOGRAPH_TRANSLATIONS as _MOGRAPH  # noqa: E402

_TRANSLATIONS.update(_MOGRAPH)

# Tracking 2D : idem.
from ui.i18n_tracking import TRACKING_TRANSLATIONS as _TRACKING  # noqa: E402

_TRANSLATIONS.update(_TRACKING)

# Décodage matériel et aperçu GPU : idem.
from ui.i18n_hardware import HARDWARE_TRANSLATIONS as _HARDWARE  # noqa: E402

_TRANSLATIONS.update(_HARDWARE)

# Noms accessibles des contrôles sans texte : idem.
from ui.i18n_accessibility import ACCESSIBILITY_TRANSLATIONS as _ACCESSIBILITY  # noqa: E402

_TRANSLATIONS.update(_ACCESSIBILITY)


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
