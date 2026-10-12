"""Messages de la ligne de commande (``Kut-Studio info | validate | presets | export``), fusionnés dans :mod:`ui.i18n`.

Le CLI parle la langue de l'utilisateur comme le reste de l'application (``--lang``, ``KUT_STUDIO_LANG``, sinon ``LANG``, sinon le
français). Les clés techniques (``--json``, noms de sections) restent stables et en anglais : seuls les **messages** sont traduits.
"""

from __future__ import annotations


def _t(fr: str, en: str, es: str) -> dict[str, str]:
    return {"fr": fr, "en": en, "es": es}


CLI_TRANSLATIONS: dict[str, dict[str, str]] = {
    "cli.usage.error": _t("Erreur d'utilisation : {message}", "Usage error: {message}", "Error de uso: {message}"),
    "cli.error.not_found": _t("Fichier introuvable : {path}", "File not found: {path}", "Archivo no encontrado: {path}"),
    "cli.error.invalid": _t(
        "Projet invalide : {message}", "Invalid project: {message}", "Proyecto no válido: {message}"
    ),
    "cli.error.unknown_preset": _t(
        "Préréglage inconnu : {preset}. Disponibles : {available}.",
        "Unknown preset: {preset}. Available: {available}.",
        "Preajuste desconocido: {preset}. Disponibles: {available}.",
    ),
    "cli.error.unknown_sequence": _t(
        "Séquence inconnue : {sequence}. Disponibles : {available}.",
        "Unknown sequence: {sequence}. Available: {available}.",
        "Secuencia desconocida: {sequence}. Disponibles: {available}.",
    ),
    "cli.error.output_exists": _t(
        "Le fichier de sortie existe déjà : {path}. Ajoutez --overwrite pour le remplacer.",
        "The output file already exists: {path}. Add --overwrite to replace it.",
        "El archivo de salida ya existe: {path}. Añada --overwrite para reemplazarlo.",
    ),
    "cli.error.output_is_input": _t(
        "La sortie est le projet lui-même : {path}. Un export ne remplace jamais son projet.",
        "The output is the project itself: {path}. An export never replaces its project.",
        "La salida es el propio proyecto: {path}. Una exportación nunca reemplaza su proyecto.",
    ),
    "cli.error.no_ffmpeg": _t(
        "FFmpeg est introuvable : installez-le (ou ajoutez-le au PATH).",
        "FFmpeg was not found: install it (or add it to PATH).",
        "No se encontró FFmpeg: instálelo (o añádalo al PATH).",
    ),
    "cli.validate.intact": _t("Projet intact : {path}", "Project intact: {path}", "Proyecto intacto: {path}"),
    "cli.validate.dropped": _t(
        "Projet chargé, mais {total} entrée(s) ignorée(s) :",
        "Project loaded, but {total} entr(ies) ignored:",
        "Proyecto cargado, pero {total} entrada(s) ignorada(s):",
    ),
    "cli.section.clip_effects": _t("effets de clip", "clip effects", "efectos de clip"),
    "cli.section.audio_effects": _t("effets audio et inserts", "audio effects and inserts", "efectos de audio e inserciones"),
    "cli.section.buses": _t("bus audio", "audio buses", "buses de audio"),
    "cli.section.transitions": _t("transitions", "clip transitions", "transiciones de clip"),
    "cli.section.ducking": _t("associations de ducking", "ducking links", "asociaciones de ducking"),
    "cli.section.color_presets": _t("préréglages de couleur", "colour presets", "ajustes preestablecidos de color"),
    "cli.section.library_folders": _t("dossiers de la bibliothèque", "library folders", "carpetas de la biblioteca"),
    "cli.section.library_tags": _t("étiquettes de la bibliothèque", "library tags", "etiquetas de la biblioteca"),
    "cli.section.library_assignments": _t(
        "affectations de la bibliothèque", "library assignments", "asignaciones de la biblioteca"
    ),
    "cli.validate.dropped_line": _t("  - {section} : {count}", "  - {section}: {count}", "  - {section}: {count}"),
    "cli.validate.missing_media": _t(
        "{count} média(s) introuvable(s) sur le disque :", "{count} media file(s) missing on disk:",
        "{count} archivo(s) multimedia no encontrado(s):",
    ),
    "cli.validate.missing_line": _t("  - {name} ({path})", "  - {name} ({path})", "  - {name} ({path})"),
    "cli.info.project": _t("Projet : {name} (format {version})", "Project: {name} (format {version})",
                           "Proyecto: {name} (formato {version})"),
    "cli.info.sequence": _t(
        "  Séquence {name} [{id}]{active} : {width}×{height}, {fps} i/s, {duration} s, {tracks} piste(s), {clips} clip(s)",
        "  Sequence {name} [{id}]{active}: {width}×{height}, {fps} fps, {duration} s, {tracks} track(s), {clips} clip(s)",
        "  Secuencia {name} [{id}]{active}: {width}×{height}, {fps} fps, {duration} s, {tracks} pista(s), {clips} clip(s)",
    ),
    "cli.info.active": _t(" (active)", " (current)", " (activa)"),
    "cli.info.media": _t("Médias : {count} ({missing} introuvable(s))", "Media: {count} ({missing} missing)",
                         "Medios: {count} ({missing} no encontrado(s))"),
    "cli.presets.line": _t("  {id} — {summary}", "  {id} — {summary}", "  {id} — {summary}"),
    "cli.export.start": _t("Export de {project} vers {output} ({preset})", "Exporting {project} to {output} ({preset})",
                           "Exportando {project} a {output} ({preset})"),
    "cli.export.done": _t("Export terminé : {path}", "Export finished: {path}", "Exportación terminada: {path}"),
    "cli.export.failed": _t("Export échoué : {message}", "Export failed: {message}", "Exportación fallida: {message}"),
    "cli.export.cancelled": _t("Export annulé.", "Export cancelled.", "Exportación cancelada."),
    "cli.export.loudness": _t(
        "Normalisation à {lufs} LUFS (mesure en cours)…", "Normalising to {lufs} LUFS (measuring)…",
        "Normalizando a {lufs} LUFS (midiendo)…",
    ),
    "cli.export.progress": _t("  {value} %", "  {value}%", "  {value} %"),
}

__all__ = ["CLI_TRANSLATIONS"]
