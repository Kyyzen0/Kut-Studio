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
    # --- Scripts déclaratifs (apply, validate script.json) -------------------------------------------------------------
    "cli.error.apply_output_is_input": _t(
        "La sortie est le projet lui-même : {path}. Ajoutez --in-place pour le modifier sur place.",
        "The output is the project itself: {path}. Add --in-place to modify it in place.",
        "La salida es el propio proyecto: {path}. Añada --in-place para modificarlo en su sitio.",
    ),
    "cli.error.apply_output_required": _t(
        "Indiquez la sortie : -o fichier.kut (ou --in-place, ou --dry-run pour ne rien écrire).",
        "Say where to write: -o file.kut (or --in-place, or --dry-run to write nothing).",
        "Indique dónde escribir: -o archivo.kut (o --in-place, o --dry-run para no escribir nada).",
    ),
    "cli.error.apply_in_place_conflict": _t(
        "--in-place écrit dans le projet : il ne se combine pas avec -o.",
        "--in-place writes into the project: it cannot be combined with -o.",
        "--in-place escribe en el proyecto: no se combina con -o.",
    ),
    "cli.script.invalid": _t(
        "Script invalide : {count} erreur(s).", "Invalid script: {count} error(s).", "Script no válido: {count} error(es)."
    ),
    "cli.script.valid": _t(
        "Script valide : {path} ({operations} opération(s)).",
        "Valid script: {path} ({operations} operation(s)).",
        "Script válido: {path} ({operations} operación(es)).",
    ),
    "cli.script.where": _t("le script", "the whole script", "todo el script"),
    "cli.script.issue_line": _t("  - {where} : {message}", "  - {where}: {message}", "  - {where}: {message}"),
    "cli.script.suggestion": _t(" (vouliez-vous dire « {suggestion} » ?)", " (did you mean \"{suggestion}\"?)", " (¿quería decir «{suggestion}»?)"),
    "cli.script.issue.not_json": _t("JSON illisible : {error}", "Unreadable JSON: {error}", "JSON ilegible: {error}"),
    "cli.script.issue.too_large": _t(
        "script trop volumineux (plus de {limit} octets)", "script too large (over {limit} bytes)", "script demasiado grande (más de {limit} bytes)"
    ),
    "cli.script.issue.not_object": _t(
        "le script doit être un objet JSON", "the script must be a JSON object", "el script debe ser un objeto JSON"
    ),
    "cli.script.issue.unknown_field": _t("champ inconnu", "unknown field", "campo desconocido"),
    "cli.script.issue.missing_version": _t(
        "version absente : ajoutez \"kut_script\": 1", "missing version: add \"kut_script\": 1", "falta la versión: añada \"kut_script\": 1"
    ),
    "cli.script.issue.newer_version": _t(
        "version {found} trop récente (cette application comprend jusqu'à {supported})",
        "version {found} is too new (this application understands up to {supported})",
        "la versión {found} es demasiado nueva (esta aplicación entiende hasta la {supported})",
    ),
    "cli.script.issue.wrong_type": _t("type incorrect : {expected} attendu", "wrong type: {expected} expected", "tipo incorrecto: se esperaba {expected}"),
    "cli.script.issue.out_of_range": _t(
        "valeur hors bornes (de {minimum} à {maximum})", "value out of range ({minimum} to {maximum})", "valor fuera de rango ({minimum} a {maximum})"
    ),
    "cli.script.issue.missing_ops": _t("liste \"ops\" absente", "missing \"ops\" list", "falta la lista \"ops\""),
    "cli.script.issue.too_many_ops": _t(
        "trop d'opérations (au plus {limit})", "too many operations (at most {limit})", "demasiadas operaciones (como máximo {limit})"
    ),
    "cli.script.issue.op_not_object": _t(
        "une opération est un objet JSON", "an operation is a JSON object", "una operación es un objeto JSON"
    ),
    "cli.script.issue.unknown_verb": _t("verbe inconnu", "unknown verb", "verbo desconocido"),
    "cli.script.issue.missing_field": _t("champ obligatoire absent", "required field missing", "falta un campo obligatorio"),
    "cli.script.issue.not_in_choices": _t(
        "valeur non permise (permises : {choices})", "value not allowed (allowed: {choices})", "valor no permitido (permitidos: {choices})"
    ),
    "cli.script.issue.bad_id": _t(
        "identifiant invalide (lettres, chiffres et « _ », {limit} caractères au plus)",
        "invalid identifier (letters, digits and \"_\", at most {limit} characters)",
        "identificador no válido (letras, cifras y «_», como máximo {limit} caracteres)",
    ),
    "cli.script.issue.bad_text": _t(
        "texte invalide (non vide, {limit} caractères au plus, sans caractère de contrôle ni espace en bordure)",
        "invalid text (not empty, at most {limit} characters, no control characters, no leading or trailing whitespace)",
        "texto no válido (no vacío, como máximo {limit} caracteres, sin caracteres de control ni espacios en los extremos)",
    ),
    "cli.script.issue.bad_color": _t(
        "couleur invalide (« #RRGGBB » ou « #RRGGBBAA »)", "invalid colour (\"#RRGGBB\" or \"#RRGGBBAA\")",
        "color no válido («#RRGGBB» o «#RRGGBBAA»)",
    ),
    "cli.script.issue.bad_ref": _t(
        "une référence s'écrit {{\"ref\": \"alias\"}}", "a reference is written {{\"ref\": \"alias\"}}",
        "una referencia se escribe {{\"ref\": \"alias\"}}",
    ),
    "cli.script.issue.unknown_alias": _t(
        "alias « {alias} » inconnu (défini plus haut ?)", "unknown alias \"{alias}\" (defined earlier?)", "alias «{alias}» desconocido (¿definido antes?)"
    ),
    "cli.script.issue.alias_kind": _t(
        "l'alias « {alias} » désigne un objet de genre {found}, pas {expected}",
        "alias \"{alias}\" is a {found}, not a {expected}",
        "el alias «{alias}» designa un objeto de tipo {found}, no {expected}",
    ),
    "cli.script.issue.too_many_params": _t(
        "trop de paramètres (au plus {limit})", "too many parameters (at most {limit})", "demasiados parámetros (como máximo {limit})"
    ),
    "cli.script.issue.bad_param_name": _t("nom de paramètre invalide", "invalid parameter name", "nombre de parámetro no válido"),
    "cli.script.issue.bad_param_value": _t(
        "valeur de paramètre invalide (nombre, booléen ou texte court)",
        "invalid parameter value (number, boolean or short text)",
        "valor de parámetro no válido (número, booleano o texto corto)",
    ),
    "cli.script.issue.duplicate_alias": _t(
        "alias « {alias} » déjà défini", "alias \"{alias}\" already defined", "alias «{alias}» ya definido"
    ),
    "cli.script.issue.duplicate_id": _t(
        "identifiant « {id} » déjà utilisé dans ce script", "identifier \"{id}\" already used in this script",
        "identificador «{id}» ya usado en este script",
    ),
    "cli.apply.refused": _t(
        "Opération refusée ({op}, n° {index}) : {message}. Rien n'a été modifié.",
        "Operation refused ({op}, #{index}): {message}. Nothing was changed.",
        "Operación rechazada ({op}, n.º {index}): {message}. No se modificó nada.",
    ),
    "cli.apply.dry_run": _t(
        "Essai à blanc : {operations} opération(s) s'appliquent sans erreur ; rien n'est écrit.",
        "Dry run: {operations} operation(s) apply cleanly; nothing is written.",
        "Prueba en seco: {operations} operación(es) se aplican sin error; no se escribe nada.",
    ),
    "cli.apply.done": _t(
        "{operations} opération(s) appliquée(s) : {path}", "{operations} operation(s) applied: {path}", "{operations} operación(es) aplicada(s): {path}"
    ),
    "cli.apply.created_line": _t("  + {op} -> {id}", "  + {op} -> {id}", "  + {op} -> {id}"),
    "cli.apply.write_failed": _t(
        "Écriture impossible : {message}", "Could not write: {message}", "No se pudo escribir: {message}"
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
