"""Messages de l'accès aux fichiers dans le bac à sable du Mac App Store, fusionnés dans :mod:`ui.i18n` (ADR-0013, étape 5).

Aucun chemin n'est recopié dans ces messages : seulement un nombre (le chemin pourrait citer un nom privé et ne tiendrait pas dans la
barre d'état).
"""

from __future__ import annotations


def _t(fr: str, en: str, es: str) -> dict[str, str]:
    return {"fr": fr, "en": en, "es": es}


SANDBOX_TRANSLATIONS: dict[str, dict[str, str]] = {
    "sandbox.access.offline": _t(
        "{count} média(s) de ce projet sont hors ligne : Kut-Studio n'a plus la permission de les lire. "
        "Dans la bibliothèque, choisissez « Relier » puis sélectionnez le fichier.",
        "{count} media file(s) in this project are offline: Kut-Studio no longer has permission to read them. "
        "In the library, choose \"Relink\" and select the file.",
        "{count} medio(s) de este proyecto están sin conexión: Kut-Studio ya no tiene permiso para leerlos. "
        "En la biblioteca, elija \"Reenlazar\" y seleccione el archivo.",
    ),
    "sandbox.access.lost": _t(
        "{count} fichier(s) ou dossier(s) autorisé(s) précédemment ne sont plus accessibles. "
        "Choisissez-les de nouveau (Importer, Ouvrir ou Relier) pour les retrouver.",
        "{count} previously authorized file(s) or folder(s) can no longer be reached. "
        "Choose them again (Import, Open or Relink) to get them back.",
        "{count} archivo(s) o carpeta(s) autorizados antes ya no son accesibles. "
        "Elíjalos de nuevo (Importar, Abrir o Reenlazar) para recuperarlos.",
    ),
}
