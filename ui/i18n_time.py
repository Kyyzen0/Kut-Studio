"""Textes du remappage temporel (fusionnés dans :mod:`ui.i18n`) : vitesse, interpolation, audio, menu, inspecteur, historique.

Un libellé d'historique est écrit dans la langue courante **au moment de l'enregistrement** (voir ``i18n_history``).
Les pourcentages de vitesse (25 %, 200 %…) se calculent : ils n'ont pas de clé.
"""

from __future__ import annotations


def _t(fr: str, en: str, es: str) -> dict[str, str]:
    return {"fr": fr, "en": en, "es": es}


TIME_TRANSLATIONS: dict[str, dict[str, str]] = {
    "time.property.speed": _t("Vitesse", "Speed", "Velocidad"),
    # --- Menu « Vitesse » du clip -----------------------------------------------------------------------------------------
    "time.menu.speed": _t("Vitesse", "Speed", "Velocidad"),
    "time.menu.reverse": _t("Sens inverse", "Reverse", "Sentido inverso"),
    "time.menu.hold": _t("Arrêt sur image ici (1 s)", "Freeze frame here (1 s)", "Fotograma fijo aquí (1 s)"),
    "time.menu.interpolation": _t("Images intermédiaires", "Frame interpolation", "Fotogramas intermedios"),
    "time.menu.quality": _t("Qualité du flux optique", "Optical flow quality", "Calidad del flujo óptico"),
    "time.menu.analyze": _t("Analyser le flux optique…", "Analyze optical flow…", "Analizar el flujo óptico…"),
    "time.menu.curve": _t("Courbe de vitesse", "Speed curve", "Curva de velocidad"),
    "time.menu.add_point": _t("Ajouter un point de vitesse", "Add a speed point", "Añadir un punto de velocidad"),
    "time.menu.clear_curve": _t("Supprimer la courbe de vitesse", "Delete the speed curve", "Eliminar la curva de velocidad"),
    "time.menu.presets": _t("Préréglages", "Presets", "Preajustes"),
    "time.menu.audio": _t("Son", "Audio", "Audio"),
    "time.menu.preserve_pitch": _t("Conserver la hauteur du son", "Preserve pitch", "Conservar el tono"),
    "time.menu.remap_audio": _t("Le son suit la vitesse", "Audio follows the speed", "El audio sigue la velocidad"),
    "time.menu.ripple": _t(
        "Garder la durée sur la timeline", "Keep the timeline duration", "Mantener la duración en la línea de tiempo",
    ),
    "time.menu.copy": _t("Copier le temps", "Copy time settings", "Copiar los ajustes de tiempo"),
    "time.menu.paste": _t("Coller le temps", "Paste time settings", "Pegar los ajustes de tiempo"),
    "time.menu.reset": _t("Réinitialiser le temps", "Reset time settings", "Restablecer el tiempo"),
    # --- Interpolation ------------------------------------------------------------------------------------------------------
    "time.interpolation.sampling": _t("Échantillonnage", "Frame sampling", "Muestreo de fotogramas"),
    "time.interpolation.blending": _t("Mélange d'images", "Frame blending", "Mezcla de fotogramas"),
    "time.interpolation.optical_flow": _t("Flux optique", "Optical flow", "Flujo óptico"),
    "time.interpolation.sampling.tip": _t(
        "L'image source la plus proche : instantané, sans calcul. Un ralenti montre des images répétées.",
        "The nearest source frame: instant, no computation. Slow motion repeats frames.",
        "El fotograma de origen más cercano: instantáneo, sin cálculo. La cámara lenta repite fotogramas.",
    ),
    "time.interpolation.blending.tip": _t(
        "Mélange les deux images voisines au prorata exact de la position : fluide, mais flou en cas de mouvement.",
        "Blends the two neighbouring frames in exact proportion to the position: smooth, but soft on fast motion.",
        "Mezcla los dos fotogramas vecinos en proporción exacta a la posición: fluido, pero borroso con movimiento.",
    ),
    "time.interpolation.optical_flow.tip": _t(
        "Fabrique l'image intermédiaire en suivant le mouvement : nette et fluide ; calculé avant l'aperçu fidèle et l'export.",
        "Builds the in-between frame by following the motion: sharp and smooth; computed before the faithful preview and export.",
        "Crea el fotograma intermedio siguiendo el movimiento: nítido y fluido; se calcula antes de la vista previa y la exportación.",
    ),
    "time.quality.auto": _t("Auto", "Auto", "Auto"),
    "time.quality.draft": _t("Brouillon", "Draft", "Borrador"),
    "time.quality.balanced": _t("Équilibrée", "Balanced", "Equilibrada"),
    "time.quality.best": _t("Maximale", "Best", "Máxima"),
    # --- Préréglages --------------------------------------------------------------------------------------------------------
    "time.preset.slow_50": _t("Ralenti 50 %", "Slow 50%", "Lento 50 %"),
    "time.preset.slow_25": _t("Ralenti 25 %", "Slow 25%", "Lento 25 %"),
    "time.preset.fast_2": _t("Accéléré 2×", "Fast 2×", "Rápido 2×"),
    "time.preset.fast_4": _t("Accéléré 4×", "Fast 4×", "Rápido 4×"),
    "time.preset.ramp_in": _t("Rampe d'entrée (départ lent)", "Ramp in (slow start)", "Rampa de entrada (inicio lento)"),
    "time.preset.ramp_out": _t("Rampe de sortie (fin lente)", "Ramp out (slow end)", "Rampa de salida (final lento)"),
    "time.preset.freeze": _t("Arrêt sur image (1 s)", "Freeze frame (1 s)", "Fotograma fijo (1 s)"),
    # --- Inspecteur ----------------------------------------------------------------------------------------------------------
    "time.field.interpolation": _t("Images intermédiaires", "Frame interpolation", "Fotogramas intermedios"),
    "time.field.quality": _t("Qualité du flux", "Flow quality", "Calidad del flujo"),
    "time.field.audio": _t("Son", "Audio", "Audio"),
    "time.field.curve": _t("Courbe de vitesse", "Speed curve", "Curva de velocidad"),
    "time.check.preserve_pitch": _t("Conserver la hauteur", "Preserve pitch", "Conservar el tono"),
    "time.check.remap_audio": _t("Suivre la vitesse", "Follow the speed", "Seguir la velocidad"),
    "time.check.preserve_pitch.tip": _t(
        "Garde la voix à sa hauteur quand la vitesse change. Décoché : effet bande, la hauteur suit la vitesse.",
        "Keeps a voice at its pitch when the speed changes. Unchecked: tape effect, the pitch follows the speed.",
        "Mantiene la voz en su tono al cambiar la velocidad. Sin marcar: efecto cinta, el tono sigue la velocidad.",
    ),
    "time.check.remap_audio.tip": _t(
        "Décoché : seule la vidéo change de vitesse, le son reste à vitesse normale (montages sur musique).",
        "Unchecked: only the video changes speed, the audio stays at normal speed (music edits).",
        "Sin marcar: solo el vídeo cambia de velocidad, el audio se mantiene a velocidad normal (montajes con música).",
    ),
    "time.button.add_point": _t("Point de vitesse", "Speed point", "Punto de velocidad"),
    "time.button.add_point.tip": _t(
        "Ajoute un point de vitesse à la tête de lecture (sans changer la courbe). Se modifie dans le Graph Editor.",
        "Adds a speed point at the playhead (without changing the curve). Edit it in the Graph Editor.",
        "Añade un punto de velocidad en el cursor (sin cambiar la curva). Se edita en el Graph Editor.",
    ),
    "time.button.clear_curve": _t("Supprimer la courbe", "Delete curve", "Eliminar la curva"),
    "time.button.analyze": _t("Analyser le flux", "Analyze flow", "Analizar el flujo"),
    "time.button.cancel_analysis": _t("Annuler l'analyse", "Cancel analysis", "Cancelar el análisis"),
    "time.status.constant": _t("Vitesse constante", "Constant speed", "Velocidad constante"),
    "time.status.points": _t("{count} points de vitesse", "{count} speed points", "{count} puntos de velocidad"),
    "time.status.reverse_curve": _t(
        "{count} points de vitesse, lus à l'envers", "{count} speed points, played backwards",
        "{count} puntos de velocidad, en sentido inverso",
    ),
    "time.status.analyzing": _t(
        "Analyse du flux optique : {done} / {total}", "Analyzing optical flow: {done} / {total}",
        "Analizando el flujo óptico: {done} / {total}",
    ),
    "time.status.analysis_done": _t(
        "Flux optique analysé : {pairs} paires, {cached} déjà en cache.",
        "Optical flow analyzed: {pairs} pairs, {cached} already cached.",
        "Flujo óptico analizado: {pairs} pares, {cached} ya en caché.",
    ),
    "time.status.analysis_cancelled": _t("Analyse du flux optique annulée.", "Optical flow analysis cancelled.", "Análisis del flujo óptico cancelado."),
    "time.status.analysis_failed": _t(
        "Analyse du flux optique impossible : {reason}", "Optical flow analysis failed: {reason}",
        "No se pudo analizar el flujo óptico: {reason}",
    ),
    "time.status.nothing_to_analyze": _t(
        "Rien à analyser : à cette vitesse aucune image n'est à fabriquer.",
        "Nothing to analyze: at this speed no frame has to be created.",
        "Nada que analizar: a esta velocidad no hay que crear ningún fotograma.",
    ),
    "time.status.not_flow": _t(
        "Choisissez d'abord « Flux optique » pour ce clip.", "Choose “Optical flow” for this clip first.",
        "Elija primero «Flujo óptico» para este clip.",
    ),
    "time.status.pasted": _t("Temps collé.", "Time settings pasted.", "Ajustes de tiempo pegados."),
    "time.status.nothing_copied": _t("Rien à coller : copiez d'abord le temps d'un clip.", "Nothing to paste: copy a clip's time settings first.", "Nada que pegar: copie primero el tiempo de un clip."),
    "time.status.copied": _t("Temps copié.", "Time settings copied.", "Ajustes de tiempo copiados."),
    "time.status.no_clip": _t("Sélectionnez un clip.", "Select a clip.", "Seleccione un clip."),
    "time.status.playhead_outside": _t(
        "Placez la tête de lecture sur le clip.", "Place the playhead on the clip.", "Coloque el cursor sobre el clip.",
    ),
    "time.status.analysis_running": _t(
        "Une analyse du flux optique est déjà en cours.", "An optical flow analysis is already running.",
        "Ya hay un análisis del flujo óptico en curso.",
    ),
    "time.status.not_a_clip_with_time": _t(
        "Ce clip n'a pas de vitesse à régler.", "This clip has no speed to set.", "Este clip no tiene velocidad que ajustar.",
    ),
    "time.badge.blending": _t("MIX", "BLEND", "MEZ"),
    "time.badge.optical_flow": _t("FLUX", "FLOW", "FLUJO"),
    "time.notice.simplified": _t(
        "Aperçu simplifié : « {mode} » s'affiche ici en échantillonnage. L'aperçu fidèle et l'export le respectent.",
        "Simplified preview: “{mode}” is shown here as frame sampling. The faithful preview and the export honour it.",
        "Vista previa simplificada: «{mode}» se muestra aquí como muestreo. La vista fiel y la exportación lo respetan.",
    ),
    "time.notice.degraded": _t(
        "Flux optique : {count} image(s) remplacée(s) par un mélange ou l'image la plus proche (coupures, occlusions…).",
        "Optical flow: {count} frame(s) replaced by a blend or the nearest frame (cuts, occlusions…).",
        "Flujo óptico: {count} fotograma(s) sustituido(s) por una mezcla o el fotograma más cercano (cortes, oclusiones…).",
    ),
    "time.notice.prepared": _t(
        "Images intermédiaires prêtes ({count} images, confiance {confidence} %).",
        "In-between frames ready ({count} frames, confidence {confidence}%).",
        "Fotogramas intermedios listos ({count} fotogramas, confianza {confidence} %).",
    ),
    # --- Réglage du backend ---------------------------------------------------------------------------------------------------
    "perf.flow_backend": _t("Calcul du flux optique", "Optical flow computation", "Cálculo del flujo óptico"),
    "perf.flow_backend.auto": _t("Auto", "Auto", "Auto"),
    "perf.flow_backend.cpu": _t("Processeur", "Processor", "Procesador"),
    "perf.flow_backend.gpu": _t("GPU (indisponible)", "GPU (unavailable)", "GPU (no disponible)"),
    "perf.flow_backend.tip": _t(
        "Sur quoi se calculent les images intermédiaires du flux optique (aperçu fidèle et export). « Auto » prend le meilleur "
        "disponible ; aucun accélérateur n'est pris en charge pour l'instant, le calcul se fait sur le processeur.",
        "What computes the optical flow in-between frames (faithful preview and export). “Auto” takes the best available; no "
        "accelerator is supported yet, so the computation runs on the processor.",
        "Qué calcula los fotogramas intermedios del flujo óptico (vista fiel y exportación). «Auto» elige el mejor disponible; "
        "por ahora no se admite ningún acelerador, el cálculo se hace en el procesador.",
    ),
    "diag.flow_backend": _t(
        "Flux optique : backend demandé « {requested} », utilisé : {used}.",
        "Optical flow: requested backend “{requested}”, used: {used}.",
        "Flujo óptico: backend solicitado «{requested}», utilizado: {used}.",
    ),
    # --- Raccourcis -----------------------------------------------------------------------------------------------------------
    "shortcuts.category.time": _t("Temps", "Time", "Tiempo"),
    "shortcuts.command.time_add_speed_point": _t(
        "Ajouter un point de vitesse", "Add a speed point", "Añadir un punto de velocidad",
    ),
    "shortcuts.command.time_freeze_frame": _t(
        "Arrêt sur image à la tête de lecture", "Freeze frame at the playhead", "Fotograma fijo en el cursor",
    ),
    # --- Historique -----------------------------------------------------------------------------------------------------------
    "history.time.interpolation": _t("Changer les images intermédiaires", "Change frame interpolation", "Cambiar los fotogramas intermedios"),
    "history.time.quality": _t("Changer la qualité du flux optique", "Change optical flow quality", "Cambiar la calidad del flujo óptico"),
    "history.time.pitch": _t("Changer la hauteur du son", "Change audio pitch handling", "Cambiar el tono del audio"),
    "history.time.audio": _t("Changer l'audio du temps", "Change the audio time handling", "Cambiar el audio del tiempo"),
    "history.time.add_point": _t("Ajouter un point de vitesse", "Add a speed point", "Añadir un punto de velocidad"),
    "history.time.hold": _t("Arrêt sur image dans la courbe", "Freeze frame in the curve", "Fotograma fijo en la curva"),
    "history.time.clear_curve": _t("Supprimer la courbe de vitesse", "Delete the speed curve", "Eliminar la curva de velocidad"),
    "history.time.reset": _t("Réinitialiser le temps", "Reset time settings", "Restablecer el tiempo"),
    "history.time.paste": _t("Coller le temps", "Paste time settings", "Pegar los ajustes de tiempo"),
    "history.time.preset.slow_50": _t("Ralenti 50 %", "Slow 50%", "Lento 50 %"),
    "history.time.preset.slow_25": _t("Ralenti 25 %", "Slow 25%", "Lento 25 %"),
    "history.time.preset.fast_2": _t("Accéléré 2×", "Fast 2×", "Rápido 2×"),
    "history.time.preset.fast_4": _t("Accéléré 4×", "Fast 4×", "Rápido 4×"),
    "history.time.preset.ramp_in": _t("Rampe d'entrée", "Ramp in", "Rampa de entrada"),
    "history.time.preset.ramp_out": _t("Rampe de sortie", "Ramp out", "Rampa de salida"),
    "history.time.preset.freeze": _t("Arrêt sur image dans la courbe", "Freeze frame in the curve", "Fotograma fijo en la curva"),
}
