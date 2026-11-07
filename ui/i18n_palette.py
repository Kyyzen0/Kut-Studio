"""Traductions de la palette de commandes « Rechercher une fonction » (fusionnées dans :mod:`ui.i18n`)."""

from __future__ import annotations


def _t(fr: str, en: str, es: str) -> dict[str, str]:
    return {"fr": fr, "en": en, "es": es}


PALETTE_TRANSLATIONS: dict[str, dict[str, str]] = {
    # Commande clavier (éditeur de raccourcis) et bouton de la barre du haut.
    "shortcuts.command.command_palette": _t("Rechercher une fonction", "Search for a function", "Buscar una función"),
    "palette.button": _t("Rechercher…", "Search…", "Buscar…"),
    "palette.button.tooltip": _t(
        "Rechercher une fonction ({shortcut}) : trouvez un outil par son nom, même caché dans un menu.",
        "Search for a function ({shortcut}): find a tool by name, even one hidden in a menu.",
        "Buscar una función ({shortcut}): encuentre una herramienta por su nombre, aunque esté en un menú.",
    ),
    "palette.button.name": _t("Rechercher une fonction", "Search for a function", "Buscar una función"),
    # Boîte.
    "palette.dialog.name": _t("Rechercher une fonction", "Search for a function", "Buscar una función"),
    "palette.field.placeholder": _t(
        "Que voulez-vous faire ? Ex. suivi, couper, sous-titres",
        "What do you want to do? E.g. tracking, cut, captions",
        "¿Qué quiere hacer? Ej. seguimiento, cortar, subtítulos",
    ),
    "palette.field.name": _t("Rechercher une fonction", "Search for a function", "Buscar una función"),
    "palette.try": _t("Essayez :", "Try:", "Pruebe:"),
    "palette.chip.tracking": _t("suivi", "tracking", "seguimiento"),
    "palette.chip.cut": _t("couper", "cut", "cortar"),
    "palette.chip.captions": _t("sous-titres", "captions", "subtítulos"),
    "palette.chip.color": _t("couleur", "color", "color"),
    "palette.close": _t("Fermer", "Close", "Cerrar"),
    "palette.close.tooltip": _t("Fermer la recherche (Échap)", "Close the search (Esc)", "Cerrar la búsqueda (Esc)"),
    "palette.list.name": _t("Résultats de la recherche", "Search results", "Resultados de la búsqueda"),
    "palette.list.description": _t("{count} résultat(s)", "{count} result(s)", "{count} resultado(s)"),
    "palette.footer": _t(
        "↑ ↓ choisir  ·  Entrée ouvrir  ·  Échap fermer",
        "↑ ↓ choose  ·  Enter open  ·  Esc close",
        "↑ ↓ elegir  ·  Intro abrir  ·  Esc cerrar",
    ),
    # Résultats.
    "palette.open": _t("Ouvrir", "Open", "Abrir"),
    "palette.switch": _t("Passer en {level}", "Switch to {level}", "Cambiar a {level}"),
    "palette.switch.tooltip": _t(
        "Passe au niveau d'usage {level} (réversible dans Fenêtre ▸ Niveau d'usage), puis lance la fonction.",
        "Switches to the {level} usage level (reversible in Window ▸ Usage level), then runs the function.",
        "Cambia al nivel de uso {level} (reversible en Ventana ▸ Nivel de uso) y ejecuta la función.",
    ),
    "palette.locked.hint": _t("{hint} · niveau {level}", "{hint} · {level} level", "{hint} · nivel {level}"),
    "palette.disabled.unknown": _t("Indisponible pour l'instant", "Unavailable right now", "No disponible por ahora"),
    "palette.disabled.reason": _t("Indisponible : {reason}", "Unavailable: {reason}", "No disponible: {reason}"),
    "palette.none": _t("Aucun résultat pour « {query} »", "No result for \"{query}\"", "Sin resultados para «{query}»"),
    "palette.none.hint": _t(
        "Essayez un autre mot : le nom d'un outil, d'un menu ou ce que vous voulez faire.",
        "Try another word: the name of a tool, a menu, or what you want to do.",
        "Pruebe otra palabra: el nombre de una herramienta, de un menú o lo que quiere hacer.",
    ),
    "palette.empty": _t(
        "Tapez un mot pour trouver une fonction.", "Type a word to find a function.", "Escriba una palabra para encontrar una función."
    ),
    "palette.recent": _t("Récemment utilisées", "Recently used", "Usadas recientemente"),
    "palette.path_separator": _t(" › ", " › ", " › "),
    # Destinations « Aller à … ».
    "palette.goto.page.title": _t("Aller à la page {name}", "Go to the {name} page", "Ir a la página {name}"),
    "palette.goto.tab.title": _t("Aller à l'onglet {name}", "Go to the {name} tab", "Ir a la pestaña {name}"),
    "palette.goto.library.title": _t("Aller à la bibliothèque {name}", "Go to the {name} library", "Ir a la biblioteca {name}"),
    "palette.goto.page": _t("Page", "Page", "Página"),
    "palette.goto.tab": _t("Onglet de l'inspecteur", "Inspector tab", "Pestaña del inspector"),
    "palette.goto.library": _t("Bibliothèque", "Library", "Biblioteca"),
    "palette.keywords.page.edit": _t("montage timeline", "edit timeline", "montaje línea de tiempo"),
    "palette.keywords.page.color": _t("étalonnage couleur grade", "color grade grading", "color etalonaje"),
    "palette.keywords.page.audio": _t("son mixage volume voix", "sound mix volume voice", "sonido mezcla volumen voz"),
    "palette.keywords.page.composition": _t("nœuds calques composition", "nodes layers compositing", "nodos capas composición"),
    "palette.keywords.tab.clip": _t("réglages mouvement position échelle", "settings motion position scale", "ajustes movimiento posición escala"),
    "palette.keywords.tab.color": _t("couleur étalonnage lut courbes", "color grade lut curves", "color etalonaje lut curvas"),
    "palette.keywords.tab.effects": _t("flou vignette vitesse ralenti", "blur vignette speed slow", "desenfoque viñeta velocidad"),
    "palette.keywords.tab.audio": _t("son volume voix égaliseur", "sound volume voice equalizer", "sonido volumen voz ecualizador"),
    "palette.keywords.tab.graphics": _t("texte titre sous-titres forme", "text title captions shape", "texto título subtítulos forma"),
    "palette.keywords.tab.compositing": _t("masque fusion incrustation recadrage", "mask blend chroma key crop", "máscara fusión recorte"),
    "palette.keywords.tab.tracking": _t("suivi tracking tracker stabilisation", "tracking tracker stabilization", "seguimiento estabilización"),
    "palette.keywords.library.media": _t("importer fichiers vidéos photos", "import files videos photos", "importar archivos vídeos fotos"),
    "palette.keywords.library.text": _t("titres sous-titres texte srt", "titles captions subtitles text srt", "títulos subtítulos texto srt"),
    "palette.keywords.library.effects": _t("filtres flou", "filters blur", "filtros desenfoque"),
    "palette.keywords.library.transitions": _t("fondu enchaîné", "fade dissolve", "fundido transición"),
    "palette.keywords.library.audio": _t("sons musique bruitages", "sounds music sfx", "sonidos música efectos"),
    "palette.keywords.library.graphics": _t("formes calques aplat", "shapes layers solid", "formas capas"),
    "palette.keywords.library.sequences": _t("séquences timeline imbriquer", "sequences timeline nest", "secuencias anidar"),
}
